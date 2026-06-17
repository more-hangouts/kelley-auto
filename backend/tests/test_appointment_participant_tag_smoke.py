"""Smoke for Phase 10.3a participant-tagging routes.

Exercises both the admin and sales PATCH surfaces backed by the shared
``services.buyer_journey.attach_appointment_to_participant``:

  - ``PATCH /api/admin/booking/appointments/{id}/participant`` (admin)
  - ``PATCH /api/sales/appointments/{id}/participant``         (sales)

The service itself is doctrine-shared, so the smoke focuses on the
route-level concerns: auth, attendance gate, error mapping, and the
audit row landing exactly once per real state change.

Seeded graph:
  - 1 admin user (actor for the admin path).
  - 1 sales user (actor for the sales path; PIN-minted via the admin
    mint endpoint).
  - 1 contact, 1 event owned by no one, 2 event_participants attached
    to that event (a chambelan and a dama), plus 1 participant on a
    DIFFERENT event used to exercise the cross-event 400 path.
  - 1 appointment linked to the first event.

Assertions per case are explicit so a regression points at the wrong
line, not at a generic "expected 200" failure.
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
    EventParticipant,
    User,
)

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_participant_ids: list[int] = []
_created_appt_ids: list[int] = []


def _make_user(*, role: str, label: str) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-participant-tag-{label}-{suffix}"
        u = User(
            username=username,
            email=f"phase10-smoke-{role}-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 10 Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username, create_access_token(u)
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


def _capture_gate() -> bool:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        return row.attendance_gate_enabled if row else True
    finally:
        db.close()


def _set_gate(*, enabled: bool) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is not None:
            row.attendance_gate_enabled = enabled
            db.commit()
    finally:
        db.close()


def _seed() -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55510{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Phase 10 Smoke {tag}",
            email=f"phase10-smoke-c-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["phase10-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        # Two events. The appointment lives under event_a; the
        # "wrong" participant under event_b is for the 400 path.
        event_a = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Phase 10 Smoke Quince A {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        event_b = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Phase 10 Smoke Quince B {tag}",
            event_date=date(2027, 10, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        db.add_all([event_a, event_b])
        db.flush()
        _created_event_ids.extend([event_a.id, event_b.id])

        chambelan_a = EventParticipant(
            event_id=event_a.id,
            contact_id=contact.id,
            role="chambelan",
            display_name=f"Chambelan A {tag}",
        )
        dama_a = EventParticipant(
            event_id=event_a.id,
            contact_id=contact.id,
            role="dama",
            display_name=f"Dama A {tag}",
        )
        other_event_participant = EventParticipant(
            event_id=event_b.id,
            contact_id=contact.id,
            role="chambelan",
            display_name=f"Chambelan B {tag}",
        )
        db.add_all([chambelan_a, dama_a, other_event_participant])
        db.flush()
        _created_participant_ids.extend(
            [chambelan_a.id, dama_a.id, other_event_participant.id]
        )

        slot = datetime.now(timezone.utc) + timedelta(days=21)
        appt = Appointment(
            confirmation_code=f"P10T{tag}",
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
            crm_event_id=event_a.id,
        )
        db.add(appt)
        db.flush()
        _created_appt_ids.append(appt.id)
        db.commit()

        return {
            "event_a_id": event_a.id,
            "event_b_id": event_b.id,
            "chambelan_a_id": chambelan_a.id,
            "dama_a_id": dama_a.id,
            "other_event_participant_id": other_event_participant.id,
            "appointment_id": appt.id,
        }
    finally:
        db.close()


def _audit_rows(event_id: int, appointment_id: int) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(
                ActivityLog.activity_type == "appointment.participant_attached"
            )
            .filter(ActivityLog.subject_id == appointment_id)
            .order_by(ActivityLog.id.asc())
            .all()
        )
    finally:
        db.close()


def _read_participant(appointment_id: int) -> int | None:
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appointment_id)
        return appt.event_participant_id if appt else None
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
        if _created_participant_ids:
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE id = ANY(:ids)"
                ),
                {"ids": _created_participant_ids},
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
        admin_id, _, admin_token = _make_user(role="admin", label="actor")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        sales_id, sales_username, _ = _make_user(role="sales", label="floor")

        sales_headers = _login_sales(sales_id, sales_username, admin_headers)
        _set_gate(enabled=False)

        seed = _seed()
        appt_id = seed["appointment_id"]
        event_a_id = seed["event_a_id"]
        chambelan_id = seed["chambelan_a_id"]
        dama_id = seed["dama_a_id"]

        # ---- admin: attach to chambelan (first real state change) ----
        resp = client.patch(
            f"/api/admin/booking/appointments/{appt_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["appointment_id"] == appt_id, body
        assert body["event_participant_id"] == chambelan_id, body
        assert _read_participant(appt_id) == chambelan_id

        rows = _audit_rows(event_a_id, appt_id)
        assert len(rows) == 1, [r.id for r in rows]
        row = rows[0]
        assert row.actor_kind == "staff", row.actor_kind
        assert row.actor_user_id == admin_id, row.actor_user_id
        assert row.payload.get("from_event_participant_id") is None, row.payload
        assert row.payload.get("to_event_participant_id") == chambelan_id, (
            row.payload
        )

        # ---- admin: idempotent re-PATCH writes no extra row ----
        resp = client.patch(
            f"/api/admin/booking/appointments/{appt_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 200, resp.text
        assert len(_audit_rows(event_a_id, appt_id)) == 1

        # ---- sales: move to dama, audit row from sales actor ----
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/participant",
            headers=sales_headers,
            json={"event_participant_id": dama_id},
        )
        assert resp.status_code == 200, resp.text
        assert _read_participant(appt_id) == dama_id
        rows = _audit_rows(event_a_id, appt_id)
        assert len(rows) == 2, [r.id for r in rows]
        latest = rows[-1]
        assert latest.actor_user_id == sales_id, latest.actor_user_id
        assert latest.payload.get("from_event_participant_id") == chambelan_id
        assert latest.payload.get("to_event_participant_id") == dama_id

        # ---- sales: detach (NULL) ----
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/participant",
            headers=sales_headers,
            json={"event_participant_id": None},
        )
        assert resp.status_code == 200, resp.text
        assert _read_participant(appt_id) is None
        rows = _audit_rows(event_a_id, appt_id)
        assert len(rows) == 3, [r.id for r in rows]
        latest = rows[-1]
        assert latest.payload.get("from_event_participant_id") == dama_id
        assert latest.payload.get("to_event_participant_id") is None

        # ---- admin: non-existent participant → 404 ----
        resp = client.patch(
            f"/api/admin/booking/appointments/{appt_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": 99_999_999},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "participant_not_found", resp.text

        # ---- admin: participant from a different event → 400 ----
        resp = client.patch(
            f"/api/admin/booking/appointments/{appt_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": seed["other_event_participant_id"]},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "participant_event_mismatch", resp.text

        # ---- admin: non-existent appointment → 404 ----
        resp = client.patch(
            "/api/admin/booking/appointments/99999999/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "appointment_not_found", resp.text

        # ---- sales: attendance gate (enable it, then expect 403) ----
        _set_gate(enabled=True)
        resp = client.patch(
            f"/api/sales/appointments/{appt_id}/participant",
            headers=sales_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 403, resp.text
        # The gate returns a structured {code, message} body. Match on
        # code so renderer-side message tweaks don't break the smoke.
        detail = resp.json().get("detail", {})
        if isinstance(detail, dict):
            assert detail.get("code") == "attendance_gate", resp.text
        else:
            assert detail == "attendance_gate", resp.text
        _set_gate(enabled=False)

        print("appointment_participant_tag smoke ok")
    finally:
        _set_gate(enabled=gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
