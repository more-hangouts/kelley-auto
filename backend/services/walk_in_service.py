"""Walk-in lead capture: in-store / phone leads that mirror widget shape.

Today the only path that creates leads is the public booking widget at
``api/routers/booking.py``. When a customer walks in or calls in, staff
have no in-app affordance to capture them; the workaround has been
to fill the public widget themselves, which awkwardly pollutes
attribution.

This service exists so the admin walk-in flow lands a row shape
indistinguishable from a widget booking: a Contact, a placeholder
Appointment (status='attended', attended_at=NOW), an optional
``AppointmentEnrichmentResponse`` row, and a freshly-promoted Event
in the ``lead`` lane with ``appointment.crm_event_id`` linked back.
That keeps the kanban / pipeline / event tabs identical regardless
of origin — they all expect appointment-backed events.

Design notes:

  - **One transaction, one commit.** The route handler owns the
    ``db.commit()``; this service stays at flush boundaries so a
    later failure rolls everything back together (no orphan contact
    with no event, no event with no audit row).
  - **No legacy enrichment via upsert_profile_for_appointment.** That
    helper takes a ``BoutiqueExperienceSubmission`` (measurements +
    sizing + preferences). Walk-in enrichment is the older survey
    shape (court_size, theme, budget, dress_styles, colors), so we
    write the ``AppointmentEnrichmentResponse`` row directly here
    rather than shoehorning two contracts into one helper.
  - **Phone is identity.** ``normalize_phone_e164`` returning ``None``
    is rejected as ``invalid_phone`` rather than silently weakening
    dedupe. Without phone normalization we'd let two leads with the
    same number end up on different contacts.
  - **Existing-contact name is not mutated.** Staff might pick an
    existing person they recognize and type a new celebrant nickname
    in Step 2; the contact's display_name should not change because
    of that. Only fresh contacts derive their name from the form.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    Appointment,
    AppointmentEnrichmentResponse,
    BusinessProfile,
    Contact,
    Event,
    User,
)
from services import activity_log, booking_service, contact_service, event_service
from services.email_transport import send_rendered_safely
from services.event_service import EventOverrides, EventServiceError

log = logging.getLogger(__name__)


class WalkInLeadError(Exception):
    """Domain-level rejection — the router maps ``.code`` to an HTTP status."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WalkInContactInput:
    first_name: str | None
    last_name: str | None
    display_name: str | None
    email: str | None
    phone: str  # required raw input — server normalizes to E.164


@dataclass(frozen=True)
class WalkInEventInput:
    celebrant_first_name: str
    celebrant_last_name: str | None
    event_name: str | None
    event_date: Any  # datetime.date | None — kept loose so router models stay thin
    owner_user_id: int | None


@dataclass(frozen=True)
class WalkInEnrichmentInput:
    party_size_bucket: str  # 'pair' | '3_4' | '5_plus'
    court_size: int | None
    quince_theme: str | None
    quince_theme_colors: list[str] | None
    budget_range: str | None
    dress_styles: list[str] | None
    colors: list[str] | None
    notes: str | None


@dataclass(frozen=True)
class WalkInLeadResult:
    contact: Contact
    appointment: Appointment
    event: Event
    was_new_contact: bool


_WALK_IN_DURATION_MINUTES = 45
_VALID_PARTY_BUCKETS = ("pair", "3_4", "5_plus")


def create_walk_in_lead(
    db: Session,
    *,
    actor_user_id: int,
    contact_in: WalkInContactInput,
    event_in: WalkInEventInput,
    enrichment_in: WalkInEnrichmentInput,
    assigned_user_id: int | None = None,
) -> WalkInLeadResult:
    """Create Contact + placeholder Appointment + enrichment + Event in one tx.

    The caller owns the commit boundary. Every write below flushes;
    the route handler calls ``db.commit()`` on the way out and rolls
    back on any raised error.

    ``assigned_user_id`` is the unified "this walk-in belongs to stylist
    X" hook. When provided it sets BOTH ``appointments.assigned_user_id``
    AND ``events.owner_user_id`` to the same id, since a sales walk-in
    has one owner and conflating the two fields would let admin
    reassign the event without touching the appointment (the rep would
    still see it on "Today, mine"). The admin route passes ``None`` and
    falls back to ``event_in.owner_user_id`` for the event only,
    matching the prior behavior.

    ``actor_user_id`` stays the caller's id regardless — "created by"
    and "assigned to" are distinct concepts in the audit log.
    """
    if enrichment_in.party_size_bucket not in _VALID_PARTY_BUCKETS:
        raise WalkInLeadError(
            f"invalid party_size_bucket {enrichment_in.party_size_bucket!r}",
            code="invalid_party_size_bucket",
        )

    raw_phone = (contact_in.phone or "").strip()
    if not raw_phone:
        raise WalkInLeadError("phone is required", code="phone_required")
    phone_e164 = booking_service.normalize_phone_e164(raw_phone)
    if phone_e164 is None:
        # Phone is the dedupe identity. Letting an un-normalizable number
        # through would create a second contact for the same person on
        # the next walk-in. 422 forces staff to correct the input.
        raise WalkInLeadError(
            "phone could not be normalized to E.164", code="invalid_phone"
        )

    if not _has_usable_name(contact_in):
        # Contact.display_name is NOT NULL; we won't write "Unknown"
        # for a staff-entered lead because that hides identity in the
        # pipeline. Require at least one of display_name / first / last.
        raise WalkInLeadError(
            "a contact name is required (display_name or first+last)",
            code="contact_name_required",
        )

    if not (event_in.celebrant_first_name or "").strip():
        raise WalkInLeadError(
            "celebrant_first_name is required", code="celebrant_first_name_required"
        )

    normalized_email = (
        contact_in.email.strip().lower() if contact_in.email else None
    )

    # ---- Contact: find-or-create on phone identity -----------------------
    # find_or_create_contact does not accept display_name; for new
    # contacts we override it post-insert when the staff form supplies
    # an explicit display_name. Existing contacts are returned as-is —
    # no mutation just because staff is filing a new lead.
    contact, was_new_contact = contact_service.find_or_create_contact(
        db,
        phone_e164=phone_e164,
        email=normalized_email,
        phone=raw_phone,
        first_name=(contact_in.first_name or None),
        last_name=(contact_in.last_name or None),
    )
    if was_new_contact and contact_in.display_name:
        explicit = contact_in.display_name.strip()
        if explicit:
            contact.display_name = explicit
            db.flush()

    # ---- Appointment placeholder: status='attended', attended_at=NOW -----
    now_utc = datetime.now(timezone.utc)
    code = booking_service.generate_unique_confirmation_code(db)
    placeholder_email = (
        contact.email or normalized_email or f"walkin+{contact.id}@walkin.local"
    )
    appt = Appointment(
        confirmation_code=code,
        slot_start_at=now_utc,
        slot_end_at=now_utc + timedelta(minutes=_WALK_IN_DURATION_MINUTES),
        slot_duration_minutes=_WALK_IN_DURATION_MINUTES,
        timezone=APP_TIMEZONE,
        celebrant_first_name=event_in.celebrant_first_name.strip(),
        celebrant_last_name=(event_in.celebrant_last_name or None),
        parent_first_name=contact.first_name,
        parent_last_name=contact.last_name,
        event_date=event_in.event_date,
        party_size_bucket=enrichment_in.party_size_bucket,
        phone=contact.phone or raw_phone,
        phone_e164=phone_e164,
        email=placeholder_email,
        customer_note=None,
        internal_notes=(enrichment_in.notes or None),
        contact_id=contact.id,
        assigned_user_id=assigned_user_id,
        # 'attended' is already in the appointments.status CHECK; combined
        # with attended_at=NOW this keeps the placeholder out of "today's
        # appointments needing action" surfaces.
        status="attended",
        attended_at=now_utc,
        user_journey=[],
        bot_suspected=False,
        # `source: walk_in` lets future audits / attribution reports tell
        # walk-in rows apart from widget rows without a schema change.
        raw_payload={"source": "walk_in"},
    )
    db.add(appt)
    db.flush()

    # ---- Enrichment row (legacy survey shape) ---------------------------
    # Written directly rather than via upsert_profile_for_appointment,
    # which expects the BoutiqueExperienceSubmission contract (sizing +
    # measurements + preferences). The fields below are the older
    # survey columns that the kanban / event card already render.
    if _has_enrichment_signal(enrichment_in):
        # source='manual_attach' is the only value in chk_aer_source
        # that fits a staff-entered legacy survey (the other valid
        # values describe customer-driven flows: pre_booking, the
        # post-booking email link, the legacy enrichment survey, or a
        # legacy_note backfill). The raw_payload preserves the
        # walk-in origin for audits without needing a new enum value.
        enrichment = AppointmentEnrichmentResponse(
            appointment_id=appt.id,
            dress_styles=list(enrichment_in.dress_styles or []),
            colors=list(enrichment_in.colors or []),
            budget_range=(enrichment_in.budget_range or None),
            quince_theme=(enrichment_in.quince_theme or None),
            quince_theme_colors=list(enrichment_in.quince_theme_colors or []),
            court_size=enrichment_in.court_size,
            inspiration_photos=[],
            free_text=(enrichment_in.notes or None),
            submitted_at=now_utc,
            source="manual_attach",
            raw_payload={"source": "walk_in"},
        )
        db.add(enrichment)
        db.flush()

    # ---- Promote: Appointment → Event (status='lead') -------------------
    # promote_appointment_to_event reads the enrichment row we just
    # flushed and pulls theme / court_size / budget onto the Event,
    # which is exactly the shape the kanban card expects.
    # When a sales caller passes `assigned_user_id`, it wins over any
    # `event_in.owner_user_id` so both fields agree on the stylist.
    resolved_event_owner = (
        assigned_user_id
        if assigned_user_id is not None
        else event_in.owner_user_id
    )
    try:
        event = event_service.promote_appointment_to_event(
            db,
            appointment_id=appt.id,
            event_type="quinceanera",
            overrides=EventOverrides(
                event_name=(event_in.event_name or None),
                event_date=event_in.event_date,
                owner_user_id=resolved_event_owner,
            ),
            actor_user_id=actor_user_id,
        )
    except EventServiceError as exc:
        # Translate to the walk-in error vocabulary so the router maps
        # everything through one error table.
        raise WalkInLeadError(
            str(exc) or "promotion_failed",
            code=exc.code or "promotion_failed",
        ) from exc

    # ---- Audit: event.walk_in_created -----------------------------------
    activity_log.log_activity(
        db,
        event_id=event.id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.EVENT_WALK_IN_CREATED,
        subject_kind="event",
        subject_id=event.id,
        payload={
            "appointment_id": appt.id,
            "contact_id": contact.id,
            "was_new_contact": was_new_contact,
        },
    )

    _send_walk_in_lead_admin_emails(
        db,
        actor_user_id=actor_user_id,
        contact=contact,
        appointment=appt,
        event=event,
        notes=enrichment_in.notes,
    )

    # Write the event-log row that the admin daily digest summarises
    # from. TIMING_MODE for this kind is 'direct' (see
    # services/notification_routing), so this call writes the row
    # without fanning out to notification_jobs — the real-time send
    # path above is the canonical sender.
    from services import notification_routing  # local to avoid cycles

    notification_routing.record_event(
        db,
        kind="admin.walk_in_lead_created",
        subject_kind="event",
        subject_id=event.id,
        actor_user_id=actor_user_id,
        payload={
            "appointment_id": appt.id,
            "contact_id": contact.id,
            "contact_display_name": contact.display_name,
            "celebrant_first_name": appt.celebrant_first_name,
            "celebrant_last_name": appt.celebrant_last_name,
        },
    )

    # Sales-side walk-in with an assignee fires staff.booking_assigned
    # so the picked stylist gets a "new booking on your calendar" email.
    # Admin walk-ins (no assignee) skip this — admin gets the walk-in
    # capture summary above instead.
    from services.staff_booking_notifications import notify_booking_assigned

    notify_booking_assigned(db, appt, actor_user_id=actor_user_id)

    return WalkInLeadResult(
        contact=contact,
        appointment=appt,
        event=event,
        was_new_contact=was_new_contact,
    )


def _send_walk_in_lead_admin_emails(
    db: Session,
    *,
    actor_user_id: int,
    contact: Contact,
    appointment: Appointment,
    event: Event,
    notes: str | None,
) -> None:
    """Notify admins that a staff member just logged a walk-in. Best-
    effort; SMTP failures don't poison the lead-creation transaction.
    Recipient preference matches the time-off and missing-clock-out
    helpers: ``business_profile.email`` if set, otherwise every active
    admin user.
    """
    captured_by = db.get(User, actor_user_id) if actor_user_id else None
    if captured_by is None:
        return

    profile = db.query(BusinessProfile).first()
    if profile is not None and profile.email:
        admin_emails = [profile.email]
    else:
        rows = (
            db.query(User)
            .filter(User.role == "admin")
            .filter(User.is_active.is_(True))
            .all()
        )
        admin_emails = [u.email for u in rows if u.email]
    if not admin_emails:
        return

    from config.settings import ADMIN_BASE_URL
    from services import notification_templates

    rendered = notification_templates.render_admin_walk_in_lead_created(
        captured_by=captured_by,
        appointment=appointment,
        contact=contact,
        notes=notes,
        admin_url=f"{ADMIN_BASE_URL}/contacts/{contact.id}",
    )
    for to in admin_emails:
        send_rendered_safely(
            to=to,
            rendered=rendered,
            scope="walk_in.lead_created",
        )


def _has_usable_name(c: WalkInContactInput) -> bool:
    if c.display_name and c.display_name.strip():
        return True
    if (c.first_name and c.first_name.strip()) or (
        c.last_name and c.last_name.strip()
    ):
        return True
    return False


def _has_enrichment_signal(e: WalkInEnrichmentInput) -> bool:
    """Skip the enrichment row when the form sent only party_size_bucket.

    party_size_bucket already lives on the Appointment, so a row with
    nothing else would just be dead weight.
    """
    return any(
        (
            e.court_size is not None,
            bool((e.quince_theme or "").strip()),
            bool(e.quince_theme_colors),
            bool((e.budget_range or "").strip()),
            bool(e.dress_styles),
            bool(e.colors),
            bool((e.notes or "").strip()),
        )
    )
