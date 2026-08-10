"""Browser softphone endpoints (Twilio Voice JS SDK).

One authenticated route: the dashboard asks for a short-lived AccessToken so
its Voice SDK ``Device`` can register as a Twilio client and place calls with
audio in the browser tab.

  POST /api/voice/token   -> { token, identity, expires_in, from_number }
  GET  /api/voice/status  -> { enabled, reason }

The token is minted for the identity derived from the AUTHENTICATED user id and
grants OUTGOING calls only, through our TwiML App. It is never derived from
request input, and it carries no incoming grant — this ships outbound-only, so
the business number's inbound routing is untouched.

Which number a call may actually reach is NOT decided here. That is authorized
per-call by the signed dial token from
``POST /api/contacts/{id}/call-attempts/browser``, which the TwiML route
verifies before dialing. An AccessToken alone lets a client connect to our
TwiML App; it cannot choose a destination.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config import settings
from database.auth import require_any_scope
from database.connection import get_db
from database.models import Contact, InboundCall, User
from modules.core.services.phone import normalize_phone_e164
from modules.messaging.services import voice_routing, voice_transport

router = APIRouter()


@router.get("/status")
def softphone_status(
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Whether the dashboard should show the in-browser call control at all.

    Lets the SPA hide the button cleanly instead of surfacing a 503 after the
    rep has already clicked it.
    """
    enabled = voice_transport.softphone_configured()
    reason = None
    if not enabled:
        if not settings.TWILIO_SOFTPHONE_ENABLED:
            reason = "disabled"
        elif not (settings.TWILIO_API_KEY_SID and settings.TWILIO_API_KEY_SECRET):
            reason = "missing_api_key"
        elif not settings.TWILIO_TWIML_APP_SID:
            reason = "missing_twiml_app"
        elif not settings.TWILIO_VOICE_FROM_NUMBER:
            reason = "missing_caller_id"
        else:
            reason = "not_configured"
    return {"enabled": enabled, "reason": reason}


@router.post("/token")
def create_access_token(
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Mint a Voice AccessToken for THIS user's browser client.

    Identity comes from the authenticated user id, never the request body, so a
    rep cannot register as someone else. Short-lived; the SPA refreshes before
    expiry and the SDK re-registers.
    """
    try:
        token = voice_transport.mint_access_token(user_id=user.id)
    except voice_transport.SoftphoneNotConfigured as exc:
        raise HTTPException(status_code=503, detail="softphone_not_configured") from exc

    return {
        "token": token,
        "identity": voice_transport.softphone_identity(user.id),
        "expires_in": settings.TWILIO_SOFTPHONE_TOKEN_TTL_SECONDS,
        # Shown in the dialer UI so the rep knows which number the contact sees.
        "from_number": settings.TWILIO_VOICE_FROM_NUMBER,
        # Whether this token can RECEIVE calls, so the SPA knows to register.
        "can_receive": settings.TWILIO_INBOUND_TO_BROWSER_ENABLED,
    }


# --- presence --------------------------------------------------------------


class PresencePayload(BaseModel):
    # A rep can stay on the dashboard but stop taking calls.
    available: bool = True


@router.post("/presence")
def heartbeat(
    payload: PresencePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Heartbeat from a registered dashboard softphone.

    Inbound routing needs to know whether anyone is actually there BEFORE it
    decides to ring browsers — Twilio can't tell us, and ringing an empty
    office would make every caller wait out the full timeout.
    """
    voice_routing.touch_presence(
        db,
        user_id=user.id,
        identity=voice_transport.softphone_identity(user.id),
        available=payload.available,
    )
    db.commit()
    return {"ok": True, "available": payload.available}


@router.delete("/presence")
def go_offline(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Clean sign-off. Staleness already covers closed laptops; this just makes
    the tidy case instant."""
    voice_routing.clear_presence(db, user_id=user.id)
    db.commit()
    return {"ok": True}


@router.get("/ringing")
def ringing_call(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Context for the call currently ringing the dashboard.

    The Voice SDK hands the browser a leg whose ``From`` is our own Twilio
    number, not the customer's, so the incoming-call card would otherwise have
    nothing useful to show. The browser calls this on ring to get the real
    caller and their matched contact.

    Matched by recency among calls in the browser-ring stage. Adequate for a
    shop that takes a handful of concurrent calls; if that stops being true,
    the precise fix is to persist each rung leg's CallSid and look up by the
    SID the browser already has.
    """
    row = (
        db.query(InboundCall)
        .filter(
            InboundCall.conference_name.isnot(None),
            InboundCall.status.in_(("received", "ringing")),
            InboundCall.created_at
            >= sql_text("NOW() - INTERVAL '90 seconds'"),
        )
        .order_by(InboundCall.created_at.desc())
        .first()
    )
    if row is None:
        return {"call": None}

    contact_name = None
    if row.contact_id:
        contact = db.get(Contact, row.contact_id)
        contact_name = contact.display_name if contact else None

    return {
        "call": {
            "id": row.id,
            "call_sid": row.provider_call_sid,
            "conference": row.conference_name,
            "from_number": row.from_number,
            "contact_id": row.contact_id,
            "contact_name": contact_name,
            "caller_city": row.caller_city,
            "caller_state": row.caller_state,
        }
    }


# --- hold ------------------------------------------------------------------


class HoldPayload(BaseModel):
    on: bool


@router.post("/calls/{call_sid}/hold")
def hold_call(
    call_sid: str,
    payload: HoldPayload,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    """Put the CALLER on hold (or take them off), leaving the rep's leg up.

    Only works for calls answered through the browser, because only those are
    routed via a conference — holding a participant is a conference operation.
    ``call_sid`` is the CALLER's inbound sid, which is also the conference
    participant id.
    """
    row = (
        db.query(InboundCall)
        .filter(InboundCall.provider_call_sid == call_sid)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="call_not_found")
    if not row.conference_name:
        raise HTTPException(status_code=409, detail="call_not_held_in_conference")

    ok = voice_routing.set_hold(
        conference_name=row.conference_name,
        participant_call_sid=call_sid,
        on=payload.on,
    )
    if not ok:
        raise HTTPException(status_code=502, detail="hold_failed")
    return {"ok": True, "on_hold": payload.on}


# --- routing settings ------------------------------------------------------


class VoiceSettingsPayload(BaseModel):
    inbound_mode: Literal["browser_then_fallback", "fallback_only", "browser_only"] | None = None
    # None clears the fallback: callers then hear the unavailable message
    # rather than being sent to a number nobody maintains.
    fallback_number: str | None = None
    ring_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    fallback_timeout_seconds: int | None = Field(default=None, ge=5, le=120)


def _serialize_settings(cfg) -> dict:
    return {
        "inbound_mode": cfg.inbound_mode,
        "fallback_number": cfg.fallback_number,
        "ring_timeout_seconds": cfg.ring_timeout_seconds,
        "fallback_timeout_seconds": cfg.fallback_timeout_seconds,
    }


@router.get("/settings")
def read_settings(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> dict:
    cfg = voice_routing.get_settings(db)
    db.commit()
    body = _serialize_settings(cfg)
    body["online_reps"] = len(voice_routing.online_identities(db))
    return body


@router.put("/settings")
def update_settings(
    payload: VoiceSettingsPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin"))],
) -> dict:
    """Admin-only: change where inbound calls go.

    The fallback number is normalized to E.164 before storage — a number typed
    as "(210) 251-3644" must not silently become a destination Twilio rejects
    at 2am when it's finally used.
    """
    cfg = voice_routing.get_settings(db)
    data = payload.model_dump(exclude_unset=True)

    if "fallback_number" in data:
        raw = (data["fallback_number"] or "").strip()
        if raw:
            normalized = normalize_phone_e164(raw)
            if not normalized:
                raise HTTPException(status_code=422, detail="invalid_fallback_number")
            cfg.fallback_number = normalized
        else:
            cfg.fallback_number = None

    for field in ("inbound_mode", "ring_timeout_seconds", "fallback_timeout_seconds"):
        if field in data and data[field] is not None:
            setattr(cfg, field, data[field])

    # A mode that needs a destination must have one, or the "fallback" is a
    # silent dead end for every caller.
    if cfg.inbound_mode == "fallback_only" and not cfg.fallback_number:
        raise HTTPException(status_code=422, detail="fallback_number_required")

    cfg.updated_by_user_id = user.id
    db.commit()
    return _serialize_settings(cfg)
