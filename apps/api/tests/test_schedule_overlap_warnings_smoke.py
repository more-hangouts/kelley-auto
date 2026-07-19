"""Smoke for Phase 0 Gap 4: advisory overlap warnings on the grid.

Duplicate detection only rejects exact start/end duplicates. Genuinely
overlapping intervals for one stylist used to pass silently. The week
payload now surfaces an advisory `overlap_warnings` list (warning-first;
manual split shifts still schedule freely).

  1. Two overlapping draft entries for one stylist produce a warning
     naming both entry ids.
  2. Non-overlapping back-to-back split shifts (sharing an edge) for
     another stylist do NOT warn.
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
    create_access_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
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


def _draft(
    actor_id: int,
    user_id: int,
    bdate: date,
    starts: datetime,
    ends: datetime,
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
            publish=False,
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
    admin_id = _make_user(role="admin")
    overlapper = _make_user(role="sales")
    splitter = _make_user(role="sales")

    week_start = date(2026, 6, 8)  # Monday
    assert week_start.isoweekday() == 1
    mon = week_start

    # Overlapping pair for `overlapper`: 9-12 and 11-14 overlap on 11-12.
    ov_a = _draft(
        admin_id,
        overlapper,
        mon,
        datetime(2026, 6, 8, 9, 0, tzinfo=tz),
        datetime(2026, 6, 8, 12, 0, tzinfo=tz),
    )
    ov_b = _draft(
        admin_id,
        overlapper,
        mon,
        datetime(2026, 6, 8, 11, 0, tzinfo=tz),
        datetime(2026, 6, 8, 14, 0, tzinfo=tz),
    )

    # Split shift for `splitter`: 9-12 then 12-15 share the edge at 12
    # and must NOT warn.
    _draft(
        admin_id,
        splitter,
        mon,
        datetime(2026, 6, 8, 9, 0, tzinfo=tz),
        datetime(2026, 6, 8, 12, 0, tzinfo=tz),
    )
    _draft(
        admin_id,
        splitter,
        mon,
        datetime(2026, 6, 8, 12, 0, tzinfo=tz),
        datetime(2026, 6, 8, 15, 0, tzinfo=tz),
    )

    admin_hdr = {"Authorization": f"Bearer {_token(admin_id)}"}
    print("===== week payload overlap warnings =====")
    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [overlapper, splitter],
        },
    )
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["overlap_warnings"]

    # Exactly one warning, for the overlapping stylist, naming both ids.
    ov_warnings = [w for w in warnings if w["user_id"] == overlapper]
    assert len(ov_warnings) == 1, ov_warnings
    assert set(ov_warnings[0]["entry_ids"]) == {ov_a, ov_b}, ov_warnings[0]
    assert ov_warnings[0]["business_date"] == mon.isoformat()

    # The split-shift stylist never warns.
    split_warnings = [w for w in warnings if w["user_id"] == splitter]
    assert split_warnings == [], (
        f"edge-sharing split shifts must not warn: {split_warnings}"
    )

    print("schedule_overlap_warnings smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
