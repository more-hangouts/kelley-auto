"""Public Twilio webhook endpoints (Omnichannel Inbox Plan Part 4; Phase 2).

Inbound SMS only. Outbound status callbacks arrive in Phase 3 with the send
path. The endpoint is public and unauthenticated by design — Twilio's request
signature is the auth. Flow, in order:

  1. Verify ``X-Twilio-Signature`` against the **public** URL (built from
     ``PUBLIC_API_BASE_URL`` + path — behind Caddy the app sees the internal
     127.0.0.1 URL, which would fail verification).
  2. Store the raw payload first (``webhook_events``, header-redacted) keyed by
     MessageSid — this is the audit/replay buffer and gives idempotency: a
     Twilio retry of the same MessageSid short-circuits to an empty 200.
  3. Thread + persist the message, link a known contact, fire the staff
     notification.
  4. Return empty TwiML so Twilio doesn't treat the reply as an error.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.redis_rate_limit import enforce_or_raise
from config import settings
from database.connection import get_db
from modules.messaging.services import inbox_service, voice_transport, webhook_ingest
from modules.messaging.services.twilio_signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter()

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml() -> Response:
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


def _public_url(request: Request) -> str:
    return settings.PUBLIC_API_BASE_URL.rstrip("/") + request.url.path


def _extract_media(form: dict[str, str]) -> list[dict]:
    """Twilio MMS: NumMedia + MediaUrl{i}/MediaContentType{i}. We store the
    (temporary) URLs; self-hosting is deferred to the media-archival slice."""
    try:
        n = int(form.get("NumMedia", "0") or "0")
    except ValueError:
        n = 0
    out: list[dict] = []
    for i in range(n):
        url = form.get(f"MediaUrl{i}")
        if url:
            out.append(
                {"url": url, "content_type": form.get(f"MediaContentType{i}")}
            )
    return out


@router.post("/sms")
async def inbound_sms(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    form_multi = await request.form()
    form = {k: (str(v) if v is not None else "") for k, v in form_multi.items()}

    # 1. Signature verification against the public URL.
    if settings.INBOUND_SMS_REQUIRE_SIGNATURE:
        if not settings.TWILIO_AUTH_TOKEN:
            log.error("inbound_sms: TWILIO_AUTH_TOKEN unset; cannot verify")
            raise HTTPException(
                status_code=503, detail={"code": "sms_not_configured"}
            )
        if not verify_signature(
            settings.TWILIO_AUTH_TOKEN,
            _public_url(request),
            form,
            request.headers.get("X-Twilio-Signature"),
        ):
            raise HTTPException(
                status_code=403, detail={"code": "invalid_signature"}
            )
    else:
        log.warning("inbound_sms: signature verification bypassed (dev flag)")

    message_sid = form.get("MessageSid") or form.get("SmsSid")
    from_number = form.get("From", "")
    to_number = form.get("To", "")
    if not message_sid or not from_number:
        raise HTTPException(status_code=400, detail={"code": "malformed_webhook"})

    # Modest per-sender cap so a stuck loop can't flood the endpoint.
    enforce_or_raise(
        bucket="twilio_inbound_sms",
        scoped=from_number,
        limit=60,
        window=60,
        request=request,
    )

    # 2. Raw-store first (audit + idempotency via (source, external_id)).
    try:
        raw = webhook_ingest.record_webhook_event(
            db,
            source="twilio",
            event_type="inbound_sms",
            external_id=message_sid,
            payload=form,
            headers=dict(request.headers),
        )
    except IntegrityError:
        db.rollback()
        # Retry of an already-ingested MessageSid — idempotent no-op.
        return _twiml()

    # 3. Thread + persist + notify.
    msg, conv, created = inbox_service.record_inbound_sms(
        db,
        message_sid=message_sid,
        from_number=from_number,
        to_number=to_number,
        body=form.get("Body"),
        media=_extract_media(form),
    )
    if created:
        inbox_service.notify_inbound(db, conv, msg)

    raw.processed = True
    raw.processed_at = datetime.now(timezone.utc)
    db.commit()

    return _twiml()


@router.post("/status")
async def delivery_status(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Twilio delivery-status callback for OUTBOUND messages. Twilio POSTs
    MessageSid + MessageStatus (sent/delivered/undelivered/failed) as the
    message moves through the carrier. Signature-verified like inbound; we
    stamp the matching conversation_messages row. Always returns 200 so
    Twilio doesn't retry-storm on a status we don't track."""
    form_multi = await request.form()
    form = {k: (str(v) if v is not None else "") for k, v in form_multi.items()}

    if settings.INBOUND_SMS_REQUIRE_SIGNATURE:
        if not settings.TWILIO_AUTH_TOKEN:
            log.error("delivery_status: TWILIO_AUTH_TOKEN unset; cannot verify")
            raise HTTPException(
                status_code=503, detail={"code": "sms_not_configured"}
            )
        if not verify_signature(
            settings.TWILIO_AUTH_TOKEN,
            _public_url(request),
            form,
            request.headers.get("X-Twilio-Signature"),
        ):
            raise HTTPException(
                status_code=403, detail={"code": "invalid_signature"}
            )

    message_sid = form.get("MessageSid") or form.get("SmsSid")
    message_status = form.get("MessageStatus") or form.get("SmsStatus")
    if not message_sid or not message_status:
        raise HTTPException(status_code=400, detail={"code": "malformed_webhook"})

    inbox_service.apply_delivery_status(
        db,
        message_sid=message_sid,
        status=message_status,
        error_code=form.get("ErrorCode") or None,
    )
    db.commit()
    return Response(status_code=204)


@router.post("/voice/bridge")
async def voice_bridge(request: Request) -> Response:
    """TwiML callback for the click-to-call bridge (business-number call path).

    Twilio requests this when the REP answers the first leg; the response tells
    Twilio to dial the CONTACT, presenting the business voice number as caller
    ID. The contact number is NOT read from the request — it is carried inside
    the ``token`` query param, a signed short-lived JWT minted when the bridge
    was started. The route dials only the number that token authorizes, so it
    can never be coerced into dialing an arbitrary number:

      1. Twilio signature verified against the public URL (when required).
      2. ``token`` decoded + validated (signature, expiry, purpose); the
         authorized ``dial`` number comes out of the verified claims.
      3. Reply with ``<Dial callerId=BUSINESS><Number>CONTACT</Number></Dial>``.

    Any failure returns an empty ``<Response/>`` (Twilio hangs up cleanly)
    rather than an error — a forged or expired token simply results in no call,
    never a dial to an attacker-chosen number.
    """
    form_multi = await request.form()
    form = {k: (str(v) if v is not None else "") for k, v in form_multi.items()}

    if settings.INBOUND_SMS_REQUIRE_SIGNATURE:
        if not settings.TWILIO_AUTH_TOKEN or not verify_signature(
            settings.TWILIO_AUTH_TOKEN,
            _public_url(request),
            form,
            request.headers.get("X-Twilio-Signature"),
        ):
            log.warning("voice_bridge: signature verification failed")
            return _twiml()

    token = request.query_params.get("token") or ""
    try:
        claims = voice_transport.verify_bridge_token(token)
    except voice_transport.InvalidBridgeToken:
        # Forged/expired/replayed token → dial nothing (empty TwiML hangs up).
        log.warning("voice_bridge: invalid or expired bridge token")
        return _twiml()

    twiml = voice_transport.build_bridge_twiml(dial_number=claims["dial"])
    return Response(content=twiml, media_type="application/xml")
