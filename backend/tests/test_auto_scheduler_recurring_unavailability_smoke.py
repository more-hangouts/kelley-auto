"""Smoke for Phase 0 Gap 2: auto-scheduler honors recurring unavailability.

The generator already skips approved time off but ignored a stylist's
self-serve recurring unavailability. Publish later hard-skips those
drafts, so the manager got drafts they could never publish.

  1. A stylist with a Wednesday recurring-unavailable block covering the
     no-appointment fill window gets NO Wednesday draft.
  2. The summary reports `skipped_unavailable_count` separately from
     `skipped_time_off_count`.
  3. The block is weekday-scoped, not week-scoped: the same stylist is
     still drafted on Thursday/Friday (other open days), proving we
     don't over-block the whole week.
"""

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
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
from database.models import User  # noqa: E402
from services import auto_scheduler, recurring_availability  # noqa: E402

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


def _make_recurring_block(
    user_id: int,
    *,
    weekday: int,
    start: str,
    end: str,
    effective_from: date,
) -> int:
    db = SessionLocal()
    try:
        d = recurring_availability.create_block(
            db,
            user_id=user_id,
            weekday=weekday,
            start_time_local=start,
            end_time_local=end,
            effective_from=effective_from,
            reason="p0sched smoke",
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
                sql_text(
                    "DELETE FROM recurring_unavailability "
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
    stylist = _make_user(role="sales")

    week_start = date(2026, 6, 1)  # Monday; open days incl Wed/Thu/Fri
    assert week_start.isoweekday() == 1
    wed = week_start + timedelta(days=2)
    thu = week_start + timedelta(days=3)
    fri = week_start + timedelta(days=4)

    # Recurring unavailable EVERY Wednesday, covering the default
    # no-appointment fill window (14:00-19:00) and then some.
    _make_recurring_block(
        stylist,
        weekday=3,  # ISO Wednesday
        start="13:00",
        end="20:00",
        effective_from=week_start - timedelta(days=7),
    )

    print("===== generate with recurring unavailability =====")
    db = SessionLocal()
    try:
        result = auto_scheduler.generate_draft_week(
            db,
            actor_user_id=admin_id,
            week_start=week_start,
            user_ids=[stylist],
        )
        db.commit()
    finally:
        db.close()

    print(
        f"  created={result['created_count']} "
        f"skipped_time_off={result['skipped_time_off_count']} "
        f"skipped_unavailable={result['skipped_unavailable_count']}"
    )

    # New counter exists and is distinct from the time-off one.
    assert "skipped_unavailable_count" in result, result
    assert result["skipped_time_off_count"] == 0, result
    assert result["skipped_unavailable_count"] >= 1, result

    # Pull the generated week back and inspect the stylist's entries.
    db = SessionLocal()
    try:
        from services import staff_schedule

        body = staff_schedule.list_week(db, week_start=week_start)
    finally:
        db.close()
    entries = [e for e in body["entries"] if e["user_id"] == stylist]

    # No entry overlaps the Wednesday block at all (cleanest assertion:
    # nothing was drafted on Wednesday for this stylist).
    wed_entries = [e for e in entries if e["business_date"] == wed.isoformat()]
    assert wed_entries == [], (
        f"recurring-unavailable stylist was drafted on Wednesday: {wed_entries}"
    )

    # Defensive: even if a future window change put an entry on Wed, it
    # must not overlap the 13:00-20:00 block.
    blk_start = datetime.combine(wed, time(13, 0), tzinfo=tz)
    blk_end = datetime.combine(wed, time(20, 0), tzinfo=tz)
    for e in wed_entries:
        s = datetime.fromisoformat(e["starts_at_local"])
        en = datetime.fromisoformat(e["ends_at_local"])
        assert not (s < blk_end and en > blk_start), (
            f"generated entry overlaps the recurring-unavailable block: {e}"
        )

    # Weekday-scoped, not week-scoped: Thu and Fri are open days with no
    # block, so the stylist IS drafted there.
    thu_entries = [e for e in entries if e["business_date"] == thu.isoformat()]
    fri_entries = [e for e in entries if e["business_date"] == fri.isoformat()]
    assert thu_entries, "stylist should still be drafted Thursday"
    assert fri_entries, "stylist should still be drafted Friday"

    print("auto_scheduler_recurring_unavailability smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
