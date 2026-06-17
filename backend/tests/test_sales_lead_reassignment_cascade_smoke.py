"""Smoke for the Phase 6 lead reassignment cascade.

Seeds an event owned by stylist A with three appointments — one past,
two future — and calls ``PATCH /api/sales/leads/{event_id}/assignment``
to move the lead to stylist B.

Assertions:
  - ``events.owner_user_id == B`` after the call.
  - Both future-dated appointments now have ``assigned_user_id == B``.
  - The past-dated appointment is **unchanged** (still A) so
    commission / historical attribution stays accurate.
  - Response ``cascaded_appointment_ids`` contains exactly the two
    future appointment ids (sorted-set comparison).
  - ``activity_log`` for the event has exactly one ``event.reassigned``
    row (parent) plus exactly two ``appointment.reassigned`` rows with
    payload ``via: 'lead_cascade'``. The past-dated appointment gets
    no row.
  - All four rows carry the caller as ``actor_user_id`` and
    ``actor_kind == 'staff'``.

Attendance gate is disabled for the duration of the smoke (PATCH is a
floor mutation). State is captured and restored on teardown.
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


def _make_user(*, role: str, label: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{label}-{suffix}"
        u = User(
            username=username,
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


def _seed_event_with_appointments(*, owner_user_id: int) -> dict:
    """One event + three appointments (1 past, 2 future), all assigned to owner."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55502{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Cascade {tag}",
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
            event_name=f"Sales Assign Smoke Cascade Quince {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="sales-assign-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        now = datetime.now(timezone.utc)
        slots = {
            "past": now - timedelta(days=2),
            "future_1": now + timedelta(days=1),
            "future_2": now + timedelta(days=5),
        }
        appts = {}
        for idx, (key, slot) in enumerate(slots.items()):
            appt = Appointment(
                confirmation_code=f"CSC{tag}{idx:02d}",
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
            appts[key] = appt.id
        db.commit()

        return {
            "contact_id": contact.id,
            "event_id": event.id,
            "past_appt_id": appts["past"],
            "future_appt_1_id": appts["future_1"],
            "future_appt_2_id": appts["future_2"],
        }
    finally:
        db.close()


def _read_state(seed: dict) -> dict:
    db = SessionLocal()
    try:
        event = db.get(Event, seed["event_id"])
        past = db.get(Appointment, seed["past_appt_id"])
        f1 = db.get(Appointment, seed["future_appt_1_id"])
        f2 = db.get(Appointment, seed["future_appt_2_id"])
        return {
            "event_owner_user_id": event.owner_user_id,
            "past_assigned_user_id": past.assigned_user_id,
            "f1_assigned_user_id": f1.assigned_user_id,
            "f2_assigned_user_id": f2.assigned_user_id,
        }
    finally:
        db.close()


def _activity_rows(event_id: int) -> dict[str, list[ActivityLog]]:
    """Return activity_log rows for the event, grouped by type."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(
                ActivityLog.activity_type.in_(
                    ("event.reassigned", "appointment.reassigned")
                )
            )
            .order_by(ActivityLog.id.asc())
            .all()
        )
        out: dict[str, list[ActivityLog]] = {
            "event.reassigned": [],
            "appointment.reassigned": [],
        }
        for r in rows:
            out[r.activity_type].append(r)
        return out
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

        sales_a_headers = _login_sales(
            sales_a_id, sales_a_username, admin_headers
        )
        _set_gate(enabled=False)

        seed = _seed_event_with_appointments(owner_user_id=sales_a_id)
        event_id = seed["event_id"]

        # ---- PATCH the lead from A to B ----
        resp = client.patch(
            f"/api/sales/leads/{event_id}/assignment",
            headers=sales_a_headers,
            json={"owner_user_id": sales_b_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == event_id, body
        assert body["owner_user_id"] == sales_b_id, body
        assert set(body["cascaded_appointment_ids"]) == {
            seed["future_appt_1_id"],
            seed["future_appt_2_id"],
        }, body

        # ---- DB state after cascade ----
        state = _read_state(seed)
        assert state["event_owner_user_id"] == sales_b_id, state
        assert state["past_assigned_user_id"] == sales_a_id, state  # frozen
        assert state["f1_assigned_user_id"] == sales_b_id, state
        assert state["f2_assigned_user_id"] == sales_b_id, state

        # ---- activity_log: 1 parent + 2 cascade rows, no row for past ----
        rows = _activity_rows(event_id)
        assert len(rows["event.reassigned"]) == 1, rows
        assert len(rows["appointment.reassigned"]) == 2, rows

        parent = rows["event.reassigned"][0]
        assert parent.actor_user_id == sales_a_id
        assert parent.actor_kind == "staff"
        assert parent.subject_kind == "event"
        assert parent.subject_id == event_id
        assert parent.payload.get("from_user_id") == sales_a_id, parent.payload
        assert parent.payload.get("to_user_id") == sales_b_id, parent.payload
        assert parent.payload.get("reason") == "sales_reassignment", parent.payload

        cascade_subjects = sorted(
            r.subject_id for r in rows["appointment.reassigned"]
        )
        assert cascade_subjects == sorted(
            [seed["future_appt_1_id"], seed["future_appt_2_id"]]
        ), cascade_subjects

        for r in rows["appointment.reassigned"]:
            assert r.actor_user_id == sales_a_id, r.actor_user_id
            assert r.actor_kind == "staff", r.actor_kind
            assert r.subject_kind == "appointment", r.subject_kind
            assert r.payload.get("from_user_id") == sales_a_id, r.payload
            assert r.payload.get("to_user_id") == sales_b_id, r.payload
            assert r.payload.get("reason") == "sales_reassignment", r.payload
            assert r.payload.get("via") == "lead_cascade", r.payload
            # The past appointment must never appear as a subject.
            assert r.subject_id != seed["past_appt_id"], r.subject_id

        # Idempotent re-PATCH (B → B) produces no extra rows.
        resp = client.patch(
            f"/api/sales/leads/{event_id}/assignment",
            headers=sales_a_headers,
            json={"owner_user_id": sales_b_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["cascaded_appointment_ids"] == [], resp.json()
        rows_after = _activity_rows(event_id)
        assert len(rows_after["event.reassigned"]) == 1
        assert len(rows_after["appointment.reassigned"]) == 2

        # Admin token rejected.
        resp = client.patch(
            f"/api/sales/leads/{event_id}/assignment",
            headers=admin_headers,
            json={"owner_user_id": sales_a_id},
        )
        assert resp.status_code == 403, resp.text

        # Non-existent event → 404.
        resp = client.patch(
            "/api/sales/leads/9999999/assignment",
            headers=sales_a_headers,
            json={"owner_user_id": sales_a_id},
        )
        assert resp.status_code == 404, resp.text

        # Invalid assignee (admin id) → 400.
        resp = client.patch(
            f"/api/sales/leads/{event_id}/assignment",
            headers=sales_a_headers,
            json={"owner_user_id": admin_id},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

        print("sales_lead_reassignment_cascade smoke ok")
    finally:
        _restore_gate(gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
