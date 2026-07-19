"""Schedule no-show cron (Phase 10 Slice 2).

Walks `staff_schedule_entries` for published rows whose `starts_at_local
+ late_grace_minutes` has elapsed in real wall time with no
`actual_clock_in_punch_id` stamped, and flips
`attendance_status` from `scheduled` to `no_show`.

This tick is intentionally cheap: it leans on the partial index
`idx_sse_no_show_scan` from migration 068 (status='published' AND
attendance_status='scheduled' AND actual_clock_in_punch_id IS NULL),
and the in-Python grace-window filter is over a short list of
candidates rather than a full table scan.

A late arrival recovers a no-show: `services.staff_schedule.stamp_clock_in`
re-derives `attendance_status` even for rows the cron pre-emptively
flipped. The cron and the clock-in hook share the same grace value
(the entry's own `late_grace_minutes`, copied from the source template
at publish time per Slice 1), so the two paths cannot disagree.

Scheduled by `workers.schedule_monitor.run_loop` at a 5-minute cadence
— much faster than the daily worker because a 9am no-show that doesn't
flip until tomorrow's 02:30 daily tick is useless to the manager.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services import cron_state, staff_schedule

log = logging.getLogger(__name__)


def tick(db: Session) -> None:
    """One pass over overdue scheduled entries. Commits on success;
    rolls back + re-raises on failure so `record_run` writes the
    failure stamp before the worker swallows and logs.
    """
    with cron_state.record_run(cron_state.SCHEDULE_NO_SHOW) as run:
        try:
            candidates = staff_schedule.find_no_show_candidates(
                db, as_of_utc=run.started_at
            )
            run.scanned = len(candidates)
            if not candidates:
                db.commit()
                return
            flipped = staff_schedule.mark_no_shows(
                db, as_of_utc=run.started_at
            )
            run.changed = len(flipped)
            db.commit()
        except Exception:
            db.rollback()
            raise
