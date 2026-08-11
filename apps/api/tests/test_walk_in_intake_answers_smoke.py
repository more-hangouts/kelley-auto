"""Smoke tests for the walk-in sheet's intake answers (migration 109).

The printed intake sheet asked five questions. Four already had a home
(`walk_in_source` from migration 104, `budget_range`), and three did not —
what they drive, what they want, how they'll pay — so the SPA concatenated
them into `events.notes` as prose. Migration 109 gave each a column so they
can be grouped on. This smoke is the guard that they survive the trip.

Coverage:

  - Admin endpoint persists all three onto the event.
  - Sales endpoint persists the same three. Both routes share one service,
    but the payload crosses two different router models on the way in, and
    that seam is exactly where a field gets silently dropped.
  - Both enum columns reject an off-list value with a 422 carrying a code
    the SPA can explain, rather than a 500 from the CHECK at flush time.
  - Omitting the questionnaire entirely still files the lead, with all
    three NULL. The questions are a conversation aid, not a gate — a rep
    pulled away mid-sentence must still be able to save what they have.
  - "Not sure yet" is absence, not a value: an empty string stores NULL so
    an unasked question and an undecided answer never look different.
  - The event detail endpoint serves all three back, since a column nobody
    can read is the same as no column.
  - The opening timeline row carries the answers, so the deal's audit trail
    is a faithful snapshot of the sheet even if the columns are edited later.

Plus migration 110's sales credit, which is a *different axis from lead
ownership* and is the regression guard for the two being conflated:

  - Crediting a salesperson does NOT make them the lead owner. Ownership
    still falls to the admin who filed it. This is the whole point of the
    column — the CRM is worked by admin staff, while the rep who brought
    the customer in may never open it.
  - Credit is never invented: filing without one leaves it NULL rather than
    defaulting to the actor, so nobody is owed commission by accident.
  - An inactive or non-staff id is rejected rather than stored.
  - The detail endpoint serves the credited rep back as its own object.

Runs serially per project convention (several smokes touch shared
singletons like confirmation_code state).

    .venv/bin/python tests/test_walk_in_intake_answers_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
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

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import ActivityLog, Event, User  # noqa: E402
from tests._attendance_helpers import (  # noqa: E402
    restore_gate,
    snapshot_and_disable_gate,
)

client = TestClient(app)

_PASSWORD = "smoke-pass-12345"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_user(role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"intake-smoke-{role}-{suffix}",
            email=f"intake-smoke-{role}-{suffix}@example.com",
            hashed_password=hash_password(_PASSWORD),
            full_name=f"Intake Smoke {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _unique_phone() -> str:
    """A fresh number inside the unassigned 555-XXXX block, so contact
    dedupe never collides with real seed data."""
    return f"(210) 555-{uuid.uuid4().int % 10_000:04d}"


def _cleanup(user_ids, contact_ids, event_ids, appt_ids):
    """Tear down in FK dependency order."""
    db = SessionLocal()
    try:
        if event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE subject_kind = 'event' AND subject_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
        if appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:aids)"),
                {"aids": appt_ids},
            )
        if event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": event_ids},
            )
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": contact_ids},
            )
        if user_ids:
            # Delete queued notifications first: leaving them to the users
            # FK's ON DELETE SET NULL races the live notification worker for
            # the same rows and deadlocks.
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:uids)"
                ),
                {"uids": user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": user_ids},
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _payload(
    *,
    phone: str,
    enrichment: dict | None = None,
    sales_credit_user_id: int | None = None,
) -> dict:
    """A walk-in with the questionnaire filled in, unless overridden."""
    answers = {
        "budget_range": "$2k-$4k",
        "notes": "Wants something before Friday.",
        "current_vehicle": "2014 Nissan Altima, 180k miles",
        "desired_vehicle_type": "truck_work_van",
        "financing_preference": "in_house",
    }
    if enrichment is not None:
        answers = enrichment
    return {
        "contact": {
            "first_name": "Maria",
            "last_name": "Garza",
            "email": None,
            "phone": phone,
        },
        "event": {
            "celebrant_first_name": "Maria",
            "celebrant_last_name": "Garza",
            "event_name": None,
            "event_date": None,
            "owner_user_id": None,
            "walk_in_source": "drive_by",
            "sales_credit_user_id": sales_credit_user_id,
        },
        "enrichment": answers,
        "booking_context": "walk_in",
    }


def _track(ids, body):
    """Record created rows for teardown. Sales and admin responses differ in
    shape; both carry the ids we need."""
    contact_ids, event_ids, appt_ids = ids
    contact = body.get("contact")
    contact_ids.append(
        contact["id"] if isinstance(contact, dict) else body["contact_id"]
    )
    event = body.get("event")
    event_ids.append(event["id"] if isinstance(event, dict) else body["event_id"])
    if body.get("appointment_id") is not None:
        appt_ids.append(body["appointment_id"])
    return body


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_admin_persists_answers(headers, ids):
    resp = client.post(
        "/api/walk-in-leads", json=_payload(phone=_unique_phone()), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.current_vehicle == "2014 Nissan Altima, 180k miles", (
            event.current_vehicle
        )
        assert event.desired_vehicle_type == "truck_work_van", (
            event.desired_vehicle_type
        )
        assert event.financing_preference == "in_house", event.financing_preference
        # The answers must NOT also be smeared into notes — the whole point
        # of the migration is that they stopped being prose.
        assert "Currently driving" not in (event.notes or ""), event.notes
    finally:
        db.close()


def check_sales_persists_answers(headers, ids):
    resp = client.post(
        "/api/sales/walk-ins",
        json=_payload(
            phone=_unique_phone(),
            enrichment={
                "current_vehicle": "2008 Silverado",
                "desired_vehicle_type": "suv",
                "financing_preference": "national_lender",
            },
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event_id"])
        assert event.current_vehicle == "2008 Silverado", event.current_vehicle
        assert event.desired_vehicle_type == "suv", event.desired_vehicle_type
        assert event.financing_preference == "national_lender", (
            event.financing_preference
        )
    finally:
        db.close()


def check_invalid_vehicle_type_rejected(headers):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(
            phone=_unique_phone(),
            enrichment={"desired_vehicle_type": "Truck / work van"},
        ),
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "invalid_desired_vehicle_type", resp.text


def check_invalid_financing_rejected(headers):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(
            phone=_unique_phone(),
            enrichment={"financing_preference": "bank"},
        ),
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "invalid_financing_preference", resp.text


def check_questionnaire_optional(headers, ids):
    """A lead filed with none of the five questions answered still saves."""
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), enrichment={}),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.current_vehicle is None, event.current_vehicle
        assert event.desired_vehicle_type is None, event.desired_vehicle_type
        assert event.financing_preference is None, event.financing_preference
    finally:
        db.close()


def check_blank_answers_store_null(headers, ids):
    """"Not sure yet" is an empty control, and an empty control is NULL —
    never an empty string, which would read as an answer in a report."""
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(
            phone=_unique_phone(),
            enrichment={
                "current_vehicle": "   ",
                "desired_vehicle_type": "",
                "financing_preference": "",
            },
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.current_vehicle is None, repr(event.current_vehicle)
        assert event.desired_vehicle_type is None, repr(event.desired_vehicle_type)
        assert event.financing_preference is None, repr(event.financing_preference)
    finally:
        db.close()


def check_event_detail_serves_answers(headers, ids):
    resp = client.post(
        "/api/walk-in-leads", json=_payload(phone=_unique_phone()), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    detail = client.get(f"/api/events/{body['event']['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    served = detail.json()
    assert served["current_vehicle"] == "2014 Nissan Altima, 180k miles", served
    assert served["desired_vehicle_type"] == "truck_work_van", served
    assert served["financing_preference"] == "in_house", served


def check_timeline_payload_carries_answers(headers, ids):
    resp = client.post(
        "/api/walk-in-leads", json=_payload(phone=_unique_phone()), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())
    event_id = body["event"]["id"]

    db = SessionLocal()
    try:
        row = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == "event.walk_in_created")
            .first()
        )
        assert row is not None, "no event.walk_in_created row"
        payload = row.payload or {}
        assert payload.get("desired_vehicle_type") == "truck_work_van", payload
        assert payload.get("financing_preference") == "in_house", payload
        assert payload.get("current_vehicle") == "2014 Nissan Altima, 180k miles", (
            payload
        )
    finally:
        db.close()


def check_credit_does_not_change_ownership(headers, ids, *, admin_id, rep_id):
    """The regression guard for the whole point of migration 110.

    Crediting a salesperson must leave the lead owned by the admin who filed
    it. If these two ever collapse into one column again, this fails.
    """
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), sales_credit_user_id=rep_id),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.sales_credit_user_id == rep_id, event.sales_credit_user_id
        assert event.owner_user_id == admin_id, (
            f"credit leaked into ownership: owner={event.owner_user_id}, "
            f"expected the filing admin {admin_id}"
        )
        assert event.owner_user_id != event.sales_credit_user_id
    finally:
        db.close()


def check_credit_not_invented(headers, ids, *, admin_id):
    """No credit given → NULL, never a fallback to whoever filed it."""
    resp = client.post(
        "/api/walk-in-leads", json=_payload(phone=_unique_phone()), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.sales_credit_user_id is None, event.sales_credit_user_id
        # Ownership still resolves to the actor — that fallback is correct
        # and must survive; only credit is left empty.
        assert event.owner_user_id == admin_id, event.owner_user_id
    finally:
        db.close()


def check_invalid_credit_rejected(headers):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), sales_credit_user_id=999_999_999),
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "invalid_sales_credit_user_id", resp.text


def check_detail_serves_credit(headers, ids, *, rep_id):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), sales_credit_user_id=rep_id),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    detail = client.get(f"/api/events/{body['event']['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    served = detail.json()
    assert served["sales_credit"] is not None, served
    assert served["sales_credit"]["id"] == rep_id, served["sales_credit"]
    # Owner and credit are served as two separate objects naming two
    # different people.
    assert served["owner"]["id"] != served["sales_credit"]["id"], served


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    appt_ids: list[int] = []
    ids = (contact_ids, event_ids, appt_ids)

    admin_id, admin_email = _seed_user("admin")
    user_ids.append(admin_id)
    admin_headers = _login(admin_email)

    sales_id, sales_email = _seed_user("sales")
    user_ids.append(sales_id)
    sales_headers = _login(sales_email)

    # This smoke is about the intake columns, not the punched-out gate —
    # the gate has its own coverage in test_clock_selfie_and_gate_smoke.py.
    gate_snapshot = snapshot_and_disable_gate()

    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    def run(name, fn, *args, **kwargs):
        nonlocal failed
        try:
            fn(*args, **kwargs)
            checks.append((name, True, None))
        except AssertionError as exc:
            failed += 1
            checks.append((name, False, str(exc)))
        except Exception as exc:
            failed += 1
            checks.append((name, False, f"unexpected: {exc!r}"))

    run("admin_persists_answers", check_admin_persists_answers, admin_headers, ids)
    run("sales_persists_answers", check_sales_persists_answers, sales_headers, ids)
    run(
        "invalid_vehicle_type_rejected",
        check_invalid_vehicle_type_rejected,
        admin_headers,
    )
    run(
        "invalid_financing_rejected",
        check_invalid_financing_rejected,
        admin_headers,
    )
    run("questionnaire_optional", check_questionnaire_optional, admin_headers, ids)
    run("blank_answers_store_null", check_blank_answers_store_null, admin_headers, ids)
    run(
        "event_detail_serves_answers",
        check_event_detail_serves_answers,
        admin_headers,
        ids,
    )
    run(
        "timeline_payload_carries_answers",
        check_timeline_payload_carries_answers,
        admin_headers,
        ids,
    )
    run(
        "credit_does_not_change_ownership",
        check_credit_does_not_change_ownership,
        admin_headers,
        ids,
        admin_id=admin_id,
        rep_id=sales_id,
    )
    run(
        "credit_not_invented",
        check_credit_not_invented,
        admin_headers,
        ids,
        admin_id=admin_id,
    )
    run("invalid_credit_rejected", check_invalid_credit_rejected, admin_headers)
    run(
        "detail_serves_credit",
        check_detail_serves_credit,
        admin_headers,
        ids,
        rep_id=sales_id,
    )

    print()
    for name, ok, err in checks:
        print(f"  ok   {name}" if ok else f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    restore_gate(gate_snapshot)
    _cleanup(user_ids, contact_ids, event_ids, appt_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
