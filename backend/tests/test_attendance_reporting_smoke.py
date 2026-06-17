"""Smoke for Phase 9 sub-slice 1, Priority 2: attendance reporting.

Covers the four backend pieces shipped in steps 4-6:

  - Range presets: current_month / last_month / current_quarter /
    last_quarter, plus the real `pay_period` anchor when
    `business_profile.biweekly_anchor_date` is set (and the legacy
    rolling window when it isn't).
  - Bucketed `/totals?bucket=day|week|biweek|month` with `by_bucket`
    in the response alongside the existing `by_day`.
  - 422 `pay_period_anchor_missing` when biweek bucketing is asked for
    but no anchor is set; 422 `invalid_bucket` for unknown bucket names.
  - CSV export at `/totals/export.csv` matches the JSON shape and
    requires admin scope.

Mutates `business_profile.biweekly_anchor_date` so it must run
serially with other attendance smokes (project rule on shared
singleton state). Saves and restores the prior anchor in cleanup.
"""

import csv
import io
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import BusinessProfile, StaffPunch, User  # noqa: E402
from services.business_time import business_date, shop_tz  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_punch_ids: list[int] = []
_prior_anchor: date | None = None
_prior_anchor_loaded = False


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p9rep-{suffix}",
            email=f"{role}-p9rep-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P9rep {role.title()}",
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


def _token_for(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _local_dt(d: date, hour: int, minute: int = 0) -> datetime:
    """Construct a tz-aware datetime in the boutique's local zone."""
    return datetime.combine(d, time(hour, minute), tzinfo=shop_tz())


def _seed_paired_session(*, user_id: int, day: date, hours: float) -> tuple[int, int]:
    """Insert an in/out pair on `day` spanning `hours` hours, anchored
    at 09:00 local. Returns (in_id, out_id)."""
    in_local = _local_dt(day, 9, 0)
    out_local = in_local + timedelta(hours=hours)
    db = SessionLocal()
    try:
        in_punch = StaffPunch(
            user_id=user_id,
            direction="in",
            punched_at=in_local.astimezone(timezone.utc),
            status="unscheduled",
        )
        out_punch = StaffPunch(
            user_id=user_id,
            direction="out",
            punched_at=out_local.astimezone(timezone.utc),
            status="unscheduled",
        )
        db.add_all([in_punch, out_punch])
        db.commit()
        db.refresh(in_punch)
        db.refresh(out_punch)
        _punch_ids.extend([in_punch.id, out_punch.id])
        return in_punch.id, out_punch.id
    finally:
        db.close()


def _set_anchor(value: date | None) -> None:
    """Set the singleton business_profile's biweekly_anchor_date, or
    clear it when value is None. Captures the prior value once so the
    cleanup block restores the row."""
    global _prior_anchor, _prior_anchor_loaded
    db = SessionLocal()
    try:
        profile = db.query(BusinessProfile).first()
        if profile is None:
            return
        if not _prior_anchor_loaded:
            _prior_anchor = profile.biweekly_anchor_date
            _prior_anchor_loaded = True
        profile.biweekly_anchor_date = value
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _punch_ids:
            db.execute(
                sql_text("DELETE FROM staff_punches WHERE id = ANY(:ids)"),
                {"ids": _punch_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        # Restore the prior anchor regardless of which branch ran.
        if _prior_anchor_loaded:
            profile = db.query(BusinessProfile).first()
            if profile is not None:
                profile.biweekly_anchor_date = _prior_anchor
        db.commit()
    finally:
        db.close()


def _hours_for_user(payload: dict, user_id: int) -> dict:
    """Pluck the per-user totals row out of the payload — global pass
    smokes assert per-user, not global, per the project rule."""
    for row in payload["totals"]:
        if row["user_id"] == user_id:
            return row
    raise AssertionError(
        f"user {user_id} missing from /totals payload; got {payload['totals']!r}"
    )


def main() -> None:
    admin_id = _make_user(role="admin")
    sales_id = _make_user(role="sales")
    user_id = _make_user(role="user")  # the punch owner
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}
    sales_headers = {"Authorization": f"Bearer {_token_for(sales_id, sales=True)}"}

    today = business_date()
    # Pick one Monday and one Tuesday in the current ISO week so the
    # week bucket clearly aggregates two days.
    monday = today - timedelta(days=today.weekday())
    tuesday = monday + timedelta(days=1)
    # Use mon+tue to keep both punches in the same calendar month most
    # of the time. This relies on Mondays and Tuesdays sharing a month;
    # only edge case is the 1st of the month landing on a Tuesday.
    same_month_a = monday
    same_month_b = tuesday
    # A day from the prior month (not used for "current_month" tests,
    # only for "last_month" coverage). Subtract 32 days to guarantee
    # we land in a different calendar month even when today is mid-month.
    last_month_day = today - timedelta(days=32)

    _seed_paired_session(user_id=user_id, day=same_month_a, hours=4.0)
    _seed_paired_session(user_id=user_id, day=same_month_b, hours=2.0)
    _seed_paired_session(user_id=user_id, day=last_month_day, hours=3.0)

    # ---- 1. Default bucket=day preserves the existing shape. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_week"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bucket"] == "day", body
    row = _hours_for_user(body, user_id)
    # Mon=4h, Tue=2h, total 6h.
    assert row["total_hours"] == 6.0, row
    days = {entry["business_date"]: entry["hours"] for entry in row["by_day"]}
    assert days.get(monday.isoformat()) == 4.0, days
    assert days.get(tuesday.isoformat()) == 2.0, days

    # `by_bucket` exists in parallel and equals `by_day` for bucket=day.
    buckets = {entry["bucket_key"]: entry["hours"] for entry in row["by_bucket"]}
    assert buckets == days, (buckets, days)

    # ---- 2. bucket=week aggregates Mon+Tue into one ISO-week row. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_week", "bucket": "week"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bucket"] == "week"
    row = _hours_for_user(body, user_id)
    assert row["total_hours"] == 6.0
    bucket_entries = row["by_bucket"]
    assert len(bucket_entries) == 1, bucket_entries
    assert bucket_entries[0]["bucket_key"] == monday.isoformat()
    assert bucket_entries[0]["hours"] == 6.0

    # ---- 3. bucket=month aggregates over current_month. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_month", "bucket": "month"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = _hours_for_user(body, user_id)
    month_entries = row["by_bucket"]
    # Every bucket_key in the response should be the current YYYY-MM.
    expected_key = f"{today.year:04d}-{today.month:02d}"
    assert len(month_entries) == 1
    assert month_entries[0]["bucket_key"] == expected_key
    # Mon and Tue are usually in the same month; assert at minimum that
    # the punches we expect to be in the current month are accounted
    # for. The 4h+2h pair sums to 6h.
    assert month_entries[0]["hours"] == 6.0, month_entries

    # ---- 4. last_month covers the 32-days-ago session. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "last_month"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # last_month_day is 32 days back. It is always in the prior month
    # *or earlier*. If today is e.g. day 3 of a 30-day month, 32 days
    # back is in month-2. We only assert that the user appears with
    # the prior session if the day actually falls in the prior month;
    # otherwise the user is absent from the payload, which is also OK.
    if last_month_day.month == ((today.month - 1) or 12):
        row = _hours_for_user(body, user_id)
        assert row["total_hours"] == 3.0, row

    # ---- 5. current_quarter and last_quarter resolve at all. ----
    for key in ("current_quarter", "last_quarter"):
        resp = client.get(
            "/api/admin/attendance/totals",
            headers=admin_headers,
            params={"range_key": key},
        )
        assert resp.status_code == 200, (key, resp.text)
        body = resp.json()
        assert body["bucket"] == "day"
        # `from_date` and `to_date` echo back as ISO strings spanning
        # exactly three months for quarters.
        f = date.fromisoformat(body["from_date"])
        t = date.fromisoformat(body["to_date"])
        assert (t - f).days >= 89, (key, f, t)
        assert (t - f).days <= 92, (key, f, t)

    # ---- 6. bucket=biweek without an anchor is 422. ----
    _set_anchor(None)
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_week", "bucket": "biweek"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "pay_period_anchor_missing", resp.text

    # The legacy `pay_period` range key falls back to the rolling
    # window when no anchor is set — never raises.
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "pay_period"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["biweekly_anchor_date"] is None
    f = date.fromisoformat(body["from_date"])
    t = date.fromisoformat(body["to_date"])
    # Rolling 14-day window: today + previous 13 days = 14 calendar days.
    assert (t - f).days == 13, (f, t)

    # ---- 7. With an anchor set, pay_period and bucket=biweek both
    #         align to the anchor. ----
    anchor = today - timedelta(days=today.weekday())  # this Monday
    _set_anchor(anchor)
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "pay_period"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["biweekly_anchor_date"] == anchor.isoformat()
    f = date.fromisoformat(body["from_date"])
    t = date.fromisoformat(body["to_date"])
    assert (t - f).days == 13, (f, t)
    # Today is in the window.
    assert f <= today <= t

    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_week", "bucket": "biweek"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    row = _hours_for_user(body, user_id)
    bucket_entries = row["by_bucket"]
    # Both seeded sessions in current_week land in the biweek bucket
    # whose key is the aligned start date — anchor itself when anchor
    # equals "this Monday".
    assert len(bucket_entries) == 1
    assert bucket_entries[0]["bucket_key"] == anchor.isoformat()
    assert bucket_entries[0]["hours"] == 6.0

    # ---- 8. invalid_bucket returns 422. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"bucket": "decade"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_bucket"

    # ---- 9. invalid range_key still rejected. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={"range_key": "current_decade"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_range_key"

    # ---- 10. CSV export streams text/csv with the right columns. ----
    resp = client.get(
        "/api/admin/attendance/totals/export.csv",
        headers=admin_headers,
        params={"range_key": "current_week", "bucket": "week"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv"), resp.headers
    assert "attachment" in resp.headers.get("content-disposition", "")
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == [
        "staff_user_id",
        "username",
        "full_name",
        "bucket",
        "bucket_key",
        "hours",
    ], rows[0]
    user_rows = [r for r in rows[1:] if r and int(r[0]) == user_id]
    # One bucket row + one TOTAL row per stylist for current_week+week.
    assert len(user_rows) == 2, user_rows
    bucket_row = next(r for r in user_rows if r[4] != "TOTAL")
    total_row = next(r for r in user_rows if r[4] == "TOTAL")
    assert bucket_row[3] == "week"
    assert bucket_row[4] == anchor.isoformat()
    assert float(bucket_row[5]) == 6.0
    assert float(total_row[5]) == 6.0

    # ---- 11. Sales token gets 403 on JSON and CSV totals. ----
    for path in (
        "/api/admin/attendance/totals",
        "/api/admin/attendance/totals/export.csv",
    ):
        resp = client.get(path, headers=sales_headers)
        assert resp.status_code == 403, (path, resp.text)

    print("phase9 attendance_reporting smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
