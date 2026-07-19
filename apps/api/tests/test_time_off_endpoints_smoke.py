"""Smoke tests for Phase 8 Slice C (endpoints).

Maps to the 6 enforcement points the user listed when greenlighting
Slice C:

  1. POST /api/sales/time-off/{id}/cancel (not DELETE).
  2. Terminal time-off requests return 409 on approve/deny/cancel/
     re-decide.
  3. Sales users can only read/cancel their own time-off requests.
  4. Admin endpoints remain date-bounded.
  5. Every request creation/decision writes a `time_off_decision_events`
     row.
  6. Shift overlap endpoint is read-only/visualization, not enforcement.

Plus the full lifecycle: stylist submits → owner amends → owner
approves; schedule read suppresses approved time-off; shift CRUD;
holiday CRUD with the NULLS-NOT-DISTINCT 409.
"""

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    StaffHoliday,
    StaffLocation,
    StaffShift,
    StaffShiftOverride,
    TimeOffDecisionEvent,
    TimeOffRequest,
    User,
)

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []
_shift_ids: list[int] = []
_override_ids: list[int] = []
_holiday_ids: list[int] = []
_tor_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p8c-{suffix}",
            email=f"{role}-p8c-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P8C {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _seed_location() -> int:
    db = SessionLocal()
    try:
        loc = StaffLocation(
            name=f"P8C Probe {uuid.uuid4().hex[:6]}",
            latitude=29.4252,
            longitude=-98.4946,
            radius_m=100,
            active=True,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM time_off_decision_events "
                    "WHERE request_id IN (SELECT id FROM time_off_requests "
                    "WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_requests WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_overrides "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shifts WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
        if _holiday_ids:
            db.execute(
                sql_text("DELETE FROM staff_holidays WHERE id = ANY(:ids)"),
                {"ids": _holiday_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)

    sales_a_id = _make_user(role="sales")
    sales_b_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_a = {"Authorization": f"Bearer {_token(sales_a_id, sales=True)}"}
    sales_b = {"Authorization": f"Bearer {_token(sales_b_id, sales=True)}"}
    admin = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    location_id = _seed_location()

    # ============================================================
    # SCOPE GATING (sales/admin tokens routed correctly)
    # ============================================================
    print("===== scope gating =====")
    # Sales token cannot hit admin shifts.
    resp = client.get("/api/admin/shifts", headers=sales_a)
    assert resp.status_code == 403, resp.text
    # Admin token cannot hit sales schedule (route requires sales scope).
    resp = client.get(
        "/api/sales/schedule",
        headers=admin,
        params={
            "from_date": "2026-06-01",
            "to_date": "2026-06-07",
        },
    )
    assert resp.status_code == 403, resp.text

    # ============================================================
    # ADMIN SHIFT CRUD
    # ============================================================
    print("===== shift CRUD =====")
    monday = date(2026, 6, 1)
    saturday = monday + timedelta(days=5)

    create_resp = client.post(
        "/api/admin/shifts",
        headers=admin,
        json={
            "user_id": sales_a_id,
            "location_id": location_id,
            "starts_at": datetime(2026, 6, 1, 9, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 6, 1, 17, 0, tzinfo=tz).isoformat(),
            "working_days": [1, 2, 3, 4, 5],
            "late_grace_period_minutes": 10,
            "earliest_check_in_minutes": 60,
            "auto_session_close_time": "22:00:00",
            "max_session_hours": 12.0,
            "notes": "Mon-Fri 9-5",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    shift_a_id = create_resp.json()["id"]
    _shift_ids.append(shift_a_id)

    # Bad payload: ends_at <= starts_at → 422.
    bad_resp = client.post(
        "/api/admin/shifts",
        headers=admin,
        json={
            "user_id": sales_a_id,
            "starts_at": datetime(2026, 6, 1, 17, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 6, 1, 9, 0, tzinfo=tz).isoformat(),
            "working_days": [1],
        },
    )
    assert bad_resp.status_code in (400, 422), bad_resp.text

    # PATCH a field.
    patch_resp = client.patch(
        f"/api/admin/shifts/{shift_a_id}",
        headers=admin,
        json={"late_grace_period_minutes": 15},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["late_grace_period_minutes"] == 15

    # Sales token rejected on PATCH.
    resp = client.patch(
        f"/api/admin/shifts/{shift_a_id}",
        headers=sales_a,
        json={"late_grace_period_minutes": 5},
    )
    assert resp.status_code == 403, resp.text

    # List shifts (filtered).
    list_resp = client.get(
        "/api/admin/shifts",
        headers=admin,
        params={"user_id": sales_a_id},
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()["shifts"]) == 1

    # ============================================================
    # SHIFT OVERLAP VISUALIZER (read-only — enforcement #6)
    # ============================================================
    print("===== overlap visualizer =====")
    # Seed a SECOND shift that overlaps the first on Monday: 11-15.
    overlap_create = client.post(
        "/api/admin/shifts",
        headers=admin,
        json={
            "user_id": sales_a_id,
            "starts_at": datetime(2026, 6, 1, 11, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 6, 1, 15, 0, tzinfo=tz).isoformat(),
            "working_days": [1],  # Monday only
        },
    )
    # Critical: the overlap MUST NOT block the create. Per the user's
    # enforcement #6 the overlap surface is a read, never a gate.
    assert overlap_create.status_code == 201, overlap_create.text
    overlap_shift_id = overlap_create.json()["id"]
    _shift_ids.append(overlap_shift_id)

    # The visualizer reports the overlap.
    overlaps_resp = client.get(
        "/api/admin/shifts/overlaps",
        headers=admin,
        params={
            "user_id": sales_a_id,
            "from_date": monday.isoformat(),
            "to_date": (monday + timedelta(days=6)).isoformat(),
        },
    )
    assert overlaps_resp.status_code == 200
    overlaps_body = overlaps_resp.json()
    assert len(overlaps_body["overlaps"]) >= 1, overlaps_body
    # Overlap is on Monday.
    monday_overlaps = [
        o for o in overlaps_body["overlaps"]
        if o["business_date"] == monday.isoformat()
    ]
    assert len(monday_overlaps) == 1

    # Bounded: missing from_date → 422.
    resp = client.get(
        "/api/admin/shifts/overlaps",
        headers=admin,
        params={"user_id": sales_a_id},
    )
    assert resp.status_code == 422, resp.text

    # ============================================================
    # OVERRIDE CRUD
    # ============================================================
    print("===== override CRUD =====")
    ov_resp = client.post(
        "/api/admin/shift-overrides",
        headers=admin,
        json={
            "user_id": sales_a_id,
            "shift_id": shift_a_id,
            "starts_on": saturday.isoformat(),
            "ends_on": saturday.isoformat(),
            "reason": "Maria covering Saturday",
        },
    )
    assert ov_resp.status_code == 201, ov_resp.text
    ov_id = ov_resp.json()["id"]
    _override_ids.append(ov_id)

    # Bad date range.
    bad_ov = client.post(
        "/api/admin/shift-overrides",
        headers=admin,
        json={
            "user_id": sales_a_id,
            "shift_id": shift_a_id,
            "starts_on": "2026-06-10",
            "ends_on": "2026-06-08",
        },
    )
    assert bad_ov.status_code in (400, 422), bad_ov.text

    # ============================================================
    # HOLIDAY CRUD + NULLS NOT DISTINCT 409
    # ============================================================
    print("===== holiday CRUD =====")
    h_resp = client.post(
        "/api/admin/holidays",
        headers=admin,
        json={
            "name": "P8C Memorial Day",
            "holiday_date": "2026-05-25",
            "is_paid": True,
            "multiplier": 1.5,
        },
    )
    assert h_resp.status_code == 201, h_resp.text
    h1_id = h_resp.json()["id"]
    _holiday_ids.append(h1_id)

    # Duplicate global holiday → 409 holiday_already_exists.
    dup_resp = client.post(
        "/api/admin/holidays",
        headers=admin,
        json={
            "name": "P8C Memorial Day",
            "holiday_date": "2026-05-25",
        },
    )
    assert dup_resp.status_code == 409, dup_resp.text
    assert dup_resp.json()["detail"]["code"] == "holiday_already_exists"

    # Same date+name with a real location_id → allowed.
    loc_h_resp = client.post(
        "/api/admin/holidays",
        headers=admin,
        json={
            "name": "P8C Memorial Day",
            "holiday_date": "2026-05-25",
            "location_id": location_id,
        },
    )
    assert loc_h_resp.status_code == 201, loc_h_resp.text
    _holiday_ids.append(loc_h_resp.json()["id"])

    # PATCH a holiday.
    patch_h = client.patch(
        f"/api/admin/holidays/{h1_id}",
        headers=admin,
        json={"is_paid": False, "notes": "downgraded to unpaid"},
    )
    assert patch_h.status_code == 200, patch_h.text
    assert patch_h.json()["is_paid"] is False

    # ============================================================
    # SALES TIME-OFF: SUBMIT
    # ============================================================
    print("===== time-off submit =====")
    submit_resp = client.post(
        "/api/sales/time-off",
        headers=sales_a,
        json={
            "starts_at": datetime(2026, 7, 1, 0, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 7, 4, 0, 0, tzinfo=tz).isoformat(),
            "reason": "family wedding",
        },
    )
    assert submit_resp.status_code == 200, submit_resp.text
    tor_a_id = submit_resp.json()["id"]
    _tor_ids.append(tor_a_id)
    assert submit_resp.json()["status"] == "pending"

    # Bad range.
    bad_submit = client.post(
        "/api/sales/time-off",
        headers=sales_a,
        json={
            "starts_at": datetime(2026, 7, 5, 0, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 7, 1, 0, 0, tzinfo=tz).isoformat(),
        },
    )
    assert bad_submit.status_code in (400, 422), bad_submit.text

    # Audit row exists for the submission (enforcement #5).
    db = SessionLocal()
    try:
        events = (
            db.execute(
                select(TimeOffDecisionEvent).where(
                    TimeOffDecisionEvent.request_id == tor_a_id
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1, events
        assert events[0].action == "requested"
        assert events[0].actor_kind == "staff"
    finally:
        db.close()

    # ============================================================
    # SALES TIME-OFF: SCOPE GATING (enforcement #3)
    # ============================================================
    print("===== time-off scope =====")
    # Sales user B cannot see A's request via the list endpoint.
    list_b = client.get("/api/sales/time-off", headers=sales_b)
    assert list_b.status_code == 200
    assert all(
        r["user_id"] == sales_b_id for r in list_b.json()["requests"]
    )

    # Sales user B cannot cancel A's request.
    cancel_b = client.post(
        f"/api/sales/time-off/{tor_a_id}/cancel", headers=sales_b
    )
    assert cancel_b.status_code == 403, cancel_b.text
    assert cancel_b.json()["detail"]["code"] == "time_off_request_not_yours"

    # ============================================================
    # CANCEL USES POST, NOT DELETE (enforcement #1)
    # ============================================================
    print("===== POST cancel =====")
    # DELETE on the resource is not registered → 404 or 405.
    delete_resp = client.delete(
        f"/api/sales/time-off/{tor_a_id}", headers=sales_a
    )
    assert delete_resp.status_code in (404, 405), delete_resp.text

    # ============================================================
    # OWNER AMEND PATH (enforcement #5: writes audit row)
    # ============================================================
    print("===== amend =====")
    amend_resp = client.post(
        f"/api/admin/time-off/{tor_a_id}/amend",
        headers=admin,
        json={
            "ends_at": datetime(2026, 7, 3, 0, 0, tzinfo=tz).isoformat(),
            "decision_notes": "trim by one day",
        },
    )
    assert amend_resp.status_code == 200, amend_resp.text
    # Status stays pending after amend.
    assert amend_resp.json()["status"] == "pending"

    # 'amended' audit row exists.
    db = SessionLocal()
    try:
        events = (
            db.execute(
                select(TimeOffDecisionEvent)
                .where(TimeOffDecisionEvent.request_id == tor_a_id)
                .order_by(TimeOffDecisionEvent.created_at)
            )
            .scalars()
            .all()
        )
        actions = [e.action for e in events]
        assert actions == ["requested", "amended"], actions
        assert events[1].actor_kind == "owner"
        assert events[1].old_values.get("ends_at") is not None
    finally:
        db.close()

    # Amend with no proposed times → 422.
    no_times = client.post(
        f"/api/admin/time-off/{tor_a_id}/amend",
        headers=admin,
        json={"decision_notes": "nothing to change"},
    )
    assert no_times.status_code == 422, no_times.text

    # ============================================================
    # OWNER DECIDE: APPROVE
    # ============================================================
    print("===== decide approve =====")
    decide_resp = client.post(
        f"/api/admin/time-off/{tor_a_id}/decide",
        headers=admin,
        json={"status": "approved", "decision_notes": "ok"},
    )
    assert decide_resp.status_code == 200, decide_resp.text
    assert decide_resp.json()["status"] == "approved"
    assert decide_resp.json()["decided_by_user_id"] == admin_id

    # 'approved' audit row exists.
    db = SessionLocal()
    try:
        events = (
            db.execute(
                select(TimeOffDecisionEvent)
                .where(TimeOffDecisionEvent.request_id == tor_a_id)
                .order_by(TimeOffDecisionEvent.created_at)
            )
            .scalars()
            .all()
        )
        actions = [e.action for e in events]
        assert "approved" in actions
    finally:
        db.close()

    # ============================================================
    # TERMINAL STATUS RETURNS 409 (enforcement #2)
    # ============================================================
    print("===== terminal 409 =====")
    # Re-decide → 409.
    redecide = client.post(
        f"/api/admin/time-off/{tor_a_id}/decide",
        headers=admin,
        json={"status": "denied"},
    )
    assert redecide.status_code == 409, redecide.text

    # Amend after approval → 409.
    amend_after = client.post(
        f"/api/admin/time-off/{tor_a_id}/amend",
        headers=admin,
        json={
            "ends_at": datetime(2026, 7, 3, 0, 0, tzinfo=tz).isoformat()
        },
    )
    assert amend_after.status_code == 409, amend_after.text

    # Stylist cancel after approval → 409.
    cancel_after = client.post(
        f"/api/sales/time-off/{tor_a_id}/cancel", headers=sales_a
    )
    assert cancel_after.status_code == 409, cancel_after.text

    # ============================================================
    # CANCEL HAPPY PATH on a DIFFERENT pending request
    # ============================================================
    print("===== cancel happy =====")
    pending_resp = client.post(
        "/api/sales/time-off",
        headers=sales_a,
        json={
            "starts_at": datetime(2026, 8, 1, 0, 0, tzinfo=tz).isoformat(),
            "ends_at": datetime(2026, 8, 2, 0, 0, tzinfo=tz).isoformat(),
            "reason": "personal day",
        },
    )
    assert pending_resp.status_code == 200, pending_resp.text
    pending_id = pending_resp.json()["id"]
    _tor_ids.append(pending_id)

    cancel_resp = client.post(
        f"/api/sales/time-off/{pending_id}/cancel", headers=sales_a
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "cancelled"

    # Idempotent cancel.
    cancel_again = client.post(
        f"/api/sales/time-off/{pending_id}/cancel", headers=sales_a
    )
    assert cancel_again.status_code == 200, cancel_again.text
    assert cancel_again.json()["status"] == "cancelled"

    # ============================================================
    # ADMIN LIST IS DATE-BOUNDED (enforcement #4)
    # ============================================================
    print("===== admin date-bounded =====")
    # Missing from_date → 422.
    miss = client.get(
        "/api/admin/time-off",
        headers=admin,
        params={"to_date": "2026-12-31"},
    )
    assert miss.status_code == 422, miss.text

    # Bad range (to < from) → 422.
    bad_range = client.get(
        "/api/admin/time-off",
        headers=admin,
        params={"from_date": "2026-12-31", "to_date": "2026-01-01"},
    )
    assert bad_range.status_code == 422, bad_range.text

    # Valid: range covering July returns A's approved request.
    july_list = client.get(
        "/api/admin/time-off",
        headers=admin,
        params={"from_date": "2026-07-01", "to_date": "2026-07-31"},
    )
    assert july_list.status_code == 200, july_list.text
    july_ids = {r["id"] for r in july_list.json()["requests"]}
    assert tor_a_id in july_ids

    # Range outside July → request not present.
    sept_list = client.get(
        "/api/admin/time-off",
        headers=admin,
        params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
    )
    assert sept_list.status_code == 200
    sept_ids = {r["id"] for r in sept_list.json()["requests"]}
    assert tor_a_id not in sept_ids

    # ============================================================
    # SALES SCHEDULE READ + TIME-OFF SUPPRESSION
    # ============================================================
    print("===== schedule =====")
    # Stylist's schedule for the week of July 1 (which spans the
    # approved time-off Jul 1-3).
    sched_resp = client.get(
        "/api/sales/schedule",
        headers=sales_a,
        params={"from_date": "2026-06-29", "to_date": "2026-07-05"},
    )
    assert sched_resp.status_code == 200, sched_resp.text
    days = {d["business_date"]: d for d in sched_resp.json()["days"]}
    # Jul 1, 2 are inside the approved window → suppressed.
    assert days["2026-07-01"]["time_off_suppressed"] is True
    assert days["2026-07-01"]["shift"] is None
    # Jul 3 is also inside the window (ends_at became Jul 3 00:00 after
    # amend? No — service stores ends_at as Jul 3 00:00 local. The
    # suppression interval treats ends_at midnight as "off through
    # Jul 2." So Jul 3 is back on schedule.)
    # Mon Jun 29 is on schedule (working_days includes Mon).
    assert days["2026-06-29"]["time_off_suppressed"] is False
    assert days["2026-06-29"]["shift"] is not None

    # Date range too wide → 422.
    wide = client.get(
        "/api/sales/schedule",
        headers=sales_a,
        params={"from_date": "2026-01-01", "to_date": "2026-12-31"},
    )
    assert wide.status_code == 422, wide.text

    # ============================================================
    # SHIFT DELETE
    # ============================================================
    print("===== shift delete =====")
    # Delete the overlap shift; the FK on staff_punches is SET NULL
    # so this is safe even with historical punches (none here, just
    # exercising the path).
    delete_shift = client.delete(
        f"/api/admin/shifts/{overlap_shift_id}", headers=admin
    )
    assert delete_shift.status_code == 204
    _shift_ids.remove(overlap_shift_id)

    print("phase8_endpoints smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
