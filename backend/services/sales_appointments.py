"""Sales-portal appointment reads (Phase 2).

Two functions:
  - `list_today(db, *, mine_user_id=None)` — returns the appointments
    whose `slot_start_at` falls inside today (in `APP_TIMEZONE`),
    ordered chronologically. Filters to `assigned_user_id` when caller
    passes `mine_user_id`.
  - `get_detail(db, appointment_id)` — full read for the detail screen:
    appointment + contact + linked event + participants + enrichment +
    last 20 activity-log rows tied to the linked event.

Both are pure read paths. Today's filter converts the local-day
boundaries to UTC up front so the query stays index-friendly on
`slot_start_at` instead of wrapping the column in `AT TIME ZONE`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from database.models import (
    ActivityLog,
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    Event,
    EventParticipant,
    User,
)
from services import activity_log, appointment_audit, event_service
from services.booking_service import format_confirmation_code, shop_tz
from services.event_service import EventServiceError

INTERNAL_NOTES_PREVIEW_CHARS = 140
RECENT_ACTIVITY_LIMIT = 20


@dataclass(frozen=True)
class _LocalDayBounds:
    start_utc: datetime
    end_utc: datetime
    local_date_iso: str


def _today_bounds() -> _LocalDayBounds:
    """Compute the half-open UTC interval covering today in APP_TIMEZONE."""
    tz = shop_tz()
    today_local = datetime.now(tz).date()
    start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return _LocalDayBounds(
        start_utc=start_local.astimezone(timezone.utc),
        end_utc=end_local.astimezone(timezone.utc),
        local_date_iso=today_local.isoformat(),
    )


def _enrichment_summary(enrichment: Optional[AppointmentEnrichmentResponse]) -> dict | None:
    if enrichment is None:
        return None
    return {
        "dress_styles": list(enrichment.dress_styles or []),
        "colors": list(enrichment.colors or []),
        "budget_range": enrichment.budget_range,
        "quince_theme": enrichment.quince_theme,
        "court_size": enrichment.court_size,
        "estimated_size_low": enrichment.estimated_size_low,
        "estimated_size_high": enrichment.estimated_size_high,
        "style_preference": enrichment.style_preference,
    }


def _internal_notes_preview(notes: str | None) -> str | None:
    if not notes:
        return None
    cleaned = " ".join(notes.split())
    if len(cleaned) <= INTERNAL_NOTES_PREVIEW_CHARS:
        return cleaned
    return cleaned[: INTERNAL_NOTES_PREVIEW_CHARS - 1].rstrip() + "…"


def list_today(
    db: Session, *, mine_user_id: int | None = None
) -> dict:
    """Return today's appointments (in APP_TIMEZONE), ordered by start time.

    The shape includes a `has_assigned` flag the frontend uses to enable
    the "show only mine" toggle: if no appointment in today's window has
    a non-null `assigned_user_id`, the toggle stays disabled (Phase 0
    confirmed nothing populates that column today, so silently filtering
    on it would render an empty list and look broken).
    """
    bounds = _today_bounds()

    base = (
        select(Appointment)
        .where(Appointment.slot_start_at >= bounds.start_utc)
        .where(Appointment.slot_start_at < bounds.end_utc)
        .order_by(Appointment.slot_start_at)
    )
    if mine_user_id is not None:
        base = base.where(Appointment.assigned_user_id == mine_user_id)

    appointments = db.execute(base).scalars().all()

    # Eager-load companion rows in batched lookups so the response
    # building below stays one query per relation, not N.
    enrichment_map: dict[int, AppointmentEnrichmentResponse] = {}
    event_map: dict[int, Event] = {}
    if appointments:
        appt_ids = [a.id for a in appointments]
        rows = (
            db.execute(
                select(AppointmentEnrichmentResponse).where(
                    AppointmentEnrichmentResponse.appointment_id.in_(appt_ids)
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            if r.appointment_id is not None:
                enrichment_map[r.appointment_id] = r

        event_ids = [a.crm_event_id for a in appointments if a.crm_event_id]
        if event_ids:
            erows = (
                db.execute(select(Event).where(Event.id.in_(event_ids)))
                .scalars()
                .all()
            )
            event_map = {e.id: e for e in erows}

    items: list[dict] = []
    has_assigned = False
    for appt in appointments:
        if appt.assigned_user_id is not None:
            has_assigned = True
        event = event_map.get(appt.crm_event_id) if appt.crm_event_id else None
        items.append(
            {
                "id": appt.id,
                "confirmation_code": format_confirmation_code(appt.confirmation_code),
                "slot_start_at": appt.slot_start_at,
                "slot_end_at": appt.slot_end_at,
                "slot_duration_minutes": appt.slot_duration_minutes,
                "timezone": appt.timezone,
                "party_size_bucket": appt.party_size_bucket,
                "parent_first_name": appt.parent_first_name,
                "parent_last_name": appt.parent_last_name,
                "celebrant_first_name": appt.celebrant_first_name,
                "celebrant_last_name": appt.celebrant_last_name,
                "status": appt.status,
                "assigned_user_id": appt.assigned_user_id,
                "internal_notes_preview": _internal_notes_preview(appt.internal_notes),
                "crm_event_id": appt.crm_event_id,
                "crm_event_status": event.status if event else None,
                "enrichment_summary": _enrichment_summary(
                    enrichment_map.get(appt.id)
                ),
            }
        )

    return {
        "date": bounds.local_date_iso,
        "timezone": str(shop_tz()),
        "has_assigned": has_assigned,
        "appointments": items,
    }


def get_detail(db: Session, *, appointment_id: int) -> dict | None:
    """Return the detail payload for one appointment.

    Returns None if the appointment id is not found. Caller maps to 404.

    Authorization note: any sales token may read any appointment id —
    not just today's. This matches the "sales sees all of today's
    appointments by default" / floor-visibility decision in the phase
    plan; a stylist drilling into yesterday's no-show on Tuesday
    morning is a legitimate workflow. If row-level scoping ever
    becomes needed, layer it in the router and not here.
    """
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        return None

    # D2: archived contacts/events disappear from sales reads by Gate 3
    # default. A sales rep viewing an appointment whose customer was
    # later archived sees the appointment without the customer card.
    contact = (
        db.get(Contact, appt.contact_id) if appt.contact_id is not None else None
    )
    if contact is not None and contact.deleted_at is not None:
        contact = None
    enrichment = (
        db.execute(
            select(AppointmentEnrichmentResponse).where(
                AppointmentEnrichmentResponse.appointment_id == appt.id
            )
        )
        .scalars()
        .first()
    )

    event = (
        db.get(Event, appt.crm_event_id)
        if appt.crm_event_id is not None
        else None
    )
    if event is not None and event.deleted_at is not None:
        event = None

    participants: list[dict] = []
    recent_activity: list[dict] = []
    arows: list = []
    actor_ids: set[int] = set()
    if event is not None:
        prows = (
            db.execute(
                select(EventParticipant)
                .where(EventParticipant.event_id == event.id)
                .order_by(EventParticipant.id)
            )
            .scalars()
            .all()
        )
        participants = [
            {
                "id": p.id,
                "role": p.role,
                "display_name": p.display_name,
                "phone": p.phone,
                "email": p.email,
                "measurements": dict(p.measurements or {}),
                "status": p.status,
            }
            for p in prows
        ]

        arows = (
            db.execute(
                select(ActivityLog)
                .where(ActivityLog.event_id == event.id)
                .order_by(desc(ActivityLog.created_at))
                .limit(RECENT_ACTIVITY_LIMIT)
            )
            .scalars()
            .all()
        )
        actor_ids = {row.actor_user_id for row in arows if row.actor_user_id}

    # Unified user-name lookup: activity actors + appointment assignee
    # + event owner. One batched query so the response carries live
    # `full_name` (or `username` fallback) without N round-trips.
    #
    # Fallback chain for the activity timeline (in order):
    #   1. Live `users.full_name` / `users.username` lookup (via name_map).
    #   2. `activity_log.actor_display_name` snapshot — preserved even
    #      if the user is deleted (FK nulled, snapshot kept).
    #   3. None — the renderer treats this as "Unknown actor" or
    #      "System" depending on `actor_kind`.
    user_ids: set[int] = set(actor_ids)
    if appt.assigned_user_id is not None:
        user_ids.add(appt.assigned_user_id)
    if event is not None and event.owner_user_id is not None:
        user_ids.add(event.owner_user_id)

    name_map: dict[int, str] = {}
    if user_ids:
        users = (
            db.execute(select(User).where(User.id.in_(user_ids)))
            .scalars()
            .all()
        )
        name_map = {u.id: (u.full_name or u.username) for u in users}

    if event is not None:
        recent_activity = [
            {
                "id": row.id,
                "created_at": row.created_at,
                "actor_kind": row.actor_kind,
                "actor_display_name": (
                    name_map.get(row.actor_user_id)
                    or row.actor_display_name
                ),
                "activity_type": row.activity_type,
                "subject_kind": row.subject_kind,
                "subject_id": row.subject_id,
                "payload": dict(row.payload or {}),
            }
            for row in arows
        ]

    return {
        "appointment": {
            "id": appt.id,
            "confirmation_code": format_confirmation_code(appt.confirmation_code),
            "slot_start_at": appt.slot_start_at,
            "slot_end_at": appt.slot_end_at,
            "slot_duration_minutes": appt.slot_duration_minutes,
            "timezone": appt.timezone,
            "party_size_bucket": appt.party_size_bucket,
            "parent_first_name": appt.parent_first_name,
            "parent_last_name": appt.parent_last_name,
            "celebrant_first_name": appt.celebrant_first_name,
            "celebrant_last_name": appt.celebrant_last_name,
            "event_date": appt.event_date,
            "phone": appt.phone,
            "email": appt.email,
            "customer_note": appt.customer_note,
            "internal_notes": appt.internal_notes,
            "status": appt.status,
            "assigned_user_id": appt.assigned_user_id,
            "assigned_user_full_name": (
                name_map.get(appt.assigned_user_id)
                if appt.assigned_user_id is not None
                else None
            ),
            # Phase 10.4: which participant's buyer journey this
            # appointment belongs to. The UI resolves role + display
            # name client-side from the participants array already in
            # this response, so no second lookup here.
            "event_participant_id": appt.event_participant_id,
            "attended_at": appt.attended_at,
            "no_show_at": appt.no_show_at,
            "cancelled_at": appt.cancelled_at,
        },
        "contact": (
            {
                "id": contact.id,
                "display_name": contact.display_name,
                "phone": contact.phone_e164 or contact.phone,
                "email": contact.email,
            }
            if contact is not None
            else None
        ),
        "event": (
            {
                "id": event.id,
                "event_type": event.event_type,
                "status": event.status,
                "status_changed_at": event.status_changed_at,
                "event_name": event.event_name,
                "event_date": event.event_date,
                "owner_user_id": event.owner_user_id,
                "owner_full_name": (
                    name_map.get(event.owner_user_id)
                    if event.owner_user_id is not None
                    else None
                ),
            }
            if event is not None
            else None
        ),
        "participants": participants,
        "enrichment": _full_enrichment(enrichment),
        "recent_activity": recent_activity,
    }


# ---------------------------------------------------------------------------
# Phase 3: composite status handler + notes update
# ---------------------------------------------------------------------------


# Status `arrived` translates the customer-visible appointment status
# into the staff vocabulary `attended` because that's the column shape
# inherited from the booking widget. The other two map 1:1.
_ACTION_TO_APPOINTMENT_STATUS: dict[str, str] = {
    "arrived": "attended",
    "no_show": "no_show",
    "cancelled": "cancelled",
}


_ACTION_TO_ACTIVITY_TYPE: dict[str, str] = {
    "arrived": activity_log.APPOINTMENT_ARRIVED,
    "no_show": activity_log.APPOINTMENT_NO_SHOW,
    "cancelled": activity_log.APPOINTMENT_CANCELLED,
}


class SalesActionError(Exception):
    """Raised when a status action cannot be applied. Carries a stable
    `code` the router maps to an HTTP status."""

    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _stamp_status(appt: Appointment, action: str) -> bool:
    """Set the appointment's status + timestamp column for `action`.

    Returns True if any column actually changed (idempotency signal).
    """
    target_status = _ACTION_TO_APPOINTMENT_STATUS[action]
    changed = False
    if appt.status != target_status:
        appt.status = target_status
        changed = True
    if action == "arrived" and appt.attended_at is None:
        appt.attended_at = func.now()
        changed = True
    if action == "no_show" and appt.no_show_at is None:
        appt.no_show_at = func.now()
        changed = True
    if action == "cancelled" and appt.cancelled_at is None:
        appt.cancelled_at = func.now()
        changed = True
    return changed


def apply_status_action(
    db: Session,
    *,
    appointment_id: int,
    action: str,
    actor_user_id: int,
    notes: str | None = None,
) -> dict:
    """Composite status handler used by the `/status` endpoint.

    Single transaction:

      arrived:
        - Set appointment.status='attended' + attended_at.
        - If `crm_event_id` is null, promote the appointment to an event
          via event_service.promote_appointment_to_event (status='lead').
        - If the linked event is in 'lead', transition it to 'consulted'
          via event_service.change_event_status. That call already
          double-writes event.status_changed into activity_log.
        - Append one `appointment.arrived` row to activity_log with
          {appointment_id, prior_event_status, new_event_status}.

      no_show / cancelled:
        - Stamp the matching status + timestamp on the appointment.
        - Do NOT touch the event status. A consult that was already
          transitioned does not auto-revert when the stylist re-classifies.
        - Append `appointment.{no_show|cancelled}` activity row IF the
          appointment has a linked event. With no event there is no
          timeline to write to and the appointment row's own columns
          are the audit trail.

    Idempotency: re-tapping the same action is a no-op past the first
    call. The event-status transition is itself idempotent
    (`change_event_status` no-ops when new_status == current status),
    so a second `arrived` does not double-write `event.status_changed`.
    The composite logs `appointment.arrived` only when something
    actually changed on this call.
    """
    if action not in _ACTION_TO_APPOINTMENT_STATUS:
        raise SalesActionError("invalid_action", http_status=422)

    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise SalesActionError("appointment_not_found", http_status=404)

    appointment_changed = _stamp_status(appt, action)

    prior_event_status: str | None = None
    new_event_status: str | None = None
    event_id: int | None = appt.crm_event_id
    promoted = False

    if action == "arrived":
        if event_id is None:
            try:
                event = event_service.promote_appointment_to_event(
                    db,
                    appointment_id=appt.id,
                    actor_user_id=actor_user_id,
                )
            except EventServiceError as exc:
                raise SalesActionError(
                    exc.code or "promotion_failed",
                    http_status=409 if exc.code == "missing_contact" else 400,
                ) from exc
            event_id = event.id
            prior_event_status = None
            new_event_status = event.status  # 'lead' fresh from promote
            promoted = True
        else:
            event = db.get(Event, event_id)
            if event is None or event.deleted_at is not None:
                # FK is SET NULL on event delete; archived events are
                # treated the same as missing — sales actions on
                # archived leads must be blocked at the action verb.
                raise SalesActionError("event_missing", http_status=409)
            prior_event_status = event.status
            new_event_status = event.status

        if new_event_status == "lead":
            try:
                event = event_service.change_event_status(
                    db,
                    event_id=event_id,
                    new_status="consulted",
                    actor_user_id=actor_user_id,
                    notes=notes,
                )
            except EventServiceError as exc:
                raise SalesActionError(
                    exc.code or "transition_failed", http_status=400
                ) from exc
            new_event_status = event.status

    # ---- Activity log row for the appointment action itself ----
    # Only write when something actually changed and the row has an
    # event to anchor to. The event-status double-write happens inside
    # change_event_status; we do not duplicate it here.
    should_log = appointment_changed and event_id is not None
    if should_log:
        activity_log.log_activity(
            db,
            event_id=event_id,
            actor_kind="staff",
            actor_user_id=actor_user_id,
            activity_type=_ACTION_TO_ACTIVITY_TYPE[action],
            subject_kind="appointment",
            subject_id=appt.id,
            payload={
                "appointment_id": appt.id,
                "prior_event_status": prior_event_status,
                "new_event_status": new_event_status,
                "promoted_event": promoted,
            },
        )

    db.flush()

    # Staff fan-out on sales-side cancellation: the assigned stylist
    # (often the same person who took the action, sometimes a coworker
    # if assignment moved earlier) needs to know the slot is free.
    # Status is already "cancelled" above so intrinsic targeting reads
    # the right assignee. Idempotent re-taps that did NOT change the
    # status row skip this — `appointment_changed` is False.
    if action == "cancelled" and appointment_changed:
        from services.staff_booking_notifications import notify_booking_cancelled

        notify_booking_cancelled(db, appt, actor_user_id=actor_user_id)

    return {
        "appointment_id": appt.id,
        "appointment_status": appt.status,
        "event_id": event_id,
        "prior_event_status": prior_event_status,
        "new_event_status": new_event_status,
        "promoted_event": promoted,
        "changed": appointment_changed,
    }


def update_internal_notes(
    db: Session,
    *,
    appointment_id: int,
    internal_notes: str,
    actor_user_id: int,
) -> dict:
    """Update `appointments.internal_notes`. Logs a length-delta row.

    The activity payload deliberately omits the prior text so the log
    stays small. If a future feature needs full diffs, store them
    elsewhere — the `activity_log` table is JSONB but read by the
    timeline UI for every event load.
    """
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise SalesActionError("appointment_not_found", http_status=404)

    prior = appt.internal_notes or ""
    new = internal_notes or ""
    if prior == new:
        return {
            "appointment_id": appt.id,
            "internal_notes": appt.internal_notes,
            "changed": False,
        }

    appt.internal_notes = new

    if appt.crm_event_id is not None:
        appointment_audit.log_notes_edited(
            db,
            appointment_id=appt.id,
            event_id=appt.crm_event_id,
            actor_user_id=actor_user_id,
            prior_notes=prior,
            new_notes=new,
        )

    db.flush()
    return {
        "appointment_id": appt.id,
        "internal_notes": appt.internal_notes,
        "changed": True,
    }


def _full_enrichment(enrichment: Optional[AppointmentEnrichmentResponse]) -> dict | None:
    if enrichment is None:
        return None
    return {
        "dress_styles": list(enrichment.dress_styles or []),
        "colors": list(enrichment.colors or []),
        "budget_range": enrichment.budget_range,
        "quince_theme": enrichment.quince_theme,
        "quince_theme_colors": list(enrichment.quince_theme_colors or []),
        "court_size": enrichment.court_size,
        "inspiration_photos": list(enrichment.inspiration_photos or []),
        "free_text": enrichment.free_text,
        "bust_inches": (
            float(enrichment.bust_inches)
            if enrichment.bust_inches is not None
            else None
        ),
        "waist_inches": (
            float(enrichment.waist_inches)
            if enrichment.waist_inches is not None
            else None
        ),
        "hips_inches": (
            float(enrichment.hips_inches)
            if enrichment.hips_inches is not None
            else None
        ),
        "height_ft": enrichment.height_ft,
        "height_in": enrichment.height_in,
        "estimated_size_low": enrichment.estimated_size_low,
        "estimated_size_high": enrichment.estimated_size_high,
        "style_preference": enrichment.style_preference,
        "back_preference": enrichment.back_preference,
        "submitted_at": enrichment.submitted_at,
    }
