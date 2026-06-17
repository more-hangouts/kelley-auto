"""Daily + weekly digest workers (B2.3).

Three runners, all called from ``workers/daily.py`` on the same
02:30 local cron tick:

  - ``run_staff_daily(db, digest_date)`` — every sales user with a
    published shift today receives a one-page summary of their day.
    Recompute from current state, not from the event log: the user
    wants "today's appointments as they exist now," not "every
    booking.created row since yesterday."

  - ``run_staff_weekly(db, week_start)`` — Sunday-only fire. Every
    sales user with at least one published shift in the upcoming
    week gets a look-ahead.

  - ``run_admin_daily(db, digest_date)`` — every admin user gets the
    boutique's daily summary: new bookings, pending time-off,
    attendance exceptions, abandoned-booking count. New bookings +
    walk-ins are read from the ``staff_notification_events`` log
    (admin.new_booking + admin.walk_in_lead_created); pending time-
    off and missing-clock-outs are recomputed from current state
    because "what's open right now" is the only useful read of those.

Recipients are computed via ``notification_routing.recipients_for``
so role defaults + per-user overrides apply consistently with the
real-time path.

Dedup is enforced by the ``uq_one_digest_per_user_per_window`` partial
unique index on ``notification_jobs``: each digest send inserts a row
with ``status='sent'`` and a ``payload.digest_window`` date stamp;
re-running the runner on the same window inserts no duplicates because
the unique index rejects them.

Send path is **synchronous** from the runner — the runner has full DB
access, so building the renderer-ready payload + sending via
``email_transport`` directly is simpler than queuing payloads with ORM
objects through the dispatcher. The ``notification_jobs`` row is the
audit/dedup ledger, not the delivery vehicle.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config.settings import ADMIN_BASE_URL, APP_TIMEZONE
from database.models import (
    Appointment,
    NotificationJob,
    StaffNotificationEvent,
    StaffScheduleEntry,
    TimeOffRequest,
    User,
)
from services import notification_routing, notification_templates
from services.business_time import shop_tz
from services.email_transport import send_rendered_safely
from services.staff_schedule import _entry_to_shift_dict

log = logging.getLogger(__name__)


# ─── Entry point called by workers/daily.py ────────────────────────────────


def tick(db: Session) -> None:
    """Run every digest cadence appropriate for the current local date.

    Errors in one runner shouldn't poison the others — each is wrapped
    so a database hiccup in (say) the admin digest doesn't drop the
    staff daily/weekly sends.
    """
    today_local = datetime.now(shop_tz()).date()
    for label, fn in (
        ("staff_daily", lambda: run_staff_daily(db, digest_date=today_local)),
        ("admin_daily", lambda: run_admin_daily(db, digest_date=today_local)),
        # Weekly is Sunday-only; the runner itself checks the weekday.
        ("staff_weekly", lambda: run_staff_weekly(db, week_start=today_local)),
    ):
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("staff_digest_runner: %s tick failed", label)


# ─── Runners ───────────────────────────────────────────────────────────────


def run_staff_daily(db: Session, *, digest_date: date) -> int:
    """Fire ``digest.staff_daily`` for each sales user with a published
    shift today. Returns the count of digests sent (excluding dedup
    skips)."""
    window_key = digest_date.isoformat()
    recipients = _recipients_for_kind(db, "digest.staff_daily")
    sent = 0
    for recipient in recipients:
        user = db.get(User, recipient.user_id)
        if user is None or not user.email or not user.is_active:
            continue
        shift = _today_shift_for(db, user_id=user.id, digest_date=digest_date)
        if shift is None:
            # No shift today → skip. The digest is only useful for
            # someone who's actually working.
            continue
        if _already_sent(
            db,
            kind="digest.staff_daily",
            recipient_user_id=user.id,
            window_key=window_key,
        ):
            continue
        appointments = _today_appointments_for(
            db, user_id=user.id, digest_date=digest_date
        )
        rendered = notification_templates.render_staff_daily_digest(
            staff_user=user,
            digest_date=digest_date,
            shift=_entry_to_shift_dict(shift),
            appointments=appointments,
            admin_url=f"{ADMIN_BASE_URL}/schedule/today",
        )
        send_rendered_safely(
            to=user.email, rendered=rendered, scope="digest.staff_daily"
        )
        _record_sent(
            db,
            kind="digest.staff_daily",
            recipient_user_id=user.id,
            recipient_email=user.email,
            window_key=window_key,
        )
        sent += 1
    db.commit()
    return sent


def run_staff_weekly(db: Session, *, week_start: date) -> int:
    """Sunday-only: fire ``digest.staff_weekly`` for each sales user
    with at least one published shift in the upcoming week. ``week_start``
    is the date the runner was invoked on; the actual schedule window
    is Mon..Sun starting the day AFTER (so a Sunday morning send is
    framed as "your upcoming week"). Returns digests sent."""
    if week_start.weekday() != 6:  # 6 == Sunday
        return 0
    upcoming_monday = week_start + timedelta(days=1)
    window_key = upcoming_monday.isoformat()
    week_end = upcoming_monday + timedelta(days=7)

    recipients = _recipients_for_kind(db, "digest.staff_weekly")
    sent = 0
    for recipient in recipients:
        user = db.get(User, recipient.user_id)
        if user is None or not user.email or not user.is_active:
            continue
        shifts = (
            db.query(StaffScheduleEntry)
            .filter(StaffScheduleEntry.user_id == user.id)
            .filter(StaffScheduleEntry.status == "published")
            .filter(StaffScheduleEntry.business_date >= upcoming_monday)
            .filter(StaffScheduleEntry.business_date < week_end)
            .order_by(StaffScheduleEntry.starts_at_local.asc())
            .all()
        )
        if not shifts:
            continue
        if _already_sent(
            db,
            kind="digest.staff_weekly",
            recipient_user_id=user.id,
            window_key=window_key,
        ):
            continue
        rendered = notification_templates.render_staff_weekly_digest(
            staff_user=user,
            week_start=upcoming_monday,
            shifts=[_entry_to_shift_dict(s) for s in shifts],
            admin_url=f"{ADMIN_BASE_URL}/schedule",
        )
        send_rendered_safely(
            to=user.email, rendered=rendered, scope="digest.staff_weekly"
        )
        _record_sent(
            db,
            kind="digest.staff_weekly",
            recipient_user_id=user.id,
            recipient_email=user.email,
            window_key=window_key,
        )
        sent += 1
    db.commit()
    return sent


def run_admin_daily(db: Session, *, digest_date: date) -> int:
    """Fire ``digest.admin_daily`` for each subscribed admin."""
    window_key = digest_date.isoformat()
    recipients = _recipients_for_kind(db, "digest.admin_daily")
    if not recipients:
        return 0

    yesterday_utc = datetime.now(timezone.utc) - timedelta(days=1)
    new_bookings = _new_bookings_since(db, since_utc=yesterday_utc)
    pending_rows = _pending_time_off_rows(db)
    missing_rows = _missing_clock_out_rows(db)
    abandoned = _abandoned_booking_count_since(db, since_utc=yesterday_utc)
    in_store_approvals = _in_store_approvals_since(
        db, since_utc=yesterday_utc
    )

    sent = 0
    for recipient in recipients:
        user = db.get(User, recipient.user_id)
        if user is None or not user.email or not user.is_active:
            continue
        if _already_sent(
            db,
            kind="digest.admin_daily",
            recipient_user_id=user.id,
            window_key=window_key,
        ):
            continue
        rendered = notification_templates.render_admin_daily_digest(
            admin_user=user,
            digest_date=digest_date,
            new_bookings=new_bookings,
            pending_time_off_rows=pending_rows,
            missing_clock_out_rows=missing_rows,
            abandoned_count=abandoned,
            in_store_approvals=in_store_approvals,
            admin_url=ADMIN_BASE_URL,
        )
        send_rendered_safely(
            to=user.email, rendered=rendered, scope="digest.admin_daily"
        )
        _record_sent(
            db,
            kind="digest.admin_daily",
            recipient_user_id=user.id,
            recipient_email=user.email,
            window_key=window_key,
        )
        sent += 1
    db.commit()
    return sent


# ─── Recipients ────────────────────────────────────────────────────────────


def _recipients_for_kind(
    db: Session, kind: str
) -> list[notification_routing.Recipient]:
    """Resolve the recipient set for a digest kind without persisting an
    event. Builds a synthetic ``StaffNotificationEvent`` in memory so
    ``recipients_for`` applies role defaults + preference overrides
    consistently with the real-time path."""
    synthetic = StaffNotificationEvent(
        kind=kind, subject_kind="digest", payload={}
    )
    return list(notification_routing.recipients_for(db, synthetic))


# ─── Dedup ledger ──────────────────────────────────────────────────────────


def _already_sent(
    db: Session,
    *,
    kind: str,
    recipient_user_id: int,
    window_key: str,
) -> bool:
    return (
        db.execute(
            sql_text(
                "SELECT 1 FROM notification_jobs "
                "WHERE kind = :k "
                "  AND recipient_user_id = :uid "
                "  AND subject_kind = 'digest' "
                "  AND status IN ('pending', 'sent') "
                "  AND payload ->> 'digest_window' = :w "
                "LIMIT 1"
            ),
            {"k": kind, "uid": recipient_user_id, "w": window_key},
        ).first()
        is not None
    )


def _record_sent(
    db: Session,
    *,
    kind: str,
    recipient_user_id: int,
    recipient_email: str,
    window_key: str,
) -> None:
    """Write the audit/dedup row for a successful send. The
    ``uq_one_digest_per_user_per_window`` partial unique index protects
    against concurrent runs; a race that sneaks past the
    ``_already_sent`` precheck collides here and raises — which the
    runner's caller catches via the try/except in ``tick`` so a single
    duplicate doesn't poison the rest of the loop."""
    job = NotificationJob(
        kind=kind,
        channel="email",
        recipient=recipient_email,
        recipient_user_id=recipient_user_id,
        subject_kind="digest",
        payload={"digest_window": window_key},
        status="sent",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()


# ─── Data lookups ──────────────────────────────────────────────────────────


def _local_day_bounds_utc(
    digest_date: date,
) -> tuple[datetime, datetime]:
    """[start, end) in UTC for the given local date in the shop tz."""
    tz = shop_tz()
    start_local = datetime.combine(digest_date, time(0, 0), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _today_shift_for(
    db: Session, *, user_id: int, digest_date: date
) -> StaffScheduleEntry | None:
    """Pick the first published shift for this user on the digest date.
    Multiple split shifts on the same day collapse to the first one for
    the digest header; the appointment list still covers everything."""
    return (
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.user_id == user_id)
        .filter(StaffScheduleEntry.business_date == digest_date)
        .filter(StaffScheduleEntry.status == "published")
        .order_by(StaffScheduleEntry.starts_at_local.asc())
        .first()
    )


def _today_appointments_for(
    db: Session, *, user_id: int, digest_date: date
) -> list[Appointment]:
    """Appointments on this stylist's column today. We filter by
    ``assigned_user_id`` only — falling back to "any appointment today
    in the boutique" would flood every stylist with the same list."""
    day_start_utc, day_end_utc = _local_day_bounds_utc(digest_date)
    return (
        db.query(Appointment)
        .filter(Appointment.assigned_user_id == user_id)
        .filter(Appointment.slot_start_at >= day_start_utc)
        .filter(Appointment.slot_start_at < day_end_utc)
        .filter(Appointment.status.in_(("confirmed", "attended")))
        .order_by(Appointment.slot_start_at.asc())
        .all()
    )


def _new_bookings_since(
    db: Session, *, since_utc: datetime
) -> list[Appointment]:
    """Appointments created since yesterday's tick. Used for the admin
    daily digest's "new bookings" section."""
    return (
        db.query(Appointment)
        .filter(Appointment.created_at >= since_utc)
        .filter(Appointment.status.in_(("confirmed", "attended")))
        .order_by(Appointment.slot_start_at.asc())
        .all()
    )


def _pending_time_off_rows(db: Session) -> list[tuple[str, str]]:
    """Open time-off requests with no decision yet. Returns
    (requester, window) tuples ready for ``_details_table``."""
    rows = (
        db.query(TimeOffRequest, User)
        .join(User, User.id == TimeOffRequest.user_id)
        .filter(TimeOffRequest.status == "pending")
        .order_by(TimeOffRequest.starts_at.asc())
        .all()
    )
    out: list[tuple[str, str]] = []
    for request, user in rows:
        who = user.full_name or user.username or "(unknown)"
        try:
            tz = ZoneInfo(APP_TIMEZONE)
            start = request.starts_at.astimezone(tz)
            end = request.ends_at.astimezone(tz)
        except Exception:  # pragma: no cover
            start = request.starts_at
            end = request.ends_at
        if start.date() == end.date():
            window = (
                f"{start.strftime('%a %b %-d')}, "
                f"{start.strftime('%-I:%M %p')} to {end.strftime('%-I:%M %p')}"
            )
        else:
            window = (
                f"{start.strftime('%a %b %-d')} through "
                f"{end.strftime('%a %b %-d')}"
            )
        out.append((who, window))
    return out


def _missing_clock_out_rows(db: Session) -> list[tuple[str, str]]:
    """Open missing-clock-out exceptions. Returns (staffer, detail)
    tuples; the detail names when they clocked in so the admin can
    spot the gap at a glance."""
    rows = (
        db.query(StaffScheduleEntry, User)
        .join(User, User.id == StaffScheduleEntry.user_id)
        .filter(StaffScheduleEntry.attendance_status == "missing_out_punch")
        .order_by(StaffScheduleEntry.business_date.asc())
        .all()
    )
    out: list[tuple[str, str]] = []
    for entry, user in rows:
        who = user.full_name or user.username or "(unknown)"
        try:
            tz = ZoneInfo(APP_TIMEZONE)
            shift_start = entry.starts_at_local.astimezone(tz)
        except Exception:  # pragma: no cover
            shift_start = entry.starts_at_local
        when = shift_start.strftime("%a %b %-d at %-I:%M %p")
        out.append((who, f"Shift started {when}, never clocked out"))
    return out


def _in_store_approvals_since(
    db: Session, *, since_utc: datetime
) -> list[tuple[str, str]]:
    """Quote in-store approvals recorded in the digest window. Reads
    ``staff_notification_events`` rows of kind ``quote.approved_in_store``
    written by ``quote_service.approve_in_store`` (Phase 9.4 D3).

    The kind's timing mode is ``digest`` so ``record_event`` writes the
    row without real-time fan-out; this summarizer is what actually
    surfaces the rows in the admin daily output. Returns ``(label, value)``
    tuples ready for ``_details_table``.

    Format:
      - label: the quote number (or ``"Quote"`` fallback for missing).
      - value: ``"Signed by <name> at <local time>"``.

    Time is rendered in the shop timezone for readability — the admin
    cares about "when on their day" not UTC.
    """
    rows = (
        db.query(StaffNotificationEvent)
        .filter(StaffNotificationEvent.kind == "quote.approved_in_store")
        .filter(StaffNotificationEvent.occurred_at >= since_utc)
        .order_by(StaffNotificationEvent.occurred_at.asc())
        .all()
    )
    tz = shop_tz()
    out: list[tuple[str, str]] = []
    for row in rows:
        payload = dict(row.payload or {})
        quote_number = payload.get("quote_number") or "Quote"
        signature_name = payload.get("signature_name") or "(unsigned)"
        approved_at_iso = payload.get("approved_at")
        when_label = ""
        if approved_at_iso:
            try:
                approved_at = datetime.fromisoformat(approved_at_iso)
                if approved_at.tzinfo is None:
                    approved_at = approved_at.replace(tzinfo=timezone.utc)
                local = approved_at.astimezone(tz)
                when_label = local.strftime(" at %-I:%M %p")
            except (TypeError, ValueError):
                when_label = ""
        out.append((str(quote_number), f"Signed by {signature_name}{when_label}"))
    return out


def _abandoned_booking_count_since(
    db: Session, *, since_utc: datetime
) -> int:
    """Count of widget-booking abandon events in the last 24h. The
    booking widget emits these into ``appointment_session_events`` with
    free-form ``event_name`` strings; ``LIKE '%abandon%'`` catches the
    common variants (``abandon``, ``booking_abandoned``, etc.) without
    locking us to one literal."""
    return int(
        db.execute(
            sql_text(
                "SELECT COUNT(*) FROM appointment_session_events "
                "WHERE created_at >= :since "
                "  AND event_name ILIKE '%abandon%'"
            ),
            {"since": since_utc},
        ).scalar()
        or 0
    )
