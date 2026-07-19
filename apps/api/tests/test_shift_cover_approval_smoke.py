"""Cover approval smoke for Scheduling Phase 2 (HTTP end-to-end).

The full happy path of a direct cover request:

  1. Requester files a cover request naming a candidate.
  2. Candidate accepts (status -> accepted_by_staff).
  3. Admin approves; the published entry's user_id transfers to the
     candidate (same entry id) and the request becomes 'approved'.
  4. The request timeline carries requested + accepted + approved events.
  5. An admin cannot approve before the candidate accepts
     (request_not_accepted).
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta
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


def _entry_owner(entry_id: int) -> int:
    db = SessionLocal()
    try:
        return db.get(StaffScheduleEntry, entry_id).user_id
    finally:
        db.close()


def _actions(request_id: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT action FROM staff_shift_request_events "
                "WHERE request_id = :rid ORDER BY id"
            ),
            {"rid": request_id},
        ).all()
        return [r[0] for r in rows]
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
    requester_hdr = {"Authorization": f"Bearer {_token(requester_id, sales=True)}"}
    candidate_hdr = {"Authorization": f"Bearer {_token(candidate_id, sales=True)}"}
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    shift = _published(
        admin_id, requester_id, date(2026, 10, 5),
        datetime(2026, 10, 5, 9, 0, tzinfo=tz),
        datetime(2026, 10, 5, 17, 0, tzinfo=tz),
    )

    print("===== create direct cover =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=requester_hdr,
        json={"request_type": "cover", "source_entry_id": shift,
              "candidate_user_id": candidate_id},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]

    print("===== admin cannot approve before acceptance =====")
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "request_not_accepted"

    print("===== candidate accepts =====")
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept",
        headers=candidate_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted_by_staff"
    assert resp.json()["accepted_by_user_id"] == candidate_id

    print("===== admin approves -> shift transfers =====")
    assert _entry_owner(shift) == requester_id
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved", "decision_notes": "ok"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    # Same entry id, new owner.
    assert _entry_owner(shift) == candidate_id, (
        "published entry should now belong to the candidate"
    )

    print("===== timeline carries requested/accepted/approved =====")
    actions = _actions(req_id)
    assert actions == ["requested", "accepted", "approved"], actions

    print("shift_cover_approval smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
