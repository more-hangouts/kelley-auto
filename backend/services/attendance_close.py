"""Auto-close cron for forgotten check-outs (Phase 7 Slice 2B-3).

Stylists will forget to clock out. The lock-in: "auto-close server-
side with explicit `auto_closed` fields and a visible 'confirm hours'
workflow, never with a silent normal-looking out time."

What this module does:

  1. Find every user whose most recent non-void punch is `direction='in'`
     AND is older than the applicable cutoff in business-local time.
     Until Phase 8 wires shifts, the cutoff is the location's
     `default_auto_session_close_time` for the in-punch's local date,
     OR — if the location has no default — `business_now() - 1 day`
     (a stale session that crossed past the calendar day).
  2. Insert a paired `direction='out'` row with:
        - `auto_closed = TRUE`
        - `auto_close_reason IN ('past_date', 'max_time_reached')`
        - `auto_closed_at = now()`
        - `hours_confirmation_status = 'needs_review'`
        - `punched_at` set to the cutoff itself, NOT to now-utc, so
          the row reflects when the system *thought* the session
          should have ended.
  3. Stamp the original in-punch's `hours_confirmation_status` to
     `needs_review` (it isn't really "closed" cleanly — owner needs
     to confirm both sides match what the stylist actually worked).
  4. Write a `staff_punch_audit_events` row with `actor_kind='system'`,
     before/after JSON, the reason code, and the in-punch id.

**Idempotency** (the user explicitly asked for this in Slice 2B-3):
the worker re-checks `current_status` for each candidate user before
inserting the auto-out punch. A second tick against the same open
session sees `state == 'out'` (the first tick already closed it) and
no-ops. The smoke proves a run-twice tick produces the same row count,
the same audit-event count, and the same `hours_confirmation_status`
distribution.

Auto-close NEVER runs from a read path. Only the daily worker calls
`tick(db)`; admin can trigger it via a future ops route if needed,
but that route is not part of Slice 2B-3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    StaffLocation,
    StaffPunch,
    StaffPunchAuditEvent,
    User,
)
from services import clock_in, cron_state, shift_resolver, staff_schedule
from services.business_time import business_now, shop_tz, to_business_local
from services.shift_resolver import ResolvedShift

log = logging.getLogger(__name__)


# Hard ceiling on a session — even with no shift data, a punch open for
# more than this many hours is auto-closed regardless of the
# location's default cutoff. Cheap protection against a literally
# forgotten session that spans days.
MAX_SESSION_HOURS = 24


@dataclass
class AutoCloseResult:
    scanned_open_sessions: int
    closed: int
    skipped_already_closed: int
    skipped_no_cutoff: int


@dataclass(frozen=True)
class _CloseDecision:
    in_punch: StaffPunch
    cutoff_at: datetime  # UTC
    reason: str  # 'past_date' | 'max_time_reached'


def _open_in_punches(db: Session) -> list[StaffPunch]:
    """Return every user's most-recent non-void punch when it's a
    direction='in'. We pull the candidate set by joining users to
    their last non-void punch in Python because the per-user count
    at boutique scale is single-digit; a window-function SQL query
    here is overkill."""
    users = db.query(User).filter(User.role.in_(("sales", "user", "admin"))).all()
    open_punches: list[StaffPunch] = []
    for u in users:
        state, last = clock_in.current_status(db, u.id)
        if state == "in" and last is not None and last.direction == "in":
            open_punches.append(last)
    return open_punches


def _decide_close(
    punch: StaffPunch,
    *,
    now_utc: datetime,
    resolved: ResolvedShift | None,
) -> _CloseDecision | None:
    """Return a close decision if `punch` is past its cutoff, else None.

    Two conditions can fire — whichever cuts off first wins:

      a) `max_time_reached`: the session is older than the resolver's
         `max_session_hours` (Slice B) or, when no shift is in scope,
         the module's `MAX_SESSION_HOURS` constant (Phase 7's
         location-default fallback).
      b) `past_date`: handled by `_past_date_decision` below — the
         shift's `auto_session_close_time` wins, the location's
         `default_auto_session_close_time` is the fallback.

    Per the user's Slice B guardrail #4, the location-default branch
    is preserved verbatim from Phase 7 — Slice B *adds* shift-aware
    cutoffs without changing the existing fallback.
    """
    if (
        resolved is not None
        and resolved.max_session_hours is not None
    ):
        max_hours = float(resolved.max_session_hours)
    else:
        max_hours = float(MAX_SESSION_HOURS)

    age = now_utc - punch.punched_at
    if age >= timedelta(hours=max_hours):
        cutoff = punch.punched_at + timedelta(hours=max_hours)
        return _CloseDecision(
            in_punch=punch,
            cutoff_at=cutoff,
            reason="max_time_reached",
        )
    return None


def _past_date_decision(
    db: Session,
    *,
    punch: StaffPunch,
    now_utc: datetime,
    resolved: ResolvedShift | None,
) -> _CloseDecision | None:
    """Apply the `past_date` cutoff. Resolves source in this order:

      1. Shift's `auto_session_close_time` if a shift covers the
         in-punch's business date.
      2. Location's `default_auto_session_close_time` (the existing
         Phase 7 fallback).

    Returns None when neither source has a cutoff configured or when
    `now_utc` hasn't reached it yet.
    """
    tz = shop_tz()
    local_in = to_business_local(punch.punched_at)
    cutoff_time = None
    if resolved is not None and resolved.auto_session_close_time is not None:
        cutoff_time = resolved.auto_session_close_time
    elif punch.location_id is not None:
        location: StaffLocation | None = db.get(
            StaffLocation, punch.location_id
        )
        if location is not None:
            cutoff_time = location.default_auto_session_close_time

    if cutoff_time is None:
        return None

    cutoff_local = datetime.combine(local_in.date(), cutoff_time, tzinfo=tz)
    # If the in-punch is itself after the cutoff (a 23:30 in with a
    # 22:00 cutoff), shift the cutoff to the next day.
    if cutoff_local <= local_in:
        cutoff_local += timedelta(days=1)
    cutoff_utc = cutoff_local.astimezone(timezone.utc)
    if now_utc < cutoff_utc:
        return None
    return _CloseDecision(
        in_punch=punch,
        cutoff_at=cutoff_utc,
        reason="past_date",
    )


def run_auto_close_pass(
    db: Session,
    *,
    now_override: datetime | None = None,
) -> AutoCloseResult:
    """One auto-close tick.

    `now_override` is for the smoke. Production calls let the helper
    use real wall-clock so the cutoff math reflects the actual run time.
    """
    now_utc = now_override or business_now().astimezone(timezone.utc)

    candidates = _open_in_punches(db)
    scanned = len(candidates)
    closed = 0
    skipped_already_closed = 0
    skipped_no_cutoff = 0

    for in_punch in candidates:
        # Idempotency guard #1: re-check status with the live session.
        # If a sibling write closed this session between the candidate
        # scan and this branch, our state is stale and we must skip.
        state, last = clock_in.current_status(db, in_punch.user_id)
        if state != "in" or last is None or last.id != in_punch.id:
            skipped_already_closed += 1
            continue

        # Resolver runs against the in-punch's business date so an
        # overnight session uses yesterday's shift template, not
        # today's nothing. Returning None falls through to the Phase 7
        # location-default branch.
        in_local = to_business_local(in_punch.punched_at)
        resolved = shift_resolver.resolve_active_shift(
            db, user_id=in_punch.user_id, as_of_local=in_local
        )

        decision = _decide_close(
            in_punch, now_utc=now_utc, resolved=resolved
        )
        if decision is None:
            decision = _past_date_decision(
                db, punch=in_punch, now_utc=now_utc, resolved=resolved
            )
        if decision is None:
            skipped_no_cutoff += 1
            continue

        # Insert the paired auto-out punch. `punched_at` is the cutoff
        # itself, not now-utc, so the timeline reflects "this session
        # should have ended at <cutoff>".
        out_punch = StaffPunch(
            user_id=in_punch.user_id,
            direction="out",
            punched_at=decision.cutoff_at,
            status="unscheduled",
            location_id=in_punch.location_id,
            shift_id=in_punch.shift_id,
            holiday_id=in_punch.holiday_id,
            auto_closed=True,
            auto_close_reason=decision.reason,
            auto_closed_at=now_utc,
            hours_confirmation_status="needs_review",
        )
        db.add(out_punch)

        # Stamp the in-punch as needs_review too — owner reviews the
        # whole session, not just the synthetic out.
        if in_punch.hours_confirmation_status not in (
            "confirmed",
            "adjusted",
            "needs_review",
        ):
            in_punch.hours_confirmation_status = "needs_review"

        db.flush()

        db.add(
            StaffPunchAuditEvent(
                punch_id=out_punch.id,
                actor_kind="system",
                actor_user_id=None,
                action="punch.auto_closed",
                reason_code=decision.reason,
                old_values={"in_punch_id": in_punch.id},
                new_values={
                    "out_punch_id": out_punch.id,
                    "punched_at": decision.cutoff_at.isoformat(),
                    "auto_close_reason": decision.reason,
                },
                notes=(
                    f"auto-closed forgotten session opened at "
                    f"{in_punch.punched_at.isoformat()}"
                ),
            )
        )

        # Slice-4: link the auto-out into any matching schedule_entry
        # so the Slice-4 missing_out_punch cron doesn't later flag a
        # row the auto-close already handled. Defensive no-op when no
        # entry exists for this in-punch (legacy or unscheduled punch).
        staff_schedule.stamp_clock_out(
            db, in_punch_id=in_punch.id, out_punch_id=out_punch.id
        )

        closed += 1

    db.flush()
    return AutoCloseResult(
        scanned_open_sessions=scanned,
        closed=closed,
        skipped_already_closed=skipped_already_closed,
        skipped_no_cutoff=skipped_no_cutoff,
    )


def tick(db: Session) -> AutoCloseResult:
    """Worker entrypoint. Wraps `run_auto_close_pass` in a cron-state
    record_run so the admin status surface knows when this last
    completed."""
    with cron_state.record_run(cron_state.AUTO_CLOSE) as run:
        result = run_auto_close_pass(db)
        run.scanned = result.scanned_open_sessions
        run.changed = result.closed
        db.commit()
    return result
