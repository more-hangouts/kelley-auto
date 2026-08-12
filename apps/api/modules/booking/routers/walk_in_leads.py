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
from modules.booking.services import walk_in_service
from modules.core.services import sales_staff
from modules.booking.services.walk_in_service import (
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
    sales_credit_user_id: int | None = None
    # Migration 104. Optional on the wire: the picker is strongly
    # encouraged in the UI but does not block filing a lead, because a rep
    # who does not yet know should not be forced to guess. The service
    # validates the value against WALK_IN_SOURCE_VALUES.
    walk_in_source: str | None = Field(default=None, max_length=32)
    walk_in_source_detail: str | None = Field(default=None, max_length=200)


class WalkInLeadEnrichmentPayload(BaseModel):
    # Wire name "enrichment" kept for SPA compatibility; the Bella's-era
    # dress-survey fields were removed with the dealership conversion
    # (unknown extras from older SPA builds are ignored by pydantic).
    #
    # party_size_bucket is now optional and defaulted server-side — a
    # dress-fitting question the dealership UI no longer asks. 'solo' and
    # the legacy Bella's buckets stay accepted so older SPA builds and the
    # storefront lead path keep working.
    party_size_bucket: Literal["solo", "pair", "3_4", "5_plus"] | None = None
    budget_range: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=4000)


class WalkInLeadCreate(BaseModel):
    contact: WalkInLeadContactPayload
    event: WalkInLeadEventPayload
    enrichment: WalkInLeadEnrichmentPayload
    # 'walk_in' (somebody arrived) or 'phone_call' (they called). Drives
    # whether an attended placeholder appointment is written at all —
    # see walk_in_service. Defaults to walk_in, preserving the prior
    # behavior for any client that does not send it.
    booking_context: Literal["walk_in", "phone_call"] = "walk_in"


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
    # None for a phone lead — no arrival, so no arrival receipt.
    appointment_id: int | None
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
    "invalid_walk_in_source": 422,
    "walk_in_source_detail_too_long": 422,
    "invalid_sales_credit_user_id": 400,
    "invalid_booking_context": 422,
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
    if (
        payload.event.sales_credit_user_id is not None
        and not sales_staff.is_assignable_sales_user(
            db, payload.event.sales_credit_user_id
        )
    ):
        raise HTTPException(status_code=400, detail="invalid_sales_credit_user_id")

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
        sales_credit_user_id=payload.event.sales_credit_user_id,
        walk_in_source=payload.event.walk_in_source,
        walk_in_source_detail=payload.event.walk_in_source_detail,
    )
    enrichment_in = WalkInEnrichmentInput(
        party_size_bucket=payload.enrichment.party_size_bucket,
        budget_range=payload.enrichment.budget_range,
        notes=payload.enrichment.notes,
    )

    try:
        result = walk_in_service.create_walk_in_lead(
            db,
            actor_user_id=user.id,
            contact_in=contact_in,
            event_in=event_in,
            enrichment_in=enrichment_in,
            booking_context=payload.booking_context,
        )
    except WalkInLeadError as exc:
        # Route owns the rollback so the service can stay flush-only.
        db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(exc.code, 400), detail=exc.code
        ) from exc

    db.commit()
    db.refresh(result.contact)
    if result.appointment is not None:
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
        appointment_id=(
            result.appointment.id if result.appointment is not None else None
        ),
        was_new_contact=result.was_new_contact,
    )
