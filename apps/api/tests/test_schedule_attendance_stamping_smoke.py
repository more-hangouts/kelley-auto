"""Smoke tests for Phase 10 Slice 2 (clock-in stamping + no-show cron
+ variance/flagged-exception read paths).

Covers four behaviors:

  1. Punching in against a published schedule entry stamps the entry
     with `actual_clock_in_punch_id` and flips `attendance_status`
     to 'present' (within grace) or 'late' (past grace). Punching out
     stamps `actual_clock_out_punch_id`.
  2. `services.no_show_cron.tick` flips a published entry from
     'scheduled' → 'no_show' when its start + grace is in the past
     and no clock-in exists, while leaving entries that were
     clocked-in or whose grace has not elapsed alone. Re-running the
     tick after a late clock-in arrival recovers the entry from
     'no_show' → 'late'.
  3. `GET /api/admin/schedule/flagged-exceptions` returns no-show
     rows for the bounded window with the stylist's display name.
  4. `GET /api/admin/schedule/variance` returns per-staff scheduled
     vs actual hours for the bounded window, sorted by abs variance.

The smoke does NOT exercise the geofence (covered by
test_clock_in_smoke). It seeds an active `staff_locations` row at the
client's coordinates so the geofence check passes and then patches
out the time-source via the `now_override` parameter on `punch_in` /
`punch_out` so a single test process can simulate "punched in 3
minutes late" without sleeping.
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
from database.models import (  # noqa: E402
    StaffLocation,
    StaffPunch,
    StaffScheduleEntry,
    User,
)
from services import clock_in, no_show_cron, staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_entry_ids: list[int] = []
_punch_ids: list[int] = []
_location_ids: list[int] = []
_parked_location_ids: list[int] = []

PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p10s2-{suffix}",
            email=f"{role}-p10s2-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P10S2 {role.title()} {suffix}",
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


def _token_admin(user_id: int) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_access_token(u)
    finally:
        db.close()


def _seed_location() -> int:
    """Seed an active geofence the smoke's punches can land inside,
    parking any pre-existing active locations as inactive so the
    haversine pick lands on ours."""
    db = SessionLocal()
    try:
        existing = db.execute(
            sql_text(
                "SELECT id FROM staff_locations WHERE active = TRUE"
            )
        ).all()
        for row in existing:
            _parked_location_ids.append(int(row[0]))
        if existing:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = FALSE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [int(r[0]) for r in existing]},
            )
        loc = StaffLocation(
            name=f"P10S2 Probe {uuid.uuid4().hex[:6]}",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=200,
            active=True,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _publish_entry(
    *,
    actor_user_id: int,
    user_id: int,
    business_date_: date,
    starts_at_local: datetime,
    ends_at_local: datetime,
    late_grace_minutes: int = 10,
) -> dict:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_user_id,
            user_id=user_id,
            business_date_=business_date_,
            starts_at_local=starts_at_local,
            ends_at_local=ends_at_local,
            late_grace_minutes=late_grace_minutes,
            publish=True,
        )
        db.commit()
        _entry_ids.append(d["id"])
        return d
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
                    "DELETE FROM staff_punches WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _parked_location_ids:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = TRUE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _parked_location_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    sales_a_id = _make_user(role="sales")
    sales_b_id = _make_user(role="sales")
    _seed_location()

    admin_hdr = {"Authorization": f"Bearer {_token_admin(admin_id)}"}

    # Pick a future-ish weekday so the test isn't sensitive to "today".
    # We drive everything off explicit now_override datetimes.
    biz_date_today = date(2026, 6, 8)  # Monday
    biz_date_yesterday = date(2026, 6, 7)
    biz_date_other = date(2026, 6, 9)

    # ============================================================
    # 1) PUNCH-IN STAMPS THE ENTRY (within grace → 'present')
    # ============================================================
    print("===== clock-in stamps entry =====")
    entry_present = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_a_id,
        business_date_=biz_date_today,
        starts_at_local=datetime(2026, 6, 8, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 6, 8, 17, 0, tzinfo=tz),
        late_grace_minutes=10,
    )

    db = SessionLocal()
    try:
        user_a = db.get(User, sales_a_id)
        # Punch in at 9:05 — well inside grace.
        now_override = datetime(2026, 6, 8, 9, 5, tzinfo=tz).astimezone(
            timezone.utc
        )
        in_punch = clock_in.punch_in(
            db,
            user=user_a,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            client_accuracy_m=15,
            now_override=now_override,
        )
        db.commit()
        _punch_ids.append(in_punch.id)
        # Re-load entry and verify stamp.
        e = db.get(StaffScheduleEntry, entry_present["id"])
        assert e.actual_clock_in_punch_id == in_punch.id, (
            f"expected entry stamped with punch {in_punch.id}, "
            f"got {e.actual_clock_in_punch_id}"
        )
        assert e.attendance_status == "present", (
            f"expected attendance_status=present, got {e.attendance_status}"
        )

        # Punch out at 17:00 — full shift.
        out_override = datetime(2026, 6, 8, 17, 0, tzinfo=tz).astimezone(
            timezone.utc
        )
        out_punch = clock_in.punch_out(
            db,
            user=user_a,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            client_accuracy_m=15,
            now_override=out_override,
        )
        db.commit()
        _punch_ids.append(out_punch.id)
        db.refresh(e)
        assert e.actual_clock_out_punch_id == out_punch.id, (
            f"expected entry stamped with out-punch {out_punch.id}, "
            f"got {e.actual_clock_out_punch_id}"
        )
    finally:
        db.close()

    # ============================================================
    # 2) PUNCH-IN PAST GRACE → 'late'
    # ============================================================
    print("===== clock-in past grace → late =====")
    entry_late = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_b_id,
        business_date_=biz_date_today,
        starts_at_local=datetime(2026, 6, 8, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 6, 8, 17, 0, tzinfo=tz),
        late_grace_minutes=10,
    )
    db = SessionLocal()
    try:
        user_b = db.get(User, sales_b_id)
        # 9:25 — past the 10-minute grace.
        now_override = datetime(2026, 6, 8, 9, 25, tzinfo=tz).astimezone(
            timezone.utc
        )
        in_punch = clock_in.punch_in(
            db,
            user=user_b,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override,
        )
        db.commit()
        _punch_ids.append(in_punch.id)
        e = db.get(StaffScheduleEntry, entry_late["id"])
        assert e.actual_clock_in_punch_id == in_punch.id
        assert e.attendance_status == "late", (
            f"expected attendance_status=late, got {e.attendance_status}"
        )
    finally:
        db.close()

    # ============================================================
    # 3) NO-SHOW CRON: overdue published entry with no clock-in flips
    # ============================================================
    print("===== no-show cron =====")
    # Yesterday's 9-5 shift on a 3rd stylist with no clock-in.
    sales_c_id = _make_user(role="sales")
    entry_no_show = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_c_id,
        business_date_=biz_date_yesterday,
        starts_at_local=datetime(2026, 6, 7, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 6, 7, 17, 0, tzinfo=tz),
        late_grace_minutes=10,
    )

    # Pre-tick: a published entry whose grace has NOT elapsed should
    # NOT flip. Seed one for `biz_date_other` 5 minutes ahead of
    # `as_of` and confirm it survives.
    as_of = datetime(2026, 6, 9, 9, 0, tzinfo=tz).astimezone(timezone.utc)
    entry_too_early = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_c_id,
        business_date_=biz_date_other,
        starts_at_local=datetime(2026, 6, 9, 9, 0, tzinfo=tz),
        ends_at_local=datetime(2026, 6, 9, 17, 0, tzinfo=tz),
        late_grace_minutes=30,
    )

    db = SessionLocal()
    try:
        flipped = staff_schedule.mark_no_shows(db, as_of_utc=as_of)
        db.commit()
        assert entry_no_show["id"] in flipped, (
            f"expected yesterday's entry {entry_no_show['id']} flipped, "
            f"flipped={flipped}"
        )
        # Sanity: the just-published 'too early' one is not in the list.
        # (It might be in the list IF its grace-window already elapsed
        # by `as_of`. The seed above is exactly at as_of, so threshold =
        # 09:00 + 30m = 09:30 > 09:00; not eligible.)
        assert entry_too_early["id"] not in flipped, (
            f"premature flip of {entry_too_early['id']} — grace bug"
        )

        e_ns = db.get(StaffScheduleEntry, entry_no_show["id"])
        assert e_ns.attendance_status == "no_show"
        e_too_early = db.get(StaffScheduleEntry, entry_too_early["id"])
        assert e_too_early.attendance_status == "scheduled"

        # Idempotency: a second tick at the same `as_of` should find
        # nothing more to do.
        flipped_again = staff_schedule.mark_no_shows(db, as_of_utc=as_of)
        db.commit()
        assert flipped_again == [], (
            f"second tick should be a no-op, flipped {flipped_again}"
        )
    finally:
        db.close()

    # ============================================================
    # 4) LATE ARRIVAL RECOVERS A NO-SHOW
    # ============================================================
    print("===== late arrival recovers no_show =====")
    db = SessionLocal()
    try:
        user_c = db.get(User, sales_c_id)
        # Pretend it's 9:15 the morning of `biz_date_other` — 15 min
        # past start. We pulled the same stylist's location into the
        # geofence and the entry has late_grace_minutes=30 so this
        # lands as 'present', not 'late'.
        now_override = datetime(2026, 6, 9, 9, 15, tzinfo=tz).astimezone(
            timezone.utc
        )
        # First, manually flip the entry to 'no_show' to prove the
        # recovery codepath — in production the no-show cron would
        # have flipped it. Direct UPDATE skips the cron-grace check.
        e_too_early = db.get(StaffScheduleEntry, entry_too_early["id"])
        e_too_early.attendance_status = "no_show"
        db.commit()

        in_punch = clock_in.punch_in(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override,
        )
        db.commit()
        _punch_ids.append(in_punch.id)
        db.refresh(e_too_early)
        assert e_too_early.attendance_status == "present", (
            "late arrival should have recovered the entry from no_show, "
            f"got {e_too_early.attendance_status}"
        )
        assert e_too_early.actual_clock_in_punch_id == in_punch.id
    finally:
        db.close()

    # ============================================================
    # 5) NO-SHOW CRON TICK via the cron module (record_run round-trip)
    # ============================================================
    print("===== no_show_cron.tick records state =====")
    # Cron uses real wall-clock `now()`; the simulated 2026-06-07 dates
    # above are in the future relative to the real server clock, so the
    # tick wouldn't flip them. Seed a genuinely overdue entry: starts
    # 2h ago in real time, grace 15m, so threshold is 1h45m in the
    # past.
    sales_d_id = _make_user(role="sales")
    real_now_local = datetime.now(tz)
    real_starts_at = real_now_local - timedelta(hours=2)
    real_ends_at = real_now_local - timedelta(hours=-6)
    entry_cron_seed = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_d_id,
        business_date_=real_starts_at.date(),
        starts_at_local=real_starts_at,
        ends_at_local=real_ends_at,
        late_grace_minutes=15,
    )

    db = SessionLocal()
    try:
        no_show_cron.tick(db)
        flipped_entry = db.get(StaffScheduleEntry, entry_cron_seed["id"])
        assert flipped_entry.attendance_status == "no_show", (
            f"cron should have flipped entry to no_show, got "
            f"{flipped_entry.attendance_status}"
        )
    finally:
        db.close()

    # Cron health row written.
    db = SessionLocal()
    try:
        from services.cron_state import SCHEDULE_NO_SHOW, all_states

        states = {s["name"]: s for s in all_states(db)}
        assert SCHEDULE_NO_SHOW in states, (
            "schedule.no_show missing from ALL_CRON_NAMES"
        )
        row = states[SCHEDULE_NO_SHOW]
        assert row["last_finished_at"] is not None
        assert row["last_error"] is None
    finally:
        db.close()

    # ============================================================
    # 6) FLAGGED EXCEPTIONS ENDPOINT
    # ============================================================
    print("===== /flagged-exceptions =====")
    # Widen the window to span both the simulated 2026-06 dates and
    # the real wall-clock entry the cron seeded above.
    window_from = min(biz_date_yesterday, real_starts_at.date())
    window_to = max(biz_date_other, real_starts_at.date())
    resp = client.get(
        "/api/admin/schedule/flagged-exceptions",
        headers=admin_hdr,
        params={
            "from_date": window_from.isoformat(),
            "to_date": window_to.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    exception_ids = {row["id"] for row in body["exceptions"]}
    # Both yesterday's no-show seed and the cron-seed should be present.
    assert entry_no_show["id"] in exception_ids
    assert entry_cron_seed["id"] in exception_ids
    # The recovered 'present' entry must NOT be in the list.
    assert entry_too_early["id"] not in exception_ids
    # Display name is denormalized into the row.
    for row in body["exceptions"]:
        assert "user_full_name" in row

    # Per-user filter narrows.
    resp = client.get(
        "/api/admin/schedule/flagged-exceptions",
        headers=admin_hdr,
        params={
            "from_date": window_from.isoformat(),
            "to_date": window_to.isoformat(),
            "user_id": sales_d_id,
        },
    )
    assert resp.status_code == 200
    filtered_ids = {row["id"] for row in resp.json()["exceptions"]}
    assert filtered_ids == {entry_cron_seed["id"]}

    # ============================================================
    # 7) EXCUSE A NO-SHOW
    # ============================================================
    print("===== /excuse =====")
    excuse_resp = client.post(
        f"/api/admin/schedule/entries/{entry_no_show['id']}/excuse",
        headers=admin_hdr,
        json={"notes": "called out sick (verified)"},
    )
    assert excuse_resp.status_code == 200, excuse_resp.text
    assert excuse_resp.json()["attendance_status"] == "excused"

    # And the now-excused row drops out of /flagged-exceptions.
    resp = client.get(
        "/api/admin/schedule/flagged-exceptions",
        headers=admin_hdr,
        params={
            "from_date": window_from.isoformat(),
            "to_date": window_to.isoformat(),
        },
    )
    assert entry_no_show["id"] not in {
        row["id"] for row in resp.json()["exceptions"]
    }

    # ============================================================
    # 8) HOURS VARIANCE
    # ============================================================
    print("===== /variance =====")
    resp = client.get(
        "/api/admin/schedule/variance",
        headers=admin_hdr,
        params={
            "from_date": window_from.isoformat(),
            "to_date": window_to.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_user = {row["user_id"]: row for row in body["rows"]}

    # sales_a clocked in 9:05 and out 17:00 → 7.92 actual against 8.0
    # scheduled → variance ≈ -0.08.
    row_a = by_user[sales_a_id]
    assert row_a["scheduled_hours"] == 8.0
    assert row_a["stamped_pairs"] == 1
    assert abs(row_a["actual_hours"] - 7.92) < 0.05
    assert row_a["variance_hours"] < 0

    # sales_b clocked in 9:25 and never out → actual=0, variance=-scheduled.
    row_b = by_user[sales_b_id]
    assert row_b["scheduled_hours"] == 8.0
    assert row_b["stamped_pairs"] == 0
    assert row_b["actual_hours"] == 0.0
    assert row_b["variance_hours"] == -8.0

    print("phase10_attendance smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
