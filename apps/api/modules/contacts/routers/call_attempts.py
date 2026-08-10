"""Native-dialer call-attempt endpoints (Phase 7).

Contact-scoped CRUD the mobile dashboard uses to log a tap-to-call BEFORE the
device opens ``tel:``, then to attach a salesperson-reported outcome when the
browser becomes visible again. Not Twilio Voice.

  POST   /api/contacts/{contact_id}/call-attempts
  PATCH  /api/contacts/{contact_id}/call-attempts/{attempt_id}
  GET    /api/contacts/{contact_id}/call-attempts

Auth mirrors the sibling contact routes: ``require_any_scope("admin","sales")``
so reps can log their own calls. The salesperson identity is ALWAYS taken from
the authenticated token — never from the request body.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from database.auth import require_any_scope
from database.connection import get_db
from database.models import Contact, User
from modules.analytics.services import call_attempts as svc

router = APIRouter()

# Outcomes the client may report (excludes the birth state call_initiated).
OutcomeLiteral = Literal[
    "connected",
    "left_voicemail",
    "no_answer",
    "busy",
    "wrong_number",
    "cancelled",
]


class CallAttemptCreate(BaseModel):
    # NOTE: no salesperson_user_id — identity comes from the token, never the
    # browser. phone is the number actually dialed (normalized server-side).
    phone: str = Field(min_length=1, max_length=40)
    event_id: int | None = None
    source: str | None = Field(default=None, max_length=40)
    idempotency_key: str | None = Field(default=None, max_length=64)


class CallAttemptPatch(BaseModel):
    outcome: OutcomeLiteral | None = None
    notes: str | None = Field(default=None, max_length=2000)


class BridgeCallCreate(BaseModel):
    # Optional per-device callback number for the rep leg; when omitted the
    # server uses TWILIO_VOICE_REP_FALLBACK_NUMBER. Identity still comes from
    # the token — this only chooses which phone Twilio rings first.
    rep_phone: str | None = Field(default=None, max_length=40)
    event_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


class BrowserCallCreate(BaseModel):
    # No phone and no rep number: the destination is the contact's own number
    # (resolved server-side) and the "rep leg" is the browser itself.
    event_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


def _load_contact(db: Session, contact_id: int) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="contact_not_found")
    return contact


@router.post("/{contact_id}/call-attempts", status_code=201)
def create_call_attempt(
    contact_id: int,
    payload: CallAttemptCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    contact = _load_contact(db, contact_id)
    try:
        attempt, created = svc.log_call_attempt(
            db,
            contact=contact,
            user=user,
            raw_phone=payload.phone,
            event_id=payload.event_id,
            source=payload.source,
            idempotency_key=payload.idempotency_key,
        )
    except svc.CallAttemptError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc
    db.commit()
    # 201 on fresh create; 200 on an idempotent replay of the same key.
    body = svc.serialize(attempt)
    body["created"] = created
    return body


@router.post("/{contact_id}/call-attempts/bridge", status_code=201)
def start_bridge_call(
    contact_id: int,
    payload: BridgeCallCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Twilio Voice click-to-call bridge (business-number call path).

    Logs a call attempt (same tracking as the native ``tel:`` path) and asks
    Twilio to ring the rep, then bridge to the contact so the contact sees the
    business number. Auth mirrors call logging: the salesperson identity comes
    from the token, never the body. Returns ``call_attempt_id`` and, when Twilio
    accepted the first leg, ``provider_call_sid``.
    """
    contact = _load_contact(db, contact_id)

    rep_phone = payload.rep_phone or settings.TWILIO_VOICE_REP_FALLBACK_NUMBER
    if not rep_phone:
        raise HTTPException(status_code=422, detail="rep_phone_missing")

    try:
        attempt, result = svc.start_bridge_call(
            db,
            contact=contact,
            user=user,
            rep_number=rep_phone,
            event_id=payload.event_id,
            idempotency_key=payload.idempotency_key,
        )
    except svc.CallAttemptError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc

    if not result.ok:
        # The attempt was logged, but Twilio didn't accept the outbound call.
        # Roll back the logged row so a failed bridge doesn't leave a phantom
        # call in the manager reports, and surface the provider reason.
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail={
                "code": "bridge_call_failed",
                "provider_error": result.error_message,
            },
        )

    db.commit()
    body = svc.serialize(attempt)
    body["call_attempt_id"] = attempt.id
    body["provider_call_sid"] = result.provider_call_sid
    body["provider_status"] = result.status
    return body


@router.post("/{contact_id}/call-attempts/browser", status_code=201)
def start_browser_call(
    contact_id: int,
    payload: BrowserCallCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Browser softphone: authorize + log one dashboard-placed call.

    Logs the attempt (same tracking as the ``tel:`` and bridge paths) and
    returns the signed ``dial_token`` the SPA passes to the Voice SDK. The token
    is what the public TwiML route trusts for the destination number, so the
    browser never gets to name the number it dials.

    No call is placed here — the browser's ``Device.connect()`` does that.
    """
    contact = _load_contact(db, contact_id)

    try:
        attempt, dial_token = svc.start_browser_call(
            db,
            contact=contact,
            user=user,
            event_id=payload.event_id,
            idempotency_key=payload.idempotency_key,
        )
    except svc.CallAttemptError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc

    db.commit()
    body = svc.serialize(attempt)
    body["call_attempt_id"] = attempt.id
    body["dial_token"] = dial_token
    return body


@router.patch("/{contact_id}/call-attempts/{attempt_id}")
def update_call_attempt(
    contact_id: int,
    attempt_id: int,
    payload: CallAttemptPatch,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    _load_contact(db, contact_id)
    attempt = svc.get_attempt(db, contact_id=contact_id, attempt_id=attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="call_attempt_not_found")

    # Notes-only patches are allowed (outcome stays as-is). At least one field
    # must be present or there's nothing to do.
    if "outcome" not in payload.model_fields_set and "notes" not in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="nothing_to_update")

    try:
        svc.record_outcome(
            db,
            attempt=attempt,
            outcome=payload.outcome if "outcome" in payload.model_fields_set else None,
            notes=payload.notes if "notes" in payload.model_fields_set else None,
        )
    except svc.CallAttemptError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc
    db.commit()
    return svc.serialize(attempt)


@router.get("/{contact_id}/call-attempts")
def list_call_attempts(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    _load_contact(db, contact_id)
    rows = svc.list_for_contact(db, contact_id=contact_id)
    return {"call_attempts": [svc.serialize(a) for a in rows]}
