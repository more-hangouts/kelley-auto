"""Shift-request / open-shift expiry cron (Scheduling Phase 5).

Runs on the short schedule-monitor cadence so open-shift posts disappear
soon after the staff-facing 12-hour claim cutoff, and stale accepted /
pending requests expire soon after their underlying shift starts.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services import cron_state, staff_shift_requests

log = logging.getLogger(__name__)


def tick(db: Session) -> None:
    """One idempotent expiry pass. Commits on success; rolls back and
    re-raises on failure so ``cron_state.record_run`` stamps the error.
    """
    with cron_state.record_run(cron_state.SCHEDULE_REQUEST_EXPIRY) as run:
        try:
            result = staff_shift_requests.expire_due(db, now=run.started_at)
            run.scanned = int(result["scanned"])
            run.changed = int(result["changed"])
            db.commit()
        except Exception:
            db.rollback()
            raise
