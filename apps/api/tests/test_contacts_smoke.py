"""Smoke tests for the contacts surface (Phase B of the Contact UX plan).

Mints its own ephemeral admin user, seeds two contacts (one with linked
appointments so the context counts and alternate-celebrant list have
something to return), exercises GET, PATCH (rename, recompose, explicit
display, email/phone clear, phone re-normalize, phone collision), and
cleans up. No external deps, no leftover rows.
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
from database.models import Appointment, Contact, Event, User  # noqa: E402
from services import booking_service  # noqa: E402


client = TestClient(app)


def _make_admin():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"contacts-smoke-{suffix}",
            email=f"contacts-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Contacts Smoke Admin",
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


def _seed_contacts(analytics_event_id: str, suffix_a: str, suffix_b: str):
    """Two contacts. A has linked appointments under two celebrant names so
    the context aggregates and `alternate_celebrants` are non-trivial. B
    exists solely so we can test phone collisions against an existing row.
    """
    db = SessionLocal()
    try:
        a = Contact(
            first_name="debbie",
            last_name=None,
            display_name="debbie",
            email=f"{analytics_event_id}-a@example.com",
            phone="(210) 555-0188",
            phone_e164=f"+1999{suffix_a}",
            tags=["contacts-smoke"],
        )
        b = Contact(
            first_name="Other",
            last_name="Person",
            display_name="Other Person",
            email=f"{analytics_event_id}-b@example.com",
            phone="(210) 555-0199",
            phone_e164=f"+1999{suffix_b}",
            tags=["contacts-smoke"],
        )
        db.add_all([a, b])
        db.flush()

        # Two appointments on contact A under different celebrants — Phase B
        # should surface "Chumba Casino" as an alternate while the contact
        # display_name is still "debbie".
        for celebrant_first, days_out, code_seed in (
            ("debbie", 7, "first"),
            ("Chumba Casino", 14, "second"),
        ):
            slot_start = datetime.now(timezone.utc) + timedelta(days=days_out)
            appt = Appointment(
                confirmation_code=booking_service.generate_unique_confirmation_code(db),
                slot_start_at=slot_start,
                slot_end_at=slot_start + timedelta(minutes=45),
                slot_duration_minutes=45,
                timezone="America/Chicago",
                celebrant_first_name=celebrant_first,
                celebrant_last_name=None,
                event_date=date(2026, 10, 10),
                party_size_bucket="solo",
                phone=a.phone,
                phone_e164=a.phone_e164,
                email=a.email,
                contact_id=a.id,
                event_id=f"{analytics_event_id}-{code_seed}",
                status="confirmed",
                user_journey=[],
                raw_payload={"smoke": True},
            )
            db.add(appt)

        db.commit()
        return a.id, b.id
    finally:
        db.close()


def _cleanup(analytics_event_id: str, contact_ids: list[int], user_id: int):
    db = SessionLocal()
    try:
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM events WHERE primary_contact_id = ANY(:ids)"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM appointments WHERE contact_id = ANY(:ids)"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------

resp = client.get("/api/contacts/1")
assert resp.status_code == 401, f"expected 401 unauth, got {resp.status_code}: {resp.text}"
print("auth required ok")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

user_id, user_email = _make_admin()
analytics_event_id = f"contacts-smoke-{uuid.uuid4().hex[:12]}"
# 7-digit numeric suffixes — phone_e164 needs a 10-digit US number after the
# +1, and the seed phones are "+1999<suffix>". uuid hex contains a-f, which
# normalize_phone_e164 strips, producing an invalid number.
suffix_a = f"{uuid.uuid4().int % 10_000_000:07d}"
suffix_b = f"{uuid.uuid4().int % 10_000_000:07d}"
contact_ids: list[int] = []

try:
    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    print("admin login ok")

    contact_a_id, contact_b_id = _seed_contacts(
        analytics_event_id, suffix_a, suffix_b
    )
    contact_ids = [contact_a_id, contact_b_id]
    print(f"seeded contacts a={contact_a_id} b={contact_b_id}")

    # ----- 404 on unknown -----
    resp = client.get("/api/contacts/999999999", headers=auth)
    assert resp.status_code == 404, resp.text
    print("get unknown 404 ok")

    # ----- GET returns editable fields + context -----
    resp = client.get(f"/api/contacts/{contact_a_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["first_name"] == "debbie"
    assert body["display_name"] == "debbie"
    assert body["appointment_count"] == 2, body
    assert body["event_count"] == 0, body
    assert body["alternate_celebrants"] == ["Chumba Casino"], body
    # Phase 3 contract expansion: address + linked_events present even
    # when the contact has none of either.
    assert body["address"] == {}, body
    assert body["linked_events"] == [], body
    print("get returns editable fields and context ok")

    # ----- linked_events: ordering + server-computed route -----
    # Seed three events on contact A. event_b has a later date so it
    # should rank first; event_a and event_c are undated and ranked
    # by created_at DESC. We assign explicit created_at values
    # because Postgres `NOW()` returns transaction time, which is
    # identical for inserts that share a single transaction; without
    # explicit timestamps the tiebreak becomes meaningless.
    base_now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        event_a = Event(
            primary_contact_id=contact_a_id,
            event_type="quinceanera",
            event_name="Old undated quince",
            event_date=None,
            quince_theme_colors=[],
            status="lead",
            status_changed_at=base_now - timedelta(seconds=2),
            created_at=base_now - timedelta(seconds=2),
            notes="contacts-smoke",
        )
        event_b = Event(
            primary_contact_id=contact_a_id,
            event_type="quinceanera",
            event_name="Future dated quince",
            event_date=date(2027, 6, 15),
            quince_theme_colors=[],
            status="sold",
            status_changed_at=base_now - timedelta(seconds=1),
            created_at=base_now - timedelta(seconds=1),
            notes="contacts-smoke",
        )
        event_c = Event(
            primary_contact_id=contact_a_id,
            event_type="quinceanera",
            event_name="Newer undated quince",
            event_date=None,
            quince_theme_colors=[],
            status="lead",
            status_changed_at=base_now,
            created_at=base_now,
            notes="contacts-smoke",
        )
        db.add_all([event_a, event_b, event_c])
        db.commit()
        seeded_event_ids = (event_a.id, event_b.id, event_c.id)
    finally:
        db.close()

    resp = client.get(f"/api/contacts/{contact_a_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["event_count"] == 3, body
    linked = body["linked_events"]
    assert len(linked) == 3, linked
    # Order: dated future first, then undated by created_at DESC.
    assert linked[0]["id"] == seeded_event_ids[1], linked  # event_b (dated)
    assert linked[1]["id"] == seeded_event_ids[2], linked  # event_c (newer undated)
    assert linked[2]["id"] == seeded_event_ids[0], linked  # event_a (older undated)
    # Each row is fully populated and routes are server-computed.
    for row in linked:
        assert row["event_type"] == "quinceanera", row
        assert row["status"] in {"lead", "sold"}, row
        assert row["route"] == f"/events/{row['id']}", row
        assert isinstance(row["event_name"], str) and row["event_name"], row
    # Future dated row keeps its event_date, undated rows are null.
    assert linked[0]["event_date"] == "2027-06-15", linked
    assert linked[1]["event_date"] is None, linked
    assert linked[2]["event_date"] is None, linked
    print("linked_events ordering + route ok")

    # ----- recompose: changing first/last refreshes display_name -----
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"first_name": "Maria", "last_name": "Garcia"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["first_name"] == "Maria"
    assert body["last_name"] == "Garcia"
    assert body["display_name"] == "Maria Garcia", body
    print("first/last recomposes display_name ok")

    # Phase A "alternate" caption disappears once the contact name matches
    # the most recent celebrant. "Chumba Casino" remains the alternate.
    assert "Chumba Casino" in body["alternate_celebrants"]
    assert "Maria Garcia" not in body["alternate_celebrants"]
    print("alternate celebrants reflect new contact name ok")

    # ----- explicit display_name overrides recompose -----
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"first_name": "Mary", "display_name": "Mary G."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["first_name"] == "Mary"
    assert body["last_name"] == "Garcia"  # unchanged
    assert body["display_name"] == "Mary G.", body
    print("explicit display_name wins over recompose ok")

    # ----- explicit empty display_name rejected -----
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"display_name": "   "},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "display_name_required"
    print("empty display_name rejected ok")

    # ----- phone change re-normalizes phone_e164 -----
    new_phone_suffix = f"{uuid.uuid4().int % 10_000_000:07d}"
    new_e164 = f"+1888{new_phone_suffix}"
    # Build a US-format input that normalize_phone_e164 maps to new_e164.
    digits = new_e164[2:]  # "888XXXXXXX"
    pretty = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"phone": pretty},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["phone"] == pretty, body
    assert body["phone_e164"] == new_e164, body
    print("phone re-normalizes ok")

    # ----- phone collision returns 409 with conflict_contact_id -----
    db = SessionLocal()
    try:
        b = db.get(Contact, contact_b_id)
        b_e164 = b.phone_e164
        b_pretty = f"({b_e164[2:5]}) {b_e164[5:8]}-{b_e164[8:]}"
    finally:
        db.close()
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"phone": b_pretty},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "phone_collision", body
    assert body["detail"]["conflict_contact_id"] == contact_b_id, body
    print("phone collision 409 with conflict id ok")

    # After 409 the contact's phone_e164 should be unchanged from before the
    # failed PATCH (still new_e164, set by the previous successful call).
    db = SessionLocal()
    try:
        a = db.get(Contact, contact_a_id)
        assert a.phone_e164 == new_e164, a.phone_e164
    finally:
        db.close()
    print("contact unchanged after collision ok")

    # ----- clearing phone via null -----
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"phone": None},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["phone"] is None
    assert body["phone_e164"] is None
    print("clear phone ok")

    # ----- notes + tags round-trip -----
    resp = client.patch(
        f"/api/contacts/{contact_a_id}",
        headers=auth,
        json={"notes": "VIP — repeat customer", "tags": ["repeat", "vip"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notes"] == "VIP — repeat customer"
    assert sorted(body["tags"]) == ["repeat", "vip"]
    print("notes + tags ok")

    # ----- 404 on PATCH unknown -----
    resp = client.patch(
        "/api/contacts/999999999", headers=auth, json={"first_name": "x"}
    )
    assert resp.status_code == 404, resp.text
    print("patch unknown 404 ok")

    print("\nALL CONTACTS SMOKE TESTS PASSED")
finally:
    _cleanup(analytics_event_id, contact_ids, user_id)
