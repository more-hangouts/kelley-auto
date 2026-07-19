"""Thin helpers for the staff.booking_* event surfaces.

Lives at the service layer because routing kinds belongs to a domain
shared by sales (`sales_assignment`, `walk_in_service`) AND the
customer + admin booking paths (`api/routers/booking`, `admin_booking`).
A dedicated module avoids backwards imports (a customer-facing router
shouldn't have to reach into `services/sales_assignment.py` to fire a
staff email).

Both helpers resolve the recipient via intrinsic targeting in
``services.notification_routing`` — specifically
``_assigned_stylist_of_appointment``, which reads
``appt.assigned_user_id`` at fan-out time. **Call-site ordering matters:**

  - "Booking now assigned to X" → call ``notify_booking_assigned``
    AFTER ``appt.assigned_user_id`` has been written to X. Intrinsic
    targeting resolves to X. (Walk-in create and assignment writes.)

  - "Booking moved off Y's calendar" → call ``notify_booking_cancelled``
    BEFORE ``appt.assigned_user_id`` is rewritten to None / a new
    stylist. Intrinsic targeting resolves to Y, who gets the
    "appointment cancelled / moved off" email. By the time Y's job is
    dispatched the row may already point at someone else; the
    cancelled renderer doesn't depend on current assignment, only on
    the slot/customer info, so the email reads correctly either way.

  - "Customer cancelled the appointment" → call
    ``notify_booking_cancelled`` AFTER the appointment's status flips
    to cancelled. ``assigned_user_id`` stays put through a status
    change, so intrinsic targeting still resolves to the right stylist.

Both helpers are no-ops when the resolved assignee is None — there's
no one to notify. They never enqueue for inactive sales users; that
guardrail lives inside ``recipients_for``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import Appointment


def notify_booking_assigned(
    db: Session, appt: Appointment, *, actor_user_id: int | None
) -> None:
    """Fire ``staff.booking_assigned`` for the appointment's current assignee."""
    if appt.assigned_user_id is None:
        return
    from config.settings import ADMIN_BASE_URL
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.booking_assigned",
        subject_kind="appointment",
        subject_id=appt.id,
        actor_user_id=actor_user_id,
        payload={
            "admin_url": f"{ADMIN_BASE_URL}/appointments/{appt.id}",
        },
    )


def notify_booking_cancelled(
    db: Session, appt: Appointment, *, actor_user_id: int | None
) -> None:
    """Fire ``staff.booking_cancelled`` for the appointment's current assignee.

    Caller controls ordering — see the module docstring for the
    reassign-loss vs status-cancel ordering rules. ``actor_user_id``
    is None for customer-initiated cancellations (no staff actor on
    record), int for staff-initiated cancel / reassign-loss paths.
    """
    if appt.assigned_user_id is None:
        return
    from config.settings import ADMIN_BASE_URL
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.booking_cancelled",
        subject_kind="appointment",
        subject_id=appt.id,
        actor_user_id=actor_user_id,
        payload={
            "admin_url": f"{ADMIN_BASE_URL}/appointments/{appt.id}",
        },
    )


def notify_booking_rescheduled(
    db: Session,
    appt: Appointment,
    *,
    previous_slot_start_at,
    actor_user_id: int | None,
) -> None:
    """Fire ``staff.booking_rescheduled`` for the appointment's current
    assignee.

    The ``appointment`` passed downstream is the **new** state (new
    slot, same stylist); ``previous_slot_start_at`` is the slot the
    booking moved from. The customer reschedule flow carries
    ``assigned_user_id`` forward onto the new row so the stylist who
    owned the original slot keeps the booking; this helper expects
    that invariant — if the new row is unassigned, there is no one
    to notify.

    ``previous_slot_start_at`` is serialized to an ISO string in the
    payload so it survives the JSONB round-trip. The dispatcher's
    ``_normalize_staff_payload`` coerces it back to a ``datetime``
    via the ``_at``-suffix heuristic before the renderer is called.

    Per the renderer's own docstring: a stylist who lost the
    assignment entirely gets ``staff.booking_cancelled`` instead — do
    NOT call this helper for that case.
    """
    if appt.assigned_user_id is None:
        return
    from config.settings import ADMIN_BASE_URL
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.booking_rescheduled",
        subject_kind="appointment",
        subject_id=appt.id,
        actor_user_id=actor_user_id,
        payload={
            "admin_url": f"{ADMIN_BASE_URL}/appointments/{appt.id}",
            "previous_slot_start_at": previous_slot_start_at.isoformat(),
        },
    )
