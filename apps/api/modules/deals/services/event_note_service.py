"""Dated notes on a deal, plus their follow-up reminders.

The rep-facing running log behind the Notes tab: one row per note
(migration 100), newest first, each with an author byline and an optional
"remind me" attached.

Design notes:

  * **Reminders ride on the note.** A follow-up is always about something
    a rep just wrote, so there is no separate reminder entity to keep in
    sync. Clearing ``remind_at`` cancels; ``resolved_at`` retires a
    reminder that already fired (or hasn't yet) without deleting the note.
  * **Editing never rewrites history silently.** ``edited_at`` is stamped
    on any body change so the UI can mark a note as edited.
  * **Soft delete.** ``deleted_at`` hides a note from the timeline and
    from the reminder pass; the row stays for audit.
  * **SMS is schema-ready, not wired.** The CHECK accepts 'sms' so adding
    it later needs no migration, but ``users`` has no phone column, so a
    write asking for it is rejected here with a stable code rather than
    accepted and silently never delivered.

Callers pass an actor (the signed-in user) for the byline and for
defaulting who gets reminded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Event, EventNote, User

# Channels the CHECK allows vs. the ones we can actually deliver today.
# Keep the first in sync with chk_event_notes_remind_channel (migration
# 100); the second grows when a transport lands.
KNOWN_CHANNELS: frozenset[str] = frozenset({"email", "sms"})
DELIVERABLE_CHANNELS: frozenset[str] = frozenset({"email"})
DEFAULT_CHANNEL = "email"

MAX_BODY_CHARS = 5000


class EventNoteError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str = "event_note_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NoteInput:
    body: str
    remind_at: datetime | None = None
    remind_user_id: int | None = None
    remind_channel: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_body(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise EventNoteError("Note cannot be empty.", code="note_body_empty")
    if len(text) > MAX_BODY_CHARS:
        raise EventNoteError(
            f"Note is longer than {MAX_BODY_CHARS} characters.",
            code="note_body_too_long",
        )
    return text


def _resolve_channel(channel: str | None) -> str:
    value = (channel or DEFAULT_CHANNEL).strip().lower()
    if value not in KNOWN_CHANNELS:
        raise EventNoteError(
            "That reminder channel is not allowed.", code="note_channel_invalid"
        )
    if value not in DELIVERABLE_CHANNELS:
        # Accepted by the schema, but nothing can send it yet — say so
        # loudly instead of writing a reminder that dies in the pass.
        raise EventNoteError(
            "Text reminders aren't available yet — staff phone numbers "
            "aren't on file. Use email for now.",
            code="note_channel_unsupported",
        )
    return value


def _validate_reminder(
    db: Session,
    *,
    remind_at: datetime | None,
    remind_user_id: int | None,
    actor_user_id: int | None,
) -> tuple[datetime | None, int | None]:
    """Normalize the reminder half of a write. Returns (remind_at, user_id).

    Defaults the recipient to the acting rep — the overwhelmingly common
    case is "remind ME" — and requires a live user either way so the
    CHECK's promise (a reminder always has a target) holds with a real
    address behind it.
    """
    if remind_at is None:
        return None, None

    when = remind_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    target_id = remind_user_id or actor_user_id
    if target_id is None:
        raise EventNoteError(
            "A reminder needs someone to remind.", code="note_reminder_no_target"
        )
    target = db.get(User, target_id)
    if target is None or target.deleted_at is not None or not target.is_active:
        raise EventNoteError(
            "That user can't receive reminders.", code="note_reminder_bad_target"
        )
    if not (target.email or "").strip():
        raise EventNoteError(
            "That user has no email address on file, so the reminder "
            "couldn't be delivered.",
            code="note_reminder_no_email",
        )
    return when, target_id


def _event_or_error(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise EventNoteError("Deal not found.", code="event_not_found")
    return event


def _note_or_error(db: Session, event_id: int, note_id: int) -> EventNote:
    note = db.get(EventNote, note_id)
    if note is None or note.deleted_at is not None or note.event_id != event_id:
        raise EventNoteError("Note not found.", code="note_not_found")
    return note


def list_notes(db: Session, event_id: int) -> list[EventNote]:
    """The deal's timeline, newest first."""
    _event_or_error(db, event_id)
    return list(
        db.execute(
            select(EventNote)
            .where(
                EventNote.event_id == event_id,
                EventNote.deleted_at.is_(None),
            )
            .order_by(EventNote.created_at.desc(), EventNote.id.desc())
        )
        .scalars()
        .all()
    )


def create_note(
    db: Session,
    *,
    event_id: int,
    data: NoteInput,
    actor: User | None = None,
) -> EventNote:
    _event_or_error(db, event_id)
    body = _clean_body(data.body)
    actor_id = actor.id if actor is not None else None

    remind_at, remind_user_id = _validate_reminder(
        db,
        remind_at=data.remind_at,
        remind_user_id=data.remind_user_id,
        actor_user_id=actor_id,
    )
    channel = _resolve_channel(data.remind_channel) if remind_at else DEFAULT_CHANNEL

    note = EventNote(
        event_id=event_id,
        body=body,
        author_user_id=actor_id,
        author_display_name=(
            (actor.full_name or actor.username) if actor is not None else None
        ),
        remind_at=remind_at,
        remind_user_id=remind_user_id,
        remind_channel=channel,
    )
    db.add(note)
    db.flush()
    return note


def update_note(
    db: Session,
    *,
    event_id: int,
    note_id: int,
    body: str | None = None,
    remind_at: datetime | None = None,
    remind_user_id: int | None = None,
    remind_channel: str | None = None,
    clear_reminder: bool = False,
    actor: User | None = None,
) -> EventNote:
    """Patch a note. Only the fields supplied move.

    ``clear_reminder`` cancels the follow-up (the wire has no way to send
    "set this to NULL" via an optional field, so it gets its own flag).
    Re-arming a reminder that already fired clears ``reminder_sent_at`` so
    the pass will deliver the new time.
    """
    note = _note_or_error(db, event_id, note_id)
    now = _now()

    if body is not None:
        cleaned = _clean_body(body)
        if cleaned != note.body:
            note.body = cleaned
            note.edited_at = now

    if clear_reminder:
        note.remind_at = None
        note.remind_user_id = None
        note.reminder_sent_at = None
    elif remind_at is not None:
        when, target_id = _validate_reminder(
            db,
            remind_at=remind_at,
            remind_user_id=remind_user_id,
            actor_user_id=(actor.id if actor is not None else None),
        )
        rescheduled = note.remind_at != when
        note.remind_at = when
        note.remind_user_id = target_id
        note.remind_channel = _resolve_channel(remind_channel or note.remind_channel)
        if rescheduled:
            # A moved reminder is a new promise — let it fire again.
            note.reminder_sent_at = None
            note.resolved_at = None
            note.resolved_by_user_id = None

    note.updated_at = now
    db.flush()
    return note


def resolve_note(
    db: Session,
    *,
    event_id: int,
    note_id: int,
    resolved: bool = True,
    actor: User | None = None,
) -> EventNote:
    """Mark a follow-up handled (or un-handle it)."""
    note = _note_or_error(db, event_id, note_id)
    now = _now()
    if resolved:
        note.resolved_at = now
        note.resolved_by_user_id = actor.id if actor is not None else None
    else:
        note.resolved_at = None
        note.resolved_by_user_id = None
    note.updated_at = now
    db.flush()
    return note


def delete_note(db: Session, *, event_id: int, note_id: int) -> None:
    """Soft-delete: drops out of the timeline and out of the reminder pass."""
    note = _note_or_error(db, event_id, note_id)
    now = _now()
    note.deleted_at = now
    note.updated_at = now
    db.flush()


def open_follow_ups_for_user(
    db: Session, *, user_id: int, limit: int = 50
) -> list[EventNote]:
    """A rep's unresolved follow-ups, soonest first — for a 'due' surface."""
    return list(
        db.execute(
            select(EventNote)
            .where(
                EventNote.remind_user_id == user_id,
                EventNote.remind_at.is_not(None),
                EventNote.resolved_at.is_(None),
                EventNote.deleted_at.is_(None),
            )
            .order_by(EventNote.remind_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
