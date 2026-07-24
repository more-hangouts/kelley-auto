"""Smoke for sales lead-search result coverage.

Seeds one contact + one linked event + one linked appointment, then
exercises each search branch:

  - Name fragment (with and without accent) matches contact + event +
    appointment, via f_unaccent.
  - Phone digit fragment matches contact and appointment.
  - Event theme substring matches the event row.
  - Confirmation code (canonical form) matches the appointment row.

For each match type, asserts the result `route` points to the
appointment, since contact/event results route through their most-
recent appointment. Also asserts `contact_id` and `assigned_user_id`
flow through where expected.

Names use the `Sales Search Smoke ` prefix added to
cleanup_admin_smoke_pollution.sql so a crashed run is sweepable.
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Appointment, Contact, Event, User  # noqa: E402

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_appointment_ids: list[int] = []


def _make_user(*, role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{suffix}"
        u = User(
            username=username,
            email=f"{role}-smoke-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin-not-the-password"),
            full_name=f"Smoke {role.title()} {suffix}",
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


def _seed_fixtures(assigned_user_id: int) -> dict:
    """One contact + one event + one appointment linking them."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"96655{uuid.uuid4().int % 100_000:05d}"  # 10-digit phone
        phone_e164 = f"+1{digits}"

        contact = Contact(
            first_name="Lorena",
            last_name="Hernández",
            display_name=f"Sales Search Smoke Lorena {tag}",
            email=f"sssmoke-{tag.lower()}@example.com",
            phone=f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}",
            phone_e164=phone_e164,
            tags=["sales-search-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Sales Search Smoke Lorena Quince {tag}",
            event_date=date(2027, 4, 12),
            quince_theme=f"Floral {tag}",
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="sales-search-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        # Confirmation code: 8 alphanumerics. Booked codes get
        # hyphenated for display but stored canonical (no hyphens,
        # uppercased) — matches the production format.
        code = f"SSSM{tag}"  # 10 chars uppercase alphanumerics
        slot = datetime.now(timezone.utc) + timedelta(days=3)
        appt = Appointment(
            confirmation_code=code,
            slot_start_at=slot,
            slot_end_at=slot + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name="Sofía",
            celebrant_last_name="Hernández",
            parent_first_name="Lorena",
            parent_last_name="Hernández",
            party_size_bucket="2_3",
            phone=contact.phone,
            phone_e164=phone_e164,
            email=contact.email,
            status="confirmed",
            assigned_user_id=assigned_user_id,
            contact_id=contact.id,
            crm_event_id=event.id,
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _created_appointment_ids.append(appt.id)

        return {
            "tag": tag,
            "digits": digits,
            "code": code,
            "contact_id": contact.id,
            "event_id": event.id,
            "appointment_id": appt.id,
        }
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _created_appointment_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appointment_ids},
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


def _result_ids(results: list[dict], type_: str) -> list[int]:
    return [r["id"] for r in results if r["type"] == type_]


def main() -> None:
    flush_for_testing()

    # ---- Mint admin + sales users; sign sales in via PIN ----
    admin_id, _ = _make_user(role="admin")
    admin_headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    sales_id, sales_username = _make_user(role="sales")
    pin_resp = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin", headers=admin_headers
    )
    assert pin_resp.status_code == 200, pin_resp.text
    minted_pin = pin_resp.json()["pin"]
    login = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert login.status_code == 200, login.text
    sales_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    seed = _seed_fixtures(assigned_user_id=sales_id)
    appt_route = f"/appointments/{seed['appointment_id']}"

    # ---- Name fragment matches all three result types ----
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": "lorena"},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seed["contact_id"] in _result_ids(body["results"], "contact"), body
    assert seed["event_id"] in _result_ids(body["results"], "event"), body
    assert seed["appointment_id"] in _result_ids(
        body["results"], "appointment"
    ), body

    for r in body["results"]:
        if r["id"] in (seed["contact_id"], seed["event_id"], seed["appointment_id"]):
            assert r["route"] == appt_route, r
            # contact_id should be the seeded contact for all three types
            # (contact: itself; event: primary_contact; appointment: linked).
            assert r["contact_id"] == seed["contact_id"], r
            # assigned_user_id flows through: appointment row carries
            # assigned_user_id directly; contact's most-recent appointment
            # is the same row; event has owner_user_id=None so the result
            # is None — the appointment match still carries it.
            if r["type"] == "appointment":
                assert r["assigned_user_id"] == sales_id, r

    # ---- Accent-insensitive match ("hernandez" finds "Hernández") ----
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": "hernandez"},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    assert seed["contact_id"] in _result_ids(
        resp.json()["results"], "contact"
    ), resp.text

    # ---- Phone digits match contact + appointment ----
    digits_tail = seed["digits"][-7:]  # last 7 digits is plenty specific
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": digits_tail},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seed["contact_id"] in _result_ids(body["results"], "contact"), body
    assert seed["appointment_id"] in _result_ids(
        body["results"], "appointment"
    ), body

    # ---- Event theme match returns event only ----
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": f"Floral {seed['tag']}"},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seed["event_id"] in _result_ids(body["results"], "event"), body

    # ---- Confirmation code (raw) matches appointment ----
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": seed["code"]},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seed["appointment_id"] in _result_ids(
        body["results"], "appointment"
    ), body

    # ---- Confirmation code with hyphens still matches ----
    # Real staff often type the hyphenated display form.
    hyphenated = "-".join(
        [seed["code"][0:3], seed["code"][3:6], seed["code"][6:]]
    )
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": hyphenated},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seed["appointment_id"] in _result_ids(
        body["results"], "appointment"
    ), body

    print("sales_search_results smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
