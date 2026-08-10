"""Admin CRM inbox router (Omnichannel Inbox Plan Part 5; Phase 2).

Read + triage over the omnichannel conversation store, under ``/api/inbox``.
Admin-scoped. Outbound send exists as an endpoint but is hard-gated off until
the A2P campaign clears (``inbox_service.send_reply`` raises 503). Thin per the
repo convention — all logic in ``services.inbox_service``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from database.connection import get_db
from database.models import User
from modules.messaging.services import inbox_service
from modules.messaging.services.inbox_service import InboxError

router = APIRouter()

# Broad inbox triage (list/read/assign) stays admin-only. The two targeted
# operations a pipeline sales rep needs — start/reuse an SMS thread for a
# contact, and send into a thread — allow admin OR sales (Phase 8). They cannot
# browse the whole inbox; they act on a specific contact/conversation. All the
# consent/opt-out/quiet-hours guards live in the service, so widening the scope
# never widens what can actually be sent.
_start_or_send_scope = require_any_scope("admin", "sales")


def _raise(exc: InboxError) -> None:
    body: dict[str, str] = {"code": exc.code}
    if getattr(exc, "detail", None):
        body["message"] = exc.detail
    raise HTTPException(status_code=exc.http_status, detail=body) from exc


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    # Sentinel-free: assignee is only touched when the key is present.
    assigned_user_id: int | None = None


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=1600)
    # Set true to send an SMS despite quiet hours (the composer's "send
    # anyway" after a 409 quiet_hours). Ignored on channels without the gate.
    allow_quiet_hours: bool = False


class StartSmsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: int
    # Optional originating deal — links a NEW thread to the deal for context.
    # Never accepts a destination number; the phone comes from the contact.
    event_id: int | None = None


@router.get("/contacts/{contact_id}/messages")
def contact_messages(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(_start_or_send_scope)],
) -> dict:
    """SMS messages across a contact's conversations, for the contact-activity
    timeline. Read-only over the canonical ConversationMessage rows."""
    return {"messages": inbox_service.messages_for_contact(db, contact_id=contact_id)}


@router.get("/events/{event_id}/messages")
def event_messages(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(_start_or_send_scope)],
) -> dict:
    """SMS messages on the conversation linked to a deal, for the deal-activity
    timeline. Read-only over ConversationMessage."""
    return {"messages": inbox_service.messages_for_event(db, event_id=event_id)}


@router.get("/unread-count")
def unread_count(
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Badge count plus the newest unread thread, for the dashboard poll.

    ``latest`` is additive — it was added for the arrival toast and existing
    callers that only read ``unread`` are unaffected. Both come from one
    predicate (``_unread_filter``) so the toast can never fire for something
    the badge does not also count.
    """
    return {
        "unread": inbox_service.unread_count_for_user(db, admin.id),
        "latest": inbox_service.latest_unread_for_user(db, admin.id),
    }


@router.get("/conversations")
def list_conversations(
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    unread: bool = Query(default=False),
    unlinked: bool = Query(default=False),
    mine: bool = Query(default=False),
    q: str | None = Query(default=None),
) -> dict:
    conversations = inbox_service.list_conversations(
        db,
        user_id=admin.id,
        channel=channel,
        status=status,
        only_unread=unread,
        only_unlinked=unlinked,
        assigned_user_id=admin.id if mine else None,
        q=q,
    )
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    data = inbox_service.conversation_detail(
        db, conversation_id, user_id=admin.id
    )
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found"})
    db.commit()  # persist the read watermark
    return data


@router.patch("/conversations/{conversation_id}")
def patch_conversation(
    conversation_id: int,
    payload: ConversationPatch,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    fields = payload.model_dump(exclude_unset=True)
    kwargs = {}
    if "status" in fields:
        kwargs["status"] = fields["status"]
    if "assigned_user_id" in fields:
        kwargs["assigned_user_id"] = fields["assigned_user_id"]
    if not kwargs:
        raise HTTPException(status_code=422, detail={"code": "nothing_to_update"})
    try:
        result = inbox_service.set_conversation_fields(db, conversation_id, **kwargs)
    except InboxError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/conversations/sms")
def start_sms_conversation(
    payload: StartSmsBody,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(_start_or_send_scope)],
) -> dict:
    """Create or reuse the canonical SMS conversation for a contact. Idempotent
    and race-safe; does NOT send a message. Returns conversation_id, contact
    summary, server-authoritative eligibility, and whether it was newly
    created."""
    try:
        result = inbox_service.start_sms_conversation(
            db, contact_id=payload.contact_id, event_id=payload.event_id
        )
    except InboxError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int,
    payload: ReplyBody,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(_start_or_send_scope)],
) -> dict:
    # Web chat sends immediately; SMS sends via Twilio when SMS_SENDING_ENABLED
    # is on (past opt-out + quiet-hours guards); Meta stays 501 until App Review.
    try:
        result = inbox_service.send_reply(
            db,
            conversation_id,
            body=payload.body,
            user_id=admin.id,
            allow_quiet_hours=payload.allow_quiet_hours,
        )
    except InboxError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
