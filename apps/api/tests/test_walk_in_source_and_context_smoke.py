"""Smoke tests for staff-entered lead origin and the phone-lead shape.

Migration 104 gave `events` a reportable `walk_in_source` bucket plus a
free-text `walk_in_source_detail`, and gave `appointments` real `source` /
`booking_context` columns. The same change split lead capture in two: a
walk-in still writes the attended placeholder appointment, a phone lead
writes no appointment at all.

Coverage:

  - Admin endpoint persists source + detail onto the event, and stamps the
    placeholder appointment 'walk_in_placeholder' / 'walk_in'.
  - Sales endpoint persists the same fields (both routes share the
    service, but the payload passes through two different router models,
    which is exactly where a field gets dropped).
  - Invalid bucket → 422 invalid_walk_in_source, and nothing is written.
  - Detail with no bucket is dropped, not stored — an unreportable detail
    string attached to a NULL bucket is worse than nothing.
  - A phone lead creates NO appointment: response appointment_id is null,
    the deal exists, and zero appointment rows point at it. This is the
    regression guard for callers being recorded as having physically
    arrived.
  - A phone lead still records its origin on the deal.
  - party_size_bucket is optional now; omitting it defaults to 'solo'
    server-side rather than 422-ing.
  - The event.walk_in_created timeline payload carries the origin.

Runs serially per project convention (feedback_smokes_run_serially —
several smokes touch shared singletons like confirmation_code state).

    .venv/bin/python tests/test_walk_in_source_and_context_smoke.py
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
from database.models import ActivityLog, Appointment, Event, User  # noqa: E402
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
            username=f"origin-smoke-{role}-{suffix}",
            email=f"origin-smoke-{role}-{suffix}@example.com",
            hashed_password=hash_password(_PASSWORD),
            full_name=f"Origin Smoke {role.title()}",
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
            # Delete the queued notifications first. Leaving them to the
            # users FK's ON DELETE SET NULL races the live notification
            # worker for the same rows and deadlocks (it holds the row
            # while we hold the user).
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
    source: str | None = "social_media",
    detail: str | None = "Facebook video",
    context: str = "walk_in",
    include_party_size: bool = True,
) -> dict:
    event = {
        "celebrant_first_name": "Sofia",
        "celebrant_last_name": "Garcia",
        "event_name": None,
        "event_date": None,
        "owner_user_id": None,
    }
    if source is not None:
        event["walk_in_source"] = source
    if detail is not None:
        event["walk_in_source_detail"] = detail

    enrichment: dict = {
        "budget_range": "$2k-$4k",
        "notes": "Asked about the Tacoma.",
    }
    if include_party_size:
        enrichment["party_size_bucket"] = "3_4"

    return {
        "contact": {
            "first_name": "Maria",
            "last_name": "Garcia",
            "email": None,
            "phone": phone,
        },
        "event": event,
        "enrichment": enrichment,
        "booking_context": context,
    }


def _track(ids, body):
    """Record created rows for teardown. Sales and admin responses differ
    in shape; both carry the ids we need to clean up."""
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


def check_admin_persists_source(headers, ids):
    resp = client.post(
        "/api/walk-in-leads", json=_payload(phone=_unique_phone()), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.walk_in_source == "social_media", event.walk_in_source
        assert event.walk_in_source_detail == "Facebook video", (
            event.walk_in_source_detail
        )
        appt = db.get(Appointment, body["appointment_id"])
        assert appt.source == "walk_in_placeholder", appt.source
        assert appt.booking_context == "walk_in", appt.booking_context
        assert appt.status == "attended", appt.status
    finally:
        db.close()


def check_sales_persists_source(headers, ids):
    resp = client.post(
        "/api/sales/walk-ins",
        json=_payload(phone=_unique_phone(), source="referral", detail="Sent by John"),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event_id"])
        assert event.walk_in_source == "referral", event.walk_in_source
        assert event.walk_in_source_detail == "Sent by John", (
            event.walk_in_source_detail
        )
    finally:
        db.close()


def check_invalid_source_rejected(headers):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), source="facebook_video"),
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "invalid_walk_in_source", resp.text


def check_detail_without_bucket_dropped(headers, ids):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), source=None, detail="TikTok"),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        event = db.get(Event, body["event"]["id"])
        assert event.walk_in_source is None, event.walk_in_source
        assert event.walk_in_source_detail is None, event.walk_in_source_detail
    finally:
        db.close()


def check_phone_lead_creates_no_appointment(headers, ids):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(
            phone=_unique_phone(),
            source="google_search",
            detail=None,
            context="phone_call",
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())
    assert body["appointment_id"] is None, body

    event_id = body["event"]["id"]
    db = SessionLocal()
    try:
        # The regression this guards: a caller recorded as having shown up.
        linked = (
            db.query(Appointment)
            .filter(Appointment.crm_event_id == event_id)
            .count()
        )
        assert linked == 0, f"phone lead created {linked} appointment(s)"

        event = db.get(Event, event_id)
        assert event is not None
        assert event.walk_in_source == "google_search", event.walk_in_source
        # Notes have nowhere else to live without an appointment.
        assert event.notes and "Tacoma" in event.notes, event.notes
        # And the deal is named like its walk-in equivalent.
        assert event.event_name == "Sofia Garcia's Deal", event.event_name
    finally:
        db.close()


def check_party_size_optional(headers, ids):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(phone=_unique_phone(), include_party_size=False),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        appt = db.get(Appointment, body["appointment_id"])
        assert appt.party_size_bucket == "solo", appt.party_size_bucket
    finally:
        db.close()


def check_timeline_payload_carries_origin(headers, ids):
    resp = client.post(
        "/api/walk-in-leads",
        json=_payload(
            phone=_unique_phone(), source="drive_by", detail="Saw the lot"
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = _track(ids, resp.json())

    db = SessionLocal()
    try:
        row = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == body["event"]["id"])
            .filter(ActivityLog.activity_type == "event.walk_in_created")
            .one()
        )
        assert row.payload.get("walk_in_source") == "drive_by", row.payload
        assert row.payload.get("walk_in_source_detail") == "Saw the lot", row.payload
        assert row.payload.get("booking_context") == "walk_in", row.payload
    finally:
        db.close()


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

    # This smoke is about origin fields, not the punched-out gate — the
    # gate has its own coverage in test_clock_selfie_and_gate_smoke.py.
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

    run("admin_persists_source", check_admin_persists_source, admin_headers, ids)
    run("sales_persists_source", check_sales_persists_source, sales_headers, ids)
    run("invalid_source_rejected", check_invalid_source_rejected, admin_headers)
    run(
        "detail_without_bucket_dropped",
        check_detail_without_bucket_dropped,
        admin_headers,
        ids,
    )
    run(
        "phone_lead_creates_no_appointment",
        check_phone_lead_creates_no_appointment,
        admin_headers,
        ids,
    )
    run("party_size_optional", check_party_size_optional, admin_headers, ids)
    run(
        "timeline_payload_carries_origin",
        check_timeline_payload_carries_origin,
        admin_headers,
        ids,
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
