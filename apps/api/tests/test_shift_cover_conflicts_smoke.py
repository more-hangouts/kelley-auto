"""Cover conflict smoke for Scheduling Phase 2.

Approval must hard-block when the candidate can't actually work the
shift, and requests can't touch a started shift:

  1. Candidate has an overlapping published shift -> candidate_conflict
     (published_overlap).
  2. Candidate has approved time off over the shift -> candidate_conflict
     (approved_time_off).
  3. Candidate has a recurring-unavailable block over the shift ->
     candidate_conflict (recurring_unavailability).
  4. A started shift blocks creating a request (entry_started) AND blocks
     approval once attendance is stamped.
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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
from database.models import StaffScheduleEntry, TimeOffRequest, User  # noqa: E402
from services import recurring_availability, staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_admin_id: int = 0


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


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _published(actor_id, user_id, bdate, starts, ends) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_id,
            user_id=user_id,
            business_date_=bdate,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
        )
        db.commit()
        return d["id"]
    finally:
        db.close()


def _accepted_cover(day: date, tz):
    """Fresh requester+candidate, a published source shift for the
    requester, and a cover request the candidate has accepted. Returns
    (req_id, candidate_id, source_id)."""
    requester = _make_user(role="sales")
    candidate = _make_user(role="sales")
    source = _published(
        _admin_id, requester, day,
        datetime(day.year, day.month, day.day, 9, 0, tzinfo=tz),
        datetime(day.year, day.month, day.day, 17, 0, tzinfo=tz),
    )
    req_hdr = {"Authorization": f"Bearer {_token(requester, sales=True)}"}
    cand_hdr = {"Authorization": f"Bearer {_token(candidate, sales=True)}"}
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=req_hdr,
        json={"request_type": "cover", "source_entry_id": source,
              "candidate_user_id": candidate},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept",
        headers=cand_hdr,
    )
    assert resp.status_code == 200, resp.text
    return req_id, candidate, source


def _approve_expect_conflict(req_id: int, conflict_type: str) -> None:
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "candidate_conflict", detail
    types = {c["type"] for c in detail.get("conflicts", [])}
    assert conflict_type in types, (detail, conflict_type)


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
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE actor_user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_decision_events WHERE request_id IN "
                    "(SELECT id FROM time_off_requests "
                    "WHERE user_id = ANY(:u))"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_requests WHERE user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM recurring_unavailability "
                    "WHERE user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:u)"
                ),
                {"u": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:u)"),
                {"u": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    global _admin_id
    tz = ZoneInfo(APP_TIMEZONE)
    _admin_id = _make_user(role="admin")

    # --- 1) candidate overlapping published shift ---
    print("===== candidate overlapping shift blocks approval =====")
    day = date(2026, 10, 5)
    req_id, candidate, _src = _accepted_cover(day, tz)
    _published(
        _admin_id, candidate, day,
        datetime(2026, 10, 5, 12, 0, tzinfo=tz),
        datetime(2026, 10, 5, 20, 0, tzinfo=tz),
    )
    _approve_expect_conflict(req_id, "published_overlap")

    # --- 2) candidate approved time off ---
    print("===== candidate time off blocks approval =====")
    day = date(2026, 10, 12)
    req_id, candidate, _src = _accepted_cover(day, tz)
    db = SessionLocal()
    try:
        t = TimeOffRequest(
            user_id=candidate,
            starts_at=datetime(2026, 10, 12, 0, 0, tzinfo=tz).astimezone(
                timezone.utc
            ),
            ends_at=datetime(2026, 10, 13, 0, 0, tzinfo=tz).astimezone(
                timezone.utc
            ),
            reason="p0sched smoke",
            status="approved",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(t)
        db.commit()
    finally:
        db.close()
    _approve_expect_conflict(req_id, "approved_time_off")

    # --- 3) candidate recurring unavailability ---
    print("===== candidate recurring unavailability blocks approval =====")
    day = date(2026, 10, 19)
    req_id, candidate, _src = _accepted_cover(day, tz)
    db = SessionLocal()
    try:
        recurring_availability.create_block(
            db,
            user_id=candidate,
            weekday=day.isoweekday(),
            start_time_local="08:00",
            end_time_local="18:00",
            effective_from=date(2026, 9, 1),
            reason="p0sched smoke",
        )
        db.commit()
    finally:
        db.close()
    _approve_expect_conflict(req_id, "recurring_unavailability")

    # --- 4a) started shift blocks request creation ---
    print("===== started shift blocks request creation =====")
    requester = _make_user(role="sales")
    req_hdr = {"Authorization": f"Bearer {_token(requester, sales=True)}"}
    db = SessionLocal()
    try:
        past = StaffScheduleEntry(
            user_id=requester,
            business_date=date(2026, 1, 12),
            starts_at_local=datetime(2026, 1, 12, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 1, 12, 17, 0, tzinfo=tz),
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        db.add(past)
        db.commit()
        db.refresh(past)
        past_id = past.id
    finally:
        db.close()
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=req_hdr,
        json={"request_type": "cover", "source_entry_id": past_id},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_started"

    # --- 4b) attendance stamped after acceptance blocks approval ---
    print("===== attendance stamp blocks approval (entry_started) =====")
    day = date(2026, 10, 26)
    req_id, candidate, src = _accepted_cover(day, tz)
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE staff_schedule_entries "
                "SET attendance_status = 'present' WHERE id = :id"
            ),
            {"id": src},
        )
        db.commit()
    finally:
        db.close()
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_started"

    print("shift_cover_conflicts smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
