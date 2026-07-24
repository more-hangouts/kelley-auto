"""Smoke for POST /api/sales/walk-ins.

Covers:

  - Punched-in sales user, no `assigned_user_id` → walk-in is created
    and assigned to the caller (default = self).
  - Punched-in sales user, explicit `assigned_user_id` for an active
    sales coworker → walk-in is created and assigned to the coworker.
  - `assigned_user_id` for an admin user → 400 ``invalid_assigned_user_id``
    (assignment is restricted to active sales users).
  - `assigned_user_id` for a non-existent id → 400.
  - Admin token → 403 ``scope_forbidden`` (sales scope required).
  - Sales user with attendance gate enabled but not punched in → 403
    ``attendance_gate``.

Attendance gate state is toggled for the duration of the smoke and
restored on teardown. Names use the existing ``Walk-In Assign Smoke``
naming family so the cleanup SQL safety net sweeps any crash leakage.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date
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
from database.models import BusinessProfile, User  # noqa: E402

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
            full_name=f"Walk-In Assign Smoke {role.title()} {label}",
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
    """Admin mints a PIN, then sales user exchanges it for a sales token."""
    mint = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin", headers=admin_headers
    )
    assert mint.status_code == 200, mint.text
    pin = mint.json()["pin"]
    login = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": pin},
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
            raise AssertionError(
                "test prerequisite: business_profile row must exist"
            )
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


def _walk_in_payload(*, tag: str, assigned_user_id: int | None = None) -> dict:
    """Minimal valid walk-in payload, with a fresh phone per call."""
    suffix = uuid.uuid4().int % 10_000
    body = {
        "contact": {
            "first_name": "Lorena",
            "last_name": f"Smoke {tag}",
            "display_name": f"Walk-In Assign Smoke Contact {tag}",
            "email": f"walkin-assign-{tag.lower()}-{suffix}@example.com",
            "phone": f"(210) 555-{suffix:04d}",
        },
        "event": {
            "celebrant_first_name": f"Sofía {tag}",
            "celebrant_last_name": "Hernández",
            "event_name": f"Walk-In Assign Smoke Quince {tag}",
            "event_date": "2027-08-15",
        },
        "enrichment": {"party_size_bucket": "pair"},
    }
    if assigned_user_id is not None:
        body["assigned_user_id"] = assigned_user_id
    return body


def _track_created(resp_json: dict) -> None:
    _created_appt_ids.append(resp_json["appointment_id"])
    _created_event_ids.append(resp_json["event_id"])
    _created_contact_ids.append(resp_json["contact_id"])


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

        # ---- Disable the attendance gate so we can exercise the 201 paths ----
        _set_gate(enabled=False)

        # Default assignment = self
        resp = client.post(
            "/api/sales/walk-ins",
            headers=sales_a_headers,
            json=_walk_in_payload(tag=f"DEFAULT-{uuid.uuid4().hex[:4].upper()}"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        _track_created(body)
        assert body["assigned_user_id"] == sales_a_id, body
        assert body["route"] == f"/appointments/{body['appointment_id']}", body

        # Explicit assignment to a sales coworker
        resp = client.post(
            "/api/sales/walk-ins",
            headers=sales_a_headers,
            json=_walk_in_payload(
                tag=f"COWORKER-{uuid.uuid4().hex[:4].upper()}",
                assigned_user_id=sales_b_id,
            ),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        _track_created(body)
        assert body["assigned_user_id"] == sales_b_id, body

        # Assignment to an admin user is rejected.
        resp = client.post(
            "/api/sales/walk-ins",
            headers=sales_a_headers,
            json=_walk_in_payload(
                tag=f"ADMIN-{uuid.uuid4().hex[:4].upper()}",
                assigned_user_id=admin_id,
            ),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

        # Assignment to a non-existent id is rejected.
        resp = client.post(
            "/api/sales/walk-ins",
            headers=sales_a_headers,
            json=_walk_in_payload(
                tag=f"NOID-{uuid.uuid4().hex[:4].upper()}",
                assigned_user_id=9_999_999,
            ),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

        # Admin token gets 403 (scope check) regardless of gate state.
        resp = client.post(
            "/api/sales/walk-ins",
            headers=admin_headers,
            json=_walk_in_payload(tag=f"ADM-{uuid.uuid4().hex[:4].upper()}"),
        )
        assert resp.status_code == 403, resp.text

        # ---- Re-enable the gate; sales_a is not punched in → 403 ----
        _set_gate(enabled=True)
        resp = client.post(
            "/api/sales/walk-ins",
            headers=sales_a_headers,
            json=_walk_in_payload(tag=f"GATE-{uuid.uuid4().hex[:4].upper()}"),
        )
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict) and detail.get("code") == "attendance_gate", detail

        print("sales_walk_in smoke ok")
    finally:
        _restore_gate(gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
