"""Smoke for staff.booking_assigned wiring (first record_event production
call site).

Verifies that:

  1. ``services.sales_assignment.reassign_appointment`` writes a
     ``staff_notification_events`` row and enqueues a
     ``notification_jobs`` row addressed to the new assignee. No event
     when unassigning (``None``).
  2. ``reassign_event_lead`` cascade emits ONE
     ``staff.booking_assigned`` event per future-dated appointment, with
     subject_kind='appointment' and subject_id set to that appointment.
     The past-dated appointment gets no event.
  3. ``services.walk_in_service.create_walk_in_lead`` fires the same
     event when ``assigned_user_id`` is provided.
  4. The dispatcher hydrates the Appointment from
     ``(subject_kind='appointment', subject_id=<id>)`` and renders the
     email without crashing — this is the new code path in
     ``notification_service._dispatch_staff_email``.

Names use the existing ``Sales Assign Smoke %`` naming family
(already in cleanup SQL) so leakage is sweepable.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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
from services import notification_service, sales_assignment, walk_in_service  # noqa: E402
from services.walk_in_service import (  # noqa: E402
    WalkInContactInput,
    WalkInEnrichmentInput,
    WalkInEventInput,
)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_appt_ids: list[int] = []


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


class _RejectingSmsTransport:
    def send(self, msg) -> None:  # pragma: no cover - never reached
        raise AssertionError("sms transport should not be used")


def _make_user(*, role: str, label: str) -> int:
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
        _created_user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _seed_appointment(*, owner_user_id: int) -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55503{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Email {tag}",
            email=f"sa-smoke-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["sales-assign-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Sales Assign Smoke Email Quince {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)
        slot = datetime.now(timezone.utc) + timedelta(days=3)
        appt = Appointment(
            confirmation_code=f"SAE{tag}",
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
            assigned_user_id=owner_user_id,
            contact_id=contact.id,
            crm_event_id=event.id,
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _created_appt_ids.append(appt.id)
        return {"appt_id": appt.id, "event_id": event.id}
    finally:
        db.close()


def _seed_event_with_3_appts(*, owner_user_id: int) -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55504{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Cascade Email {tag}",
            email=f"sa-cascade-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["sales-assign-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Sales Assign Smoke Cascade Email {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)
        now = datetime.now(timezone.utc)
        ids = {}
        for idx, (key, slot) in enumerate(
            {
                "past": now - timedelta(days=2),
                "f1": now + timedelta(days=1),
                "f2": now + timedelta(days=5),
            }.items()
        ):
            appt = Appointment(
                confirmation_code=f"SAEC{tag}{idx:02d}",
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
                assigned_user_id=owner_user_id,
                contact_id=contact.id,
                crm_event_id=event.id,
            )
            db.add(appt)
            db.flush()
            _created_appt_ids.append(appt.id)
            ids[key] = appt.id
        db.commit()
        return {"event_id": event.id, **ids}
    finally:
        db.close()


def _count_events(*, kind: str, subject_id: int | None = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(StaffNotificationEvent).filter(
            StaffNotificationEvent.kind == kind
        )
        if subject_id is not None:
            q = q.filter(StaffNotificationEvent.subject_id == subject_id)
        return q.count()
    finally:
        db.close()


def _jobs_for(*, recipient_user_id: int, kind: str) -> list[NotificationJob]:
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
        if _created_appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE subject_kind = 'appointment' "
                    "  AND subject_id = ANY(:ids)"
                ),
                {"ids": _created_appt_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE subject_kind = 'appointment' "
                    "  AND subject_id = ANY(:ids)"
                ),
                {"ids": _created_appt_ids},
            )
        if _created_user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:ids)"
                ),
                {"ids": _created_user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE actor_user_id = ANY(:ids)"
                ),
                {"ids": _created_user_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
        if _created_appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_enrichment_responses "
                    "WHERE appointment_id = ANY(:ids)"
                ),
                {"ids": _created_appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appt_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
        if _created_contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _created_contact_ids},
            )
        if _created_user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _created_user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    admin_id = _make_user(role="admin", label="actor")
    sales_a_id = _make_user(role="sales", label="A")
    sales_b_id = _make_user(role="sales", label="B")

    # ============================================================
    # 1. reassign_appointment fires staff.booking_assigned
    # ============================================================
    seed = _seed_appointment(owner_user_id=sales_a_id)
    appt_id = seed["appt_id"]

    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=sales_b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()

    assert _count_events(kind="staff.booking_assigned", subject_id=appt_id) == 1
    jobs = _jobs_for(recipient_user_id=sales_b_id, kind="staff.booking_assigned")
    assert len(jobs) == 1, jobs
    job = jobs[0]
    assert job.subject_kind == "appointment"
    assert job.subject_id == appt_id
    assert job.payload.get("admin_url"), job.payload
    print("  ok   reassign_appointment fires staff.booking_assigned to new assignee")

    # Idempotent reassign to the same value → no extra event
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=sales_b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()
    assert _count_events(kind="staff.booking_assigned", subject_id=appt_id) == 1
    print("  ok   idempotent reassign emits no extra event")

    # Unassign (None) → no event, no job
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=None,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()
    assert _count_events(kind="staff.booking_assigned", subject_id=appt_id) == 1
    print("  ok   unassign emits no new event")

    # ============================================================
    # 2. Dispatcher hydrates Appointment + renders without crashing
    # ============================================================
    fake_email = _RecordingTransport()
    fake_sms = _RejectingSmsTransport()
    db = SessionLocal()
    try:
        # The single live job from step 1.
        live = (
            db.query(NotificationJob)
            .filter(NotificationJob.id == job.id)
            .first()
        )
        notification_service.dispatch_job(
            db,
            live,
            email_transport=fake_email,
            sms_transport=fake_sms,
        )
        db.commit()
    finally:
        db.close()
    assert len(fake_email.sent) == 1, fake_email.sent
    sent = fake_email.sent[0]
    # The recipient is sales_b's email (resolved by intrinsic targeting
    # from appt.assigned_user_id at fan-out time — but the appointment
    # is now unassigned, so the recipient was captured at enqueue time
    # off the job row instead of the appointment's current state).
    db = SessionLocal()
    try:
        sales_b = db.get(User, sales_b_id)
        assert sent.to == sales_b.email, (sent.to, sales_b.email)
    finally:
        db.close()
    assert "booking" in sent.subject.lower() or "calendar" in sent.subject.lower(), sent.subject
    print("  ok   dispatcher hydrates Appointment and renders staff.booking_assigned")

    # ============================================================
    # 3. reassign_event_lead cascade fires per-future-appointment event
    # ============================================================
    cascade = _seed_event_with_3_appts(owner_user_id=sales_a_id)

    db = SessionLocal()
    try:
        sales_assignment.reassign_event_lead(
            db,
            event_id=cascade["event_id"],
            new_owner_id=sales_b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()

    # Exactly 2 events — one per future appointment, none for the past one.
    assert _count_events(
        kind="staff.booking_assigned", subject_id=cascade["f1"]
    ) == 1
    assert _count_events(
        kind="staff.booking_assigned", subject_id=cascade["f2"]
    ) == 1
    assert _count_events(
        kind="staff.booking_assigned", subject_id=cascade["past"]
    ) == 0
    print("  ok   lead cascade fires staff.booking_assigned only for future appts")

    # ============================================================
    # 4. walk_in_service with assigned_user_id fires the same event
    # ============================================================
    db = SessionLocal()
    try:
        result = walk_in_service.create_walk_in_lead(
            db,
            actor_user_id=admin_id,
            contact_in=WalkInContactInput(
                first_name="Walk",
                last_name="In",
                display_name=f"Sales Assign Smoke Walkin {uuid.uuid4().hex[:6].upper()}",
                email=f"sa-smoke-walkin-{uuid.uuid4().hex[:6]}@example.com",
                phone=f"(210) 555-{uuid.uuid4().int % 10_000:04d}",
            ),
            event_in=WalkInEventInput(
                celebrant_first_name="Celebrant",
                celebrant_last_name=None,
                event_name=f"Sales Assign Smoke Walkin Quince {uuid.uuid4().hex[:6].upper()}",
                event_date=date(2027, 7, 4),
                owner_user_id=None,
            ),
            enrichment_in=WalkInEnrichmentInput(
                party_size_bucket="pair",
                court_size=None,
                quince_theme=None,
                quince_theme_colors=None,
                budget_range=None,
                dress_styles=None,
                colors=None,
                notes=None,
            ),
            assigned_user_id=sales_a_id,
        )
        db.commit()
        new_appt_id = result.appointment.id
        new_event_id = result.event.id
        new_contact_id = result.contact.id
        _created_appt_ids.append(new_appt_id)
        _created_event_ids.append(new_event_id)
        _created_contact_ids.append(new_contact_id)
    finally:
        db.close()

    assert _count_events(
        kind="staff.booking_assigned", subject_id=new_appt_id
    ) == 1
    jobs_a = _jobs_for(recipient_user_id=sales_a_id, kind="staff.booking_assigned")
    # sales_a got the walk-in event (and previously the cascade); just
    # confirm the new appointment shows up among their jobs.
    assert any(j.subject_id == new_appt_id for j in jobs_a), [
        (j.id, j.subject_id) for j in jobs_a
    ]
    print("  ok   walk-in with assigned_user_id fires staff.booking_assigned")

    print("\nstaff_booking_assigned_wiring smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
