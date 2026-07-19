"""Scheduling Phase 5 admin-grid exception count smoke."""

import os
import sys
import uuid
from datetime import date, datetime
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

from sqlalchemy import text as sql_text  # noqa: E402

from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    OpenShiftPost,
    StaffScheduleEntry,
    StaffShiftRequest,
    User,
)
from services import staff_schedule  # noqa: E402

_user_ids: list[int] = []
_post_ids: list[int] = []


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p5badge-{suffix}",
            email=f"{role}-p5badge-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P5 Badge {role.title()} {suffix}",
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


def _entry(user_id: int, bdate: date, start_h: int, end_h: int) -> int:
    tz = ZoneInfo(APP_TIMEZONE)
    db = SessionLocal()
    try:
        e = StaffScheduleEntry(
            user_id=user_id,
            business_date=bdate,
            starts_at_local=datetime(
                bdate.year, bdate.month, bdate.day, start_h, 0, tzinfo=tz
            ),
            ends_at_local=datetime(
                bdate.year, bdate.month, bdate.day, end_h, 0, tzinfo=tz
            ),
            status="published",
            published_at=datetime.now(tz),
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_request_events WHERE request_id IN "
                    "(SELECT id FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:u))"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            if _post_ids:
                db.execute(
                    sql_text("DELETE FROM open_shift_posts WHERE id = ANY(:p)"),
                    {"p": _post_ids},
                )
            db.execute(
                sql_text("DELETE FROM staff_schedule_entries WHERE user_id = ANY(:u)"),
                {"u": _user_ids},
            )
            db.execute(sql_text("DELETE FROM users WHERE id = ANY(:u)"), {"u": _user_ids})
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    week_start = date(2026, 12, 7)
    monday = week_start
    tuesday = date(2026, 12, 8)
    admin = _make_user(role="admin")
    a = _make_user()
    b = _make_user()

    a_entry = _entry(a, monday, 9, 17)
    b_entry = _entry(b, tuesday, 10, 18)
    # Overlap for A on Monday.
    _entry(a, monday, 13, 19)

    db = SessionLocal()
    try:
        db.add(
            StaffShiftRequest(
                request_type="swap",
                status="pending",
                source_entry_id=a_entry,
                target_entry_id=b_entry,
                requester_user_id=a,
                candidate_user_id=b,
            )
        )
        post = OpenShiftPost(
            business_date=monday,
            starts_at_local=datetime(2026, 12, 7, 12, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 12, 7, 20, 0, tzinfo=tz),
            status="open",
            created_by_user_id=admin,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        _post_ids.append(post.id)
    finally:
        db.close()

    print("===== week payload exception counts =====")
    db = SessionLocal()
    try:
        body = staff_schedule.list_week(db, week_start=week_start)
        counts = body["schedule_exception_counts"]
        assert counts["by_date"][monday.isoformat()]["pending_requests"] == 1
        assert counts["by_date"][monday.isoformat()]["open_shifts"] == 1
        assert counts["by_date"][monday.isoformat()]["conflicts"] == 1
        assert counts["by_date"][tuesday.isoformat()]["pending_requests"] == 1
        assert counts["by_cell"][f"{a}|{monday.isoformat()}"]["pending_requests"] == 1
        assert counts["by_cell"][f"{a}|{monday.isoformat()}"]["conflicts"] == 1
        assert counts["by_cell"][f"{b}|{tuesday.isoformat()}"]["pending_requests"] == 1
    finally:
        db.close()
    print("schedule_request_badges smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
