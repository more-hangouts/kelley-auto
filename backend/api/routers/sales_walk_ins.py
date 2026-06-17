"""Sales-portal walk-in capture.

`POST /api/sales/walk-ins` — punched-in stylist creates a walk-in
lead, assigned to themselves by default or to a coworker via
`assigned_user_id`. Delegates the actual writes to
`services.walk_in_service.create_walk_in_lead`, which is the same
service the admin walk-in endpoint uses; the sales route just resolves
the assignee and forwards.

Auth: `require_floor_access("sales")` — sales scope only, and only
when punched in (or when the attendance gate is disabled).
Read-only sales paths (lead search) do not require an active punch
per Phase 3; mutations like this one do.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.routers.walk_in_leads import (
    WalkInLeadContactPayload,
    WalkInLeadEnrichmentPayload,
    WalkInLeadEventPayload,
)
from database.connection import get_db
from database.models import User
from services import sales_staff, walk_in_service
from services.attendance_gate import require_floor_access
from services.walk_in_service import (
    WalkInContactInput,
    WalkInEnrichmentInput,
    WalkInEventInput,
    WalkInLeadError,
)

router = APIRouter()


class SalesWalkInCreate(BaseModel):
    contact: WalkInLeadContactPayload
    event: WalkInLeadEventPayload
    enrichment: WalkInLeadEnrichmentPayload
    # Optional. Server resolves None → current_user.id. A non-null
    # value must belong to an active sales user; admin / inactive ids
    # are rejected with 400. Reuses the same assignable-staff filter
    # the Phase 6 picker exposes via GET /api/sales/staff/assignable.
    assigned_user_id: int | None = None


class SalesWalkInResponse(BaseModel):
    appointment_id: int
    event_id: int
    contact_id: int
    assigned_user_id: int
    route: str


_ERROR_STATUS = {
    "invalid_phone": 422,
    "phone_required": 422,
    "contact_name_required": 422,
    "celebrant_first_name_required": 422,
    "invalid_party_size_bucket": 422,
    "missing_contact": 422,
    "contact_not_found": 404,
    "appointment_not_found": 404,
    "already_promoted": 409,
    "unsupported_event_type": 400,
    "promotion_failed": 400,
}


@router.post("", response_model=SalesWalkInResponse, status_code=201)
def create_sales_walk_in(
    payload: SalesWalkInCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> SalesWalkInResponse:
    # Resolve assignment. Default = self (the stylist taking the walk-in).
    assigned_user_id = (
        payload.assigned_user_id
        if payload.assigned_user_id is not None
        else current_user.id
    )

    # Validate the assignee even when the caller chose themselves: an
    # admin masquerading via the sales surface (shouldn't happen at the
    # auth layer, but defense in depth) wouldn't be an "active sales
    # user" and would be caught here instead of silently producing an
    # admin-owned lead in the sales pipeline.
    if not sales_staff.is_assignable_sales_user(db, assigned_user_id):
        raise HTTPException(status_code=400, detail="invalid_assigned_user_id")

    contact_in = WalkInContactInput(
        first_name=payload.contact.first_name,
        last_name=payload.contact.last_name,
        display_name=payload.contact.display_name,
        email=str(payload.contact.email) if payload.contact.email else None,
        phone=payload.contact.phone,
    )
    event_in = WalkInEventInput(
        celebrant_first_name=payload.event.celebrant_first_name,
        celebrant_last_name=payload.event.celebrant_last_name,
        event_name=payload.event.event_name,
        event_date=payload.event.event_date,
        owner_user_id=payload.event.owner_user_id,
    )
    enrichment_in = WalkInEnrichmentInput(
        party_size_bucket=payload.enrichment.party_size_bucket,
        court_size=payload.enrichment.court_size,
        quince_theme=payload.enrichment.quince_theme,
        quince_theme_colors=payload.enrichment.quince_theme_colors,
        budget_range=payload.enrichment.budget_range,
        dress_styles=payload.enrichment.dress_styles,
        colors=payload.enrichment.colors,
        notes=payload.enrichment.notes,
    )

    try:
        result = walk_in_service.create_walk_in_lead(
            db,
            actor_user_id=current_user.id,
            contact_in=contact_in,
            event_in=event_in,
            enrichment_in=enrichment_in,
            assigned_user_id=assigned_user_id,
        )
    except WalkInLeadError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400), detail=exc.code
        ) from exc

    db.commit()
    db.refresh(result.appointment)
    db.refresh(result.event)
    db.refresh(result.contact)

    return SalesWalkInResponse(
        appointment_id=result.appointment.id,
        event_id=result.event.id,
        contact_id=result.contact.id,
        assigned_user_id=assigned_user_id,
        route=f"/appointments/{result.appointment.id}",
    )
