"""Shared service for participant buyer-journey tagging (Phase 10.3a).

A "buyer journey" is a participant's view of the rows attached to them
through ``event_participant_id``. With the B1 data model (per
docs/SALES_REP_DASHBOARD_PHASES.md 10.2), a journey is implicit — it's
the tuple of (event_participant, appointments tagged to it, quotes
tagged to it, invoices tagged to it) at any point in time. There is no
journey row to create or delete.

The mutation primitive here is **tagging**: setting or clearing the
``event_participant_id`` column on a single appointment, quote, or
invoice. Phase 10.3 ships the shared tagging primitive across those
three buyer rows without introducing a separate buyer_journey table.

Validation rules (in order):
  - The appointment must exist (404 otherwise).
  - If attaching: the participant must exist (404 otherwise) and must
    belong to the same event as the appointment (400 otherwise). Events
    stay shared; a buyer journey lives UNDER the event, never across
    events.
  - If detaching (``event_participant_id=None``): no participant lookup
    needed; the column is just cleared.

Audit:
  - Writes ``appointment.participant_attached`` with payload carrying
    ``from_event_participant_id`` and ``to_event_participant_id``.
    Detach is the same kind with ``to=None`` so a timeline renderer can
    cover both attach and detach with one branch.
  - Skipped on no-op (column already at the target value), matching the
    pattern in ``sales_assignment.reassign_appointment``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import Appointment, EventParticipant, Invoice, Quote
from services import activity_log


class BuyerJourneyError(Exception):
    """Domain rejection. Router maps ``.code`` to an HTTP status."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def attach_appointment_to_participant(
    db: Session,
    *,
    appointment_id: int,
    event_participant_id: int | None,
    actor_user_id: int,
) -> Appointment:
    """Tag an appointment with a specific event_participant (or clear).

    Caller owns the commit boundary. Returns the updated Appointment
    after the flush; the activity row is appended in the same session.
    """
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise BuyerJourneyError(
            "appointment not found", code="appointment_not_found"
        )

    target = event_participant_id
    if target is not None:
        participant = db.get(EventParticipant, target)
        if participant is None or participant.deleted_at is not None:
            raise BuyerJourneyError(
                "event participant not found",
                code="participant_not_found",
            )
        if appt.crm_event_id is None:
            raise BuyerJourneyError(
                "appointment is not linked to a CRM event",
                code="appointment_unlinked_from_event",
            )
        if participant.event_id != appt.crm_event_id:
            raise BuyerJourneyError(
                "participant belongs to a different event",
                code="participant_event_mismatch",
            )

    from_id = appt.event_participant_id
    if from_id == target:
        # No-op; skip the audit row so the timeline isn't filled with
        # noise from double-taps and idempotent retries.
        return appt

    appt.event_participant_id = target
    db.flush()

    # Anchor the audit row to the appointment's linked event so the
    # event-detail timeline shows the tagging. Appointments with no
    # crm_event_id get the column updated but no audit row — matches
    # the established pattern for appointment.* activity rows.
    if appt.crm_event_id is not None:
        activity_log.log_activity(
            db,
            event_id=appt.crm_event_id,
            actor_kind="staff",
            actor_user_id=actor_user_id,
            activity_type=activity_log.APPOINTMENT_PARTICIPANT_ATTACHED,
            subject_kind="appointment",
            subject_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "from_event_participant_id": from_id,
                "to_event_participant_id": target,
            },
        )

    return appt


def attach_quote_to_participant(
    db: Session,
    *,
    quote_id: int,
    event_participant_id: int | None,
    actor_user_id: int,
) -> Quote:
    """Tag a quote with a specific event_participant (or clear).

    Same validation contract as ``attach_appointment_to_participant``.
    Soft-deleted quotes are treated as not found so callers cannot
    silently revive a deleted row by tagging it. Quotes always have a
    NOT NULL ``event_id`` (the ON DELETE RESTRICT FK enforces this), so
    the "no event" branch the appointment service needs doesn't apply
    here.
    """
    quote = db.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise BuyerJourneyError("quote not found", code="quote_not_found")

    target = event_participant_id
    if target is not None:
        participant = db.get(EventParticipant, target)
        if participant is None or participant.deleted_at is not None:
            raise BuyerJourneyError(
                "event participant not found",
                code="participant_not_found",
            )
        if participant.event_id != quote.event_id:
            raise BuyerJourneyError(
                "participant belongs to a different event",
                code="participant_event_mismatch",
            )

    from_id = quote.event_participant_id
    if from_id == target:
        return quote

    quote.event_participant_id = target
    db.flush()

    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_PARTICIPANT_ATTACHED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_id": quote.id,
            "from_event_participant_id": from_id,
            "to_event_participant_id": target,
        },
    )
    return quote


def attach_invoice_to_participant(
    db: Session,
    *,
    invoice_id: int,
    event_participant_id: int | None,
    actor_user_id: int,
) -> Invoice:
    """Tag an invoice with a specific event_participant (or clear).

    Same shape as the quote tagging path; invoices also carry a NOT NULL
    ``event_id`` and a soft-delete ``deleted_at`` column.
    """
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        raise BuyerJourneyError("invoice not found", code="invoice_not_found")

    target = event_participant_id
    if target is not None:
        participant = db.get(EventParticipant, target)
        if participant is None or participant.deleted_at is not None:
            raise BuyerJourneyError(
                "event participant not found",
                code="participant_not_found",
            )
        if participant.event_id != invoice.event_id:
            raise BuyerJourneyError(
                "participant belongs to a different event",
                code="participant_event_mismatch",
            )

    from_id = invoice.event_participant_id
    if from_id == target:
        return invoice

    invoice.event_participant_id = target
    db.flush()

    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_PARTICIPANT_ATTACHED,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "invoice_id": invoice.id,
            "from_event_participant_id": from_id,
            "to_event_participant_id": target,
        },
    )
    return invoice
