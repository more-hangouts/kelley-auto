"""Add-participant flow (Phase 6 of the Sales Portal).

Single service entry point used by both the admin event Overview and
the sales appointment detail. Anchors the principle that no
participant exists without a contact: the function always lands one
`contacts` row (creating or reusing) and one `event_participants` row
linked to it.

Caller passes raw phone + email + names. We normalize phone to E.164
via `services.booking_service.normalize_phone_e164`, then route through
`contact_service.find_or_create_contact` which returns
`(contact, was_new)` directly — no parallel pre-lookup needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Event, EventParticipant
from services import activity_log, booking_service, contact_service


class EventParticipantError(Exception):
    """Stable error codes the router maps to HTTP statuses."""

    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _compose_display_name(first: str, last: str | None) -> str:
    return " ".join(p for p in (first, last) if p).strip()


def add_event_participant(
    db: Session,
    *,
    event_id: int,
    parent_first_name: str,
    parent_last_name: str | None,
    celebrant_first_name: str,
    celebrant_last_name: str | None,
    phone: str,
    email: str | None,
    role: str,
    party_size_bucket: str | None,
    actor_user_id: int | None,
    actor_kind: str,
) -> dict:
    """Create one event_participants row, find-or-create its contact.

    Returns a dict the router serializes:
        {
          "participant": EventParticipant,
          "contact": Contact,
          "was_new_contact": bool,
        }

    Raises EventParticipantError on missing event or unparseable phone.
    """
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise EventParticipantError("event_not_found", http_status=404)

    phone_e164 = booking_service.normalize_phone_e164(phone)
    if not phone_e164:
        raise EventParticipantError("phone_invalid", http_status=422)

    normalized_email = email.lower() if email else None

    contact, was_new_contact = contact_service.find_or_create_contact(
        db,
        phone_e164=phone_e164,
        email=normalized_email,
        phone=phone,
        first_name=parent_first_name,
        last_name=parent_last_name,
    )

    display_name = _compose_display_name(
        celebrant_first_name, celebrant_last_name
    )
    measurements: dict = {}
    if party_size_bucket:
        measurements["party_size_bucket"] = party_size_bucket

    participant = EventParticipant(
        event_id=event.id,
        contact_id=contact.id,
        role=role,
        display_name=display_name,
        phone=phone,
        email=normalized_email,
        measurements=measurements,
    )
    db.add(participant)
    db.flush()

    activity_log.log_activity(
        db,
        event_id=event.id,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_PARTICIPANT_ADDED,
        subject_kind="contact",
        subject_id=contact.id,
        payload={
            "participant_id": participant.id,
            "display_name": participant.display_name,
            "role": participant.role,
            "party_size_bucket": party_size_bucket,
            "was_new_contact": was_new_contact,
        },
    )
    db.flush()

    return {
        "participant": participant,
        "contact": contact,
        "was_new_contact": was_new_contact,
    }


# ---------------------------------------------------------------------------
# Archive / restore (D3 of docs/CRM_RECORD_DELETION_PLAN.md)
# ---------------------------------------------------------------------------


def archive_event_participant(
    db: Session,
    *,
    participant_id: int,
    actor_user_id: int | None,
    reason: str,
    note: str | None = None,
) -> EventParticipant:
    """Soft-delete a participant row. Idempotent on already-archived
    rows.

    Refuses if the participant is the sole active quinceañera on its
    event, or if it backs an active invoice or quote via
    ``event_participant_id`` (the dependency report enforces both)."""
    from services import record_dependencies  # avoid import cycle

    record_dependencies.validate_archive_reason(reason)

    participant = db.get(EventParticipant, participant_id)
    if participant is None:
        raise EventParticipantError("participant_not_found", http_status=404)
    if participant.deleted_at is not None:
        return participant

    report = record_dependencies.get_record_dependencies(
        db, entity_type="event_participant", entity_id=participant_id
    )
    if not report.can_archive:
        raise EventParticipantError(
            "archive_blocked",
            http_status=409,
        )

    now = datetime.now(timezone.utc)
    participant.deleted_at = now
    # Flip the status to 'removed' so the legacy partial unique
    # predicate (which also keys on status='active') stays coherent
    # alongside the new deleted_at predicate.
    if participant.status == "active":
        participant.status = "removed"
    participant.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(participant.event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_PARTICIPANT_ARCHIVED,
        subject_kind="event_participant",
        subject_id=int(participant_id),
        payload={
            "reason": reason,
            "note": note,
            "role": participant.role,
            "display_name": participant.display_name,
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return participant


def restore_event_participant(
    db: Session,
    *,
    participant_id: int,
    actor_user_id: int | None,
) -> EventParticipant:
    """Lift the archive on a participant. Idempotent on live rows.

    Refuses when:
      - the parent event is archived (orphan would not render); or
      - the participant's role is ``'quinceanera'`` and another
        active live quinceañera already occupies the event slot.
    """
    from services import record_dependencies

    participant = db.get(EventParticipant, participant_id)
    if participant is None:
        raise EventParticipantError("participant_not_found", http_status=404)
    if participant.deleted_at is None:
        return participant

    event = db.get(Event, participant.event_id)
    if event is None or event.deleted_at is not None:
        raise EventParticipantError("parent_archived", http_status=409)

    if participant.role == "quinceanera":
        sibling = (
            db.query(func.count(EventParticipant.id))
            .filter(EventParticipant.event_id == participant.event_id)
            .filter(EventParticipant.id != participant_id)
            .filter(EventParticipant.role == "quinceanera")
            .filter(EventParticipant.status == "active")
            .filter(EventParticipant.deleted_at.is_(None))
            .scalar()
            or 0
        )
        if int(sibling) > 0:
            raise EventParticipantError(
                "quinceanera_slot_taken", http_status=409
            )

    report = record_dependencies.get_record_dependencies(
        db, entity_type="event_participant", entity_id=participant_id
    )

    now = datetime.now(timezone.utc)
    participant.deleted_at = None
    # If the row was 'removed' at archive time, leave it 'removed' —
    # status and the archive bit are independent. Staff can flip it
    # back to 'active' explicitly from the participant editor.
    participant.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(participant.event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_PARTICIPANT_RESTORED,
        subject_kind="event_participant",
        subject_id=int(participant_id),
        payload={
            "role": participant.role,
            "display_name": participant.display_name,
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return participant
