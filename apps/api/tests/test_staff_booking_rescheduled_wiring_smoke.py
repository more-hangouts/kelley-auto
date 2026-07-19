"""Smoke for staff.booking_rescheduled wiring (#13 — closes the
booking-lifecycle trio with #12 / #14).

Verifies that:

  1. An assigned appointment passed to ``notify_booking_rescheduled``
     writes one ``staff_notification_events`` row and enqueues one
     ``notification_jobs`` row to the assignee. The payload carries
     ``previous_slot_start_at`` as an ISO string (JSONB-friendly) and
     a ``/appointments/<id>`` admin url.
  2. An unassigned appointment is a silent no-op (no event, no job).
  3. The dispatcher renders the rescheduled email end-to-end —
     ``_normalize_staff_payload`` coerces the ISO string back to a
     ``datetime`` (it has an ``_at`` suffix), the dispatcher hydrates
     the new Appointment from ``subject_id``, and the renderer wires
     both into its template.
  4. The customer reschedule path
     (``api/routers/booking.post_reschedule``) carries
     ``assigned_user_id`` forward onto the new row — without this, #13
     could never fire from a real customer-initiated reschedule.

Naming reuses the ``Sales Assign Smoke`` family already in cleanup SQL.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    Contact,
    Event,
    NotificationJob,
    StaffNotificationEvent,
    User,
)
from services import notification_service  # noqa: E402
from services.staff_booking_notifications import (  # noqa: E402
    notify_booking_rescheduled,
)

_user_ids: list[int] = []
_contact_ids: list[int] = []
_event_ids: list[int] = []
_appt_ids: list[int] = []


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


class _RejectingSmsTransport:
    def send(self, msg) -> None:  # pragma: no cover
        raise AssertionError("sms transport should not be used")


def _mkuser(*, role: str, label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-smoke-{label}-{suffix}",
            email=f"{role}-smoke-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Sales Assign Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _seed_appointment(*, assigned_user_id: int | None) -> int:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55507{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Reschedule {tag}",
            email=f"sa-smoke-resched-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["sales-assign-smoke"],
        )
        db.add(contact)
        db.flush()
        _contact_ids.append(contact.id)
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Sales Assign Smoke Reschedule Quince {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=assigned_user_id,
        )
        db.add(event)
        db.flush()
        _event_ids.append(event.id)
        slot = datetime.now(timezone.utc) + timedelta(days=4)
        appt = Appointment(
            confirmation_code=f"SAR{tag}",
            slot_start_at=slot,
            slot_end_at=slot + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name=f"Cel {tag}",
            party_size_bucket="pair",
            phone=contact.phone,
            phone_e164=contact.phone_e164,
            email=contact.email,
            status="confirmed",
            assigned_user_id=assigned_user_id,
            contact_id=contact.id,
            crm_event_id=event.id,
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _appt_ids.append(appt.id)
        return appt.id
    finally:
        db.close()


def _count_events(*, kind: str, subject_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(StaffNotificationEvent)
            .filter(StaffNotificationEvent.kind == kind)
            .filter(StaffNotificationEvent.subject_id == subject_id)
            .count()
        )
    finally:
        db.close()


def _jobs(*, recipient_user_id: int, kind: str) -> list[NotificationJob]:
    db = SessionLocal()
    try:
        return (
            db.query(NotificationJob)
            .filter(NotificationJob.recipient_user_id == recipient_user_id)
            .filter(NotificationJob.kind == kind)
            .order_by(NotificationJob.id.asc())
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE subject_kind = 'appointment' "
                    "  AND subject_id = ANY(:ids)"
                ),
                {"ids": _appt_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE subject_kind = 'appointment' "
                    "  AND subject_id = ANY(:ids)"
                ),
                {"ids": _appt_ids},
            )
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:ids)"
                ),
                {"ids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE actor_user_id = ANY(:ids)"
                ),
                {"ids": _user_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _carry_forward_check() -> None:
    """Static check that ``api/routers/booking.py`` carries
    ``assigned_user_id`` forward in the reschedule path. Without this
    the helper is wired correctly but the route would always pass an
    unassigned appointment, silently making #13 unreachable from real
    customer traffic.
    """
    booking_py = (_REPO_ROOT / "api/routers/booking.py").read_text()
    # The new_appt construction in post_reschedule must include the
    # assigned_user_id field copied from original. Both substrings
    # appear in the same Appointment(...) block.
    assert "assigned_user_id=original.assigned_user_id" in booking_py, (
        "customer reschedule must carry assigned_user_id forward "
        "(see api/routers/booking.py post_reschedule)"
    )


def main() -> None:
    admin_id = _mkuser(role="admin", label="actor")
    sales_id = _mkuser(role="sales", label="A")

    _carry_forward_check()
    print("  ok   customer reschedule carries assigned_user_id forward")

    # ============================================================
    # 1. Assigned reschedule → 1 event, 1 job with ISO payload
    # ============================================================
    appt_id = _seed_appointment(assigned_user_id=sales_id)
    previous_slot = datetime.now(timezone.utc) + timedelta(days=2)
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appt_id)
        notify_booking_rescheduled(
            db,
            appt,
            previous_slot_start_at=previous_slot,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()

    assert (
        _count_events(kind="staff.booking_rescheduled", subject_id=appt_id) == 1
    )
    jobs = _jobs(
        recipient_user_id=sales_id, kind="staff.booking_rescheduled"
    )
    job = next((j for j in jobs if j.subject_id == appt_id), None)
    assert job is not None, jobs
    assert job.subject_kind == "appointment"
    # Payload stores the timestamp as an ISO string so JSONB round-trip
    # is lossless; the dispatcher coerces it back via the `_at` suffix.
    assert job.payload.get("previous_slot_start_at"), job.payload
    iso = job.payload["previous_slot_start_at"]
    assert isinstance(iso, str), iso
    assert iso == previous_slot.isoformat(), (iso, previous_slot.isoformat())
    assert job.payload.get("admin_url", "").endswith(f"/appointments/{appt_id}")
    print("  ok   assigned reschedule emits one event + one job with ISO payload")

    # ============================================================
    # 2. Unassigned reschedule → silent no-op
    # ============================================================
    appt_id_unassigned = _seed_appointment(assigned_user_id=None)
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appt_id_unassigned)
        notify_booking_rescheduled(
            db,
            appt,
            previous_slot_start_at=previous_slot,
            actor_user_id=None,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(
            kind="staff.booking_rescheduled", subject_id=appt_id_unassigned
        )
        == 0
    ), "unassigned reschedule must be silent"
    print("  ok   unassigned reschedule is a silent no-op")

    # ============================================================
    # 3. Dispatcher renders end-to-end with hydrated Appointment +
    #    coerced datetime
    # ============================================================
    fake_email = _RecordingTransport()
    db = SessionLocal()
    try:
        job_to_dispatch = (
            db.query(NotificationJob)
            .filter(NotificationJob.id == job.id)
            .first()
        )
        notification_service.dispatch_job(
            db,
            job_to_dispatch,
            email_transport=fake_email,
            sms_transport=_RejectingSmsTransport(),
        )
        db.commit()
    finally:
        db.close()
    assert len(fake_email.sent) == 1, fake_email.sent
    sent = fake_email.sent[0]
    # The render uses words like "moved" or "rescheduled" in the
    # subject — either is acceptable so the renderer can change its
    # phrasing without breaking the wire test.
    subject_lower = sent.subject.lower()
    assert "moved" in subject_lower or "rescheduled" in subject_lower, sent.subject
    print("  ok   dispatcher renders staff.booking_rescheduled end-to-end")

    # ============================================================
    # 4. Customer-initiated actor (actor_user_id=None) is accepted
    # ============================================================
    customer_appt_id = _seed_appointment(assigned_user_id=sales_id)
    db = SessionLocal()
    try:
        appt = db.get(Appointment, customer_appt_id)
        notify_booking_rescheduled(
            db,
            appt,
            previous_slot_start_at=previous_slot,
            actor_user_id=None,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(
            kind="staff.booking_rescheduled", subject_id=customer_appt_id
        )
        == 1
    )
    db = SessionLocal()
    try:
        ev = (
            db.query(StaffNotificationEvent)
            .filter(
                StaffNotificationEvent.kind == "staff.booking_rescheduled"
            )
            .filter(StaffNotificationEvent.subject_id == customer_appt_id)
            .first()
        )
        assert ev is not None
        assert ev.actor_user_id is None, ev.actor_user_id
    finally:
        db.close()
    print("  ok   customer-initiated reschedule writes actor_user_id=None")

    print("\nstaff_booking_rescheduled_wiring smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
