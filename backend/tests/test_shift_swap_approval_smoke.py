"""Swap approval smoke for Scheduling Phase 4 (HTTP end-to-end).

  1. Staff A proposes a swap of their shift for Staff B's shift.
  2. B (the target's owner) accepts.
  3. Admin approves; the two published entries swap owners (same ids).
  4. Timeline carries requested + accepted + approved; both staff get
     swap notification events.
  5. Admin can't approve before B accepts (request_not_accepted).
"""

import os
import sys
import uuid
from datetime import date, datetime
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
from database.models import StaffScheduleEntry, User  # noqa: E402
from services import staff_schedule  # noqa: E402

client = TestClient(app)

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


def _owner(entry_id: int) -> int:
    db = SessionLocal()
    try:
        return db.get(StaffScheduleEntry, entry_id).user_id
    finally:
        db.close()


def _actions(req_id: int) -> list[str]:
    db = SessionLocal()
    try:
        return [
            r[0]
            for r in db.execute(
                sql_text(
                    "SELECT action FROM staff_shift_request_events "
                    "WHERE request_id = :r ORDER BY id"
                ),
                {"r": req_id},
            ).all()
        ]
    finally:
        db.close()


def _event_count(req_id: int, kind: str, recipient_id: int) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_notification_events "
                "WHERE subject_id = :r AND kind = :k "
                "AND (payload->>'recipient_user_id')::int = :u"
            ),
            {"r": req_id, "k": kind, "u": recipient_id},
        ).scalar()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            for stmt in (
                "DELETE FROM staff_shift_request_events WHERE request_id IN "
                "(SELECT id FROM staff_shift_requests WHERE requester_user_id = ANY(:u))",
                "DELETE FROM staff_shift_requests WHERE requester_user_id = ANY(:u)",
                "DELETE FROM notification_jobs WHERE recipient_user_id = ANY(:u)",
                "DELETE FROM staff_notification_events WHERE actor_user_id = ANY(:u)",
                "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:u)",
                "DELETE FROM users WHERE id = ANY(:u)",
            ):
                db.execute(sql_text(stmt), {"u": _user_ids})
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    a_id = _make_user(role="sales")
    b_id = _make_user(role="sales")
    a_hdr = {"Authorization": f"Bearer {_token(a_id, sales=True)}"}
    b_hdr = {"Authorization": f"Bearer {_token(b_id, sales=True)}"}
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    a_src = _published(
        admin_id, a_id, date(2027, 1, 11),
        datetime(2027, 1, 11, 9, 0, tzinfo=tz),
        datetime(2027, 1, 11, 17, 0, tzinfo=tz),
    )
    b_tgt = _published(
        admin_id, b_id, date(2027, 1, 12),
        datetime(2027, 1, 12, 9, 0, tzinfo=tz),
        datetime(2027, 1, 12, 17, 0, tzinfo=tz),
    )

    print("===== A proposes swap with B =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=a_hdr,
        json={"request_type": "swap", "source_entry_id": a_src,
              "target_entry_id": b_tgt},
    )
    assert resp.status_code == 200, resp.text
    req = resp.json()
    req_id = req["id"]
    assert req["candidate_user_id"] == b_id, req
    assert _event_count(req_id, "staff.shift_swap_requested", b_id) == 1

    print("===== admin can't approve before B accepts =====")
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "request_not_accepted"

    print("===== B accepts =====")
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept",
        headers=b_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted_by_staff"
    assert _event_count(req_id, "staff.shift_swap_accepted", a_id) == 1

    print("===== admin approves -> owners swap =====")
    assert _owner(a_src) == a_id and _owner(b_tgt) == b_id
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    assert _owner(a_src) == b_id, "A's shift should now belong to B"
    assert _owner(b_tgt) == a_id, "B's shift should now belong to A"

    assert _actions(req_id) == ["requested", "accepted", "approved"]
    assert _event_count(req_id, "staff.shift_swap_approved", a_id) == 1
    assert _event_count(req_id, "staff.shift_swap_approved", b_id) == 1

    print("shift_swap_approval smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
