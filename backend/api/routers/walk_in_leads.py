"""Walk-in lead capture endpoint.

Mounted at ``/api/walk-in-leads`` (admin-only). One POST creates the
full lead shape — Contact + placeholder Appointment + enrichment +
Event in the ``lead`` lane — so the kanban shows it next to a
widget-sourced lead without any further wiring.

The route is intentionally thin: it owns request validation, auth,
and the transaction boundary, but delegates the data writes to
``services.walk_in_service.create_walk_in_lead``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import walk_in_service
from services.walk_in_service import (
    WalkInContactInput,
    WalkInEnrichmentInput,
    WalkInEventInput,
    WalkInLeadError,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class WalkInLeadContactPayload(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str = Field(min_length=1, max_length=32)


class WalkInLeadEventPayload(BaseModel):
    celebrant_first_name: str = Field(min_length=1, max_length=100)
    celebrant_last_name: str | None = Field(default=None, max_length=100)
    event_name: str | None = Field(default=None, max_length=200)
    event_date: date | None = None
    owner_user_id: int | None = None


class WalkInLeadEnrichmentPayload(BaseModel):
    party_size_bucket: Literal["pair", "3_4", "5_plus"]
    court_size: int | None = Field(default=None, ge=0, le=100)
    quince_theme: str | None = Field(default=None, max_length=200)
    quince_theme_colors: list[str] | None = None
    budget_range: str | None = Field(default=None, max_length=50)
    dress_styles: list[str] | None = None
    colors: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class WalkInLeadCreate(BaseModel):
    contact: WalkInLeadContactPayload
    event: WalkInLeadEventPayload
    enrichment: WalkInLeadEnrichmentPayload


class WalkInLeadContactResponse(BaseModel):
    id: int
    display_name: str
    phone_e164: str | None
    email: str | None


class WalkInLeadEventResponse(BaseModel):
    id: int
    event_name: str
    status: str
    event_date: date | None


class WalkInLeadResponse(BaseModel):
    contact: WalkInLeadContactResponse
    event: WalkInLeadEventResponse
    appointment_id: int
    was_new_contact: bool


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


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


@router.post("", response_model=WalkInLeadResponse, status_code=201)
def create_walk_in_lead(
    payload: WalkInLeadCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> WalkInLeadResponse:
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
            actor_user_id=user.id,
            contact_in=contact_in,
            event_in=event_in,
            enrichment_in=enrichment_in,
        )
    except WalkInLeadError as exc:
        # Route owns the rollback so the service can stay flush-only.
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400), detail=exc.code
        ) from exc

    db.commit()
    db.refresh(result.contact)
    db.refresh(result.appointment)
    db.refresh(result.event)

    return WalkInLeadResponse(
        contact=WalkInLeadContactResponse(
            id=result.contact.id,
            display_name=result.contact.display_name,
            phone_e164=result.contact.phone_e164,
            email=result.contact.email,
        ),
        event=WalkInLeadEventResponse(
            id=result.event.id,
            event_name=result.event.event_name,
            status=result.event.status,
            event_date=result.event.event_date,
        ),
        appointment_id=result.appointment.id,
        was_new_contact=result.was_new_contact,
    )
