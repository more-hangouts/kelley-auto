"""Authenticated admin endpoints for the booking widget.

Phase 4 minimal: list, detail, patch (status + internal notes). The full
admin surface (calendar view, availability rules editor, theme settings,
analytics) lands in later phases.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import (
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    Event,
    User,
)
from services import (
    appointment_audit,
    booking_service,
    buyer_journey,
    event_service,
    notification_service,
)
from services.buyer_journey import BuyerJourneyError
from services.event_service import EventServiceError

log = logging.getLogger(__name__)

router = APIRouter()


_ADMIN_EDITABLE_STATUSES = (
    "pending",
    "confirmed",
    "attended",
    "no_show",
    "cancelled",
    "rescheduled",
)


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class AppointmentRow(BaseModel):
    id: int
    confirmation_code: str
    slot_start_at: datetime
    slot_end_at: datetime
    timezone: str
    status: str
    celebrant_first_name: str
    celebrant_last_name: str | None
    parent_first_name: str | None
    parent_last_name: str | None
    event_date: date | None
    party_size_bucket: str
    phone: str
    phone_e164: str | None
    email: str
    utm_source: str | None
    utm_campaign: str | None
    device_type: str | None
    bot_suspected: bool
    created_at: datetime


class AppointmentListResponse(BaseModel):
    items: list[AppointmentRow]
    total: int
    limit: int
    offset: int


class AppointmentDetail(AppointmentRow):
    customer_note: str | None
    internal_notes: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    rescheduled_from_id: int | None
    attended_at: datetime | None
    no_show_at: datetime | None
    purchase_at: datetime | None
    purchase_value_cents: int | None
    visitor_id: str | None
    session_id: str | None
    event_id: str | None
    page_url: str | None
    referrer_url: str | None
    utm_medium: str | None
    utm_content: str | None
    utm_term: str | None
    utm_id: str | None
    fbclid: str | None
    gclid: str | None
    msclkid: str | None
    fbp_cookie: str | None
    fbc_cookie: str | None
    user_agent: str | None
    screen: str | None
    viewport: str | None
    browser_language: str | None
    platform: str | None
    browser_timezone: str | None
    time_on_widget_ms: int | None
    interaction_count: int | None
    steps_completed: int | None
    user_journey: list[dict[str, Any]]
    behavior_score: int | None
    meta_capi_synced_at: datetime | None
    google_enhanced_synced_at: datetime | None
    raw_payload: dict[str, Any]
    enrichment: dict[str, Any] | None
    # CRM linkage — populated post-014/015 migrations.
    contact_id: int | None
    contact_display_name: str | None
    crm_event_id: int | None
    crm_event_name: str | None
    crm_event_status: str | None
    # True iff the appointment is linked to a contact AND not yet promoted.
    # Drives the "Promote to Event" button in the admin UI.
    can_promote_to_event: bool


class AppointmentPatch(BaseModel):
    status: Literal[
        "pending", "confirmed", "attended", "no_show", "cancelled", "rescheduled"
    ] | None = None
    internal_notes: str | None = Field(default=None, max_length=2000)
    purchase_value_cents: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------


def _row(appt: Appointment) -> AppointmentRow:
    return AppointmentRow(
        id=appt.id,
        confirmation_code=booking_service.format_confirmation_code(appt.confirmation_code),
        slot_start_at=appt.slot_start_at,
        slot_end_at=appt.slot_end_at,
        timezone=appt.timezone,
        status=appt.status,
        celebrant_first_name=appt.celebrant_first_name,
        celebrant_last_name=appt.celebrant_last_name,
        parent_first_name=appt.parent_first_name,
        parent_last_name=appt.parent_last_name,
        event_date=appt.event_date,
        party_size_bucket=appt.party_size_bucket,
        phone=appt.phone,
        phone_e164=appt.phone_e164,
        email=appt.email,
        utm_source=appt.utm_source,
        utm_campaign=appt.utm_campaign,
        device_type=appt.device_type,
        bot_suspected=appt.bot_suspected,
        created_at=appt.created_at,
    )


def _detail(
    appt: Appointment,
    enrichment: AppointmentEnrichmentResponse | None,
    contact: Contact | None,
    event: Event | None,
) -> AppointmentDetail:
    return AppointmentDetail(
        **_row(appt).model_dump(),
        customer_note=appt.customer_note,
        internal_notes=appt.internal_notes,
        cancelled_at=appt.cancelled_at,
        cancellation_reason=appt.cancellation_reason,
        rescheduled_from_id=appt.rescheduled_from_id,
        attended_at=appt.attended_at,
        no_show_at=appt.no_show_at,
        purchase_at=appt.purchase_at,
        purchase_value_cents=appt.purchase_value_cents,
        visitor_id=str(appt.visitor_id) if appt.visitor_id else None,
        session_id=appt.session_id,
        event_id=appt.event_id,
        page_url=appt.page_url,
        referrer_url=appt.referrer_url,
        utm_medium=appt.utm_medium,
        utm_content=appt.utm_content,
        utm_term=appt.utm_term,
        utm_id=appt.utm_id,
        fbclid=appt.fbclid,
        gclid=appt.gclid,
        msclkid=appt.msclkid,
        fbp_cookie=appt.fbp_cookie,
        fbc_cookie=appt.fbc_cookie,
        user_agent=appt.user_agent,
        screen=appt.screen,
        viewport=appt.viewport,
        browser_language=appt.browser_language,
        platform=appt.platform,
        browser_timezone=appt.browser_timezone,
        time_on_widget_ms=appt.time_on_widget_ms,
        interaction_count=appt.interaction_count,
        steps_completed=appt.steps_completed,
        user_journey=appt.user_journey or [],
        behavior_score=appt.behavior_score,
        meta_capi_synced_at=appt.meta_capi_synced_at,
        google_enhanced_synced_at=appt.google_enhanced_synced_at,
        raw_payload=appt.raw_payload or {},
        enrichment=_enrichment_payload(enrichment),
        contact_id=appt.contact_id,
        contact_display_name=contact.display_name if contact is not None else None,
        crm_event_id=appt.crm_event_id,
        crm_event_name=event.event_name if event is not None else None,
        crm_event_status=event.status if event is not None else None,
        can_promote_to_event=(
            appt.contact_id is not None and appt.crm_event_id is None
        ),
    )


def _enrichment_payload(e: AppointmentEnrichmentResponse | None) -> dict[str, Any] | None:
    if e is None:
        return None
    return {
        "submitted_at": e.submitted_at.isoformat() if e.submitted_at else None,
        "dress_styles": e.dress_styles or [],
        "colors": e.colors or [],
        "budget_range": e.budget_range,
        "quince_theme": e.quince_theme,
        "quince_theme_colors": e.quince_theme_colors or [],
        "court_size": e.court_size,
        "inspiration_photos": e.inspiration_photos or [],
        "free_text": e.free_text,
    }


@router.get("/appointments", response_model=AppointmentListResponse)
def list_appointments(
    status: Annotated[str | None, Query()] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    q: Annotated[str | None, Query(description="search name/email/phone/code")] = None,
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> AppointmentListResponse:
    query = db.query(Appointment)
    if status:
        if status not in _ADMIN_EDITABLE_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status")
        query = query.filter(Appointment.status == status)
    if from_date:
        query = query.filter(Appointment.slot_start_at >= from_date)
    if to_date:
        # Inclusive: "to=2026-04-26" should include all of 2026-04-26.
        query = query.filter(Appointment.slot_start_at < to_date + timedelta(days=1))
    if source:
        query = query.filter(Appointment.utm_source == source)
    if q:
        like = f"%{q.strip()}%"
        # D1: confirmation_code is stored canonical (no hyphens / spaces).
        # If admin pastes `BX-ABCDE-FGHJK-...` from a customer email, the
        # raw `%BX-ABCDE-...%` ilike would miss the canonical stored value
        # — so canonicalise the search term for that column only.
        code_like = f"%{booking_service.normalize_confirmation_code(q)}%"
        query = query.filter(
            or_(
                Appointment.celebrant_first_name.ilike(like),
                Appointment.celebrant_last_name.ilike(like),
                Appointment.parent_first_name.ilike(like),
                Appointment.parent_last_name.ilike(like),
                Appointment.email.ilike(like),
                Appointment.phone.ilike(like),
                Appointment.phone_e164.ilike(like),
                Appointment.confirmation_code.ilike(code_like),
            )
        )

    total = query.count()
    rows = (
        query.order_by(Appointment.slot_start_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return AppointmentListResponse(
        items=[_row(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _load_detail_relations(
    db: Session, appt: Appointment
) -> tuple[
    AppointmentEnrichmentResponse | None, Contact | None, Event | None
]:
    enrichment = (
        db.query(AppointmentEnrichmentResponse)
        .filter(AppointmentEnrichmentResponse.appointment_id == appt.id)
        .first()
    )
    contact = db.get(Contact, appt.contact_id) if appt.contact_id else None
    if contact is not None and contact.deleted_at is not None:
        contact = None
    event = db.get(Event, appt.crm_event_id) if appt.crm_event_id else None
    if event is not None and event.deleted_at is not None:
        event = None
    return enrichment, contact, event


@router.get("/appointments/{appointment_id}", response_model=AppointmentDetail)
def get_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> AppointmentDetail:
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")
    enrichment, contact, event = _load_detail_relations(db, appt)
    return _detail(appt, enrichment, contact, event)


@router.patch("/appointments/{appointment_id}", response_model=AppointmentDetail)
def patch_appointment(
    appointment_id: int,
    payload: AppointmentPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_scope),
) -> AppointmentDetail:
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="appointment not found")

    now = datetime.now(timezone.utc)
    # exclude_unset distinguishes "field absent" from "field present and null",
    # so the UI can explicitly clear nullable values like purchase_value_cents
    # by sending {"purchase_value_cents": null}.
    changes = payload.model_dump(exclude_unset=True)

    # Snapshot before mutation so the activity-log emit at the bottom of
    # the handler can detect whether internal_notes actually changed.
    prior_internal_notes = appt.internal_notes

    transitioned_to_cancelled = False
    if "status" in changes and changes["status"] != appt.status:
        appt.status = changes["status"]
        # Stamp lifecycle timestamps on transitions so reporting/CAPI value
        # pushback later can rely on these fields without scanning history.
        if appt.status == "attended" and appt.attended_at is None:
            appt.attended_at = now
        elif appt.status == "no_show" and appt.no_show_at is None:
            appt.no_show_at = now
        elif appt.status == "cancelled" and appt.cancelled_at is None:
            appt.cancelled_at = now
            transitioned_to_cancelled = True

    if "internal_notes" in changes:
        appt.internal_notes = changes["internal_notes"]

    if "purchase_value_cents" in changes:
        appt.purchase_value_cents = changes["purchase_value_cents"]
        if appt.purchase_value_cents is not None and appt.purchase_at is None:
            appt.purchase_at = now
        elif appt.purchase_value_cents is None:
            appt.purchase_at = None

    # Activity-log: emit APPOINTMENT_NOTES_EDITED when the admin PATCH
    # actually changes the notes column on an appointment with a linked
    # CRM event. Matches the sales-side audit shape so the activity
    # timeline reflects edits from either surface (Phase 9.4 / D2).
    if (
        "internal_notes" in changes
        and appt.crm_event_id is not None
        and (prior_internal_notes or "") != (appt.internal_notes or "")
    ):
        appointment_audit.log_notes_edited(
            db,
            appointment_id=appt.id,
            event_id=appt.crm_event_id,
            actor_user_id=user.id,
            prior_notes=prior_internal_notes,
            new_notes=appt.internal_notes,
        )

    appt.updated_at = now
    db.commit()
    db.refresh(appt)

    # Admin-side cancellation should still notify the customer + cancel any
    # pending reminder/enrichment jobs, and mirror onto the linked CRM event so
    # the kanban reflects board truth. All best-effort — appointment is the
    # source record.
    if transitioned_to_cancelled:
        try:
            notification_service.enqueue_for_cancellation(db, appt)
            db.commit()
        except Exception:
            db.rollback()

        # Tell the assigned stylist their slot just opened. Skipped
        # silently when the appointment had no assignee.
        try:
            from services.staff_booking_notifications import (
                notify_booking_cancelled,
            )

            notify_booking_cancelled(db, appt, actor_user_id=user.id)
            db.commit()
        except Exception:
            log.exception(
                "failed to enqueue staff booking_cancelled appt_id=%s",
                appt.id,
            )
            db.rollback()

        if appt.crm_event_id is not None:
            try:
                event_service.change_event_status(
                    db,
                    event_id=appt.crm_event_id,
                    new_status="cancelled",
                    actor_user_id=user.id,
                    notes="Admin cancelled the linked appointment.",
                )
                db.commit()
            except EventServiceError:
                log.exception(
                    "mirror cancel to event failed appt_id=%s event_id=%s",
                    appt.id,
                    appt.crm_event_id,
                )
                db.rollback()
            except Exception:
                log.exception(
                    "mirror cancel to event crashed appt_id=%s", appt.id
                )
                db.rollback()

    enrichment, contact, event = _load_detail_relations(db, appt)
    return _detail(appt, enrichment, contact, event)


# ---------------------------------------------------------------------------
# Phase 10.3a: participant tagging
# ---------------------------------------------------------------------------


class ParticipantTagPatch(BaseModel):
    # Nullable: explicit ``None`` means "untag." Mirrors the assignment
    # PATCH shape so callers needing to clear don't need a separate
    # endpoint.
    event_participant_id: int | None = None


class ParticipantTagResponse(BaseModel):
    appointment_id: int
    event_participant_id: int | None


_PARTICIPANT_TAG_ERROR_STATUS = {
    "appointment_not_found": 404,
    "participant_not_found": 404,
    "appointment_unlinked_from_event": 400,
    "participant_event_mismatch": 400,
}


@router.patch(
    "/appointments/{appointment_id}/participant",
    response_model=ParticipantTagResponse,
)
def tag_appointment_participant(
    appointment_id: int,
    payload: ParticipantTagPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_scope),
) -> ParticipantTagResponse:
    """Set or clear ``appointments.event_participant_id`` from admin.

    The buyer-journey link is sales-doctrine even when admin uses it
    (per docs/SALES_REP_DASHBOARD_PHASES.md Phase 10): admin gets the
    same primitive as sales because the underlying action is the same
    business write. Audit row anchors to the appointment's linked event.
    """
    try:
        appt = buyer_journey.attach_appointment_to_participant(
            db,
            appointment_id=appointment_id,
            event_participant_id=payload.event_participant_id,
            actor_user_id=user.id,
        )
    except BuyerJourneyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_PARTICIPANT_TAG_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(appt)
    return ParticipantTagResponse(
        appointment_id=appt.id,
        event_participant_id=appt.event_participant_id,
    )
