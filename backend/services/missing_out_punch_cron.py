"""Missing-out-punch cron (Phase 10 Slice 4).

Daily sweep that flips `staff_schedule_entries.attendance_status`
from a normal state to `'missing_out_punch'` for every published
entry where the stylist clocked in, never clocked out, and the
entry's `business_date` is strictly before today (boutique-local).

Runs from `workers.daily` at ~02:30 local. Auto-close (which runs in
the same daily tick *before* this cron) now wires
`staff_schedule.stamp_clock_out` for any entry it auto-closes, so
this cron only ever surfaces sessions the auto-close path didn't
handle — typically because no shift / location auto-close cutoff
existed, the cron failed, or the boutique opted out of auto-close.

Caller commits inside `tick`; the cron's `record_run` context
writes the failure stamp on exceptions.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services import cron_state, staff_schedule

log = logging.getLogger(__name__)


def tick(db: Session) -> None:
    with cron_state.record_run(cron_state.SCHEDULE_MISSING_OUT_PUNCH) as run:
        try:
            candidates = staff_schedule.find_missing_out_candidates(
                db, as_of_utc=run.started_at
            )
            run.scanned = len(candidates)
            if not candidates:
                db.commit()
                return
            flipped = staff_schedule.mark_missing_out_punches(
                db, as_of_utc=run.started_at
            )
            run.changed = len(flipped)
            db.commit()
        except Exception:
            db.rollback()
            raise

        # After the flip commits, notify the staffer and the admins. Best-
        # effort: SMTP failures shouldn't poison a successful cron run, and
        # the row state is already on disk so a manual sweep can catch
        # anyone who didn't receive their email.
        if flipped:
            staff_schedule._send_missing_clock_out_emails(
                db, flipped_entry_ids=flipped
            )
