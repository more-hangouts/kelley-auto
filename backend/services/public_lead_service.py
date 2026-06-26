"""Public lead intake — Day 4.

Turns a customer inquiry from the marketing site into a CRM ``vehicle_sale``
deal, reusing the same contact dedup and event-creation paths the booking /
walk-in flows use. This is the bridge from public inventory browsing to the
Day 3 deal pipeline.

Privacy: callers get a fixed acknowledgement only. This module never reveals
whether the contact already existed or whether a matching deal was found —
the router returns the same message on every path.

Vehicle linking is deliberately STRICT: a lead links only to a still-for-
sale car (available/pending, via ``resolve_linkable_vehicle``). A reference
to a car that has since gone sold/hidden/wholesale/inactive — or a bogus or
non-vehicle ref — degrades to a general (unlinked) lead instead of crashing
or rejecting, so a stale tab never costs a real inquiry. The original ref is
still recorded in the activity payload so staff can see what the customer
was looking at.

Dedup mirrors the Day 3 rule: one OPEN ``vehicle_sale`` deal per (contact,
vehicle) — or per contact for general leads. A duplicate appends a
``lead.public_submitted`` activity to the existing deal instead of spawning
a second board card.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import BusinessProfile, Event, User
from services import (
    activity_log,
    booking_service,
    contact_service,
    event_service,
    public_inventory_service,
)
from services.email_transport import send_rendered_safely
from services.event_service import EventOverrides
from services.event_workflow import all_statuses

log = logging.getLogger(__name__)

_VEHICLE_SALE = "vehicle_sale"


def _lead_notify_recipients(db: Session) -> list[str]:
    """Staff recipients for a lead alert. Explicit env override wins; else the
    business profile's contact email; else every active admin user's email."""
    from config.settings import PUBLIC_LEAD_NOTIFY_EMAILS

    if PUBLIC_LEAD_NOTIFY_EMAILS:
        return list(PUBLIC_LEAD_NOTIFY_EMAILS)
    profile = db.query(BusinessProfile).first()
    if profile is not None and profile.email:
        return [profile.email]
    rows = (
        db.query(User)
        .filter(User.role == "admin")
        .filter(User.is_active.is_(True))
        .all()
    )
    return [u.email for u in rows if u.email]


def _notify_staff_of_lead(
    db: Session,
    *,
    is_new: bool,
    contact: Any,
    vehicle: Any,
    payload: dict[str, Any],
    deal_id: int,
) -> None:
    """Best-effort staff email when a lead lands. Never raises: a broken
    mailer (or no configured recipient) must not fail the customer's
    submission. ``send_rendered_safely`` already swallows SMTP errors; the
    outer try also covers recipient lookup / template rendering."""
    try:
        recipients = _lead_notify_recipients(db)
        if not recipients:
            log.info("public_lead.notify_skipped_no_recipient")
            return
        from config.settings import ADMIN_BASE_URL
        from services import notification_templates

        rendered = notification_templates.render_public_lead_notification(
            is_new=is_new,
            contact=contact,
            vehicle=vehicle,
            payload=payload,
            admin_url=f"{ADMIN_BASE_URL}/events/{deal_id}",
        )
        for to in recipients:
            send_rendered_safely(
                to=to, rendered=rendered, scope="public_lead.received"
            )
    except Exception:
        log.exception(
            "public_lead.notify_failed (lead still recorded, deal_id=%s)",
            deal_id,
        )


class PublicLeadError(Exception):
    """Domain rejection surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str = "public_lead_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class LeadInput:
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    vehicle_ref: str | None = None
    message: str | None = None
    preferred_day: str | None = None
    preferred_time: str | None = None
    source_page: str | None = None
    utm: dict[str, str] = field(default_factory=dict)


def _open_vehicle_sale_statuses() -> set[str]:
    # Non-terminal columns = the deal is still live (delivered/lost are
    # terminal). Derived from the workflow so it tracks any future column.
    return {s.code for s in all_statuses(_VEHICLE_SALE) if not s.is_terminal}


def _split_name(name: str | None) -> tuple[str | None, str | None]:
    parts = (name or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _compose_notes(lead: LeadInput, *, ref_requested_but_unlinked: bool) -> str | None:
    lines: list[str] = []
    if lead.message and lead.message.strip():
        lines.append(lead.message.strip())
    pref = " ".join(
        p for p in (lead.preferred_day, lead.preferred_time) if p and p.strip()
    ).strip()
    if pref:
        lines.append(f"Preferred contact: {pref}")
    if ref_requested_but_unlinked:
        lines.append("(Inquiry referenced a vehicle that is no longer available.)")
    return "\n".join(lines) or None


def submit_public_lead(db: Session, lead: LeadInput) -> Event:
    """Create or reuse a ``vehicle_sale`` deal for this inquiry. Returns the
    Event (for the smoke / internal callers); the router discards it and
    returns a generic acknowledgement. Caller owns the commit.
    """
    raw_phone = (lead.phone or "").strip() or None
    phone_e164 = (
        booking_service.normalize_phone_e164(raw_phone) if raw_phone else None
    )
    email = (lead.email or "").strip().lower() or None
    # At least one *usable* identity key is required. A raw phone we can't
    # normalize (and no email) gives us nothing to dedup on, so reject —
    # the router maps this to 422.
    if not phone_e164 and not email:
        raise PublicLeadError(
            "a usable phone number or email is required",
            code="missing_contact_info",
        )

    first, last = _split_name(lead.name)
    # was_new intentionally ignored — never surfaced to the caller.
    contact, _was_new = contact_service.find_or_create_contact(
        db,
        phone_e164=phone_e164,
        email=email,
        phone=raw_phone,
        first_name=first,
        last_name=last,
    )

    vehicle = (
        public_inventory_service.resolve_linkable_vehicle(db, lead.vehicle_ref)
        if lead.vehicle_ref
        else None
    )
    link_id = vehicle.id if vehicle is not None else None
    ref_requested_but_unlinked = bool(lead.vehicle_ref) and vehicle is None

    payload: dict[str, Any] = {
        "source": "public_site",
        "source_page": lead.source_page,
        "utm": lead.utm or {},
        "message": lead.message,
        "preferred_day": lead.preferred_day,
        "preferred_time": lead.preferred_time,
        "vehicle_ref_requested": lead.vehicle_ref,
        "vehicle_catalog_item_id": link_id,
        "vehicle_listing_code": vehicle.public_code if vehicle is not None else None,
        "linked": link_id is not None,
    }

    # Dedup: one open vehicle_sale per (contact, vehicle) — or per contact
    # for a general (unlinked) lead.
    stmt = (
        select(Event)
        .where(
            Event.event_type == _VEHICLE_SALE,
            Event.primary_contact_id == contact.id,
            Event.deleted_at.is_(None),
            Event.status.in_(_open_vehicle_sale_statuses()),
        )
        .order_by(Event.id.desc())
    )
    if link_id is not None:
        stmt = stmt.where(Event.vehicle_catalog_item_id == link_id)
    else:
        stmt = stmt.where(Event.vehicle_catalog_item_id.is_(None))
    existing = db.execute(stmt).scalars().first()

    if existing is not None:
        activity_log.log_activity(
            db,
            event_id=existing.id,
            actor_kind="customer",
            actor_user_id=None,
            activity_type=activity_log.PUBLIC_LEAD_SUBMITTED,
            subject_kind="event",
            subject_id=existing.id,
            payload=payload,
        )
        db.flush()
        _notify_staff_of_lead(
            db,
            is_new=False,
            contact=contact,
            vehicle=vehicle,
            payload=payload,
            deal_id=existing.id,
        )
        return existing

    event_name = None
    if vehicle is not None:
        ymm = " ".join(
            str(x) for x in (vehicle.year, vehicle.make, vehicle.model) if x
        ).strip()
        event_name = (f"{ymm} — {contact.display_name}".strip(" —")) or None

    event = event_service.create_walk_in_event(
        db,
        contact_id=contact.id,
        event_type=_VEHICLE_SALE,
        overrides=EventOverrides(
            event_name=event_name,
            notes=_compose_notes(
                lead, ref_requested_but_unlinked=ref_requested_but_unlinked
            ),
            vehicle_catalog_item_id=link_id,
        ),
        actor_user_id=None,
    )
    activity_log.log_activity(
        db,
        event_id=event.id,
        actor_kind="customer",
        actor_user_id=None,
        activity_type=activity_log.PUBLIC_LEAD_SUBMITTED,
        subject_kind="event",
        subject_id=event.id,
        payload=payload,
    )
    db.flush()
    _notify_staff_of_lead(
        db,
        is_new=True,
        contact=contact,
        vehicle=vehicle,
        payload=payload,
        deal_id=event.id,
    )
    return event
