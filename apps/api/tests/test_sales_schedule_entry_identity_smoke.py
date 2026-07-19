"""Smoke for Phase 0 Gap 3: sales schedule exposes entry identity.

Staff request actions (cover/drop/swap/pickup, later phases) need the
concrete `schedule_entry_id`. The stylist's OWN schedule should also
show the manager's note for that shift — but the coworker-visible team
view must stay sanitized.

  1. Own `GET /api/sales/schedule` shift carries `schedule_entry_id`
     (the published entry id) and `manager_notes`.
  2. Team `GET /api/sales/schedule/team` carries `entry_id` for the
     same shift but NO `manager_notes`/attendance fields, and the
     note's literal text appears nowhere in the team payload.
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta
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
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []

SECRET_NOTE = "P0SCHED-MANAGER-NOTE-OWNER-ONLY"

FORBIDDEN_TEAM_KEYS = {
    "manager_notes",
    "attendance_status",
    "actual_clock_in_punch_id",
    "actual_clock_out_punch_id",
    "late_grace_minutes",
}


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p0sched-{suffix}",
            email=f"{role}-p0sched-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P0Sched {role.title()} {suffix}",
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


def _sales_token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_sales_token(db.get(User, user_id))
    finally:
        db.close()


def _publish(
    actor_id: int,
    user_id: int,
    bdate: date,
    starts: datetime,
    ends: datetime,
    *,
    manager_notes: str | None = None,
) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_id,
            user_id=user_id,
            business_date_=bdate,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
            manager_notes=manager_notes,
        )
        db.commit()
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
    # An admin actor to author the entry; two sales users so the team
    # view has a clear "self vs coworker" split.
    admin_id = _make_user(role="admin")
    me_id = _make_user(role="sales")
    coworker_id = _make_user(role="sales")

    week_start = date(2026, 6, 8)  # Monday
    assert week_start.isoweekday() == 1

    entry_id = _publish(
        admin_id,
        me_id,
        week_start,
        datetime(2026, 6, 8, 9, 0, tzinfo=tz),
        datetime(2026, 6, 8, 17, 0, tzinfo=tz),
        manager_notes=SECRET_NOTE,
    )

    me_hdr = {"Authorization": f"Bearer {_sales_token(me_id)}"}
    coworker_hdr = {"Authorization": f"Bearer {_sales_token(coworker_id)}"}
    range_params = {
        "from_date": week_start.isoformat(),
        "to_date": (week_start + timedelta(days=6)).isoformat(),
    }

    # ============================================================
    # 1) OWN schedule carries entry id + manager notes
    # ============================================================
    print("===== own schedule entry identity =====")
    resp = client.get(
        "/api/sales/schedule", headers=me_hdr, params=range_params
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    monday = next(
        d for d in body["days"] if d["business_date"] == week_start.isoformat()
    )
    shift = monday["shift"]
    assert shift is not None, monday
    assert shift["schedule_entry_id"] == entry_id, shift
    assert shift["manager_notes"] == SECRET_NOTE, shift

    # ============================================================
    # 2) TEAM schedule carries entry_id but NOT the note
    # ============================================================
    print("===== team schedule sanitized =====")
    resp = client.get(
        "/api/sales/schedule/team", headers=coworker_hdr, params=range_params
    )
    assert resp.status_code == 200, resp.text
    team = resp.json()
    row = next(
        (e for e in team["entries"] if e["entry_id"] == entry_id), None
    )
    assert row is not None, (
        "published entry missing from the team view (entry_id absent)"
    )
    leaked = set(row.keys()) & FORBIDDEN_TEAM_KEYS
    assert not leaked, f"team row leaked forbidden keys: {leaked}"
    assert SECRET_NOTE not in resp.text, (
        "manager note text leaked into the team-schedule payload"
    )

    print("sales_schedule_entry_identity smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
