"""Smoke tests for the public booking API.

Runs against the dev database (mutates state, cleans up after itself).
Invoke with: ``python tests/test_booking_smoke.py``
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, time as dtime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Load real .env first so we hit the real dev DB rather than fake defaults.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from zoneinfo import ZoneInfo

from api.server import app
from database.connection import SessionLocal
from database.models import Appointment, AppointmentSessionEvent, Contact, Event
from services import booking_service
from services.booking_tokens import cancel_url, mint_token


client = TestClient(app)


def _next_open_slot() -> tuple[datetime, int]:
    """Return the next bookable slot start (UTC) and its duration_minutes."""
    today = date.today()
    db = SessionLocal()
    try:
        for offset in range(0, 30):
            d = today + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                for slot in day["slots"]:
                    return slot["start"].astimezone(timezone.utc), slot["duration_minutes"]
    finally:
        db.close()
    raise RuntimeError("no open slot found within 30 days — seed data missing?")


def _cleanup_event_id(event_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM appointment_session_events WHERE event_id = :eid"),
            {"eid": event_id},
        )
        rows = db.execute(
            sql_text(
                "SELECT id, contact_id, crm_event_id FROM appointments "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        ).all()
        ids = [r[0] for r in rows]
        contact_ids = sorted({r[1] for r in rows if r[1] is not None})
        crm_event_ids = sorted({r[2] for r in rows if r[2] is not None})
        # Auto-promotion creates an event per booking. Drop those first so the
        # contact deletion below isn't blocked by the RESTRICT FK on
        # events.primary_contact_id. Cascades clean up event_participants and
        # event_status_change_events.
        if crm_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": crm_event_ids},
            )
        if ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointments WHERE rescheduled_from_id = ANY(:ids)"
                ),
                {"ids": ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        # Delete contacts only if no other appointments or events reference
        # them — paranoid in case a real customer shares the smoke-test
        # phone number.
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM contacts WHERE id = ANY(:cids) "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM appointments WHERE contact_id = contacts.id"
                    ") "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM events WHERE primary_contact_id = contacts.id"
                    ")"
                ),
                {"cids": contact_ids},
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service-layer
# ---------------------------------------------------------------------------

assert booking_service.normalize_phone_e164("(210) 670-5845") == "+12106705845"
assert booking_service.normalize_phone_e164("12106705845") == "+12106705845"
assert booking_service.normalize_phone_e164("+44 20 7946 0958") == "+442079460958"
assert booking_service.normalize_phone_e164("nope") is None
assert booking_service.normalize_phone_e164("") is None
print("phone normalization ok")

# Confirmation code uniqueness across many draws. Post-D1: codes are
# stored canonical (no hyphens, `BX` + 20-char body). Display layer
# adds hyphens via `format_confirmation_code`.
db = SessionLocal()
try:
    codes = {booking_service.generate_unique_confirmation_code(db) for _ in range(50)}
    assert len(codes) == 50, "duplicate confirmation codes generated"
    assert all(c.startswith("BX") and len(c) == 22 and "-" not in c for c in codes)
finally:
    db.close()
print("confirmation codes ok")


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

resp = client.get("/api/booking/theme")
assert resp.status_code == 200, resp.text
theme = resp.json()
assert "theme" in theme and "copy_text" in theme and "flow" in theme
assert theme["copy_text"]["header_title"] == "Initial consultation"
print("theme endpoint ok")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

today = date.today()
end = today + timedelta(days=14)
resp = client.get(
    "/api/booking/availability",
    params={"from": today.isoformat(), "to": end.isoformat()},
)
assert resp.status_code == 200, resp.text
av = resp.json()
assert av["timezone"] == "America/Chicago"
assert len(av["days"]) >= 1
# Mon (weekday 0) and Tue (weekday 1) should never have slots — shop closed.
mon_tue_slots = [
    s for d in av["days"] if d["weekday"] in (0, 1) for s in d["slots"]
]
assert mon_tue_slots == [], "Mon/Tue should have no availability"
# Some open day in the next 14 should have at least one slot.
total_slots = sum(len(d["slots"]) for d in av["days"])
assert total_slots > 0, "expected at least one open slot in next 14 days"
print(f"availability endpoint ok ({total_slots} slots in next 14 days)")

# Bad range
resp = client.get(
    "/api/booking/availability",
    params={"from": end.isoformat(), "to": today.isoformat()},
)
assert resp.status_code == 400
print("availability rejects reversed range ok")


# ---------------------------------------------------------------------------
# Booking submission + idempotency
# ---------------------------------------------------------------------------

slot_start_utc, duration = _next_open_slot()
event_id = f"smoke-{uuid.uuid4()}"
visitor_id = str(uuid.uuid4())

payload = {
    "slot_start": slot_start_utc.isoformat(),
    "slot_duration_minutes": duration,
    "parent_first_name": "Smoke",
    "parent_last_name": "Tester",
    "celebrant_first_name": "Test Celebrant",
    "event_date": (date.today() + timedelta(days=180)).isoformat(),
    "party_size": "3_4",
    "phone": "(210) 555-0142",
    "email": "smoke+test@example.com",
    "note": "Smoke test booking",
    "event_id": event_id,
    "visitor_id": visitor_id,
    "session_id": "smoke-session-1",
    "attribution": {
        "page_url": "https://shopbellasxv.com/",
        "utm_source": "smoke",
        "utm_campaign": "phase2-tests",
        "fbclid": "fbclid-smoke",
    },
    "device": {
        "device_type": "desktop",
        "user_agent": "smoke-test/1.0",
        "browser_timezone": "America/Chicago",
    },
    "behavior": {
        "time_on_widget_ms": 45000,
        "interaction_count": 12,
        "steps_completed": 3,
        "user_journey": [{"step": "date"}, {"step": "who"}, {"step": "contact"}],
    },
}

try:
    resp = client.post("/api/booking/appointments", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["confirmation_code"].startswith("BX-")
    assert body["status"] == "confirmed"
    assert body["reschedule_url"].startswith("http")
    assert body["cancel_url"].startswith("http")
    # Phase 5: every booking response carries the tokenized Boutique
    # Experience URL. With no profile id submitted, attached should be
    # False so the booking widget shows the "complete profile" CTA.
    assert body["boutique_experience_attached"] is False, body
    assert "/fit-prep.html?token=" in body["boutique_experience_url"], body
    code1 = body["confirmation_code"]
    print(f"booking creation ok ({code1})")

    # Idempotency: same event_id → same appointment, no duplicate
    resp = client.post("/api/booking/appointments", json=payload)
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["confirmation_code"] == code1
    print("booking idempotency ok")

    # Contact identity: appointment must be linked to a contact, the contact
    # must carry the normalized phone, and a same-phone retry must NOT create
    # a duplicate contact row.
    db = SessionLocal()
    try:
        appt_row = (
            db.query(Appointment)
            .filter(Appointment.event_id == event_id)
            .first()
        )
        assert appt_row is not None
        assert appt_row.contact_id is not None, "booking did not populate contact_id"
        contact_row = db.get(Contact, appt_row.contact_id)
        assert contact_row is not None
        assert contact_row.phone_e164 == "+12105550142", contact_row.phone_e164
        # The parent is the contact identity (booker), not the celebrant.
        assert contact_row.first_name == "Smoke", contact_row.first_name
        assert contact_row.last_name == "Tester", contact_row.last_name
        assert contact_row.email == "smoke+test@example.com"
        # Only one contact for this phone — find_or_create dedup works.
        same_phone = (
            db.query(Contact)
            .filter(Contact.phone_e164 == contact_row.phone_e164)
            .count()
        )
        assert same_phone == 1, same_phone
        # Auto-promotion: every booking should land on the pipeline as a lead.
        assert (
            appt_row.crm_event_id is not None
        ), "booking did not auto-promote to a CRM event"
        ev = db.get(Event, appt_row.crm_event_id)
        assert ev is not None
        assert ev.status == "lead", ev.status
        assert ev.event_type == "quinceanera"
        assert ev.primary_contact_id == contact_row.id
        booking_event_id_for_assertions = ev.id
    finally:
        db.close()
    print("contact identity + auto-promote ok")

    # Honeypot: fresh event_id, but company_website filled → 400
    bad = {**payload, "event_id": f"smoke-{uuid.uuid4()}", "company_website": "spam.example.com"}
    resp = client.post("/api/booking/appointments", json=bad)
    assert resp.status_code == 400
    print("honeypot rejection ok")

    # Slot tampering: try to book a Sunday at 2am (no rule covers it)
    sunday_2am = datetime.combine(
        date.today() + timedelta(days=(6 - date.today().weekday()) % 7 or 7),
        dtime(2, 0),
        tzinfo=ZoneInfo("America/Chicago"),
    )
    tamper = {
        **payload,
        "event_id": f"smoke-{uuid.uuid4()}",
        "slot_start": sunday_2am.astimezone(timezone.utc).isoformat(),
    }
    resp = client.post("/api/booking/appointments", json=tamper)
    assert resp.status_code == 409
    print("slot tampering rejected ok")

    # Cancel via signed token
    db = SessionLocal()
    try:
        appt = db.query(Appointment).filter(Appointment.event_id == event_id).first()
        assert appt is not None
        appt_id = appt.id
        # G1: mint_token now needs the Appointment row (for slot_start_at
        # bound). Detach a copy from the session so we can use it after close.
        appt_for_token = appt
        db.expunge(appt_for_token)
    finally:
        db.close()

    cancel_link = cancel_url(appt_for_token)
    token = cancel_link.rsplit("/", 1)[1]
    resp = client.post(f"/api/booking/cancel/{token}", json={"reason": "smoke cleanup"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    # Cancellation should mirror onto the linked CRM event so the kanban
    # reflects board truth.
    db = SessionLocal()
    try:
        ev = db.get(Event, booking_event_id_for_assertions)
        assert ev is not None
        assert ev.status == "cancelled", ev.status
    finally:
        db.close()
    print("cancel via signed token ok (mirrored to event)")

    # Wrong-purpose token rejected
    bad_token = mint_token(appt_for_token, "enrichment")
    resp = client.post(f"/api/booking/cancel/{bad_token}", json={})
    assert resp.status_code == 404
    print("wrong-purpose token rejected ok")

    # Abandon endpoint writes to session events, not appointments
    abandon_event_id = f"smoke-abandon-{uuid.uuid4()}"
    resp = client.post(
        "/api/booking/abandon",
        json={
            "event_id": abandon_event_id,
            "visitor_id": str(uuid.uuid4()),
            "session_id": "smoke-abandon-session",
            "step": "step_2",
            "partial": {"celebrant_first_name": "Partial"},
            "behavior": {"time_on_widget_ms": 12000, "interaction_count": 4, "steps_completed": 1},
        },
    )
    assert resp.status_code == 200
    db = SessionLocal()
    try:
        ev = (
            db.query(AppointmentSessionEvent)
            .filter(AppointmentSessionEvent.event_id == abandon_event_id)
            .first()
        )
        assert ev is not None
        assert ev.event_name == "abandoned"
        # And no appointments row was created from the abandon
        appt = (
            db.query(Appointment)
            .filter(Appointment.event_id == abandon_event_id)
            .first()
        )
        assert appt is None
        db.execute(
            sql_text("DELETE FROM appointment_session_events WHERE event_id = :eid"),
            {"eid": abandon_event_id},
        )
        db.commit()
    finally:
        db.close()
    print("abandon writes session event ok")

finally:
    _cleanup_event_id(event_id)
    # Also clean any honeypot/tamper rows that managed to insert (shouldn't have)
    print("cleanup done")

print("\nbooking smoke ok")
