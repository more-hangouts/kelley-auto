"""Sales-staff endpoints — DEPRECATED add-participant alias.

Phase 6 of the sales portal moved the add-participant route to its
canonical home at `POST /api/events/{event_id}/participants`
([api/routers/event_participants.py](api/routers/event_participants.py)).
The old `/api/sales/events/{event_id}/participants` URL is preserved
here as a thin alias so any frontend or external integration that
still points at it keeps working through one rolling release. New
code should use the canonical path.

The duplicate `_lookup_existing_contact` helper that used to live in
this module was removed in Phase 6 — `find_or_create_contact` now
returns `(contact, was_new)` directly, which is the only signal the
activity-log payload needed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.routers.event_participants import (
    ParticipantCreate,
    ParticipantResponse,
    _add as _add_participant,
)
from database.connection import get_db
from database.models import User
from services.attendance_gate import require_floor_access

router = APIRouter()


@router.post(
    "/events/{event_id}/participants",
    response_model=ParticipantResponse,
    status_code=201,
    deprecated=True,
)
def add_event_participant_legacy(
    event_id: int,
    payload: ParticipantCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[
        User, Depends(require_floor_access("admin", "sales"))
    ],
) -> ParticipantResponse:
    """Deprecated alias for `POST /api/events/{event_id}/participants`.
    Same scope + attendance gate as the canonical route."""
    return _add_participant(
        db, event_id=event_id, payload=payload, actor_user_id=user.id
    )
