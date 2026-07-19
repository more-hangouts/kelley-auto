"""Smoke test for Phase 10 Slice 7 — appointment density warnings.

Runs as a script:

    venv/bin/python tests/test_appointment_density_smoke.py
"""

from __future__ import annotations

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
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    RecurringUnavailability,
    StaffScheduleEntry,
    User,
)
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_appt_ids: list[int] = []
_entry_ids: list[int] = []
_ru_ids: list[int] = []


def _make_user(role: str, label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"density-{label}-{suffix}",
            email=f"density-{label}-{suffix}@example.com",
            hashed_password=hash_password("density-pass-12345"),
            full_name=f"Density {label.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(int(u.id))
        return int(u.id)
    finally:
        db.close()


def _token(user_id: int) -> str:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        return create_access_token(user)
    finally:
        db.close()


def _seed_shift(
    *,
    user_id: int,
    creator_id: int,
    day: date,
    start: time,
    end: time,
) -> None:
    tz = ZoneInfo(os.environ["APP_TIMEZONE"])
    db = SessionLocal()
    try:
        entry = StaffScheduleEntry(
            user_id=user_id,
            business_date=day,
            starts_at_local=datetime.combine(day, start, tzinfo=tz),
            ends_at_local=datetime.combine(day, end, tzinfo=tz),
            status="draft",
            attendance_status="scheduled",
            late_grace_minutes=30,
            source="manual",
            created_by_user_id=creator_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        _entry_ids.append(int(entry.id))
    finally:
        db.close()


def _seed_unavailability(user_id: int, day: date) -> None:
    db = SessionLocal()
    try:
        row = RecurringUnavailability(
            user_id=user_id,
            weekday=day.isoweekday(),
            start_time_local=time(9, 0),
            end_time_local=time(12, 0),
            effective_from=day - timedelta(days=7),
            reason="school pickup",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _ru_ids.append(int(row.id))
    finally:
        db.close()


def _seed_appointment(day: date, start: time, *, status: str) -> None:
    tz = ZoneInfo(os.environ["APP_TIMEZONE"])
    start_local = datetime.combine(day, start, tzinfo=tz)
    end_local = start_local + timedelta(minutes=60)
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:10].upper()
        appt = Appointment(
            confirmation_code=f"DENS{suffix}",
            slot_start_at=start_local,
            slot_end_at=end_local,
            slot_duration_minutes=60,
            timezone=os.environ["APP_TIMEZONE"],
            celebrant_first_name="Density",
            celebrant_last_name=suffix[:4],
            event_date=day + timedelta(days=180),
            party_size_bucket="pair",
            phone="2105550101",
            email=f"density-{suffix.lower()}@example.com",
            status=status,
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _appt_ids.append(int(appt.id))
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _entry_ids:
            db.execute(
                sql_text("DELETE FROM staff_schedule_entries WHERE id = ANY(:ids)"),
                {"ids": _entry_ids},
            )
        if _ru_ids:
            db.execute(
                sql_text("DELETE FROM recurring_unavailability WHERE id = ANY(:ids)"),
                {"ids": _ru_ids},
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


def main() -> None:
    week_start = date(2026, 11, 2)
    day = week_start + timedelta(days=2)
    admin_id = _make_user("admin", "admin")
    stylist_a = _make_user("sales", "stylist-a")
    stylist_b = _make_user("sales", "stylist-b")

    try:
        _seed_shift(
            user_id=stylist_a,
            creator_id=admin_id,
            day=day,
            start=time(9, 0),
            end=time(17, 0),
        )
        _seed_shift(
            user_id=stylist_b,
            creator_id=admin_id,
            day=day,
            start=time(9, 0),
            end=time(17, 0),
        )
        _seed_unavailability(stylist_b, day)
        for minute in (0, 15, 30):
            _seed_appointment(day, time(10, minute), status="confirmed")
        _seed_appointment(day, time(10, 45), status="cancelled")

        db = SessionLocal()
        try:
            payload = staff_schedule.list_week(
                db,
                week_start=week_start,
                user_ids=[stylist_a, stylist_b],
            )
            warnings = payload["appointment_density_warnings"]
            assert len(warnings) == 1, warnings
            warning = warnings[0]
            assert warning["business_date"] == day.isoformat(), warning
            assert warning["appointment_count"] == 3, warning
            assert warning["scheduled_stylist_count"] == 1, warning
            assert warning["required_stylist_count"] == 2, warning
            assert warning["shortage"] == 1, warning
        finally:
            db.close()

        headers = {"Authorization": f"Bearer {_token(admin_id)}"}
        resp = client.get(
            "/api/admin/schedule/week",
            params=[
                ("week_start", week_start.isoformat()),
                ("user_ids", str(stylist_a)),
                ("user_ids", str(stylist_b)),
            ],
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        warnings = body["appointment_density_warnings"]
        assert len(warnings) == 1, warnings
        assert warnings[0]["scheduled_stylist_count"] == 1, warnings

        print("appointment density smoke passed")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
