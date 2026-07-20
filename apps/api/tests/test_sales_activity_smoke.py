"""Smoke tests for Phase 14 — sales activity monitoring + commission clock-in.

Standalone script (run: `python tests/test_sales_activity_smoke.py`), same
shape as the other *_smoke.py files: seed throwaway users, exercise the
live app via TestClient, assert, then clean up every row it created and
restore business_profile.attendance_mode.

Coverage (plan 14.6):
  - Commission mode: clock-in with NO coordinates is accepted as an
    'app_session' punch; payroll mode still rejects a no-GPS punch.
  - Sales search records sales.search_performed with query length +
    result count only (never the raw query text), and is NOT throttled.
  - Subject views ARE throttled within the window (one row per repeat).
  - Admin summary + per-rep recent reflect the activity; scope isolation
    holds both ways (admin token 403 on sales, sales token 403 on admin).
"""

import os
import sys
import uuid
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
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import SalesActivityEvent, User  # noqa: E402
from modules.analytics.services import sales_activity  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p14-{suffix}",
            email=f"{role}-p14-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P14 {role.title()}",
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


def _set_attendance_mode(mode: str) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text("UPDATE business_profile SET attendance_mode = :m"), {"m": mode})
        db.commit()
    finally:
        db.close()


def _get_attendance_mode() -> str:
    db = SessionLocal()
    try:
        return db.execute(sql_text("SELECT attendance_mode FROM business_profile")).scalar()
    finally:
        db.close()


def _get_profile_settings() -> tuple[str, str]:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text("SELECT attendance_mode, selfie_policy FROM business_profile")
        ).first()
        return row[0], row[1]
    finally:
        db.close()


def _set_selfie_policy(policy: str) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text("UPDATE business_profile SET selfie_policy = :p"), {"p": policy})
        db.commit()
    finally:
        db.close()


def _activity_rows(user_id: int) -> list[SalesActivityEvent]:
    db = SessionLocal()
    try:
        return (
            db.query(SalesActivityEvent)
            .filter(SalesActivityEvent.actor_user_id == user_id)
            .order_by(SalesActivityEvent.id)
            .all()
        )
    finally:
        db.close()


_mode_snapshot: str | None = None
_selfie_snapshot: str | None = None


def main() -> None:
    global _mode_snapshot, _selfie_snapshot
    _mode_snapshot, _selfie_snapshot = _get_profile_settings()
    # Pin selfie to 'optional' so it never interferes with the clock-in
    # scenarios (a stored 'required' policy would 400 before the geofence
    # check in payroll mode). Restored in the finally block.
    _set_selfie_policy("optional")

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_headers = {"Authorization": f"Bearer {_token_for(sales_id, sales=True)}"}
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}

    # ------------------------------------------------------------------
    # Scenario A: payroll mode rejects a no-GPS clock-in.
    # ------------------------------------------------------------------
    _set_attendance_mode("payroll")
    resp = client.post("/api/sales/clock/in", headers=sales_headers, data={})
    assert resp.status_code == 403, f"payroll no-GPS should 403, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["code"] == "outside_geofence", resp.text

    # ------------------------------------------------------------------
    # Scenario B: commission mode accepts a no-GPS clock-in as app_session.
    # ------------------------------------------------------------------
    _set_attendance_mode("commission")
    resp = client.post("/api/sales/clock/in", headers=sales_headers, data={})
    assert resp.status_code == 200, f"commission no-GPS should 200, got {resp.status_code}: {resp.text}"
    punch = resp.json()
    assert punch["accepted_by"] == "app_session", punch
    assert punch["location_id"] is None or isinstance(punch["location_id"], int)
    # /clock/status must advertise commission mode (what the UI keys off).
    status = client.get("/api/sales/clock/status", headers=sales_headers).json()
    assert status["attendance_mode"] == "commission", status
    assert status["state"] == "in", status

    # ------------------------------------------------------------------
    # Scenario C: search records sales.search_performed, no raw text, and
    # is NOT throttled (two searches -> two rows).
    # ------------------------------------------------------------------
    secret_query = "zzsmoke" + uuid.uuid4().hex[:6]
    for _ in range(2):
        r = client.get(f"/api/sales/search/leads?q={secret_query}", headers=sales_headers)
        assert r.status_code == 200, r.text
    search_rows = [
        row for row in _activity_rows(sales_id)
        if row.activity_type == sales_activity.SALES_SEARCH_PERFORMED
    ]
    assert len(search_rows) == 2, f"searches must not be throttled: {len(search_rows)}"
    for row in search_rows:
        md = row.activity_metadata or {}
        assert "query_length" in md and "result_count" in md, md
        # The raw query text must never be persisted anywhere on the row.
        blob = f"{row.route}|{row.source}|{md}"
        assert secret_query not in blob, f"raw query text leaked: {blob}"
        assert md["query_length"] == len(secret_query), md

    # ------------------------------------------------------------------
    # Scenario D: subject views ARE throttled (service-level; the
    # appointments/contacts endpoints have no data on the dealership).
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        first = sales_activity.record(
            db, actor_user_id=sales_id,
            activity_type=sales_activity.SALES_CONTACT_VIEWED,
            subject_kind="contact", subject_id=987654,
        )
        repeat = sales_activity.record(
            db, actor_user_id=sales_id,
            activity_type=sales_activity.SALES_CONTACT_VIEWED,
            subject_kind="contact", subject_id=987654,
        )
        other = sales_activity.record(
            db, actor_user_id=sales_id,
            activity_type=sales_activity.SALES_CONTACT_VIEWED,
            subject_kind="contact", subject_id=987655,
        )
    finally:
        db.close()
    assert first is not None, "first view should record"
    assert repeat is None, "immediate repeat view should be throttled"
    assert other is not None, "a different subject should record"
    contact_rows = [
        row for row in _activity_rows(sales_id)
        if row.activity_type == sales_activity.SALES_CONTACT_VIEWED
    ]
    assert len(contact_rows) == 2, f"throttle should leave 2 contact rows, got {len(contact_rows)}"

    # ------------------------------------------------------------------
    # Scenario E: admin summary + per-rep recent reflect the activity.
    # ------------------------------------------------------------------
    summary = client.get(
        "/api/admin/sales-activity/summary?range=today", headers=admin_headers
    )
    assert summary.status_code == 200, summary.text
    reps = {r["actor_user_id"]: r for r in summary.json()["reps"]}
    assert sales_id in reps, "seeded rep missing from summary"
    me = reps[sales_id]
    assert me["searches"] == 2, me
    assert me["contacts_viewed"] == 2, me
    assert me["last_activity_at"] is not None, me

    recent = client.get(
        f"/api/admin/sales-activity/rep/{sales_id}/recent?limit=50", headers=admin_headers
    )
    assert recent.status_code == 200, recent.text
    assert len(recent.json()["rows"]) == 4, recent.json()  # 2 searches + 2 contact views

    # ------------------------------------------------------------------
    # Scenario F: scope isolation both ways.
    # ------------------------------------------------------------------
    r = client.get(f"/api/sales/search/leads?q={secret_query}", headers=admin_headers)
    assert r.status_code == 403, f"admin token on sales search should 403: {r.status_code}"
    r = client.get("/api/admin/sales-activity/summary?range=today", headers=sales_headers)
    assert r.status_code == 403, f"sales token on admin summary should 403: {r.status_code}"

    print("sales_activity smoke ok")


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM sales_activity_events WHERE actor_user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM staff_punches WHERE user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
        if _mode_snapshot is not None:
            _set_attendance_mode(_mode_snapshot)
        if _selfie_snapshot is not None:
            _set_selfie_policy(_selfie_snapshot)
