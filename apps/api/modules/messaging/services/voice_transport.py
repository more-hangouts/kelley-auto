"""Twilio Voice click-to-call bridge (CRM "Business number call").

An additive path beside the native ``tel:`` dialer in ``CallContact.jsx``. A
salesperson taps "Business number call"; instead of the device dialing the
contact directly (which exposes the rep's personal cell), Twilio:

  1. Places an outbound call to the REP's phone (``initiate_bridge_call``).
  2. When the rep answers, Twilio requests the TwiML callback URL we handed it.
  3. That callback returns ``<Dial callerId=BUSINESS><Number>CONTACT</Number>``,
     so the second leg reaches the contact showing the business caller ID.

Design, mirroring the SMS transport (no ``twilio`` SDK dependency — a thin
httpx POST to the REST API, matching ``sms_transport`` and the stdlib inbound
signature verifier):

  * NEVER raises — every failure becomes a ``VoiceCallResult(ok=False, ...)`` so
    the router can turn it into a clean error without a transaction blowing up.
  * The contact number the second leg dials is carried INSIDE a signed,
    short-lived JWT (``mint_bridge_token`` / ``verify_bridge_token``), NOT a
    query parameter. The public TwiML route dials only the number the signed
    token authorizes, so it can never be coerced into dialing an arbitrary
    number by a forged request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from jwt.exceptions import InvalidTokenError

from config import settings

log = logging.getLogger(__name__)

_CALL_TIMEOUT_SECONDS = 10.0
_TWIML_BRIDGE_PATH = "/api/webhooks/twilio/voice/bridge"
_TOKEN_ALGORITHM = "HS256"
# The signed bridge token only has to live long enough for Twilio to ring the
# rep and request the callback — a couple of minutes at most. Keep it tight so
# a leaked token is useless almost immediately.
_TOKEN_TTL_SECONDS = 300
_TOKEN_PURPOSE = "voice_bridge"


@dataclass
class VoiceCallResult:
    ok: bool
    provider_call_sid: str | None = None
    status: str | None = None  # Twilio's initial call status (queued/…)
    error_code: str | None = None
    error_message: str | None = None


def voice_transport_configured() -> bool:
    """True when the master switch is on AND account creds + a business voice
    caller-ID number are all present. The router checks this before logging an
    attempt so a misconfiguration is a clean 503, not a half-started call."""
    return bool(
        settings.TWILIO_VOICE_ENABLED
        and settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_VOICE_FROM_NUMBER
    )


def _token_secret() -> str:
    # Reuse the app secret; the token is short-lived and single-purpose. Falls
    # back to SECRET_KEY (always present — validate_config requires it).
    return settings.RESCHEDULE_TOKEN_SECRET or settings.SECRET_KEY


def mint_bridge_token(*, call_attempt_id: int, contact_id: int, dial_number: str) -> str:
    """Sign a short-lived token binding one call attempt to the EXACT contact
    number the TwiML callback is allowed to dial. The number travels inside the
    signed claims — never as a URL parameter — so the public callback route can
    only ever bridge to the number this token authorizes."""
    now = datetime.now(timezone.utc)
    claims = {
        "purpose": _TOKEN_PURPOSE,
        "cid": contact_id,
        "aid": call_attempt_id,
        "dial": dial_number,
        "iat": now,
        "exp": now + timedelta(seconds=_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(claims, _token_secret(), algorithm=_TOKEN_ALGORITHM)


def verify_bridge_token(token: str) -> dict:
    """Decode + validate a bridge token. Returns the claims dict (with ``dial``
    the authorized contact number). Raises ``InvalidBridgeToken`` on ANY failure
    — every path collapses to one exception so the route can't leak which check
    failed."""
    try:
        claims = jwt.decode(token, _token_secret(), algorithms=[_TOKEN_ALGORITHM])
    except InvalidTokenError as exc:  # expired, bad signature, malformed
        raise InvalidBridgeToken("token decode failed") from exc
    if claims.get("purpose") != _TOKEN_PURPOSE:
        raise InvalidBridgeToken("token purpose mismatch")
    dial = claims.get("dial")
    if not dial or not isinstance(dial, str):
        raise InvalidBridgeToken("token missing dial number")
    return claims


class InvalidBridgeToken(Exception):
    """Raised when a voice-bridge token fails to decode or validate."""


def bridge_callback_url(token: str) -> str:
    """The public URL Twilio requests when the rep answers. Built from
    PUBLIC_API_BASE_URL (behind Caddy the app sees 127.0.0.1, which Twilio
    can't reach) with the signed token as the single query parameter."""
    base = settings.PUBLIC_API_BASE_URL.rstrip("/")
    return f"{base}{_TWIML_BRIDGE_PATH}?token={token}"


def initiate_bridge_call(
    *, rep_number: str, callback_url: str
) -> VoiceCallResult:
    """Place the FIRST leg: Twilio calls the rep. When the rep answers Twilio
    fetches ``callback_url`` for the TwiML that dials the contact. NEVER raises
    — a transport error or Twilio rejection returns ``ok=False``.

    ``To`` is the rep's phone; ``From`` is the business voice number (so if the
    rep's phone shows a caller ID it's the business, not an unknown number).
    """
    if not voice_transport_configured():
        return VoiceCallResult(
            ok=False,
            error_code="not_configured",
            error_message="Twilio voice is not configured",
        )

    data = {
        "To": rep_number,
        "From": settings.TWILIO_VOICE_FROM_NUMBER,
        "Url": callback_url,
        "Method": "POST",
    }
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Calls.json"
    )
    try:
        with httpx.Client(timeout=_CALL_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                data=data,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            )
    except httpx.HTTPError as exc:
        log.warning("twilio voice transport error to=%s: %s", rep_number, exc)
        return VoiceCallResult(
            ok=False,
            error_code="transport_error",
            error_message=f"{type(exc).__name__}: {exc}"[:500],
        )

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code in (200, 201):
        return VoiceCallResult(
            ok=True,
            provider_call_sid=payload.get("sid"),
            status=payload.get("status"),
        )

    log.warning(
        "twilio voice rejected to=%s http=%s code=%s: %s",
        rep_number,
        resp.status_code,
        payload.get("code"),
        payload.get("message"),
    )
    return VoiceCallResult(
        ok=False,
        error_code=str(payload.get("code") or f"http_{resp.status_code}"),
        error_message=str(payload.get("message") or resp.text)[:500],
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_bridge_twiml(*, dial_number: str) -> str:
    """TwiML for the rep-answered leg: dial the contact, presenting the business
    voice number as caller ID. The number comes from the verified token, so this
    never dials anything an unauthenticated caller could inject."""
    caller_id = settings.TWILIO_VOICE_FROM_NUMBER or ""
    dial_attrs = f' callerId="{_xml_escape(caller_id)}"' if caller_id else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Dial{dial_attrs}>"
        f"<Number>{_xml_escape(dial_number)}</Number>"
        "</Dial>"
        "</Response>"
    )
