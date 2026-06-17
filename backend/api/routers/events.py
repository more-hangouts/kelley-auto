"""CRM events: kanban board, status transitions, promote-from-appointment.

Currently scoped to event_type='quinceanera' — the only workflow defined in
services/event_workflow.py.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from database.connection import get_db
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
from services import activity_log, booking_service, event_service
from services.event_service import EventOverrides, EventServiceError
from services.event_workflow import all_statuses

router = APIRouter()


_QuinceStatus = Literal[
    "lead",
    "consulted",
    "sold",
    "on_order",
    "arrived",
    "in_alterations",
    "ready_for_pickup",
    "picked_up",
    "cancelled",
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EventCreate(BaseModel):
    """Either provide from_appointment_id (promote a lead) or
    primary_contact_id + event_name (manual / walk-in)."""

    from_appointment_id: int | None = None
    primary_contact_id: int | None = None
    event_type: Literal["quinceanera"] = "quinceanera"

    event_name: str | None = Field(default=None, max_length=200)
    event_date: date | None = None
    court_size: int | None = Field(default=None, ge=0, le=100)
    quince_theme: str | None = Field(default=None, max_length=200)
    quince_theme_colors: list[str] | None = None
    budget_range: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=4000)
    owner_user_id: int | None = None

    @model_validator(mode="after")
    def _require_exactly_one_origin(self) -> "EventCreate":
        if self.from_appointment_id is None and self.primary_contact_id is None:
            raise ValueError(
                "either from_appointment_id or primary_contact_id is required"
            )
        if self.from_appointment_id is not None and self.primary_contact_id is not None:
            raise ValueError(
                "from_appointment_id and primary_contact_id are mutually exclusive"
            )
        if self.from_appointment_id is None and not self.event_name:
            raise ValueError(
                "event_name is required when not promoting from an appointment"
            )
        return self


class EventStatusPatch(BaseModel):
    status: _QuinceStatus
    notes: str | None = Field(default=None, max_length=2000)


class ContactSummary(BaseModel):
    id: int
    display_name: str


class OwnerSummary(BaseModel):
    id: int
    full_name: str | None


class EventResponse(BaseModel):
    id: int
    event_type: str
    event_name: str
    event_date: date | None
    court_size: int | None
    quince_theme: str | None
    quince_theme_colors: list[str]
    budget_range: str | None
    status: str
    status_changed_at: datetime
    primary_contact: ContactSummary
    owner: OwnerSummary | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class StatusHistoryEntry(BaseModel):
    from_status: str | None
    to_status: str
    changed_at: datetime
    notes: str | None


class ParticipantSummary(BaseModel):
    id: int
    role: str
    display_name: str
    # Phase 10.5: per-buyer breakdown for the event quick-view drawer.
    # Counts mirror the board's `named_buyer_count` semantics (any row
    # tagged to the participant, regardless of soft-delete) so the
    # per-buyer numbers and the headline card count never disagree.
    # `outstanding_balance_cents` filters to live invoices only (matches
    # the card's outstanding-balance rollup) and is the AR figure
    # tied to this buyer specifically.
    linked_appointment_count: int = 0
    linked_quote_count: int = 0
    linked_invoice_count: int = 0
    outstanding_balance_cents: int = 0


class LinkedAppointmentEnrichment(BaseModel):
    submitted_at: datetime | None
    dress_styles: list[str]
    colors: list[str]
    budget_range: str | None
    quince_theme: str | None
    quince_theme_colors: list[str]
    court_size: int | None
    inspiration_photos: list[str]
    free_text: str | None


class BoutiqueExperienceProfile(BaseModel):
    """Full Boutique Experience profile, surfaced on event detail.

    Covers the calculator path (measurements, computed sizing, free-text
    preferences) plus identity (`source`, `submitted_at`). Survey-shape
    preferences stay on `LinkedAppointmentEnrichment` for now to avoid
    breaking the existing staff UI; Phase 6 can decide whether to merge.
    """

    profile_id: int
    submitted_at: datetime | None
    source: str | None
    summary: str | None

    bust_inches: float | None
    waist_inches: float | None
    hips_inches: float | None
    height_ft: int | None
    height_in: int | None

    estimated_size_low: int | None
    estimated_size_high: int | None
    size_by_bust: int | None
    size_by_waist: int | None
    size_by_hips: int | None
    chart_source: str | None
    off_chart: bool | None

    style: str | None
    back: str | None
    budget: str | None
    colors: str | None
    likes: str | None
    avoids: str | None


BoutiqueExperienceStatus = Literal["complete", "not_started"]


class LinkedAppointmentSummary(BaseModel):
    id: int
    confirmation_code: str
    slot_start_at: datetime
    slot_end_at: datetime
    slot_duration_minutes: int | None
    status: str
    party_size_bucket: str | None
    customer_note: str | None
    phone: str | None
    phone_e164: str | None
    email: str | None
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None
    page_url: str | None
    referrer_url: str | None
    has_fbclid: bool
    has_gclid: bool
    rescheduled_from_id: int | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    # Phase 10.4: which participant's buyer journey this appointment
    # belongs to. NULL = celebrant's appointment or unspecified.
    event_participant_id: int | None = None
    enrichment: LinkedAppointmentEnrichment | None
    # Boutique Experience surface. `status` and `submitted_at` are the
    # at-a-glance signal; `summary` is the customer-rendered staff display
    # text; the structured object lives on full detail only.
    boutique_experience_status: BoutiqueExperienceStatus
    boutique_experience_submitted_at: datetime | None
    boutique_experience_summary: str | None
    boutique_experience: BoutiqueExperienceProfile | None


class LinkedQuoteSummary(BaseModel):
    id: int
    quote_number: str | None
    status: str
    issue_date: date
    sent_at: datetime | None
    total_cents: int
    # Phase 10.6: which participant's buyer journey this quote belongs
    # to. NULL = celebrant or unspecified — surfaced in the Untagged
    # bucket on the Overview buyer-journey section.
    event_participant_id: int | None = None


class LinkedInvoiceSummary(BaseModel):
    id: int
    invoice_number: str | None
    status: str
    issue_date: date
    due_date: date | None
    sent_at: datetime | None
    total_cents: int
    balance_cents: int
    # Phase 10.6: which participant's buyer journey this invoice belongs
    # to. NULL = celebrant or unspecified.
    event_participant_id: int | None = None


class EventDetailResponse(EventResponse):
    primary_contact_phone: str | None
    primary_contact_email: str | None
    participants: list[ParticipantSummary]
    appointments: list[LinkedAppointmentSummary]
    # Phase 10.6: linked quotes + invoices for the per-buyer journey
    # cards on the Overview tab. Soft-deleted rows are excluded — the
    # journey is a UX surface, not the board's count signal, and a
    # deleted row should not appear in the operator's timeline. The
    # ParticipantSummary counts intentionally still match the board
    # (including soft-deletes) for the quick-view drawer; the journey
    # section derives its own per-buyer counts from these lists.
    quotes: list[LinkedQuoteSummary]
    invoices: list[LinkedInvoiceSummary]
    status_history: list[StatusHistoryEntry]


class BoardCardResponse(BaseModel):
    id: int
    event_name: str
    event_date: date | None
    court_size: int | None
    quince_theme: str | None
    status: str
    status_changed_at: datetime
    primary_contact: ContactSummary
    owner: OwnerSummary | None
    last_appointment_at: datetime | None
    boutique_experience_status: BoutiqueExperienceStatus
    has_outstanding_invoice: bool
    outstanding_balance_cents: int
    # Phase 10.4: distinct event_participants with at least one tagged
    # appointment/quote/invoice on this event. 0 means no buyer-journey
    # signal yet (the celebrant's rows are likely untagged today).
    named_buyer_count: int = 0


class BoardColumnResponse(BaseModel):
    code: str
    label: str
    sort_order: int
    is_terminal: bool
    cards: list[BoardCardResponse]


class BoardResponse(BaseModel):
    event_type: str
    columns: list[BoardColumnResponse]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=EventResponse, status_code=201)
def create_event(
    payload: EventCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> EventResponse:
    overrides = EventOverrides(
        event_name=payload.event_name,
        event_date=payload.event_date,
        court_size=payload.court_size,
        quince_theme=payload.quince_theme,
        quince_theme_colors=payload.quince_theme_colors,
        budget_range=payload.budget_range,
        notes=payload.notes,
        owner_user_id=payload.owner_user_id,
    )

    try:
        if payload.from_appointment_id is not None:
            event = event_service.promote_appointment_to_event(
                db,
                appointment_id=payload.from_appointment_id,
                event_type=payload.event_type,
                overrides=overrides,
                actor_user_id=user.id,
            )
        else:
            event = event_service.create_walk_in_event(
                db,
                contact_id=payload.primary_contact_id,
                event_type=payload.event_type,
                overrides=overrides,
                actor_user_id=user.id,
            )
    except EventServiceError as exc:
        raise HTTPException(status_code=_status_for(exc.code), detail=exc.code) from exc

    db.commit()
    db.refresh(event)
    return _to_event_response(db, event)


@router.patch("/{event_id}/status", response_model=EventResponse)
def patch_status(
    event_id: int,
    payload: EventStatusPatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> EventResponse:
    try:
        event = event_service.change_event_status(
            db,
            event_id=event_id,
            new_status=payload.status,
            actor_user_id=user.id,
            notes=payload.notes,
        )
    except EventServiceError as exc:
        raise HTTPException(status_code=_status_for(exc.code), detail=exc.code) from exc

    db.commit()
    db.refresh(event)
    return _to_event_response(db, event)


@router.get("/board", response_model=BoardResponse)
def get_board(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    event_type: str = Query(default="quinceanera"),
) -> BoardResponse:
    try:
        columns = event_service.get_board_data(db, event_type=event_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BoardResponse(
        event_type=event_type,
        columns=[
            BoardColumnResponse(
                code=col.code,
                label=col.label,
                sort_order=col.sort_order,
                is_terminal=col.is_terminal,
                cards=[
                    BoardCardResponse(
                        id=c.id,
                        event_name=c.event_name,
                        event_date=c.event_date,
                        court_size=c.court_size,
                        quince_theme=c.quince_theme,
                        status=c.status,
                        status_changed_at=c.status_changed_at,
                        primary_contact=ContactSummary(
                            id=c.primary_contact_id,
                            display_name=c.primary_contact_name,
                        ),
                        owner=OwnerSummary(id=c.owner_user_id, full_name=c.owner_name)
                        if c.owner_user_id is not None
                        else None,
                        last_appointment_at=c.last_appointment_at,
                        boutique_experience_status=c.boutique_experience_status,
                        has_outstanding_invoice=c.has_outstanding_invoice,
                        outstanding_balance_cents=c.outstanding_balance_cents,
                        named_buyer_count=c.named_buyer_count,
                    )
                    for c in col.cards
                ],
            )
            for col in columns
        ],
    )


@router.get("/{event_id}", response_model=EventDetailResponse)
def get_event(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> EventDetailResponse:
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="event_not_found")

    contact = db.get(Contact, event.primary_contact_id)
    owner = db.get(User, event.owner_user_id) if event.owner_user_id else None

    participants = (
        db.query(EventParticipant)
        .filter(EventParticipant.event_id == event_id)
        .filter(EventParticipant.status == "active")
        .filter(EventParticipant.deleted_at.is_(None))
        .order_by(EventParticipant.id.asc())
        .all()
    )
    appointments = (
        db.query(Appointment)
        .filter(Appointment.crm_event_id == event_id)
        .order_by(Appointment.slot_start_at.desc())
        .all()
    )
    appointment_ids = [a.id for a in appointments]
    enrichments_by_appt: dict[int, AppointmentEnrichmentResponse] = {}
    if appointment_ids:
        rows = (
            db.query(AppointmentEnrichmentResponse)
            .filter(AppointmentEnrichmentResponse.appointment_id.in_(appointment_ids))
            .all()
        )
        enrichments_by_appt = {r.appointment_id: r for r in rows}
    history = (
        db.query(EventStatusChangeEvent)
        .filter(EventStatusChangeEvent.event_id == event_id)
        .order_by(EventStatusChangeEvent.changed_at.desc())
        .limit(20)
        .all()
    )

    # Phase 10.5: per-buyer counts for the quick-view breakdown. Three
    # small group-bys against the per-event row sets; the participant
    # list is short so a simple in-memory dict lookup is cheaper than
    # a join-and-group across all three tables.
    appt_counts: dict[int, int] = dict(
        db.query(Appointment.event_participant_id, func.count())
        .filter(Appointment.crm_event_id == event_id)
        .filter(Appointment.event_participant_id.is_not(None))
        .group_by(Appointment.event_participant_id)
        .all()
    )
    quote_counts: dict[int, int] = dict(
        db.query(Quote.event_participant_id, func.count())
        .filter(Quote.event_id == event_id)
        .filter(Quote.event_participant_id.is_not(None))
        .group_by(Quote.event_participant_id)
        .all()
    )
    invoice_rows = (
        db.query(
            Invoice.event_participant_id,
            func.count(),
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
            ),
        )
        .filter(Invoice.event_id == event_id)
        .filter(Invoice.event_participant_id.is_not(None))
        .group_by(Invoice.event_participant_id)
        .all()
    )
    invoice_counts: dict[int, int] = {pid: cnt for pid, cnt, _ in invoice_rows}
    outstanding_by_participant: dict[int, int] = {
        pid: int(balance) for pid, _, balance in invoice_rows
    }

    # Phase 10.6: linked quote + invoice lists for the Overview tab's
    # buyer-journey cards. Soft-deleted rows are excluded — the
    # operator's timeline should not show a quote/invoice they've
    # deleted. Ordered by issue_date so the journey reads chronologically
    # alongside the appointments list.
    linked_quotes = (
        db.query(Quote)
        .filter(Quote.event_id == event_id)
        .filter(Quote.deleted_at.is_(None))
        .order_by(Quote.issue_date.asc(), Quote.id.asc())
        .all()
    )
    linked_invoices = (
        db.query(Invoice)
        .filter(Invoice.event_id == event_id)
        .filter(Invoice.deleted_at.is_(None))
        .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
        .all()
    )

    base = _to_event_response(db, event).model_dump()
    return EventDetailResponse(
        **base,
        primary_contact_phone=contact.phone_e164 or contact.phone if contact else None,
        primary_contact_email=contact.email if contact else None,
        participants=[
            ParticipantSummary(
                id=p.id,
                role=p.role,
                display_name=p.display_name,
                linked_appointment_count=appt_counts.get(p.id, 0),
                linked_quote_count=quote_counts.get(p.id, 0),
                linked_invoice_count=invoice_counts.get(p.id, 0),
                outstanding_balance_cents=outstanding_by_participant.get(p.id, 0),
            )
            for p in participants
        ],
        appointments=[
            _to_linked_appointment(a, enrichments_by_appt.get(a.id))
            for a in appointments
        ],
        quotes=[
            LinkedQuoteSummary(
                id=q.id,
                quote_number=q.quote_number,
                status=q.status,
                issue_date=q.issue_date,
                sent_at=q.sent_at,
                total_cents=q.total_cents,
                event_participant_id=q.event_participant_id,
            )
            for q in linked_quotes
        ],
        invoices=[
            LinkedInvoiceSummary(
                id=i.id,
                invoice_number=i.invoice_number,
                status=i.status,
                issue_date=i.issue_date,
                due_date=i.due_date,
                sent_at=i.sent_at,
                total_cents=i.total_cents,
                balance_cents=i.balance_cents,
                event_participant_id=i.event_participant_id,
            )
            for i in linked_invoices
        ],
        status_history=[
            StatusHistoryEntry(
                from_status=h.from_status,
                to_status=h.to_status,
                changed_at=h.changed_at,
                notes=h.notes,
            )
            for h in history
        ],
    )


@router.get("/workflow/{event_type}")
def get_workflow(
    event_type: str,
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Return the full status definition for a workflow — for UI dropdowns."""
    try:
        statuses = all_statuses(event_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "event_type": event_type,
        "statuses": [
            {
                "code": s.code,
                "label": s.label,
                "sort_order": s.sort_order,
                "is_terminal": s.is_terminal,
                "description": s.description,
            }
            for s in statuses
        ],
    }


# ---------------------------------------------------------------------------
# Activity log (Phase 9)
# ---------------------------------------------------------------------------


class ActivityRowResponse(BaseModel):
    id: int
    event_id: int
    actor_user_id: int | None
    actor_kind: str
    actor_display_name: str | None
    activity_type: str
    subject_kind: str | None
    subject_id: int | None
    payload: dict
    created_at: datetime


class ActivityListResponse(BaseModel):
    activities: list[ActivityRowResponse]
    next_before_id: int | None  # pass to ?before_id= for the next page


@router.get("/{event_id}/activity", response_model=ActivityListResponse)
def list_event_activity(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    limit: int = Query(default=100, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
) -> ActivityListResponse:
    event_row = db.get(Event, event_id)
    if event_row is None or event_row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="event_not_found")
    rows = activity_log.list_activities_for_event(
        db, event_id=event_id, limit=limit, before_id=before_id
    )
    next_before = rows[-1].id if len(rows) == limit else None
    return ActivityListResponse(
        activities=[
            ActivityRowResponse(
                id=r.id,
                event_id=r.event_id,
                actor_user_id=r.actor_user_id,
                actor_kind=r.actor_kind,
                actor_display_name=r.actor_display_name,
                activity_type=r.activity_type,
                subject_kind=r.subject_kind,
                subject_id=r.subject_id,
                payload=r.payload,
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_before_id=next_before,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _profile_status(
    profile: AppointmentEnrichmentResponse | None,
) -> BoutiqueExperienceStatus:
    """Derive the at-a-glance Boutique Experience status.

    `submitted_at` is the explicit "customer hit submit" signal stamped by
    every write path. Treat its presence as completion; the survey row
    that predates this work also ends up here, since it has the same
    column.
    """
    if profile is None or profile.submitted_at is None:
        return "not_started"
    return "complete"


def _to_boutique_experience_profile(
    profile: AppointmentEnrichmentResponse | None,
) -> BoutiqueExperienceProfile | None:
    if profile is None:
        return None
    return BoutiqueExperienceProfile(
        profile_id=profile.id,
        submitted_at=profile.submitted_at,
        source=profile.source,
        summary=profile.summary,
        bust_inches=float(profile.bust_inches) if profile.bust_inches is not None else None,
        waist_inches=float(profile.waist_inches) if profile.waist_inches is not None else None,
        hips_inches=float(profile.hips_inches) if profile.hips_inches is not None else None,
        height_ft=profile.height_ft,
        height_in=profile.height_in,
        estimated_size_low=profile.estimated_size_low,
        estimated_size_high=profile.estimated_size_high,
        size_by_bust=profile.size_by_bust,
        size_by_waist=profile.size_by_waist,
        size_by_hips=profile.size_by_hips,
        chart_source=profile.chart_source,
        off_chart=profile.off_chart,
        style=profile.style_preference,
        back=profile.back_preference,
        budget=profile.budget_preference,
        colors=profile.color_preferences_text,
        likes=profile.likes,
        avoids=profile.avoids,
    )


def _to_linked_appointment(
    a: Appointment, enrichment: AppointmentEnrichmentResponse | None
) -> LinkedAppointmentSummary:
    return LinkedAppointmentSummary(
        id=a.id,
        confirmation_code=booking_service.format_confirmation_code(a.confirmation_code),
        slot_start_at=a.slot_start_at,
        slot_end_at=a.slot_end_at,
        slot_duration_minutes=a.slot_duration_minutes,
        status=a.status,
        party_size_bucket=a.party_size_bucket,
        customer_note=a.customer_note,
        phone=a.phone,
        phone_e164=a.phone_e164,
        email=a.email,
        utm_source=a.utm_source,
        utm_medium=a.utm_medium,
        utm_campaign=a.utm_campaign,
        page_url=a.page_url,
        referrer_url=a.referrer_url,
        has_fbclid=bool(a.fbclid),
        has_gclid=bool(a.gclid),
        rescheduled_from_id=a.rescheduled_from_id,
        cancelled_at=a.cancelled_at,
        cancellation_reason=a.cancellation_reason,
        created_at=a.created_at,
        event_participant_id=a.event_participant_id,
        enrichment=(
            LinkedAppointmentEnrichment(
                submitted_at=enrichment.submitted_at,
                dress_styles=enrichment.dress_styles or [],
                colors=enrichment.colors or [],
                budget_range=enrichment.budget_range,
                quince_theme=enrichment.quince_theme,
                quince_theme_colors=enrichment.quince_theme_colors or [],
                court_size=enrichment.court_size,
                inspiration_photos=enrichment.inspiration_photos or [],
                free_text=enrichment.free_text,
            )
            if enrichment is not None
            else None
        ),
        boutique_experience_status=_profile_status(enrichment),
        boutique_experience_submitted_at=enrichment.submitted_at if enrichment else None,
        boutique_experience_summary=enrichment.summary if enrichment else None,
        boutique_experience=_to_boutique_experience_profile(enrichment),
    )


def _to_event_response(db: Session, event: Event) -> EventResponse:
    contact = db.get(Contact, event.primary_contact_id)
    owner = db.get(User, event.owner_user_id) if event.owner_user_id else None
    return EventResponse(
        id=event.id,
        event_type=event.event_type,
        event_name=event.event_name,
        event_date=event.event_date,
        court_size=event.court_size,
        quince_theme=event.quince_theme,
        quince_theme_colors=event.quince_theme_colors or [],
        budget_range=event.budget_range,
        status=event.status,
        status_changed_at=event.status_changed_at,
        primary_contact=ContactSummary(
            id=contact.id, display_name=contact.display_name
        ),
        owner=OwnerSummary(id=owner.id, full_name=owner.full_name) if owner else None,
        notes=event.notes,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


_ERROR_STATUS_MAP = {
    "appointment_not_found": 404,
    "event_not_found": 404,
    "contact_not_found": 404,
    "missing_contact": 422,
    "already_promoted": 409,
    "invalid_status": 422,
    "unsupported_event_type": 400,
}


def _status_for(code: str) -> int:
    return _ERROR_STATUS_MAP.get(code, 400)
