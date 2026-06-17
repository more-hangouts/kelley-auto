"""Smoke tests for Phase 8 Slice B (shift resolver + cron integration).

Maps directly to the 8 invariants the user listed when greenlighting
Slice B:

  1. Override wins over assigned/base shift.
  2. Assigned/base shift wins over location default.
  3. Approved time off suppresses a shift read-side.
  4. Earliest check-in blocks before
     `starts_at - earliest_check_in_minutes`.
  5. Late status lands after
     `starts_at + late_grace_period_minutes`.
  6. Early-out status lands before
     `ends_at - early_out_grace_minutes`.
  7. Holiday tagging stamps `holiday_id` without blocking.
  8. Pre-close and auto-close choose shift cutoff first, then fall
     back to location cutoff.

Plus a 9th overnight-shift expansion check (the user explicitly
asked for that early in Slice A but the smoke lives here because
the resolver is what carries duration through midnight).

The resolver bypasses the geofence for cleanly-seeded test punches by
calling the service directly (`punch_in(db, ...)`) rather than going
through HTTP — geofence is already covered by the Slice 1 smoke and
not the focus here.
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import select, text as sql_text  # noqa: E402

from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    StaffHoliday,
    StaffLocation,
    StaffPunch,
    StaffShift,
    StaffShiftOverride,
    TimeOffRequest,
    User,
)
from services import (  # noqa: E402
    attendance_close,
    attendance_pre_close,
    clock_in,
    shift_resolver,
)
from services.clock_in import ClockInError  # noqa: E402

_user_ids: list[int] = []
_location_ids: list[int] = []
_shift_ids: list[int] = []
_override_ids: list[int] = []
_holiday_ids: list[int] = []


PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000


def _make_user(suffix: str) -> int:
    db = SessionLocal()
    try:
        u = User(
            username=f"sales-p8b-{suffix}",
            email=f"sales-p8b-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P8B Sales {suffix}",
            is_active=True,
            role="sales",
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


def _seed_location(*, default_close: time | None = None) -> int:
    db = SessionLocal()
    try:
        loc = StaffLocation(
            name=f"P8B Probe {uuid.uuid4().hex[:6]}",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=100,
            default_auto_session_close_time=default_close,
            active=True,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _seed_shift(
    *,
    user_id: int,
    starts_at: datetime,
    ends_at: datetime,
    working_days: list[int],
    location_id: int | None = None,
    late_grace: int = 0,
    earliest_check_in: int = 120,
    early_out_grace: int = 0,
    auto_session_close: time | None = None,
    max_session_hours: float | None = None,
) -> int:
    db = SessionLocal()
    try:
        shift = StaffShift(
            user_id=user_id,
            location_id=location_id,
            starts_at=starts_at,
            ends_at=ends_at,
            working_days=working_days,
            late_grace_period_minutes=late_grace,
            earliest_check_in_minutes=earliest_check_in,
            early_out_grace_minutes=early_out_grace,
            auto_session_close_time=auto_session_close,
            max_session_hours=max_session_hours,
        )
        db.add(shift)
        db.commit()
        db.refresh(shift)
        _shift_ids.append(shift.id)
        return shift.id
    finally:
        db.close()


def _seed_override(
    *,
    user_id: int,
    shift_id: int,
    starts_on: date,
    ends_on: date,
) -> int:
    db = SessionLocal()
    try:
        ov = StaffShiftOverride(
            user_id=user_id,
            shift_id=shift_id,
            starts_on=starts_on,
            ends_on=ends_on,
        )
        db.add(ov)
        db.commit()
        db.refresh(ov)
        _override_ids.append(ov.id)
        return ov.id
    finally:
        db.close()


def _seed_holiday(
    *,
    name: str,
    holiday_date: date,
    location_id: int | None = None,
) -> int:
    db = SessionLocal()
    try:
        h = StaffHoliday(
            name=name,
            holiday_date=holiday_date,
            location_id=location_id,
            is_paid=True,
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        _holiday_ids.append(h.id)
        return h.id
    finally:
        db.close()


def _seed_approved_time_off(
    *, user_id: int, starts_at: datetime, ends_at: datetime
) -> int:
    db = SessionLocal()
    try:
        r = TimeOffRequest(
            user_id=user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            status="approved",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        return r.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE actor_user_id = ANY(:uids) OR punch_id IN ("
                    "SELECT id FROM staff_punches WHERE user_id = ANY(:uids))"
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
                sql_text(
                    "DELETE FROM time_off_decision_events "
                    "WHERE request_id IN (SELECT id FROM time_off_requests "
                    "WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_requests WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_overrides "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shifts WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
        if _holiday_ids:
            db.execute(
                sql_text("DELETE FROM staff_holidays WHERE id = ANY(:ids)"),
                {"ids": _holiday_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    today = datetime.now(tz).date()
    # Use a future Monday so weekday math is deterministic regardless
    # of when the smoke runs.
    while today.isoweekday() != 1:
        today = today + timedelta(days=1)
    monday = today  # 1 = Monday
    saturday = monday + timedelta(days=5)  # 6 = Saturday

    user_id = _make_user(uuid.uuid4().hex[:6])
    location_id = _seed_location(default_close=time(22, 0))

    # ============================================================
    # 1) OVERRIDE WINS OVER ASSIGNED/BASE SHIFT
    # ============================================================
    print("===== invariant 1: override > base =====")
    base_shift_id = _seed_shift(
        user_id=user_id,
        starts_at=datetime(monday.year, monday.month, monday.day, 9, 0, tzinfo=tz),
        ends_at=datetime(monday.year, monday.month, monday.day, 17, 0, tzinfo=tz),
        working_days=[1, 2, 3, 4, 5],  # Mon-Fri 9-5
        location_id=location_id,
        late_grace=10,
        earliest_check_in=60,
        early_out_grace=10,
        auto_session_close=time(22, 0),
        max_session_hours=12,
    )
    saturday_cover_shift_id = _seed_shift(
        user_id=user_id,
        starts_at=datetime(saturday.year, saturday.month, saturday.day, 11, 0, tzinfo=tz),
        ends_at=datetime(saturday.year, saturday.month, saturday.day, 19, 0, tzinfo=tz),
        # Note: working_days deliberately does NOT include Saturday.
        # The override applies anyway because overrides are "the
        # schedule for these dates," not "filtered by working_days."
        working_days=[6],  # Saturday so we can also test that path later
        location_id=location_id,
        late_grace=15,
        earliest_check_in=90,
        early_out_grace=15,
        auto_session_close=time(20, 30),
        max_session_hours=10,
    )
    _seed_override(
        user_id=user_id,
        shift_id=saturday_cover_shift_id,
        starts_on=monday,
        ends_on=monday,  # one-day override on a Monday
    )

    db = SessionLocal()
    try:
        # On Monday, the override should win even though the base
        # 9-5 shift covers this weekday.
        as_of = datetime(monday.year, monday.month, monday.day, 12, 0, tzinfo=tz)
        resolved = shift_resolver.resolve_active_shift(
            db, user_id=user_id, as_of_local=as_of
        )
        assert resolved is not None
        assert resolved.is_override is True
        assert resolved.shift_id == saturday_cover_shift_id, resolved.shift_id
        # Override carries the override-shift's grace settings.
        assert resolved.late_grace_period_minutes == 15
        assert resolved.starts_at_local.hour == 11

        # On Tuesday (no override) the base wins.
        tuesday = monday + timedelta(days=1)
        as_of_tue = datetime(tuesday.year, tuesday.month, tuesday.day, 12, 0, tzinfo=tz)
        resolved_tue = shift_resolver.resolve_active_shift(
            db, user_id=user_id, as_of_local=as_of_tue
        )
        assert resolved_tue is not None
        assert resolved_tue.is_override is False
        assert resolved_tue.shift_id == base_shift_id
        assert resolved_tue.starts_at_local.hour == 9
    finally:
        db.close()

    # ============================================================
    # 2) ASSIGNED/BASE SHIFT WINS OVER LOCATION DEFAULT
    # ============================================================
    print("===== invariant 2: base > location default =====")
    # The location's default_auto_session_close_time = 22:00; the
    # base shift's auto_session_close_time = 22:00 (same value here,
    # but the assertion is about source). We swap the base shift's
    # close time to 21:00 so the resolver path is observably distinct.
    db = SessionLocal()
    try:
        base = db.get(StaffShift, base_shift_id)
        base.auto_session_close_time = time(21, 0)
        base.max_session_hours = 10
        db.commit()
    finally:
        db.close()

    # Seed an in-punch on Tuesday at 13:00 local — solidly within the
    # shift's max_session_hours=10 window when `now` lands at 21:30,
    # so `past_date` fires at the shift's 21:00 cutoff rather than
    # max_time_reached firing first. The intent of this invariant is
    # "shift cutoff > location cutoff," which we can only observe when
    # past_date wins the close decision.
    tuesday = monday + timedelta(days=1)
    in_local = datetime(tuesday.year, tuesday.month, tuesday.day, 13, 0, tzinfo=tz)
    db = SessionLocal()
    try:
        p = StaffPunch(
            user_id=user_id,
            direction="in",
            punched_at=in_local.astimezone(timezone.utc),
            status="recorded",
            location_id=location_id,
            shift_id=base_shift_id,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        in_punch_id = p.id
    finally:
        db.close()

    fake_now = datetime(tuesday.year, tuesday.month, tuesday.day, 21, 30, tzinfo=tz)
    db = SessionLocal()
    try:
        result = attendance_close.run_auto_close_pass(
            db, now_override=fake_now.astimezone(timezone.utc)
        )
        db.commit()
    finally:
        db.close()
    # `run_auto_close_pass` walks every user, so residue open sessions
    # from a partial prior smoke run inflate `result.closed`. Assert
    # `>= 1` and verify the per-user state below — that's what
    # actually proves the invariant.
    assert result.closed >= 1, result

    db = SessionLocal()
    try:
        outs = (
            db.execute(
                select(StaffPunch)
                .where(StaffPunch.user_id == user_id)
                .where(StaffPunch.direction == "out")
            )
            .scalars()
            .all()
        )
        assert len(outs) == 1
        out_punch = outs[0]
        # The cutoff is the shift's 21:00, so punched_at on the auto
        # out should be 21:00 local, NOT 22:00 (the location default).
        out_local = out_punch.punched_at.astimezone(tz)
        assert out_local.time() == time(21, 0), (
            f"expected shift cutoff 21:00, got {out_local.time()}"
        )
        assert out_punch.auto_close_reason == "past_date"
    finally:
        db.close()

    # ============================================================
    # 3) APPROVED TIME OFF SUPPRESSES A SHIFT (READ-SIDE)
    # ============================================================
    print("===== invariant 3: time off suppresses =====")
    wednesday = monday + timedelta(days=2)
    thursday = monday + timedelta(days=3)
    # Approved time off Tue-Wed.
    _seed_approved_time_off(
        user_id=user_id,
        starts_at=datetime(tuesday.year, tuesday.month, tuesday.day, 0, 0, tzinfo=tz),
        ends_at=datetime(thursday.year, thursday.month, thursday.day, 0, 0, tzinfo=tz),
    )

    db = SessionLocal()
    try:
        days = shift_resolver.expand_shifts(
            db,
            user_id=user_id,
            from_date=monday,
            to_date=monday + timedelta(days=4),
            suppress_time_off=True,
        )
    finally:
        db.close()

    by_date = {d["business_date"]: d for d in days}
    assert by_date[monday.isoformat()]["shift"] is not None  # override day
    assert by_date[tuesday.isoformat()]["time_off_suppressed"] is True
    assert by_date[tuesday.isoformat()]["shift"] is None
    assert by_date[wednesday.isoformat()]["time_off_suppressed"] is True
    # ends_at midnight Thursday means Thursday is back on the schedule.
    assert by_date[thursday.isoformat()]["time_off_suppressed"] is False
    assert by_date[thursday.isoformat()]["shift"] is not None

    # Cron-facing resolver IGNORES time-off suppression so a stylist
    # who somehow punched in still gets auto-closed.
    db = SessionLocal()
    try:
        as_of_tue = datetime(tuesday.year, tuesday.month, tuesday.day, 12, 0, tzinfo=tz)
        cron_resolved = shift_resolver.resolve_active_shift(
            db, user_id=user_id, as_of_local=as_of_tue
        )
        assert cron_resolved is not None
        # The base shift still resolves on a time-off day — the cron
        # is supposed to be defensive, not deferential.
    finally:
        db.close()

    # ============================================================
    # 4) EARLIEST CHECK-IN BLOCKS BEFORE
    #    `starts_at - earliest_check_in_minutes`
    # ============================================================
    print("===== invariant 4: earliest check-in =====")
    next_friday = monday + timedelta(days=4)
    too_early_now = datetime(
        next_friday.year, next_friday.month, next_friday.day, 7, 0, tzinfo=tz
    )
    # Base 9-5 with earliest_check_in_minutes=60 → can clock in at 8:00.
    # 7:00 is too early.
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        try:
            clock_in.punch_in(
                db,
                user=u,
                client_lat=PROBE_LAT,
                client_lng=PROBE_LNG,
                now_override=too_early_now,
            )
        except ClockInError as exc:
            assert exc.code == "too_early_for_shift", exc.code
            assert exc.http_status == 403
            assert "earliest_allowed_at" in exc.extra
            # Earliest = 8:00 local (60 min before 9:00).
            assert (
                exc.extra["earliest_allowed_at"].split("T")[1].startswith("08:00")
            ), exc.extra["earliest_allowed_at"]
        else:
            db.rollback()
            raise AssertionError(
                "earliest check-in did not block 7:00 punch"
            )
        db.rollback()
    finally:
        db.close()

    # 8:30 (30 min before, inside the 60-minute earliest window) is fine.
    on_time_now = datetime(
        next_friday.year, next_friday.month, next_friday.day, 8, 30, tzinfo=tz
    )
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch = clock_in.punch_in(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=on_time_now,
        )
        db.commit()
        # Status: 8:30 is before 9:00 + 10min grace, so 'recorded'.
        assert punch.status == "recorded", punch.status
        assert punch.shift_id == base_shift_id
    finally:
        db.close()

    # Punch out same day at 17:30 — past ends_at (17:00) and past
    # early-out threshold (16:50) → 'recorded'.
    out_clean = datetime(
        next_friday.year, next_friday.month, next_friday.day, 17, 30, tzinfo=tz
    )
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch_out = clock_in.punch_out(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=out_clean,
        )
        db.commit()
        assert punch_out.status == "recorded", punch_out.status
    finally:
        db.close()

    # ============================================================
    # 5) LATE STATUS LANDS AFTER
    #    `starts_at + late_grace_period_minutes`
    # ============================================================
    print("===== invariant 5: late =====")
    # Use the next Friday + 7 (next-next Friday) for a fresh punch.
    later_friday = next_friday + timedelta(days=7)
    late_now = datetime(
        later_friday.year, later_friday.month, later_friday.day, 9, 15, tzinfo=tz
    )
    # 9:00 + 10min grace = 9:10 threshold; 9:15 is past it → 'late'.
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch = clock_in.punch_in(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=late_now,
        )
        db.commit()
        assert punch.status == "late", punch.status
    finally:
        db.close()

    # ============================================================
    # 6) EARLY-OUT STATUS LANDS BEFORE
    #    `ends_at - early_out_grace_minutes`
    # ============================================================
    print("===== invariant 6: early_out =====")
    # 17:00 ends_at - 10min grace = 16:50 threshold; 16:30 is before
    # → 'early_out'.
    early_out_now = datetime(
        later_friday.year, later_friday.month, later_friday.day, 16, 30, tzinfo=tz
    )
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch = clock_in.punch_out(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=early_out_now,
        )
        db.commit()
        assert punch.status == "early_out", punch.status
    finally:
        db.close()

    # ============================================================
    # 7) HOLIDAY TAGGING STAMPS holiday_id WITHOUT BLOCKING
    # ============================================================
    print("===== invariant 7: holiday tagging =====")
    holiday_friday = later_friday + timedelta(days=7)
    # A per-location holiday on this date.
    location_holiday_id = _seed_holiday(
        name="Boutique Anniversary",
        holiday_date=holiday_friday,
        location_id=location_id,
    )
    # A global holiday with a different name on the same date — the
    # per-location row should win.
    global_holiday_id = _seed_holiday(
        name="National Day",
        holiday_date=holiday_friday,
        location_id=None,
    )

    on_holiday_now = datetime(
        holiday_friday.year,
        holiday_friday.month,
        holiday_friday.day,
        9,
        0,
        tzinfo=tz,
    )
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch = clock_in.punch_in(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=on_holiday_now,
        )
        db.commit()
        assert punch.holiday_id == location_holiday_id, (
            f"expected per-location holiday {location_holiday_id}, "
            f"got {punch.holiday_id}"
        )
        # The punch was NOT blocked.
        assert punch.id is not None
        # Status was still classified normally (no special "holiday"
        # status — that's a reporting concern, not a blocking one).
        assert punch.status in ("recorded", "late")
    finally:
        db.close()

    # Punch out so we can verify the global-only fallback later.
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        clock_in.punch_out(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=datetime(
                holiday_friday.year,
                holiday_friday.month,
                holiday_friday.day,
                17,
                0,
                tzinfo=tz,
            ),
        )
        db.commit()
    finally:
        db.close()

    # Global-only fallback: delete the per-location row, punch on a
    # different day with only a global holiday → punch should pick up
    # the global holiday id.
    only_global_friday = holiday_friday + timedelta(days=7)
    only_global_id = _seed_holiday(
        name="Global Only",
        holiday_date=only_global_friday,
        location_id=None,
    )
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        punch = clock_in.punch_in(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=datetime(
                only_global_friday.year,
                only_global_friday.month,
                only_global_friday.day,
                9,
                0,
                tzinfo=tz,
            ),
        )
        db.commit()
        assert punch.holiday_id == only_global_id, (
            f"expected global fallback {only_global_id}, got {punch.holiday_id}"
        )
    finally:
        db.close()

    # And a clean day with no holidays gets None.
    no_holiday_friday = only_global_friday + timedelta(days=7)
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        # Punch out first so we can punch in cleanly.
        clock_in.punch_out(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=datetime(
                only_global_friday.year,
                only_global_friday.month,
                only_global_friday.day,
                17,
                0,
                tzinfo=tz,
            ),
        )
        db.commit()

        punch = clock_in.punch_in(
            db,
            user=u,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=datetime(
                no_holiday_friday.year,
                no_holiday_friday.month,
                no_holiday_friday.day,
                9,
                0,
                tzinfo=tz,
            ),
        )
        db.commit()
        assert punch.holiday_id is None
    finally:
        db.close()

    # ============================================================
    # 8) PRE-CLOSE + AUTO-CLOSE: SHIFT CUTOFF FIRST, LOCATION FALLBACK
    # ============================================================
    print("===== invariant 8: cron precedence =====")

    # Fresh user with NO shift (location-default-only).
    user_b_id = _make_user(uuid.uuid4().hex[:6])
    user_b_punch_day = no_holiday_friday + timedelta(days=7)
    in_local_b = datetime(
        user_b_punch_day.year,
        user_b_punch_day.month,
        user_b_punch_day.day,
        10,
        0,
        tzinfo=tz,
    )
    db = SessionLocal()
    try:
        p = StaffPunch(
            user_id=user_b_id,
            direction="in",
            punched_at=in_local_b.astimezone(timezone.utc),
            status="recorded",
            location_id=location_id,
        )
        db.add(p)
        db.commit()
    finally:
        db.close()

    # now_override = 22:30 local on user_b's day. Location default is
    # 22:00 → past_date fires using the location's value.
    fake_now = datetime(
        user_b_punch_day.year,
        user_b_punch_day.month,
        user_b_punch_day.day,
        22,
        30,
        tzinfo=tz,
    ).astimezone(timezone.utc)
    db = SessionLocal()
    try:
        result = attendance_close.run_auto_close_pass(
            db, now_override=fake_now
        )
        db.commit()
    finally:
        db.close()
    assert result.closed >= 1, result

    db = SessionLocal()
    try:
        outs = (
            db.execute(
                select(StaffPunch)
                .where(StaffPunch.user_id == user_b_id)
                .where(StaffPunch.direction == "out")
            )
            .scalars()
            .all()
        )
        assert len(outs) == 1
        out_local = outs[0].punched_at.astimezone(tz).time()
        # No shift → location's 22:00 cutoff fires.
        assert out_local == time(22, 0), out_local
    finally:
        db.close()

    # Pre-close shift-cutoff precedence: a fresh user with a shift
    # whose auto_session_close_time differs from the location default.
    user_c_id = _make_user(uuid.uuid4().hex[:6])
    user_c_day = user_b_punch_day + timedelta(days=7)
    while user_c_day.isoweekday() != 1:
        user_c_day = user_c_day + timedelta(days=1)
    # Shift: 9-5, auto_close=20:30, on Mondays.
    user_c_shift_id = _seed_shift(
        user_id=user_c_id,
        starts_at=datetime(user_c_day.year, user_c_day.month, user_c_day.day, 9, 0, tzinfo=tz),
        ends_at=datetime(user_c_day.year, user_c_day.month, user_c_day.day, 17, 0, tzinfo=tz),
        working_days=[1],
        location_id=location_id,
        auto_session_close=time(20, 30),
    )

    in_local_c = datetime(user_c_day.year, user_c_day.month, user_c_day.day, 9, 0, tzinfo=tz)
    db = SessionLocal()
    try:
        p = StaffPunch(
            user_id=user_c_id,
            direction="in",
            punched_at=in_local_c.astimezone(timezone.utc),
            status="recorded",
            location_id=location_id,
            shift_id=user_c_shift_id,
        )
        db.add(p)
        db.commit()
    finally:
        db.close()

    # `now` 20:15 local — inside the 30-minute lead window for the
    # SHIFT cutoff (20:30) but well before the location default (22:00).
    pre_close_now = datetime(
        user_c_day.year, user_c_day.month, user_c_day.day, 20, 15, tzinfo=tz
    ).astimezone(timezone.utc)

    sent_emails = []
    real_get_transport = attendance_pre_close.email_transport.get_email_transport

    class _RecordingTransport:
        def send(self, msg):
            sent_emails.append(msg)

    attendance_pre_close.email_transport.get_email_transport = (
        lambda: _RecordingTransport()
    )
    try:
        db = SessionLocal()
        try:
            result = attendance_pre_close.run_pre_close_pass(
                db, now_override=pre_close_now
            )
            db.commit()
        finally:
            db.close()
        assert result.sent == 1, result
        # Email subject should reference 8:30 PM (shift cutoff), not
        # 10:00 PM (location default).
        assert "8:30" in sent_emails[0].subject, sent_emails[0].subject
    finally:
        attendance_pre_close.email_transport.get_email_transport = (
            real_get_transport
        )

    # ============================================================
    # 9) OVERNIGHT SHIFT EXPANSION (resolver carries duration)
    # ============================================================
    print("===== invariant 9: overnight expansion =====")
    user_d_id = _make_user(uuid.uuid4().hex[:6])
    overnight_sat = user_c_day + timedelta(days=5)  # next Saturday
    while overnight_sat.isoweekday() != 6:
        overnight_sat = overnight_sat + timedelta(days=1)

    overnight_shift_id = _seed_shift(
        user_id=user_d_id,
        starts_at=datetime(overnight_sat.year, overnight_sat.month, overnight_sat.day, 18, 0, tzinfo=tz),
        ends_at=datetime(overnight_sat.year, overnight_sat.month, overnight_sat.day + 1 if overnight_sat.day + 1 <= 28 else overnight_sat.day, 2, 0, tzinfo=tz),
        working_days=[6],  # Saturday repeat
        location_id=location_id,
    )
    # Reseed properly so we don't worry about month boundary above.
    db = SessionLocal()
    try:
        s = db.get(StaffShift, overnight_shift_id)
        s.starts_at = datetime(overnight_sat.year, overnight_sat.month, overnight_sat.day, 18, 0, tzinfo=tz)
        sun = overnight_sat + timedelta(days=1)
        s.ends_at = datetime(sun.year, sun.month, sun.day, 2, 0, tzinfo=tz)
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        as_of = datetime(overnight_sat.year, overnight_sat.month, overnight_sat.day, 20, 0, tzinfo=tz)
        resolved = shift_resolver.resolve_active_shift(
            db, user_id=user_d_id, as_of_local=as_of
        )
        assert resolved is not None
        # 6 hours of duration crossing midnight.
        delta_hours = (resolved.ends_at_local - resolved.starts_at_local).total_seconds() / 3600
        assert abs(delta_hours - 8.0) < 0.001, delta_hours
        # ends_at_local lands on the next calendar day.
        assert resolved.ends_at_local.date() != resolved.starts_at_local.date()
        assert resolved.ends_at_local.time() == time(2, 0)
    finally:
        db.close()

    print("phase8_resolver smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
