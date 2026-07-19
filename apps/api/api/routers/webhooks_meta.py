"""Public Meta webhook endpoints — Facebook Messenger + Instagram DMs
(Omnichannel Inbox Plan Part 4; Phase 5).

Two endpoints under ``/api/webhooks/meta``:

  - ``GET``  — the subscription verification handshake. Meta calls it with
    ``hub.mode=subscribe`` + ``hub.verify_token``; we echo ``hub.challenge``
    back as plain text when the token matches ``META_WEBHOOK_VERIFY_TOKEN``.

  - ``POST`` — message events. We verify ``X-Hub-Signature-256`` over the raw
    body (app secret), store the raw payload for audit, then thread each
    message. One POST can batch several messages across entries; per-message
    idempotency is the ``(provider, mid)`` unique on ``conversation_messages``
    (not the raw-store dedup, which is per-single-id), so we always store the
    raw row and let the message layer de-dupe retries and our own echoes.

Inbound works with app-role (dev-mode) accounts before App Review. Outbound
replies are NOT here — they need App Review + the human_agent tag.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from config import settings
from database.connection import get_db
from services import inbox_service, webhook_ingest
from services.meta_signature import verify_signature

log = logging.getLogger(__name__)
router = APIRouter()

# Meta object type → our channel label.
_OBJECT_CHANNEL = {"page": "facebook", "instagram": "instagram"}


@router.get("")
def verify(request: Request) -> Response:
    """Subscription verification handshake."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")
    if (
        mode == "subscribe"
        and settings.META_WEBHOOK_VERIFY_TOKEN
        and token == settings.META_WEBHOOK_VERIFY_TOKEN
    ):
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail={"code": "verification_failed"})


@router.post("")
async def events(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    raw = await request.body()

    if settings.INBOUND_META_REQUIRE_SIGNATURE:
        if not settings.META_APP_SECRET:
            log.error("meta webhook: META_APP_SECRET unset; cannot verify")
            raise HTTPException(status_code=503, detail={"code": "meta_not_configured"})
        if not verify_signature(
            settings.META_APP_SECRET, raw, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(status_code=403, detail={"code": "invalid_signature"})
    else:
        log.warning("meta webhook: signature verification bypassed (dev flag)")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail={"code": "malformed_webhook"})

    channel = _OBJECT_CHANNEL.get(payload.get("object"))
    if channel is None:
        # A subscription we don't handle (feed, mentions, etc.) — ack so Meta
        # doesn't retry, but do nothing.
        return PlainTextResponse("EVENT_RECEIVED")

    # Audit: one raw row per delivery. external_id=None (a batch has many mids;
    # message-level dedup is the idempotency guard).
    raw_row = webhook_ingest.record_webhook_event(
        db,
        source="meta",
        event_type=f"{channel}_message",
        payload=payload,
        headers=dict(request.headers),
    )

    handled = 0
    for entry in payload.get("entry", []) or []:
        for ev in entry.get("messaging", []) or []:
            if _handle_messaging_event(db, channel, ev):
                handled += 1

    raw_row.processed = True
    raw_row.processed_at = datetime.now(timezone.utc)
    db.commit()
    return PlainTextResponse("EVENT_RECEIVED")


def _extract_media(message: dict) -> list[dict]:
    out: list[dict] = []
    for att in message.get("attachments", []) or []:
        payload = att.get("payload") or {}
        url = payload.get("url")
        if url:
            out.append({"url": url, "content_type": att.get("type")})
    return out


def _handle_messaging_event(db: Session, channel: str, ev: dict) -> bool:
    """Process one Messenger/IG messaging event. Returns True if a message was
    recorded. Skips non-message events (delivery/read/postback)."""
    message = ev.get("message")
    if not isinstance(message, dict):
        return False
    mid = message.get("mid")
    if not mid:
        return False

    is_echo = bool(message.get("is_echo"))
    sender = (ev.get("sender") or {}).get("id")
    recipient = (ev.get("recipient") or {}).get("id")
    if not sender or not recipient:
        return False

    # On an echo the business (page) is the sender; otherwise the customer is.
    if is_echo:
        external_id, business_ref = recipient, sender
    else:
        external_id, business_ref = sender, recipient

    msg, conv, created = inbox_service.record_inbound_meta(
        db,
        channel=channel,
        external_id=external_id,
        business_ref=business_ref,
        message_id=mid,
        body=message.get("text"),
        media=_extract_media(message),
        is_echo=is_echo,
    )
    if created and not is_echo:
        inbox_service.notify_inbound(db, conv, msg)
    return created
