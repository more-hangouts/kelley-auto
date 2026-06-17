"""Sales-portal assignment endpoints (Phase 6).

  - ``GET  /api/sales/staff/assignable``     — picker for the floor UI.
  - ``PATCH /api/sales/appointments/{id}/assignment`` — move one appt.
  - ``PATCH /api/sales/leads/{event_id}/assignment`` — move a lead and
    cascade onto future-dated appointments.

The read endpoint is sales-scope only (no attendance gate — a punched-
out stylist building tomorrow's plan still needs to see coworkers).
The two PATCH routes require ``require_floor_access("sales")`` because
they mutate floor state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_any_scope, require_sales_scope
from database.connection import get_db
from database.models import User
from services import sales_assignment, sales_staff
from services.attendance_gate import require_floor_access
from services.sales_assignment import SalesAssignmentError

router = APIRouter()


class AssignableStaffRow(BaseModel):
    id: int
    full_name: str


class AssignmentPatch(BaseModel):
    # Nullable: explicit ``None`` means "unassign." The router maps this
    # straight through to the service so a caller wanting to clear an
    # assignment doesn't need a separate endpoint.
    assigned_user_id: int | None = None


class LeadAssignmentPatch(BaseModel):
    owner_user_id: int | None = None


class AppointmentAssignmentResponse(BaseModel):
    appointment_id: int
    assigned_user_id: int | None


class LeadAssignmentResponse(BaseModel):
    event_id: int
    owner_user_id: int | None
    cascaded_appointment_ids: list[int]


class FutureAppointmentPreview(BaseModel):
    id: int
    slot_start_at: datetime
    celebrant_first_name: str | None
    celebrant_last_name: str | None
    assigned_user_id: int | None
    assigned_user_full_name: str | None


class LeadCascadePreviewResponse(BaseModel):
    event_id: int
    event_owner_user_id: int | None
    event_owner_full_name: str | None
    future_appointments: list[FutureAppointmentPreview]


_ERROR_STATUS = {
    "appointment_not_found": 404,
    "event_not_found": 404,
    "invalid_assigned_user_id": 400,
}


@router.get("/staff/assignable", response_model=list[AssignableStaffRow])
def list_assignable(
    db: Annotated[Session, Depends(get_db)],
    # Phase 11: relaxed from sales-only to admin-or-sales so the admin
    # event-owner dialog reuses this single source-of-truth picker
    # instead of forking a sibling admin endpoint. The response shape
    # and the underlying sales_staff filter (role='sales' AND active)
    # are unchanged — admin sees the same list of assignable stylists,
    # never themselves.
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> list[AssignableStaffRow]:
    rows = sales_staff.list_assignable_sales_users(db)
    return [
        AssignableStaffRow(id=u.id, full_name=(u.full_name or u.username))
        for u in rows
    ]


@router.patch(
    "/appointments/{appointment_id}/assignment",
    response_model=AppointmentAssignmentResponse,
)
def reassign_appointment(
    appointment_id: int,
    payload: AssignmentPatch,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> AppointmentAssignmentResponse:
    try:
        appt = sales_assignment.reassign_appointment(
            db,
            appointment_id=appointment_id,
            new_assignee_id=payload.assigned_user_id,
            actor_user_id=current_user.id,
        )
    except SalesAssignmentError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(appt)
    return AppointmentAssignmentResponse(
        appointment_id=appt.id,
        assigned_user_id=appt.assigned_user_id,
    )


@router.get(
    "/leads/{event_id}/cascade-preview",
    response_model=LeadCascadePreviewResponse,
)
def preview_lead_cascade(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _sales: Annotated[User, Depends(require_sales_scope)],
) -> LeadCascadePreviewResponse:
    """Show which future appointments a lead reassignment would cascade onto.

    Read-only; uses the same ``slot_start_at >= NOW()`` cutoff as the
    PATCH so the dialog's preview matches exactly what the mutation
    would touch. No attendance gate — a punched-out stylist building
    tomorrow's plan still needs to see the cascade scope.
    """
    result = sales_assignment.lead_cascade_preview(db, event_id=event_id)
    if result is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return LeadCascadePreviewResponse(**result)


@router.patch(
    "/leads/{event_id}/assignment",
    response_model=LeadAssignmentResponse,
)
def reassign_lead(
    event_id: int,
    payload: LeadAssignmentPatch,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> LeadAssignmentResponse:
    try:
        result = sales_assignment.reassign_event_lead(
            db,
            event_id=event_id,
            new_owner_id=payload.owner_user_id,
            actor_user_id=current_user.id,
        )
    except SalesAssignmentError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(result.event)
    return LeadAssignmentResponse(
        event_id=result.event.id,
        owner_user_id=result.event.owner_user_id,
        cascaded_appointment_ids=result.cascaded_appointment_ids,
    )
