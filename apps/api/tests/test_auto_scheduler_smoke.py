"""Smoke test for the auto-scheduler (`services.auto_scheduler`).

Covers the five behaviors locked in for Phase 11 groundwork:

  1. Closed days (Mon/Tue) get no draft entries.
  2. Open days with NO appointments get drafts on the configured
     no-appointment window (default 14:00-19:00).
  3. Open days with appointments get drafts whose start time is
     `appointment_buffer_minutes` before the first appointment of the
     day (floored at business_open_time), and end at business_close.
  4. Staff with an approved time-off request for an open day are NOT
     scheduled on that day; the response surfaces them in
     `skipped_time_off_count`.
  5. Re-running the generator on the same week is idempotent: it
     creates 0 new entries and counts the pre-existing ones in
     `skipped_existing_count`.

We exercise `auto_scheduler.generate_draft_week` directly *and* end-
to-end through `POST /api/admin/schedule/generate-draft-week` so the
router wiring + Pydantic payload contract are both covered.
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
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    TimeOffRequest,
    User,
)
from services import auto_scheduler, booking_service  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_appt_ids: list[int] = []
_tor_ids: list[int] = []


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-autosched-{suffix}",
            email=f"{role}-autosched-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"AutoSched {role.title()} {suffix}",
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


def _seed_appointment(
    *,
    when_local: datetime,
    assigned_user_id: int | None = None,
    duration: int = 60,
) -> int:
    """Drop an Appointment row at the given boutique-local time. The
    auto-scheduler doesn't care about most of the columns; we fill the
    NOT NULLs and let the rest default."""
    db = SessionLocal()
    try:
        slot_utc = when_local.astimezone(timezone.utc)
        a = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(
                db
            ),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=duration),
            slot_duration_minutes=duration,
            timezone=APP_TIMEZONE,
            celebrant_first_name="AutoSched",
            celebrant_last_name="Smoke",
            party_size_bucket="solo",
            phone="(210) 555-0199",
            email=f"autosched-{uuid.uuid4().hex[:6]}@example.com",
            status="confirmed",
            assigned_user_id=assigned_user_id,
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _appt_ids.append(a.id)
        return a.id
    finally:
        db.close()


def _seed_approved_time_off(
    *, user_id: int, starts_at: datetime, ends_at: datetime
) -> int:
    db = SessionLocal()
    try:
        t = TimeOffRequest(
            user_id=user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason="autosched smoke",
            status="approved",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        _tor_ids.append(t.id)
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
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _entries_for(week_start: date, user_ids: list[int]) -> list[dict]:
    """Pull all schedule entries in the week for the smoke user set,
    via the admin /week endpoint. Filters out any rows belonging to
    other test runs by user_id."""
    from services import staff_schedule

    db = SessionLocal()
    try:
        body = staff_schedule.list_week(db, week_start=week_start)
    finally:
        db.close()
    wanted = set(user_ids)
    return [e for e in body["entries"] if e["user_id"] in wanted]


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)

    admin_id = _make_user(role="admin")
    stylist_a = _make_user(role="sales")
    stylist_b = _make_user(role="sales")
    stylist_c = _make_user(role="sales")  # the time-off stylist

    week_start = date(2026, 6, 1)  # Monday
    assert week_start.isoweekday() == 1
    smoke_user_ids = [stylist_a, stylist_b, stylist_c]

    wed = week_start + timedelta(days=2)
    thu = week_start + timedelta(days=3)
    fri = week_start + timedelta(days=4)

    # ============================================================
    # SETUP — appointments + time off
    # ============================================================
    print("===== seeding appointments & time off =====")
    # Thursday: 1:30pm appointment → buffer 60 → expect 12:30 start
    # (exercises the buffer arithmetic without hitting the open-time
    # floor).
    _seed_appointment(
        when_local=datetime.combine(thu, time(13, 30), tzinfo=tz),
        assigned_user_id=stylist_a,
    )
    # Friday: 1pm appointment → buffer 60 → expected start 12:00, which
    # is exactly business_open — exercises the floor at business_open.
    _seed_appointment(
        when_local=datetime.combine(fri, time(13, 0), tzinfo=tz),
        assigned_user_id=stylist_b,
    )
    # Wednesday has NO appointment → expect 2pm-7pm fill shift.

    # Stylist C is off all of Wednesday (full-day approved request).
    _seed_approved_time_off(
        user_id=stylist_c,
        starts_at=datetime.combine(wed, time.min, tzinfo=tz).astimezone(
            timezone.utc
        ),
        ends_at=datetime.combine(
            wed + timedelta(days=1), time.min, tzinfo=tz
        ).astimezone(timezone.utc),
    )

    # ============================================================
    # 1) Validation: Monday-anchored week_start
    # ============================================================
    print("===== validates week_start is Monday =====")
    db = SessionLocal()
    try:
        try:
            auto_scheduler.generate_draft_week(
                db,
                actor_user_id=admin_id,
                week_start=week_start + timedelta(days=1),  # Tuesday
                user_ids=smoke_user_ids,
            )
            raise AssertionError("expected week_start_not_monday")
        except Exception as exc:
            assert getattr(exc, "code", None) == "week_start_not_monday", exc
    finally:
        db.close()

    # ============================================================
    # 2) First run — generate the week
    # ============================================================
    print("===== first run of generate_draft_week =====")
    db = SessionLocal()
    try:
        result = auto_scheduler.generate_draft_week(
            db,
            actor_user_id=admin_id,
            week_start=week_start,
            user_ids=smoke_user_ids,
        )
        db.commit()
    finally:
        db.close()

    print(
        f"  created={result['created_count']} "
        f"skipped_existing={result['skipped_existing_count']} "
        f"skipped_time_off={result['skipped_time_off_count']} "
        f"closed_days={result['skipped_closed_days_count']}"
    )

    # ---- ASSERTION 1: Mon/Tue are closed → no entries on those days
    entries = _entries_for(week_start, smoke_user_ids)
    mon_tue = [
        e
        for e in entries
        if e["business_date"]
        in {week_start.isoformat(), (week_start + timedelta(days=1)).isoformat()}
    ]
    assert mon_tue == [], (
        f"closed days should not get drafts, got {mon_tue}"
    )
    # And the result summary should call out the 2 closed days.
    assert result["skipped_closed_days_count"] == 2, result

    # ---- ASSERTION 2: Wednesday (no appointments) → 14:00-19:00
    wed_entries = [
        e for e in entries if e["business_date"] == wed.isoformat()
    ]
    assert len(wed_entries) >= 1, (
        f"Wednesday should have at least one no-appointment draft, "
        f"got {wed_entries}"
    )
    for e in wed_entries:
        starts_local = datetime.fromisoformat(e["starts_at_local"])
        ends_local = datetime.fromisoformat(e["ends_at_local"])
        assert (starts_local.hour, starts_local.minute) == (14, 0), (
            f"Wed start should be 14:00, got {starts_local.time()}"
        )
        assert (ends_local.hour, ends_local.minute) == (19, 0), (
            f"Wed end should be 19:00, got {ends_local.time()}"
        )
        assert e["status"] == "draft"
        # Stylist C is on time off and must NOT appear.
        assert e["user_id"] != stylist_c, (
            "stylist on approved time off should not be scheduled"
        )

    # ---- ASSERTION 3: Thursday → 10am appt with 60-min buffer = 9am start
    thu_entries = [
        e
        for e in entries
        if e["business_date"] == thu.isoformat()
        and e["user_id"] == stylist_a
    ]
    assert len(thu_entries) == 1, (
        f"stylist A should have a Thursday draft, got {thu_entries}"
    )
    thu_starts = datetime.fromisoformat(thu_entries[0]["starts_at_local"])
    thu_ends = datetime.fromisoformat(thu_entries[0]["ends_at_local"])
    assert (thu_starts.hour, thu_starts.minute) == (12, 30), (
        f"Thu start (appt at 13:30 - 60min buffer) should be 12:30, "
        f"got {thu_starts.time()}"
    )
    assert (thu_ends.hour, thu_ends.minute) == (19, 0), (
        f"Thu end should be 19:00, got {thu_ends.time()}"
    )

    # ---- ASSERTION 3b: Friday → 1pm appt with 60-min buffer = 12pm
    #     (floor at business_open, which is also 12:00)
    fri_entries = [
        e
        for e in entries
        if e["business_date"] == fri.isoformat()
        and e["user_id"] == stylist_b
    ]
    assert len(fri_entries) == 1, fri_entries
    fri_starts = datetime.fromisoformat(fri_entries[0]["starts_at_local"])
    assert (fri_starts.hour, fri_starts.minute) == (12, 0), (
        f"Fri start should floor at business_open 12:00, got {fri_starts.time()}"
    )

    # ---- ASSERTION 4: stylist C (approved time off all of Wed) is not
    #     scheduled on Wed AND surfaces in skipped_time_off_count.
    c_wed = [
        e
        for e in entries
        if e["user_id"] == stylist_c and e["business_date"] == wed.isoformat()
    ]
    assert c_wed == [], (
        f"stylist on approved time off should not be scheduled, got {c_wed}"
    )
    assert result["skipped_time_off_count"] >= 1, result

    # ============================================================
    # 3) Second run — idempotent
    # ============================================================
    print("===== second run is idempotent =====")
    db = SessionLocal()
    try:
        again = auto_scheduler.generate_draft_week(
            db,
            actor_user_id=admin_id,
            week_start=week_start,
            user_ids=smoke_user_ids,
        )
        db.commit()
    finally:
        db.close()

    # Created nothing new (everything was already there).
    assert again["created_count"] == 0, again
    # The pre-existing entries from run #1 must appear in
    # skipped_existing_count for the open days we touched.
    assert again["skipped_existing_count"] >= result["created_count"], (
        again,
        result,
    )

    # Entry totals didn't change.
    entries_after = _entries_for(week_start, smoke_user_ids)
    assert len(entries_after) == len(entries), (
        f"second run created or deleted entries: "
        f"{len(entries)} → {len(entries_after)}"
    )

    # ============================================================
    # 4) HTTP end-to-end + auth surface
    # ============================================================
    print("===== HTTP end-to-end =====")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_hdr = {"Authorization": f"Bearer {_token(stylist_a, sales=True)}"}

    # Sales token must be rejected.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
        },
        headers=sales_hdr,
    )
    assert resp.status_code == 403, (resp.status_code, resp.text)

    # GET /auto-schedule/rules surfaces defaults for the dialog.
    resp = client.get(
        "/api/admin/schedule/auto-schedule/rules", headers=admin_hdr
    )
    assert resp.status_code == 200, resp.text
    rules_body = resp.json()
    assert rules_body["business_open_time"] == "12:00"
    assert rules_body["business_close_time"] == "19:00"
    assert rules_body["no_appointment_shift_start"] == "14:00"
    assert rules_body["no_appointment_shift_end"] == "19:00"
    assert "Wed" in rules_body["open_days"]

    # POST returns the same shape the dialog reads (idempotent re-run
    # of the now-populated week).
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created_count"] == 0
    assert body["skipped_closed_days_count"] == 2
    assert isinstance(body["warnings"], list)
    assert body["rules"]["business_open_time"] == "12:00"

    # Non-Monday week_start → 422 with our code.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": (week_start + timedelta(days=1)).isoformat(),
            "user_ids": smoke_user_ids,
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "week_start_not_monday"

    # ============================================================
    # 5) Override validation surface
    # ============================================================
    print("===== override validation =====")

    # Buffer not in the allowed set → 422 invalid_appointment_buffer.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
            "overrides": {"appointment_buffer_minutes": 45},
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_appointment_buffer"

    # min_stylists_when_quiet < 1 → 422 invalid_min_stylists.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
            "overrides": {"min_stylists_when_quiet": 0},
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_min_stylists"

    # No-appt window with end <= start → 422 invalid_no_appointment_window.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
            "overrides": {
                "no_appointment_shift_start": "17:00",
                "no_appointment_shift_end": "15:00",
            },
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_no_appointment_window"

    # Business hours with close <= open → 422 invalid_business_hours.
    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
            "overrides": {
                "business_open_time": "18:00",
                "business_close_time": "12:00",
            },
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_business_hours"

    # ============================================================
    # 6) Override-respected: shifted no-appt window + quiet-day coverage
    # ============================================================
    print("===== overrides are respected =====")

    # Clear existing draft entries for our smoke users so we can
    # observe a fresh override-driven generation.
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM staff_schedule_entries "
                "WHERE user_id = ANY(:uids)"
            ),
            {"uids": smoke_user_ids},
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(
        "/api/admin/schedule/generate-draft-week",
        json={
            "week_start": week_start.isoformat(),
            "user_ids": smoke_user_ids,
            "overrides": {
                "no_appointment_shift_start": "15:00",
                "no_appointment_shift_end": "19:00",
                "appointment_buffer_minutes": 90,
                "min_stylists_when_quiet": 2,
                "min_stylists_when_appointments": 1,
                "rotate_fairly": True,
            },
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Echo of merged rules confirms the wire format round-tripped.
    assert body["rules"]["no_appointment_shift_start"] == "15:00"
    assert body["rules"]["appointment_buffer_minutes"] == 90
    assert body["rules"]["min_stylists_when_quiet"] == 2

    overridden_entries = _entries_for(week_start, smoke_user_ids)
    # Wed has no appointments; with quiet-day min=2 we expect 2 entries,
    # both at 15:00-19:00.
    wed_entries = [
        e for e in overridden_entries if e["business_date"] == wed.isoformat()
    ]
    assert len(wed_entries) == 2, (
        f"Wed should have 2 entries from min_stylists_when_quiet=2, "
        f"got {len(wed_entries)}"
    )
    for e in wed_entries:
        starts_local = datetime.fromisoformat(e["starts_at_local"])
        ends_local = datetime.fromisoformat(e["ends_at_local"])
        assert (starts_local.hour, starts_local.minute) == (15, 0), (
            f"Wed override start should be 15:00, got {starts_local.time()}"
        )
        assert (ends_local.hour, ends_local.minute) == (19, 0), (
            f"Wed override end should be 19:00, got {ends_local.time()}"
        )

    # Thursday has a 13:30 appt → with 90-min buffer, start = 12:00
    # (since business_open default is 12:00, the floor is hit).
    thu_entries_2 = [
        e
        for e in overridden_entries
        if e["business_date"] == thu.isoformat()
        and e["user_id"] == stylist_a
    ]
    assert len(thu_entries_2) == 1, thu_entries_2
    thu_starts_2 = datetime.fromisoformat(thu_entries_2[0]["starts_at_local"])
    assert (thu_starts_2.hour, thu_starts_2.minute) == (12, 0), (
        f"Thu override (appt 13:30 - 90min buffer = 12:00, floored at "
        f"business_open 12:00) should be 12:00, got {thu_starts_2.time()}"
    )

    print("auto_scheduler smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
