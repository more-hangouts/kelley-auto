"""Deal notes — the rep-facing running log and its follow-up reminders.

    GET    /api/events/{event_id}/notes
    POST   /api/events/{event_id}/notes
    PATCH  /api/events/{event_id}/notes/{note_id}
    POST   /api/events/{event_id}/notes/{note_id}/resolve
    DELETE /api/events/{event_id}/notes/{note_id}

Admin and sales tokens both reach these; sales additionally must be
punched in unless the owner disabled the attendance gate (the same
``require_floor_access`` posture as adding a participant — writing a
note is floor work).

Business logic lives in services/event_note_service.py; this file only
adapts the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import EventNote, User
from modules.deals.services import event_note_service
from modules.deals.services.event_note_service import EventNoteError, NoteInput
from modules.scheduling.services.attendance_gate import require_floor_access

router = APIRouter()


# Domain error code -> HTTP status. Anything unmapped is a 400.
_ERROR_STATUS: dict[str, int] = {
    "event_not_found": 404,
    "note_not_found": 404,
    "note_body_empty": 422,
    "note_body_too_long": 422,
    "note_channel_invalid": 422,
    "note_channel_unsupported": 422,
    "note_reminder_no_target": 422,
    "note_reminder_bad_target": 422,
    "note_reminder_no_email": 422,
}


def _raise(exc: EventNoteError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, 400),
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)
    remind_at: datetime | None = None
    # Defaults to the acting rep in the service — "remind me" is the norm.
    remind_user_id: int | None = None
    remind_channel: Literal["email", "sms"] | None = None


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, min_length=1)
    remind_at: datetime | None = None
    remind_user_id: int | None = None
    remind_channel: Literal["email", "sms"] | None = None
    # Explicit, because an omitted optional field can't express "unset".
    clear_reminder: bool = False


class NoteResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = True


class NoteResponse(BaseModel):
    id: int
    event_id: int
    body: str
    author_user_id: int | None
    author_display_name: str | None
    remind_at: datetime | None
    remind_user_id: int | None
    remind_channel: str
    reminder_sent_at: datetime | None
    resolved_at: datetime | None
    edited_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _to_response(note: EventNote) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        event_id=note.event_id,
        body=note.body,
        author_user_id=note.author_user_id,
        author_display_name=note.author_display_name,
        remind_at=note.remind_at,
        remind_user_id=note.remind_user_id,
        remind_channel=note.remind_channel,
        reminder_sent_at=note.reminder_sent_at,
        resolved_at=note.resolved_at,
        edited_at=note.edited_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get("/{event_id}/notes", response_model=list[NoteResponse])
def list_event_notes(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> list[NoteResponse]:
    """The deal's note timeline, newest first."""
    try:
        notes = event_note_service.list_notes(db, event_id)
    except EventNoteError as exc:
        _raise(exc)
    return [_to_response(n) for n in notes]


@router.post("/{event_id}/notes", response_model=NoteResponse, status_code=201)
def create_event_note(
    event_id: int,
    payload: NoteCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> NoteResponse:
    try:
        note = event_note_service.create_note(
            db,
            event_id=event_id,
            data=NoteInput(
                body=payload.body,
                remind_at=payload.remind_at,
                remind_user_id=payload.remind_user_id,
                remind_channel=payload.remind_channel,
            ),
            actor=user,
        )
    except EventNoteError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    db.refresh(note)
    return _to_response(note)


@router.patch("/{event_id}/notes/{note_id}", response_model=NoteResponse)
def update_event_note(
    event_id: int,
    note_id: int,
    payload: NoteUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> NoteResponse:
    try:
        note = event_note_service.update_note(
            db,
            event_id=event_id,
            note_id=note_id,
            body=payload.body,
            remind_at=payload.remind_at,
            remind_user_id=payload.remind_user_id,
            remind_channel=payload.remind_channel,
            clear_reminder=payload.clear_reminder,
            actor=user,
        )
    except EventNoteError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    db.refresh(note)
    return _to_response(note)


@router.post("/{event_id}/notes/{note_id}/resolve", response_model=NoteResponse)
def resolve_event_note(
    event_id: int,
    note_id: int,
    payload: NoteResolve,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> NoteResponse:
    """Mark a follow-up handled (or reopen it)."""
    try:
        note = event_note_service.resolve_note(
            db,
            event_id=event_id,
            note_id=note_id,
            resolved=payload.resolved,
            actor=user,
        )
    except EventNoteError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    db.refresh(note)
    return _to_response(note)


@router.delete("/{event_id}/notes/{note_id}", status_code=204)
def delete_event_note(
    event_id: int,
    note_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> None:
    try:
        event_note_service.delete_note(db, event_id=event_id, note_id=note_id)
    except EventNoteError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
