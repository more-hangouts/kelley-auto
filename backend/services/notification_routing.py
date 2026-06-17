"""Staff notification dispatcher — foundation only (B1).

Skeleton for the event → recipients → jobs routing described in
``docs/STAFF_NOTIFICATIONS_MAP.md``. This module is the contract the
B2 work will hang off of:

  - ``record_event(...)`` is the single entry point that event surfaces
    will eventually call instead of constructing emails inline. It
    writes one ``StaffNotificationEvent`` row (the durable activity
    log feeding digests) and, for real-time event kinds, fans the
    event out to ``notification_jobs`` rows for each computed recipient.

  - ``recipients_for(...)`` resolves the recipient set for an event
    in three layers: intrinsic targeting (a per-kind function that
    derives "this event is *about* user X"), role defaults
    (subscription bundle per ``users.role``), and per-user overrides
    from ``notification_preferences``. Intrinsic targeting always
    wins; preferences only govern role-default subscriptions.

  - Three registries hold the policy: ``TIMING_MODE``,
    ``ROLE_DEFAULTS``, ``INTRINSIC_TARGETING``. They're populated
    here against the catalog so B2 can wire the first event surface
    without re-deciding policy mid-implementation. Anything that
    needs a code path (e.g. an intrinsic-targeting lookup of "the
    assigned stylist for this appointment") is stubbed with a clearly
    marked ``# B2: implement`` placeholder rather than guessed at.

No event-surface call sites were touched in B1. B2 enables the first
surfaces to swap from direct hooks to ``record_event``.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    Event,
    NotificationPreference,
    StaffNotificationEvent,
    StaffScheduleEntry,
    TimeOffRequest,
    User,
)

log = logging.getLogger(__name__)


# ─── Public API ────────────────────────────────────────────────────────────


def record_event(
    db: Session,
    *,
    kind: str,
    subject_kind: str | None = None,
    subject_id: int | None = None,
    actor_user_id: int | None = None,
    payload: dict | None = None,
) -> StaffNotificationEvent:
    """Write the event to ``staff_notification_events`` and, for
    real-time event kinds, fan the event out to ``notification_jobs``
    rows for each computed recipient. Returns the persisted event row
    so call sites can chain audit-log writes against the same id.

    For digest-only kinds, the event row alone is enough to:

      - feed the daily / weekly digest workers (B2) once they exist;
      - let the admin debug view list "what events happened today" without
        scraping the in-flight transactional paths.
    """
    event = StaffNotificationEvent(
        kind=kind,
        subject_kind=subject_kind,
        subject_id=subject_id,
        actor_user_id=actor_user_id,
        payload=payload or {},
    )
    db.add(event)
    db.flush()

    timing = TIMING_MODE.get(kind, "real_time")
    if timing in ("real_time", "real_time_and_digest"):
        from services.notification_service import (
            enqueue_staff_job,
            has_staff_email_renderer,
        )

        if not has_staff_email_renderer(kind):
            log.warning(
                "notification_routing.record_event: no staff renderer for "
                "kind=%s id=%s; event persisted without real-time fan-out",
                kind,
                event.id,
            )
            return event

        for recipient in recipients_for(db, event):
            enqueue_staff_job(
                db,
                kind=kind,
                recipient_user_id=recipient.user_id,
                recipient=recipient.email,
                subject_kind=subject_kind,
                subject_id=subject_id,
                payload=event.payload,
            )

    return event


def recipients_for(
    db: Session, event: StaffNotificationEvent
) -> list["Recipient"]:
    """Resolve the recipient set for an event. Three layers, in order:

      1. Intrinsic targeting — users the event is *about* (the affected
         staffer for a shift edit, the assigned stylist for a booking).
         Always included, never opt-out-able.
      2. Role-default subscribers — every active user whose role's
         default subscription bundle includes this event kind.
      3. Per-user overrides — rows in ``notification_preferences`` flip
         a role-default subscriber on or off explicitly.

    Returns a deduplicated list of recipients. An empty list means
    "no one is subscribed and the event has no intrinsic recipient";
    real-time fan-out becomes a no-op.
    """
    intrinsic_fn = INTRINSIC_TARGETING.get(event.kind)
    intrinsic: list[Recipient] = list(intrinsic_fn(db, event)) if intrinsic_fn else []

    # Role defaults: every active user whose role bundle includes this kind.
    role_default_user_ids: set[int] = set()
    candidate_users = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .all()
    )
    for user in candidate_users:
        role = (user.role or "").lower()
        if ROLE_DEFAULTS.get(role, {}).get(event.kind, False):
            role_default_user_ids.add(user.id)

    # Overrides: explicit user choices win over role defaults.
    overrides = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.event_kind == event.kind)
        .all()
    )
    for pref in overrides:
        if pref.enabled:
            role_default_user_ids.add(pref.user_id)
        else:
            role_default_user_ids.discard(pref.user_id)

    role_users = [u for u in candidate_users if u.id in role_default_user_ids]

    out: list[Recipient] = list(intrinsic)
    seen_ids = {r.user_id for r in out}
    for user in role_users:
        if user.id in seen_ids or not user.email:
            continue
        out.append(Recipient(user_id=user.id, email=user.email))
        seen_ids.add(user.id)
    return out


# ─── Types ─────────────────────────────────────────────────────────────────


class Recipient:
    """A resolved notification target. Carries the user id so the queue
    can audit fan-out per-staffer, plus the email currently on file so
    the dispatcher doesn't need a second User lookup at send time.
    """

    __slots__ = ("user_id", "email")

    def __init__(self, *, user_id: int, email: str) -> None:
        self.user_id = user_id
        self.email = email

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Recipient(user_id={self.user_id}, email={self.email!r})"


# ─── Registries ────────────────────────────────────────────────────────────
#
# Each registry below is keyed by the catalog ``kind`` string used in
# ``docs/STAFF_EMAIL_BUILD_TRACKER.md``. New event kinds get added here
# first, then their hook is allowed to call ``record_event``.

#: Timing mode per event kind.
#:
#:   * ``real_time``           — record_event fans out to ``notification_jobs``
#:     immediately for each computed recipient.
#:   * ``digest``              — record_event writes the event log row only;
#:     digest workers summarise from the log. No transactional email.
#:   * ``real_time_and_digest``— both. The digest summary covers what was
#:     already sent in real time so the recipient gets a quick alert + a
#:     batch summary.
#:   * ``direct``              — record_event writes the event log row only;
#:     a non-routing code path (e.g. ``services/walk_in_service`` for
#:     ``admin.walk_in_lead_created``) is the canonical real-time sender.
#:     Used when the original transactional hook predates the routing
#:     module and a migration to ``real_time`` fan-out would need more
#:     payload-rehydration plumbing than the slice is worth.
TIMING_MODE: dict[str, str] = {
    # Customer booking lifecycle — already wired through the legacy
    # ``enqueue_for_*`` helpers; listed here so the dispatcher knows the
    # mode if a B2 hook moves them to record_event.
    "booking.confirmation": "real_time",
    "booking.reminder": "real_time",
    "booking.enrichment_invitation": "real_time",
    "booking.reschedule_confirmation": "real_time",
    "booking.cancellation_confirmation": "real_time",
    "booking.thank_you": "real_time",
    "booking.no_show_followup": "real_time",
    # Staff bookings — A1 blocked at the assignment write site today.
    "staff.booking_assigned": "real_time",
    "staff.booking_rescheduled": "real_time",
    "staff.booking_cancelled": "real_time",
    # 'direct' — services/walk_in_service owns real-time delivery for
    # admins; record_event just writes the event log row so the
    # admin daily digest has it to summarise.
    "admin.walk_in_lead_created": "direct",
    "admin.new_booking": "real_time",
    # Schedule — #17 + #20 wired in A2.
    "staff.schedule_published": "real_time",
    "staff.shift_edited": "real_time",
    "staff.shift_deleted": "real_time",
    "staff.shift_added": "real_time",
    # Time-off — wired in the parallel-session A-pass.
    "admin.time_off_requested": "real_time",
    "staff.time_off_approved": "real_time",
    "staff.time_off_denied": "real_time",
    "staff.time_off_amended": "real_time",
    # Attendance — wired in A3.
    "staff.missing_clock_out": "real_time",
    "admin.missing_clock_out": "real_time",
    # Account / auth — wired across multiple slices.
    "admin.password_reset_request": "real_time",
    "admin.password_changed": "real_time",
    "staff.welcome_new_user": "real_time",
    "staff.pin_reset": "real_time",
    "staff.account_locked": "real_time",
    "staff.role_changed": "real_time",
    # Financial — wired in A3.
    "staff.quote_signed": "real_time",
    "staff.payment_received": "real_time",
    # Scheduling Phase 2: cover/drop shift requests. Each event names its
    # recipient explicitly in the payload (requester, candidate, or the
    # added/removed assignee), so they use the _explicit_recipient
    # intrinsic target rather than a subject-row lookup.
    "staff.shift_cover_requested": "real_time",
    "staff.shift_cover_accepted": "real_time",
    "staff.shift_cover_approved": "real_time",
    "staff.shift_cover_denied": "real_time",
    "staff.shift_drop_approved": "real_time",
    "staff.shift_drop_denied": "real_time",
    "staff.shift_pickup_denied": "real_time",
    "staff.shift_swap_requested": "real_time",
    "staff.shift_swap_accepted": "real_time",
    "staff.shift_swap_approved": "real_time",
    "staff.shift_swap_denied": "real_time",
    # Phase 9.4 D3: staff-witnessed in-store quote approval. Digest-only
    # because the witnessing staff already saw it happen; the lead owner
    # who wasn't present gets a daily roll-up rather than a real-time
    # blast.
    "quote.approved_in_store": "digest",
    # Digests — by definition digest-only.
    "digest.staff_daily": "digest",
    "digest.staff_weekly": "digest",
    "digest.admin_daily": "digest",
    # On-demand — admin clicks "Resend this week's schedule" and the
    # service in services/staff_schedule.resend_published_week enqueues
    # one staff.schedule_published job per affected staffer directly,
    # bypassing record_event's recipient resolution (the admin
    # explicitly picks recipients; intrinsic+role-default isn't the
    # right model here). 'direct' documents that the routing module
    # is not the canonical sender.
    "manual.resend_schedule": "direct",
}


#: Per-role default subscription bundles. ``{role: {kind: enabled}}``.
#: Missing kind defaults to ``False`` (not subscribed). Intrinsic targeting
#: bypasses this entirely.
#:
#: Note: customer-targeted kinds (``booking.*``, ``quote.sent``, etc.) are
#: omitted from every role because the recipient is the customer themselves,
#: not a staff role.
ROLE_DEFAULTS: dict[str, dict[str, bool]] = {
    "admin": {
        # Booking-side admin alerts
        "admin.new_booking": True,
        "admin.walk_in_lead_created": True,
        # Time-off review queue
        "admin.time_off_requested": True,
        # Attendance exceptions
        "admin.missing_clock_out": True,
        # Daily summary
        "digest.admin_daily": True,
        # Admins do NOT default-subscribe to their own auth events
        # (password_reset_request, password_changed) — those are
        # intrinsically targeted: only the user whose account it is
        # receives them.
    },
    "sales": {
        # Daily prep
        "digest.staff_daily": True,
        # Weekly look-ahead
        "digest.staff_weekly": True,
        # Schedule events all use intrinsic targeting (the affected
        # staffer); role defaults stay empty so a manager promoting
        # someone doesn't accidentally subscribe them to everyone's
        # shift edits.
    },
}


# ─── Intrinsic targeting helpers ──────────────────────────────────────────
#
# Each helper resolves an event's subject to the User who the event is
# *about* — the affected staffer, the assigned stylist, the user whose
# account was just touched. They take ``(db, event)`` and return an
# iterable of ``Recipient`` (almost always 0 or 1 today; the iterable
# shape exists so a future "all stylists assigned to a multi-stylist
# appointment" kind can return multiple without a signature change).
#
# Subject conventions each helper expects from the call site:
#
#   * subject_kind='user'         + subject_id=users.id         → the user
#   * subject_kind='time_off'     + subject_id=time_off_requests.id
#   * subject_kind='shift'        + subject_id=staff_schedule_entries.id
#   * subject_kind='appointment'  + subject_id=appointments.id
#
# All helpers fail soft: a missing subject_id, missing row, inactive
# user, or user with no email returns an empty iterable. The dispatcher
# then either falls through to role-default subscribers or no-ops.


def _user_to_recipient(user: User | None) -> list[Recipient]:
    if user is None or not user.is_active or not user.email:
        return []
    return [Recipient(user_id=user.id, email=user.email)]


def _the_user_themselves(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """For account/auth events: the user IS the subject."""
    if event.subject_id is None:
        return []
    return _user_to_recipient(db.get(User, event.subject_id))


def _explicit_recipient(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """The recipient named explicitly in ``payload['recipient_user_id']``.

    Used by the shift-request flow, where a single transition can target
    different people (the requester, the named candidate, the assignee
    added or removed by a cover) and the right recipient isn't derivable
    from the subject row alone."""
    uid = (event.payload or {}).get("recipient_user_id")
    if uid is None:
        return []
    return _user_to_recipient(db.get(User, int(uid)))


def _requester_of_time_off(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """The staffer who filed the time-off request gets notified about
    decisions and amendments on it."""
    if event.subject_id is None:
        return []
    request = db.get(TimeOffRequest, event.subject_id)
    if request is None:
        return []
    return _user_to_recipient(db.get(User, request.user_id))


def _staffer_of_schedule_entry(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """The stylist whose calendar a shift sits on. Used for shift-add /
    edit / delete and for missing-clock-out (where the shift is the
    natural subject of the alert)."""
    if event.subject_id is None:
        return []
    entry = db.get(StaffScheduleEntry, event.subject_id)
    if entry is None:
        return []
    return _user_to_recipient(db.get(User, entry.user_id))


def _assigned_stylist_of_appointment(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """The stylist an appointment is assigned to. Empty when no stylist
    is assigned (the booking flow's natural state until someone claims
    or auto-assignment runs) — the role-default subscribers still get
    the event if any are configured."""
    if event.subject_id is None:
        return []
    appt = db.get(Appointment, event.subject_id)
    if appt is None or appt.assigned_user_id is None:
        return []
    return _user_to_recipient(db.get(User, appt.assigned_user_id))


def _owner_of_event(
    db: Session, event: StaffNotificationEvent
) -> Iterable[Recipient]:
    """The lead/event owner. Used when a write affects the event as a
    whole (in-store quote approval, etc.) and the owner is the right
    person to surface it to. Empty when the event has no owner — the
    role-default subscribers still get the event if configured."""
    if event.subject_id is None:
        return []
    crm_event = db.get(Event, event.subject_id)
    if (
        crm_event is None
        or crm_event.deleted_at is not None
        or crm_event.owner_user_id is None
    ):
        return []
    return _user_to_recipient(db.get(User, crm_event.owner_user_id))


#: Functions that compute the intrinsic recipients of an event from its
#: subject. Each function takes ``(db, event)`` and returns an iterable of
#: ``Recipient``. Missing entry means the event has no intrinsic recipient
#: (only role-default subscribers receive it).
INTRINSIC_TARGETING: dict[
    str, Callable[[Session, StaffNotificationEvent], Iterable[Recipient]]
] = {
    # Account / auth — the user IS the subject.
    "staff.welcome_new_user":         _the_user_themselves,
    "staff.pin_reset":                _the_user_themselves,
    "staff.account_locked":           _the_user_themselves,
    "staff.role_changed":             _the_user_themselves,
    "admin.password_reset_request":   _the_user_themselves,
    "admin.password_changed":         _the_user_themselves,
    # Time-off — the requester gets every decision/amendment on their
    # own request. `admin.time_off_requested` deliberately has NO
    # intrinsic targeting because it goes to admins, not the requester.
    "staff.time_off_approved":        _requester_of_time_off,
    "staff.time_off_denied":          _requester_of_time_off,
    "staff.time_off_amended":         _requester_of_time_off,
    # Schedule — the affected staffer.
    "staff.shift_added":              _staffer_of_schedule_entry,
    "staff.shift_edited":             _staffer_of_schedule_entry,
    "staff.shift_deleted":            _staffer_of_schedule_entry,
    "staff.missing_clock_out":        _staffer_of_schedule_entry,
    # Bookings — the assigned stylist. Empty when unassigned, which is
    # the normal state today until a stylist-claim UI exists.
    "staff.booking_assigned":         _assigned_stylist_of_appointment,
    "staff.booking_rescheduled":      _assigned_stylist_of_appointment,
    "staff.booking_cancelled":        _assigned_stylist_of_appointment,
    # Quotes — the event owner. Used for the staff-witnessed in-store
    # approval path (Phase 9.4 D3); customer-portal sign goes through a
    # different code path today.
    "quote.approved_in_store":        _owner_of_event,
    # Scheduling Phase 2 shift requests — recipient named in the payload.
    "staff.shift_cover_requested":    _explicit_recipient,
    "staff.shift_cover_accepted":     _explicit_recipient,
    "staff.shift_cover_approved":     _explicit_recipient,
    "staff.shift_cover_denied":       _explicit_recipient,
    "staff.shift_drop_approved":      _explicit_recipient,
    "staff.shift_drop_denied":        _explicit_recipient,
    "staff.shift_pickup_denied":      _explicit_recipient,
    "staff.shift_swap_requested":     _explicit_recipient,
    "staff.shift_swap_accepted":      _explicit_recipient,
    "staff.shift_swap_approved":      _explicit_recipient,
    "staff.shift_swap_denied":        _explicit_recipient,
}
