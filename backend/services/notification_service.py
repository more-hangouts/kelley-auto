"""Booking notification orchestration.

Enqueue API (called from the booking router) writes rows to
``notification_jobs``; the worker (workers/notifications.py) claims pending
rows whose ``due_at`` has passed, renders templates against the *current*
appointment state, dispatches via the configured transport, and stamps the
outcome.

Render-at-send means cancelled/rescheduled appointments don't accidentally
trigger reminders — the dispatch step skips jobs whose appointment status
has moved out of the live set.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config.settings import (
    ADMIN_BASE_URL,
    BOOKING_INTERNAL_NOTIFICATION_EMAILS,
    SMTP_FROM_EMAIL,
)
from database.models import Appointment, NotificationJob, User
from services.email_transport import (
    EmailMessagePayload,
    EmailTransport,
    get_email_transport,
)
from services.notification_templates import (
    EMAIL_RENDERERS,
    SMS_RENDERERS,
    RenderedEmail,
    is_boutique_profile_attached,
    render_account_locked,
    render_admin_daily_digest,
    render_admin_missing_clock_out,
    render_admin_walk_in_lead_created,
    render_email,
    render_password_changed,
    render_password_reset_request,
    render_pin_reset,
    render_role_changed,
    render_schedule_published,
    render_shift_added,
    render_shift_deleted,
    render_shift_edited,
    render_sms,
    render_staff_booking_assigned,
    render_staff_booking_cancelled,
    render_staff_booking_rescheduled,
    render_staff_daily_digest,
    render_staff_missing_clock_out,
    render_staff_payment_received,
    render_staff_simple_notice,
    render_staff_quote_signed,
    render_staff_weekly_digest,
    render_time_off_amended_to_staff,
    render_time_off_decided_to_staff,
    render_time_off_requested_to_owner,
    render_welcome_new_user,
)
from services.sms_transport import SmsTransport, get_sms_transport

log = logging.getLogger(__name__)


_LIVE_STATUSES = ("pending", "confirmed")
_REMINDER_LEAD_HOURS = 24
_ENRICHMENT_DELAY_MINUTES = 2

_MAX_ATTEMPTS = 5


StaffEmailRenderer = Callable[..., RenderedEmail]


def _call_with_staff_user(
    renderer: Callable[..., RenderedEmail],
) -> StaffEmailRenderer:
    return lambda user, payload, **extra: _call_renderer(
        renderer, payload, staff_user=user, **extra
    )


def _call_with_user(renderer: Callable[..., RenderedEmail]) -> StaffEmailRenderer:
    return lambda user, payload, **extra: _call_renderer(
        renderer, payload, user=user, **extra
    )


def _call_with_admin_user(
    renderer: Callable[..., RenderedEmail],
) -> StaffEmailRenderer:
    return lambda user, payload, **extra: _call_renderer(
        renderer, payload, admin_user=user, **extra
    )


def _call_with_staff_user_as_stylist(
    renderer: Callable[..., RenderedEmail],
) -> StaffEmailRenderer:
    return lambda user, payload, **extra: _call_renderer(
        renderer, payload, stylist=user, **extra
    )


def _call_renderer(
    renderer: Callable[..., RenderedEmail],
    payload: dict[str, Any],
    **fixed_kwargs: Any,
) -> RenderedEmail:
    accepted = set(inspect.signature(renderer).parameters)
    kwargs = {
        key: value
        for key, value in {**payload, **fixed_kwargs}.items()
        if key in accepted
    }
    return renderer(**kwargs)


# Staff-targeted templates render against a recipient user plus a payload
# snapshot. B2 event hooks are responsible for putting the renderer-specific
# fields in payload; the dispatcher only normalizes JSON-friendly temporal
# values and uses the user's current active/email state before sending.
STAFF_EMAIL_RENDERERS: dict[str, StaffEmailRenderer] = {
    "staff.schedule_published": _call_with_staff_user(render_schedule_published),
    "staff.shift_added": _call_with_staff_user(render_shift_added),
    "staff.shift_edited": _call_with_staff_user(render_shift_edited),
    "staff.shift_deleted": _call_with_staff_user(render_shift_deleted),
    "staff.missing_clock_out": _call_with_staff_user(render_staff_missing_clock_out),
    "admin.missing_clock_out": _call_with_staff_user(render_admin_missing_clock_out),
    "admin.time_off_requested": _call_with_staff_user_as_stylist(
        render_time_off_requested_to_owner
    ),
    "staff.time_off_approved": _call_with_staff_user_as_stylist(
        render_time_off_decided_to_staff
    ),
    "staff.time_off_denied": _call_with_staff_user_as_stylist(
        render_time_off_decided_to_staff
    ),
    "staff.time_off_amended": _call_with_staff_user_as_stylist(
        render_time_off_amended_to_staff
    ),
    "staff.pin_reset": _call_with_staff_user(render_pin_reset),
    "admin.password_reset_request": _call_with_user(render_password_reset_request),
    "admin.password_changed": _call_with_user(render_password_changed),
    "staff.welcome_new_user": _call_with_staff_user(render_welcome_new_user),
    "staff.account_locked": _call_with_staff_user(render_account_locked),
    "staff.role_changed": _call_with_staff_user(render_role_changed),
    "staff.booking_assigned": _call_with_staff_user(render_staff_booking_assigned),
    "staff.booking_rescheduled": _call_with_staff_user(
        render_staff_booking_rescheduled
    ),
    "staff.booking_cancelled": _call_with_staff_user(render_staff_booking_cancelled),
    "digest.staff_daily": _call_with_staff_user(render_staff_daily_digest),
    "digest.staff_weekly": _call_with_staff_user(render_staff_weekly_digest),
    "digest.admin_daily": _call_with_admin_user(render_admin_daily_digest),
    "staff.quote_signed": _call_with_staff_user(render_staff_quote_signed),
    "staff.payment_received": _call_with_staff_user(render_staff_payment_received),
    # Scheduling Phase 2: cover/drop shift-request lifecycle. Copy is
    # supplied in the event payload (headline/message/details) so a single
    # generic renderer covers every step.
    "staff.shift_cover_requested": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_cover_accepted": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_cover_approved": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_cover_denied": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_drop_approved": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_drop_denied": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_pickup_denied": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_swap_requested": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_swap_accepted": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_swap_approved": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "staff.shift_swap_denied": _call_with_staff_user(
        render_staff_simple_notice
    ),
    "admin.walk_in_lead_created": lambda user, payload: render_admin_walk_in_lead_created(
        captured_by=payload["captured_by"],
        appointment=payload["appointment"],
        contact=payload["contact"],
        notes=payload.get("notes"),
        admin_url=payload.get("admin_url", ADMIN_BASE_URL),
    ),
}


def has_staff_email_renderer(kind: str) -> bool:
    return kind in STAFF_EMAIL_RENDERERS


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


def _enqueue(
    db: Session,
    *,
    kind: str,
    channel: str,
    appointment_id: int,
    recipient: str,
    due_at: datetime | None = None,
    payload: dict | None = None,
) -> NotificationJob:
    job = NotificationJob(
        kind=kind,
        channel=channel,
        appointment_id=appointment_id,
        recipient=recipient,
        due_at=due_at or datetime.now(timezone.utc),
        payload=payload or {},
    )
    db.add(job)
    db.flush()
    return job


def enqueue_staff_job(
    db: Session,
    *,
    kind: str,
    recipient_user_id: int,
    recipient: str,
    subject_kind: str | None = None,
    subject_id: int | None = None,
    due_at: datetime | None = None,
    payload: dict | None = None,
) -> NotificationJob:
    """Queue a staff-targeted email job.

    Staff jobs share ``notification_jobs`` with legacy customer booking
    jobs, but they are keyed by ``recipient_user_id`` and a polymorphic
    subject pair instead of being forced through ``appointment_id``.
    """
    job = NotificationJob(
        kind=kind,
        channel="email",
        recipient_user_id=recipient_user_id,
        recipient=recipient,
        subject_kind=subject_kind,
        subject_id=subject_id,
        due_at=due_at or datetime.now(timezone.utc),
        payload=payload or {},
    )
    db.add(job)
    db.flush()
    return job


def enqueue_for_new_booking(db: Session, appt: Appointment) -> None:
    """Customer confirmation + internal notification + enrichment + reminder.

    The profile invitation is skipped when a Boutique Experience profile is
    already attached to this lead (calculator-first path), so the customer
    doesn't get asked to fill out something they just submitted.
    """
    now = datetime.now(timezone.utc)
    _enqueue(
        db,
        kind="booking_confirmation",
        channel="email",
        appointment_id=appt.id,
        recipient=appt.email,
    )
    for staff_email in BOOKING_INTERNAL_NOTIFICATION_EMAILS:
        _enqueue(
            db,
            kind="internal_new_booking",
            channel="email",
            appointment_id=appt.id,
            recipient=staff_email,
        )
    if not is_boutique_profile_attached(appt):
        _enqueue(
            db,
            kind="enrichment_invitation",
            channel="email",
            appointment_id=appt.id,
            recipient=appt.email,
            due_at=now + timedelta(minutes=_ENRICHMENT_DELAY_MINUTES),
        )
    reminder_due = appt.slot_start_at - timedelta(hours=_REMINDER_LEAD_HOURS)
    if reminder_due > now:
        _enqueue(
            db,
            kind="reminder",
            channel="email",
            appointment_id=appt.id,
            recipient=appt.email,
            due_at=reminder_due,
        )


def enqueue_for_reschedule(
    db: Session, *, original_id: int, new_appt: Appointment
) -> None:
    """Reschedule confirmation + enrichment + reminder for the new appt.

    Phase 1 keeps the Boutique Experience profile attached to the original
    appointment as historical data and aggregates "complete?" across the
    CRM event, so a customer who finished their profile before
    rescheduling does not get re-asked. The invitation enqueue checks
    that here.
    """
    cancel_pending_for_appointment(db, original_id)
    _enqueue(
        db,
        kind="reschedule_confirmation",
        channel="email",
        appointment_id=new_appt.id,
        recipient=new_appt.email,
    )
    now = datetime.now(timezone.utc)
    if not is_boutique_profile_attached(new_appt):
        _enqueue(
            db,
            kind="enrichment_invitation",
            channel="email",
            appointment_id=new_appt.id,
            recipient=new_appt.email,
            due_at=now + timedelta(minutes=_ENRICHMENT_DELAY_MINUTES),
        )
    reminder_due = new_appt.slot_start_at - timedelta(hours=_REMINDER_LEAD_HOURS)
    if reminder_due > now:
        _enqueue(
            db,
            kind="reminder",
            channel="email",
            appointment_id=new_appt.id,
            recipient=new_appt.email,
            due_at=reminder_due,
        )


def enqueue_for_cancellation(db: Session, appt: Appointment) -> None:
    cancel_pending_for_appointment(db, appt.id)
    _enqueue(
        db,
        kind="cancellation_confirmation",
        channel="email",
        appointment_id=appt.id,
        recipient=appt.email,
    )


def cancel_pending_for_appointment(db: Session, appointment_id: int) -> int:
    """Mark all still-pending jobs for an appointment as cancelled. Returns count."""
    res = db.execute(
        sql_text(
            """
            UPDATE notification_jobs
               SET status = 'cancelled', updated_at = NOW()
             WHERE appointment_id = :aid
               AND status = 'pending'
            """
        ),
        {"aid": appointment_id},
    )
    return res.rowcount or 0


# ---------------------------------------------------------------------------
# Worker dispatch
# ---------------------------------------------------------------------------


def claim_due_jobs(db: Session, *, limit: int = 25) -> list[NotificationJob]:
    """Claim up to ``limit`` due pending jobs by flipping their attempts.

    The select is wrapped in ``FOR UPDATE SKIP LOCKED`` so multiple workers
    can run concurrently without stepping on each other — the row stays
    locked for the rest of the transaction, and other workers skip it.
    Each claim increments ``attempts``; a crash mid-dispatch eventually
    surfaces as a permanent failure rather than getting retried forever.
    """
    now = datetime.now(timezone.utc)
    rows = (
        db.query(NotificationJob)
        .filter(
            NotificationJob.status == "pending",
            NotificationJob.due_at <= now,
        )
        .order_by(NotificationJob.due_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        row.attempts = (row.attempts or 0) + 1
        row.updated_at = now
    db.flush()
    return rows


def dispatch_job(
    db: Session,
    job: NotificationJob,
    *,
    email_transport: EmailTransport,
    sms_transport: SmsTransport,
) -> None:
    """Render + send a single job, updating its status in place."""
    if job.channel == "email" and job.kind in STAFF_EMAIL_RENDERERS:
        _dispatch_staff_email(db, job, email_transport=email_transport)
        return

    appt = (
        db.query(Appointment)
        .filter(Appointment.id == job.appointment_id)
        .first()
    )
    if appt is None:
        _mark(job, "failed", "appointment missing")
        return

    if _should_skip_for_status(job.kind, appt.status):
        _mark(job, "cancelled", f"appointment status={appt.status}")
        return

    try:
        if job.channel == "email":
            if job.kind not in EMAIL_RENDERERS:
                raise ValueError(f"unknown email kind: {job.kind}")
            email_transport.send(render_email(job.kind, appt, job.recipient))
        elif job.channel == "sms":
            if job.kind not in SMS_RENDERERS:
                raise ValueError(f"unknown sms kind: {job.kind}")
            sms_transport.send(render_sms(job.kind, appt, job.recipient))
        else:
            raise ValueError(f"unknown channel: {job.channel}")
    except Exception as exc:
        log.exception(
            "notification dispatch failed job_id=%s kind=%s channel=%s",
            job.id,
            job.kind,
            job.channel,
        )
        if (job.attempts or 0) >= _MAX_ATTEMPTS:
            _mark(job, "failed", str(exc)[:500])
        else:
            # Back to pending; due_at re-checked next tick.
            job.status = "pending"
            job.last_error = str(exc)[:500]
            job.updated_at = datetime.now(timezone.utc)
        return

    _mark(job, "sent", None)


def _dispatch_staff_email(
    db: Session,
    job: NotificationJob,
    *,
    email_transport: EmailTransport,
) -> None:
    if job.recipient_user_id is None:
        _mark(job, "failed", "recipient_user_id missing")
        return

    user = db.query(User).filter(User.id == job.recipient_user_id).first()
    if user is None:
        _mark(job, "failed", "recipient user missing")
        return
    if not user.is_active:
        _mark(job, "cancelled", "recipient user inactive")
        return
    if not user.email:
        _mark(job, "failed", "recipient user email missing")
        return

    # Hydrate the subject model when one is named on the job. The
    # staff-renderer signature uses model instances (e.g. ``appointment``)
    # because they were originally designed for direct call-site use;
    # ``record_event`` only persists ``subject_id`` to JSONB, so the
    # dispatcher loads the model here and forwards it as a kwarg. The
    # ``_call_with_*`` adapters filter kwargs by the renderer's accepted
    # parameter set, so renderers that don't take the subject ignore it.
    extra_kwargs: dict[str, Any] = {}
    subject_kind = job.subject_kind
    if subject_kind == "appointment" and job.subject_id is not None:
        subject = db.get(Appointment, job.subject_id)
        if subject is None:
            _mark(job, "failed", "appointment subject missing")
            return
        extra_kwargs["appointment"] = subject

    renderer = STAFF_EMAIL_RENDERERS[job.kind]
    try:
        rendered = renderer(
            user, _normalize_staff_payload(job.payload or {}), **extra_kwargs
        )
        email_transport.send(_email_payload(user.email, rendered))
    except Exception as exc:
        log.exception(
            "staff notification dispatch failed job_id=%s kind=%s",
            job.id,
            job.kind,
        )
        if (job.attempts or 0) >= _MAX_ATTEMPTS:
            _mark(job, "failed", str(exc)[:500])
        else:
            job.status = "pending"
            job.last_error = str(exc)[:500]
            job.updated_at = datetime.now(timezone.utc)
        return

    _mark(job, "sent", None)


def _email_payload(recipient: str, rendered: RenderedEmail) -> EmailMessagePayload:
    return EmailMessagePayload(
        to=recipient,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
        reply_to=SMTP_FROM_EMAIL or None,
    )


def _normalize_staff_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce JSONB-friendly payload values back to renderer-friendly types."""
    return _coerce_temporals(deepcopy(payload))


def _coerce_temporals(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _coerce_temporals(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_temporals(v, key) for v in value]
    if not isinstance(value, str) or key is None:
        return value

    if key in {"digest_date", "week_start"}:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value

    if (
        key.endswith("_at")
        or key in {"starts_at", "ends_at", "locked_until"}
    ):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    return value


def _should_skip_for_status(kind: str, status: str) -> bool:
    # Reminders + enrichment invites should not fire if the appointment is
    # already cancelled/rescheduled/attended/no_show.
    if kind in ("reminder", "enrichment_invitation", "sms_reminder"):
        return status not in _LIVE_STATUSES
    # Confirmation messages still go out even if the appointment was just
    # cancelled — the customer should know the round-trip happened.
    return False


def _mark(job: NotificationJob, status: str, error: str | None) -> None:
    job.status = status
    job.last_error = error
    if status == "sent":
        job.sent_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Convenience for tests / manual runs
# ---------------------------------------------------------------------------


def run_once(db: Session) -> int:
    """Process all currently-due jobs in one pass. Returns count processed."""
    email = get_email_transport()
    sms = get_sms_transport()
    jobs = claim_due_jobs(db)
    if not jobs:
        return 0
    for job in jobs:
        dispatch_job(db, job, email_transport=email, sms_transport=sms)
    db.commit()
    return len(jobs)
