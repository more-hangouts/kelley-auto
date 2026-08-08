"""Smoke tests for staff-created appointments (POST /api/admin/booking/appointments).

The interesting behavior here is which conflicts are hard and which are
advisory, because production's availability rules are the boutique's
inherited public hours (Wed-Sun, 12:00-19:00, 45-minute grid, capacity 1).
A staff booking path that treated those as absolute would 409 every Monday,
every evening, and every second concurrent customer — see the module
docstring in services/staff_appointments.py.

Coverage:

  - Books a future slot on a deal: appointment is 'confirmed', linked to
    the event, stamped source='staff_created' with the given
    booking_context, and does NOT touch the walk-in placeholder that
    already exists on that deal.
  - Outside published hours (a Monday, which has no availability rule at
    all) still books, and reports 'outside_published_hours' as a warning
    rather than failing. This is the regression guard for the feature
    being dead on arrival against real store hours.
  - A past slot is refused with 409.
  - Blackout overlap is refused with 409.
  - Double-booking the same rep is refused with 409; the same slot with a
    different rep is fine.
  - A contact_id that is not the event's own primary contact is refused
    with 422, so an appointment can never be filed under one person and
    hung off another person's deal. The deal's own contact sent
    redundantly still books.
  - An unknown booking_context is refused with 422.

    .venv/bin/python tests/test_staff_appointment_booking_smoke.py
"""

from __future__ import annotations

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
from database.models import Appointment, User  # noqa: E402

client = TestClient(app)

_PASSWORD = "smoke-pass-12345"
_ENDPOINT = "/api/admin/booking/appointments"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_user(role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"staffappt-{role}-{suffix}",
            email=f"staffappt-{role}-{suffix}@example.com",
            hashed_password=hash_password(_PASSWORD),
            full_name=f"Staff Appt {role.title()}",
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
    return f"(210) 555-{uuid.uuid4().int % 10_000:04d}"


def _seed_deal(headers) -> dict:
    """Create a walk-in lead to hang appointments off. Reusing the walk-in
    endpoint keeps this smoke honest about the real row shape (and gives us
    a placeholder appointment to prove we don't disturb)."""
    resp = client.post(
        "/api/walk-in-leads",
        json={
            "contact": {
                "first_name": "Ana",
                "last_name": "Reyes",
                "email": None,
                "phone": _unique_phone(),
            },
            "event": {
                "celebrant_first_name": "Ana",
                "celebrant_last_name": "Reyes",
                "event_name": None,
                "event_date": None,
                "owner_user_id": None,
            },
            "enrichment": {"budget_range": None, "notes": None},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _next_weekday(target_weekday: int, *, hour: int) -> datetime:
    """A UTC datetime on the next occurrence of `target_weekday`
    (Monday=0), at `hour` shop-local. Always in the future."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(os.environ["APP_TIMEZONE"])
    now_local = datetime.now(tz)
    days = (target_weekday - now_local.weekday()) % 7
    if days == 0:
        days = 7
    day = (now_local + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return day.astimezone(timezone.utc)


def _cleanup(user_ids, contact_ids, event_ids, appt_ids, blackout_ids):
    db = SessionLocal()
    try:
        if blackout_ids:
            db.execute(
                sql_text("DELETE FROM appointment_blackouts WHERE id = ANY(:ids)"),
                {"ids": blackout_ids},
            )
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
                sql_text(
                    "DELETE FROM notification_jobs WHERE appointment_id = ANY(:aids)"
                ),
                {"aids": appt_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE subject_kind = 'appointment' AND subject_id = ANY(:aids)"
                ),
                {"aids": appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:aids)"),
                {"aids": appt_ids},
            )
        if event_ids:
            for table in (
                "event_participants",
                "event_status_change_events",
            ):
                db.execute(
                    sql_text(f"DELETE FROM {table} WHERE event_id = ANY(:eids)"),
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
# Checks
# ---------------------------------------------------------------------------


def check_books_future_slot(headers, deal, appt_ids):
    # Saturday 13:00 local — inside the inherited public hours and on the
    # 45-minute grid, so this is the "everything lines up" case.
    slot = _next_weekday(5, hour=13)
    resp = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "booking_context": "existing_customer",
            "internal_notes": "Coming back with spouse",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    appt_ids.append(body["appointment_id"])
    assert body["event_id"] == deal["event"]["id"], body

    db = SessionLocal()
    try:
        appt = db.get(Appointment, body["appointment_id"])
        assert appt.status == "confirmed", appt.status
        assert appt.source == "staff_created", appt.source
        assert appt.booking_context == "existing_customer", appt.booking_context
        assert appt.attended_at is None, "a future booking is not an arrival"
        assert appt.slot_duration_minutes == 45, appt.slot_duration_minutes

        # The walk-in placeholder must be untouched.
        placeholder = db.get(Appointment, deal["appointment_id"])
        assert placeholder.source == "walk_in_placeholder", placeholder.source
        assert placeholder.status == "attended", placeholder.status
    finally:
        db.close()


def check_outside_published_hours_warns_not_fails(headers, deal, appt_ids):
    # Monday has no availability rule at all in production. The public
    # widget would offer nothing; staff must still be able to book.
    slot = _next_weekday(0, hour=10)
    resp = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "booking_context": "phone_call",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    appt_ids.append(body["appointment_id"])
    assert "outside_published_hours" in body["warnings"], body["warnings"]


def check_past_slot_rejected(headers, deal):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    resp = client.post(
        _ENDPOINT,
        json={"event_id": deal["event"]["id"], "slot_start": past.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "slot_conflict", detail
    assert "slot_in_past" in detail["conflicts"], detail


def check_blackout_rejected(headers, deal, blackout_ids):
    slot = _next_weekday(5, hour=15)
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "INSERT INTO appointment_blackouts (start_at, end_at, reason) "
                "VALUES (:s, :e, 'smoke blackout') RETURNING id"
            ),
            {"s": slot - timedelta(hours=1), "e": slot + timedelta(hours=1)},
        ).scalar()
        db.commit()
        blackout_ids.append(row)
    finally:
        db.close()

    resp = client.post(
        _ENDPOINT,
        json={"event_id": deal["event"]["id"], "slot_start": slot.isoformat()},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "slot_in_blackout" in resp.json()["detail"]["conflicts"], resp.text


def check_rep_double_booking(headers, deal, rep_a, rep_b, appt_ids):
    slot = _next_weekday(6, hour=13)
    first = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "assigned_user_id": rep_a,
        },
        headers=headers,
    )
    assert first.status_code == 201, first.text
    appt_ids.append(first.json()["appointment_id"])

    clash = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "assigned_user_id": rep_a,
        },
        headers=headers,
    )
    assert clash.status_code == 409, clash.text
    assert "rep_double_booked" in clash.json()["detail"]["conflicts"], clash.text

    # Same slot, different rep: two reps with customers at once is a normal
    # Saturday, not a conflict.
    other = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "assigned_user_id": rep_b,
        },
        headers=headers,
    )
    assert other.status_code == 201, other.text
    appt_ids.append(other.json()["appointment_id"])


def check_contact_event_mismatch_rejected(headers, deal, other_deal, appt_ids):
    """A contact_id that isn't the event's own is refused, not honored.

    Writing contact_id=B alongside crm_event_id=A would hang B's
    appointment off A's deal, and every surface that reads one field
    without the other would then disagree about whose visit it is.
    """
    slot = _next_weekday(3, hour=13)
    resp = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "contact_id": other_deal["contact"]["id"],
            "slot_start": slot.isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "contact_event_mismatch", resp.text

    # The deal's own contact sent redundantly is fine — it agrees.
    ok = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "contact_id": deal["contact"]["id"],
            "slot_start": slot.isoformat(),
        },
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    appt_ids.append(ok.json()["appointment_id"])

    db = SessionLocal()
    try:
        appt = db.get(Appointment, ok.json()["appointment_id"])
        assert appt.contact_id == deal["contact"]["id"], appt.contact_id
        assert appt.crm_event_id == deal["event"]["id"], appt.crm_event_id
    finally:
        db.close()


def check_invalid_context_rejected(headers, deal):
    slot = _next_weekday(5, hour=14)
    resp = client.post(
        _ENDPOINT,
        json={
            "event_id": deal["event"]["id"],
            "slot_start": slot.isoformat(),
            "booking_context": "carrier_pigeon",
        },
        headers=headers,
    )
    # Rejected by the router's Literal before it reaches the service.
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    appt_ids: list[int] = []
    blackout_ids: list[int] = []

    admin_id, admin_email = _seed_user("admin")
    user_ids.append(admin_id)
    headers = _login(admin_email)

    rep_a, _ = _seed_user("sales")
    rep_b, _ = _seed_user("sales")
    user_ids.extend([rep_a, rep_b])

    deal = _seed_deal(headers)
    contact_ids.append(deal["contact"]["id"])
    event_ids.append(deal["event"]["id"])
    appt_ids.append(deal["appointment_id"])

    # A second, unrelated deal — the "wrong contact" in the mismatch check.
    other_deal = _seed_deal(headers)
    contact_ids.append(other_deal["contact"]["id"])
    event_ids.append(other_deal["event"]["id"])
    appt_ids.append(other_deal["appointment_id"])

    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    def run(name, fn, *args):
        nonlocal failed
        try:
            fn(*args)
            checks.append((name, True, None))
        except AssertionError as exc:
            failed += 1
            checks.append((name, False, str(exc)))
        except Exception as exc:
            failed += 1
            checks.append((name, False, f"unexpected: {exc!r}"))

    run("books_future_slot", check_books_future_slot, headers, deal, appt_ids)
    run(
        "outside_published_hours_warns_not_fails",
        check_outside_published_hours_warns_not_fails,
        headers,
        deal,
        appt_ids,
    )
    run("past_slot_rejected", check_past_slot_rejected, headers, deal)
    run("blackout_rejected", check_blackout_rejected, headers, deal, blackout_ids)
    run(
        "rep_double_booking",
        check_rep_double_booking,
        headers,
        deal,
        rep_a,
        rep_b,
        appt_ids,
    )
    run(
        "contact_event_mismatch_rejected",
        check_contact_event_mismatch_rejected,
        headers,
        deal,
        other_deal,
        appt_ids,
    )
    run("invalid_context_rejected", check_invalid_context_rejected, headers, deal)

    print()
    for name, ok, err in checks:
        print(f"  ok   {name}" if ok else f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _cleanup(user_ids, contact_ids, event_ids, appt_ids, blackout_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
