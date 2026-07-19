"""Storefront web chat — a second inbound writer into the omnichannel inbox
(migrations 094 + 097; design ported from catering210).

Not a separate chat product: every visitor message lands in the same
``conversations`` / ``conversation_messages`` tables the SMS/Meta webhooks
write, with ``channel='web_chat'`` / ``provider='website'``, and staff answer
from the same Inbox tab. A web-chat reply needs NO transport — writing the
outbound row IS the delivery; the visitor's poll picks it up.

Behavior is deterministic (no AI): a versioned JSON question tree drives a
guided intake, options can fire canned answers, and anything the script can't
answer — an ``escalate`` option or any free text — hands off to a human and
links/mints a CRM deal exactly once (``conversations.event_id`` guard).

Identity: ``external_id`` is a server-minted ``wc_<uuid4hex>`` session id,
NOT the phone/email — phone/email can change or be missing mid-flow; the
generated id gives the browser a stable polling key and never collapses two
visitors into one thread. A returning contact within 24h reuses their open
thread (one owner alert per real visitor, not per page refresh).

Convention: services flush, routers own the commit.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Contact,
    Conversation,
    ConversationMessage,
    Event,
    WebChatScript,
)
from services import (
    booking_service,
    contact_service,
    event_service,
    inbox_service,
    storefront_analytics_service,
)
from services.event_service import EventOverrides
from services.event_workflow import all_statuses

log = logging.getLogger(__name__)

PROVIDER = "website"
CHANNEL = "web_chat"
_BUSINESS_REF = "web"
_REUSE_WINDOW = timedelta(hours=24)
_PRESENCE_THROTTLE = timedelta(seconds=30)

# Presence: the widget polls every 5s while open; a visitor stamped within
# this window is "still on the page", so a staff reply will reach them live.
VISITOR_ACTIVE_WINDOW = timedelta(seconds=90)

_VEHICLE_SALE = "vehicle_sale"

# The seeded question tree. An option carries at most one of:
#   "next": "<question_id>"  → advance to another question
#   "answer": "<answer key>" → fire the canned reply, then show the root again
#   "escalate": true         → hand off to a human (creates/links a CRM deal)
# Free text ALWAYS escalates — the scripted chat never fabricates an answer.
SEED_SCRIPT: dict[str, Any] = {
    "version": 1,
    "greeting": "Hi! I can help with inventory, financing, and trade-ins. What brings you in?",
    "handoff": (
        "Got it — you're in line with our team. Someone will reply here "
        "shortly (keep this tab open, or leave your number and we'll follow up)."
    ),
    "root": "start",
    "questions": [
        {
            "id": "start",
            "prompt": "What can we help you with?",
            "options": [
                {"id": "browse", "label": "I'm looking for a car", "next": "vehicle_type"},
                {"id": "financing", "label": "Financing / get approved", "answer": "financing"},
                {"id": "trade", "label": "I have a trade-in", "answer": "trade_in"},
                {"id": "hours", "label": "Hours & location", "answer": "hours"},
                {"id": "other", "label": "Something else", "escalate": True},
            ],
        },
        {
            "id": "vehicle_type",
            "prompt": "What type of vehicle are you after?",
            "options": [
                {"id": "suv", "label": "SUV", "answer": "inventory"},
                {"id": "truck", "label": "Truck / pickup", "answer": "inventory"},
                {"id": "sedan", "label": "Sedan", "answer": "inventory"},
                {"id": "any", "label": "Not sure yet — show me options", "answer": "inventory"},
            ],
        },
    ],
    "answers": {
        "financing": {
            "body": (
                "We're a buy-here-pay-here lot — no credit check, and most "
                "customers drive off with as low as $2,000 down. Tell us a "
                "little about what you're looking for and we'll get you "
                "approved, or start the application at "
                "kelleyautoplex.com/loan-application."
            )
        },
        "trade_in": {
            "body": (
                "We take trade-ins! Bring the vehicle by for a quick "
                "appraisal, or tell us the year, make, model, and mileage "
                "here and we'll give you a ballpark."
            )
        },
        "hours": {
            "body": (
                "You can find us at 4222 San Pedro Ave, San Antonio, TX "
                "78212 — call or text (210) 251-3644. Browse the full lot "
                "any time at kelleyautoplex.com/inventory."
            )
        },
        "inventory": {
            "body": (
                "Everything on the lot is listed with photos and pricing at "
                "kelleyautoplex.com/inventory. See something you like? Tell "
                "me here and our team will have it ready for a test drive."
            )
        },
    },
}


class WebChatError(Exception):
    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mint_session_id() -> str:
    return f"wc_{uuid.uuid4().hex}"


# ─── Script management ──────────────────────────────────────────────────────


def validate_script(script: dict) -> list[str]:
    """Structural validation for an owner-edited script. Returns a list of
    problems (empty = valid)."""
    errors: list[str] = []
    if not isinstance(script, dict):
        return ["script must be an object"]
    questions = script.get("questions")
    answers = script.get("answers") or {}
    if not isinstance(questions, list) or not questions:
        errors.append("questions must be a non-empty list")
        return errors
    ids: set[str] = set()
    for q in questions:
        qid = q.get("id")
        if not qid or not isinstance(qid, str):
            errors.append("every question needs a string id")
            continue
        if qid in ids:
            errors.append(f"duplicate question id: {qid}")
        ids.add(qid)
        prompt = q.get("prompt")
        if not prompt or len(str(prompt)) > 300:
            errors.append(f"{qid}: prompt required, max 300 chars")
        options = q.get("options")
        if not isinstance(options, list) or not (2 <= len(options) <= 8):
            errors.append(f"{qid}: 2-8 options required")
            continue
        for opt in options:
            if not opt.get("id") or not opt.get("label"):
                errors.append(f"{qid}: every option needs id + label")
            if len(str(opt.get("label", ""))) > 80:
                errors.append(f"{qid}: option labels max 80 chars")
            answer_key = opt.get("answer")
            if answer_key and answer_key not in answers:
                errors.append(f"{qid}: unknown answer key {answer_key!r}")
    root = script.get("root")
    if root and root not in ids:
        errors.append(f"root {root!r} is not a question id")
    for q in questions:
        for opt in q.get("options") or []:
            nxt = opt.get("next")
            if nxt and nxt not in ids:
                errors.append(f"{q.get('id')}: next {nxt!r} is not a question id")
    for key, ans in answers.items():
        body = (ans or {}).get("body")
        if not body or len(str(body)) > 1000:
            errors.append(f"answer {key!r}: body required, max 1000 chars")
    return errors


def get_active_script(db: Session) -> dict:
    """Latest saved script, or the seeded fallback when none was ever saved."""
    row = db.execute(
        select(WebChatScript).order_by(WebChatScript.version.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return SEED_SCRIPT
    script = dict(row.script)
    script["version"] = row.version
    return script


def save_script(db: Session, script: dict, *, user_id: int | None) -> int:
    """Append-only save: the new script becomes ``version = max + 1``."""
    errors = validate_script(script)
    if errors:
        raise WebChatError("invalid_script", http_status=422)
    current = db.execute(
        select(WebChatScript.version).order_by(WebChatScript.version.desc()).limit(1)
    ).scalar_one_or_none()
    version = (current or SEED_SCRIPT["version"]) + 1
    body = {k: v for k, v in script.items() if k != "version"}
    db.add(WebChatScript(version=version, script=body, created_by_user_id=user_id))
    db.flush()
    return version


# ─── Conversation helpers ───────────────────────────────────────────────────


def _get_conversation(db: Session, session_id: str) -> Conversation:
    conv = db.execute(
        select(Conversation).where(
            Conversation.provider == PROVIDER,
            Conversation.channel == CHANNEL,
            Conversation.external_id == session_id,
        )
    ).scalar_one_or_none()
    if conv is None:
        raise WebChatError("session_not_found", http_status=404)
    return conv


def _serialize_message(m: ConversationMessage) -> dict:
    kind = "visitor"
    if m.direction == "outbound":
        kind = (
            "auto"
            if (m.provider_message_id or "").startswith("wcauto:")
            else "staff"
        )
    return {
        "id": m.id,
        "direction": m.direction,
        "kind": kind,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _messages_after(
    db: Session, conv: Conversation, after_id: int = 0
) -> list[dict]:
    rows = (
        db.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conv.id,
                ConversationMessage.id > after_id,
            )
            .order_by(ConversationMessage.id.asc())
        )
        .scalars()
        .all()
    )
    return [_serialize_message(m) for m in rows]


def _write_visitor_message(
    db: Session, conv: Conversation, body: str
) -> ConversationMessage:
    msg, _conv, _created = inbox_service.record_inbound_message(
        db,
        provider=PROVIDER,
        channel=CHANNEL,
        external_id=conv.external_id,
        business_ref=_BUSINESS_REF,
        message_id=f"wc:{conv.external_id}:{uuid.uuid4().hex[:12]}",
        body=body,
    )
    return msg


def _write_auto_reply(
    db: Session, conv: Conversation, body: str
) -> ConversationMessage:
    """Canned scripted reply. Outbound + already 'sent' — the row itself is
    the delivery (the widget's poll reads it). Tagged ``wcauto:`` so the UI
    can distinguish scripted replies from a human's."""
    now = _now()
    msg = ConversationMessage(
        conversation_id=conv.id,
        direction="outbound",
        channel=CHANNEL,
        provider=PROVIDER,
        sender_ref=_BUSINESS_REF,
        recipient_ref=conv.external_id,
        body=body,
        status="sent",
        provider_message_id=f"wcauto:{conv.external_id}:{uuid.uuid4().hex[:12]}",
        created_at=now,
        sent_at=now,
    )
    db.add(msg)
    conv.last_message_at = now
    conv.last_outbound_at = now
    conv.updated_at = now
    db.flush()
    return msg


def _escalate(db: Session, conv: Conversation) -> bool:
    """Hand the thread to a human: link (or mint) the CRM deal, exactly once.
    Returns True when this call was the first escalation."""
    if conv.event_id is not None:
        return False
    if conv.contact_id is None:  # web chat always sets it, but stay safe
        return False

    open_statuses = {s.code for s in all_statuses(_VEHICLE_SALE) if not s.is_terminal}
    existing = (
        db.execute(
            select(Event)
            .where(
                Event.event_type == _VEHICLE_SALE,
                Event.primary_contact_id == conv.contact_id,
                Event.deleted_at.is_(None),
                Event.status.in_(open_statuses),
            )
            .order_by(Event.id.desc())
        )
        .scalars()
        .first()
    )
    if existing is not None:
        conv.event_id = existing.id
    else:
        contact = db.get(Contact, conv.contact_id)
        name = contact.display_name if contact is not None else None
        event = event_service.create_walk_in_event(
            db,
            contact_id=conv.contact_id,
            event_type=_VEHICLE_SALE,
            overrides=EventOverrides(
                event_name=(f"Web chat — {name}".strip(" —") or None),
                notes="Escalated from website chat.",
            ),
            actor_user_id=None,
        )
        conv.event_id = event.id
    db.flush()
    storefront_analytics_service.record_milestone(
        db,
        event_name="chat_escalated",
        crm_event_id=conv.event_id,
        metadata={"conversation_id": conv.id},
    )
    return True


# ─── Public flow ────────────────────────────────────────────────────────────


def start_chat(
    db: Session,
    *,
    name: str | None,
    phone: str | None,
    email: str | None,
    sms_opt_in: bool = False,
    page_url: str | None = None,
    intake: list[dict] | None = None,
    script_version: int | None = None,
) -> dict:
    """Contact capture → conversation + intake block.

    Requires at least one usable contact key (normalizable phone or an
    email). A returning contact reuses their non-resolved thread from the
    last 24h — ``created=False`` — so refresh/double-submit can't spam staff
    with duplicate alerts. Flushes only; the router commits."""
    raw_phone = (phone or "").strip() or None
    phone_e164 = booking_service.normalize_phone_e164(raw_phone) if raw_phone else None
    clean_email = (email or "").strip().lower() or None
    if not phone_e164 and not clean_email:
        raise WebChatError("missing_contact_info", http_status=422)

    first, _, rest = (name or "").strip().partition(" ")
    contact, _was_new = contact_service.find_or_create_contact(
        db,
        phone_e164=phone_e164,
        email=clean_email,
        phone=raw_phone,
        first_name=first or None,
        last_name=rest.strip() or None,
    )

    # A2P consent, mirroring the lead form: first consent wins, STOP stands.
    if sms_opt_in and contact.sms_consent_at is None:
        contact.sms_consent_at = _now()
        contact.sms_consent_source = f"web_chat:{(page_url or 'unknown')}"[:200]

    reuse_cutoff = _now() - _REUSE_WINDOW
    conv = (
        db.execute(
            select(Conversation)
            .where(
                Conversation.provider == PROVIDER,
                Conversation.channel == CHANNEL,
                Conversation.contact_id == contact.id,
                Conversation.status != "resolved",
                Conversation.last_message_at >= reuse_cutoff,
            )
            .order_by(Conversation.last_message_at.desc())
        )
        .scalars()
        .first()
    )
    created = conv is None
    if created:
        conv, _ = inbox_service.upsert_conversation(
            db,
            provider=PROVIDER,
            channel=CHANNEL,
            external_id=mint_session_id(),
            business_ref=_BUSINESS_REF,
        )
        conv.contact_id = contact.id

    now = _now()
    conv.visitor_last_seen_at = now
    conv.visitor_page_url = page_url
    if sms_opt_in:
        conv.visitor_sms_opt_in = True

    lines = []
    if created:
        lines.append("Started a chat on the website.")
    else:
        lines.append("Came back to the website chat.")
    if page_url:
        lines.append(f"Viewing: {page_url}")
    for step in intake or []:
        q = str(step.get("question") or "").strip()[:300]
        a = str(step.get("answer") or "").strip()[:120]
        if q and a:
            lines.append(f"{q} → {a}")
    if script_version:
        lines.append(f"Script: v{script_version}")
    msg = _write_visitor_message(db, conv, "\n".join(lines))

    if created:
        inbox_service.notify_inbound(db, conv, msg)

    return {
        "session_id": conv.external_id,
        "created": created,
        "messages": _messages_after(db, conv, 0),
    }


def record_answer(
    db: Session, *, session_id: str, question_id: str, option_id: str
) -> dict:
    """One scripted tap → an inbox line, plus the canned reply / escalation
    the option carries. Unknown question/option ids are rejected — the
    client can only replay the server's own script."""
    conv = _get_conversation(db, session_id)
    script = get_active_script(db)
    question = next(
        (q for q in script.get("questions", []) if q.get("id") == question_id), None
    )
    option = None
    if question is not None:
        option = next(
            (o for o in question.get("options", []) if o.get("id") == option_id),
            None,
        )
    if question is None or option is None:
        raise WebChatError("invalid_option", http_status=422)

    before_id = _last_message_id(db, conv)
    _write_visitor_message(
        db, conv, f"{question.get('prompt')}\n→ {option.get('label')}"
    )

    answer_key = option.get("answer")
    if answer_key:
        answer = (script.get("answers") or {}).get(answer_key) or {}
        if answer.get("body"):
            _write_auto_reply(db, conv, str(answer["body"]))
    if option.get("escalate"):
        if _escalate(db, conv):
            _write_auto_reply(db, conv, str(script.get("handoff") or "One moment —"))

    return {"messages": _messages_after(db, conv, before_id)}


def record_visitor_message(db: Session, *, session_id: str, body: str) -> dict:
    """Free text → escalates to a person. The scripted chat never fabricates
    an answer for text it doesn't understand."""
    conv = _get_conversation(db, session_id)
    text = (body or "").strip()
    if not text:
        raise WebChatError("empty_message", http_status=422)

    before_id = _last_message_id(db, conv)
    msg = _write_visitor_message(db, conv, text[:2000])
    first_escalation = _escalate(db, conv)
    if first_escalation:
        script = get_active_script(db)
        _write_auto_reply(db, conv, str(script.get("handoff") or "One moment —"))
    # A human is now waiting — alert staff (routing dedups per subscriber
    # channel; scripted taps deliberately never notify).
    inbox_service.notify_inbound(db, conv, msg)

    return {"messages": _messages_after(db, conv, before_id)}


def poll_messages(
    db: Session,
    *,
    session_id: str,
    after_id: int = 0,
    page_url: str | None = None,
) -> dict:
    """Cursor poll: rows with ``id > after_id`` only — the client never
    re-fetches the transcript it already has. Doubles as the presence
    heartbeat (throttled to one write per 30s)."""
    conv = _get_conversation(db, session_id)
    now = _now()
    if (
        conv.visitor_last_seen_at is None
        or now - conv.visitor_last_seen_at > _PRESENCE_THROTTLE
    ):
        conv.visitor_last_seen_at = now
        if page_url:
            conv.visitor_page_url = page_url
        db.flush()
    return {"messages": _messages_after(db, conv, after_id)}


def _last_message_id(db: Session, conv: Conversation) -> int:
    from sqlalchemy import func

    return int(
        db.execute(
            select(func.coalesce(func.max(ConversationMessage.id), 0)).where(
                ConversationMessage.conversation_id == conv.id
            )
        ).scalar_one()
    )
