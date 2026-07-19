"""Smoke for ``GET /api/sales/leads/{event_id}/cascade-preview`` (Phase 9.2a).

The preview endpoint is the read-only twin of the lead reassignment PATCH.
It exists so the sales-portal assignment dialog can show *which* future
appointments would cascade before the user confirms a lead-level move.

Seeds one event owned by stylist A with four appointments:

  - past_appt:       slot 2 days ago,      assigned_user_id = A
  - future_appt_1:   slot in 1 day,        assigned_user_id = A
  - future_appt_2:   slot in 5 days,       assigned_user_id = B
  - future_appt_3:   slot in 10 days,      assigned_user_id = NULL

Assertions:

  - 401 unauthenticated.
  - 403 with an admin token (``require_sales_scope`` rejects).
  - 200 with a sales token. Response carries event_owner_user_id=A and
    event_owner_full_name resolved live from ``users``.
  - future_appointments contains exactly future_appt_1, _2, _3 — the
    past appointment is excluded by the ``slot_start_at >= NOW()`` cutoff.
  - future_appointments is ordered by slot_start_at ascending.
  - assigned_user_full_name resolves correctly for each row: A's name,
    B's name, and ``None`` for the unassigned future appointment.
  - 404 for an unknown event_id.

This is a read endpoint, so no attendance gate is involved. Audit and
notification side effects belong to the PATCH and are exercised by
``test_sales_lead_reassignment_cascade_smoke.py``; this smoke must NOT
mutate ``events`` or ``appointments``.
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


def _make_user(*, role: str, label: str) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{label}-{suffix}"
        full_name = f"Sales Assign Smoke {role.title()} {label}"
        u = User(
            username=username,
            email=f"{role}-smoke-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=full_name,
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username, full_name
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


def _seed(*, owner_user_id: int, alt_user_id: int) -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55503{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Sales Assign Smoke Preview {tag}",
            email=f"sa-smoke-preview-{tag.lower()}@example.com",
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
            event_name=f"Sales Assign Smoke Preview Quince {tag}",
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
        rows = [
            ("past", now - timedelta(days=2), owner_user_id),
            ("future_1", now + timedelta(days=1), owner_user_id),
            ("future_2", now + timedelta(days=5), alt_user_id),
            ("future_3", now + timedelta(days=10), None),
        ]
        appts: dict[str, int] = {}
        for idx, (key, slot, assignee) in enumerate(rows):
            appt = Appointment(
                confirmation_code=f"PRV{tag}{idx:02d}",
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
                assigned_user_id=assignee,
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
            "future_appt_3_id": appts["future_3"],
        }
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _created_appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appt_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
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

    admin_id, _, _ = _make_user(role="admin", label="actor")
    admin_headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    sales_a_id, sales_a_username, sales_a_name = _make_user(
        role="sales", label="A"
    )
    sales_b_id, _, sales_b_name = _make_user(role="sales", label="B")

    sales_a_headers = _login_sales(sales_a_id, sales_a_username, admin_headers)

    seed = _seed(owner_user_id=sales_a_id, alt_user_id=sales_b_id)
    event_id = seed["event_id"]

    # ---- 401 unauthenticated ----
    resp = client.get(f"/api/sales/leads/{event_id}/cascade-preview")
    assert resp.status_code == 401, resp.text

    # ---- 403 admin token ----
    resp = client.get(
        f"/api/sales/leads/{event_id}/cascade-preview",
        headers=admin_headers,
    )
    assert resp.status_code == 403, resp.text

    # ---- 200 sales token, correct shape ----
    resp = client.get(
        f"/api/sales/leads/{event_id}/cascade-preview",
        headers=sales_a_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_id"] == event_id, body
    assert body["event_owner_user_id"] == sales_a_id, body
    assert body["event_owner_full_name"] == sales_a_name, body

    future = body["future_appointments"]
    # Past appointment must not be in the preview.
    future_ids = [row["id"] for row in future]
    assert seed["past_appt_id"] not in future_ids, future_ids
    # All three future appointments must be present.
    assert set(future_ids) == {
        seed["future_appt_1_id"],
        seed["future_appt_2_id"],
        seed["future_appt_3_id"],
    }, future_ids
    # Ordered by slot_start_at ascending.
    assert future_ids == [
        seed["future_appt_1_id"],
        seed["future_appt_2_id"],
        seed["future_appt_3_id"],
    ], future_ids

    by_id = {row["id"]: row for row in future}
    row_1 = by_id[seed["future_appt_1_id"]]
    assert row_1["assigned_user_id"] == sales_a_id, row_1
    assert row_1["assigned_user_full_name"] == sales_a_name, row_1

    row_2 = by_id[seed["future_appt_2_id"]]
    assert row_2["assigned_user_id"] == sales_b_id, row_2
    assert row_2["assigned_user_full_name"] == sales_b_name, row_2

    row_3 = by_id[seed["future_appt_3_id"]]
    assert row_3["assigned_user_id"] is None, row_3
    assert row_3["assigned_user_full_name"] is None, row_3

    # ---- 404 unknown event ----
    resp = client.get(
        "/api/sales/leads/9999999/cascade-preview", headers=sales_a_headers
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "event_not_found", resp.text

    print("sales_lead_cascade_preview smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
