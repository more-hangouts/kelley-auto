"""Inbound voice routing + logging (phase 1: forward to the office line).

Every call that reaches the business Twilio number is recorded in
``inbound_calls`` and then routed. Phase 1 routing is deliberately the simplest
thing that gives the shop a working phone: dial the published office number,
and if nobody picks up, say something human instead of dropping the line.

Two invariants worth stating, because both are easy to get wrong:

  * LOG FIRST, ROUTE SECOND. The row is written before any TwiML is returned,
    so a call that rings out, fails, or arrives while the feature flag is off is
    logged exactly like one that connects. Missed calls are the ones the shop
    most needs to see, so they cannot be the ones we fail to record.
  * NEVER RAISE INTO THE WEBHOOK. A logging failure must not cost the caller
    their call — routing TwiML is still returned if the insert blows up. A
    dropped log row is an annoyance; a dropped customer call is lost business.

Caller ID on the forwarded leg is deliberately NOT set: for an inbound call
Twilio defaults ``<Dial>`` to the original caller's number, so whoever answers
at the shop sees the CUSTOMER's number and can call them back from their own
handset. Overriding it with the business number would replace every caller's ID
with our own and destroy that.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from database.models import Contact, InboundCall
from modules.core.services.phone import normalize_phone_e164

log = logging.getLogger(__name__)

# Twilio DialCallStatus / CallStatus values mapped onto the migration-105
# allowlist. Twilio spells these with hyphens; the column uses underscores.
_STATUS_MAP = {
    "queued": "ringing",
    "initiated": "ringing",
    "ringing": "ringing",
    "in-progress": "in_progress",
    "answered": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
}


def inbound_configured() -> bool:
    """True when inbound calls can actually be forwarded: the flag is on and a
    destination number is set. Checked before routing so a half-configured
    deploy politely declines instead of dialing nowhere."""
    return bool(
        settings.TWILIO_INBOUND_VOICE_ENABLED
        and settings.TWILIO_INBOUND_FORWARD_NUMBER
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def match_contact_id(db: Session, from_number: str) -> int | None:
    """Best-effort link from the caller's number to a known contact.

    Matches on the normalized E.164 form against both ``phone_e164`` and the
    raw ``phone`` column, since imported CRM rows predate normalization. Returns
    the OLDEST match so a caller with duplicate contact rows lands consistently
    on the same one rather than flapping between them.
    """
    normalized = normalize_phone_e164(from_number or "")
    if not normalized:
        return None
    row = (
        db.query(Contact.id)
        .filter(
            (Contact.phone_e164 == normalized) | (Contact.phone == normalized),
            Contact.deleted_at.is_(None),
        )
        .order_by(Contact.id.asc())
        .first()
    )
    return row[0] if row else None


def log_inbound_call(
    db: Session,
    *,
    call_sid: str,
    from_number: str,
    to_number: str,
    caller_city: str | None = None,
    caller_state: str | None = None,
) -> InboundCall | None:
    """Record an arriving call. Idempotent on ``provider_call_sid`` so a Twilio
    webhook retry returns the existing row instead of duplicating the call.

    Returns ``None`` if the row could not be written — the caller routes anyway.
    """
    existing = (
        db.query(InboundCall)
        .filter(InboundCall.provider_call_sid == call_sid)
        .one_or_none()
    )
    if existing is not None:
        return existing

    call = InboundCall(
        provider_call_sid=call_sid,
        from_number=from_number,
        to_number=to_number,
        contact_id=match_contact_id(db, from_number),
        status="received",
        caller_city=caller_city,
        caller_state=caller_state,
    )
    db.add(call)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent retry won the insert race — adopt its row.
        db.rollback()
        return (
            db.query(InboundCall)
            .filter(InboundCall.provider_call_sid == call_sid)
            .one_or_none()
        )
    return call


def record_status(
    db: Session,
    *,
    call_sid: str,
    provider_status: str | None,
    duration_seconds: int | None = None,
) -> InboundCall | None:
    """Apply a Twilio status callback to an existing inbound call.

    Unknown statuses are ignored rather than written, so a future Twilio
    vocabulary addition can never violate the migration-105 CHECK constraint and
    500 the callback.
    """
    call = (
        db.query(InboundCall)
        .filter(InboundCall.provider_call_sid == call_sid)
        .one_or_none()
    )
    if call is None:
        return None

    mapped = _STATUS_MAP.get((provider_status or "").strip().lower())
    if mapped:
        call.status = mapped
    if duration_seconds is not None:
        call.duration_seconds = duration_seconds
    return call


def build_forward_twiml(*, forward_to: str) -> str:
    """Ring the office line, then apologize if nobody answers.

    ``action`` is intentionally omitted: with no action URL Twilio falls through
    to the verbs AFTER ``<Dial>`` when the leg ends unanswered, which is exactly
    the "we missed you" message. Adding an action would swallow that fallthrough.

    No ``callerId`` — see the module docstring: the default passes the CUSTOMER's
    number through to whoever answers.
    """
    message = _xml_escape(settings.TWILIO_INBOUND_UNAVAILABLE_MESSAGE)
    timeout = int(settings.TWILIO_INBOUND_FORWARD_TIMEOUT_SECONDS)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Dial timeout="{timeout}">'
        f"<Number>{_xml_escape(forward_to)}</Number>"
        "</Dial>"
        f'<Say voice="alice">{message}</Say>'
        "</Response>"
    )


def build_unavailable_twiml() -> str:
    """Spoken when inbound routing is off or misconfigured. A caller hears a
    real sentence instead of Twilio's default error tone — which is what an
    unset ``voice_url`` gives you today."""
    message = _xml_escape(settings.TWILIO_INBOUND_UNAVAILABLE_MESSAGE)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say voice="alice">{message}</Say>'
        "</Response>"
    )
