"""Staff-created appointments — booking someone in from the CRM.

The public widget is the only thing that has ever put a real future
appointment on the calendar. Everything staff had was the walk-in
placeholder, which is backdated to now and already marked attended: a
receipt, not a booking. So a rep on the phone with a customer who wants to
come in Thursday had nowhere to put that, and the workaround was a note.

This service is the missing path. It creates a normal ``appointments`` row
— same table, same statuses, same calendar, same reminder machinery — with
``source='staff_created'`` and a ``booking_context`` recording what the rep
was doing (a phone call, a walk-in follow-up, an existing customer). It is
NOT a second booking system: the availability primitives below are the same
ones ``booking_service`` uses for the widget.

**Why staff booking cannot simply call ``slot_is_bookable``.** That
function answers a narrower question than it appears to: *would the public
widget have offered this slot?* It requires an active availability rule
whose weekday, start time, and duration all line up. Production's rules are
the boutique's inherited public hours — Wednesday through Sunday, 12:00 to
19:00, 45-minute grid, capacity 1 — so a strict reuse would reject every
Monday and Tuesday, every hour outside that window, every start time off
the 45-minute grid, and every second concurrent appointment anywhere on the
lot. Staff would hit 409 on most real bookings and correctly conclude the
feature is broken.

The published rules are a *marketing* artifact: what the store advertises
to strangers. A rep booking a customer they are talking to knows whether
the store will be open. So the conflicts split in two:

  HARD (409, the booking is refused):
    - the slot is in the past
    - it overlaps a blackout — someone deliberately blocked that time
    - the assigned rep already has a live appointment overlapping it.
      Double-booking a *person* is a real conflict; two reps with
      customers at the same time is a normal Saturday.

  SOFT (allowed, returned as warnings for the UI to show):
    - outside published availability rules
    - the shared per-slot capacity is already used

Capacity is deliberately soft. It is a single global number (1) describing
one fitting room, not a dealership with several reps, and hard-failing on
it would block the most ordinary booking there is. When the rules are
eventually re-cut for the dealership, tightening a warning into an error is
a one-line change; loosening a wrongly-hard error after staff have learned
to distrust the feature is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    Appointment,
    AppointmentBlackout,
    Contact,
    Event,
    User,
)
from modules.booking.services import booking_service
from modules.core.services import activity_log

log = logging.getLogger(__name__)

# Statuses that occupy a slot. Mirrors booking_service._LIVE_STATUSES —
# cancelled / no_show / attended / rescheduled rows do not block anything.
_LIVE_STATUSES = ("pending", "confirmed")

_DEFAULT_DURATION_MINUTES = 45
_MIN_DURATION_MINUTES = 15
_MAX_DURATION_MINUTES = 240

# Contexts a staff member can book under. The narrower lead-capture set
# lives in walk_in_service; this is the full vocabulary from migration 104.
STAFF_BOOKING_CONTEXTS = booking_service.BOOKING_CONTEXT_VALUES


class StaffAppointmentError(Exception):
    """Domain rejection — the router maps ``.code`` to an HTTP status."""

    def __init__(self, message: str, *, code: str, warnings=None) -> None:
        super().__init__(message)
        self.code = code
        self.warnings = list(warnings or [])


@dataclass(frozen=True)
class SlotEvaluation:
    """Why a slot is or is not bookable by staff."""

    hard_conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def bookable(self) -> bool:
        return not self.hard_conflicts


def evaluate_staff_slot(
    db: Session,
    *,
    slot_start: datetime,
    duration_minutes: int,
    assigned_user_id: int | None,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> SlotEvaluation:
    """Classify a proposed staff booking. See the module docstring for why
    the published availability rules are advisory here and blackouts,
    the past, and rep double-booking are not.
    """
    hard: list[str] = []
    warnings: list[str] = []

    if slot_start.tzinfo is None:
        return SlotEvaluation(hard_conflicts=["slot_start_not_timezone_aware"])

    now = now or datetime.now(timezone.utc)
    slot_end = slot_start + timedelta(minutes=duration_minutes)

    if slot_start < now:
        hard.append("slot_in_past")

    blackouts = (
        db.query(AppointmentBlackout)
        .filter(
            AppointmentBlackout.end_at > slot_start,
            AppointmentBlackout.start_at < slot_end,
        )
        .all()
    )
    if blackouts:
        hard.append("slot_in_blackout")

    if assigned_user_id is not None:
        clash = (
            db.query(Appointment)
            .filter(
                Appointment.assigned_user_id == assigned_user_id,
                Appointment.status.in_(_LIVE_STATUSES),
                Appointment.slot_end_at > slot_start,
                Appointment.slot_start_at < slot_end,
            )
        )
        if exclude_appointment_id is not None:
            clash = clash.filter(Appointment.id != exclude_appointment_id)
        if db.query(clash.exists()).scalar():
            hard.append("rep_double_booked")

    # --- Advisory: the same checks the public widget treats as absolute ---
    ok, _reason = booking_service.slot_is_bookable(
        db,
        slot_start=slot_start,
        slot_duration_minutes=duration_minutes,
        min_lead_minutes=0,
        now=now,
    )
    if not ok:
        tz = booking_service.shop_tz()
        on_date = slot_start.astimezone(tz).date()
        rules = _active_rules_for(db, on_date)
        if not rules:
            warnings.append("outside_published_hours")
        else:
            # A rule exists for the day but this exact slot did not match
            # it — off-grid start, different duration, or the shared
            # capacity is spoken for. Distinguish the last one, since it is
            # the only one a rep can act on.
            booked = (
                db.query(Appointment)
                .filter(
                    Appointment.status.in_(_LIVE_STATUSES),
                    Appointment.slot_end_at > slot_start,
                    Appointment.slot_start_at < slot_end,
                )
                .count()
            )
            if booked >= max(r.capacity for r in rules):
                warnings.append("slot_at_capacity")
            else:
                warnings.append("outside_published_hours")

    return SlotEvaluation(hard_conflicts=hard, warnings=warnings)


def _active_rules_for(db, on_date):
    from database.models import AppointmentAvailabilityRule

    rules = (
        db.query(AppointmentAvailabilityRule)
        .filter(AppointmentAvailabilityRule.active.is_(True))
        .all()
    )
    weekday = on_date.weekday()
    return [
        r
        for r in rules
        if r.weekday == weekday
        and (r.effective_from is None or on_date >= r.effective_from)
        and (r.effective_to is None or on_date <= r.effective_to)
    ]


@dataclass(frozen=True)
class StaffAppointmentResult:
    appointment: Appointment
    warnings: list[str]


def create_staff_appointment(
    db: Session,
    *,
    actor_user_id: int,
    slot_start: datetime,
    duration_minutes: int | None,
    booking_context: str,
    event_id: int | None = None,
    contact_id: int | None = None,
    assigned_user_id: int | None = None,
    internal_notes: str | None = None,
) -> StaffAppointmentResult:
    """Book a future appointment from the CRM.

    An anchor is required: ``event_id`` (book on a deal) or ``contact_id``
    (book a person with no deal yet). Passing an event resolves the contact
    from it, so the appointment is always attached to someone. Sending both
    is only allowed when they agree — see the mismatch check below.

    The caller owns the commit boundary, matching walk_in_service.
    """
    if booking_context not in STAFF_BOOKING_CONTEXTS:
        raise StaffAppointmentError(
            "booking_context must be one of: "
            + ", ".join(sorted(STAFF_BOOKING_CONTEXTS)),
            code="invalid_booking_context",
        )

    if event_id is None and contact_id is None:
        raise StaffAppointmentError(
            "an event_id or contact_id is required", code="missing_anchor"
        )

    event: Event | None = None
    if event_id is not None:
        event = db.get(Event, event_id)
        if event is None or event.deleted_at is not None:
            raise StaffAppointmentError("event not found", code="event_not_found")
        # The deal owns the identity. A caller may send the deal's own
        # contact redundantly, but a *different* one is rejected rather
        # than honored: writing contact_id=B with crm_event_id=A would
        # hang B's appointment off A's deal, and every surface that reads
        # one field without the other (the board, the rep's day, the
        # buyer-journey link) would then disagree about whose visit it is.
        if contact_id is not None and contact_id != event.primary_contact_id:
            raise StaffAppointmentError(
                "contact_id does not match the event's primary contact",
                code="contact_event_mismatch",
            )
        contact_id = event.primary_contact_id

    contact = db.get(Contact, contact_id) if contact_id else None
    if contact is None or contact.deleted_at is not None:
        raise StaffAppointmentError("contact not found", code="contact_not_found")

    minutes = duration_minutes or _DEFAULT_DURATION_MINUTES
    if not (_MIN_DURATION_MINUTES <= minutes <= _MAX_DURATION_MINUTES):
        raise StaffAppointmentError(
            f"duration must be between {_MIN_DURATION_MINUTES} and "
            f"{_MAX_DURATION_MINUTES} minutes",
            code="invalid_duration",
        )

    if slot_start.tzinfo is None:
        # A naive datetime from the SPA means shop-local wall time — the
        # same reading the reschedule route applies.
        slot_start = slot_start.replace(tzinfo=booking_service.shop_tz())
    slot_start_utc = slot_start.astimezone(timezone.utc)

    if assigned_user_id is not None:
        assignee = db.get(User, assigned_user_id)
        if assignee is None or not assignee.is_active:
            raise StaffAppointmentError(
                "assigned user not found or inactive", code="invalid_assigned_user_id"
            )

    evaluation = evaluate_staff_slot(
        db,
        slot_start=slot_start_utc,
        duration_minutes=minutes,
        assigned_user_id=assigned_user_id,
    )
    if not evaluation.bookable:
        raise StaffAppointmentError(
            "; ".join(evaluation.hard_conflicts),
            code="slot_conflict",
            warnings=evaluation.warnings,
        )

    first_name = (
        contact.first_name
        or (contact.display_name or "Customer").split(" ")[0]
    )[:100]
    appt = Appointment(
        confirmation_code=booking_service.generate_unique_confirmation_code(db),
        slot_start_at=slot_start_utc,
        slot_end_at=slot_start_utc + timedelta(minutes=minutes),
        slot_duration_minutes=minutes,
        timezone=APP_TIMEZONE,
        # celebrant_* are the legacy Bella's-era column names; they hold
        # the customer's name on a dealership row.
        celebrant_first_name=first_name,
        celebrant_last_name=(contact.last_name or None),
        # party size is meaningless for a vehicle visit; 'solo' is the
        # neutral NOT-NULL placeholder the CHECK allows, matching the
        # storefront lead path.
        party_size_bucket="solo",
        phone=(contact.phone or contact.phone_e164 or ""),
        phone_e164=contact.phone_e164,
        # email is NOT NULL; fall back to a placeholder like the walk-in
        # and storefront paths when the contact only left a phone.
        email=(contact.email or f"lead+{contact.id}@lead.local"),
        status="confirmed",
        source="staff_created",
        booking_context=booking_context,
        assigned_user_id=assigned_user_id,
        internal_notes=internal_notes or None,
        contact_id=contact.id,
        crm_event_id=event.id if event is not None else None,
        user_journey=[],
        bot_suspected=False,
        raw_payload={"source": "staff_created", "booking_context": booking_context},
    )
    db.add(appt)
    db.flush()

    if event is not None:
        activity_log.log_activity(
            db,
            event_id=event.id,
            actor_kind="staff",
            actor_user_id=actor_user_id,
            activity_type=activity_log.APPOINTMENT_SCHEDULED,
            subject_kind="appointment",
            subject_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "slot_start_at": appt.slot_start_at.isoformat(),
                "booking_context": booking_context,
                "assigned_user_id": assigned_user_id,
                # Warnings are part of the record: "booked outside published
                # hours" is exactly the kind of thing someone asks about
                # later.
                "warnings": evaluation.warnings,
            },
        )

    # Reuse the existing staff-notification path rather than inventing one.
    if assigned_user_id is not None:
        from modules.booking.services.staff_booking_notifications import (
            notify_booking_assigned,
        )

        notify_booking_assigned(db, appt, actor_user_id=actor_user_id)

    return StaffAppointmentResult(appointment=appt, warnings=evaluation.warnings)
