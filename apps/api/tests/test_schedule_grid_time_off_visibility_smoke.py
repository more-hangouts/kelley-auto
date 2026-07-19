"""Smoke for Phase 0 Gap 1: time-off cells must not hide entries.

The admin weekly grid grays out cells covered by approved time off.
The bug was render-side: the cell short-circuited on time off and
never drew an existing draft/published entry sitting in that same
(stylist, day) cell, so a manager couldn't see or delete it.

This smoke locks the BACKEND contract the fixed render depends on:
`GET /api/admin/schedule/week` returns BOTH the time-off block and any
overlapping draft/published entry for the same cell. (The render half
is covered by the frontend build.)

  1. Seed approved time off for a stylist on Wednesday.
  2. Seed an overlapping DRAFT entry in that same cell.
  3. The week payload carries the time-off block AND the draft entry,
     keyed to the same (user_id, business_date).
  4. A published entry in a time-off cell also still shows.
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
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import TimeOffRequest, User  # noqa: E402
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


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


def _token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_access_token(db.get(User, user_id))
    finally:
        db.close()


def _entry(
    actor_id: int,
    user_id: int,
    bdate: date,
    starts: datetime,
    ends: datetime,
    *,
    publish: bool,
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
            publish=publish,
        )
        db.commit()
        return d["id"]
    finally:
        db.close()


def _approved_time_off(user_id: int, starts: datetime, ends: datetime) -> int:
    db = SessionLocal()
    try:
        t = TimeOffRequest(
            user_id=user_id,
            starts_at=starts.astimezone(timezone.utc),
            ends_at=ends.astimezone(timezone.utc),
            reason="p0sched smoke",
            status="approved",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t.id
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
                sql_text(
                    "DELETE FROM time_off_decision_events "
                    "WHERE request_id IN ("
                    "SELECT id FROM time_off_requests "
                    "WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_requests "
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
    stylist_a = _make_user(role="sales")  # draft inside time off
    stylist_b = _make_user(role="sales")  # published inside time off

    week_start = date(2026, 6, 8)  # Monday
    assert week_start.isoweekday() == 1
    wed = week_start + timedelta(days=2)

    # Entries that already live in the cell BEFORE time off is approved
    # — this models the real "shift existed, then time off came in"
    # sequence the gap describes. (Publishing/creating into an existing
    # time-off cell is itself blocked by the conflict check, which is
    # correct; the gap is purely about not hiding pre-existing rows.)
    draft_a = _entry(
        admin_id,
        stylist_a,
        wed,
        datetime(2026, 6, 10, 9, 0, tzinfo=tz),
        datetime(2026, 6, 10, 17, 0, tzinfo=tz),
        publish=False,
    )
    pub_b = _entry(
        admin_id,
        stylist_b,
        wed,
        datetime(2026, 6, 10, 12, 0, tzinfo=tz),
        datetime(2026, 6, 10, 19, 0, tzinfo=tz),
        publish=True,
    )

    # Approved time off covering all of Wednesday lands AFTER the shifts.
    tor_a = _approved_time_off(
        stylist_a,
        datetime.combine(wed, time.min, tzinfo=tz),
        datetime.combine(wed + timedelta(days=1), time.min, tzinfo=tz),
    )
    tor_b = _approved_time_off(
        stylist_b,
        datetime.combine(wed, time.min, tzinfo=tz),
        datetime.combine(wed + timedelta(days=1), time.min, tzinfo=tz),
    )

    admin_hdr = {"Authorization": f"Bearer {_token(admin_id)}"}

    print("===== week payload carries both time off AND entries =====")
    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [stylist_a, stylist_b],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Time-off blocks present for both stylists on Wednesday.
    block_request_ids = {b["request_id"] for b in body["time_off_blocks"]}
    assert tor_a in block_request_ids, body["time_off_blocks"]
    assert tor_b in block_request_ids, body["time_off_blocks"]

    # The crux: the entries are STILL in the payload even though their
    # cells are covered by approved time off.
    entries_by_id = {e["id"]: e for e in body["entries"]}
    assert draft_a in entries_by_id, (
        "draft entry in a time-off cell was dropped from the week payload"
    )
    assert pub_b in entries_by_id, (
        "published entry in a time-off cell was dropped from the week payload"
    )

    # Both entries land on the same day their time off covers — proving
    # they share the cell the grid grays out.
    assert entries_by_id[draft_a]["business_date"] == wed.isoformat()
    assert entries_by_id[draft_a]["user_id"] == stylist_a
    assert entries_by_id[draft_a]["status"] == "draft"
    assert entries_by_id[pub_b]["business_date"] == wed.isoformat()
    assert entries_by_id[pub_b]["user_id"] == stylist_b
    assert entries_by_id[pub_b]["status"] == "published"

    print("schedule_grid_time_off_visibility smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
