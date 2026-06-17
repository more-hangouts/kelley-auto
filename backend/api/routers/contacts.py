"""Contact edit + lightweight context.

Phase B of the Contact UX plan: lets staff fix a stale contact name
(`debbie` -> `Maria`) without dropping into the database. No merge logic
here; phone-collision returns 409 with the colliding contact id so the
future Phase C merge flow has a natural entry point.

Global Search Phase 3 expands `GET /api/contacts/{id}` to include
`address` and a typed `linked_events` list with server-computed
routes. The contact detail page consumes both. Edit responses keep
the same shape so the staff edit dialog stays in sync without a
second fetch.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from database.connection import get_db
from database.models import Contact, User
from services import contact_service
from services.contact_service import ContactServiceError

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LinkedEventSummary(BaseModel):
    id: int
    event_name: str
    event_type: str
    status: str
    event_date: date_type | None
    route: str


class ContactResponse(BaseModel):
    id: int
    first_name: str | None
    last_name: str | None
    display_name: str
    email: str | None
    phone: str | None
    phone_e164: str | None
    address: dict
    notes: str | None
    tags: list[str]
    event_count: int
    appointment_count: int
    alternate_celebrants: list[str]
    linked_events: list[LinkedEventSummary]


class ContactPatch(BaseModel):
    """Partial update. Use `model_fields_set` to distinguish unsent from
    explicitly-null so callers can clear a field by sending `null`."""

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = None


class ContactCreate(BaseModel):
    """Inputs for an admin-driven contact creation (e.g., the command
    palette's "Create contact" fallback). Any combination of phone/email/
    name is acceptable; the service de-dupes on phone_e164 then email."""

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)


class ContactCreateResponse(BaseModel):
    contact: ContactResponse
    was_new: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _to_response(
    contact: Contact, ctx: dict, linked_events: list[dict]
) -> ContactResponse:
    return ContactResponse(
        id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        display_name=contact.display_name,
        email=contact.email,
        phone=contact.phone,
        phone_e164=contact.phone_e164,
        address=dict(contact.address or {}),
        notes=contact.notes,
        tags=list(contact.tags or []),
        event_count=ctx["event_count"],
        appointment_count=ctx["appointment_count"],
        alternate_celebrants=ctx["alternate_celebrants"],
        linked_events=[LinkedEventSummary(**e) for e in linked_events],
    )


@router.post("", response_model=ContactCreateResponse, status_code=201)
def create_contact(
    payload: ContactCreate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> ContactCreateResponse:
    contact, was_new = contact_service.create_admin_contact(
        db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        notes=payload.notes,
    )
    ctx = contact_service.get_contact_context(db, contact_id=contact.id)
    linked = contact_service.get_linked_events(db, contact_id=contact.id)
    return ContactCreateResponse(
        contact=_to_response(contact, ctx, linked),
        was_new=was_new,
    )


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> ContactResponse:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact_not_found")
    ctx = contact_service.get_contact_context(db, contact_id=contact_id)
    linked = contact_service.get_linked_events(db, contact_id=contact_id)
    return _to_response(contact, ctx, linked)


@router.patch("/{contact_id}", response_model=ContactResponse)
def patch_contact(
    contact_id: int,
    payload: ContactPatch,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> ContactResponse:
    patch = {k: getattr(payload, k) for k in payload.model_fields_set}

    try:
        contact = contact_service.update_contact(
            db, contact_id=contact_id, patch=patch
        )
    except ContactServiceError as exc:
        if exc.code == "contact_not_found":
            raise HTTPException(status_code=404, detail=exc.code) from exc
        if exc.code == "phone_collision":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "conflict_contact_id": exc.conflict_contact_id,
                },
            ) from exc
        if exc.code in ("display_name_required", "unknown_fields"):
            raise HTTPException(status_code=422, detail=exc.code) from exc
        raise HTTPException(status_code=400, detail=exc.code) from exc

    db.commit()
    db.refresh(contact)
    ctx = contact_service.get_contact_context(db, contact_id=contact_id)
    linked = contact_service.get_linked_events(db, contact_id=contact_id)
    return _to_response(contact, ctx, linked)
