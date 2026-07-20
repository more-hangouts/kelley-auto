"""Public storefront web-chat endpoints (design ported from catering210).

Five routes, all public + rate-limited, mounted at ``/api/web-chat``:

    GET  /script                    seeded/versioned question tree
    POST /start                     contact capture → conversation + intake
    POST /{session_id}/answer       one scripted tap → inbox line (+ canned reply)
    POST /{session_id}/message      free text → escalates to a person
    GET  /{session_id}/messages     cursor poll for replies (after_id)

Spam posture, all at the edge:
  * path-level ``wc_<32 hex>`` pattern rejects probes before any DB hit;
  * per-endpoint Redis IP buckets (start is the tightest);
  * honeypot + instant-submit check on /start returns a FAKE session and
    writes nothing — the bot can't tell it was caught;
  * bot user agents get the same fake session.

Convention: the service flushes, this router owns the commit."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.redis_rate_limit import rate_limit
from database.connection import get_db
from services import storefront_analytics_service
from modules.messaging.services import web_chat_service
from modules.messaging.services.web_chat_service import WebChatError

router = APIRouter()

# Bots submit instantly; humans take seconds to tap through intake.
_MIN_INTERACTION_MS = 1500

_SessionId = Annotated[str, Path(pattern=r"^wc_[0-9a-f]{32}$")]

_script_ip_limit = rate_limit(bucket="web_chat_script_ip", limit=60, window=60)
_start_ip_limit = rate_limit(bucket="web_chat_start_ip", limit=5, window=60)
_message_ip_limit = rate_limit(bucket="web_chat_message_ip", limit=30, window=60)
_poll_ip_limit = rate_limit(bucket="web_chat_poll_ip", limit=120, window=60)


class StartPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    sms_opt_in: bool = False
    page_url: str | None = Field(default=None, max_length=1000)
    # Client-side intake transcript: [{question, answer}], replayed into the
    # first inbox message so staff see the taps without N server round-trips.
    intake: list[dict[str, Any]] | None = None
    script_version: int | None = None
    # Honeypot — must stay empty — and time-to-submit for the instant check.
    company_website: str | None = Field(default=None, max_length=200)
    elapsed_ms: int = 0


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_id: str = Field(max_length=64)
    option_id: str = Field(max_length=64)


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    body: str = Field(max_length=2000)


def _raise(exc: WebChatError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=exc.code)


def _fake_session() -> dict:
    """Indistinguishable-from-real response for caught bots; writes nothing.
    The minted id matches the session pattern but no conversation exists, so
    any follow-up call 404s like an expired session would."""
    return {
        "session_id": web_chat_service.mint_session_id(),
        "created": True,
        "messages": [],
    }


@router.get("/script", dependencies=[Depends(_script_ip_limit)])
def get_script(db: Annotated[Session, Depends(get_db)]) -> dict:
    script = web_chat_service.get_active_script(db)
    return {"script": script}


@router.post("/start", dependencies=[Depends(_start_ip_limit)])
def start(
    payload: StartPayload,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if (
        payload.company_website
        or payload.elapsed_ms < _MIN_INTERACTION_MS
        or storefront_analytics_service.is_bot_user_agent(
            request.headers.get("user-agent")
        )
    ):
        return _fake_session()
    try:
        result = web_chat_service.start_chat(
            db,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            sms_opt_in=payload.sms_opt_in,
            page_url=payload.page_url,
            intake=payload.intake,
            script_version=payload.script_version,
        )
    except WebChatError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{session_id}/answer", dependencies=[Depends(_message_ip_limit)])
def answer(
    session_id: _SessionId,
    payload: AnswerPayload,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    try:
        result = web_chat_service.record_answer(
            db,
            session_id=session_id,
            question_id=payload.question_id,
            option_id=payload.option_id,
        )
    except WebChatError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{session_id}/message", dependencies=[Depends(_message_ip_limit)])
def message(
    session_id: _SessionId,
    payload: MessagePayload,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    try:
        result = web_chat_service.record_visitor_message(
            db, session_id=session_id, body=payload.body
        )
    except WebChatError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.get("/{session_id}/messages", dependencies=[Depends(_poll_ip_limit)])
def messages(
    session_id: _SessionId,
    db: Annotated[Session, Depends(get_db)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    page_url: Annotated[str | None, Query(max_length=1000)] = None,
) -> dict:
    try:
        result = web_chat_service.poll_messages(
            db, session_id=session_id, after_id=after_id, page_url=page_url
        )
    except WebChatError as exc:
        db.rollback()
        _raise(exc)
    db.commit()  # presence stamp
    return result
