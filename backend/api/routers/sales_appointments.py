"""Sales-portal appointment reads (Phase 2).

Two endpoints behind `require_sales_scope`:

  GET /api/sales/appointments/today?mine=true|false
  GET /api/sales/appointments/{appointment_id}

Both delegate to `services.sales_appointments` so any future Phase 3
status handler / Phase 4 try-on log can read the same shapes without
re-implementing the joins.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import buyer_journey, sales_appointments
from services.attendance_gate import require_floor_access
from services.buyer_journey import BuyerJourneyError
from services.sales_appointments import SalesActionError

router = APIRouter()


StatusAction = Literal["arrived", "no_show", "cancelled"]


class EnrichmentSummary(BaseModel):
    dress_styles: list[Any] = []
    colors: list[Any] = []
    budget_range: str | None = None
    quince_theme: str | None = None
    court_size: int | None = None
    estimated_size_low: int | None = None
    estimated_size_high: int | None = None
    style_preference: str | None = None


class TodayAppointmentItem(BaseModel):
    id: int
    confirmation_code: str
    slot_start_at: datetime
    slot_end_at: datetime
    slot_duration_minutes: int
    timezone: str
    party_size_bucket: str
    parent_first_name: str | None
    parent_last_name: str | None
    celebrant_first_name: str
    celebrant_last_name: str | None
    status: str
    assigned_user_id: int | None
    internal_notes_preview: str | None
    crm_event_id: int | None
    crm_event_status: str | None
    enrichment_summary: EnrichmentSummary | None


class TodayAppointmentsResponse(BaseModel):
    date: str
    timezone: str
    has_assigned: bool
    appointments: list[TodayAppointmentItem]


class ContactSummary(BaseModel):
    id: int
    display_name: str
    phone: str | None
    email: str | None


class EventSummary(BaseModel):
    id: int
    event_type: str
    status: str
    status_changed_at: datetime
    event_name: str
    event_date: date | None
    owner_user_id: int | None = None
    owner_full_name: str | None = None


class ParticipantSummary(BaseModel):
    id: int
    role: str
    display_name: str
    phone: str | None
    email: str | None
    measurements: dict[str, Any] = {}
    status: str


class FullEnrichment(BaseModel):
    dress_styles: list[Any] = []
    colors: list[Any] = []
    budget_range: str | None = None
    quince_theme: str | None = None
    quince_theme_colors: list[Any] = []
    court_size: int | None = None
    inspiration_photos: list[Any] = []
    free_text: str | None = None
    bust_inches: float | None = None
    waist_inches: float | None = None
    hips_inches: float | None = None
    height_ft: int | None = None
    height_in: int | None = None
    estimated_size_low: int | None = None
    estimated_size_high: int | None = None
    style_preference: str | None = None
    back_preference: str | None = None
    submitted_at: datetime | None = None


class ActivityRow(BaseModel):
    id: int
    created_at: datetime
    actor_kind: str
    actor_display_name: str | None
    activity_type: str
    subject_kind: str | None
    subject_id: int | None
    payload: dict[str, Any] = {}


class AppointmentDetailFields(BaseModel):
    id: int
    confirmation_code: str
    slot_start_at: datetime
    slot_end_at: datetime
    slot_duration_minutes: int
    timezone: str
    party_size_bucket: str
    parent_first_name: str | None
    parent_last_name: str | None
    celebrant_first_name: str
    celebrant_last_name: str | None
    event_date: date | None
    phone: str
    email: str
    customer_note: str | None
    internal_notes: str | None
    status: str
    assigned_user_id: int | None
    assigned_user_full_name: str | None = None
    event_participant_id: int | None = None
    attended_at: datetime | None
    no_show_at: datetime | None
    cancelled_at: datetime | None


class AppointmentDetailResponse(BaseModel):
    appointment: AppointmentDetailFields
    contact: ContactSummary | None
    event: EventSummary | None
    participants: list[ParticipantSummary]
    enrichment: FullEnrichment | None
    recent_activity: list[ActivityRow]


@router.get("/today", response_model=TodayAppointmentsResponse)
def list_today(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
    mine: Annotated[bool, Query(description="Filter to assigned_user_id = me")] = False,
) -> TodayAppointmentsResponse:
    payload = sales_appointments.list_today(
        db, mine_user_id=current_user.id if mine else None
    )
    return TodayAppointmentsResponse(**payload)


@router.get("/{appointment_id}", response_model=AppointmentDetailResponse)
def get_detail(
    appointment_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_sales_scope)],
) -> AppointmentDetailResponse:
    payload = sales_appointments.get_detail(db, appointment_id=appointment_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="appointment_not_found")
    return AppointmentDetailResponse(**payload)


# ---------------------------------------------------------------------------
# Phase 3: status quick-actions + editable internal notes
# ---------------------------------------------------------------------------


class StatusActionRequest(BaseModel):
    action: StatusAction
    notes: str | None = Field(default=None, max_length=2000)


class StatusActionResponse(BaseModel):
    appointment_id: int
    appointment_status: str
    event_id: int | None
    prior_event_status: str | None
    new_event_status: str | None
    promoted_event: bool
    changed: bool


class NotesPatchRequest(BaseModel):
    internal_notes: str = Field(default="", max_length=10_000)


class NotesPatchResponse(BaseModel):
    appointment_id: int
    internal_notes: str | None
    changed: bool


def _raise_for_action_error(exc: SalesActionError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc


@router.post("/{appointment_id}/status", response_model=StatusActionResponse)
def post_status_action(
    appointment_id: int,
    payload: StatusActionRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> StatusActionResponse:
    """Quick-action: arrived / no_show / cancelled.

    `arrived` runs the composite handler (promote → consult). Re-tapping
    the same action is idempotent; the response's `changed` flag tells
    the UI whether anything actually moved.
    """
    try:
        result = sales_appointments.apply_status_action(
            db,
            appointment_id=appointment_id,
            action=payload.action,
            actor_user_id=current_user.id,
            notes=payload.notes,
        )
    except SalesActionError as exc:
        db.rollback()
        _raise_for_action_error(exc)

    db.commit()
    return StatusActionResponse(**result)


@router.patch("/{appointment_id}/notes", response_model=NotesPatchResponse)
def patch_internal_notes(
    appointment_id: int,
    payload: NotesPatchRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> NotesPatchResponse:
    """Update internal notes. Activity payload records length deltas
    only — never the prior text — so the log stays small."""
    try:
        result = sales_appointments.update_internal_notes(
            db,
            appointment_id=appointment_id,
            internal_notes=payload.internal_notes,
            actor_user_id=current_user.id,
        )
    except SalesActionError as exc:
        db.rollback()
        _raise_for_action_error(exc)

    db.commit()
    return NotesPatchResponse(**result)


# ---------------------------------------------------------------------------
# Phase 10.3a: participant tagging
# ---------------------------------------------------------------------------


class ParticipantTagPatch(BaseModel):
    # Nullable: explicit ``None`` means "untag." Same shape as the
    # admin route at PATCH /api/admin/booking/appointments/{id}/participant.
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
    "/{appointment_id}/participant",
    response_model=ParticipantTagResponse,
)
def tag_appointment_participant(
    appointment_id: int,
    payload: ParticipantTagPatch,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> ParticipantTagResponse:
    """Tag this appointment to a specific event_participant from sales.

    Attendance-gated for sales (matches every other floor mutation in
    this router). Shares the ``buyer_journey.attach_appointment_to_participant``
    service with the admin route — same audit row, same validation
    rules, same no-op-on-idempotent-retry behavior.
    """
    try:
        appt = buyer_journey.attach_appointment_to_participant(
            db,
            appointment_id=appointment_id,
            event_participant_id=payload.event_participant_id,
            actor_user_id=current_user.id,
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
