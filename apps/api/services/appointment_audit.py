"""Shared audit helpers for appointment writes.

Centralizes activity_log payload shapes used by more than one writer.
Lift to this module when an audit row must be emitted from both admin
and sales paths (or any other future surface) so the payload shape
stays one definition.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from services import activity_log


def log_notes_edited(
    db: Session,
    *,
    appointment_id: int,
    event_id: int,
    actor_user_id: int,
    prior_notes: str | None,
    new_notes: str | None,
) -> None:
    """Append an APPOINTMENT_NOTES_EDITED row with length-delta payload.

    The text itself is intentionally omitted; the activity_log table is
    read whole on every event detail load, so storing full diffs would
    balloon timeline-render costs. Length deltas are enough for the
    "who edited what when, and was it longer or shorter" question the
    timeline UI asks today.

    Caller owns the transaction. The helper does not commit. Caller is
    also responsible for change detection (only invoke when the notes
    value actually changed) and the linked-event check (only invoke
    when ``event_id`` is set — appointments without a linked CRM event
    have nowhere to anchor the audit row).
    """
    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPOINTMENT_NOTES_EDITED,
        subject_kind="appointment",
        subject_id=appointment_id,
        payload={
            "appointment_id": appointment_id,
            "prior_length": len(prior_notes or ""),
            "new_length": len(new_notes or ""),
        },
    )
