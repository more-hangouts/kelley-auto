"""Smoke tests for the walk-in lead capture endpoint.

The walk-in flow has multiple side effects in one transaction (contact
+ placeholder appointment + enrichment + event + activity row), so
the smoke verifies each side independently against the same POST.

Coverage:

  - Happy path — POST creates Contact + Appointment ('attended',
    attended_at set) + enrichment row (legacy survey fields) + Event in
    'lead' lane; appointment.crm_event_id links the event; event picks
    up theme/court/budget from enrichment; activity row tagged
    'event.walk_in_created' anchored to the event id.
  - Phone dedupe — re-POST with the same phone but a new event:
    same contact_id, was_new_contact=False, fresh Event + Appointment.
  - Display-name precedence — existing contact name is NOT mutated when
    a second walk-in is filed with different first/last fields.
  - Bad phone — POST with un-normalizable phone → 422 with detail
    'invalid_phone'. Protects the dedupe identity invariant.
  - Auth gate — POST without admin token → 401/403.

Runs serially per project convention (feedback_smokes_run_serially —
several smokes touch shared singletons like confirmation_code state).

    venv/bin/python tests/test_walk_in_lead_smoke.py
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
from database.models import (  # noqa: E402
    ActivityLog,
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    Event,
    User,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"walkin-smoke-{suffix}",
            email=f"walkin-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Walk-In Smoke Admin",
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


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _unique_phone() -> str:
    """Generate a fresh 10-digit phone that normalizes to E.164.

    7-digit subscriber number = `555` + 4 random digits keeps the
    whole thing inside the 555-XXXX block that's not assigned to
    real subscribers; dedupe stays unentangled with real seed data."""
    suffix = uuid.uuid4().int % 10_000
    return f"(210) 555-{suffix:04d}"


def _cleanup(user_ids, contact_ids, event_ids, appt_ids):
    """Tear down in FK dependency order. activity_log depends on event_id;
    appointments on contacts/events; events on contacts."""
    db = SessionLocal()
    try:
        if event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
        if appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_enrichment_responses "
                    "WHERE appointment_id = ANY(:aids)"
                ),
                {"aids": appt_ids},
            )
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


def _walk_in_payload(
    *, phone: str, celebrant_first: str = "Sofia", theme: str = "Garden"
) -> dict:
    return {
        "contact": {
            "first_name": "Maria",
            "last_name": "Garcia",
            "email": None,
            "phone": phone,
        },
        "event": {
            "celebrant_first_name": celebrant_first,
            "celebrant_last_name": "Garcia",
            "event_name": None,
            "event_date": None,
            "owner_user_id": None,
        },
        "enrichment": {
            "party_size_bucket": "3_4",
            "court_size": 14,
            "quince_theme": theme,
            "quince_theme_colors": ["sage", "blush"],
            "budget_range": "$2k-$4k",
            "dress_styles": ["ballgown"],
            "colors": ["sage", "blush"],
            "notes": "Walked in around 3pm, has a 6-month-out date.",
        },
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_happy_path(headers, ids):
    """Single POST writes the full lead shape."""
    phone = _unique_phone()
    payload = _walk_in_payload(phone=phone)
    resp = client.post("/api/walk-in-leads", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Response shape
    assert body["was_new_contact"] is True
    assert body["contact"]["display_name"] == "Maria Garcia"
    assert body["contact"]["phone_e164"] is not None
    assert body["event"]["status"] == "lead"
    assert body["event"]["event_name"], body["event"]
    assert isinstance(body["appointment_id"], int)

    contact_id = body["contact"]["id"]
    event_id = body["event"]["id"]
    appt_id = body["appointment_id"]
    ids["contact_ids"].append(contact_id)
    ids["event_ids"].append(event_id)
    ids["appt_ids"].append(appt_id)

    db = SessionLocal()
    try:
        appt = db.get(Appointment, appt_id)
        event = db.get(Event, event_id)
        # Appointment placeholder is 'settled' — attended_at set, status
        # 'attended', and linked back to the event so the kanban card
        # last-appointment timestamp resolves.
        assert appt is not None
        assert appt.status == "attended", appt.status
        assert appt.attended_at is not None
        assert appt.crm_event_id == event_id, (appt.crm_event_id, event_id)
        assert appt.contact_id == contact_id
        assert appt.party_size_bucket == "3_4"
        assert (appt.raw_payload or {}).get("source") == "walk_in", appt.raw_payload

        # Event carries enrichment fields pulled by promote_appointment_to_event.
        assert event is not None
        assert event.status == "lead"
        assert event.primary_contact_id == contact_id
        assert event.court_size == 14, event.court_size
        assert event.quince_theme == "Garden", event.quince_theme
        assert event.budget_range == "$2k-$4k", event.budget_range

        # Enrichment row exists with the legacy survey fields the
        # widget would have stored.
        enrich = (
            db.query(AppointmentEnrichmentResponse)
            .filter(AppointmentEnrichmentResponse.appointment_id == appt_id)
            .first()
        )
        assert enrich is not None, "enrichment row missing"
        assert enrich.quince_theme == "Garden"
        assert enrich.court_size == 14
        assert enrich.budget_range == "$2k-$4k"
        assert enrich.dress_styles == ["ballgown"]
        # source uses the existing 'manual_attach' enum value; the
        # walk-in origin is preserved in raw_payload to avoid an
        # enum-extending migration just for this feature.
        assert enrich.source == "manual_attach", enrich.source
        assert (enrich.raw_payload or {}).get("source") == "walk_in"
        assert enrich.submitted_at is not None

        # Activity log row anchored to the new event id.
        activity = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == "event.walk_in_created")
            .first()
        )
        assert activity is not None, "event.walk_in_created activity missing"
        assert activity.actor_kind == "staff"
        assert activity.subject_kind == "event"
        assert activity.subject_id == event_id
        payload_dict = dict(activity.payload or {})
        assert payload_dict.get("appointment_id") == appt_id
        assert payload_dict.get("contact_id") == contact_id
        assert payload_dict.get("was_new_contact") is True
    finally:
        db.close()


def check_phone_dedupes_contact(headers, ids):
    """Re-POST with the same phone returns the same contact but a fresh
    event/appointment."""
    phone = _unique_phone()

    first = client.post(
        "/api/walk-in-leads",
        json=_walk_in_payload(phone=phone, celebrant_first="Ana", theme="Forest"),
        headers=headers,
    )
    assert first.status_code == 201, first.text
    first_body = first.json()
    ids["contact_ids"].append(first_body["contact"]["id"])
    ids["event_ids"].append(first_body["event"]["id"])
    ids["appt_ids"].append(first_body["appointment_id"])

    second = client.post(
        "/api/walk-in-leads",
        json=_walk_in_payload(phone=phone, celebrant_first="Lucia", theme="Sunset"),
        headers=headers,
    )
    assert second.status_code == 201, second.text
    second_body = second.json()
    ids["event_ids"].append(second_body["event"]["id"])
    ids["appt_ids"].append(second_body["appointment_id"])

    # Same contact, new event/appointment.
    assert second_body["contact"]["id"] == first_body["contact"]["id"], (
        second_body["contact"]["id"], first_body["contact"]["id"]
    )
    assert second_body["was_new_contact"] is False
    assert second_body["event"]["id"] != first_body["event"]["id"]
    assert second_body["appointment_id"] != first_body["appointment_id"]


def check_existing_contact_name_not_mutated(headers, ids):
    """Filing a second walk-in for an existing contact must not rewrite
    that contact's display_name even if the form has different first/last.
    The user's identity is owned by Contacts, not by per-event forms."""
    phone = _unique_phone()
    first = client.post(
        "/api/walk-in-leads",
        json={
            **_walk_in_payload(phone=phone),
            "contact": {
                "first_name": "Original",
                "last_name": "Name",
                "email": None,
                "phone": phone,
            },
        },
        headers=headers,
    )
    assert first.status_code == 201, first.text
    contact_id = first.json()["contact"]["id"]
    ids["contact_ids"].append(contact_id)
    ids["event_ids"].append(first.json()["event"]["id"])
    ids["appt_ids"].append(first.json()["appointment_id"])
    original_display = first.json()["contact"]["display_name"]
    assert original_display == "Original Name"

    # Second POST with different name fields on the same phone.
    second = client.post(
        "/api/walk-in-leads",
        json={
            **_walk_in_payload(phone=phone, celebrant_first="Other"),
            "contact": {
                "first_name": "Different",
                "last_name": "Person",
                "display_name": "Different Person",
                "email": None,
                "phone": phone,
            },
        },
        headers=headers,
    )
    assert second.status_code == 201, second.text
    ids["event_ids"].append(second.json()["event"]["id"])
    ids["appt_ids"].append(second.json()["appointment_id"])

    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        assert contact is not None
        assert contact.display_name == original_display, contact.display_name
    finally:
        db.close()


def check_invalid_phone_returns_422(headers):
    """Un-normalizable phone is rejected so the same person never lands
    on two contacts."""
    payload = _walk_in_payload(phone="not a phone")
    resp = client.post("/api/walk-in-leads", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("detail") == "invalid_phone", body


def check_missing_name_returns_422(headers):
    """At least one of display_name or first/last is required so a
    contact never lands with display_name='Unknown'."""
    payload = _walk_in_payload(phone=_unique_phone())
    payload["contact"]["first_name"] = None
    payload["contact"]["last_name"] = None
    payload["contact"]["display_name"] = None
    resp = client.post("/api/walk-in-leads", json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json().get("detail") == "contact_name_required"


def check_auth_gate():
    """Unauthenticated POST is blocked."""
    payload = _walk_in_payload(phone=_unique_phone())
    resp = client.post("/api/walk-in-leads", json=payload)
    assert resp.status_code in (401, 403), resp.status_code


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    appt_ids: list[int] = []
    ids = {
        "contact_ids": contact_ids,
        "event_ids": event_ids,
        "appt_ids": appt_ids,
    }

    user_id, email = _seed_admin()
    user_ids.append(user_id)
    headers = _login(email)

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

    run("happy_path_writes_full_lead_shape", check_happy_path, headers, ids)
    run("phone_dedupes_contact", check_phone_dedupes_contact, headers, ids)
    run(
        "existing_contact_name_not_mutated",
        check_existing_contact_name_not_mutated,
        headers,
        ids,
    )
    run("invalid_phone_returns_422", check_invalid_phone_returns_422, headers)
    run("missing_name_returns_422", check_missing_name_returns_422, headers)
    run("auth_gate_blocks_unauthenticated", check_auth_gate)

    print()
    for name, ok, err in checks:
        if ok:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _cleanup(user_ids, contact_ids, event_ids, appt_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
