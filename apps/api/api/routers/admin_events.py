"""Admin event endpoints not covered by the public /api/events router.

Today this is just lead-owner reassignment (Phase 11):

  - ``GET  /api/admin/events/{event_id}/cascade-preview`` — preview the
    future appointments a lead-owner reassignment would cascade onto.
  - ``PATCH /api/admin/events/{event_id}/owner`` — apply the move.

Both delegate to ``services.sales_assignment`` so cascade rules, audit
shape, and notification ordering cannot drift from the sales-side
equivalents at ``/api/sales/leads/{event_id}/...``. The admin routes
intentionally live at a different URL prefix because they have
different auth: ``require_admin_scope`` with no attendance/floor gate
(admin is not geofenced), where sales requires ``require_floor_access``.

The shared service is sales-named today; renaming to a neutral
``services/assignment_service.py`` is gated on a follow-up commit
after Phase 11 ships so the diff stays mechanical and isolated from
new behavior.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import sales_assignment
from services.sales_assignment import SalesAssignmentError

router = APIRouter()


# Mirrors LeadAssignmentPatch / LeadAssignmentResponse on the sales
# router. Kept separate so a future shape divergence between admin and
# sales is localized to its own surface rather than retrofitting the
# sales response model.


class AdminEventOwnerPatch(BaseModel):
    # Nullable: explicit ``None`` clears the owner. Same convention as
    # sales' LeadAssignmentPatch.
    owner_user_id: int | None = None


class AdminEventOwnerResponse(BaseModel):
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


class AdminCascadePreviewResponse(BaseModel):
    event_id: int
    event_owner_user_id: int | None
    event_owner_full_name: str | None
    future_appointments: list[FutureAppointmentPreview]


_ERROR_STATUS = {
    "event_not_found": 404,
    "invalid_assigned_user_id": 400,
}


# Phase 11: tag audit rows so admin-initiated reassignments are
# distinguishable from sales reassignments in the activity timeline.
# The service plumbs this through to both the EVENT_REASSIGNED row and
# every per-cascade APPOINTMENT_REASSIGNED row.
_ADMIN_REASON = "admin_owner_change"


@router.get(
    "/{event_id}/cascade-preview",
    response_model=AdminCascadePreviewResponse,
)
def preview_owner_cascade(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> AdminCascadePreviewResponse:
    """Show which future appointments a lead-owner change would cascade onto.

    Read-only; uses the same ``slot_start_at >= NOW()`` cutoff as the
    PATCH so the dialog's preview matches exactly what the mutation
    would touch. Delegates to ``sales_assignment.lead_cascade_preview``
    so the cascade scope stays identical to the sales side.
    """
    result = sales_assignment.lead_cascade_preview(db, event_id=event_id)
    if result is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return AdminCascadePreviewResponse(**result)


@router.patch(
    "/{event_id}/owner",
    response_model=AdminEventOwnerResponse,
)
def reassign_owner(
    event_id: int,
    payload: AdminEventOwnerPatch,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> AdminEventOwnerResponse:
    """Move an event's owner; cascade onto its future-dated appointments.

    Delegates to ``sales_assignment.reassign_event_lead`` so the cascade
    rules, audit-row shape, notification ordering, and idempotency
    (no-op when owner is unchanged) all match the sales-side path.
    """
    try:
        result = sales_assignment.reassign_event_lead(
            db,
            event_id=event_id,
            new_owner_id=payload.owner_user_id,
            actor_user_id=current_user.id,
            reason=_ADMIN_REASON,
        )
    except SalesAssignmentError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(result.event)
    return AdminEventOwnerResponse(
        event_id=result.event.id,
        owner_user_id=result.event.owner_user_id,
        cascaded_appointment_ids=result.cascaded_appointment_ids,
    )
