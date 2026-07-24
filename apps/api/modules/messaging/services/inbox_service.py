"""Omnichannel inbox service (Phase 2).

The business logic behind the CRM inbox: turning an inbound Twilio SMS into a
threaded conversation + message, resolving per-user unread state, and firing
the staff notification. Routers (webhook + admin) stay thin and call here.

Design notes:

  - **Idempotent inbound.** Dedup on ``(provider, provider_message_id)`` so a
    Twilio retry of the same MessageSid never double-inserts. This mirrors the
    ``webhook_events`` raw-store dedup — belt and suspenders.

  - **Race-safe threading.** ``upsert_conversation`` attempts the insert inside
    a SAVEPOINT; a concurrent inbound that wins the ``uq_conversations_identity``
    unique index makes the loser roll back *only the savepoint* and re-fetch,
    never disturbing the outer transaction (which also holds the raw-webhook
    row). No forked threads.

  - **Reopen on inbound.** A new inbound message flips a ``resolved`` thread
    back to ``open`` — a customer replying to a closed conversation is, by
    definition, not done.

  - **Opt-out is recorded even inbound-only.** STOP lands an opt-out on the
    linked contact (and always on the conversation metadata) the moment it
    arrives, so the Phase 3 outbound guard already has the data.

  - **Contact matching, not creation.** An inbound number is matched to an
    existing contact (newest non-deleted with that E.164); unknown numbers
    stay unlinked for staff triage rather than minting junk contacts against
    the imported roster.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import ADMIN_BASE_URL
from database.models import (
    Contact,
    Conversation,
    ConversationMessage,
    ConversationRead,
    Event,
)
from modules.core.services import business_time, notification_routing
from modules.core.services.phone import normalize_phone_e164

log = logging.getLogger(__name__)

# Twilio/CTIA standard opt-out + opt-in keywords (case-insensitive, exact).
STOP_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})
START_KEYWORDS = frozenset({"START", "YES", "UNSTOP"})

_CHANNEL_LABELS = {
    "sms": "SMS",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "web_chat": "Web chat",
}

# Mirrors the widget's 5s open-panel poll: a presence heartbeat within this
# window means the visitor is still on the page (see web_chat_service).
_WEB_CHAT_ACTIVE_WINDOW = timedelta(seconds=90)


def web_chat_active_window() -> timedelta:
    return _WEB_CHAT_ACTIVE_WINDOW


def in_sms_quiet_hours(now: datetime | None = None) -> bool:
    """True if the shop-local hour falls in the outbound SMS quiet window.
    Handles the normal overnight case (21→08 wraps midnight) and a same-day
    window; START == END disables it."""
    from config.settings import SMS_QUIET_HOURS_END, SMS_QUIET_HOURS_START

    start, end = SMS_QUIET_HOURS_START, SMS_QUIET_HOURS_END
    if start == end:
        return False
    local = business_time.to_business_local(now) if now else business_time.business_now()
    hour = local.hour
    if start > end:  # wraps midnight, e.g. 21..08
        return hour >= start or hour < end
    return start <= hour < end

PREVIEW_LEN = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Contact matching ───────────────────────────────────────────────────────


def match_contact_by_phone(db: Session, e164: str | None) -> Contact | None:
    """Newest non-deleted contact with this E.164. Duplicate phones are
    plausible (244 scraped contacts) so we pick deterministically rather than
    erroring; ambiguity is flagged on the conversation metadata by the caller.
    """
    if not e164:
        return None
    return (
        db.query(Contact)
        .filter(Contact.phone_e164 == e164, Contact.deleted_at.is_(None))
        .order_by(Contact.updated_at.desc(), Contact.id.desc())
        .first()
    )


def _recent_event_for_contact(db: Session, contact_id: int) -> Event | None:
    return (
        db.query(Event)
        .filter(Event.primary_contact_id == contact_id, Event.deleted_at.is_(None))
        .order_by(Event.status_changed_at.desc().nullslast(), Event.id.desc())
        .first()
    )


# ─── Conversation upsert ────────────────────────────────────────────────────


def upsert_conversation(
    db: Session,
    *,
    provider: str,
    channel: str,
    external_id: str,
    business_ref: str | None = None,
) -> tuple[Conversation, bool]:
    """Get-or-create the thread for (provider, channel, external_id). Returns
    ``(conversation, created)``. Race-safe via a savepoint so a losing
    concurrent insert re-fetches without poisoning the outer transaction.
    """
    existing = (
        db.query(Conversation)
        .filter_by(provider=provider, channel=channel, external_id=external_id)
        .first()
    )
    if existing is not None:
        return existing, False

    savepoint = db.begin_nested()
    conv = Conversation(
        provider=provider,
        channel=channel,
        external_id=external_id,
        business_ref=business_ref,
        status="open",
    )
    db.add(conv)
    try:
        savepoint.commit()  # emits the INSERT within the savepoint
        return conv, True
    except IntegrityError:
        savepoint.rollback()
        conv = (
            db.query(Conversation)
            .filter_by(provider=provider, channel=channel, external_id=external_id)
            .one()
        )
        return conv, False


# ─── Inbound (channel-agnostic core) ────────────────────────────────────────


def record_inbound_message(
    db: Session,
    *,
    provider: str,
    channel: str,
    external_id: str,
    business_ref: str,
    message_id: str,
    body: str | None,
    media: list | None = None,
    is_echo: bool = False,
    display_name: str | None = None,
) -> tuple[ConversationMessage, Conversation, bool]:
    """Idempotently record a message on any channel. Returns
    ``(message, conversation, created)``; ``created`` is False when
    ``(provider, message_id)`` was already stored (a webhook retry, or an echo
    of our own send). Does NOT commit — the webhook owns the transaction.

    ``is_echo=True`` marks a message the business sent from outside the CRM
    (e.g. the Facebook Page inbox); it lands as an outbound row so the thread
    stays complete.
    """
    if message_id:
        dupe = (
            db.query(ConversationMessage)
            .filter_by(provider=provider, provider_message_id=message_id)
            .first()
        )
        if dupe is not None:
            return dupe, db.get(Conversation, dupe.conversation_id), False

    conv, _created = upsert_conversation(
        db,
        provider=provider,
        channel=channel,
        external_id=external_id,
        business_ref=business_ref,
    )

    # Stash a display name for triage while the thread is unlinked.
    if display_name and conv.contact_id is None:
        meta = dict(conv.conversation_metadata or {})
        if meta.get("display_name") != display_name:
            meta["display_name"] = display_name
            conv.conversation_metadata = meta

    now = _now()
    if is_echo:
        direction, status = "outbound", "sent"
        sender_ref, recipient_ref = business_ref, external_id
    else:
        direction, status = "inbound", "received"
        sender_ref, recipient_ref = external_id, business_ref

    msg = ConversationMessage(
        conversation_id=conv.id,
        direction=direction,
        channel=channel,
        provider=provider,
        sender_ref=sender_ref,
        recipient_ref=recipient_ref,
        body=body,
        media=media or [],
        status=status,
        provider_message_id=message_id,
        is_echo=is_echo,
        created_at=now,
    )
    db.add(msg)

    conv.last_message_at = now
    if is_echo:
        conv.last_outbound_at = now
    else:
        # Reopen: an inbound reply means the thread needs attention again,
        # whether it was resolved or parked as answered/pending.
        if conv.status in ("resolved", "pending"):
            conv.status = "open"
        conv.last_inbound_at = now
        conv.last_inbound_preview = (body or "").strip()[:PREVIEW_LEN]
    conv.updated_at = now
    db.flush()
    return msg, conv, True


def record_inbound_sms(
    db: Session,
    *,
    message_sid: str,
    from_number: str,
    to_number: str,
    body: str | None,
    media: list | None = None,
) -> tuple[ConversationMessage, Conversation, bool]:
    """Twilio SMS: normalize phones, record via the generic core, then apply
    the SMS-specific extras — contact match by phone_e164 and STOP/START
    opt-out keywords. Idempotent on MessageSid. Does NOT commit.
    """
    from_e164 = normalize_phone_e164(from_number) or from_number
    to_e164 = normalize_phone_e164(to_number) or to_number

    msg, conv, created = record_inbound_message(
        db,
        provider="twilio",
        channel="sms",
        external_id=from_e164,
        business_ref=to_e164,
        message_id=message_sid,
        body=body,
        media=media,
    )
    if not created:
        return msg, conv, False

    # Link to a known contact (and its most recent deal) if still unlinked.
    if conv.contact_id is None:
        contact = match_contact_by_phone(db, from_e164)
        if contact is not None:
            conv.contact_id = contact.id
            if conv.event_id is None:
                ev = _recent_event_for_contact(db, contact.id)
                if ev is not None:
                    conv.event_id = ev.id

    keyword = (body or "").strip().upper()
    if keyword in STOP_KEYWORDS:
        _apply_opt_out(db, conv, source="sms_keyword")
    elif keyword in START_KEYWORDS:
        _apply_opt_in(db, conv)

    db.flush()
    return msg, conv, True


# Twilio delivery-status → our message status. queued/sending are transient
# and don't downgrade a row we already marked sent.
_DELIVERY_STATUS_MAP = {
    "sent": "sent",
    "delivered": "delivered",
    "undelivered": "failed",
    "failed": "failed",
}


def apply_delivery_status(
    db: Session,
    *,
    message_sid: str,
    status: str,
    error_code: str | None = None,
) -> bool:
    """Update an outbound message from a Twilio status callback. Idempotent
    and monotonic — a late ``sent`` never clobbers a ``delivered``. Returns
    True if a row was found and touched. Does NOT commit."""
    mapped = _DELIVERY_STATUS_MAP.get((status or "").strip().lower())
    if mapped is None:
        return False
    msg = (
        db.query(ConversationMessage)
        .filter_by(provider="twilio", provider_message_id=message_sid)
        .first()
    )
    if msg is None:
        return False
    # Never downgrade a terminal-good state.
    if msg.status == "delivered" and mapped != "delivered":
        return True
    now = _now()
    msg.status = mapped
    if mapped == "delivered":
        msg.delivered_at = now
    elif mapped == "failed":
        msg.failed_at = now
        if error_code:
            msg.provider_error_code = str(error_code)
    elif mapped == "sent" and msg.sent_at is None:
        msg.sent_at = now
    db.flush()
    return True


def record_inbound_meta(
    db: Session,
    *,
    channel: str,  # 'facebook' | 'instagram'
    external_id: str,  # PSID / IG-scoped id
    business_ref: str,  # page id / ig account id
    message_id: str,
    body: str | None,
    media: list | None = None,
    is_echo: bool = False,
    display_name: str | None = None,
) -> tuple[ConversationMessage, Conversation, bool]:
    """Facebook Messenger / Instagram DM. Identity is a PSID/IGSID, so there's
    no phone contact-match — the thread stays unlinked for staff triage. Best-
    effort profile-name fetch (when a Page token is configured) so triage shows
    a real name instead of an opaque id. Does NOT commit.
    """
    if display_name is None and not is_echo:
        from modules.messaging.services import meta_client

        prof = meta_client.fetch_profile(external_id, channel=channel)
        if prof:
            display_name = prof.get("display_name")

    return record_inbound_message(
        db,
        provider="meta",
        channel=channel,
        external_id=external_id,
        business_ref=business_ref,
        message_id=message_id,
        body=body,
        media=media,
        is_echo=is_echo,
        display_name=display_name,
    )


def _apply_opt_out(db: Session, conv: Conversation, *, source: str) -> None:
    now = _now()
    conv.conversation_metadata = {
        **(conv.conversation_metadata or {}),
        "sms_opted_out_at": now.isoformat(),
        "sms_opt_out_source": source,
    }
    if conv.contact_id is not None:
        contact = db.get(Contact, conv.contact_id)
        if contact is not None:
            contact.sms_opted_out_at = now
            contact.sms_opt_out_source = source


def _apply_opt_in(db: Session, conv: Conversation) -> None:
    meta = {**(conv.conversation_metadata or {})}
    meta.pop("sms_opted_out_at", None)
    meta.pop("sms_opt_out_source", None)
    meta["sms_opted_in_at"] = _now().isoformat()
    conv.conversation_metadata = meta
    if conv.contact_id is not None:
        contact = db.get(Contact, conv.contact_id)
        if contact is not None:
            contact.sms_opted_out_at = None
            contact.sms_opt_out_source = None


# ─── Staff notification ─────────────────────────────────────────────────────


def notify_inbound(db: Session, conv: Conversation, msg: ConversationMessage) -> None:
    """Fire the ``inbox.message_received`` event so routing fans it out to the
    assignee/lead-owner (intrinsic), admins (role default), and subscribers.
    Best-effort: a notification failure must never fail the inbound webhook.
    """
    contact_label = _conversation_label(db, conv)
    preview = (msg.body or "").strip()[:PREVIEW_LEN] or "(no text)"
    channel_label = _CHANNEL_LABELS.get(conv.channel, conv.channel)
    inbox_url = f"{ADMIN_BASE_URL}/inbox"
    # Payload carries both structured fields (for future in-app rendering) and
    # the headline/message/details the shared render_staff_simple_notice email
    # template consumes — same payload-driven pattern as the shift-request
    # kinds, so no per-kind template is needed.
    try:
        notification_routing.record_event(
            db,
            kind="inbox.message_received",
            subject_kind="conversation",
            subject_id=conv.id,
            payload={
                "conversation_id": conv.id,
                "channel": conv.channel,
                "contact_label": contact_label,
                "preview": preview,
                "inbox_url": inbox_url,
                "headline": f"New {channel_label} message",
                "message": (
                    f"{contact_label} sent a message: “{preview}”. "
                    f"Open the inbox to reply: {inbox_url}"
                ),
                "details": [["From", contact_label], ["Channel", channel_label]],
                "preheader": preview,
            },
        )
    except Exception:  # pragma: no cover - defensive
        log.exception("inbox.notify_inbound failed for conversation %s", conv.id)


def _conversation_label(db: Session, conv: Conversation) -> str:
    if conv.contact_id is not None:
        contact = db.get(Contact, conv.contact_id)
        if contact is not None and contact.display_name:
            return contact.display_name
    return conv.external_id


# ─── Read state ─────────────────────────────────────────────────────────────


def mark_read(db: Session, conversation_id: int, user_id: int) -> None:
    """Upsert the per-user read watermark to now."""
    now = _now()
    stmt = pg_insert(ConversationRead).values(
        conversation_id=conversation_id, user_id=user_id, last_read_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["conversation_id", "user_id"],
        set_={"last_read_at": now},
    )
    db.execute(stmt)


class InboxError(Exception):
    def __init__(
        self, code: str, *, http_status: int = 400, detail: str | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        # Optional human-readable extra (e.g. a Twilio rejection reason) the
        # router can surface alongside the machine code.
        self.detail = detail


_UNSET = object()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _contact_summary(db: Session, conv: Conversation) -> dict | None:
    if conv.contact_id is None:
        return None
    c = db.get(Contact, conv.contact_id)
    if c is None:
        return None
    return {
        "id": c.id,
        "display_name": c.display_name,
        "phone": c.phone or c.phone_e164,
        "email": c.email,
        "sms_opted_out": c.sms_opted_out_at is not None,
        "sms_consent": c.sms_consent_at is not None,
    }


def _event_summary(db: Session, conv: Conversation) -> dict | None:
    if conv.event_id is None:
        return None
    e = db.get(Event, conv.event_id)
    if e is None or e.deleted_at is not None:
        return None
    return {
        "id": e.id,
        "event_type": e.event_type,
        "status": e.status,
        "owner_user_id": e.owner_user_id,
    }


def _serialize_message(m: ConversationMessage) -> dict:
    return {
        "id": m.id,
        "direction": m.direction,
        "channel": m.channel,
        "body": m.body,
        "media": m.media or [],
        "status": m.status,
        "sender_ref": m.sender_ref,
        "recipient_ref": m.recipient_ref,
        "is_echo": m.is_echo,
        "sent_by_user_id": m.sent_by_user_id,
        "error_code": m.provider_error_code,
        "error_message": m.provider_error_message,
        "created_at": _iso(m.created_at),
    }


def _serialize_conversation(
    db: Session, conv: Conversation, *, unread: bool
) -> dict:
    meta = conv.conversation_metadata or {}
    contact = _contact_summary(db, conv)
    opted_out = bool(meta.get("sms_opted_out_at")) or bool(
        contact and contact.get("sms_opted_out")
    )
    # Consent for the composer state mirrors the send-path guard: form consent
    # on the contact OR a customer-originated inbound message in the thread.
    consent_ok = bool(
        (contact and contact.get("sms_consent"))
        or conv.last_inbound_at is not None
    )
    reply_enabled, reply_disabled_reason = _reply_state(
        conv, contact_opted_out=opted_out, consent_ok=consent_ok
    )
    return {
        "id": conv.id,
        "channel": conv.channel,
        "provider": conv.provider,
        "external_id": conv.external_id,
        # Fetched Meta profile name (or SMS has none) — lets the UI title an
        # unlinked thread with a real name instead of a raw PSID/number.
        "display_name": meta.get("display_name"),
        "status": conv.status,
        "assigned_user_id": conv.assigned_user_id,
        "contact": contact,
        "event": _event_summary(db, conv),
        "last_message_at": _iso(conv.last_message_at),
        "last_inbound_at": _iso(conv.last_inbound_at),
        "last_inbound_preview": conv.last_inbound_preview,
        "unread": unread,
        "is_linked": conv.contact_id is not None,
        "opted_out": opted_out,
        # Web chat extras (None/False on other channels). ``visitor_active``
        # = the widget's presence heartbeat landed within the last 90s, i.e.
        # the visitor is still on the page and will see a reply live.
        "visitor_page_url": conv.visitor_page_url,
        "visitor_last_seen_at": _iso(conv.visitor_last_seen_at),
        "visitor_active": bool(
            conv.visitor_last_seen_at is not None
            and _now() - conv.visitor_last_seen_at
            <= web_chat_active_window()
        ),
        # Whether the composer can actually deliver on this channel today.
        # Web chat is always live; SMS is live once A2P sending is enabled, the
        # recipient hasn't opted out, and consent/inbound-reply holds; Meta
        # stays gated. reply_disabled_reason is a slug the UI maps to copy.
        "reply_enabled": reply_enabled,
        "reply_disabled_reason": reply_disabled_reason,
    }


def _reply_state(
    conv: Conversation, *, contact_opted_out: bool, consent_ok: bool
) -> tuple[bool, str | None]:
    """Return (reply_enabled, disabled_reason) for this channel. The reason is
    a stable slug the UI maps to copy; None when the composer is live."""
    if conv.channel == "web_chat":
        return True, None
    if conv.channel == "sms":
        from config.settings import SMS_SENDING_ENABLED

        if not SMS_SENDING_ENABLED:
            return False, "sms_sending_disabled"
        if contact_opted_out:
            return False, "recipient_opted_out"
        if not consent_ok:
            return False, "recipient_no_sms_consent"
        return True, None
    return False, "channel_send_not_supported"


def list_conversations(
    db: Session,
    *,
    user_id: int,
    channel: str | None = None,
    status: str | None = None,
    only_unread: bool = False,
    only_unlinked: bool = False,
    assigned_user_id: int | None = None,
    q: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Inbox list for one staff user, newest first, each row carrying that
    user's unread flag (derived from conversation_reads)."""
    from sqlalchemy.orm import aliased

    reads = aliased(ConversationRead)
    query = db.query(Conversation, reads.last_read_at).outerjoin(
        reads,
        and_(reads.conversation_id == Conversation.id, reads.user_id == user_id),
    )
    if channel:
        query = query.filter(Conversation.channel == channel)
    if status:
        query = query.filter(Conversation.status == status)
    if only_unlinked:
        query = query.filter(Conversation.contact_id.is_(None))
    if assigned_user_id is not None:
        query = query.filter(Conversation.assigned_user_id == assigned_user_id)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Conversation.external_id).like(like),
                func.lower(Conversation.last_inbound_preview).like(like),
            )
        )
    query = query.order_by(
        Conversation.last_message_at.desc().nullslast()
    ).limit(min(limit, 200))

    out: list[dict] = []
    for conv, last_read in query.all():
        unread = conv.last_inbound_at is not None and (
            last_read is None or conv.last_inbound_at > last_read
        )
        if only_unread and not unread:
            continue
        out.append(_serialize_conversation(db, conv, unread=unread))
    return out


def conversation_detail(
    db: Session, conversation_id: int, *, user_id: int, do_mark_read: bool = True
) -> dict | None:
    """Full thread + context. Marks the conversation read for this user."""
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    data = _serialize_conversation(db, conv, unread=False)
    msgs = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
        .all()
    )
    data["messages"] = [_serialize_message(m) for m in msgs]
    if do_mark_read:
        mark_read(db, conversation_id, user_id)
    return data


def set_conversation_fields(
    db: Session,
    conversation_id: int,
    *,
    status=_UNSET,
    assigned_user_id=_UNSET,
) -> dict:
    """Patch status and/or assignee. Raises InboxError on a bad value."""
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise InboxError("conversation_not_found", http_status=404)
    if status is not _UNSET:
        if status not in ("open", "pending", "resolved"):
            raise InboxError("invalid_status", http_status=422)
        conv.status = status
    if assigned_user_id is not _UNSET:
        conv.assigned_user_id = assigned_user_id
    conv.updated_at = _now()
    db.flush()
    return _serialize_conversation(db, conv, unread=False)


def send_reply(
    db: Session,
    conversation_id: int,
    *,
    body: str,
    user_id: int,
    allow_quiet_hours: bool = False,
) -> dict:
    """Outbound reply, branched on channel.

    ``web_chat`` needs NO transport: writing the outbound row IS the delivery
    — the visitor's cursor poll reads it. The channel check runs FIRST so the
    SMS/Meta gate can never block a chat reply.

    ``sms`` sends via Twilio (A2P 10DLC approved), guarded in order:
    opt-out → quiet hours (overridable) → transport configured → send. The
    row is persisted with the returned Twilio SID (for the delivery-status
    callback) or the error, and NEVER partially: a failed send yields a
    ``failed`` row and a raised InboxError, not a half-written thread.

    Meta (facebook/instagram) outbound stays hard-gated until App Review."""
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise InboxError("conversation_not_found", http_status=404)
    text = (body or "").strip()
    if not text:
        raise InboxError("empty_message", http_status=422)

    if conv.channel == "web_chat":
        now = _now()
        msg = ConversationMessage(
            conversation_id=conv.id,
            direction="outbound",
            channel=conv.channel,
            provider=conv.provider,
            sender_ref=conv.business_ref or "web",
            recipient_ref=conv.external_id,
            body=text,
            status="sent",
            sent_by_user_id=user_id,
            created_at=now,
            sent_at=now,
        )
        db.add(msg)
        conv.last_message_at = now
        conv.last_outbound_at = now
        # Answered → waiting on the visitor. Their next message reopens it.
        conv.status = "pending"
        conv.updated_at = now
        db.flush()
        return {
            "message": _serialize_message(msg),
            "conversation": _serialize_conversation(db, conv, unread=False),
        }

    if conv.channel == "sms":
        return _send_sms_reply(
            db, conv, body=text, user_id=user_id, allow_quiet_hours=allow_quiet_hours
        )

    # Facebook / Instagram outbound not built (needs Meta App Review).
    raise InboxError("channel_send_not_supported", http_status=501)


def _sms_consent_ok(conv: Conversation, contact: Contact | None) -> bool:
    """Whether an outbound SMS is permitted on consent grounds.

    Policy (matches the A2P registration and contacts.sms_consent_at doc):
    a business-initiated text needs express consent, BUT replying inside a
    thread the customer started themselves is the standard TCPA/established-
    business-relationship reply case and doesn't require the form checkbox.

    So: allow if the contact has recorded ``sms_consent_at`` OR this
    conversation has at least one inbound message (``last_inbound_at`` is set
    on every inbound — see record_inbound_message). Opt-out is handled
    separately and always wins.
    """
    if contact is not None and contact.sms_consent_at is not None:
        return True
    return conv.last_inbound_at is not None


def _send_sms_reply(
    db: Session,
    conv: Conversation,
    *,
    body: str,
    user_id: int,
    allow_quiet_hours: bool,
) -> dict:
    from config.settings import SMS_SENDING_ENABLED
    from modules.core.services import sms_transport

    if not SMS_SENDING_ENABLED:
        raise InboxError("sms_sending_disabled", http_status=503)

    # A valid E.164 recipient is a precondition for every downstream guard —
    # opt-out/consent are meaningless against a malformed destination.
    if normalize_phone_e164(conv.external_id or "") is None:
        raise InboxError("recipient_has_no_valid_phone", http_status=422)

    contact = db.get(Contact, conv.contact_id) if conv.contact_id else None

    # Opt-out is absolute — a STOP contact must never be texted.
    meta = conv.conversation_metadata or {}
    opted_out = bool(meta.get("sms_opted_out_at")) or (
        contact is not None and contact.sms_opted_out_at is not None
    )
    if opted_out:
        raise InboxError("recipient_opted_out", http_status=409)

    # Consent gate: business-initiated texts need express consent; replying to
    # a customer-originated thread is the TCPA reply case and is allowed.
    if not _sms_consent_ok(conv, contact):
        raise InboxError("recipient_no_sms_consent", http_status=409)

    if not allow_quiet_hours and in_sms_quiet_hours():
        # Recoverable: the UI offers a "send anyway" that re-calls with
        # allow_quiet_hours=True.
        raise InboxError("quiet_hours", http_status=409)

    if not sms_transport.sms_transport_configured():
        raise InboxError("sms_not_configured", http_status=503)

    now = _now()
    consent_at = contact.sms_consent_at if contact is not None else None

    # Persist a queued row FIRST so the message is durable even if the process
    # dies mid-send; then attempt delivery and stamp the outcome.
    msg = ConversationMessage(
        conversation_id=conv.id,
        direction="outbound",
        channel="sms",
        provider="twilio",
        sender_ref="business",
        recipient_ref=conv.external_id,
        body=body,
        status="queued",
        sent_by_user_id=user_id,
        consent_snapshot={
            "sms_consent_at": consent_at.isoformat() if consent_at else None,
            "quiet_hours_override": bool(allow_quiet_hours),
        },
        created_at=now,
    )
    db.add(msg)
    db.flush()

    result = sms_transport.get_sms_transport().send_result(
        to=conv.external_id, body=body
    )
    if result.ok:
        msg.status = "sent"
        msg.sent_at = _now()
        msg.provider_message_id = result.provider_message_id
        conv.last_message_at = msg.sent_at
        conv.last_outbound_at = msg.sent_at
        if conv.status == "open":
            conv.status = "pending"  # awaiting the customer's reply
        conv.updated_at = msg.sent_at
        db.flush()
        return {
            "message": _serialize_message(msg),
            "conversation": _serialize_conversation(db, conv, unread=False),
        }

    # Failed: raise so the router rolls back — the queued row is discarded
    # (no phantom "failed" message in the thread) and the composer shows the
    # reason. The attempt is still in Twilio's logs and this app's logs.
    raise InboxError(
        "sms_send_failed",
        http_status=502,
        detail=result.error_message,
    )


# ─── Outbound-initiated SMS (Phase 8: start from CRM surfaces) ───────────────


def sms_eligibility(db: Session, contact: Contact) -> dict:
    """Server-authoritative "is this contact approved for messaging" check.

    Returns a stable {eligible, reason} object. Reason precedence (first failing
    gate wins): no_phone -> no_consent -> opted_out -> sms_disabled ->
    transport_unavailable -> eligible. NEVER trust a client for eligibility.

    An existing conversation independently marked opted out (STOP recorded on
    conversation_metadata even when unlinked) is also treated as opted_out.
    """
    from config.settings import SMS_SENDING_ENABLED
    from modules.core.services import sms_transport

    e164 = normalize_phone_e164(contact.phone_e164 or "")
    if e164 is None:
        return {"eligible": False, "reason": "no_phone"}

    if contact.sms_consent_at is None:
        # No form consent — but a customer-originated thread is the TCPA reply
        # case. Mirror the send-path _sms_consent_ok exactly.
        conv = _existing_sms_conversation(db, e164)
        if conv is None or conv.last_inbound_at is None:
            return {"eligible": False, "reason": "no_consent"}

    # Opt-out wins over consent: check the contact AND any existing thread's
    # independent opt-out marker.
    if contact.sms_opted_out_at is not None:
        return {"eligible": False, "reason": "opted_out"}
    conv = _existing_sms_conversation(db, e164)
    if conv is not None:
        meta = conv.conversation_metadata or {}
        if meta.get("sms_opted_out_at"):
            return {"eligible": False, "reason": "opted_out"}

    if not SMS_SENDING_ENABLED:
        return {"eligible": False, "reason": "sms_disabled"}
    if not sms_transport.sms_transport_configured():
        return {"eligible": False, "reason": "transport_unavailable"}

    return {"eligible": True, "reason": "eligible"}


def _existing_sms_conversation(db: Session, e164: str) -> Conversation | None:
    return (
        db.query(Conversation)
        .filter_by(provider="twilio", channel="sms", external_id=e164)
        .first()
    )


def start_sms_conversation(
    db: Session, *, contact_id: int, event_id: int | None = None
) -> dict:
    """Idempotently create-or-reuse the canonical Twilio SMS conversation for a
    contact's phone number. Does NOT send a message. Returns
    ``{conversation_id, contact, eligibility, created}``.

    - The destination is ALWAYS the contact's own normalized phone — never a
      client-supplied number.
    - Reuse is keyed on (twilio, sms, <contact E.164>) via the unique index, so
      two simultaneous callers converge on one thread and multiple deals for one
      contact never fork the SMS history.
    - Links contact_id/event_id only when unset, so an existing thread's
      historical context is never clobbered.
    """
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise InboxError("contact_not_found", http_status=404)

    e164 = normalize_phone_e164(contact.phone_e164 or "")
    if e164 is None:
        raise InboxError("recipient_has_no_valid_phone", http_status=422)

    from config.settings import TWILIO_FROM_NUMBER

    conv, created = upsert_conversation(
        db,
        provider="twilio",
        channel="sms",
        external_id=e164,
        business_ref=TWILIO_FROM_NUMBER,
    )

    # Non-clobbering link (mirrors record_inbound_sms): only fill blanks.
    if conv.contact_id is None:
        conv.contact_id = contact.id
    if conv.event_id is None:
        ev = (
            db.get(Event, event_id)
            if event_id is not None
            else _recent_event_for_contact(db, contact.id)
        )
        if ev is not None and ev.deleted_at is None:
            conv.event_id = ev.id
    db.flush()

    return {
        "conversation_id": conv.id,
        "created": created,
        "contact": _contact_summary(db, conv),
        "eligibility": sms_eligibility(db, contact),
    }


def unread_count_for_user(db: Session, user_id: int) -> int:
    """Conversations with an inbound message newer than this user's read
    watermark (or never read)."""
    return (
        db.query(func.count(Conversation.id))
        .outerjoin(
            ConversationRead,
            and_(
                ConversationRead.conversation_id == Conversation.id,
                ConversationRead.user_id == user_id,
            ),
        )
        .filter(
            Conversation.last_inbound_at.isnot(None),
            or_(
                ConversationRead.last_read_at.is_(None),
                Conversation.last_inbound_at > ConversationRead.last_read_at,
            ),
        )
        .scalar()
    ) or 0
