"""First-party storefront analytics — visitor/session/event ingestion and the
lead-attribution join (migration 090).

Two responsibilities:

  * ``record_event`` — the write behind ``POST /api/public/track``. Upserts the
    visitor (``ka_vid``) and session (``ka_sid``), then appends one behavioral
    event. Best-effort by design: analytics must never surface an error to a
    shopper, and a duplicate delivery (same ``event_id``) is silently ignored.

  * ``attach_lead_attribution`` — called from the public lead path once a deal
    exists. Records the ``lead_submitted`` conversion event and writes the 1:1
    ``lead_attribution`` bridge row. Wrapped in a SAVEPOINT so a tracking hiccup
    can never roll back or fail the customer's actual lead submission.

Privacy: this module writes NO BHPH application PII (DOB/DL/SSN/address) and no
raw IP — only a hashed IP and pseudonymous ad cookies (``_fbp``/``_fbc``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import BigInteger, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    CatalogItem,
    LeadAttribution,
    StorefrontEvent,
    StorefrontSession,
    StorefrontVisitor,
)

log = logging.getLogger(__name__)

# Whitelist keeps the stream clean — anything else is silently acknowledged
# and dropped so a rogue/instrumented client can't pollute the table.
ALLOWED_EVENTS = frozenset(
    {
        "page_view",
        "vehicle_view",
        "lead_form_opened",
        "lead_form_started",
        "lead_submitted",
    }
)

# Server-only funnel milestones. NEVER accepted from /track — the browser is a
# witness, not an author: it reports behavior, it does not declare money. These
# are written in-process via record_milestone() and inherit the deal's
# first-touch attribution so revenue groups under the channel that produced
# the lead.
SERVER_EVENTS = frozenset({"payment_received", "chat_escalated"})

_CONVERSION_EVENT = "lead_submitted"

# ---------------------------------------------------------------------------
# Source derivation (the catering210 priority ladder):
#     explicit UTM → ad click-id → referrer host → None
# A None source is honest "(direct)/unknown" — never fabricated.
# ---------------------------------------------------------------------------

# Click-id param → source; order is precedence (fbclid wins ties).
_CLICK_ID_PARAMS: tuple[tuple[str, str], ...] = (
    ("fbclid", "facebook"),
    ("gclid", "google"),
    ("msclkid", "bing"),
)

# Referrer-host suffix/substring → (source, medium).
_REFERRER_RULES: tuple[tuple[str, str, str], ...] = (
    ("facebook.com", "facebook", "referral"),
    ("fb.com", "facebook", "referral"),
    ("instagram.com", "instagram", "referral"),
    ("google.", "google", "organic"),
    ("bing.com", "bing", "organic"),
    ("yahoo.com", "yahoo", "organic"),
    ("duckduckgo.com", "duckduckgo", "organic"),
    ("tiktok.com", "tiktok", "referral"),
    ("youtube.com", "youtube", "referral"),
    ("yelp.com", "yelp", "referral"),
    ("nextdoor.com", "nextdoor", "referral"),
    ("craigslist.org", "craigslist", "referral"),
    ("cargurus.com", "cargurus", "referral"),
    ("autotrader.com", "autotrader", "referral"),
    ("cars.com", "cars.com", "referral"),
    ("offerup.com", "offerup", "referral"),
)

_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|scrape|fetch|monitor|headless|lighthouse"
    r"|facebookexternalhit|meta-externalagent|facebookcatalog|whatsapp"
    r"|pingdom|gtmetrix|semrush|ahrefs|mj12|dataprovider|petalbot"
    r"|python-requests|python-httpx|aiohttp|curl/|wget/|go-http-client|okhttp",
    re.IGNORECASE,
)


def is_bot_user_agent(user_agent: str | None) -> bool:
    """True for crawlers, link-preview renderers, and script UAs. Meta bursts
    a landing page with fetches the minute an ad goes live — stored, those
    hits masquerade as funnel engagement. An empty UA is treated as a bot:
    every real storefront browser sends one."""
    if not user_agent or not user_agent.strip():
        return True
    return bool(_BOT_UA_RE.search(user_agent))


def _clean_token(value: str | None, *, limit: int = 120) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned[:limit] or None


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.strip().lower()
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _classify_referrer(host: str | None) -> tuple[str, str] | None:
    if not host:
        return None
    for needle, source, medium in _REFERRER_RULES:
        if needle in host:
            return source, medium
    return None


# Meta's ``_fbc`` cookie encodes the ad click id: fb.1.<timestamp>.<fbclid>
_FBC_RE = re.compile(r"^fb\.\d+\.\d+\.(.+)$")


def _fbclid_from_fbc(fbc: str | None) -> str | None:
    """Recover the fbclid from a stored ``_fbc`` cookie — proof of a Facebook
    ad click even when the in-app browser dropped the tagged URL."""
    match = _FBC_RE.match((fbc or "").strip())
    return match.group(1)[:255] if match else None


def _url_params(url: str | None) -> dict[str, str]:
    """First value of each query param of ``url``, lowercased keys."""
    if not url or "?" not in url:
        return {}
    try:
        parsed = parse_qs(urlparse(url).query, keep_blank_values=False)
    except ValueError:
        return {}
    return {k.lower(): v[0] for k, v in parsed.items() if v}


def derive_source(
    *,
    utm: dict[str, str] | None = None,
    click_ids: dict[str, str] | None = None,
    referrer: str | None = None,
    landing_page: str | None = None,
    page_url: str | None = None,
) -> dict[str, str | None]:
    """Resolve ``{source, medium, click_id}`` with the priority ladder.

    Kills the classic "everything shows as (direct)" failure: a Meta ad click
    arrives with only an ``fbclid`` and no UTMs, and a naive tracker logs it
    as direct. Recovery rung: when the visitor lost their tags to an in-app
    browser re-navigation, the *referrer/landing URL they arrived from* often
    still carries them — parse those before falling back to host matching.
    """
    utm = utm or {}
    click_ids = {k: v for k, v in (click_ids or {}).items() if v}

    source = _clean_token(utm.get("source"))
    medium = _clean_token(utm.get("medium"))

    # Click id: keep the highest-precedence one we have, from the explicit
    # payload first, then recovered from any stored URL.
    click_id: str | None = None
    click_source: str | None = None
    for candidates in (
        click_ids,
        _url_params(page_url),
        _url_params(landing_page),
        _url_params(referrer),
    ):
        for param, param_source in _CLICK_ID_PARAMS:
            value = (candidates.get(param) or "").strip()
            if value:
                click_id = value[:255]
                click_source = param_source
                break
        if click_id:
            break

    if not source and click_source:
        source, medium = click_source, medium or "paid"

    if not source:
        # Orphan recovery: tagged UTMs living in the URL the visitor came
        # from (e.g. the fully-tagged ad landing URL as referrer).
        for url in (landing_page, referrer):
            recovered = _url_params(url)
            if recovered.get("utm_source"):
                source = _clean_token(recovered.get("utm_source"))
                medium = medium or _clean_token(recovered.get("utm_medium"))
                break

    if not source:
        ref_host = _host_of(referrer)
        page_host = _host_of(page_url) or _host_of(landing_page)
        if ref_host and ref_host != page_host:  # ignore self-referrals
            classified = _classify_referrer(ref_host)
            if classified:
                source, medium = classified[0], medium or classified[1]
            else:
                source, medium = ref_host[:120], medium or "referral"

    return {"source": source, "medium": medium, "click_id": click_id}


@dataclass
class TrackingContext:
    """Attribution/identity carried on a lead submission (from cookies + form).
    Everything here is optional — a lead with no tracking still creates a deal;
    it just won't have a browsing journey attached."""

    visitor_key: str | None = None
    session_key: str | None = None
    event_id: str | None = None  # CAPI dedup id for the lead_submitted event
    landing_page: str | None = None
    source_page: str | None = None
    referrer: str | None = None
    utm: dict[str, str] = field(default_factory=dict)
    fbp: str | None = None
    fbc: str | None = None
    fbclid: str | None = None
    gclid: str | None = None
    msclkid: str | None = None
    listing_code: str | None = None
    vehicle_id: int | None = None
    # Request context (set by the router, not the client payload). Used only
    # for Meta CAPI matching — analytics tables keep storing hashed IP only.
    client_ip: str | None = None
    user_agent: str | None = None

    def has_signal(self) -> bool:
        return bool(
            self.visitor_key
            or self.session_key
            or self.event_id
            or self.fbp
            or self.fbc
            or self.utm
            or self.landing_page
            or self.source_page
            or self.referrer
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_vehicle_id(
    db: Session, *, vehicle_id: int | None, listing_code: str | None
) -> int | None:
    """Tolerantly map a client-supplied vehicle reference to a real
    ``catalog_items.id``. Returns None (never raises) if it can't — the raw
    ``listing_code`` is still stored on the event for context."""
    try:
        if vehicle_id is not None:
            hit = db.execute(
                select(CatalogItem.id).where(CatalogItem.id == vehicle_id)
            ).scalar_one_or_none()
            if hit is not None:
                return hit
        if listing_code:
            return db.execute(
                select(CatalogItem.id).where(CatalogItem.public_code == listing_code)
            ).scalar_one_or_none()
    except Exception:  # pragma: no cover — resolution is best-effort
        log.debug("vehicle resolve failed", exc_info=True)
    return None


def _upsert_visitor(
    db: Session,
    visitor_key: str | None,
    *,
    utm: dict[str, str] | None,
    derived: dict[str, str | None] | None = None,
) -> StorefrontVisitor | None:
    if not visitor_key:
        return None
    # The touch we persist is the raw UTMs enriched with the derived channel,
    # so first/last-touch reporting works even for UTM-less paid clicks.
    touch = dict(utm or {})
    for key in ("source", "medium", "click_id"):
        value = (derived or {}).get(key)
        if value and not touch.get(key):
            touch[key] = value

    visitor = db.execute(
        select(StorefrontVisitor).where(StorefrontVisitor.visitor_key == visitor_key)
    ).scalar_one_or_none()
    now = _now()
    if visitor is None:
        visitor = StorefrontVisitor(
            visitor_key=visitor_key,
            first_seen_at=now,
            last_seen_at=now,
            first_touch_attribution=touch,
            last_touch_attribution=touch,
        )
        db.add(visitor)
        db.flush()
    else:
        visitor.last_seen_at = now
        if touch:
            visitor.last_touch_attribution = touch
            # Backfill first-touch if the visitor arrived untagged and only
            # later showed a channel (e.g. cookie survived, tags recovered).
            if not visitor.first_touch_attribution:
                visitor.first_touch_attribution = touch
    return visitor


def _upsert_session(
    db: Session,
    session_key: str | None,
    *,
    visitor: StorefrontVisitor | None,
    landing_page: str | None,
    referrer: str | None,
    utm: dict[str, str] | None,
    user_agent: str | None,
    ip_hash: str | None,
) -> StorefrontSession | None:
    # A session needs a visitor (FK NOT NULL). No visitor → no session, but the
    # event can still be recorded unattached.
    if not session_key or visitor is None:
        return None
    session = db.execute(
        select(StorefrontSession).where(StorefrontSession.session_key == session_key)
    ).scalar_one_or_none()
    now = _now()
    if session is None:
        session = StorefrontSession(
            visitor_id=visitor.id,
            session_key=session_key,
            started_at=now,
            last_seen_at=now,
            landing_page=landing_page,
            initial_referrer=referrer,
            initial_utm=utm or {},
            user_agent=user_agent,
            ip_hash=ip_hash,
        )
        db.add(session)
        db.flush()
    else:
        session.last_seen_at = now
    return session


def record_event(
    db: Session,
    *,
    visitor_key: str | None,
    session_key: str | None,
    event_name: str,
    event_id: str | None = None,
    path: str | None = None,
    referrer: str | None = None,
    utm: dict[str, str] | None = None,
    listing_code: str | None = None,
    vehicle_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    landing_page: str | None = None,
    user_agent: str | None = None,
    ip_hash: str | None = None,
    click_ids: dict[str, str] | None = None,
) -> StorefrontEvent | None:
    """Upsert visitor + session and append one behavioral event.

    Does NOT commit — the caller (the endpoint) owns the transaction. Returns
    the inserted event, or None if the event name is not whitelisted or the
    ``event_id`` was already seen (duplicate delivery)."""
    if event_name not in ALLOWED_EVENTS:
        return None

    utm = {k: v for k, v in (utm or {}).items() if v} or {}
    derived = derive_source(
        utm=utm,
        click_ids=click_ids,
        referrer=referrer,
        landing_page=landing_page,
        page_url=path,
    )
    visitor = _upsert_visitor(db, visitor_key, utm=utm, derived=derived)
    session = _upsert_session(
        db,
        session_key,
        visitor=visitor,
        landing_page=landing_page,
        referrer=referrer,
        utm=utm,
        user_agent=user_agent,
        ip_hash=ip_hash,
    )

    # Dedup: if this event_id was already delivered, keep the visitor/session
    # touch above but skip the duplicate row.
    if event_id:
        existing = db.execute(
            select(StorefrontEvent.id).where(StorefrontEvent.event_id == event_id)
        ).scalar_one_or_none()
        if existing is not None:
            return None

    event = StorefrontEvent(
        visitor_id=visitor.id if visitor is not None else None,
        session_id=session.id if session is not None else None,
        event_name=event_name,
        event_id=event_id,
        path=path,
        referrer=referrer,
        utm=utm,
        listing_code=listing_code,
        vehicle_catalog_item_id=_resolve_vehicle_id(
            db, vehicle_id=vehicle_id, listing_code=listing_code
        ),
        source=derived["source"],
        medium=derived["medium"],
        click_id=derived["click_id"],
        event_metadata=metadata or {},
        occurred_at=_now(),
    )
    db.add(event)
    try:
        # Nested SAVEPOINT so a race on the unique event_id index rolls back
        # only this insert, not the visitor/session upserts.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        return None
    return event


def attach_lead_attribution(
    db: Session, *, crm_event_id: int, ctx: TrackingContext
) -> None:
    """Record the ``lead_submitted`` conversion event and the 1:1
    ``lead_attribution`` bridge for a freshly created/updated deal.

    Fully best-effort and SAVEPOINT-isolated: any failure here is logged and
    swallowed so it can never roll back or fail the customer's lead."""
    if not ctx.has_signal():
        return
    try:
        with db.begin_nested():
            click_ids = {
                "fbclid": ctx.fbclid or _fbclid_from_fbc(ctx.fbc),
                "gclid": ctx.gclid,
                "msclkid": ctx.msclkid,
            }
            derived = derive_source(
                utm=ctx.utm,
                click_ids=click_ids,
                referrer=ctx.referrer,
                landing_page=ctx.landing_page,
                page_url=ctx.source_page,
            )
            visitor = _upsert_visitor(db, ctx.visitor_key, utm=ctx.utm, derived=derived)
            # Last-resort recovery: an untagged submission from a visitor whose
            # FIRST touch was tagged inherits that first touch (the in-app
            # browser dropped the URL params, not the history we stored).
            if not derived["source"] and visitor is not None:
                first_touch = visitor.first_touch_attribution or {}
                derived = {
                    "source": _clean_token(first_touch.get("source")),
                    "medium": _clean_token(first_touch.get("medium")),
                    "click_id": (first_touch.get("click_id") or None),
                }
            session = db.execute(
                select(StorefrontSession).where(
                    StorefrontSession.session_key == ctx.session_key
                )
            ).scalar_one_or_none() if ctx.session_key else None

            conversion_event = record_event(
                db,
                visitor_key=ctx.visitor_key,
                session_key=ctx.session_key,
                event_name=_CONVERSION_EVENT,
                event_id=ctx.event_id,
                path=ctx.source_page,
                referrer=ctx.referrer,
                utm=ctx.utm,
                listing_code=ctx.listing_code,
                vehicle_id=ctx.vehicle_id,
                metadata={"crm_event_id": crm_event_id},
                landing_page=ctx.landing_page,
                click_ids=click_ids,
            )

            attribution = db.execute(
                select(LeadAttribution).where(
                    LeadAttribution.event_id == crm_event_id
                )
            ).scalar_one_or_none()
            if attribution is None:
                attribution = LeadAttribution(
                    event_id=crm_event_id,
                    visitor_id=visitor.id if visitor is not None else None,
                    session_id=session.id if session is not None else None,
                    conversion_storefront_event_id=(
                        conversion_event.id if conversion_event is not None else None
                    ),
                    landing_page=ctx.landing_page,
                    source_page=ctx.source_page,
                    utm=ctx.utm or {},
                    referrer=ctx.referrer,
                    fbp=ctx.fbp,
                    fbc=ctx.fbc,
                    source=derived["source"],
                    medium=derived["medium"],
                    click_id=derived["click_id"],
                )
                db.add(attribution)
            else:
                # Repeat submission on the same deal: refresh the conversion
                # pointer and backfill anything we didn't have first time.
                if conversion_event is not None:
                    attribution.conversion_storefront_event_id = conversion_event.id
                attribution.visitor_id = attribution.visitor_id or (
                    visitor.id if visitor is not None else None
                )
                attribution.session_id = attribution.session_id or (
                    session.id if session is not None else None
                )
                attribution.landing_page = attribution.landing_page or ctx.landing_page
                attribution.source_page = attribution.source_page or ctx.source_page
                attribution.referrer = attribution.referrer or ctx.referrer
                attribution.fbp = attribution.fbp or ctx.fbp
                attribution.fbc = attribution.fbc or ctx.fbc
                if ctx.utm and not attribution.utm:
                    attribution.utm = ctx.utm
                # First-touch preserved: only fill the channel if we never had
                # one for this deal.
                if not attribution.source and derived["source"]:
                    attribution.source = derived["source"]
                    attribution.medium = attribution.medium or derived["medium"]
                attribution.click_id = attribution.click_id or derived["click_id"]
    except Exception:  # pragma: no cover — attribution must never fail a lead
        log.exception("lead attribution failed crm_event_id=%s", crm_event_id)


def record_milestone(
    db: Session,
    *,
    event_name: str,
    crm_event_id: int,
    amount_cents: int | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StorefrontEvent | None:
    """Append a SERVER-ONLY funnel milestone (e.g. ``payment_received``) for a
    deal, inheriting the deal's first-touch attribution so downstream revenue
    groups under the channel that produced the lead — months later.

    Idempotent: deduped on ``dedupe_key`` (stored in metadata), or on
    ``(event_name, crm_event_id)`` when no key is given, because these hooks
    fire from retry-prone request paths. Best-effort and SAVEPOINT-isolated —
    a telemetry write must never fail the payment it rode in with. Does NOT
    commit; the caller owns the transaction.

    A deal with no attribution row still gets its milestone recorded with a
    null source — honestly unknown beats fabricated.
    """
    if event_name not in SERVER_EVENTS:
        log.warning("record_milestone rejected non-server event %r", event_name)
        return None
    try:
        with db.begin_nested():
            existing = select(StorefrontEvent.id).where(
                StorefrontEvent.event_name == event_name
            )
            if dedupe_key:
                existing = existing.where(
                    StorefrontEvent.event_metadata["dedupe_key"].astext == dedupe_key
                )
            else:
                existing = existing.where(
                    StorefrontEvent.event_metadata["crm_event_id"].astext
                    == str(crm_event_id)
                )
            if db.execute(existing.limit(1)).scalar_one_or_none() is not None:
                return None

            attribution = db.execute(
                select(LeadAttribution).where(
                    LeadAttribution.event_id == crm_event_id
                )
            ).scalar_one_or_none()

            event_metadata: dict[str, Any] = dict(metadata or {})
            event_metadata["crm_event_id"] = crm_event_id
            if dedupe_key:
                event_metadata["dedupe_key"] = dedupe_key
            if amount_cents is not None:
                event_metadata["amount_cents"] = int(amount_cents)

            event = StorefrontEvent(
                visitor_id=attribution.visitor_id if attribution else None,
                session_id=attribution.session_id if attribution else None,
                event_name=event_name,
                utm=(attribution.utm or {}) if attribution else {},
                source=attribution.source if attribution else None,
                medium=attribution.medium if attribution else None,
                click_id=attribution.click_id if attribution else None,
                event_metadata=event_metadata,
                occurred_at=_now(),
            )
            db.add(event)
            db.flush()
            return event
    except Exception:  # pragma: no cover — milestones must never fail money
        log.exception(
            "record_milestone failed event_name=%s crm_event_id=%s",
            event_name,
            crm_event_id,
        )
        return None


# ---------------------------------------------------------------------------
# Aggregate reporting — the whole dashboard is GROUP BY over one stream.
# ---------------------------------------------------------------------------

def _local_day(column):
    """SQL expression: calendar date of ``column`` in the shop's timezone, so
    "today" matches the owner's clock, not UTC."""
    return func.date(func.timezone(APP_TIMEZONE, column))


_FUNNEL_ORDER = (
    "page_view",
    "vehicle_view",
    "lead_form_opened",
    "lead_form_started",
    "lead_submitted",
    "payment_received",
)


def summary(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Aggregate storefront analytics for the admin dashboard: funnel counts,
    leads and revenue by channel, daily traffic, and most-viewed vehicles.
    Read-only; all windows are the trailing ``days`` (bounded 1–365)."""
    days = max(1, min(int(days), 365))
    since = _now() - timedelta(days=days)
    ev = StorefrontEvent
    in_window = ev.occurred_at >= since

    # --- funnel + uniques ---------------------------------------------------
    funnel_counts = dict(
        db.execute(
            select(ev.event_name, func.count(ev.id))
            .where(in_window)
            .group_by(ev.event_name)
        ).all()
    )
    unique_visitors = db.execute(
        select(func.count(func.distinct(ev.visitor_id))).where(
            in_window, ev.visitor_id.is_not(None)
        )
    ).scalar_one()
    unique_sessions = db.execute(
        select(func.count(func.distinct(ev.session_id))).where(
            in_window, ev.session_id.is_not(None)
        )
    ).scalar_one()

    src = func.coalesce(ev.source, "(direct)")
    med = func.coalesce(ev.medium, "(none)")
    amount = cast(ev.event_metadata["amount_cents"].astext, BigInteger)

    # --- traffic by channel -------------------------------------------------
    traffic_by_source = [
        {
            "source": row.source,
            "medium": row.medium,
            "page_views": row.page_views,
            "visitors": row.visitors,
        }
        for row in db.execute(
            select(
                src.label("source"),
                med.label("medium"),
                func.count(ev.id).label("page_views"),
                func.count(func.distinct(ev.visitor_id)).label("visitors"),
            )
            .where(in_window, ev.event_name == "page_view")
            .group_by(src, med)
            .order_by(func.count(ev.id).desc())
            .limit(15)
        ).all()
    ]

    # --- leads by channel (first-touch, from the attribution bridge) --------
    la = LeadAttribution
    la_src = func.coalesce(la.source, "(direct)")
    la_med = func.coalesce(la.medium, "(none)")
    leads_by_source = [
        {"source": row.source, "medium": row.medium, "leads": row.leads}
        for row in db.execute(
            select(
                la_src.label("source"),
                la_med.label("medium"),
                func.count(la.id).label("leads"),
            )
            .where(la.created_at >= since)
            .group_by(la_src, la_med)
            .order_by(func.count(la.id).desc())
            .limit(15)
        ).all()
    ]

    # --- revenue by channel — payment_received inherited first-touch --------
    revenue_by_source = [
        {
            "source": row.source,
            "medium": row.medium,
            "payments": row.payments,
            "revenue_cents": int(row.revenue_cents or 0),
        }
        for row in db.execute(
            select(
                src.label("source"),
                med.label("medium"),
                func.count(ev.id).label("payments"),
                func.coalesce(func.sum(amount), 0).label("revenue_cents"),
            )
            .where(in_window, ev.event_name == "payment_received")
            .group_by(src, med)
            .order_by(func.coalesce(func.sum(amount), 0).desc())
            .limit(15)
        ).all()
    ]
    total_revenue_cents = int(
        db.execute(
            select(func.coalesce(func.sum(amount), 0)).where(
                in_window, ev.event_name == "payment_received"
            )
        ).scalar_one()
        or 0
    )

    # --- daily series (shop-local days) -------------------------------------
    day = _local_day(ev.occurred_at)
    daily_rows = db.execute(
        select(
            day.label("day"),
            ev.event_name,
            func.count(ev.id).label("n"),
        )
        .where(in_window, ev.event_name.in_(("page_view", "vehicle_view", "lead_submitted")))
        .group_by(day, ev.event_name)
        .order_by(day)
    ).all()
    daily: dict[str, dict[str, int]] = {}
    for row in daily_rows:
        bucket = daily.setdefault(
            row.day.isoformat(),
            {"page_view": 0, "vehicle_view": 0, "lead_submitted": 0},
        )
        bucket[row.event_name] = row.n
    daily_series = [
        {"day": d, **counts} for d, counts in sorted(daily.items())
    ]

    # --- most-viewed vehicles ----------------------------------------------
    top_vehicle_rows = db.execute(
        select(
            ev.vehicle_catalog_item_id,
            func.count(ev.id).label("views"),
            func.count(func.distinct(ev.visitor_id)).label("visitors"),
        )
        .where(
            in_window,
            ev.event_name == "vehicle_view",
            ev.vehicle_catalog_item_id.is_not(None),
        )
        .group_by(ev.vehicle_catalog_item_id)
        .order_by(func.count(ev.id).desc())
        .limit(10)
    ).all()
    labels: dict[int, str] = {}
    codes: dict[int, str | None] = {}
    if top_vehicle_rows:
        for item in db.execute(
            select(
                CatalogItem.id,
                CatalogItem.public_code,
                CatalogItem.year,
                CatalogItem.make,
                CatalogItem.model,
            ).where(
                CatalogItem.id.in_([r.vehicle_catalog_item_id for r in top_vehicle_rows])
            )
        ).all():
            labels[item.id] = _catalog_label(item) or f"#{item.id}"
            codes[item.id] = item.public_code
    top_vehicles = [
        {
            "vehicle_catalog_item_id": row.vehicle_catalog_item_id,
            "label": labels.get(row.vehicle_catalog_item_id),
            "listing_code": codes.get(row.vehicle_catalog_item_id),
            "views": row.views,
            "visitors": row.visitors,
        }
        for row in top_vehicle_rows
    ]

    return {
        "days": days,
        "since": _iso(since),
        "uniques": {"visitors": unique_visitors, "sessions": unique_sessions},
        "funnel": [
            {"event_name": name, "count": funnel_counts.get(name, 0)}
            for name in _FUNNEL_ORDER
        ],
        "traffic_by_source": traffic_by_source,
        "leads_by_source": leads_by_source,
        "revenue_by_source": revenue_by_source,
        "total_revenue_cents": total_revenue_cents,
        "daily": daily_series,
        "top_vehicles": top_vehicles,
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


# Public listing codes appearing in a URL path, e.g. "/inventory/KAP-00024".
_PUBLIC_CODE_RE = re.compile(r"[A-Z]{2,5}-\d{3,6}")


def _catalog_label(row) -> str | None:
    return (
        " ".join(str(x) for x in (row.year, row.make, row.model) if x).strip() or None
    )


def _resolve_vehicle_labels(
    db: Session, events: list[StorefrontEvent]
) -> tuple[dict[int, str], dict[str, str]]:
    """Build id→"YEAR MAKE MODEL" and code→label maps for every vehicle
    referenced anywhere in the journey (by catalog id, listing_code, or a code
    embedded in a page path), in one query. Lets the panel show the car itself
    instead of a KAP stock number."""
    ids = {e.vehicle_catalog_item_id for e in events if e.vehicle_catalog_item_id}
    codes: set[str] = set()
    for e in events:
        if e.listing_code:
            codes.add(e.listing_code.upper())
        if e.path:
            codes.update(_PUBLIC_CODE_RE.findall(e.path.upper()))

    by_id: dict[int, str] = {}
    by_code: dict[str, str] = {}
    if not ids and not codes:
        return by_id, by_code

    conds = []
    if ids:
        conds.append(CatalogItem.id.in_(ids))
    if codes:
        conds.append(CatalogItem.public_code.in_(codes))
    rows = db.execute(
        select(
            CatalogItem.id,
            CatalogItem.public_code,
            CatalogItem.year,
            CatalogItem.make,
            CatalogItem.model,
        ).where(or_(*conds))
    ).all()
    for r in rows:
        label = _catalog_label(r)
        if not label:
            continue
        by_id[r.id] = label
        if r.public_code:
            by_code[r.public_code.upper()] = label
    return by_id, by_code


def _event_vehicle_label(
    e: StorefrontEvent, by_id: dict[int, str], by_code: dict[str, str]
) -> str | None:
    if e.vehicle_catalog_item_id and e.vehicle_catalog_item_id in by_id:
        return by_id[e.vehicle_catalog_item_id]
    if e.listing_code and e.listing_code.upper() in by_code:
        return by_code[e.listing_code.upper()]
    if e.path:
        for code in _PUBLIC_CODE_RE.findall(e.path.upper()):
            if code in by_code:
                return by_code[code]
    return None


def get_lead_journey(db: Session, *, crm_event_id: int) -> dict[str, Any]:
    """Read-only browsing journey for a deal, for the admin Lead Journey panel.

    Returns source/UTM, the session, the vehicles viewed, and the ordered path
    to conversion. Contains NO application PII — only first-party behavioral
    data. ``has_attribution`` is False when the lead arrived without any
    storefront tracking (e.g. a phone-in logged as a deal)."""
    attribution = db.execute(
        select(LeadAttribution).where(LeadAttribution.event_id == crm_event_id)
    ).scalar_one_or_none()
    if attribution is None:
        return {"has_attribution": False}

    source = {
        "utm": attribution.utm or {},
        "referrer": attribution.referrer,
        "landing_page": attribution.landing_page,
        "source_page": attribution.source_page,
    }

    session_info: dict[str, Any] | None = None
    if attribution.session_id is not None:
        s = db.get(StorefrontSession, attribution.session_id)
        if s is not None:
            session_info = {
                "started_at": _iso(s.started_at),
                "last_seen_at": _iso(s.last_seen_at),
                "landing_page": s.landing_page,
                "initial_referrer": s.initial_referrer,
                "user_agent": s.user_agent,
            }

    events: list[StorefrontEvent] = []
    if attribution.visitor_id is not None:
        events = list(
            db.execute(
                select(StorefrontEvent)
                .where(StorefrontEvent.visitor_id == attribution.visitor_id)
                .order_by(StorefrontEvent.occurred_at.asc(), StorefrontEvent.id.asc())
            ).scalars()
        )

    by_id, by_code = _resolve_vehicle_labels(db, events)

    path = [
        {
            "event_name": e.event_name,
            "path": e.path,
            "listing_code": e.listing_code,
            # The car itself, when this step is on/about a vehicle — so the UI
            # can show "2018 Nissan Altima" instead of "/inventory/KAP-00026".
            "vehicle_label": _event_vehicle_label(e, by_id, by_code),
            "occurred_at": _iso(e.occurred_at),
        }
        for e in events
    ]

    # Unique vehicles viewed, first-seen order. Prefer the year/make/model the
    # beacon captured; fall back to the catalog lookup.
    vehicles_viewed: list[dict[str, Any]] = []
    seen_vehicles: set[str] = set()
    for e in events:
        if e.event_name != "vehicle_view":
            continue
        key = str(e.vehicle_catalog_item_id or e.listing_code or e.id)
        if key in seen_vehicles:
            continue
        seen_vehicles.add(key)
        meta = e.event_metadata or {}
        label = (
            " ".join(
                str(meta[k])
                for k in ("vehicle_year", "vehicle_make", "vehicle_model")
                if meta.get(k)
            ).strip()
            or _event_vehicle_label(e, by_id, by_code)
        )
        vehicles_viewed.append(
            {
                "label": label,
                "vehicle_catalog_item_id": e.vehicle_catalog_item_id,
                "occurred_at": _iso(e.occurred_at),
            }
        )

    # Time from the first recorded touch to the lead_submitted conversion.
    converted_at = None
    if attribution.conversion_storefront_event_id is not None:
        conv = db.get(StorefrontEvent, attribution.conversion_storefront_event_id)
        if conv is not None:
            converted_at = conv.occurred_at
    if converted_at is None:
        for e in reversed(events):
            if e.event_name == _CONVERSION_EVENT:
                converted_at = e.occurred_at
                break

    minutes_to_convert = None
    if events and converted_at is not None:
        delta = converted_at - events[0].occurred_at
        minutes_to_convert = round(delta.total_seconds() / 60.0, 1)

    return {
        "has_attribution": True,
        "source": source,
        "session": session_info,
        "vehicles_viewed": vehicles_viewed,
        "path": path,
        "event_count": len(events),
        "converted_at": _iso(converted_at),
        "minutes_to_convert": minutes_to_convert,
    }
