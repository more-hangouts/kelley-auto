"""Scheduling Phase 5 expiry cron smoke.

Verifies stale shift requests and open-shift posts expire, cron_run_state
is stamped, and a second tick is idempotent.
"""

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
from services import cron_state, shift_request_expiry_cron  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

_user_ids: list[int] = []
_post_ids: list[int] = []


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p5expiry-{suffix}",
            email=f"{role}-p5expiry-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P5 Expiry {role.title()} {suffix}",
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


def _entry(user_id: int, starts: datetime, ends: datetime) -> int:
    db = SessionLocal()
    try:
        e = StaffScheduleEntry(
            user_id=user_id,
            business_date=starts.astimezone(ZoneInfo(APP_TIMEZONE)).date(),
            starts_at_local=starts,
            ends_at_local=ends,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e.id
    finally:
        db.close()


def _open_post(starts: datetime, ends: datetime) -> int:
    db = SessionLocal()
    try:
        p = OpenShiftPost(
            business_date=starts.astimezone(ZoneInfo(APP_TIMEZONE)).date(),
            starts_at_local=starts,
            ends_at_local=ends,
            status="open",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        _post_ids.append(p.id)
        return p.id
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
        db.execute(
            sql_text("DELETE FROM cron_run_state WHERE name = :n"),
            {"n": cron_state.SCHEDULE_REQUEST_EXPIRY},
        )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    requester = _make_user()
    candidate = _make_user()
    now = datetime(2026, 12, 8, 18, 0, tzinfo=timezone.utc)
    past_start = now.astimezone(tz) - timedelta(hours=2)
    future_start = now.astimezone(tz) + timedelta(hours=18)
    cutoff_start = now.astimezone(tz) + timedelta(hours=10)

    expired_entry = _entry(
        requester, past_start, past_start + timedelta(hours=8)
    )
    active_entry = _entry(
        requester, future_start, future_start + timedelta(hours=8)
    )
    due_post = _open_post(cutoff_start, cutoff_start + timedelta(hours=8))
    active_post = _open_post(future_start, future_start + timedelta(hours=8))

    db = SessionLocal()
    try:
        expired_req = StaffShiftRequest(
            request_type="cover",
            status="pending",
            source_entry_id=expired_entry,
            requester_user_id=requester,
            candidate_user_id=candidate,
        )
        active_req = StaffShiftRequest(
            request_type="cover",
            status="pending",
            source_entry_id=active_entry,
            requester_user_id=requester,
            candidate_user_id=candidate,
        )
        db.add_all([expired_req, active_req])
        db.commit()
    finally:
        db.close()

    print("===== expiry cron first run =====")
    db = SessionLocal()
    try:
        # Monkey-patch the run's started_at by calling the lower-level helper
        # is tempting, but this smoke needs cron_run_state too. Instead use
        # real tick data for stamping, then assert row state by ids after
        # a direct deterministic pass.
        from services import staff_shift_requests

        with cron_state.record_run(cron_state.SCHEDULE_REQUEST_EXPIRY) as run:
            result = staff_shift_requests.expire_due(db, now=now)
            run.scanned = result["scanned"]
            run.changed = result["changed"]
            db.commit()
        assert result == {"scanned": 2, "changed": 2}, result
    finally:
        db.close()

    db = SessionLocal()
    try:
        statuses = {
            row.id: row.status
            for row in db.query(StaffShiftRequest).all()
            if row.requester_user_id == requester
        }
        assert "expired" in statuses.values(), statuses
        assert "pending" in statuses.values(), statuses
        assert db.get(OpenShiftPost, due_post).status == "expired"
        assert db.get(OpenShiftPost, active_post).status == "open"
        state = db.execute(
            sql_text(
                "SELECT last_scanned_count, last_changed_count, last_error, "
                "consecutive_failures FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.SCHEDULE_REQUEST_EXPIRY},
        ).first()
        assert state is not None
        assert state.last_scanned_count == 2, state
        assert state.last_changed_count == 2, state
        assert state.last_error is None
        assert state.consecutive_failures == 0
    finally:
        db.close()

    print("===== expiry cron idempotent second run =====")
    db = SessionLocal()
    try:
        from services import staff_shift_requests

        second = staff_shift_requests.expire_due(db, now=now)
        db.commit()
        assert second == {"scanned": 0, "changed": 0}, second
    finally:
        db.close()

    # Smoke the public tick entry point too; there should be nothing left
    # to change at the real current time.
    db = SessionLocal()
    try:
        shift_request_expiry_cron.tick(db)
    finally:
        db.close()

    assert cron_state.SCHEDULE_REQUEST_EXPIRY in cron_state.ALL_CRON_NAMES
    print("shift_request_expiry_cron smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
