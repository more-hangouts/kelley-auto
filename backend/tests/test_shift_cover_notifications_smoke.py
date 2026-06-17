"""Cover notification smoke for Scheduling Phase 2.

Every cover transition writes an in-app staff notification event to the
right person (Gate 4: in-app always), and the actionable kinds fan out an
email job:

  1. create  -> staff.shift_cover_requested to the candidate
  2. accept   -> staff.shift_cover_accepted to the requester
  3. approve  -> staff.shift_cover_approved to BOTH the old assignee
     (requester, "covered") and the new assignee (candidate, "added")
  4. email fan-out: a notification_jobs row exists for each recipient.
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
from database.models import User  # noqa: E402
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


def _event_count(req_id: int, kind: str, recipient_id: int) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_notification_events "
                "WHERE subject_id = :rid AND kind = :k "
                "AND (payload->>'recipient_user_id')::int = :uid"
            ),
            {"rid": req_id, "k": kind, "uid": recipient_id},
        ).scalar()
    finally:
        db.close()


def _job_count(recipient_id: int, kind: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT COUNT(*) FROM notification_jobs "
                "WHERE recipient_user_id = :uid AND kind = :k"
            ),
            {"uid": recipient_id, "k": kind},
        ).scalar()
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
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    requester_id = _make_user(role="sales")
    candidate_id = _make_user(role="sales")
    req_hdr = {"Authorization": f"Bearer {_token(requester_id, sales=True)}"}
    cand_hdr = {"Authorization": f"Bearer {_token(candidate_id, sales=True)}"}
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    shift = _published(
        admin_id, requester_id, date(2026, 11, 2),
        datetime(2026, 11, 2, 9, 0, tzinfo=tz),
        datetime(2026, 11, 2, 17, 0, tzinfo=tz),
    )

    print("===== create -> candidate notified =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=req_hdr,
        json={"request_type": "cover", "source_entry_id": shift,
              "candidate_user_id": candidate_id},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]
    assert _event_count(req_id, "staff.shift_cover_requested", candidate_id) == 1
    assert _job_count(candidate_id, "staff.shift_cover_requested") == 1

    print("===== accept -> requester notified =====")
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept",
        headers=cand_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert _event_count(req_id, "staff.shift_cover_accepted", requester_id) == 1

    print("===== approve -> both assignees notified =====")
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 200, resp.text
    # Old assignee (requester) gets the "covered" notice; new assignee
    # (candidate) gets the "added" notice — same kind, different recipient.
    assert _event_count(req_id, "staff.shift_cover_approved", requester_id) == 1
    assert _event_count(req_id, "staff.shift_cover_approved", candidate_id) == 1
    assert _job_count(requester_id, "staff.shift_cover_approved") == 1
    assert _job_count(candidate_id, "staff.shift_cover_approved") == 1

    print("shift_cover_notifications smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
