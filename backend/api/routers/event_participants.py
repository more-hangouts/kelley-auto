"""Add-participant endpoint (Phase 6 of the Sales Portal).

Canonical home for the add-participant flow used by both surfaces:

  POST /api/events/{event_id}/participants

Accepts both admin and sales tokens via `require_any_scope("admin",
"sales")`. The earlier `POST /api/sales/events/{event_id}/participants`
route in `api.routers.sales` is preserved as a deprecated alias that
delegates to the same service helper; new frontend code should hit
the canonical path.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import User
from services import event_participants
from services.attendance_gate import require_floor_access
from services.event_participants import EventParticipantError

router = APIRouter()


# Phase 6 audit: the booking widget today (widgets/bellas-booking-widget.js)
# only emits these three values for `party_size`. The legacy values
# (`solo`, `2_3`, `4_plus`) remain in the DB CHECK constraint on
# appointments for historical rows, but we deliberately do NOT accept
# them here — new participant rows should use the canonical vocabulary
# the widget produces today. If Bellas adds a "solo" stylist-only
# walk-through case in the future, extend this Literal explicitly
# rather than letting drift creep back in.
ParticipantRole = Literal[
    "quinceanera", "dama", "chambelan", "parent", "other"
]
PartySizeBucket = Literal["pair", "3_4", "5_plus"]


class ParticipantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_first_name: str = Field(min_length=1, max_length=100)
    parent_last_name: str | None = Field(default=None, max_length=100)
    celebrant_first_name: str = Field(min_length=1, max_length=100)
    celebrant_last_name: str | None = Field(default=None, max_length=100)
    phone: str = Field(min_length=7, max_length=32)
    email: EmailStr | None = None
    party_size_bucket: PartySizeBucket | None = None
    role: ParticipantRole = "other"

    @model_validator(mode="after")
    def _trim_names(self) -> "ParticipantCreate":
        self.parent_first_name = self.parent_first_name.strip()
        self.parent_last_name = (
            self.parent_last_name.strip() if self.parent_last_name else None
        )
        self.celebrant_first_name = self.celebrant_first_name.strip()
        self.celebrant_last_name = (
            self.celebrant_last_name.strip() if self.celebrant_last_name else None
        )
        if not self.parent_first_name:
            raise ValueError("parent_first_name is required")
        if not self.celebrant_first_name:
            raise ValueError("celebrant_first_name is required")
        return self


class ContactSummary(BaseModel):
    id: int
    display_name: str


class ParticipantResponse(BaseModel):
    id: int
    event_id: int
    contact: ContactSummary
    role: str
    display_name: str
    phone: str | None
    email: str | None
    party_size_bucket: str | None
    was_new_contact: bool


def _to_response(result: dict, party_size_bucket: str | None) -> ParticipantResponse:
    participant = result["participant"]
    contact = result["contact"]
    return ParticipantResponse(
        id=participant.id,
        event_id=participant.event_id,
        contact=ContactSummary(id=contact.id, display_name=contact.display_name),
        role=participant.role,
        display_name=participant.display_name,
        phone=participant.phone,
        email=participant.email,
        party_size_bucket=party_size_bucket,
        was_new_contact=result["was_new_contact"],
    )


def _add(
    db: Session,
    *,
    event_id: int,
    payload: ParticipantCreate,
    actor_user_id: int,
) -> ParticipantResponse:
    """Service-call wrapper used by both the canonical route and the
    /sales/* alias."""
    try:
        result = event_participants.add_event_participant(
            db,
            event_id=event_id,
            parent_first_name=payload.parent_first_name,
            parent_last_name=payload.parent_last_name,
            celebrant_first_name=payload.celebrant_first_name,
            celebrant_last_name=payload.celebrant_last_name,
            phone=payload.phone,
            email=payload.email,
            role=payload.role,
            party_size_bucket=payload.party_size_bucket,
            actor_user_id=actor_user_id,
            actor_kind="staff",
        )
    except EventParticipantError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc

    db.commit()
    return _to_response(result, payload.party_size_bucket)


@router.post(
    "/{event_id}/participants",
    response_model=ParticipantResponse,
    status_code=201,
)
def add_event_participant(
    event_id: int,
    payload: ParticipantCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[
        User, Depends(require_floor_access("admin", "sales"))
    ],
) -> ParticipantResponse:
    """Add a participant to an event. Canonical home for both the admin
    Overview and the sales appointment detail. Sales tokens additionally
    must be punched in unless the owner has disabled the attendance
    gate; admin tokens always pass."""
    return _add(db, event_id=event_id, payload=payload, actor_user_id=user.id)


# Re-exported for the deprecated `/api/sales/events/{event_id}/participants`
# alias mounted via `api.routers.sales`.
__all__ = [
    "ParticipantCreate",
    "ParticipantResponse",
    "_add",
]
