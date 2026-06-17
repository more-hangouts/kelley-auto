"""Sales-portal assignment writes (Phase 6).

Two mutation primitives, both audited:

  - ``reassign_appointment``: sets ``appointments.assigned_user_id``.
    No cascade — a single appointment is moved between stylists.
  - ``reassign_event_lead``: sets ``events.owner_user_id`` AND
    cascades the new assignee onto every appointment tied to the
    event with ``slot_start_at >= NOW()``. Past-dated appointments
    are left untouched so commission and historical attribution
    stay accurate.

Audit shape (Phase 6 decision-locked in docs/SALES_REP_DASHBOARD_PHASES.md):
  - Appointment reassignment: one ``appointment.reassigned`` row,
    anchored to the appointment's ``crm_event_id`` (skipped when the
    appointment has no linked event, matching the established pattern
    for appointment.* activity rows).
  - Lead reassignment: one ``event.reassigned`` parent row, plus one
    ``appointment.reassigned`` row per cascaded appointment. Both
    payloads carry ``{from_user_id, to_user_id, reason}``; the
    cascade-child rows also carry ``via: "lead_cascade"`` so the
    timeline can render "cascaded from lead reassignment" rather than
    "stylist manually reassigned this appointment."

Validation of the new assignee is delegated to ``sales_staff`` so the
filter stays in one place (the Phase 6 picker, the Phase 5 walk-in
endpoint, and both PATCHes here all share it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Appointment, Event, User
from services import activity_log, sales_staff
from services.staff_booking_notifications import (
    notify_booking_assigned,
    notify_booking_cancelled,
)


class SalesAssignmentError(Exception):
    """Domain rejection. Router maps ``.code`` to an HTTP status."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LeadReassignmentResult:
    event: Event
    cascaded_appointment_ids: list[int]


# Default audit-row `reason` field. Per-call override added in Phase 11
# so admin lead-owner reassignments can tag distinctly from sales
# (`reason="admin_owner_change"`); the default keeps sales semantics
# unchanged for existing callers.
_REASON = "sales_reassignment"


def reassign_appointment(
    db: Session,
    *,
    appointment_id: int,
    new_assignee_id: int | None,
    actor_user_id: int,
) -> Appointment:
    """Move one appointment to a new stylist (or unassign with ``None``).

    Caller owns the commit boundary. Returns the updated Appointment
    after a flush; the activity row is appended in the same session.
    """
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise SalesAssignmentError(
            "appointment not found", code="appointment_not_found"
        )

    if new_assignee_id is not None and not sales_staff.is_assignable_sales_user(
        db, new_assignee_id
    ):
        raise SalesAssignmentError(
            "assignee must be an active sales user",
            code="invalid_assigned_user_id",
        )

    from_id = appt.assigned_user_id
    if from_id == new_assignee_id:
        # No-op; skip the audit row so the timeline isn't filled with
        # noise from double-taps and idempotent retries.
        return appt

    # "You lost this booking" fires BEFORE the assignee column is
    # rewritten so intrinsic targeting still resolves to the previous
    # owner. Skipped silently when from_id is None (first-time
    # assignment has no previous stylist to notify).
    if from_id is not None:
        notify_booking_cancelled(db, appt, actor_user_id=actor_user_id)

    appt.assigned_user_id = new_assignee_id
    db.flush()

    # The audit row anchors to the appointment's linked event so the
    # event-detail timeline shows the move. Appointments with no
    # crm_event_id (rare; legacy widget rows pre-promotion) get the
    # field updated but no audit row — matches APPOINTMENT_ARRIVED /
    # APPOINTMENT_NOTES_EDITED behavior elsewhere in this service.
    if appt.crm_event_id is not None:
        activity_log.log_activity(
            db,
            event_id=appt.crm_event_id,
            actor_kind="staff",
            actor_user_id=actor_user_id,
            activity_type=activity_log.APPOINTMENT_REASSIGNED,
            subject_kind="appointment",
            subject_id=appt.id,
            payload={
                "from_user_id": from_id,
                "to_user_id": new_assignee_id,
                "reason": _REASON,
            },
        )

    notify_booking_assigned(db, appt, actor_user_id=actor_user_id)
    return appt


def reassign_event_lead(
    db: Session,
    *,
    event_id: int,
    new_owner_id: int | None,
    actor_user_id: int,
    reason: str = _REASON,
) -> LeadReassignmentResult:
    """Move a lead to a new owner and cascade to future appointments.

    Updates ``events.owner_user_id`` and, in the same transaction,
    sets ``appointments.assigned_user_id`` on every appointment with
    ``crm_event_id == event_id`` and ``slot_start_at >= NOW()``.
    Past-dated appointments are untouched. Writes one
    ``event.reassigned`` row plus one ``appointment.reassigned`` row
    per cascaded appointment (skipping appointment audit rows for
    rows that already had ``assigned_user_id == new_owner_id``).

    Caller owns the commit.
    """
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise SalesAssignmentError("event not found", code="event_not_found")

    if new_owner_id is not None and not sales_staff.is_assignable_sales_user(
        db, new_owner_id
    ):
        raise SalesAssignmentError(
            "owner must be an active sales user",
            code="invalid_assigned_user_id",
        )

    from_owner_id = event.owner_user_id
    if from_owner_id == new_owner_id:
        return LeadReassignmentResult(event=event, cascaded_appointment_ids=[])

    event.owner_user_id = new_owner_id
    db.flush()

    activity_log.log_activity(
        db,
        event_id=event.id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_REASSIGNED,
        subject_kind="event",
        subject_id=event.id,
        payload={
            "from_user_id": from_owner_id,
            "to_user_id": new_owner_id,
            "reason": reason,
        },
    )

    # Cascade to future-dated appointments only. The cutoff is
    # business-naive wall-clock — sales reassignment is about "who
    # works the next appointment", not a midnight boundary, so UTC
    # NOW() is the right comparison.
    cascade_cutoff = datetime.now(timezone.utc)
    future_appts = (
        db.query(Appointment)
        .filter(Appointment.crm_event_id == event.id)
        .filter(Appointment.slot_start_at >= cascade_cutoff)
        .all()
    )
    cascaded_ids: list[int] = []
    for appt in future_appts:
        appt_from = appt.assigned_user_id
        if appt_from == new_owner_id:
            # No real change; don't write an appointment audit row
            # just to say "still assigned to B." The lead-level row
            # already records the broader reassignment.
            appt.assigned_user_id = new_owner_id
            continue
        # Same ordering rule as single-appointment reassign: emit the
        # "you lost it" event BEFORE rewriting the assignee column so
        # intrinsic targeting resolves to the previous owner.
        if appt_from is not None:
            notify_booking_cancelled(db, appt, actor_user_id=actor_user_id)
        appt.assigned_user_id = new_owner_id
        db.flush()
        notify_booking_assigned(db, appt, actor_user_id=actor_user_id)
        activity_log.log_activity(
            db,
            event_id=event.id,
            actor_kind="staff",
            actor_user_id=actor_user_id,
            activity_type=activity_log.APPOINTMENT_REASSIGNED,
            subject_kind="appointment",
            subject_id=appt.id,
            payload={
                "from_user_id": appt_from,
                "to_user_id": new_owner_id,
                "reason": reason,
                "via": "lead_cascade",
            },
        )
        cascaded_ids.append(appt.id)

    return LeadReassignmentResult(
        event=event, cascaded_appointment_ids=cascaded_ids
    )


def lead_cascade_preview(db: Session, *, event_id: int) -> dict | None:
    """Return the future appointments a lead reassignment would cascade onto.

    Pure read; no mutation. Returns ``None`` if the event is missing
    (caller maps to 404). Same query as ``reassign_event_lead``: the
    cutoff is ``slot_start_at >= NOW()`` in UTC, ordered chronologically
    so the dialog can render the list as it appears on the schedule.

    User-name resolution is batched into a single ``users`` lookup
    covering the event owner and every appointment's current assignee.
    """
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        return None

    cascade_cutoff = datetime.now(timezone.utc)
    future_appts = (
        db.query(Appointment)
        .filter(Appointment.crm_event_id == event.id)
        .filter(Appointment.slot_start_at >= cascade_cutoff)
        .order_by(Appointment.slot_start_at)
        .all()
    )

    user_ids: set[int] = set()
    if event.owner_user_id is not None:
        user_ids.add(event.owner_user_id)
    for appt in future_appts:
        if appt.assigned_user_id is not None:
            user_ids.add(appt.assigned_user_id)

    name_map: dict[int, str] = {}
    if user_ids:
        users = db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        name_map = {u.id: (u.full_name or u.username) for u in users}

    return {
        "event_id": event.id,
        "event_owner_user_id": event.owner_user_id,
        "event_owner_full_name": (
            name_map.get(event.owner_user_id)
            if event.owner_user_id is not None
            else None
        ),
        "future_appointments": [
            {
                "id": appt.id,
                "slot_start_at": appt.slot_start_at,
                "celebrant_first_name": appt.celebrant_first_name,
                "celebrant_last_name": appt.celebrant_last_name,
                "assigned_user_id": appt.assigned_user_id,
                "assigned_user_full_name": (
                    name_map.get(appt.assigned_user_id)
                    if appt.assigned_user_id is not None
                    else None
                ),
            }
            for appt in future_appts
        ],
    }
