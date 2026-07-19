"""Swap conflict smoke for Scheduling Phase 4.

Approval hard-blocks when either staffer can't work the OTHER's shift, a
shift has started, or the request is already terminal:

  1. Candidate's overlapping third shift -> candidate_conflict
     (published_overlap, for the candidate).
  2. Requester's approved time off over the target -> candidate_conflict
     (approved_time_off, for the requester).
  3. Candidate's recurring unavailability over the source -> conflict.
  4. A started shift (attendance stamped) blocks approval (entry_started).
  5. A terminal (denied) request can't be re-decided (request_terminal).
"""

import os
import sys
import uuid
from datetime import date, datetime, timezone
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
from database.models import TimeOffRequest, User  # noqa: E402
from services import recurring_availability, staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_admin_id = 0


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


def _publish(user_id, d: date, tz, start_h=9, end_h=17) -> int:
    db = SessionLocal()
    try:
        out = staff_schedule.create_entry(
            db,
            actor_user_id=_admin_id,
            user_id=user_id,
            business_date_=d,
            starts_at_local=datetime(d.year, d.month, d.day, start_h, 0, tzinfo=tz),
            ends_at_local=datetime(d.year, d.month, d.day, end_h, 0, tzinfo=tz),
            publish=True,
        )
        db.commit()
        return out["id"]
    finally:
        db.close()


def _accepted_swap(src_day: date, tgt_day: date, tz):
    a = _make_user(role="sales")
    b = _make_user(role="sales")
    a_src = _publish(a, src_day, tz)
    b_tgt = _publish(b, tgt_day, tz)
    a_hdr = {"Authorization": f"Bearer {_token(a, sales=True)}"}
    b_hdr = {"Authorization": f"Bearer {_token(b, sales=True)}"}
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=a_hdr,
        json={"request_type": "swap", "source_entry_id": a_src,
              "target_entry_id": b_tgt},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept", headers=b_hdr
    )
    assert resp.status_code == 200, resp.text
    return req_id, a, b, a_src, b_tgt


def _approve(req_id):
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    return client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )


def _expect_conflict(resp, conflict_type, for_user_id):
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "candidate_conflict", detail
    hit = [
        c
        for c in detail.get("conflicts", [])
        if c["type"] == conflict_type and c.get("for_user_id") == for_user_id
    ]
    assert hit, (detail, conflict_type, for_user_id)


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            for stmt in (
                "DELETE FROM staff_shift_request_events WHERE request_id IN (SELECT id FROM staff_shift_requests WHERE requester_user_id = ANY(:u))",
                "DELETE FROM staff_shift_requests WHERE requester_user_id = ANY(:u)",
                "DELETE FROM notification_jobs WHERE recipient_user_id = ANY(:u)",
                "DELETE FROM staff_notification_events WHERE actor_user_id = ANY(:u)",
                "DELETE FROM time_off_decision_events WHERE request_id IN (SELECT id FROM time_off_requests WHERE user_id = ANY(:u))",
                "DELETE FROM time_off_requests WHERE user_id = ANY(:u)",
                "DELETE FROM recurring_unavailability WHERE user_id = ANY(:u)",
                "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:u)",
                "DELETE FROM users WHERE id = ANY(:u)",
            ):
                db.execute(sql_text(stmt), {"u": _user_ids})
        db.commit()
    finally:
        db.close()


def main() -> None:
    global _admin_id
    tz = ZoneInfo(APP_TIMEZONE)
    _admin_id = _make_user(role="admin")

    # 1) candidate overlapping third shift on the source's day
    print("===== candidate overlap blocks swap =====")
    src, tgt = date(2027, 2, 1), date(2027, 2, 2)
    req_id, a, b, a_src, b_tgt = _accepted_swap(src, tgt, tz)
    _publish(b, src, tz, 12, 20)  # B busy when they'd take A's slot
    _expect_conflict(_approve(req_id), "published_overlap", b)

    # 2) requester time off over the target's day
    print("===== requester time off blocks swap =====")
    src, tgt = date(2027, 2, 8), date(2027, 2, 9)
    req_id, a, b, a_src, b_tgt = _accepted_swap(src, tgt, tz)
    db = SessionLocal()
    try:
        db.add(
            TimeOffRequest(
                user_id=a,
                starts_at=datetime(2027, 2, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc),
                ends_at=datetime(2027, 2, 10, 0, 0, tzinfo=tz).astimezone(timezone.utc),
                reason="p0sched smoke",
                status="approved",
                decided_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()
    _expect_conflict(_approve(req_id), "approved_time_off", a)

    # 3) candidate recurring unavailability over the source's day
    print("===== candidate recurring unavailability blocks swap =====")
    src, tgt = date(2027, 2, 15), date(2027, 2, 16)
    req_id, a, b, a_src, b_tgt = _accepted_swap(src, tgt, tz)
    db = SessionLocal()
    try:
        recurring_availability.create_block(
            db,
            user_id=b,
            weekday=src.isoweekday(),
            start_time_local="08:00",
            end_time_local="18:00",
            effective_from=date(2027, 1, 1),
            reason="p0sched smoke",
        )
        db.commit()
    finally:
        db.close()
    _expect_conflict(_approve(req_id), "recurring_unavailability", b)

    # 4) started shift blocks approval
    print("===== started shift blocks swap =====")
    src, tgt = date(2027, 2, 22), date(2027, 2, 23)
    req_id, a, b, a_src, b_tgt = _accepted_swap(src, tgt, tz)
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE staff_schedule_entries SET attendance_status='present' WHERE id = :id"
            ),
            {"id": a_src},
        )
        db.commit()
    finally:
        db.close()
    resp = _approve(req_id)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_started"

    # 5) terminal request can't be re-decided
    print("===== terminal request can't be re-decided =====")
    src, tgt = date(2027, 3, 1), date(2027, 3, 2)
    req_id, a, b, a_src, b_tgt = _accepted_swap(src, tgt, tz)
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "denied"},
    )
    assert resp.status_code == 200, resp.text
    resp = _approve(req_id)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "request_terminal"

    print("shift_swap_conflicts smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
