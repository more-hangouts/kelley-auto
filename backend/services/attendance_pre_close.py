"""Pre-close reminder cron (Phase 7 Slice 2B-3).

Sends a "Still working? Clock out or extend." email shortly before the
auto-close cutoff fires, so the stylist gets a chance to confirm rather
than landing in the owner's review queue. Per the lock-in: "Bypasses
quiet hours because it protects the stylist's payable hours."

Scope guard:

  - Only fires for sessions that have a deterministic cutoff. Today
    that means an in-punch attached to a `staff_locations` row with
    `default_auto_session_close_time` set. Phase 8's shifts will
    extend this to per-stylist shift cutoffs; the cron loop is
    structured so adding a shift-aware cutoff is one branch, not a
    rewrite.
  - "Shortly before" defaults to 30 minutes pre-cutoff. The reminder
    fires on the same day as the cutoff so the email arrives while
    the stylist is still on the floor.

**Idempotency**: every send writes to `attendance_pre_close_reminders`
keyed by `(punch_id, cutoff_business_date)` with a UNIQUE constraint.
Two ticks in the same window cannot fire two emails — the second
INSERT raises and is swallowed as "already sent".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import (
    AttendancePreCloseReminder,
    StaffLocation,
    StaffPunch,
    User,
)
from services import clock_in, cron_state, email_transport, shift_resolver
from services.business_time import business_now, shop_tz, to_business_local
from services.email_transport import EmailMessagePayload

log = logging.getLogger(__name__)

# Window the reminder fires in. We send when the cutoff is within
# REMINDER_LEAD_MINUTES of `now`, and only if the cutoff is still in
# the future — a tick that runs after the cutoff is auto-close's job,
# not pre-close's.
REMINDER_LEAD_MINUTES = 30


@dataclass
class PreCloseResult:
    scanned: int
    sent: int
    skipped_already_sent: int
    skipped_no_cutoff: int
    skipped_no_email: int


def _stylists_with_open_session(db: Session) -> list[StaffPunch]:
    users = db.query(User).filter(User.role.in_(("sales", "user", "admin"))).all()
    out: list[StaffPunch] = []
    for u in users:
        state, last = clock_in.current_status(db, u.id)
        if state == "in" and last is not None and last.direction == "in":
            out.append(last)
    return out


def _cutoff_for(
    db: Session, *, in_punch: StaffPunch, now_utc: datetime
) -> tuple[datetime, "datetime"] | None:
    """Return `(cutoff_utc, cutoff_local)` if this in-punch has a
    deterministic cutoff today (or after `now`), else None.

    Slice B precedence: shift's `auto_session_close_time` wins; the
    location's `default_auto_session_close_time` is the fallback. The
    fallback is preserved verbatim from Phase 7 per guardrail #4 —
    the only behavioral change is that a shift, when present, takes
    over.
    """
    tz = shop_tz()
    local_in = to_business_local(in_punch.punched_at)

    # 1) Shift cutoff first
    cutoff_time = None
    resolved = shift_resolver.resolve_active_shift(
        db, user_id=in_punch.user_id, as_of_local=local_in
    )
    if resolved is not None and resolved.auto_session_close_time is not None:
        cutoff_time = resolved.auto_session_close_time

    # 2) Location default fallback (existing Phase 7 path)
    if cutoff_time is None:
        if in_punch.location_id is None:
            return None
        location: StaffLocation | None = db.get(
            StaffLocation, in_punch.location_id
        )
        if location is None or location.default_auto_session_close_time is None:
            return None
        cutoff_time = location.default_auto_session_close_time

    cutoff_local = datetime.combine(local_in.date(), cutoff_time, tzinfo=tz)
    if cutoff_local <= local_in:
        cutoff_local += timedelta(days=1)
    cutoff_utc = cutoff_local.astimezone(timezone.utc)
    if cutoff_utc <= now_utc:
        # Past the cutoff — auto-close handles this; pre-close skips.
        return None
    return cutoff_utc, cutoff_local


def _send_email(
    *,
    to: str,
    stylist_name: str,
    cutoff_local: datetime,
) -> None:
    transport = email_transport.get_email_transport()
    cutoff_str = cutoff_local.strftime("%-I:%M %p")
    subject = f"Still working? Clock out by {cutoff_str}"
    text = (
        f"Hi {stylist_name},\n\n"
        f"You're still on the clock at Bellas. Your shift is set to "
        f"auto-close at {cutoff_str} today. If you're still working, "
        "tap the clock chip in the sales portal and confirm. If you're "
        "done, please clock out so the system records the right hours.\n\n"
        "— Bellas XV"
    )
    transport.send(
        EmailMessagePayload(
            to=to,
            subject=subject,
            text=text,
            html=None,
            reply_to=None,
        )
    )


def run_pre_close_pass(
    db: Session,
    *,
    now_override: datetime | None = None,
) -> PreCloseResult:
    """One pre-close tick.

    Walks every open session, computes the cutoff, and sends a
    reminder if `now` is inside the lead window. Idempotency comes
    from `attendance_pre_close_reminders` keyed by
    `(punch_id, cutoff_business_date)`.
    """
    now_utc = now_override or business_now().astimezone(timezone.utc)
    lead = timedelta(minutes=REMINDER_LEAD_MINUTES)

    candidates = _stylists_with_open_session(db)
    scanned = len(candidates)
    sent = 0
    skipped_already = 0
    skipped_no_cutoff = 0
    skipped_no_email = 0

    for in_punch in candidates:
        cutoff_pair = _cutoff_for(db, in_punch=in_punch, now_utc=now_utc)
        if cutoff_pair is None:
            skipped_no_cutoff += 1
            continue
        cutoff_utc, cutoff_local = cutoff_pair
        if cutoff_utc - now_utc > lead:
            skipped_no_cutoff += 1
            continue

        cutoff_business_date = cutoff_local.date()

        # Idempotency: try to insert; if the UNIQUE collides, the
        # reminder already went out for this (punch, cutoff).
        marker = AttendancePreCloseReminder(
            punch_id=in_punch.id,
            cutoff_business_date=cutoff_business_date,
        )
        sp = db.begin_nested()
        try:
            db.add(marker)
            db.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            skipped_already += 1
            continue

        user = db.get(User, in_punch.user_id)
        if user is None or not user.email:
            skipped_no_email += 1
            continue

        try:
            _send_email(
                to=user.email,
                stylist_name=user.full_name or user.username or "there",
                cutoff_local=cutoff_local,
            )
        except Exception:
            # If the send fails the marker row is still committed —
            # we never spam-resend on transient SMTP failure. Logging
            # the trace is enough; the next pass leaves this punch
            # alone.
            log.exception(
                "pre_close: SMTP send failed for punch_id=%s", in_punch.id
            )
            continue
        sent += 1

    db.flush()
    return PreCloseResult(
        scanned=scanned,
        sent=sent,
        skipped_already_sent=skipped_already,
        skipped_no_cutoff=skipped_no_cutoff,
        skipped_no_email=skipped_no_email,
    )


def tick(db: Session) -> PreCloseResult:
    """Worker entrypoint."""
    with cron_state.record_run(cron_state.PRE_CLOSE_REMINDER) as run:
        result = run_pre_close_pass(db)
        run.scanned = result.scanned
        run.changed = result.sent
        db.commit()
    return result
