"""Smoke test for Phase 10 Slice 6 — recurring stylist unavailability
(Epic 3.4).

Covers:

  1. Service-level CRUD + invariants:
       - weekday_out_of_range, invalid_time_range,
         invalid_effective_range, duplicate_active_rule
       - list_for_user filters expired rules by default
       - find_conflict returns the right block id
  2. expand_blocks_for_week — materializes only weekdays the rule
     covers in the visible Mon-anchored week and respects
     effective_from / effective_until.
  3. Admin /api/admin/schedule/week response carries
     `recurring_unavailable_blocks[]` for seeded users.
  4. Sales router GET/POST/DELETE /api/sales/schedule/availability —
     stylist self-serve flow; another stylist cannot delete a
     coworker's block.
  5. Publish path:
       - create_entry(publish=True) on an overlapping shift raises
         `recurring_unavailable_conflict`
       - publish_entry on an overlapping draft raises the same
       - publish_week on a mixed week puts the conflicting entry in
         `skipped` with `reason='recurring_unavailable_conflict'`
         and publishes the non-conflicting ones.
  6. expand_shifts attaches `recurring_unavailable_blocks` to each
     day's payload (informational, never suppresses the shift).
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
from database.models import RecurringUnavailability, User  # noqa: E402
from services import (  # noqa: E402
    recurring_availability,
    shift_resolver,
    staff_schedule,
)
from services.recurring_availability import (  # noqa: E402
    RecurringAvailabilityError,
)
from services.staff_schedule import StaffScheduleError  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-recur-{suffix}",
            email=f"{role}-recur-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"Recur {role.title()} {suffix}",
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


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
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


def _expect_error(callable_, code: str) -> RecurringAvailabilityError:
    try:
        callable_()
    except RecurringAvailabilityError as exc:
        assert exc.code == code, (
            f"expected code={code!r}, got {exc.code!r}"
        )
        return exc
    raise AssertionError(
        f"expected RecurringAvailabilityError({code}) — got none"
    )


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    sales_a = _make_user(role="sales")
    sales_b = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_a_hdr = {
        "Authorization": f"Bearer {_token(sales_a, sales=True)}"
    }
    sales_b_hdr = {
        "Authorization": f"Bearer {_token(sales_b, sales=True)}"
    }

    # Pick an isolated future week so we don't collide with other smokes.
    week_start = date(2026, 10, 5)
    assert week_start.isoweekday() == 1

    # ============================================================
    # 1) SERVICE-LEVEL CRUD + invariants
    # ============================================================
    print("===== service CRUD =====")
    db = SessionLocal()
    try:
        # weekday out of range
        _expect_error(
            lambda: recurring_availability.create_block(
                db,
                user_id=sales_a,
                weekday=8,
                start_time_local="09:00",
                end_time_local="10:00",
            ),
            "weekday_out_of_range",
        )

        # end <= start
        _expect_error(
            lambda: recurring_availability.create_block(
                db,
                user_id=sales_a,
                weekday=2,
                start_time_local="17:00",
                end_time_local="09:00",
            ),
            "invalid_time_range",
        )

        # effective_until < effective_from
        _expect_error(
            lambda: recurring_availability.create_block(
                db,
                user_id=sales_a,
                weekday=2,
                start_time_local="09:00",
                end_time_local="10:00",
                effective_from=date(2026, 10, 6),
                effective_until=date(2026, 10, 1),
            ),
            "invalid_effective_range",
        )

        # Happy path: Tue 18:00-21:00 indefinite, with reason
        created = recurring_availability.create_block(
            db,
            user_id=sales_a,
            weekday=2,
            start_time_local=time(18, 0),
            end_time_local=time(21, 0),
            reason="school pickup",
        )
        db.commit()
        assert created["weekday"] == 2
        assert created["start_time_local"] == "18:00"
        assert created["end_time_local"] == "21:00"
        assert created["effective_until"] is None
        assert created["reason"] == "school pickup"

        # Duplicate indefinite same shape → 409
        _expect_error(
            lambda: recurring_availability.create_block(
                db,
                user_id=sales_a,
                weekday=2,
                start_time_local="18:00",
                end_time_local="21:00",
            ),
            "duplicate_active_rule",
        )

        # Non-overlapping second rule on same weekday OK. Seed with
        # historical effective_from so we can also exercise the
        # "filter expired" branch below.
        morning = recurring_availability.create_block(
            db,
            user_id=sales_a,
            weekday=2,
            start_time_local=time(6, 0),
            end_time_local=time(8, 0),
            effective_from=date(2025, 1, 1),
        )
        db.commit()

        # list_for_user filters expired by default
        recurring_availability.set_effective_until(
            db,
            user_id=sales_a,
            block_id=morning["id"],
            effective_until=date(2025, 6, 30),
        )
        db.commit()
        active = recurring_availability.list_for_user(
            db, user_id=sales_a, as_of=date(2026, 10, 5)
        )
        ids = {r["id"] for r in active}
        assert morning["id"] not in ids
        assert created["id"] in ids

        all_rows = recurring_availability.list_for_user(
            db,
            user_id=sales_a,
            include_expired=True,
            as_of=date(2026, 10, 5),
        )
        assert {r["id"] for r in all_rows} >= {
            created["id"],
            morning["id"],
        }

        # find_conflict — overlap on a Tuesday
        conflict_id = recurring_availability.find_conflict(
            db,
            user_id=sales_a,
            starts_at_local=datetime(2026, 10, 6, 17, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 10, 6, 19, 0, tzinfo=tz),
        )
        assert conflict_id == created["id"], (
            f"expected conflict_id={created['id']}, got {conflict_id}"
        )

        # No conflict — same Tuesday but earlier window
        none_id = recurring_availability.find_conflict(
            db,
            user_id=sales_a,
            starts_at_local=datetime(2026, 10, 6, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 10, 6, 12, 0, tzinfo=tz),
        )
        assert none_id is None

        # No conflict — Monday (different weekday)
        none_id = recurring_availability.find_conflict(
            db,
            user_id=sales_a,
            starts_at_local=datetime(2026, 10, 5, 18, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 10, 5, 21, 0, tzinfo=tz),
        )
        assert none_id is None
    finally:
        db.close()

    # ============================================================
    # 2) expand_blocks_for_week — only weekdays it covers
    # ============================================================
    print("===== expand_blocks_for_week =====")
    db = SessionLocal()
    try:
        rows = recurring_availability.expand_blocks_for_week(
            db, week_start=week_start, user_ids=[sales_a, sales_b]
        )
        a_rows = [r for r in rows if r["user_id"] == sales_a]
        # Tue 2026-10-06 is the only weekday the rule materializes on.
        assert len(a_rows) == 1, a_rows
        assert a_rows[0]["business_date"] == "2026-10-06"

        b_rows = [r for r in rows if r["user_id"] == sales_b]
        assert b_rows == []
    finally:
        db.close()

    # ============================================================
    # 3) Admin /week response carries recurring_unavailable_blocks
    # ============================================================
    print("===== admin /week payload =====")
    resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [sales_a, sales_b],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "recurring_unavailable_blocks" in body
    a_blocks = [
        b
        for b in body["recurring_unavailable_blocks"]
        if b["user_id"] == sales_a
    ]
    assert len(a_blocks) == 1
    assert a_blocks[0]["business_date"] == "2026-10-06"

    # ============================================================
    # 4) Sales router CRUD
    # ============================================================
    print("===== sales router =====")
    # GET (B sees zero)
    r = client.get(
        "/api/sales/schedule/availability", headers=sales_b_hdr
    )
    assert r.status_code == 200, r.text
    assert r.json()["blocks"] == []

    # POST a rule as B
    r = client.post(
        "/api/sales/schedule/availability",
        headers=sales_b_hdr,
        json={
            "weekday": 5,
            "start_time_local": "10:00",
            "end_time_local": "12:00",
            "reason": "class",
        },
    )
    assert r.status_code == 201, r.text
    b_block_id = r.json()["id"]

    # GET (B sees one)
    r = client.get(
        "/api/sales/schedule/availability", headers=sales_b_hdr
    )
    assert r.status_code == 200
    blocks = r.json()["blocks"]
    assert len(blocks) >= 1
    assert any(b["id"] == b_block_id for b in blocks)

    # POST duplicate → 409
    r = client.post(
        "/api/sales/schedule/availability",
        headers=sales_b_hdr,
        json={
            "weekday": 5,
            "start_time_local": "10:00",
            "end_time_local": "12:00",
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "duplicate_active_rule"

    # A trying to delete B's block → 404
    r = client.delete(
        f"/api/sales/schedule/availability/{b_block_id}",
        headers=sales_a_hdr,
    )
    assert r.status_code == 404

    # B deletes own → 204
    r = client.delete(
        f"/api/sales/schedule/availability/{b_block_id}",
        headers=sales_b_hdr,
    )
    assert r.status_code == 204

    # ============================================================
    # 5) Publish path — conflict with recurring rule
    # ============================================================
    print("===== publish conflict =====")
    db = SessionLocal()
    conflicting_draft_id: int
    safe_draft_id: int
    try:
        # Draft on Tue 2026-10-06 19:00-22:00 — overlaps A's 18-21 rule.
        bad = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a,
            business_date_=date(2026, 10, 6),
            starts_at_local=datetime(2026, 10, 6, 19, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 10, 6, 22, 0, tzinfo=tz),
        )
        # Draft on Mon — no conflict.
        good = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a,
            business_date_=week_start,
            starts_at_local=datetime(2026, 10, 5, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 10, 5, 17, 0, tzinfo=tz),
        )
        db.commit()
        conflicting_draft_id = bad["id"]
        safe_draft_id = good["id"]

        # create_entry(publish=True) directly on the conflict window → raises
        try:
            staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a,
                business_date_=date(2026, 10, 6),
                starts_at_local=datetime(2026, 10, 6, 18, 30, tzinfo=tz),
                ends_at_local=datetime(2026, 10, 6, 20, 30, tzinfo=tz),
                publish=True,
            )
        except StaffScheduleError as exc:
            assert exc.code == "recurring_unavailable_conflict", exc.code
            assert (
                exc.extra.get("recurring_unavailable_block_id") is not None
            )
        else:
            raise AssertionError(
                "create_entry(publish=True) should have raised "
                "recurring_unavailable_conflict"
            )

        # publish_entry on the conflicting draft → raises
        try:
            staff_schedule.publish_entry(
                db,
                actor_user_id=admin_id,
                entry_id=conflicting_draft_id,
            )
        except StaffScheduleError as exc:
            assert exc.code == "recurring_unavailable_conflict", exc.code
        else:
            raise AssertionError(
                "publish_entry should have raised "
                "recurring_unavailable_conflict"
            )

        # publish_week — partial publish: good goes through, bad skipped.
        result = staff_schedule.publish_week(
            db,
            actor_user_id=admin_id,
            week_start=week_start,
            user_ids=[sales_a],
        )
        db.commit()
        assert safe_draft_id in result["entry_ids"], result
        skipped_ids = {row["entry_id"] for row in result["skipped"]}
        assert conflicting_draft_id in skipped_ids, result
        bad_row = next(
            r for r in result["skipped"] if r["entry_id"] == conflicting_draft_id
        )
        assert bad_row["reason"] == "recurring_unavailable_conflict", bad_row
    finally:
        db.close()

    # ============================================================
    # 6) expand_shifts attaches recurring_unavailable_blocks
    # ============================================================
    print("===== expand_shifts payload =====")
    db = SessionLocal()
    try:
        days = shift_resolver.expand_shifts(
            db,
            user_id=sales_a,
            from_date=week_start,
            to_date=week_start + timedelta(days=6),
        )
        # Tuesday entry has one recurring block; other days have zero.
        tue = next(d for d in days if d["business_date"] == "2026-10-06")
        assert len(tue["recurring_unavailable_blocks"]) == 1
        mon = next(d for d in days if d["business_date"] == "2026-10-05")
        assert mon["recurring_unavailable_blocks"] == []
    finally:
        db.close()

    print("recurring_availability smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
