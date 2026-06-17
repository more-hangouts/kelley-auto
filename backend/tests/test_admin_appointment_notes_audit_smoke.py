"""Smoke for D2 of Phase 9.4: admin PATCH emits APPOINTMENT_NOTES_EDITED.

Closes the audit asymmetry the Phase A capability-map review flagged:
the sales-side PATCH /api/sales/appointments/{id}/notes already writes
an ``appointment.notes_edited`` activity row, but the admin-side PATCH
/api/admin/booking/appointments/{id} (same notes column, different
gate) did not. Both surfaces now share ``services.appointment_audit``
so the payload shape stays one definition.

Seeds an event-linked appointment and exercises:

  - First PATCH with a notes value writes exactly one
    ``appointment.notes_edited`` row with actor_kind='staff',
    actor_user_id=admin, subject_kind='appointment', subject_id=appt,
    and the length-delta payload (prior_length=0, new_length=N).
  - Idempotent re-PATCH with the same value writes no extra row.
  - PATCH with a different value writes a second row with
    prior_length matching the first round's new_length.
  - PATCH that does not include the ``internal_notes`` field writes no
    row even when status or other fields change.
  - PATCH on an appointment with no ``crm_event_id`` writes no row
    (there's no event timeline to anchor to; mirrors the sales path).
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    Appointment,
    Contact,
    Event,
    User,
)

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_appt_ids: list[int] = []


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"admin-smoke-notes-audit-{suffix}"
        u = User(
            username=username,
            email=f"admin-notes-audit-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Admin Notes Audit Smoke {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, create_access_token(u)
    finally:
        db.close()


def _seed_appt_with_event() -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55504{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Admin Notes Audit Smoke {tag}",
            email=f"admin-notes-audit-c-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["admin-notes-audit-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Admin Notes Audit Smoke Quince {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="admin-notes-audit-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        slot = datetime.now(timezone.utc) + timedelta(days=2)
        appt = Appointment(
            confirmation_code=f"NTE{tag}",
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
            contact_id=contact.id,
            crm_event_id=event.id,
        )
        db.add(appt)
        db.flush()
        _created_appt_ids.append(appt.id)
        db.commit()
        return {
            "contact_id": contact.id,
            "event_id": event.id,
            "appointment_id": appt.id,
        }
    finally:
        db.close()


def _seed_appt_no_event() -> int:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55505{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Admin Notes Audit Smoke noevt {tag}",
            email=f"admin-notes-audit-ne-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["admin-notes-audit-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        slot = datetime.now(timezone.utc) + timedelta(days=3)
        appt = Appointment(
            confirmation_code=f"NTN{tag}",
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
            contact_id=contact.id,
            crm_event_id=None,
        )
        db.add(appt)
        db.flush()
        _created_appt_ids.append(appt.id)
        db.commit()
        return appt.id
    finally:
        db.close()


def _notes_rows_for_event(event_id: int) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == "appointment.notes_edited")
            .order_by(ActivityLog.id.asc())
            .all()
        )
    finally:
        db.close()


def _notes_rows_for_appt(appointment_id: int) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return (
            db.query(ActivityLog)
            .filter(ActivityLog.subject_kind == "appointment")
            .filter(ActivityLog.subject_id == appointment_id)
            .filter(ActivityLog.activity_type == "appointment.notes_edited")
            .order_by(ActivityLog.id.asc())
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
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
        if _created_appt_ids:
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
    flush_for_testing()

    admin_id, admin_token = _make_admin()
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    seed = _seed_appt_with_event()
    event_id = seed["event_id"]
    appt_id = seed["appointment_id"]

    # ---- First PATCH writes exactly one notes-edited row ----
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=admin_headers,
        json={"internal_notes": "hello world"},
    )
    assert resp.status_code == 200, resp.text
    rows = _notes_rows_for_event(event_id)
    assert len(rows) == 1, [r.id for r in rows]
    row = rows[0]
    assert row.actor_kind == "staff", row.actor_kind
    assert row.actor_user_id == admin_id, row.actor_user_id
    assert row.subject_kind == "appointment", row.subject_kind
    assert row.subject_id == appt_id, row.subject_id
    assert row.payload.get("appointment_id") == appt_id, row.payload
    assert row.payload.get("prior_length") == 0, row.payload
    assert row.payload.get("new_length") == 11, row.payload

    # ---- Idempotent re-PATCH writes no extra row ----
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=admin_headers,
        json={"internal_notes": "hello world"},
    )
    assert resp.status_code == 200, resp.text
    rows = _notes_rows_for_event(event_id)
    assert len(rows) == 1, [r.id for r in rows]

    # ---- PATCH with a different value writes a second row whose
    # prior_length matches the first round's new_length.
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=admin_headers,
        json={"internal_notes": "hi"},
    )
    assert resp.status_code == 200, resp.text
    rows = _notes_rows_for_event(event_id)
    assert len(rows) == 2, [r.id for r in rows]
    second = rows[1]
    assert second.payload.get("prior_length") == 11, second.payload
    assert second.payload.get("new_length") == 2, second.payload

    # ---- PATCH that does not include internal_notes writes no row ----
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=admin_headers,
        json={"status": "confirmed"},
    )
    assert resp.status_code == 200, resp.text
    rows = _notes_rows_for_event(event_id)
    assert len(rows) == 2, [r.id for r in rows]

    # ---- Appointment without crm_event_id: no audit row written.
    # event_id-less appointments have no timeline to anchor to, matching
    # the sales-path behavior.
    noevent_appt_id = _seed_appt_no_event()
    resp = client.patch(
        f"/api/admin/booking/appointments/{noevent_appt_id}",
        headers=admin_headers,
        json={"internal_notes": "no event row should land"},
    )
    assert resp.status_code == 200, resp.text
    rows = _notes_rows_for_appt(noevent_appt_id)
    assert rows == [], [r.id for r in rows]

    print("admin_appointment_notes_audit smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
