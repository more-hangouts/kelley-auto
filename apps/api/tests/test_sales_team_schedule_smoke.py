"""Smoke for Phase 10 Slice 5: sales-scoped team schedule.

Covers GET /api/sales/schedule/team:

  1. Auth: sales token OK; admin token OK (admin scope is a superset);
     unauthenticated 401.
  2. Privacy contract: only published rows appear; the response keys
     are exactly the allowed set (no manager_notes,
     attendance_status, actual_clock_*, late_grace_minutes, etc.).
     A draft entry seeded alongside published ones is excluded.
     manager_notes content explicitly does NOT show up anywhere in
     the serialized response.
  3. Multi-user: shifts for multiple coworkers come back, sorted by
     business_date then starts_at_local then full_name.
  4. Date range validation: missing dates → 422; reversed → 422;
     wider than MAX_RANGE_DAYS (31) → 422.
  5. viewer_user_id is the requesting sales user's id (so the
     frontend can mark "You" without a second lookup).
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_entry_ids: list[int] = []

ALLOWED_KEYS = {
    "entry_id",
    "user_id",
    "username",
    "full_name",
    "business_date",
    "starts_at_local",
    "ends_at_local",
}

FORBIDDEN_KEYS = {
    "manager_notes",
    "attendance_status",
    "actual_clock_in_punch_id",
    "actual_clock_out_punch_id",
    "late_grace_minutes",
    "status",  # 'draft'/'published' is internal — don't leak it
    "published_at",
    "published_by_user_id",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "source",
    "source_shift_id",
}


def _make_user(*, role: str, active: bool = True) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p10s5-{suffix}",
            email=f"{role}-p10s5-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P10S5 {role.title()} {suffix}",
            is_active=active,
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


def _publish(
    actor_user_id: int,
    user_id: int,
    business_date_: date,
    starts: datetime,
    ends: datetime,
    *,
    manager_notes: str | None = None,
) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_user_id,
            user_id=user_id,
            business_date_=business_date_,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
            manager_notes=manager_notes,
        )
        db.commit()
        _entry_ids.append(d["id"])
        return d["id"]
    finally:
        db.close()


def _draft(
    actor_user_id: int,
    user_id: int,
    business_date_: date,
    starts: datetime,
    ends: datetime,
) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_user_id,
            user_id=user_id,
            business_date_=business_date_,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=False,
        )
        db.commit()
        _entry_ids.append(d["id"])
        return d["id"]
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    # Slice-5-fix coverage: a *second* admin who has a published
    # schedule entry. The team query must filter by role='sales' so
    # this admin's entry never leaks into a coworker's view.
    admin_with_shift_id = _make_user(role="admin")
    sales_a_id = _make_user(role="sales")
    sales_b_id = _make_user(role="sales")
    sales_c_id = _make_user(role="sales", active=False)  # inactive

    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_a_hdr = {"Authorization": f"Bearer {_token(sales_a_id, sales=True)}"}
    sales_b_hdr = {"Authorization": f"Bearer {_token(sales_b_id, sales=True)}"}

    week_start = date(2026, 7, 13)  # Monday
    assert week_start.isoweekday() == 1

    # Seed:
    #   * sales_a: Mon 9-5 (published with secret notes), Wed draft (excluded)
    #   * sales_b: Mon 13-21 (published)
    #   * sales_c: Tue 9-5 published — but user is INACTIVE, must NOT appear
    a_mon_id = _publish(
        admin_id,
        sales_a_id,
        week_start,
        datetime(2026, 7, 13, 9, 0, tzinfo=tz),
        datetime(2026, 7, 13, 17, 0, tzinfo=tz),
        manager_notes="SECRET-NOTE-FOR-MANAGER-EYES-ONLY",
    )
    b_mon_id = _publish(
        admin_id,
        sales_b_id,
        week_start,
        datetime(2026, 7, 13, 13, 0, tzinfo=tz),
        datetime(2026, 7, 13, 21, 0, tzinfo=tz),
    )
    a_wed_draft_id = _draft(
        admin_id,
        sales_a_id,
        week_start + timedelta(days=2),
        datetime(2026, 7, 15, 9, 0, tzinfo=tz),
        datetime(2026, 7, 15, 17, 0, tzinfo=tz),
    )
    c_tue_id = _publish(
        admin_id,
        sales_c_id,
        week_start + timedelta(days=1),
        datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        datetime(2026, 7, 14, 17, 0, tzinfo=tz),
    )
    # An ACTIVE admin user with a published entry — must be excluded
    # because the query filters role='sales'. Covers the Slice-5
    # finding that the role filter was missing.
    admin_thu_id = _publish(
        admin_id,
        admin_with_shift_id,
        week_start + timedelta(days=3),
        datetime(2026, 7, 16, 10, 0, tzinfo=tz),
        datetime(2026, 7, 16, 14, 0, tzinfo=tz),
    )

    range_params = {
        "from_date": week_start.isoformat(),
        "to_date": (week_start + timedelta(days=6)).isoformat(),
    }

    # ============================================================
    # 1) AUTH
    # ============================================================
    print("===== auth =====")
    # No token → 401 (or 403 depending on the auth dep chain).
    resp = client.get("/api/sales/schedule/team", params=range_params)
    assert resp.status_code in (401, 403), resp.text

    # Sales token works.
    resp = client.get(
        "/api/sales/schedule/team",
        headers=sales_a_hdr,
        params=range_params,
    )
    assert resp.status_code == 200, resp.text
    body_a = resp.json()
    assert body_a["viewer_user_id"] == sales_a_id

    # Admin token is REJECTED — `require_sales_scope` is strict, not
    # a "any-of" check. This is the same behavior the other sales
    # endpoints rely on (an admin logged in on a sales tab can't
    # accidentally hit stylist endpoints).
    resp = client.get(
        "/api/sales/schedule/team",
        headers=admin_hdr,
        params=range_params,
    )
    assert resp.status_code == 403, resp.text
    assert "scope_forbidden" in resp.text

    # ============================================================
    # 2) PRIVACY CONTRACT
    # ============================================================
    print("===== privacy contract =====")
    entries = body_a["entries"]
    entry_ids = {e["entry_id"] for e in entries}
    user_ids_in_resp = {e["user_id"] for e in entries}

    # Draft is excluded.
    assert a_wed_draft_id not in entry_ids, (
        "draft entry must NOT appear in the sales team schedule"
    )
    # Inactive user's published row is excluded.
    assert sales_c_id not in user_ids_in_resp, (
        "inactive user must NOT appear in the sales team schedule"
    )
    assert c_tue_id not in entry_ids

    # Slice-5 finding: active admin's published row is ALSO excluded
    # (role filter, not just active filter).
    assert admin_with_shift_id not in user_ids_in_resp, (
        "active admin user must NOT appear in the sales team schedule"
    )
    assert admin_thu_id not in entry_ids, (
        "admin's published entry leaked into the coworker view — "
        "the role='sales' filter is missing"
    )

    # Published rows for active users ARE present.
    assert a_mon_id in entry_ids
    assert b_mon_id in entry_ids

    # Every row has exactly the allowed keys — no surprises.
    for e in entries:
        actual_keys = set(e.keys())
        assert actual_keys == ALLOWED_KEYS, (
            f"team-schedule row leaked keys: {actual_keys - ALLOWED_KEYS}"
            f" / missing: {ALLOWED_KEYS - actual_keys}"
        )
        # Belt-and-suspenders: explicitly forbidden keys absent.
        leaked = actual_keys & FORBIDDEN_KEYS
        assert not leaked, f"forbidden keys leaked: {leaked}"

    # The manager note's literal text must NOT appear ANYWHERE in the
    # serialized response (covers a future field rename slipping by).
    raw_body = resp.text
    assert "SECRET-NOTE-FOR-MANAGER-EYES-ONLY" not in raw_body, (
        "manager_notes content leaked into the sales team-schedule payload"
    )
    # Same check on sales_a's response.
    assert "SECRET-NOTE-FOR-MANAGER-EYES-ONLY" not in str(body_a), (
        "manager_notes content leaked into the sales team-schedule payload"
    )

    # ============================================================
    # 3) MULTI-USER + SORT
    # ============================================================
    print("===== multi-user + sort =====")
    # Both A and B should be in the response. A_mon (9am) sorts
    # before B_mon (1pm) on the same day.
    monday_rows = [
        e for e in entries if e["business_date"] == week_start.isoformat()
    ]
    assert len(monday_rows) >= 2
    assert monday_rows[0]["user_id"] == sales_a_id, (
        f"expected A's 9am shift first on Monday, got {monday_rows}"
    )
    assert monday_rows[1]["user_id"] == sales_b_id

    # ============================================================
    # 4) DATE RANGE VALIDATION
    # ============================================================
    print("===== range validation =====")
    # Missing dates.
    resp = client.get("/api/sales/schedule/team", headers=sales_a_hdr)
    assert resp.status_code == 422, resp.text

    resp = client.get(
        "/api/sales/schedule/team",
        headers=sales_a_hdr,
        params={"from_date": week_start.isoformat()},
    )
    assert resp.status_code == 422, resp.text

    # Reversed.
    resp = client.get(
        "/api/sales/schedule/team",
        headers=sales_a_hdr,
        params={
            "from_date": (week_start + timedelta(days=5)).isoformat(),
            "to_date": week_start.isoformat(),
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_date_range"

    # Too wide.
    resp = client.get(
        "/api/sales/schedule/team",
        headers=sales_a_hdr,
        params={
            "from_date": week_start.isoformat(),
            "to_date": (week_start + timedelta(days=60)).isoformat(),
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "date_range_too_wide"

    # ============================================================
    # 5) viewer_user_id matches caller
    # ============================================================
    print("===== viewer attribution =====")
    resp = client.get(
        "/api/sales/schedule/team",
        headers=sales_b_hdr,
        params=range_params,
    )
    assert resp.status_code == 200
    assert resp.json()["viewer_user_id"] == sales_b_id

    print("phase10_team_schedule smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
