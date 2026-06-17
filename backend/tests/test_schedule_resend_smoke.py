"""B2.4 smoke: admin /resend-published endpoint.

Covers:

  1. POST /api/admin/schedule/weeks/{monday}/resend-published with no
     body resends to every staffer with a published shift in the week.
  2. notification_jobs gets one row per recipient with kind
     staff.schedule_published, payload.manual_resend=true, and the
     week's shift list pre-shaped for the dispatcher (ISO datetime
     strings for starts_at/ends_at).
  3. Filtered POST with user_ids only resends to that subset.
  4. user_ids that include someone with no shifts in the week are
     returned in skipped_users (the UI can tell the admin their
     selection was stale).
  5. Non-Monday week_start gets a 422 from the StaffScheduleError
     ``week_start_not_monday`` mapping.
"""

from __future__ import annotations

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
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    NotificationJob,
    StaffScheduleEntry,
    User,
)


SEED_PREFIX = "smoke-resend-sched"
SHOP_TZ = ZoneInfo(os.environ["APP_TIMEZONE"])
client = TestClient(app)


def _make_user(db, *, role: str, suffix: str) -> User:
    user = User(
        username=f"{SEED_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}",
        email=f"{SEED_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("smoke-pw-not-real-1234567890"),
        full_name=f"Smoke {suffix.title()}",
        is_active=True,
        role=role,
        permissions=[],
    )
    db.add(user)
    db.flush()
    return user


def _seed_shift(
    db, *, user_id: int, on_date: date, actor_user_id: int, hour: int = 10
) -> StaffScheduleEntry:
    start = datetime.combine(on_date, time(hour, 0), tzinfo=SHOP_TZ)
    end = datetime.combine(on_date, time(hour + 5, 0), tzinfo=SHOP_TZ)
    entry = StaffScheduleEntry(
        user_id=user_id,
        business_date=on_date,
        starts_at_local=start,
        ends_at_local=end,
        status="published",
        attendance_status="scheduled",
        late_grace_minutes=30,
        source="manual",
        published_at=datetime.now(timezone.utc),
        published_by_user_id=actor_user_id,
        created_by_user_id=actor_user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _cleanup(user_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def _next_monday(after: date) -> date:
    days_ahead = (7 - after.weekday()) % 7 or 7
    return after + timedelta(days=days_ahead)


def main() -> int:
    db = SessionLocal()
    user_ids: list[int] = []
    try:
        admin = _make_user(db, role="admin", suffix="admin")
        sales_a = _make_user(db, role="sales", suffix="a")
        sales_b = _make_user(db, role="sales", suffix="b")
        sales_c = _make_user(db, role="sales", suffix="c")
        user_ids = [admin.id, sales_a.id, sales_b.id, sales_c.id]
        db.commit()

        monday = _next_monday(date.today())
        # sales_a has Mon + Wed shifts, sales_b has Fri shift, sales_c
        # has no shifts in this week (will appear in skipped_users
        # when explicitly requested).
        _seed_shift(db, user_id=sales_a.id, on_date=monday, actor_user_id=admin.id)
        _seed_shift(
            db,
            user_id=sales_a.id,
            on_date=monday + timedelta(days=2),
            actor_user_id=admin.id,
            hour=13,
        )
        _seed_shift(
            db,
            user_id=sales_b.id,
            on_date=monday + timedelta(days=4),
            actor_user_id=admin.id,
        )
        db.commit()

        admin_token = create_access_token(admin)
        headers = {"Authorization": f"Bearer {admin_token}"}

        # ===== 1. POST with no body resends to every affected staffer =====
        resp = client.post(
            f"/api/admin/schedule/weeks/{monday.isoformat()}/resend-published",
            headers=headers,
            json={},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["week_start"] == monday.isoformat()
        assert set(body["recipients"]) == {sales_a.id, sales_b.id}, body
        assert body["jobs_enqueued"] == 2
        assert body["skipped_users"] == []
        print(
            f"  ok   resend with empty body fanned out to {body['jobs_enqueued']} staff"
        )

        # ===== 2. notification_jobs rows are shaped for the dispatcher =====
        jobs_a = (
            db.query(NotificationJob)
            .filter(NotificationJob.recipient_user_id == sales_a.id)
            .filter(NotificationJob.kind == "staff.schedule_published")
            .all()
        )
        assert len(jobs_a) == 1, f"expected 1 job for sales_a, got {len(jobs_a)}"
        job = jobs_a[0]
        assert job.payload["manual_resend"] is True
        assert job.payload["week_start"] == monday.isoformat()
        assert len(job.payload["shifts"]) == 2, "sales_a has two shifts that week"
        for s in job.payload["shifts"]:
            # ISO strings, not datetime objects — survives JSON round-trip.
            assert isinstance(s["starts_at"], str) and "T" in s["starts_at"]
            assert isinstance(s["ends_at"], str) and "T" in s["ends_at"]
        print("  ok   per-recipient job carries ISO-shaped shifts + manual_resend flag")

        # ===== 3. Filtered POST with user_ids only resends that subset =====
        # Clear queued jobs so the next call's counts are isolated.
        db.execute(
            sql_text(
                "DELETE FROM notification_jobs WHERE recipient_user_id = ANY(:ids)"
            ),
            {"ids": [sales_a.id, sales_b.id, sales_c.id]},
        )
        db.commit()
        resp = client.post(
            f"/api/admin/schedule/weeks/{monday.isoformat()}/resend-published",
            headers=headers,
            json={"user_ids": [sales_b.id]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recipients"] == [sales_b.id]
        assert body["jobs_enqueued"] == 1
        print("  ok   user_ids filter limits the fan-out to the requested subset")

        # ===== 4. user_ids that have no shifts surface in skipped_users =====
        resp = client.post(
            f"/api/admin/schedule/weeks/{monday.isoformat()}/resend-published",
            headers=headers,
            json={"user_ids": [sales_c.id, sales_b.id]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recipients"] == [sales_b.id]
        assert body["skipped_users"] == [sales_c.id], body
        print("  ok   user with no shifts that week ends up in skipped_users")

        # ===== 5. Non-Monday week_start is rejected with 422 =====
        tuesday = monday + timedelta(days=1)
        resp = client.post(
            f"/api/admin/schedule/weeks/{tuesday.isoformat()}/resend-published",
            headers=headers,
            json={},
        )
        assert resp.status_code == 422, resp.text
        print("  ok   non-Monday week_start rejected with 422")

        db.commit()
        print("\nschedule_resend smoke ok")
        return 0
    finally:
        _cleanup(user_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
