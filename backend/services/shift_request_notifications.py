"""Staff notifications for the shift-request flow (Scheduling Phase 2).

Thin wrappers over ``notification_routing.record_event`` so every
cover/drop transition writes an in-app staff notification event (always)
and, because the kinds are registered with the generic
``render_staff_simple_notice`` renderer, fans out an email for the
actionable steps (Gate 4: in-app always + email for actionable).

Each event names its recipient explicitly via
``payload['recipient_user_id']`` (the requester, the named candidate, or
the assignee added/removed by a cover) — see
``notification_routing._explicit_recipient``. The renderer reads
``headline`` / ``message`` / ``details`` from the same payload, so the
copy lives here at the call site.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import StaffScheduleEntry, User
from services import notification_routing
from services.business_time import to_business_local


def _window(entry: StaffScheduleEntry | None) -> str:
    if entry is None:
        return ""
    start = to_business_local(entry.starts_at_local)
    end = to_business_local(entry.ends_at_local)
    day = start.strftime("%a, %b %-d")
    return f"{day}, {start.strftime('%-I:%M %p')} to {end.strftime('%-I:%M %p')}"


def _name(user: User | None) -> str:
    if user is None:
        return "a coworker"
    return user.full_name or user.username


def _emit(
    db: Session,
    *,
    kind: str,
    request_id: int,
    recipient_user_id: int,
    actor_user_id: int | None,
    headline: str,
    message: str,
    details: list | None = None,
) -> None:
    notification_routing.record_event(
        db,
        kind=kind,
        subject_kind="shift_request",
        subject_id=request_id,
        actor_user_id=actor_user_id,
        payload={
            "recipient_user_id": recipient_user_id,
            "headline": headline,
            "message": message,
            "details": details or [],
        },
    )


def notify_cover_requested(
    db: Session,
    *,
    request_id: int,
    candidate: User,
    requester: User,
    source_entry: StaffScheduleEntry,
) -> None:
    """A direct cover names a candidate — ask them to accept."""
    window = _window(source_entry)
    _emit(
        db,
        kind="staff.shift_cover_requested",
        request_id=request_id,
        recipient_user_id=candidate.id,
        actor_user_id=requester.id,
        headline="A coworker asked you to cover a shift",
        message=(
            f"{_name(requester)} asked if you can cover their shift. "
            "Open your schedule to accept or decline."
        ),
        details=[["Shift", window], ["Requested by", _name(requester)]],
    )


def notify_cover_accepted(
    db: Session,
    *,
    request_id: int,
    requester: User,
    candidate: User,
    source_entry: StaffScheduleEntry,
) -> None:
    """Candidate accepted — let the requester know it's pending approval."""
    window = _window(source_entry)
    _emit(
        db,
        kind="staff.shift_cover_accepted",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=candidate.id,
        headline="Your cover request was accepted",
        message=(
            f"{_name(candidate)} accepted your cover request. A manager "
            "will review and approve it."
        ),
        details=[["Shift", window], ["Accepted by", _name(candidate)]],
    )


def notify_cover_approved(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int,
    requester: User,
    candidate: User,
    source_entry: StaffScheduleEntry,
) -> None:
    """Cover approved — the requester is removed, the candidate is added."""
    window = _window(source_entry)
    _emit(
        db,
        kind="staff.shift_cover_approved",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=actor_user_id,
        headline="Your shift is covered",
        message=(
            f"A manager approved {_name(candidate)} to cover your shift. "
            "You're no longer scheduled for it."
        ),
        details=[["Shift", window], ["Covered by", _name(candidate)]],
    )
    _emit(
        db,
        kind="staff.shift_cover_approved",
        request_id=request_id,
        recipient_user_id=candidate.id,
        actor_user_id=actor_user_id,
        headline="You've been added to a shift",
        message=(
            f"A manager approved you to cover {_name(requester)}'s shift. "
            "It's now on your schedule."
        ),
        details=[["Shift", window], ["Covering for", _name(requester)]],
    )


def notify_cover_denied(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int | None,
    requester: User,
    candidate: User | None,
    source_entry: StaffScheduleEntry | None,
    notes: str | None = None,
    declined: bool = False,
) -> None:
    """Cover denied by a manager, or declined by the candidate."""
    window = _window(source_entry)
    detail = [["Shift", window]]
    if notes:
        detail.append(["Note", notes])
    if declined:
        message = (
            f"{_name(candidate)} declined your cover request. Your shift is "
            "unchanged — you can ask someone else."
        )
    else:
        message = (
            "A manager declined your cover request. Your shift is unchanged."
        )
    _emit(
        db,
        kind="staff.shift_cover_denied",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=actor_user_id,
        headline="Your cover request was declined",
        message=message,
        details=detail,
    )
    if candidate is not None and not declined:
        _emit(
            db,
            kind="staff.shift_cover_denied",
            request_id=request_id,
            recipient_user_id=candidate.id,
            actor_user_id=actor_user_id,
            headline="A cover request was declined",
            message=(
                "The cover request you accepted was not approved by a "
                "manager. No change to your schedule."
            ),
            details=[["Shift", window]],
        )


def notify_swap_requested(
    db: Session,
    *,
    request_id: int,
    candidate: User,
    requester: User,
    source_entry: StaffScheduleEntry,
    target_entry: StaffScheduleEntry,
) -> None:
    """A coworker proposed trading shifts with you — accept or decline."""
    _emit(
        db,
        kind="staff.shift_swap_requested",
        request_id=request_id,
        recipient_user_id=candidate.id,
        actor_user_id=requester.id,
        headline="A coworker proposed a shift swap",
        message=(
            f"{_name(requester)} wants to swap shifts with you. Open your "
            "schedule to accept or decline."
        ),
        details=[
            ["You'd give up", _window(target_entry)],
            ["You'd take", _window(source_entry)],
        ],
    )


def notify_swap_accepted(
    db: Session,
    *,
    request_id: int,
    requester: User,
    candidate: User,
    source_entry: StaffScheduleEntry,
    target_entry: StaffScheduleEntry,
) -> None:
    _emit(
        db,
        kind="staff.shift_swap_accepted",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=candidate.id,
        headline="Your swap was accepted",
        message=(
            f"{_name(candidate)} accepted your swap. A manager will review "
            "and approve it."
        ),
        details=[
            ["You'd give up", _window(source_entry)],
            ["You'd take", _window(target_entry)],
        ],
    )


def notify_swap_approved(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int,
    requester: User,
    candidate: User,
    source_entry: StaffScheduleEntry,
    target_entry: StaffScheduleEntry,
) -> None:
    """Swap approved — each staffer now works the other's shift."""
    _emit(
        db,
        kind="staff.shift_swap_approved",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=actor_user_id,
        headline="Your shift swap is approved",
        message=(
            f"A manager approved your swap with {_name(candidate)}. Your "
            "schedule has been updated."
        ),
        details=[
            ["No longer working", _window(source_entry)],
            ["Now working", _window(target_entry)],
        ],
    )
    _emit(
        db,
        kind="staff.shift_swap_approved",
        request_id=request_id,
        recipient_user_id=candidate.id,
        actor_user_id=actor_user_id,
        headline="Your shift swap is approved",
        message=(
            f"A manager approved your swap with {_name(requester)}. Your "
            "schedule has been updated."
        ),
        details=[
            ["No longer working", _window(target_entry)],
            ["Now working", _window(source_entry)],
        ],
    )


def notify_swap_denied(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int | None,
    requester: User,
    candidate: User | None,
    source_entry: StaffScheduleEntry | None,
    notes: str | None = None,
    declined: bool = False,
) -> None:
    """Swap denied by a manager, or declined by the coworker."""
    detail = []
    if notes:
        detail.append(["Note", notes])
    if declined:
        msg_requester = (
            f"{_name(candidate)} declined your swap. Both schedules are "
            "unchanged."
        )
    else:
        msg_requester = (
            "A manager declined your swap. Both schedules are unchanged."
        )
    _emit(
        db,
        kind="staff.shift_swap_denied",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=actor_user_id,
        headline="Your shift swap was declined",
        message=msg_requester,
        details=detail,
    )
    if candidate is not None and not declined:
        _emit(
            db,
            kind="staff.shift_swap_denied",
            request_id=request_id,
            recipient_user_id=candidate.id,
            actor_user_id=actor_user_id,
            headline="A shift swap was declined",
            message=(
                "The swap you accepted was not approved by a manager. No "
                "change to your schedule."
            ),
            details=[],
        )


def notify_pickup_denied(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int,
    requester: User,
    notes: str | None = None,
) -> None:
    """A manager denied a staffer's open-shift claim. (Approved claims are
    announced by the staff.shift_added event the new entry emits.)"""
    detail = []
    if notes:
        detail.append(["Note", notes])
    _emit(
        db,
        kind="staff.shift_pickup_denied",
        request_id=request_id,
        recipient_user_id=requester.id,
        actor_user_id=actor_user_id,
        headline="Your shift claim was declined",
        message=(
            "A manager declined your claim on an open shift. It may have "
            "been filled by someone else."
        ),
        details=detail,
    )


def notify_drop_decided(
    db: Session,
    *,
    request_id: int,
    actor_user_id: int,
    requester: User,
    source_entry: StaffScheduleEntry | None,
    approved: bool,
    notes: str | None = None,
) -> None:
    window = _window(source_entry)
    detail = [["Shift", window]]
    if notes:
        detail.append(["Note", notes])
    if approved:
        _emit(
            db,
            kind="staff.shift_drop_approved",
            request_id=request_id,
            recipient_user_id=requester.id,
            actor_user_id=actor_user_id,
            headline="Your shift was dropped",
            message=(
                "A manager approved your drop request. You're no longer "
                "scheduled for this shift."
            ),
            details=detail,
        )
    else:
        _emit(
            db,
            kind="staff.shift_drop_denied",
            request_id=request_id,
            recipient_user_id=requester.id,
            actor_user_id=actor_user_id,
            headline="Your drop request was declined",
            message=(
                "A manager declined your drop request. You're still "
                "scheduled for this shift."
            ),
            details=detail,
        )
