"""Smoke tests for the CRM events surface.

Mints its own ephemeral admin user, seeds a contact + linked appointment +
enrichment, exercises promote/status/board, then cleans up. No external deps,
no leftover rows.
"""

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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    User,
)
from services import booking_service  # noqa: E402


client = TestClient(app)


def _make_admin():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"events-smoke-{suffix}",
            email=f"events-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Events Smoke Admin",
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


def _seed_lead(analytics_event_id: str, phone_suffix: str):
    """Create contact + appointment (already linked via contact_id) + enrichment.

    Returns (contact_id, appointment_id).
    """
    db = SessionLocal()
    try:
        contact = Contact(
            first_name="Maria",
            last_name="Garcia",
            display_name="Maria Garcia",
            email=f"{analytics_event_id}@example.com",
            phone="(210) 555-0177",
            phone_e164=f"+1999{phone_suffix}",
            tags=["events-smoke"],
        )
        db.add(contact)
        db.flush()

        slot_start = datetime.now(timezone.utc) + timedelta(days=14)
        appt = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_start,
            slot_end_at=slot_start + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name="Maria",
            celebrant_last_name="Garcia",
            event_date=date(2026, 9, 15),
            party_size_bucket="solo",
            phone="(210) 555-0177",
            phone_e164=contact.phone_e164,
            email=contact.email,
            contact_id=contact.id,
            event_id=analytics_event_id,
            status="confirmed",
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(appt)
        db.flush()

        enrichment = AppointmentEnrichmentResponse(
            appointment_id=appt.id,
            dress_styles=["ball_gown"],
            colors=["pink", "rose_gold"],
            budget_range="$1500-2500",
            quince_theme="Enchanted Garden",
            quince_theme_colors=["pink", "rose_gold", "ivory"],
            court_size=14,
            inspiration_photos=[],
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(enrichment)
        db.commit()
        return contact.id, appt.id
    finally:
        db.close()


def _cleanup(analytics_event_id: str, contact_id: int | None, user_id: int):
    db = SessionLocal()
    try:
        if contact_id is not None:
            # Events cascade participants + status_change_events. Deleting events
            # nulls appointments.crm_event_id (ON DELETE SET NULL).
            db.execute(
                sql_text(
                    "DELETE FROM events WHERE primary_contact_id = :cid"
                ),
                {"cid": contact_id},
            )
            # Enrichment cascades from appointments.
            db.execute(
                sql_text("DELETE FROM appointments WHERE event_id = :eid"),
                {"eid": analytics_event_id},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE contact_id = :cid"),
                {"cid": contact_id},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :cid"),
                {"cid": contact_id},
            )
        db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

resp = client.get("/api/events/board")
assert resp.status_code == 401, f"expected 401 unauth, got {resp.status_code}: {resp.text}"
print("auth required ok")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

user_id, user_email = _make_admin()
analytics_event_id = f"events-smoke-{uuid.uuid4().hex[:12]}"
phone_suffix = uuid.uuid4().hex[:7]
contact_id: int | None = None
appt_id: int | None = None

try:
    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    print("admin login ok")

    contact_id, appt_id = _seed_lead(analytics_event_id, phone_suffix)
    print(f"seeded contact={contact_id} appointment={appt_id}")

    # ----- workflow definition -----
    resp = client.get("/api/events/workflow/quinceanera", headers=auth)
    assert resp.status_code == 200, resp.text
    wf = resp.json()
    codes = [s["code"] for s in wf["statuses"]]
    assert codes == [
        "lead",
        "consulted",
        "sold",
        "on_order",
        "arrived",
        "in_alterations",
        "ready_for_pickup",
        "picked_up",
        "cancelled",
    ], codes
    terminals = [s["code"] for s in wf["statuses"] if s["is_terminal"]]
    assert terminals == ["picked_up", "cancelled"], terminals
    print("workflow definition ok")

    # ----- validator: rejects both origins -----
    resp = client.post(
        "/api/events",
        headers=auth,
        json={"from_appointment_id": appt_id, "primary_contact_id": contact_id},
    )
    assert resp.status_code == 422, resp.text
    print("validator rejects both origins ok")

    # ----- validator: rejects empty payload -----
    resp = client.post("/api/events", headers=auth, json={})
    assert resp.status_code == 422, resp.text
    print("validator rejects no origin ok")

    # ----- validator: walk-in requires event_name -----
    resp = client.post(
        "/api/events", headers=auth, json={"primary_contact_id": contact_id}
    )
    assert resp.status_code == 422, resp.text
    print("validator requires event_name on walk-in ok")

    # ----- promote lead -----
    resp = client.post(
        "/api/events", headers=auth, json={"from_appointment_id": appt_id}
    )
    assert resp.status_code == 201, resp.text
    event = resp.json()
    event_id = event["id"]
    assert event["status"] == "lead"
    assert event["event_type"] == "quinceanera"
    assert event["event_name"] == "Maria Garcia's Quince", event
    assert event["court_size"] == 14, event
    assert event["quince_theme"] == "Enchanted Garden", event
    assert event["budget_range"] == "$1500-2500", event
    assert event["quince_theme_colors"] == ["pink", "rose_gold", "ivory"], event
    assert event["primary_contact"]["display_name"] == "Maria Garcia"
    assert event["event_date"] == "2026-09-15", event
    print(f"promote ok (event id={event_id})")

    # appointment now linked
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appt_id)
        assert appt.crm_event_id == event_id, appt.crm_event_id
    finally:
        db.close()
    print("appointment.crm_event_id set ok")

    # quinceañera participant created
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT role, display_name, contact_id "
                "FROM event_participants WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).all()
        assert len(rows) == 1, rows
        assert rows[0].role == "quinceanera"
        assert rows[0].display_name == "Maria Garcia"
        assert rows[0].contact_id == contact_id
    finally:
        db.close()
    print("quinceanera participant ok")

    # ----- promotion uses appointment celebrant, not stale contact name -----
    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        slot_start = datetime.now(timezone.utc) + timedelta(days=21)
        shared_phone_appt = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_start,
            slot_end_at=slot_start + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name="Chumba Casino",
            celebrant_last_name=None,
            event_date=date(2026, 10, 10),
            party_size_bucket="solo",
            phone=contact.phone,
            phone_e164=contact.phone_e164,
            email="shared-phone-celebrant@example.com",
            contact_id=contact.id,
            event_id=f"{analytics_event_id}-shared",
            status="confirmed",
            user_journey=[],
            raw_payload={"smoke": True, "shared_phone": True},
        )
        db.add(shared_phone_appt)
        db.commit()
        shared_phone_appt_id = shared_phone_appt.id
    finally:
        db.close()

    resp = client.post(
        "/api/events", headers=auth, json={"from_appointment_id": shared_phone_appt_id}
    )
    assert resp.status_code == 201, resp.text
    shared_phone_event = resp.json()
    assert shared_phone_event["event_name"] == "Chumba Casino's Quince"
    assert shared_phone_event["primary_contact"]["display_name"] == "Maria Garcia"

    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT display_name, email "
                "FROM event_participants WHERE event_id = :eid"
            ),
            {"eid": shared_phone_event["id"]},
        ).all()
        assert len(rows) == 1, rows
        assert rows[0].display_name == "Chumba Casino"
        assert rows[0].email == "shared-phone-celebrant@example.com"
    finally:
        db.close()
    print("shared-phone appointment naming ok")

    # initial audit row
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT from_status, to_status FROM event_status_change_events "
                "WHERE event_id = :eid ORDER BY changed_at"
            ),
            {"eid": event_id},
        ).all()
        assert len(rows) == 1, rows
        assert rows[0].from_status is None
        assert rows[0].to_status == "lead"
    finally:
        db.close()
    print("initial audit row ok")

    # ----- second promotion of same appointment fails -----
    resp = client.post(
        "/api/events", headers=auth, json={"from_appointment_id": appt_id}
    )
    assert resp.status_code == 409, resp.text
    print("re-promote conflict ok")

    # ----- status patch: lead -> consulted -----
    resp = client.patch(
        f"/api/events/{event_id}/status",
        headers=auth,
        json={"status": "consulted", "notes": "Loved the trumpet silhouette"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "consulted"
    print("status patch ok")

    # transition audit row written
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT from_status, to_status, notes "
                "FROM event_status_change_events WHERE event_id = :eid "
                "ORDER BY changed_at DESC LIMIT 1"
            ),
            {"eid": event_id},
        ).all()
        assert rows[0].from_status == "lead"
        assert rows[0].to_status == "consulted"
        assert "trumpet" in (rows[0].notes or "")
    finally:
        db.close()
    print("audit row for transition ok")

    # ----- bad status rejected -----
    resp = client.patch(
        f"/api/events/{event_id}/status", headers=auth, json={"status": "garbage"}
    )
    assert resp.status_code == 422, resp.text
    print("bad status rejected ok")

    # ----- repeat-status no-op -----
    resp = client.patch(
        f"/api/events/{event_id}/status", headers=auth, json={"status": "consulted"}
    )
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        count = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM event_status_change_events "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).scalar()
        # initial 'lead' + transition to 'consulted' = 2; repeat must not append.
        assert count == 2, count
    finally:
        db.close()
    print("repeat-status is no-op ok")

    # ----- board reads it in consulted column -----
    resp = client.get("/api/events/board", headers=auth)
    assert resp.status_code == 200, resp.text
    board = resp.json()
    assert board["event_type"] == "quinceanera"
    consulted_col = next(c for c in board["columns"] if c["code"] == "consulted")
    assert any(card["id"] == event_id for card in consulted_col["cards"]), board
    lead_col = next(c for c in board["columns"] if c["code"] == "lead")
    assert all(card["id"] != event_id for card in lead_col["cards"])
    card = next(c for c in consulted_col["cards"] if c["id"] == event_id)
    assert card["last_appointment_at"] is not None, card
    assert card["primary_contact"]["display_name"] == "Maria Garcia"
    print("board ok")

    # ----- walk-in event from contact only -----
    resp = client.post(
        "/api/events",
        headers=auth,
        json={
            "primary_contact_id": contact_id,
            "event_name": "Walk-in test event",
            "event_date": "2026-10-01",
        },
    )
    assert resp.status_code == 201, resp.text
    walkin = resp.json()
    assert walkin["event_name"] == "Walk-in test event"
    assert walkin["status"] == "lead"
    assert walkin["event_date"] == "2026-10-01"
    print(f"walk-in create ok (event id={walkin['id']})")

    # ----- promotion of unknown appointment 404s -----
    resp = client.post(
        "/api/events", headers=auth, json={"from_appointment_id": 999_999_999}
    )
    assert resp.status_code == 404, resp.text
    print("missing appointment 404 ok")

finally:
    _cleanup(analytics_event_id, contact_id, user_id)
    print("cleanup done")

print("\nevents smoke ok")
