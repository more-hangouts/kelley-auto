"""In-store and phone lead capture.

Today the only path that creates leads is the public booking widget at
``api/routers/booking.py``. When a customer walks in or calls in, staff
have no in-app affordance to capture them; the workaround has been
to fill the public widget themselves, which awkwardly pollutes
attribution.

This service exists so the admin walk-in flow lands a row shape
indistinguishable from a widget booking: a Contact, a placeholder
Appointment (status='attended', attended_at=NOW), and a
freshly-promoted Event in the first pipeline lane with
``appointment.crm_event_id`` linked back. That keeps the kanban /
pipeline / event tabs identical regardless of origin — they all
expect appointment-backed events.

Design notes:

  - **One transaction, one commit.** The route handler owns the
    ``db.commit()``; this service stays at flush boundaries so a
    later failure rolls everything back together (no orphan contact
    with no event, no event with no audit row).
  - **Phone is identity.** ``normalize_phone_e164`` returning ``None``
    is rejected as ``invalid_phone`` rather than silently weakening
    dedupe. Without phone normalization we'd let two leads with the
    same number end up on different contacts.
  - **Existing-contact name is not mutated.** Staff might pick an
    existing person they recognize and type a new celebrant nickname
    in Step 2; the contact's display_name should not change because
    of that. Only fresh contacts derive their name from the form.
  - **A phone lead is not an arrival.** This endpoint has always been
    described to staff as "walk-in or phone lead", but it stamped every
    lead with an ``attended`` placeholder appointment, so callers were
    recorded as having physically shown up — quietly inflating
    attendance and the appointment table alike. ``booking_context``
    now decides: 'walk_in' keeps the arrival receipt, 'phone_call'
    creates the deal directly through ``create_walk_in_event`` (the
    same appointment-free path the storefront lead form and web chat
    already use). No new lead shape, one fewer fiction.
  - **Origin is asked, not inferred.** ``walk_in_source`` records what
    the rep was told at the counter. It is stored on the deal, not
    folded into the storefront's derived attribution — see migration
    104.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    Appointment,
    BusinessProfile,
    Contact,
    Event,
    User,
)
from modules.core.services import activity_log, sales_staff
from modules.contacts.services import contact_service
from modules.booking.services import booking_service, event_service
from modules.core.services.email_transport import send_rendered_safely
from modules.booking.services.event_service import EventOverrides, EventServiceError

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
    # Migration 104. Both optional: the picker is strongly encouraged in
    # the UI but non-blocking, because a rep who is mid-conversation and
    # does not yet know should be able to file the lead anyway. A wrong
    # bucket is worse than an empty one.
    walk_in_source: str | None = None
    walk_in_source_detail: str | None = None
    # Migration 110: the salesperson owed commission for bringing this
    # customer in. Independent of owner_user_id — see the migration
    # docstring. Optional: plenty of leads arrive with no rep involved.
    sales_credit_user_id: int | None = None


@dataclass(frozen=True)
class WalkInEnrichmentInput:
    # Wire name "enrichment" kept for SPA compatibility; the Bella's-era
    # dress-survey fields (court_size, theme, dress_styles, colors) were
    # removed with the dealership conversion.
    #
    # party_size_bucket is the last of those survey fields still standing.
    # "How many in your party?" is a dress-fitting question, not a car
    # question, so the dealership UI no longer asks it and None resolves
    # to the neutral 'solo' the CHECK already allows — the same value
    # the storefront lead path uses. An explicit value is still honored
    # so historical clients keep working.
    party_size_bucket: str | None
    budget_range: str | None
    notes: str | None
    # Migration 109: the paper walk-in sheet's questions 2, 3 and 5. They
    # ride on the enrichment input rather than the event input because
    # they are the same kind of thing as budget_range — what the customer
    # told the rep, not what the system knows about the deal.
    #
    # Defaulted so historical callers (and the storefront lead path, which
    # never asks these) construct the dataclass unchanged.
    current_vehicle: str | None = None
    desired_vehicle_type: str | None = None
    financing_preference: str | None = None


@dataclass(frozen=True)
class WalkInLeadResult:
    contact: Contact
    # None for phone leads: nobody arrived, so there is no arrival receipt.
    appointment: Appointment | None
    event: Event
    was_new_contact: bool


_WALK_IN_DURATION_MINUTES = 45
_VALID_PARTY_BUCKETS = ("solo", "pair", "3_4", "5_plus")
_DEFAULT_PARTY_BUCKET = "solo"

# Staff-entered lead origin (migration 104). Keep in sync with the CHECK in
# 104_lead_origin_and_appointment_source.py. The bucket is stable and
# reportable; which platform or post lives in walk_in_source_detail.
WALK_IN_SOURCE_VALUES: frozenset[str] = frozenset(
    {
        "social_media",
        "drive_by",
        "referral",
        "repeat_customer",
        "google_search",
        "website",
        "other",
    }
)

_WALK_IN_SOURCE_DETAIL_MAX = 200

# Paper-sheet intake answers (migration 109). Keep in sync with the CHECKs in
# 109_walk_in_intake_answers.py and with the option lists the SPA renders in
# apps/admin/src/utils/walkInLeadIntake.js.
#
# Neither set has an "undecided" member on purpose: a rep who does not know
# leaves the control empty, which stores NULL and reads as "not answered".
# See the migration docstring.
DESIRED_VEHICLE_TYPE_VALUES: frozenset[str] = frozenset(
    {"car", "suv", "minivan", "truck_work_van"}
)

FINANCING_PREFERENCE_VALUES: frozenset[str] = frozenset(
    {"national_lender", "in_house", "cash"}
)

_CURRENT_VEHICLE_MAX = 120

# The two ways a lead reaches this service. The wider
# booking_service.BOOKING_CONTEXT_VALUES set also covers contexts that only
# apply to staff-created appointments ('existing_customer', 'admin'); lead
# capture is only ever one of these two.
LEAD_CONTEXT_VALUES: frozenset[str] = frozenset({"walk_in", "phone_call"})
_DEFAULT_LEAD_CONTEXT = "walk_in"


def create_walk_in_lead(
    db: Session,
    *,
    actor_user_id: int,
    contact_in: WalkInContactInput,
    event_in: WalkInEventInput,
    enrichment_in: WalkInEnrichmentInput,
    assigned_user_id: int | None = None,
    booking_context: str = _DEFAULT_LEAD_CONTEXT,
) -> WalkInLeadResult:
    """Create Contact + Event (+ arrival receipt for walk-ins) in one tx.

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

    ``booking_context`` selects the shape:

      - ``'walk_in'`` — somebody physically arrived. Writes the
        placeholder Appointment (status='attended', attended_at=NOW)
        and promotes it, so the deal is appointment-backed exactly as
        before.
      - ``'phone_call'`` — nobody arrived. Creates the deal directly
        with no appointment at all. ``result.appointment`` is None.

    Both shapes are already load-bearing elsewhere: the storefront lead
    form and web chat create appointment-free deals through the same
    ``create_walk_in_event``, and the board left-joins appointments.
    """
    if booking_context not in LEAD_CONTEXT_VALUES:
        raise WalkInLeadError(
            f"invalid booking_context {booking_context!r}",
            code="invalid_booking_context",
        )

    party_size_bucket = enrichment_in.party_size_bucket or _DEFAULT_PARTY_BUCKET
    if party_size_bucket not in _VALID_PARTY_BUCKETS:
        raise WalkInLeadError(
            f"invalid party_size_bucket {enrichment_in.party_size_bucket!r}",
            code="invalid_party_size_bucket",
        )

    walk_in_source = _clean(event_in.walk_in_source)
    if walk_in_source is not None and walk_in_source not in WALK_IN_SOURCE_VALUES:
        raise WalkInLeadError(
            "walk_in_source must be one of: "
            + ", ".join(sorted(WALK_IN_SOURCE_VALUES)),
            code="invalid_walk_in_source",
        )
    walk_in_source_detail = _clean(event_in.walk_in_source_detail)
    if walk_in_source_detail is not None:
        if len(walk_in_source_detail) > _WALK_IN_SOURCE_DETAIL_MAX:
            raise WalkInLeadError(
                f"walk_in_source_detail exceeds {_WALK_IN_SOURCE_DETAIL_MAX} characters",
                code="walk_in_source_detail_too_long",
            )
        if walk_in_source is None:
            # Detail without a bucket is unreportable ("Facebook video"
            # attached to nothing). Drop it rather than storing a value no
            # query will ever reach.
            walk_in_source_detail = None

    # ---- Paper-sheet intake answers (migration 109) ----------------------
    # Same non-blocking posture as walk_in_source: a rep mid-conversation
    # who has not asked yet files the lead with these empty. What is NOT
    # tolerated is a value outside the CHECK — that would 500 at flush,
    # so it is caught here and returned as a 422 the SPA can explain.
    current_vehicle = _clean(enrichment_in.current_vehicle)
    if current_vehicle is not None and len(current_vehicle) > _CURRENT_VEHICLE_MAX:
        raise WalkInLeadError(
            f"current_vehicle exceeds {_CURRENT_VEHICLE_MAX} characters",
            code="current_vehicle_too_long",
        )

    desired_vehicle_type = _clean(enrichment_in.desired_vehicle_type)
    if (
        desired_vehicle_type is not None
        and desired_vehicle_type not in DESIRED_VEHICLE_TYPE_VALUES
    ):
        raise WalkInLeadError(
            "desired_vehicle_type must be one of: "
            + ", ".join(sorted(DESIRED_VEHICLE_TYPE_VALUES)),
            code="invalid_desired_vehicle_type",
        )

    financing_preference = _clean(enrichment_in.financing_preference)
    if (
        financing_preference is not None
        and financing_preference not in FINANCING_PREFERENCE_VALUES
    ):
        raise WalkInLeadError(
            "financing_preference must be one of: "
            + ", ".join(sorted(FINANCING_PREFERENCE_VALUES)),
            code="invalid_financing_preference",
        )

    # ---- Sales credit (migration 110) ------------------------------------
    # Validated against the same assignable-staff set the picker lists, so a
    # client cannot credit commission to an inactive or non-staff id. Left
    # None when unset — there is deliberately no fallback to the actor.
    sales_credit_user_id = event_in.sales_credit_user_id
    if sales_credit_user_id is not None and not sales_staff.is_assignable_sales_user(
        db, sales_credit_user_id
    ):
        raise WalkInLeadError(
            "sales_credit_user_id must be an active sales or admin user",
            code="invalid_sales_credit_user_id",
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

    # When a sales caller passes `assigned_user_id`, it wins over any
    # `event_in.owner_user_id` so both fields agree on the rep.
    resolved_event_owner = (
        assigned_user_id
        if assigned_user_id is not None
        else event_in.owner_user_id
    )
    overrides = EventOverrides(
        event_name=(event_in.event_name or None),
        event_date=event_in.event_date,
        budget_range=(enrichment_in.budget_range or None),
        owner_user_id=resolved_event_owner,
        walk_in_source=walk_in_source,
        walk_in_source_detail=walk_in_source_detail,
        current_vehicle=current_vehicle,
        desired_vehicle_type=desired_vehicle_type,
        financing_preference=financing_preference,
        sales_credit_user_id=sales_credit_user_id,
    )

    appt: Appointment | None = None
    if booking_context == "walk_in":
        # ---- Appointment placeholder: status='attended', attended_at=NOW -
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
            party_size_bucket=party_size_bucket,
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
            # Migration 104 columns. raw_payload keeps its legacy key so the
            # historical rows and the new ones read the same way in an audit.
            source="walk_in_placeholder",
            booking_context="walk_in",
            user_journey=[],
            bot_suspected=False,
            raw_payload={"source": "walk_in"},
        )
        db.add(appt)
        db.flush()

        # ---- Promote: Appointment → Event (first pipeline lane) ---------
        try:
            event = event_service.promote_appointment_to_event(
                db,
                appointment_id=appt.id,
                event_type="vehicle_sale",
                overrides=overrides,
                actor_user_id=actor_user_id,
            )
        except EventServiceError as exc:
            # Translate to the walk-in error vocabulary so the router maps
            # everything through one error table.
            raise WalkInLeadError(
                str(exc) or "promotion_failed",
                code=exc.code or "promotion_failed",
            ) from exc
    else:
        # ---- Phone lead: the deal, with no arrival to record -------------
        # promote_appointment_to_event derives the deal name from the
        # appointment's celebrant fields; with no appointment, resolve the
        # same name here so a phone lead and a walk-in for the same person
        # land on identical deal names.
        try:
            event = event_service.create_walk_in_event(
                db,
                contact_id=contact.id,
                event_type="vehicle_sale",
                overrides=replace(
                    overrides,
                    event_name=(
                        overrides.event_name
                        or _buyer_deal_name(event_in)
                    ),
                    notes=(enrichment_in.notes or None),
                ),
                actor_user_id=actor_user_id,
            )
        except EventServiceError as exc:
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
            "appointment_id": appt.id if appt is not None else None,
            "contact_id": contact.id,
            "was_new_contact": was_new_contact,
            # Origin rides in the timeline payload so the deal's first row
            # answers "where did this person come from?" without a join.
            "booking_context": booking_context,
            "walk_in_source": walk_in_source,
            "walk_in_source_detail": walk_in_source_detail,
            # Migration 109 answers ride along too, so the deal's opening
            # timeline row is a faithful snapshot of the intake sheet even
            # if someone later edits the columns.
            "current_vehicle": current_vehicle,
            "desired_vehicle_type": desired_vehicle_type,
            "financing_preference": financing_preference,
            "sales_credit_user_id": sales_credit_user_id,
        },
    )

    _send_walk_in_lead_admin_emails(
        db,
        actor_user_id=actor_user_id,
        contact=contact,
        appointment=appt,
        event=event,
        notes=enrichment_in.notes,
        buyer_name=_buyer_name(event_in),
        booking_context=booking_context,
        current_vehicle=current_vehicle,
        desired_vehicle_type=desired_vehicle_type,
        financing_preference=financing_preference,
        budget_range=_clean(enrichment_in.budget_range),
        sales_credit_user_id=sales_credit_user_id,
    )

    # Write the event-log row that the admin daily digest summarises
    # from. TIMING_MODE for this kind is 'direct' (see
    # services/notification_routing), so this call writes the row
    # without fanning out to notification_jobs — the real-time send
    # path above is the canonical sender.
    from modules.core.services import notification_routing  # local to avoid cycles

    notification_routing.record_event(
        db,
        kind="admin.walk_in_lead_created",
        subject_kind="event",
        subject_id=event.id,
        actor_user_id=actor_user_id,
        payload={
            "appointment_id": appt.id if appt is not None else None,
            "contact_id": contact.id,
            "contact_display_name": contact.display_name,
            "celebrant_first_name": (event_in.celebrant_first_name or "").strip()
            or None,
            "celebrant_last_name": (event_in.celebrant_last_name or None),
            "booking_context": booking_context,
            "walk_in_source": walk_in_source,
        },
    )

    # Sales-side walk-in with an assignee fires staff.booking_assigned
    # so the picked stylist gets a "new booking on your calendar" email.
    # Admin walk-ins (no assignee) skip this — admin gets the walk-in
    # capture summary above instead. A phone lead has no booking to
    # announce; the assignee learns about it from the deal itself.
    if appt is not None:
        from modules.booking.services.staff_booking_notifications import (
            notify_booking_assigned,
        )

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
    appointment: Appointment | None,
    event: Event,
    notes: str | None,
    buyer_name: str | None = None,
    booking_context: str = _DEFAULT_LEAD_CONTEXT,
    current_vehicle: str | None = None,
    desired_vehicle_type: str | None = None,
    financing_preference: str | None = None,
    budget_range: str | None = None,
    sales_credit_user_id: int | None = None,
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
    from modules.core.services import notification_templates

    rendered = notification_templates.render_admin_walk_in_lead_created(
        captured_by=captured_by,
        appointment=appointment,
        contact=contact,
        notes=notes,
        admin_url=f"{ADMIN_BASE_URL}/contacts/{contact.id}",
        customer_name=buyer_name,
        lead_kind="phone" if booking_context == "phone_call" else "walk-in",
        intake_rows=_intake_email_rows(
            budget_range=budget_range,
            current_vehicle=current_vehicle,
            desired_vehicle_type=desired_vehicle_type,
            financing_preference=financing_preference,
            # Commission credit belongs in this email specifically: it is
            # the one read by whoever runs payroll, and "who brought them
            # in" is the question they are reading it to answer.
            credited_name=_credited_user_name(db, sales_credit_user_id),
        ),
    )
    for to in admin_emails:
        send_rendered_safely(
            to=to,
            rendered=rendered,
            scope="walk_in.lead_created",
        )


# Display labels for the migration-109 slugs, used only when rendering the
# admin alert email. The SPA has its own copy of these strings; that
# duplication is deliberate — an email is read outside the app and should not
# have to ask the SPA how to spell "Truck / work van". Unknown values fall
# through to the raw slug rather than vanishing.
_DESIRED_VEHICLE_TYPE_LABELS = {
    "car": "Car",
    "suv": "SUV",
    "minivan": "Minivan",
    "truck_work_van": "Truck / work van",
}

_FINANCING_PREFERENCE_LABELS = {
    "national_lender": "National lender (bank)",
    "in_house": "In-house financing",
    "cash": "Cash",
}


def _credited_user_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = db.get(User, user_id)
    if row is None:
        return None
    return row.full_name or row.username


def _intake_email_rows(
    *,
    budget_range: str | None,
    current_vehicle: str | None,
    desired_vehicle_type: str | None,
    financing_preference: str | None,
    credited_name: str | None = None,
) -> list[tuple[str, str]]:
    """The intake sheet's answers as (label, value) rows for the alert email.

    Unanswered questions are omitted rather than rendered as "—": the person
    reading this email at 9am wants the three things the customer actually
    said, not a form with blanks in it.
    """
    rows = [
        ("Brought in by", credited_name),
        ("Budget", budget_range),
        ("Currently driving", current_vehicle),
        (
            "Looking for",
            _DESIRED_VEHICLE_TYPE_LABELS.get(
                desired_vehicle_type or "", desired_vehicle_type
            ),
        ),
        (
            "Financing",
            _FINANCING_PREFERENCE_LABELS.get(
                financing_preference or "", financing_preference
            ),
        ),
    ]
    return [(label, value) for label, value in rows if value]


def _clean(value: str | None) -> str | None:
    """Trim to None. Empty-string form fields are absent fields."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _buyer_name(event_in: WalkInEventInput) -> str | None:
    parts = [
        p.strip()
        for p in (event_in.celebrant_first_name, event_in.celebrant_last_name)
        if p and p.strip()
    ]
    return " ".join(parts) if parts else None


def _buyer_deal_name(event_in: WalkInEventInput) -> str | None:
    """The deal name a walk-in would have gotten, for the phone path.

    ``promote_appointment_to_event`` derives it from the appointment's
    celebrant columns; with no appointment there is nothing to derive from,
    so the same "<buyer>'s Deal" shape is built here. Returns None when the
    buyer has no name, letting ``create_walk_in_event`` fall back to its
    own contact-based default.
    """
    name = _buyer_name(event_in)
    return f"{name}'s Deal" if name else None


def _has_usable_name(c: WalkInContactInput) -> bool:
    if c.display_name and c.display_name.strip():
        return True
    if (c.first_name and c.first_name.strip()) or (
        c.last_name and c.last_name.strip()
    ):
        return True
    return False

