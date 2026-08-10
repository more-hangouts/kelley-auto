"""Public Twilio webhook endpoints (Omnichannel Inbox Plan Part 4; Phase 2).

Inbound SMS only. Outbound status callbacks arrive in Phase 3 with the send
path. The endpoint is public and unauthenticated by design — Twilio's request
signature is the auth. Flow, in order:

  1. Verify ``X-Twilio-Signature`` against the **public** URL (built from
     ``PUBLIC_API_BASE_URL`` + path/query — behind Caddy the app sees the
     internal 127.0.0.1 URL, which would fail verification).
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
from database.models import InboundCall
from modules.messaging.services import (
    inbound_voice,
    inbox_service,
    voice_routing,
    voice_transport,
    webhook_ingest,
)
from modules.messaging.services.twilio_signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter()

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml() -> Response:
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


def _public_url(request: Request) -> str:
    url = settings.PUBLIC_API_BASE_URL.rstrip("/") + request.url.path
    query = request.scope.get("query_string", b"").decode("ascii", errors="ignore")
    return f"{url}?{query}" if query else url


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


@router.post("/voice/inbound")
async def voice_inbound(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Public entry point for calls ARRIVING at the business number.

    This is the URL the Twilio number's ``voice_url`` points at. Order matters:

      1. Verify the Twilio signature (when required) — an unsigned request is
         not a real call and gets no routing.
      2. LOG the call before routing, so a call that rings out, fails, or lands
         while the feature flag is off is recorded exactly like one that
         connects. Missed calls are precisely what the shop needs to see.
      3. Return routing TwiML: forward to the office line, or — when inbound is
         off/misconfigured — SAY an apology. Either way the caller hears a
         human sentence rather than a carrier error tone.

    A logging failure never costs the caller their call: the insert is wrapped
    so routing still happens. A lost log row is an annoyance; a lost customer
    call is lost business.
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
            log.warning("voice_inbound: signature verification failed")
            return _twiml()

    call_sid = form.get("CallSid") or ""
    from_number = form.get("From") or ""
    to_number = form.get("To") or ""

    forward_to = settings.TWILIO_INBOUND_FORWARD_NUMBER
    routed = inbound_voice.inbound_configured()

    if call_sid:
        try:
            call = inbound_voice.log_inbound_call(
                db,
                call_sid=call_sid,
                from_number=from_number,
                to_number=to_number,
                caller_city=form.get("FromCity") or None,
                caller_state=form.get("FromState") or None,
            )
            if call is not None:
                call.disposition = "forwarded" if routed else "rejected"
                call.forwarded_to = forward_to if routed else None
            db.commit()
        except Exception:  # noqa: BLE001 — routing must survive any log failure
            db.rollback()
            log.exception("voice_inbound: failed to log call sid=%s", call_sid)
    else:
        log.warning("voice_inbound: request carried no CallSid")

    if not routed:
        log.info("voice_inbound: inbound routing disabled — declining politely")
        return Response(
            content=inbound_voice.build_unavailable_twiml(),
            media_type="application/xml",
        )

    # --- Phase 2 routing decision -----------------------------------------
    # Ring dashboard softphones first; fall back to a configured number only
    # when nobody is online or nobody picks up.
    cfg = voice_routing.get_settings(db)
    mode = cfg.inbound_mode
    fallback = cfg.fallback_number or forward_to
    identities = (
        voice_routing.online_identities(db)
        if mode in ("browser_then_fallback", "browser_only")
        else []
    )

    if identities and call_sid:
        room = voice_routing.conference_name_for(call_sid)
        # Ring the browsers BEFORE answering the caller into the conference, so
        # the rep's phone is already ringing by the time the caller stops
        # hearing ringback. Legs Twilio refuses are not counted, so the
        # fallback never waits on a leg that was never placed.
        started = voice_routing.ring_browsers(
            conference_name=room,
            identities=identities,
            timeout=cfg.ring_timeout_seconds,
        )
        if started:
            try:
                if call is not None:
                    call.conference_name = room
                    call.rep_legs_total = started
                    call.disposition = "browser"
                db.commit()
            except Exception:  # noqa: BLE001 — routing survives a log failure
                db.rollback()
                log.exception("voice_inbound: failed to record conference state")
            log.info(
                "voice_inbound: ringing %s browser client(s) sid=%s", started, call_sid
            )
            return Response(
                content=voice_routing.build_conference_twiml(conference_name=room),
                media_type="application/xml",
            )
        # Every browser leg was refused — treat exactly like nobody online.
        log.warning("voice_inbound: no browser legs accepted; falling back")

    if mode == "browser_only" or not fallback:
        log.info("voice_inbound: no browser available and no fallback — message")
        return Response(
            content=inbound_voice.build_unavailable_twiml(),
            media_type="application/xml",
        )

    log.info("voice_inbound: forwarding sid=%s from=%s to fallback", call_sid, from_number)
    return Response(
        content=inbound_voice.build_forward_twiml(forward_to=fallback),
        media_type="application/xml",
    )


@router.post("/voice/conference/join")
async def voice_conference_join(request: Request) -> Response:
    """TwiML for a leg joining an answered call's conference.

    Requested by Twilio when a rep's browser answers, or when the fallback
    number picks up. Returns the join TwiML for the conference named in the
    query string; the name is a ``kap-call-<CallSid>`` room, not a secret, and
    the Twilio signature is what authenticates the request.
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
            log.warning("voice_conference_join: signature verification failed")
            return _twiml()

    room = request.query_params.get("conference") or ""
    if not room.startswith("kap-call-"):
        log.warning("voice_conference_join: refused conference name %r", room)
        return _twiml()

    return Response(
        content=voice_routing.build_join_twiml(conference_name=room),
        media_type="application/xml",
    )


@router.post("/voice/conference/rep-status")
async def voice_rep_status(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Outcome of ONE browser ring leg.

    When every rung browser has reported without joining, this starts the PSTN
    fallback — once, no matter how many callbacks land simultaneously. Counting
    all legs rather than reacting to the first decline is what stops one rep's
    "no thanks" from pulling the caller off another rep's ringing phone.
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
            log.warning("voice_rep_status: signature verification failed")
            return Response(status_code=204)

    room = request.query_params.get("conference") or ""
    call = (
        db.query(InboundCall).filter(InboundCall.conference_name == room).one_or_none()
        if room
        else None
    )
    if call is None:
        return Response(status_code=204)

    # A leg that actually connected means a rep answered; nothing to arbitrate.
    if (form.get("CallStatus") or "").lower() in ("in-progress", "answered"):
        return Response(status_code=204)

    try:
        should_fallback = voice_routing.claim_fallback(db, call=call)
        cfg = voice_routing.get_settings(db)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("voice_rep_status: arbitration failed room=%s", room)
        return Response(status_code=204)

    if not should_fallback:
        return Response(status_code=204)

    if cfg.inbound_mode == "browser_only" or not cfg.fallback_number:
        # No PSTN fallback configured; the caller stays on hold music until
        # they hang up. Deliberate: 'browser_only' means browser or nothing.
        log.info("voice_rep_status: browsers declined, no fallback configured")
        return Response(status_code=204)

    log.info("voice_rep_status: browsers declined — ringing fallback for %s", room)
    voice_routing.ring_fallback(
        conference_name=room,
        number=cfg.fallback_number,
        timeout=cfg.fallback_timeout_seconds,
    )
    return Response(status_code=204)


@router.post("/voice/status")
async def voice_status_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Twilio call status callback for inbound calls.

    Updates the logged call with the provider-reported outcome (completed /
    busy / no-answer / failed) and the talk duration. Always 204s: a callback
    for a call we never logged, or an unknown status, is not an error worth
    making Twilio retry.
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
            log.warning("voice_status: signature verification failed")
            return Response(status_code=204)

    call_sid = form.get("CallSid") or ""
    if not call_sid:
        return Response(status_code=204)

    raw_duration = form.get("CallDuration") or form.get("DialCallDuration") or ""
    try:
        duration = int(raw_duration) if raw_duration else None
    except ValueError:
        duration = None

    try:
        inbound_voice.record_status(
            db,
            call_sid=call_sid,
            provider_status=(
                form.get("CallStatus") or form.get("DialCallStatus") or None
            ),
            duration_seconds=duration,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — a callback must never 500 back to Twilio
        db.rollback()
        log.exception("voice_status: failed to record status sid=%s", call_sid)

    return Response(status_code=204)


@router.post("/voice/outbound")
async def voice_outbound(request: Request) -> Response:
    """TwiML callback for a call placed from the dashboard softphone.

    Twilio requests this when a browser client connects to our TwiML App. The
    reply tells Twilio which number to dial, presenting the business caller ID.

    The destination is NOT read from the request. The browser sends a signed
    ``DialToken`` custom parameter, minted by
    ``POST /api/contacts/{id}/call-attempts/browser`` after that route
    authenticated the rep, resolved the contact's own number, and logged the
    attempt. This route dials only the number that token authorizes — mirroring
    ``/voice/bridge``, so neither voice path can be talked into dialing an
    arbitrary number (the toll-fraud shape this endpoint would otherwise have,
    since it is necessarily public).

    Any failure returns empty ``<Response/>`` — Twilio hangs up cleanly and no
    call is placed, rather than erroring or dialing something unverified.
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
            log.warning("voice_outbound: signature verification failed")
            return _twiml()

    # Custom params from Device.connect() arrive as ordinary form fields.
    try:
        claims = voice_transport.verify_dial_token(form.get("DialToken") or "")
    except voice_transport.InvalidBridgeToken:
        log.warning("voice_outbound: invalid or expired dial token")
        return _twiml()

    log.info(
        "voice_outbound: dialing for call_attempt=%s user=%s",
        claims.get("aid"),
        claims.get("uid"),
    )
    twiml = voice_transport.build_outbound_twiml(dial_number=claims["dial"])
    return Response(content=twiml, media_type="application/xml")


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
