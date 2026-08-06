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
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import Appointment, BusinessProfile, Event, User
from modules.core.services import activity_log
from modules.contacts.services import contact_service, lead_application_service
from modules.booking.services import booking_service, event_service
from modules.inventory.services import public_inventory_service
from modules.analytics.services import meta_capi_service, storefront_analytics_service
from modules.analytics.services.storefront_analytics_service import TrackingContext
from modules.core.services import email_transport
from modules.core.services.email_transport import send_rendered_safely
from modules.booking.services.event_service import EventOverrides
from modules.booking.services.event_workflow import all_statuses
from modules.contacts.services.lead_application_service import ApplicationInput

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
    has_application: bool = False,
) -> None:
    """Staff email when a lead lands. Never raises — a broken mailer must not
    fail the customer's submission — but the outcome is no longer silent: it
    is recorded as a ``lead.notification_sent`` / ``lead.notification_failed``
    audit row on the deal (visible in the admin timeline) and a failed alert
    logs at ERROR. A new lead nobody was told about is an operational
    incident, not a harmless best-effort miss."""
    transport = email_transport.active_email_transport_kind()
    recipients: list[str] = []
    delivered: list[str] = []
    reason: str | None = None
    try:
        recipients = _lead_notify_recipients(db)
        if not recipients:
            reason = "no_recipient_configured"
        elif not email_transport.email_delivery_enabled():
            # Transport only logs (NullEmailTransport) — mail never leaves.
            reason = "delivery_disabled_null_transport"
        else:
            from config.settings import ADMIN_BASE_URL
            from modules.core.services import notification_templates

            rendered = notification_templates.render_public_lead_notification(
                is_new=is_new,
                contact=contact,
                vehicle=vehicle,
                payload=payload,
                admin_url=f"{ADMIN_BASE_URL}/events/{deal_id}",
                has_application=has_application,
            )
            for to in recipients:
                if send_rendered_safely(
                    to=to, rendered=rendered, scope="public_lead.received"
                ):
                    delivered.append(to)
            if not delivered:
                reason = "all_sends_failed"
    except Exception as exc:  # noqa: BLE001
        reason = f"exception:{type(exc).__name__}"
        log.exception(
            "public_lead.notify_failed (lead still recorded, deal_id=%s)",
            deal_id,
        )

    _record_notification_outcome(
        db,
        deal_id=deal_id,
        transport=transport,
        recipients=recipients,
        delivered=delivered,
        reason=reason,
    )


def _record_notification_outcome(
    db: Session,
    *,
    deal_id: int,
    transport: str,
    recipients: list[str],
    delivered: list[str],
    reason: str | None,
) -> None:
    """Append a notification outcome row so a missed lead alert is visible in
    the deal timeline and grep-able in logs, rather than hiding behind the
    'sent' of the submission. Best-effort itself — an audit-write failure must
    not bubble into the customer's request."""
    ok = reason is None
    payload = {
        "transport": transport,
        "recipients": recipients,
        "delivered": delivered,
        "reason": reason,
    }
    if not ok:
        log.error(
            "public_lead.notification_failed deal_id=%s transport=%s reason=%s "
            "recipients=%s",
            deal_id,
            transport,
            reason,
            recipients,
        )
    try:
        activity_log.log_activity(
            db,
            event_id=deal_id,
            actor_kind="system",
            actor_user_id=None,
            activity_type=(
                activity_log.LEAD_NOTIFICATION_SENT
                if ok
                else activity_log.LEAD_NOTIFICATION_FAILED
            ),
            subject_kind="event",
            subject_id=deal_id,
            payload=payload,
        )
        db.flush()
    except Exception:  # noqa: BLE001
        log.exception(
            "public_lead.notification_outcome_unrecorded deal_id=%s", deal_id
        )


def _send_customer_confirmation(
    db: Session, *, contact: Any, vehicle: Any, deal_id: int
) -> None:
    """Send the customer a confirmation that we received their request.

    Distinct from the staff alert. Fully best-effort — no email address, a
    disabled transport, or a mailer failure must never fail the lead — and the
    outcome is recorded on the deal timeline (lead.confirmation_sent/_failed).
    """
    email = (getattr(contact, "email", None) or "").strip()
    if not email or not email_transport.email_delivery_enabled():
        return

    ok = False
    try:
        from modules.core.services import notification_templates

        vehicle_label = None
        if vehicle is not None:
            vehicle_label = (
                " ".join(
                    str(x) for x in (vehicle.year, vehicle.make, vehicle.model) if x
                ).strip()
                or None
            )
        profile = db.query(BusinessProfile).first()
        rendered = notification_templates.render_lead_confirmation(
            customer_name=contact.display_name,
            vehicle_label=vehicle_label,
            profile=profile,
        )
        ok = send_rendered_safely(
            to=email, rendered=rendered, scope="public_lead.confirmation"
        )
    except Exception:  # noqa: BLE001 — confirmation is best-effort
        log.exception("public_lead.confirmation_failed deal_id=%s", deal_id)

    try:
        activity_log.log_activity(
            db,
            event_id=deal_id,
            actor_kind="system",
            actor_user_id=None,
            activity_type=(
                activity_log.LEAD_CONFIRMATION_SENT
                if ok
                else activity_log.LEAD_CONFIRMATION_FAILED
            ),
            subject_kind="event",
            subject_id=deal_id,
            # Minimize PII in the audit payload — the email already lives on the
            # contact record; the timeline only needs the delivery domain.
            payload={"to_domain": email.rsplit("@", 1)[-1] if "@" in email else None},
        )
        db.flush()
    except Exception:  # noqa: BLE001
        log.exception(
            "public_lead.confirmation_outcome_unrecorded deal_id=%s", deal_id
        )


_REQUESTED_APPOINTMENT_DURATION_MINUTES = 30


def _create_requested_appointment(
    db: Session, *, contact: Any, event: Event, lead: LeadInput
) -> Appointment | None:
    """Turn a customer's requested time into a PENDING appointment on the deal.

    Pending (not confirmed) — staff still call to confirm — but it lands on the
    calendar and the deal at the requested slot instead of hiding in a note.
    Best-effort: a bad/absent time never fails the lead. Deduped so a repeat
    submit for the same slot doesn't stack appointments."""
    if not lead.preferred_date or lead.preferred_hour is None:
        return None
    try:
        year, month, day = (int(p) for p in lead.preferred_date.split("-"))
        start_at = datetime(
            year, month, day, int(lead.preferred_hour), 0, tzinfo=ZoneInfo(APP_TIMEZONE)
        )
    except (ValueError, TypeError):
        log.warning(
            "public_lead: unparseable preferred slot date=%r hour=%r",
            lead.preferred_date,
            lead.preferred_hour,
        )
        return None

    # Dedup: an existing live appointment on this deal at this slot.
    already = db.execute(
        select(Appointment.id).where(
            Appointment.crm_event_id == event.id,
            Appointment.slot_start_at == start_at,
            Appointment.status.in_(("pending", "confirmed")),
        )
    ).first()
    if already is not None:
        return None

    first = (contact.first_name or (contact.display_name or "Customer").split()[0])[:100]
    appt = Appointment(
        confirmation_code=booking_service.generate_unique_confirmation_code(db),
        slot_start_at=start_at,
        slot_end_at=start_at
        + timedelta(minutes=_REQUESTED_APPOINTMENT_DURATION_MINUTES),
        slot_duration_minutes=_REQUESTED_APPOINTMENT_DURATION_MINUTES,
        timezone=APP_TIMEZONE,
        celebrant_first_name=first,
        celebrant_last_name=(contact.last_name or None),
        # party size is meaningless for a vehicle visit; 'solo' is the neutral
        # NOT-NULL placeholder the CHECK allows.
        party_size_bucket="solo",
        phone=(contact.phone or contact.phone_e164 or ""),
        phone_e164=contact.phone_e164,
        # email is NOT NULL; fall back to a routable-looking placeholder like
        # the walk-in flow when the lead only left a phone.
        email=(contact.email or f"lead+{contact.id}@lead.local"),
        status="pending",
        contact_id=contact.id,
        crm_event_id=event.id,
        internal_notes="Requested via storefront — call to confirm.",
        raw_payload={"source": "public_lead"},
    )
    db.add(appt)
    db.flush()
    return appt


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
    # Structured preferred appointment slot (from the storefront time picker):
    # a dealership-local date (YYYY-MM-DD) + hour (0-23). When present, a
    # pending appointment is created on the deal so it lands on the calendar.
    preferred_date: str | None = None
    preferred_hour: int | None = None
    source_page: str | None = None
    utm: dict[str, str] = field(default_factory=dict)
    # Structured BHPH application fields (optional). These NEVER go into
    # events.notes or the activity_log payload — they are encrypted at rest in
    # lead_applications via lead_application_service.
    date_of_birth: str | None = None
    driver_license_number: str | None = None
    driver_license_state: str | None = None
    has_driver_license: bool | None = None
    address: dict[str, str] | None = None
    # A2P 10DLC: the customer actively checked the optional SMS-consent box.
    # Recorded on the contact (sms_consent_at/_source); never a submit gate.
    sms_consent: bool = False


def _application_input_from_lead(lead: LeadInput) -> ApplicationInput:
    """Map the BHPH fields off a LeadInput into an ApplicationInput. SSN is not
    collected at intake (reserved for future underwriting)."""
    return ApplicationInput(
        date_of_birth=lead.date_of_birth,
        driver_license_number=lead.driver_license_number,
        driver_license_state=lead.driver_license_state,
        has_driver_license=lead.has_driver_license,
        address=lead.address,
    )


def _open_vehicle_sale_statuses() -> set[str]:
    # Non-terminal columns = the deal is still live (sold/lost are terminal).
    # Derived from the workflow so it tracks any future column.
    return {s.code for s in all_statuses(_VEHICLE_SALE) if not s.is_terminal}


def _split_name(name: str | None) -> tuple[str | None, str | None]:
    parts = (name or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _compose_notes(lead: LeadInput, *, ref_requested_but_unlinked: bool) -> str | None:
    """Deal notes from GENUINE signal only.

    Every storefront lead is BHPH/no-credit-check, so canned lines like "Buy
    here pay here request" are identical for everyone and just noise — those are
    no longer sent. The vehicle of interest is captured structurally (the deal's
    linked vehicle + title) and the requested time becomes a real appointment,
    so notes carry only what the customer actually typed, plus a flag when the
    car they referenced is no longer available."""
    lines: list[str] = []
    if lead.message and lead.message.strip():
        lines.append(lead.message.strip())
    if ref_requested_but_unlinked:
        lines.append("(Inquiry referenced a vehicle that is no longer available.)")
    return "\n".join(lines) or None


def _enqueue_meta_lead(
    db: Session,
    *,
    crm_event_id: int,
    lead: LeadInput,
    vehicle,
    ctx: TrackingContext,
) -> None:
    """Queue the Meta CAPI ``Lead`` twin of this conversion (sender is gated
    by META_CAPI_ENABLED; until then rows just accumulate). Only approved
    matching identifiers leave here — name/email/phone (hashed downstream)
    and ad cookies. Never DOB/DL/address/message."""
    label = None
    if vehicle is not None:
        label = (
            " ".join(str(x) for x in (vehicle.year, vehicle.make, vehicle.model) if x)
            or None
        )
    meta_capi_service.enqueue_lead_conversion(
        db,
        crm_event_id=crm_event_id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        ctx=ctx,
        vehicle_listing_code=vehicle.public_code if vehicle is not None else None,
        vehicle_label=label,
        vehicle_price_cents=vehicle.unit_price_cents if vehicle is not None else None,
    )


def submit_public_lead(
    db: Session, lead: LeadInput, *, tracking: TrackingContext | None = None
) -> Event:
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

    # A2P 10DLC: record express written consent when the customer checked the
    # optional box. First consent wins (the timestamp is the legal record);
    # a prior STOP is NOT cleared here — opt-out stands until the customer
    # texts START themselves.
    if lead.sms_consent and contact.sms_consent_at is None:
        contact.sms_consent_at = datetime.now(timezone.utc)
        contact.sms_consent_source = f"web_form:{lead.source_page or 'unknown'}"[:200]

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
        _app = _application_input_from_lead(lead)
        _has_app = not _app.is_empty()
        if _has_app:
            lead_application_service.upsert_application(
                db,
                event_id=existing.id,
                contact_id=contact.id,
                data=_app,
                actor_kind="system",
            )
        if tracking is not None:
            storefront_analytics_service.attach_lead_attribution(
                db, crm_event_id=existing.id, ctx=tracking
            )
            _enqueue_meta_lead(
                db, crm_event_id=existing.id, lead=lead, vehicle=vehicle, ctx=tracking
            )
        _create_requested_appointment(db, contact=contact, event=existing, lead=lead)
        _send_customer_confirmation(
            db, contact=contact, vehicle=vehicle, deal_id=existing.id
        )
        _notify_staff_of_lead(
            db,
            is_new=False,
            contact=contact,
            vehicle=vehicle,
            payload=payload,
            deal_id=existing.id,
            has_application=_has_app,
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
    _app = _application_input_from_lead(lead)
    _has_app = not _app.is_empty()
    if _has_app:
        lead_application_service.upsert_application(
            db,
            event_id=event.id,
            contact_id=contact.id,
            data=_app,
            actor_kind="system",
        )
    if tracking is not None:
        storefront_analytics_service.attach_lead_attribution(
            db, crm_event_id=event.id, ctx=tracking
        )
        _enqueue_meta_lead(
            db, crm_event_id=event.id, lead=lead, vehicle=vehicle, ctx=tracking
        )
    _create_requested_appointment(db, contact=contact, event=event, lead=lead)
    _send_customer_confirmation(
        db, contact=contact, vehicle=vehicle, deal_id=event.id
    )
    _notify_staff_of_lead(
        db,
        is_new=True,
        contact=contact,
        vehicle=vehicle,
        payload=payload,
        deal_id=event.id,
        has_application=_has_app,
    )
    return event
