"""Daily reminder + quote-expiry pass.

Phase 11. Runs as an asyncio task inside the FastAPI process,
scheduled to fire once per local day in the configured shop timezone.
The first tick fires shortly after process start so a fresh deploy
catches up on any reminders the previous day's worker missed during
downtime; subsequent ticks fire at ~02:30 local time when nobody is
in the boutique.

Why in-process and not OS cron:

  - The shop's deploy is a single FastAPI process; introducing a
    sibling cron + systemd timer would mean two services to monitor.
  - The reminder pass is idempotent (per-installment ``*_sent_at``
    stamps), so a re-run after a process restart is safe.
  - ``asyncio.to_thread`` keeps the SQL work off the event loop.

If the shop ever runs multiple API replicas, this needs to migrate to
its own process or pick a leader via a Postgres advisory lock — but
that's a v2 problem.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import APP_TIMEZONE
from database.connection import SessionLocal
from services import (
    attendance_close,
    attendance_geo_retention,
    attendance_pre_close,
    clock_selfie_retention,
    missing_out_punch_cron,
    reminder_runner,
    staff_digest_runner,
    webhook_ingest,
)

log = logging.getLogger(__name__)


# Time of day to run (local shop time). 02:30 keeps it well clear of
# the morning shift and any late-evening invoice work.
_RUN_HOUR = 2
_RUN_MINUTE = 30

# Initial delay before the first tick — enough to let the API finish
# warm-up and not spam the log on rapid restart loops, but short
# enough that a deploy that misses the 02:30 window still runs the
# pass before staff arrive.
_INITIAL_DELAY_SECONDS = 60


def _seconds_until_next_run(now_utc: datetime, tz: ZoneInfo) -> float:
    """Seconds from `now_utc` to the next ``02:30`` in the shop tz."""
    now_local = now_utc.astimezone(tz)
    target_today = now_local.replace(
        hour=_RUN_HOUR, minute=_RUN_MINUTE, second=0, microsecond=0
    )
    if now_local >= target_today:
        target = target_today + timedelta(days=1)
    else:
        target = target_today
    delta = (target - now_local).total_seconds()
    return max(60.0, delta)


async def run_loop(stop_event: asyncio.Event) -> None:
    log.info("daily worker started")
    try:
        tz = ZoneInfo(APP_TIMEZONE)
    except Exception:  # pragma: no cover — bad config
        log.exception("daily worker timezone parse failed; defaulting to UTC")
        tz = ZoneInfo("UTC")

    # First tick fires after a short delay so a startup-during-the-day
    # deploy still catches up, then we settle into the daily cadence.
    delay = _INITIAL_DELAY_SECONDS
    while not stop_event.is_set():
        slept = 0.0
        while slept < delay and not stop_event.is_set():
            await asyncio.sleep(min(5.0, delay - slept))
            slept += 5.0
        if stop_event.is_set():
            break
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            log.exception("daily worker tick failed")
        delay = _seconds_until_next_run(datetime.now(timezone.utc), tz)
    log.info("daily worker stopped")


def _tick() -> None:
    db = SessionLocal()
    try:
        reminder_runner.run_daily(db)
    finally:
        db.close()

    # Phase 7 Slice 2B-3 attendance crons + C2 webhook retention.
    # Each runs in its own session + record_run so a failure in one
    # doesn't poison the others; the daily worker picks them up again
    # on the next tick.
    # Order matters: attendance_close runs first so it can stamp
    # schedule_entries' actual_clock_out_punch_id; then
    # missing_out_punch_cron only catches sessions auto-close didn't
    # handle (no shift cutoff, cron failure, opt-out, etc.).
    for tick_fn in (
        attendance_close.tick,
        attendance_pre_close.tick,
        clock_selfie_retention.tick,
        attendance_geo_retention.tick,
        webhook_ingest.tick,
        missing_out_punch_cron.tick,
        # Digest runner sits at the end so it sees the day's
        # attendance/missing-out state after the upstream ticks have
        # had a chance to flip anything. Has its own internal try/except
        # per cadence so a slow staff_weekly query doesn't block
        # admin_daily or vice versa.
        staff_digest_runner.tick,
    ):
        db = SessionLocal()
        try:
            tick_fn(db)
        except Exception:
            log.exception("daily worker: cron tick failed")
        finally:
            db.close()
