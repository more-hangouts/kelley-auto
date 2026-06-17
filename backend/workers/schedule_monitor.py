"""Schedule monitor worker (Phase 10 Slice 2).

Short-cadence loop that runs `services.no_show_cron.tick` every 5
minutes so a missed shift flips to `no_show` quickly enough for the
manager to react the same morning. The daily worker at 02:30 is the
wrong cadence for this — a 9am no-show shouldn't wait 17 hours to
surface.

The loop mirrors `workers/notifications.run_loop`'s shape: an asyncio
task that sleeps in 1-second slices so shutdown cancellation is
responsive, and re-raises nothing so a transient DB hiccup logs and
the next tick still fires. The cron's own `record_run` context is
what writes the failure stamp to `cron_run_state`.
"""

from __future__ import annotations

import asyncio
import logging

from database.connection import SessionLocal
from services import no_show_cron, shift_request_expiry_cron

log = logging.getLogger(__name__)


DEFAULT_POLL_SECONDS = 300  # 5 minutes — granular enough for a
# morning-shift no-show, sparse enough that historical scan stays
# trivial.


async def run_loop(
    stop_event: asyncio.Event, *, poll_seconds: float = DEFAULT_POLL_SECONDS
) -> None:
    log.info("schedule monitor worker started (poll=%ss)", poll_seconds)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            log.exception("schedule monitor tick failed")
        slept = 0.0
        while slept < poll_seconds and not stop_event.is_set():
            await asyncio.sleep(min(1.0, poll_seconds - slept))
            slept += 1.0
    log.info("schedule monitor worker stopped")


def _tick() -> None:
    for tick_fn in (
        no_show_cron.tick,
        shift_request_expiry_cron.tick,
    ):
        db = SessionLocal()
        try:
            tick_fn(db)
        except Exception:
            log.exception("schedule monitor: cron tick failed")
        finally:
            db.close()
