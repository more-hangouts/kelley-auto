"""Meta Conversions API sender (Phase 3 of the storefront analytics plan).

Two halves, deliberately decoupled:

  * ``enqueue_lead_conversion`` — called in-transaction from the public lead
    path. Normalizes + SHA-256 hashes the customer identifiers Meta allows for
    matching (email/phone/first/last), attaches the pseudonymous browser
    identifiers (``_fbp``/``_fbc``, client IP, user agent), and writes ONE
    ``ad_conversion_events`` row per conversion. Rows are written even while
    delivery is disabled so the queue is a complete first-party record.

  * ``send_pending`` — drains the queue to the Meta Graph API. Gated by the
    ``META_CAPI_ENABLED`` master kill switch plus the presence of a Pixel ID
    and access token; until all three are set, rows simply accumulate as
    ``pending``. Runs from two places: a FastAPI background task right after
    a lead commits (fast path), and the 5-minute schedule-monitor worker
    (retry sweep). Per-row delivery so one malformed event can't poison a
    batch; ``event_id`` matches the browser Pixel event for deduplication.

Privacy invariants (see STORE_FRONT_ANALYTICS_AND_CAPI_PLAN.md):
  - NEVER enqueue DOB, driver-license data, SSN, application address, notes,
    or free-text message content.
  - ``user_data`` identifiers are hashed server-side before they are stored.
    The only raw values kept are client IP + user agent, which Meta requires
    unhashed for web events and which already live (hashed IP) in sessions.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import (
    META_CAPI_API_VERSION,
    META_CAPI_ENABLED,
    META_CAPI_TEST_EVENT_CODE,
    META_CAPI_TOKEN,
    META_PIXEL_ID,
    PUBLIC_SITE_URL,
)
from database.connection import SessionLocal
from database.models import AdConversionEvent
from modules.analytics.services.storefront_analytics_service import TrackingContext

log = logging.getLogger(__name__)

# Meta rejects web events older than 7 days; anything that stale is marked
# `suppressed` instead of burning retries forever.
_MAX_EVENT_AGE = timedelta(days=7)
_MAX_ATTEMPTS = 5
_SEND_TIMEOUT_SECONDS = 10.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_email(email: str | None) -> str | None:
    cleaned = (email or "").strip().lower()
    return _sha256(cleaned) if cleaned and "@" in cleaned else None


def _hash_phone(phone: str | None) -> str | None:
    """Meta wants digits only with country code. US 10-digit numbers get a
    leading ``1``; anything shorter is too ambiguous to hash usefully."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        digits = "1" + digits
    return _sha256(digits) if len(digits) >= 11 else None


def _hash_name_part(part: str | None) -> str | None:
    cleaned = (part or "").strip().lower()
    return _sha256(cleaned) if cleaned else None


def _absolute_source_url(source_page: str | None) -> str | None:
    if not source_page:
        return None
    if source_page.startswith("http://") or source_page.startswith("https://"):
        return source_page
    return PUBLIC_SITE_URL.rstrip("/") + "/" + source_page.lstrip("/")


def enqueue_lead_conversion(
    db: Session,
    *,
    crm_event_id: int,
    name: str | None,
    email: str | None,
    phone: str | None,
    ctx: TrackingContext,
    vehicle_listing_code: str | None = None,
    vehicle_label: str | None = None,
    vehicle_price_cents: int | None = None,
) -> AdConversionEvent | None:
    """Queue one Meta ``Lead`` event for a submitted storefront lead.

    Does NOT commit — runs inside the lead transaction so the queue row and
    the deal land (or roll back) together. Best-effort: any failure logs and
    returns None; a queueing hiccup must never fail the customer's lead."""
    try:
        first, _, last = (name or "").strip().partition(" ")
        user_data: dict[str, str] = {}
        for key, value in (
            ("em", _hash_email(email)),
            ("ph", _hash_phone(phone)),
            ("fn", _hash_name_part(first)),
            ("ln", _hash_name_part(last.strip() or None)),
            ("fbp", ctx.fbp),
            ("fbc", ctx.fbc),
            ("client_ip_address", ctx.client_ip),
            ("client_user_agent", ctx.user_agent),
        ):
            if value:
                user_data[key] = value

        custom_data: dict[str, object] = {}
        code = vehicle_listing_code or ctx.listing_code
        if code:
            custom_data["content_type"] = "vehicle"
            custom_data["content_ids"] = [code]
        if vehicle_label:
            custom_data["content_name"] = vehicle_label
        if vehicle_price_cents:
            custom_data["currency"] = "USD"
            custom_data["value"] = round(vehicle_price_cents / 100.0, 2)

        row = AdConversionEvent(
            provider="meta",
            event_name="Lead",
            # Same id the browser Pixel fires with → Meta dedups the pair.
            event_id=ctx.event_id,
            event_time=_now(),
            source_url=_absolute_source_url(ctx.source_page or ctx.landing_page),
            action_source="website",
            user_data=user_data,
            custom_data=custom_data,
            lead_event_id=crm_event_id,
        )
        db.add(row)
        db.flush()
        return row
    except Exception:  # pragma: no cover — queueing must never fail a lead
        log.exception("meta_capi enqueue failed crm_event_id=%s", crm_event_id)
        return None


def _delivery_configured() -> bool:
    return bool(META_CAPI_ENABLED and META_PIXEL_ID and META_CAPI_TOKEN)


def _graph_payload(row: AdConversionEvent) -> dict:
    event: dict[str, object] = {
        "event_name": row.event_name,
        "event_time": int(row.event_time.timestamp()),
        "action_source": row.action_source,
        "user_data": row.user_data or {},
    }
    if row.event_id:
        event["event_id"] = row.event_id
    if row.source_url:
        event["event_source_url"] = row.source_url
    if row.custom_data:
        event["custom_data"] = row.custom_data
    payload: dict[str, object] = {"data": [event]}
    if META_CAPI_TEST_EVENT_CODE:
        payload["test_event_code"] = META_CAPI_TEST_EVENT_CODE
    return payload


def send_pending(db: Session, *, limit: int = 25) -> int:
    """Deliver queued conversions to Meta. Returns the number sent.

    No-ops (leaving rows ``pending``) until the kill switch, Pixel ID, and
    token are all configured — so the backlog delivers the moment credentials
    land. Caller owns the commit."""
    if not _delivery_configured():
        return 0

    rows = list(
        db.execute(
            select(AdConversionEvent)
            .where(
                AdConversionEvent.provider == "meta",
                AdConversionEvent.status.in_(("pending", "failed")),
                AdConversionEvent.attempt_count < _MAX_ATTEMPTS,
            )
            .order_by(AdConversionEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if not rows:
        return 0

    url = (
        f"https://graph.facebook.com/{META_CAPI_API_VERSION}/"
        f"{META_PIXEL_ID}/events"
    )
    sent = 0
    now = _now()
    with httpx.Client(timeout=_SEND_TIMEOUT_SECONDS) as client:
        for row in rows:
            if now - row.event_time > _MAX_EVENT_AGE:
                row.status = "suppressed"
                row.last_error = "event older than 7 days; Meta would reject"
                continue
            row.attempt_count += 1
            try:
                resp = client.post(
                    url,
                    json=_graph_payload(row),
                    params={"access_token": META_CAPI_TOKEN},
                )
                if resp.status_code == 200:
                    row.status = "sent"
                    row.sent_at = _now()
                    row.last_error = None
                    sent += 1
                else:
                    row.status = "failed"
                    # Body is Meta's error JSON — safe to keep, no PII of ours.
                    row.last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    log.warning(
                        "meta_capi send failed id=%s attempt=%s: %s",
                        row.id,
                        row.attempt_count,
                        row.last_error,
                    )
            except httpx.HTTPError as exc:
                row.status = "failed"
                row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.warning(
                    "meta_capi send error id=%s attempt=%s: %s",
                    row.id,
                    row.attempt_count,
                    row.last_error,
                )
    return sent


def flush_queue() -> None:
    """Standalone drain for FastAPI BackgroundTasks — runs right after a lead
    commits so the conversion reaches Meta in seconds, not at the next sweep.
    Owns its session/commit; every error is swallowed (the 5-minute retry
    sweep will pick up anything this pass missed)."""
    if not _delivery_configured():
        return
    db = SessionLocal()
    try:
        sent = send_pending(db)
        db.commit()
        if sent:
            log.info("meta_capi background flush sent %s event(s)", sent)
    except Exception:
        db.rollback()
        log.exception("meta_capi background flush failed")
    finally:
        db.close()


def tick(db: Session) -> None:
    """Retry sweep entry point for the schedule-monitor worker loop."""
    sent = send_pending(db)
    db.commit()
    if sent:
        log.info("meta_capi sweep sent %s event(s)", sent)
