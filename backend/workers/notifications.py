"""Async loop that drives notification_jobs dispatch.

Runs as an asyncio task inside the FastAPI process. Single-process is fine
for v1 traffic; once we run multiple API replicas this should split into
its own process and rely on FOR UPDATE SKIP LOCKED (already in place) to
coordinate. The poll interval intentionally short-sleeps in 1-second slices
so shutdown cancellation is responsive.
"""

from __future__ import annotations

import asyncio
import logging

from database.connection import SessionLocal
from services.email_transport import get_email_transport
from services.notification_service import claim_due_jobs, dispatch_job
from services.sms_transport import get_sms_transport

log = logging.getLogger(__name__)


DEFAULT_POLL_SECONDS = 30


async def run_loop(stop_event: asyncio.Event, *, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
    log.info("notifications worker started (poll=%ss)", poll_seconds)
    email = get_email_transport()
    sms = get_sms_transport()
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_tick, email, sms)
        except Exception:
            log.exception("notifications worker tick failed")
        # Sleep responsively so shutdown doesn't have to wait the full interval.
        slept = 0.0
        while slept < poll_seconds and not stop_event.is_set():
            await asyncio.sleep(min(1.0, poll_seconds - slept))
            slept += 1.0
    log.info("notifications worker stopped")


def _tick(email_transport, sms_transport) -> None:
    db = SessionLocal()
    try:
        jobs = claim_due_jobs(db)
        if not jobs:
            db.commit()
            return
        for job in jobs:
            dispatch_job(db, job, email_transport=email_transport, sms_transport=sms_transport)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
