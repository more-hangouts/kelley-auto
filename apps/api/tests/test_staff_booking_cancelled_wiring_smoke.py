"""Smoke for staff.booking_cancelled wiring (the natural pair to #12).

Verifies that:

  1. Reassignment A→B emits both events in one transaction:
     ``staff.booking_cancelled`` for A (loss, fires BEFORE the column
     is rewritten so intrinsic targeting still resolves to A) plus
     ``staff.booking_assigned`` for B (gain, fires after the write).
     Two jobs queued, one per stylist.
  2. Unassign A→None emits only the loss. No "assigned to no one"
     event.
  3. First-time set None→B emits only the gain. No "lost it from no
     one" event.
  4. Idempotent same-value reassign emits nothing.
  5. Lead cascade emits one loss + one gain per future-dated
     appointment whose assignee changes (none for past appointments).
  6. Sales-side cancellation via ``apply_status_action(action="cancelled")``
     emits the loss for the still-assigned stylist.
  7. The dispatcher renders ``staff.booking_cancelled`` end-to-end
     against the Appointment hydrated from ``subject_id`` — same code
     path #12 proved, different renderer.

Naming reuses the ``Sales Assign Smoke`` family already in
``scripts/cleanup_admin_smoke_pollution.sql``.
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
from services import (  # noqa: E402
    notification_service,
    sales_appointments,
    sales_assignment,
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


def _seed_assigned_appointment(
    *, owner_user_id: int, future: bool = True
) -> int:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55505{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Cancel {tag}",
            email=f"sa-smoke-cancel-{tag.lower()}@example.com",
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
            event_name=f"Sales Assign Smoke Cancel Quince {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
        )
        db.add(event)
        db.flush()
        _event_ids.append(event.id)
        offset = timedelta(days=3) if future else timedelta(days=-2)
        slot = datetime.now(timezone.utc) + offset
        appt = Appointment(
            confirmation_code=f"SAC{tag}",
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
        _appt_ids.append(appt.id)
        return appt.id
    finally:
        db.close()


def _seed_event_with_3_appts(*, owner_user_id: int) -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55506{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke CancelCascade {tag}",
            email=f"sa-cascade-cancel-{tag.lower()}@example.com",
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
            event_name=f"Sales Assign Smoke CancelCascade {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
        )
        db.add(event)
        db.flush()
        _event_ids.append(event.id)
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
                confirmation_code=f"SACC{tag}{idx:02d}",
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
            _appt_ids.append(appt.id)
            ids[key] = appt.id
        db.commit()
        return {"event_id": event.id, **ids}
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


def main() -> None:
    admin_id = _mkuser(role="admin", label="actor")
    a_id = _mkuser(role="sales", label="A")
    b_id = _mkuser(role="sales", label="B")

    # ============================================================
    # 1. Reassign A→B emits BOTH cancelled (A) and assigned (B)
    # ============================================================
    appt_id = _seed_assigned_appointment(owner_user_id=a_id)
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()

    assert _count_events(kind="staff.booking_cancelled", subject_id=appt_id) == 1
    assert _count_events(kind="staff.booking_assigned", subject_id=appt_id) == 1
    a_jobs = _jobs(recipient_user_id=a_id, kind="staff.booking_cancelled")
    b_jobs = _jobs(recipient_user_id=b_id, kind="staff.booking_assigned")
    assert any(j.subject_id == appt_id for j in a_jobs), [
        (j.id, j.subject_id) for j in a_jobs
    ]
    assert any(j.subject_id == b_id for j in b_jobs) or any(
        j.subject_id == appt_id for j in b_jobs
    )
    print("  ok   reassign A→B fires cancelled(A) + assigned(B)")

    # ============================================================
    # 2. Unassign A→None emits cancelled only
    # ============================================================
    # First reset to A (no cross-event noise; this fires cancelled(B)
    # + assigned(A), which is itself worth confirming).
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=a_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()
    # Now A→None.
    cancelled_before = _count_events(
        kind="staff.booking_cancelled", subject_id=appt_id
    )
    assigned_before = _count_events(
        kind="staff.booking_assigned", subject_id=appt_id
    )
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
    assert (
        _count_events(kind="staff.booking_cancelled", subject_id=appt_id)
        == cancelled_before + 1
    )
    assert (
        _count_events(kind="staff.booking_assigned", subject_id=appt_id)
        == assigned_before
    ), "unassign must not emit assigned"
    print("  ok   unassign A→None fires cancelled only")

    # ============================================================
    # 3. First-time set None→B emits assigned only
    # ============================================================
    cancelled_before = _count_events(
        kind="staff.booking_cancelled", subject_id=appt_id
    )
    assigned_before = _count_events(
        kind="staff.booking_assigned", subject_id=appt_id
    )
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(kind="staff.booking_cancelled", subject_id=appt_id)
        == cancelled_before
    ), "first-time-set must not emit cancelled"
    assert (
        _count_events(kind="staff.booking_assigned", subject_id=appt_id)
        == assigned_before + 1
    )
    print("  ok   first-time None→B fires assigned only")

    # ============================================================
    # 4. Idempotent same-value reassign emits nothing
    # ============================================================
    cancelled_before = _count_events(
        kind="staff.booking_cancelled", subject_id=appt_id
    )
    assigned_before = _count_events(
        kind="staff.booking_assigned", subject_id=appt_id
    )
    db = SessionLocal()
    try:
        sales_assignment.reassign_appointment(
            db,
            appointment_id=appt_id,
            new_assignee_id=b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(kind="staff.booking_cancelled", subject_id=appt_id)
        == cancelled_before
    )
    assert (
        _count_events(kind="staff.booking_assigned", subject_id=appt_id)
        == assigned_before
    )
    print("  ok   idempotent reassign emits nothing")

    # ============================================================
    # 5. Lead cascade: per future appt → 1 cancelled + 1 assigned;
    #    past appt → nothing
    # ============================================================
    seed = _seed_event_with_3_appts(owner_user_id=a_id)
    db = SessionLocal()
    try:
        sales_assignment.reassign_event_lead(
            db,
            event_id=seed["event_id"],
            new_owner_id=b_id,
            actor_user_id=admin_id,
        )
        db.commit()
    finally:
        db.close()

    for key in ("f1", "f2"):
        appt_subject = seed[key]
        assert (
            _count_events(
                kind="staff.booking_cancelled", subject_id=appt_subject
            )
            == 1
        )
        assert (
            _count_events(
                kind="staff.booking_assigned", subject_id=appt_subject
            )
            == 1
        )
    assert _count_events(
        kind="staff.booking_cancelled", subject_id=seed["past"]
    ) == 0
    assert _count_events(
        kind="staff.booking_assigned", subject_id=seed["past"]
    ) == 0
    print("  ok   cascade fires loss+gain per future appt only")

    # ============================================================
    # 6. Sales-side cancellation via apply_status_action
    # ============================================================
    sc_appt_id = _seed_assigned_appointment(owner_user_id=a_id)
    db = SessionLocal()
    try:
        sales_appointments.apply_status_action(
            db,
            appointment_id=sc_appt_id,
            action="cancelled",
            actor_user_id=a_id,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(kind="staff.booking_cancelled", subject_id=sc_appt_id)
        == 1
    )
    # Idempotent re-tap → no extra event
    db = SessionLocal()
    try:
        sales_appointments.apply_status_action(
            db,
            appointment_id=sc_appt_id,
            action="cancelled",
            actor_user_id=a_id,
        )
        db.commit()
    finally:
        db.close()
    assert (
        _count_events(kind="staff.booking_cancelled", subject_id=sc_appt_id)
        == 1
    )
    print("  ok   sales-side cancel fires cancelled (idempotent)")

    # ============================================================
    # 7. Dispatcher renders staff.booking_cancelled end-to-end
    # ============================================================
    # Find any cancelled job we queued in step 1 to A. The dispatcher
    # path is the same hydrate-Appointment-from-subject_id codepath #12
    # proved; this step confirms the cancelled renderer doesn't crash.
    db = SessionLocal()
    try:
        cancel_job = (
            db.query(NotificationJob)
            .filter(NotificationJob.kind == "staff.booking_cancelled")
            .filter(NotificationJob.recipient_user_id == a_id)
            .filter(NotificationJob.subject_kind == "appointment")
            .order_by(NotificationJob.id.asc())
            .first()
        )
        assert cancel_job is not None, "expected a queued cancel job for A"
        fake_email = _RecordingTransport()
        notification_service.dispatch_job(
            db,
            cancel_job,
            email_transport=fake_email,
            sms_transport=_RejectingSmsTransport(),
        )
        db.commit()
        assert len(fake_email.sent) == 1, fake_email.sent
        sent = fake_email.sent[0]
        assert "cancelled" in sent.subject.lower(), sent.subject
    finally:
        db.close()
    print("  ok   dispatcher renders staff.booking_cancelled end-to-end")

    print("\nstaff_booking_cancelled_wiring smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
