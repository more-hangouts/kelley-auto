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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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

_CONVERSION_EVENT = "lead_submitted"


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
    listing_code: str | None = None
    vehicle_id: int | None = None

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
    db: Session, visitor_key: str | None, *, utm: dict[str, str] | None
) -> StorefrontVisitor | None:
    if not visitor_key:
        return None
    visitor = db.execute(
        select(StorefrontVisitor).where(StorefrontVisitor.visitor_key == visitor_key)
    ).scalar_one_or_none()
    now = _now()
    if visitor is None:
        visitor = StorefrontVisitor(
            visitor_key=visitor_key,
            first_seen_at=now,
            last_seen_at=now,
            first_touch_attribution=utm or {},
            last_touch_attribution=utm or {},
        )
        db.add(visitor)
        db.flush()
    else:
        visitor.last_seen_at = now
        if utm:
            visitor.last_touch_attribution = utm
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
) -> StorefrontEvent | None:
    """Upsert visitor + session and append one behavioral event.

    Does NOT commit — the caller (the endpoint) owns the transaction. Returns
    the inserted event, or None if the event name is not whitelisted or the
    ``event_id`` was already seen (duplicate delivery)."""
    if event_name not in ALLOWED_EVENTS:
        return None

    utm = {k: v for k, v in (utm or {}).items() if v} or {}
    visitor = _upsert_visitor(db, visitor_key, utm=utm)
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
            visitor = _upsert_visitor(db, ctx.visitor_key, utm=ctx.utm)
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
    except Exception:  # pragma: no cover — attribution must never fail a lead
        log.exception("lead attribution failed crm_event_id=%s", crm_event_id)


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
