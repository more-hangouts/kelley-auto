"""Smoke for Phase 11 admin lead-owner reassignment.

Mirrors the sales-side cascade smoke shape but exercises the new
admin route:

  - ``GET  /api/admin/events/{event_id}/cascade-preview``
  - ``PATCH /api/admin/events/{event_id}/owner``

The admin route delegates to the same shared service as sales
(``services/sales_assignment.py``), so cascade scope and audit shape
should match the sales smoke byte-for-byte except for one field: the
audit ``reason`` is ``"admin_owner_change"`` for admin-initiated
moves vs ``"sales_reassignment"`` for sales.

Layout:
  - 1 event owned by sales user A.
  - 3 appointments: 1 past (assigned A), 2 future (1 assigned A, 1
    unassigned).
  - 1 unrelated event owned by A with 1 future appt — must NOT be
    touched by the cascade.

Assertions:
  - Cascade preview returns exactly the 2 future appts.
  - PATCH moves event.owner_user_id to B.
  - Both future appts now assigned to B; past appt stays A; the
    unrelated event's appt is unchanged.
  - Response cascaded_appointment_ids contains the 2 future ids.
  - ``activity_log`` for the event has 1 ``event.reassigned`` row
    (reason=``admin_owner_change``) and 2 ``appointment.reassigned``
    rows (reason=``admin_owner_change``, via=``lead_cascade``).
  - Idempotent re-PATCH (B → B) writes no new audit rows and returns
    an empty cascaded_appointment_ids.
  - Sales-token PATCH against the admin route is rejected (403).
  - Non-existent event PATCH → 404.
  - Invalid assignee (admin id) → 400.

No attendance-gate manipulation needed — admin is not floor-gated.
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


def _make_user(*, role: str, label: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"admin-smoke-owner-reassign-{label}-{suffix}"
        u = User(
            username=username,
            email=f"admin-owner-reassign-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Admin Owner Reassign Smoke {role.title()} {label}",
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


def _token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_access_token(db.get(User, user_id))
    finally:
        db.close()


def _login_sales(sales_id: int, sales_username: str, admin_headers: dict) -> dict:
    """Mint a PIN and exchange for a sales access token.

    Used for the negative case where we verify a sales token cannot hit
    the admin route. We deliberately use the sales auth flow (not just
    a JWT minted directly) so the test exercises the same scope shape
    a real sales user would carry.
    """
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


def _seed_event_with_appointments(
    *, owner_user_id: int, tag_suffix: str
) -> dict:
    """One event + three appointments (1 past, 2 future)."""
    db = SessionLocal()
    try:
        digits = f"55503{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Admin Owner Reassign Smoke {tag_suffix}",
            email=f"admin-owner-reassign-c-{tag_suffix.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["admin-owner-reassign-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Admin Owner Reassign Smoke Quince {tag_suffix}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="admin-owner-reassign-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        now = datetime.now(timezone.utc)
        # Past appt is assigned to owner; future_1 is assigned to owner;
        # future_2 is intentionally unassigned to prove the cascade also
        # moves rows that had no prior assignee.
        slots = {
            "past": (now - timedelta(days=2), owner_user_id),
            "future_1": (now + timedelta(days=1), owner_user_id),
            "future_2": (now + timedelta(days=5), None),
        }
        appts: dict[str, int] = {}
        for idx, (key, (slot, assignee)) in enumerate(slots.items()):
            appt = Appointment(
                confirmation_code=f"AOR{tag_suffix}{idx:02d}",
                slot_start_at=slot,
                slot_end_at=slot + timedelta(minutes=45),
                slot_duration_minutes=45,
                timezone="America/Chicago",
                celebrant_first_name=f"Cel {tag_suffix}",
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
        }
    finally:
        db.close()


def _seed_unrelated_event(*, owner_user_id: int, tag_suffix: str) -> dict:
    """A second event under the same owner with one future appt. The
    cascade for the first event must NOT touch this event's appt."""
    db = SessionLocal()
    try:
        digits = f"55504{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Admin Owner Reassign Smoke Other {tag_suffix}",
            email=f"admin-owner-reassign-other-{tag_suffix.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["admin-owner-reassign-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Admin Owner Reassign Smoke Other Quince {tag_suffix}",
            event_date=date(2027, 10, 15),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="admin-owner-reassign-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        slot = datetime.now(timezone.utc) + timedelta(days=3)
        appt = Appointment(
            confirmation_code=f"AOX{tag_suffix}00",
            slot_start_at=slot,
            slot_end_at=slot + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name=f"Cel {tag_suffix} Other",
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
        db.commit()
        return {"event_id": event.id, "appt_id": appt.id}
    finally:
        db.close()


def _read_state(seed: dict, unrelated: dict) -> dict:
    db = SessionLocal()
    try:
        event = db.get(Event, seed["event_id"])
        past = db.get(Appointment, seed["past_appt_id"])
        f1 = db.get(Appointment, seed["future_appt_1_id"])
        f2 = db.get(Appointment, seed["future_appt_2_id"])
        other = db.get(Appointment, unrelated["appt_id"])
        return {
            "event_owner_user_id": event.owner_user_id,
            "past_assigned_user_id": past.assigned_user_id,
            "f1_assigned_user_id": f1.assigned_user_id,
            "f2_assigned_user_id": f2.assigned_user_id,
            "other_assigned_user_id": other.assigned_user_id,
        }
    finally:
        db.close()


def _activity_rows(event_id: int) -> dict[str, list[ActivityLog]]:
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

    admin_id, _ = _make_user(role="admin", label="actor")
    admin_headers = {"Authorization": f"Bearer {_token(admin_id)}"}

    sales_a_id, sales_a_username = _make_user(role="sales", label="A")
    sales_b_id, _ = _make_user(role="sales", label="B")

    tag = uuid.uuid4().hex[:6].upper()
    seed = _seed_event_with_appointments(owner_user_id=sales_a_id, tag_suffix=tag)
    unrelated = _seed_unrelated_event(owner_user_id=sales_a_id, tag_suffix=tag)
    event_id = seed["event_id"]

    # ---- Cascade preview returns exactly the two future appts ----
    resp = client.get(
        f"/api/admin/events/{event_id}/cascade-preview", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    assert preview["event_id"] == event_id
    assert preview["event_owner_user_id"] == sales_a_id
    preview_ids = {row["id"] for row in preview["future_appointments"]}
    assert preview_ids == {
        seed["future_appt_1_id"],
        seed["future_appt_2_id"],
    }, preview

    # ---- PATCH the owner from A to B ----
    resp = client.patch(
        f"/api/admin/events/{event_id}/owner",
        headers=admin_headers,
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
    state = _read_state(seed, unrelated)
    assert state["event_owner_user_id"] == sales_b_id, state
    assert state["past_assigned_user_id"] == sales_a_id, state  # frozen
    assert state["f1_assigned_user_id"] == sales_b_id, state
    assert state["f2_assigned_user_id"] == sales_b_id, state
    # Unrelated event's appt must not be touched.
    assert state["other_assigned_user_id"] == sales_a_id, state

    # ---- activity_log: 1 parent + 2 cascade rows, reason=admin_owner_change ----
    rows = _activity_rows(event_id)
    assert len(rows["event.reassigned"]) == 1, rows
    assert len(rows["appointment.reassigned"]) == 2, rows

    parent = rows["event.reassigned"][0]
    assert parent.actor_user_id == admin_id
    assert parent.actor_kind == "staff"
    assert parent.subject_kind == "event"
    assert parent.subject_id == event_id
    assert parent.payload.get("from_user_id") == sales_a_id, parent.payload
    assert parent.payload.get("to_user_id") == sales_b_id, parent.payload
    assert (
        parent.payload.get("reason") == "admin_owner_change"
    ), parent.payload

    cascade_subjects = sorted(
        r.subject_id for r in rows["appointment.reassigned"]
    )
    assert cascade_subjects == sorted(
        [seed["future_appt_1_id"], seed["future_appt_2_id"]]
    ), cascade_subjects

    for r in rows["appointment.reassigned"]:
        assert r.actor_user_id == admin_id, r.actor_user_id
        assert r.actor_kind == "staff", r.actor_kind
        assert r.subject_kind == "appointment", r.subject_kind
        assert r.payload.get("to_user_id") == sales_b_id, r.payload
        assert (
            r.payload.get("reason") == "admin_owner_change"
        ), r.payload
        assert r.payload.get("via") == "lead_cascade", r.payload
        # The past appointment must never appear as a subject.
        assert r.subject_id != seed["past_appt_id"], r.subject_id

    # ---- Idempotent re-PATCH (B → B) produces no extra rows ----
    resp = client.patch(
        f"/api/admin/events/{event_id}/owner",
        headers=admin_headers,
        json={"owner_user_id": sales_b_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cascaded_appointment_ids"] == [], resp.json()
    rows_after = _activity_rows(event_id)
    assert len(rows_after["event.reassigned"]) == 1
    assert len(rows_after["appointment.reassigned"]) == 2

    # ---- Sales token rejected on admin route (admin-only scope) ----
    sales_headers = _login_sales(sales_a_id, sales_a_username, admin_headers)
    resp = client.patch(
        f"/api/admin/events/{event_id}/owner",
        headers=sales_headers,
        json={"owner_user_id": sales_a_id},
    )
    assert resp.status_code == 403, resp.text
    resp = client.get(
        f"/api/admin/events/{event_id}/cascade-preview",
        headers=sales_headers,
    )
    assert resp.status_code == 403, resp.text

    # ---- Sales picker (relaxed in Phase 11) accepts admin token ----
    resp = client.get("/api/sales/staff/assignable", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    picker_ids = {row["id"] for row in resp.json()}
    assert sales_a_id in picker_ids, resp.json()
    assert sales_b_id in picker_ids, resp.json()
    # Admin users must not appear in the picker.
    assert admin_id not in picker_ids, resp.json()

    # ---- Non-existent event → 404 ----
    resp = client.patch(
        "/api/admin/events/9999999/owner",
        headers=admin_headers,
        json={"owner_user_id": sales_a_id},
    )
    assert resp.status_code == 404, resp.text

    # ---- Invalid assignee (admin id) → 400 ----
    resp = client.patch(
        f"/api/admin/events/{event_id}/owner",
        headers=admin_headers,
        json={"owner_user_id": admin_id},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "invalid_assigned_user_id", resp.text

    # ---- Unassign (None) succeeds and clears the owner ----
    resp = client.patch(
        f"/api/admin/events/{event_id}/owner",
        headers=admin_headers,
        json={"owner_user_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_user_id"] is None, resp.json()

    print("admin_lead_reassignment smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
