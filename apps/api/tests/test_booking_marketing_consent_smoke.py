"""Smoke test for booking widget marketing_consent field.

Covers the four state transitions the consent design has to get right:

1. First booking with marketing_consent=False → contact row has
   marketing_consent_at = NULL.
2. Same contact's NEXT booking with marketing_consent=True → row's
   marketing_consent_at is now set.
3. Same contact's NEXT booking with marketing_consent=False (after
   prior opt-in) → row's marketing_consent_at is PRESERVED, not cleared.
   This is the load-bearing invariant: an unchecked checkbox on a
   return booking must not look like a withdrawal of consent.
4. Brand-new contact with marketing_consent=True on its first booking
   → row's marketing_consent_at is set on creation.

Run with: ``python tests/test_booking_marketing_consent_smoke.py``
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from api.server import app
from database.connection import SessionLocal
from services import booking_service


client = TestClient(app)


def _open_slots(n: int) -> list[tuple[datetime, int]]:
    """Return n distinct bookable slot starts (UTC) + their durations."""
    today = date.today()
    db = SessionLocal()
    out: list[tuple[datetime, int]] = []
    try:
        for offset in range(0, 60):
            d = today + timedelta(days=offset)
            days = booking_service.compute_availability(
                db, from_date=d, to_date=d, min_lead_minutes=120
            )
            for day in days:
                for slot in day["slots"]:
                    out.append(
                        (slot["start"].astimezone(timezone.utc), slot["duration_minutes"])
                    )
                    if len(out) == n:
                        return out
    finally:
        db.close()
    raise RuntimeError(f"only found {len(out)}/{n} open slots in next 60 days")


def _cleanup(event_ids: list[str], phone_e164: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM appointment_session_events WHERE event_id = ANY(:eids)"),
            {"eids": event_ids},
        )
        rows = db.execute(
            sql_text(
                "SELECT id, contact_id, crm_event_id FROM appointments "
                "WHERE event_id = ANY(:eids)"
            ),
            {"eids": event_ids},
        ).all()
        ids = [r[0] for r in rows]
        contact_ids = sorted({r[1] for r in rows if r[1] is not None})
        crm_event_ids = sorted({r[2] for r in rows if r[2] is not None})
        if crm_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": crm_event_ids},
            )
        if ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM contacts WHERE id = ANY(:cids) "
                    "AND phone_e164 = :phone "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM appointments WHERE contact_id = contacts.id"
                    ") "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM events WHERE primary_contact_id = contacts.id"
                    ")"
                ),
                {"cids": contact_ids, "phone": phone_e164},
            )
        db.commit()
    finally:
        db.close()


def _payload(event_id, slot_start_utc, duration, *, phone, email, marketing_consent):
    return {
        "slot_start": slot_start_utc.isoformat(),
        "slot_duration_minutes": duration,
        "parent_first_name": "Consent",
        "parent_last_name": "Smoke",
        "celebrant_first_name": "Tester",
        "event_date": (date.today() + timedelta(days=180)).isoformat(),
        "party_size": "3_4",
        "phone": phone,
        "email": email,
        "note": None,
        "marketing_consent": marketing_consent,
        "event_id": event_id,
        "visitor_id": str(uuid.uuid4()),
        "session_id": f"smoke-{uuid.uuid4().hex[:32]}",
        "attribution": {"page_url": "https://shopbellasxv.com/"},
        "device": {"device_type": "desktop", "user_agent": "consent-smoke/1.0"},
        "behavior": {
            "time_on_widget_ms": 45000,
            "interaction_count": 12,
            "steps_completed": 3,
        },
    }


def _consent_at(phone_e164: str):
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT id, marketing_consent_at FROM contacts WHERE phone_e164 = :p"
            ),
            {"p": phone_e164},
        ).first()
        return row
    finally:
        db.close()


# Two distinct test contacts — one for the state-transition flow on a
# single contact, one for the brand-new-contact-with-True path.
RUN_TAG = int(uuid.uuid4().int % 10000)
PHONE_A_LOCAL = f"(210) 555-{RUN_TAG:04d}"
PHONE_A_E164 = booking_service.normalize_phone_e164(PHONE_A_LOCAL)
EMAIL_A = f"smoke-consent-a-{RUN_TAG:04d}@example.com"

RUN_TAG_B = (RUN_TAG + 1) % 10000
PHONE_B_LOCAL = f"(210) 555-{RUN_TAG_B:04d}"
PHONE_B_E164 = booking_service.normalize_phone_e164(PHONE_B_LOCAL)
EMAIL_B = f"smoke-consent-b-{RUN_TAG_B:04d}@example.com"

slots = _open_slots(4)
event_ids_a = [f"smoke-consent-a-{uuid.uuid4()}" for _ in range(3)]
event_id_b = f"smoke-consent-b-{uuid.uuid4()}"

try:
    # ---- Contact A, booking 1: opt-out → NULL ----
    resp = client.post(
        "/api/booking/appointments",
        json=_payload(
            event_ids_a[0], slots[0][0], slots[0][1],
            phone=PHONE_A_LOCAL, email=EMAIL_A, marketing_consent=False,
        ),
    )
    assert resp.status_code == 201, resp.text
    row = _consent_at(PHONE_A_E164)
    assert row is not None, "contact A not created"
    assert row.marketing_consent_at is None, (
        f"opt-out booking should leave marketing_consent_at NULL, got {row.marketing_consent_at!r}"
    )
    print("opt-out on first booking leaves marketing_consent_at NULL ok")

    # ---- Contact A, booking 2: opt-in → timestamp now set ----
    resp = client.post(
        "/api/booking/appointments",
        json=_payload(
            event_ids_a[1], slots[1][0], slots[1][1],
            phone=PHONE_A_LOCAL, email=EMAIL_A, marketing_consent=True,
        ),
    )
    assert resp.status_code == 201, resp.text
    row = _consent_at(PHONE_A_E164)
    assert row.marketing_consent_at is not None, (
        "opt-in on a previously-NULL contact should set marketing_consent_at"
    )
    first_consent_at = row.marketing_consent_at
    assert (datetime.now(timezone.utc) - first_consent_at).total_seconds() < 60, (
        f"marketing_consent_at should be ~NOW, got {first_consent_at!r}"
    )
    print("opt-in flips NULL → timestamp ok")

    # ---- Contact A, booking 3: opt-out AFTER prior opt-in → preserved ----
    resp = client.post(
        "/api/booking/appointments",
        json=_payload(
            event_ids_a[2], slots[2][0], slots[2][1],
            phone=PHONE_A_LOCAL, email=EMAIL_A, marketing_consent=False,
        ),
    )
    assert resp.status_code == 201, resp.text
    row = _consent_at(PHONE_A_E164)
    assert row.marketing_consent_at == first_consent_at, (
        f"unchecked checkbox on a return booking must not clear prior consent. "
        f"before: {first_consent_at!r} after: {row.marketing_consent_at!r}"
    )
    print("opt-out after prior opt-in preserves the timestamp ok")

    # ---- Contact B, fresh contact, first booking with opt-in → set on create ----
    resp = client.post(
        "/api/booking/appointments",
        json=_payload(
            event_id_b, slots[3][0], slots[3][1],
            phone=PHONE_B_LOCAL, email=EMAIL_B, marketing_consent=True,
        ),
    )
    assert resp.status_code == 201, resp.text
    row = _consent_at(PHONE_B_E164)
    assert row is not None, "contact B not created"
    assert row.marketing_consent_at is not None, (
        "opt-in on a brand-new contact should set marketing_consent_at on creation"
    )
    print("opt-in on brand-new contact sets timestamp at creation ok")

    print("\nAll marketing_consent smokes passed.")
finally:
    _cleanup(event_ids_a, PHONE_A_E164)
    _cleanup([event_id_b], PHONE_B_E164)
