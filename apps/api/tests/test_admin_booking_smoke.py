"""Smoke tests for the authenticated admin booking endpoints.

Mints its own ephemeral admin user so it doesn't depend on, or modify,
the real seeded admin. Cleans up everything it creates.
"""

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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from api.server import app
from database.auth import hash_password
from database.connection import SessionLocal
from database.models import Appointment, User
from services import booking_service


client = TestClient(app)


def _make_admin():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"admin-smoke-{suffix}",
            email=f"admin-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _delete_user(user_id):
    db = SessionLocal()
    try:
        db.execute(sql_text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
    finally:
        db.close()


def _seed_appointment(event_id):
    """Create one appointment via the service layer (skipping HTTP) so we have
    something to list/patch."""
    db = SessionLocal()
    try:
        # Find next open slot.
        from datetime import date
        slot_start = None
        slot_dur = None
        today = date.today()
        for offset in range(0, 30):
            d = today + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                if day["slots"]:
                    slot_start = day["slots"][0]["start"]
                    slot_dur = day["slots"][0]["duration_minutes"]
                    break
            if slot_start:
                break
        assert slot_start, "no open slot for seeding"

        slot_start_utc = slot_start.astimezone(timezone.utc)
        appt = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_start_utc,
            slot_end_at=slot_start_utc + timedelta(minutes=slot_dur),
            slot_duration_minutes=slot_dur,
            timezone="America/Chicago",
            celebrant_first_name="Admin Smoke",
            party_size_bucket="solo",
            phone="(210) 555-0188",
            email="admin-smoke-target@example.com",
            event_id=event_id,
            status="confirmed",
            utm_source="smoke",
            utm_campaign="phase4-tests",
            time_on_widget_ms=30000,
            interaction_count=8,
            steps_completed=3,
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        return appt.id
    finally:
        db.close()


def _cleanup_appointment(event_id):
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM appointments WHERE event_id = :eid"),
            {"eid": event_id},
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

resp = client.get("/api/admin/booking/appointments")
assert resp.status_code == 401, f"expected 401 unauth, got {resp.status_code}: {resp.text}"
print("auth required ok")


# ---------------------------------------------------------------------------
# Mint admin + log in
# ---------------------------------------------------------------------------

user_id: int | None = None
user_email: str | None = None
event_id = f"admin-smoke-{uuid.uuid4()}"
appt_id = None
try:
    # Seed inside the try so a mid-seed exception still hits the finally
    # for cleanup of any partially-created row.
    user_id, user_email = _make_admin()

    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer " + token}
    print("admin login ok")

    appt_id = _seed_appointment(event_id)

    # List
    resp = client.get(
        "/api/admin/booking/appointments",
        params={"q": "Admin Smoke"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(it["id"] == appt_id for it in body["items"])
    print(f"list ok ({body['total']} total)")

    # Filter by status
    resp = client.get(
        "/api/admin/booking/appointments",
        params={"status": "confirmed", "limit": 5},
        headers=auth,
    )
    assert resp.status_code == 200
    assert all(it["status"] == "confirmed" for it in resp.json()["items"])
    print("status filter ok")

    # Bad status
    resp = client.get(
        "/api/admin/booking/appointments",
        params={"status": "garbage"},
        headers=auth,
    )
    assert resp.status_code == 400
    print("bad status rejected ok")

    # Detail
    resp = client.get(f"/api/admin/booking/appointments/{appt_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == appt_id
    assert "raw_payload" in detail and detail["raw_payload"] == {"smoke": True}
    assert detail["enrichment"] is None
    # CRM linkage fields exist with sensible defaults. Seed bypassed
    # the booking flow, so contact_id is None and the appointment is
    # not promotable until staff attach a contact.
    assert "contact_id" in detail and detail["contact_id"] is None
    assert "crm_event_id" in detail and detail["crm_event_id"] is None
    assert detail["contact_display_name"] is None
    assert detail["crm_event_name"] is None
    assert detail["crm_event_status"] is None
    assert detail["can_promote_to_event"] is False
    print("detail ok (CRM fields exposed)")

    # 404 for missing
    resp = client.get("/api/admin/booking/appointments/999999999", headers=auth)
    assert resp.status_code == 404
    print("missing id 404 ok")

    # Patch — set internal notes + mark attended + record purchase
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=auth,
        json={
            "status": "attended",
            "internal_notes": "Showed up. Loved the trumpet silhouette.",
            "purchase_value_cents": 285000,
        },
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert after["status"] == "attended"
    assert after["attended_at"] is not None
    assert after["internal_notes"].startswith("Showed up")
    assert after["purchase_value_cents"] == 285000
    assert after["purchase_at"] is not None
    print("patch (status + notes + purchase) ok")

    # Patch with no changes is a no-op success
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=auth,
        json={},
    )
    assert resp.status_code == 200
    print("empty patch ok")

    # Clear purchase value by sending null explicitly
    resp = client.patch(
        f"/api/admin/booking/appointments/{appt_id}",
        headers=auth,
        json={"purchase_value_cents": None},
    )
    assert resp.status_code == 200, resp.text
    cleared = resp.json()
    assert cleared["purchase_value_cents"] is None, cleared
    assert cleared["purchase_at"] is None, cleared
    print("clear purchase value ok")

    # Inclusive to_date: filter for the day of the appointment must include it.
    appt_day = datetime.fromisoformat(after["slot_start_at"]).date().isoformat()
    resp = client.get(
        "/api/admin/booking/appointments",
        params={"from": appt_day, "to": appt_day},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert any(it["id"] == appt_id for it in listed["items"]), (
        "to_date should be inclusive: appointment on the queried day was excluded"
    )
    print("inclusive to_date ok")

finally:
    if appt_id:
        _cleanup_appointment(event_id)
    if user_id is not None:
        _delete_user(user_id)
    print("cleanup done")

print("\nadmin booking smoke ok")
