"""Event domain service: promotion, status transitions, kanban board reads.

Keeps SQL/ORM choices out of the router so the API surface stays thin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import case, func, select, union
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    Event,
    EventParticipant,
    EventStatusChangeEvent,
    Invoice,
    Quote,
    User,
)
from services.event_workflow import (
    EVENT_WORKFLOWS,
    EventStatus,
    all_statuses,
    status_codes,
)


class EventServiceError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str = "event_error") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Promotion: appointment -> event
# ---------------------------------------------------------------------------


@dataclass
class EventOverrides:
    event_name: str | None = None
    event_date: date | None = None
    court_size: int | None = None
    quince_theme: str | None = None
    quince_theme_colors: list[str] | None = None
    budget_range: str | None = None
    notes: str | None = None
    owner_user_id: int | None = None


def promote_appointment_to_event(
    db: Session,
    *,
    appointment_id: int,
    event_type: str = "quinceanera",
    overrides: EventOverrides | None = None,
    actor_user_id: int | None = None,
) -> Event:
    """Create an event row from an appointment. Idempotency:
    if the appointment already has crm_event_id set, raise — caller must
    explicitly handle that case rather than silently no-op.
    """

    _require_supported_event_type(event_type)

    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise EventServiceError("appointment not found", code="appointment_not_found")

    if appt.crm_event_id is not None:
        raise EventServiceError(
            "appointment already linked to an event",
            code="already_promoted",
        )

    if appt.contact_id is None:
        raise EventServiceError(
            "appointment has no contact_id — backfill or attach a contact first",
            code="missing_contact",
        )

    contact = db.get(Contact, appt.contact_id)
    if contact is None or contact.deleted_at is not None:
        raise EventServiceError(
            "contact for appointment not found", code="contact_not_found"
        )

    enrichment = db.execute(
        select(AppointmentEnrichmentResponse).where(
            AppointmentEnrichmentResponse.appointment_id == appointment_id
        )
    ).scalar_one_or_none()

    o = overrides or EventOverrides()
    celebrant_name = _appointment_celebrant_name(appt)

    event = Event(
        primary_contact_id=contact.id,
        event_type=event_type,
        event_name=o.event_name
        or _default_event_name(contact, event_type, preferred_name=celebrant_name),
        event_date=o.event_date or appt.event_date,
        court_size=o.court_size if o.court_size is not None else _enrichment_int(
            enrichment, "court_size"
        ),
        quince_theme=o.quince_theme or _enrichment_str(enrichment, "quince_theme"),
        quince_theme_colors=o.quince_theme_colors
        or _enrichment_list(enrichment, "quince_theme_colors"),
        budget_range=o.budget_range or _enrichment_str(enrichment, "budget_range"),
        notes=o.notes,
        owner_user_id=o.owner_user_id or actor_user_id,
        status="lead",
    )
    db.add(event)
    db.flush()  # need event.id for the participant + appointment link

    _seed_initial_event_state(
        db,
        event,
        contact,
        actor_user_id,
        participant_display_name=celebrant_name,
        participant_phone=appt.phone,
        participant_email=appt.email,
    )
    appt.crm_event_id = event.id

    db.flush()
    return event


def create_walk_in_event(
    db: Session,
    *,
    contact_id: int,
    event_type: str = "quinceanera",
    overrides: EventOverrides | None = None,
    actor_user_id: int | None = None,
) -> Event:
    """Create an event with no source appointment — walk-in entry point."""

    _require_supported_event_type(event_type)

    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise EventServiceError("contact not found", code="contact_not_found")

    o = overrides or EventOverrides()
    event = Event(
        primary_contact_id=contact.id,
        event_type=event_type,
        event_name=o.event_name or _default_event_name(contact, event_type),
        event_date=o.event_date,
        court_size=o.court_size,
        quince_theme=o.quince_theme,
        quince_theme_colors=o.quince_theme_colors or [],
        budget_range=o.budget_range,
        notes=o.notes,
        owner_user_id=o.owner_user_id or actor_user_id,
        status="lead",
    )
    db.add(event)
    db.flush()

    _seed_initial_event_state(db, event, contact, actor_user_id)
    db.flush()
    return event


def _require_supported_event_type(event_type: str) -> None:
    if event_type not in EVENT_WORKFLOWS:
        raise EventServiceError(
            f"unsupported event_type {event_type!r}",
            code="unsupported_event_type",
        )


def _seed_initial_event_state(
    db: Session,
    event: Event,
    contact: Contact,
    actor_user_id: int | None,
    *,
    participant_display_name: str | None = None,
    participant_phone: str | None = None,
    participant_email: str | None = None,
) -> None:
    """Quinceañera participant + initial 'lead' audit row.

    Both promotion and walk-in creation rely on this so the data shape after
    creation is identical regardless of origin: every event has at least one
    participant (the celebrant) and a status_history entry tracing back to
    null -> lead.
    """
    db.add(
        EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="quinceanera",
            display_name=participant_display_name or contact.display_name,
            phone=participant_phone or contact.phone,
            email=participant_email or contact.email,
        )
    )
    db.add(
        EventStatusChangeEvent(
            event_id=event.id,
            from_status=None,
            to_status="lead",
            changed_by_user_id=actor_user_id,
        )
    )


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def change_event_status(
    db: Session,
    *,
    event_id: int,
    new_status: str,
    actor_user_id: int | None = None,
    notes: str | None = None,
) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise EventServiceError("event not found", code="event_not_found")

    valid = status_codes(event.event_type)
    if new_status not in valid:
        raise EventServiceError(
            f"status {new_status!r} not valid for event_type {event.event_type!r}",
            code="invalid_status",
        )

    if new_status == event.status:
        return event  # no-op; don't write a noise audit row

    from_status = event.status
    event.status = new_status
    event.status_changed_at = func.now()
    event.updated_at = func.now()

    db.add(
        EventStatusChangeEvent(
            event_id=event.id,
            from_status=from_status,
            to_status=new_status,
            changed_by_user_id=actor_user_id,
            notes=notes,
        )
    )
    db.flush()
    # Mirror the change into activity_log so the timeline tab has a
    # single source of truth. The legacy event_status_change_events
    # table stays around because the kanban depends on it; we just
    # double-write here.
    from services import activity_log  # local to avoid import cycle
    activity_log.log_activity(
        db,
        event_id=event.id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_STATUS_CHANGED,
        subject_kind="event",
        subject_id=event.id,
        payload={
            "from_status": from_status,
            "to_status": new_status,
            "notes": notes,
        },
    )
    return event


# ---------------------------------------------------------------------------
# Archive / restore (D3 of docs/CRM_RECORD_DELETION_PLAN.md)
# ---------------------------------------------------------------------------


def archive_event(
    db: Session,
    *,
    event_id: int,
    actor_user_id: int | None,
    reason: str,
    note: str | None = None,
) -> Event:
    """Soft-delete an event. Idempotent on already-archived rows.

    Refuses if the dependency report has any blocking active dependency
    (active invoice / quote / recorded payment). The activity_log row
    anchors to the event itself."""
    from services import activity_log, record_dependencies

    record_dependencies.validate_archive_reason(reason)

    event = db.get(Event, event_id)
    if event is None:
        raise EventServiceError("event not found", code="event_not_found")
    if event.deleted_at is not None:
        return event

    report = record_dependencies.get_record_dependencies(
        db, entity_type="event", entity_id=event_id
    )
    if not report.can_archive:
        raise EventServiceError(
            "archive blocked: " + "; ".join(report.block_reasons),
            code="archive_blocked",
        )

    now = datetime.now(timezone.utc)
    event.deleted_at = now
    event.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_ARCHIVED,
        subject_kind="event",
        subject_id=int(event_id),
        payload={
            "reason": reason,
            "note": note,
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return event


def restore_event(
    db: Session,
    *,
    event_id: int,
    actor_user_id: int | None,
) -> Event:
    """Lift the archive on an event. Idempotent on live rows.

    Refuses if the event's ``primary_contact`` is itself archived —
    restoring an event whose customer is in the Recycle Bin would
    leave a row that the admin pipeline immediately filters out via
    the Contact-side join in ``get_board_data``."""
    from services import activity_log, record_dependencies

    event = db.get(Event, event_id)
    if event is None:
        raise EventServiceError("event not found", code="event_not_found")
    if event.deleted_at is None:
        return event

    contact = db.get(Contact, event.primary_contact_id)
    if contact is None or contact.deleted_at is not None:
        raise EventServiceError(
            "restore blocked: primary contact is archived — "
            "restore the contact first",
            code="parent_archived",
        )

    report = record_dependencies.get_record_dependencies(
        db, entity_type="event", entity_id=event_id
    )

    now = datetime.now(timezone.utc)
    event.deleted_at = None
    event.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_RESTORED,
        subject_kind="event",
        subject_id=int(event_id),
        payload={
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return event


# ---------------------------------------------------------------------------
# Board read
# ---------------------------------------------------------------------------


@dataclass
class BoardCard:
    id: int
    event_name: str
    event_date: date | None
    court_size: int | None
    quince_theme: str | None
    status: str
    status_changed_at: datetime
    primary_contact_id: int
    primary_contact_name: str
    owner_user_id: int | None
    owner_name: str | None
    last_appointment_at: datetime | None
    boutique_experience_status: str  # "complete" | "not_started"
    has_outstanding_invoice: bool
    outstanding_balance_cents: int
    # Phase 10.4: how many distinct event_participants have at least one
    # appointment, quote, or invoice tagged to them on this event. The
    # celebrant counts as a buyer once they appear in any tagged row;
    # untagged rows (event_participant_id IS NULL) are not counted, by
    # design — NULL means "celebrant or unspecified" and we don't want
    # legacy untagged data inflating the buyer signal.
    named_buyer_count: int


@dataclass
class BoardColumn:
    code: str
    label: str
    sort_order: int
    is_terminal: bool
    cards: list[BoardCard]


def get_board_data(db: Session, *, event_type: str = "quinceanera") -> list[BoardColumn]:
    statuses: tuple[EventStatus, ...] = all_statuses(event_type)

    # Latest linked-appointment date per event (for "last contact" badge).
    last_appt_subq = (
        select(
            Appointment.crm_event_id.label("event_id"),
            func.max(Appointment.slot_start_at).label("last_appointment_at"),
        )
        .where(Appointment.crm_event_id.is_not(None))
        .group_by(Appointment.crm_event_id)
        .subquery()
    )

    # Per-event "Boutique Experience complete?" flag. The card is complete
    # if any linked appointment has a profile row with submitted_at set.
    # A reschedule keeps the original profile attached to the original
    # appointment, so this aggregation naturally surfaces the latest
    # completed profile across visits.
    profile_subq = (
        select(
            Appointment.crm_event_id.label("event_id"),
            func.bool_or(
                AppointmentEnrichmentResponse.submitted_at.is_not(None)
            ).label("has_complete_profile"),
        )
        .join(
            AppointmentEnrichmentResponse,
            AppointmentEnrichmentResponse.appointment_id == Appointment.id,
        )
        .where(Appointment.crm_event_id.is_not(None))
        .group_by(Appointment.crm_event_id)
        .subquery()
    )

    # "Outstanding invoice" = at least one non-deleted canonical invoice with
    # money still owed. Phase 4b moved this off the legacy event_documents
    # uploader rows and onto the canonical `invoices` table — `status IN
    # ('sent', 'partial')` covers both fully unpaid and partially paid bills,
    # which is a small UX broadening from the pre-Phase-4b behavior (today a
    # partial-pay invoice would not light the badge; after this it does).
    #
    # Phase 10 adds the dollar rollup. `balance_cents` is maintained by
    # the invoice service to equal `total - paid_to_date` for live invoices
    # and 0 for cancelled/reversed/draft, so summing it across the same
    # status filter is the correct AR figure. The CASE WHEN narrows to
    # the same set so the boolean badge and the dollar pill always agree.
    outstanding_subq = (
        select(
            Invoice.event_id.label("event_id"),
            func.bool_or(
                Invoice.status.in_(("sent", "partial"))
                & Invoice.deleted_at.is_(None)
            ).label("has_outstanding_invoice"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Invoice.status.in_(("sent", "partial"))
                            & Invoice.deleted_at.is_(None),
                            Invoice.balance_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("outstanding_balance_cents"),
        )
        .group_by(Invoice.event_id)
        .subquery()
    )

    # Phase 10.4: distinct (event_id, event_participant_id) pairs across
    # appointments, quotes, and invoices. UNION dedupes within the inner
    # SELECTs and across them, so a buyer with rows in two of the three
    # tables still counts once. NULL event_participant_id rows are filtered
    # in each inner SELECT — they represent "celebrant or unspecified" and
    # are not buyer-journey signals.
    named_buyer_pairs = union(
        select(
            Appointment.crm_event_id.label("event_id"),
            Appointment.event_participant_id.label("participant_id"),
        ).where(Appointment.event_participant_id.is_not(None)),
        select(
            Quote.event_id.label("event_id"),
            Quote.event_participant_id.label("participant_id"),
        ).where(Quote.event_participant_id.is_not(None)),
        select(
            Invoice.event_id.label("event_id"),
            Invoice.event_participant_id.label("participant_id"),
        ).where(Invoice.event_participant_id.is_not(None)),
    ).subquery("all_named_buyers")

    named_buyer_subq = (
        select(
            named_buyer_pairs.c.event_id,
            func.count().label("named_buyer_count"),
        )
        .group_by(named_buyer_pairs.c.event_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Event.id,
            Event.event_name,
            Event.event_date,
            Event.court_size,
            Event.quince_theme,
            Event.status,
            Event.status_changed_at,
            Event.primary_contact_id,
            Contact.display_name.label("contact_name"),
            Event.owner_user_id,
            User.full_name.label("owner_name"),
            last_appt_subq.c.last_appointment_at,
            profile_subq.c.has_complete_profile,
            outstanding_subq.c.has_outstanding_invoice,
            outstanding_subq.c.outstanding_balance_cents,
            named_buyer_subq.c.named_buyer_count,
        )
        .join(Contact, Contact.id == Event.primary_contact_id)
        .outerjoin(User, User.id == Event.owner_user_id)
        .outerjoin(last_appt_subq, last_appt_subq.c.event_id == Event.id)
        .outerjoin(profile_subq, profile_subq.c.event_id == Event.id)
        .outerjoin(outstanding_subq, outstanding_subq.c.event_id == Event.id)
        .outerjoin(
            named_buyer_subq, named_buyer_subq.c.event_id == Event.id
        )
        .where(Event.event_type == event_type)
        # D2: archived CRM records hide from the admin pipeline. Portal
        # reads bypass this filter (see Gate 3 in
        # docs/CRM_RECORD_DELETION_PLAN.md).
        .where(Event.deleted_at.is_(None))
        .where(Contact.deleted_at.is_(None))
        .order_by(Event.status_changed_at.desc())
    ).all()

    by_status: dict[str, list[BoardCard]] = {s.code: [] for s in statuses}
    for r in rows:
        if r.status not in by_status:
            # Defensive: a stray status not in the workflow shouldn't crash the board.
            continue
        by_status[r.status].append(
            BoardCard(
                id=r.id,
                event_name=r.event_name,
                event_date=r.event_date,
                court_size=r.court_size,
                quince_theme=r.quince_theme,
                status=r.status,
                status_changed_at=r.status_changed_at,
                primary_contact_id=r.primary_contact_id,
                primary_contact_name=r.contact_name,
                owner_user_id=r.owner_user_id,
                owner_name=r.owner_name,
                last_appointment_at=r.last_appointment_at,
                boutique_experience_status=(
                    "complete" if r.has_complete_profile else "not_started"
                ),
                has_outstanding_invoice=bool(r.has_outstanding_invoice),
                outstanding_balance_cents=int(
                    r.outstanding_balance_cents or 0
                ),
                named_buyer_count=int(r.named_buyer_count or 0),
            )
        )

    return [
        BoardColumn(
            code=s.code,
            label=s.label,
            sort_order=s.sort_order,
            is_terminal=s.is_terminal,
            cards=by_status[s.code],
        )
        for s in statuses
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_event_name(
    contact: Contact, event_type: str, *, preferred_name: str | None = None
) -> str:
    base = preferred_name or contact.first_name or contact.display_name
    suffix = {"quinceanera": "Quince"}.get(event_type, "Event")
    return f"{base}'s {suffix}"


def _appointment_celebrant_name(appt: Appointment) -> str | None:
    # Widget submissions after the parent-capture flow only carry the
    # celebrant's first name; the surname is the parent's. Fall back to
    # the legacy celebrant_last_name for historical rows.
    last = appt.parent_last_name or appt.celebrant_last_name
    parts = [
        p.strip()
        for p in (appt.celebrant_first_name, last)
        if p and p.strip()
    ]
    return " ".join(parts) if parts else None


def _enrichment_int(
    e: AppointmentEnrichmentResponse | None, attr: str
) -> int | None:
    if e is None:
        return None
    val = getattr(e, attr, None)
    return int(val) if val is not None else None


def _enrichment_str(
    e: AppointmentEnrichmentResponse | None, attr: str
) -> str | None:
    if e is None:
        return None
    val = getattr(e, attr, None)
    return str(val) if val else None


def _enrichment_list(
    e: AppointmentEnrichmentResponse | None, attr: str
) -> list[Any]:
    if e is None:
        return []
    val = getattr(e, attr, None)
    return list(val) if val else []
