"""Smoke for Phase 6 sales-portal assignment endpoints.

Covers:

  - ``GET /api/sales/staff/assignable``:
      * Lists active sales users (Sales A, Sales B).
      * Excludes admin users and inactive sales users.
      * Admin token gets 403 (sales scope).
  - ``PATCH /api/sales/appointments/{id}/assignment``:
      * Sales A reassigns an appointment to Sales B → 200.
      * activity_log gets one ``appointment.reassigned`` row anchored
        to the appointment's linked event, with payload
        ``{from_user_id: A, to_user_id: B, reason: 'sales_reassignment'}``
        and actor = caller.
      * Assigning to an admin id → 400 ``invalid_assigned_user_id``.
      * Assigning to an inactive sales id → 400.
      * Non-existent appointment id → 404.
      * Idempotent same-value patch → 200 but no new audit row.
      * Admin token → 403.

Cascade behavior is covered separately in
``test_sales_lead_reassignment_cascade_smoke.py``.

Attendance gate is disabled for the duration of the smoke (the PATCH
routes are floor-mutations). State is captured and restored on
teardown.
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
    BusinessProfile,
    Contact,
    Event,
    User,
)

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_appt_ids: list[int] = []


def _make_user(*, role: str, label: str, is_active: bool = True) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{label}-{suffix}"
        u = User(
            username=username,
            email=f"{role}-smoke-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Sales Assign Smoke {role.title()} {label}",
            is_active=is_active,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username
    finally:
        db.close()


def _admin_token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_access_token(db.get(User, user_id))
    finally:
        db.close()


def _login_sales(sales_id: int, sales_username: str, admin_headers: dict) -> dict:
    mint = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin", headers=admin_headers
    )
    assert mint.status_code == 200, mint.text
    login = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": mint.json()["pin"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _capture_gate() -> dict:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        return {
            "attendance_gate_enabled": (
                row.attendance_gate_enabled if row else True
            ),
        }
    finally:
        db.close()


def _set_gate(*, enabled: bool) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is None:
            raise AssertionError("business_profile row must exist")
        row.attendance_gate_enabled = enabled
        db.commit()
    finally:
        db.close()


def _restore_gate(snapshot: dict) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is None:
            return
        row.attendance_gate_enabled = snapshot["attendance_gate_enabled"]
        db.commit()
    finally:
        db.close()


def _seed_appointment(*, owner_user_id: int) -> dict:
    """Contact + event (owner_user_id) + appointment (assigned to owner)."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55501{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Contact {tag}",
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
            event_name=f"Sales Assign Smoke Quince {tag}",
            event_date=date(2027, 6, 15),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="sales-assign-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        slot = datetime.now(timezone.utc) + timedelta(days=4)
        appt = Appointment(
            confirmation_code=f"SA{tag}",
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

        return {"contact_id": contact.id, "event_id": event.id, "appt_id": appt.id}
    finally:
        db.close()


def _count_reassign_rows(*, event_id: int, subject_id: int | None = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(ActivityLog).filter(
            ActivityLog.event_id == event_id,
            ActivityLog.activity_type == "appointment.reassigned",
        )
        if subject_id is not None:
            q = q.filter(ActivityLog.subject_id == subject_id)
        return q.count()
    finally:
        db.close()


def _latest_reassign_row(*, event_id: int, subject_id: int) -> ActivityLog | None:
    db = SessionLocal()
    try:
        return (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == "appointment.reassigned")
            .filter(ActivityLog.subject_id == subject_id)
            .order_by(ActivityLog.id.desc())
            .first()
        )
    finally:
        db.close()


def _read_appointment(appt_id: int) -> Appointment | None:
    db = SessionLocal()
    try:
        return db.get(Appointment, appt_id)
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
    gate_snapshot = _capture_gate()

    try:
        admin_id, _ = _make_user(role="admin", label="actor")
        admin_headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
        sales_a_id, sales_a_username = _make_user(role="sales", label="A")
        sales_b_id, _ = _make_user(role="sales", label="B")
        inactive_id, _ = _make_user(
            role="sales", label="INACTIVE", is_active=False
        )

        sales_a_headers = _login_sales(
            sales_a_id, sales_a_username, admin_headers
        )

        # ---- GET /api/sales/staff/assignable ----
        resp = client.get(
            "/api/sales/staff/assignable", headers=sales_a_headers
        )
        assert resp.status_code == 200, resp.text
        ids = {row["id"] for row in resp.json()}
        assert sales_a_id in ids, ids
        assert sales_b_id in ids, ids
        assert admin_id not in ids, ids
        assert inactive_id not in ids, ids

        # Phase 11: the picker is now admin-or-sales scope so the admin
        # event-owner dialog can reuse it (previously sales-only). The
        # response shape and the underlying sales_staff filter are
        # unchanged — admin sees the same active-sales-user list and is
        # never themselves a row.
        resp = client.get(
            "/api/sales/staff/assignable", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        admin_view_ids = {row["id"] for row in resp.json()}
        assert sales_a_id in admin_view_ids, admin_view_ids
        assert sales_b_id in admin_view_ids, admin_view_ids
        assert admin_id not in admin_view_ids, admin_view_ids
        assert inactive_id not in admin_view_ids, admin_view_ids

        # ---- PATCH appointment assignment ----
        _set_gate(enabled=False)
        seed = _seed_appointment(owner_user_id=sales_a_id)
        appt_id = seed["appt_id"]
        event_id = seed["event_id"]

        # Sales A reassigns to Sales B → 200; one audit row.
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": sales_b_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appointment_id"] == appt_id, body
        assert body["assigned_user_id"] == sales_b_id, body
        assert _read_appointment(appt_id).assigned_user_id == sales_b_id

        rows_before = _count_reassign_rows(event_id=event_id, subject_id=appt_id)
        assert rows_before == 1, rows_before

        row = _latest_reassign_row(event_id=event_id, subject_id=appt_id)
        assert row is not None
        assert row.actor_user_id == sales_a_id
        assert row.actor_kind == "staff"
        assert row.payload.get("from_user_id") == sales_a_id, row.payload
        assert row.payload.get("to_user_id") == sales_b_id, row.payload
        assert row.payload.get("reason") == "sales_reassignment", row.payload

        # Idempotent: same value → 200 but no new audit row.
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": sales_b_id},
        )
        assert resp.status_code == 200, resp.text
        assert _count_reassign_rows(event_id=event_id, subject_id=appt_id) == 1

        # Assigning to an admin id → 400.
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": admin_id},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

        # Inactive sales user → 400.
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": inactive_id},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

        # Non-existent appointment → 404.
        resp = client.patch(
            "/api/sales/appointments/9999999/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": sales_b_id},
        )
        assert resp.status_code == 404, resp.text

        # Admin token rejected (sales scope required).
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=admin_headers,
            json={"assigned_user_id": sales_a_id},
        )
        assert resp.status_code == 403, resp.text

        # Unassign with explicit None.
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/assignment",
            headers=sales_a_headers,
            json={"assigned_user_id": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["assigned_user_id"] is None
        assert _read_appointment(appt_id).assigned_user_id is None

        # Audit row count went from 1 → 2 (B → None is a real change).
        assert _count_reassign_rows(event_id=event_id, subject_id=appt_id) == 2

        print("sales_assignment smoke ok")
    finally:
        _restore_gate(gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
