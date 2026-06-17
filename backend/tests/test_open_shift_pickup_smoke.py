"""Open-shift pickup smoke for Scheduling Phase 3 (HTTP end-to-end).

  1. Admin posts an open shift.
  2. A sales user sees it on the board and claims it (pending pickup).
  3. Admin approves the pickup; a published schedule entry is created for
     the claimant with the post's times, and the post closes as 'claimed'
     linked to the winning request.
  4. A second claimant's pending request is expired when the post is taken.
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
from database.models import OpenShiftPost, User  # noqa: E402

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


def _post_state(post_id: int) -> dict:
    db = SessionLocal()
    try:
        p = db.get(OpenShiftPost, post_id)
        return {
            "status": p.status,
            "claimed_by": p.claimed_by_user_id,
            "claimed_request_id": p.claimed_request_id,
        }
    finally:
        db.close()


def _published_count(user_id: int, bdate: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_schedule_entries "
                "WHERE user_id = :u AND business_date = :d "
                "AND status = 'published'"
            ),
            {"u": user_id, "d": bdate},
        ).scalar()
    finally:
        db.close()


def _req_status(req_id: int) -> str:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT status FROM staff_shift_requests WHERE id = :id"
            ),
            {"id": req_id},
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
                    "DELETE FROM open_shift_posts "
                    "WHERE created_by_user_id = ANY(:u) "
                    "OR claimed_by_user_id = ANY(:u)"
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
    claimant_id = _make_user(role="sales")
    other_id = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    claimant_hdr = {"Authorization": f"Bearer {_token(claimant_id, sales=True)}"}
    other_hdr = {"Authorization": f"Bearer {_token(other_id, sales=True)}"}

    bdate = "2026-11-09"

    print("===== admin posts an open shift =====")
    resp = client.post(
        "/api/admin/schedule/open-shifts",
        headers=admin_hdr,
        json={
            "business_date": bdate,
            "starts_at_local": datetime(2026, 11, 9, 9, 0, tzinfo=tz).isoformat(),
            "ends_at_local": datetime(2026, 11, 9, 17, 0, tzinfo=tz).isoformat(),
            "manager_notes": "Need a stylist for the morning.",
        },
    )
    assert resp.status_code == 201, resp.text
    post = resp.json()
    post_id = post["id"]
    assert post["status"] == "open"

    print("===== sales sees the board + claims =====")
    resp = client.get(
        "/api/sales/schedule/open-shifts",
        headers=claimant_hdr,
        params={"from_date": "2026-11-02", "to_date": "2026-11-15"},
    )
    assert resp.status_code == 200, resp.text
    assert any(p["id"] == post_id for p in resp.json()["posts"])

    resp = client.post(
        f"/api/sales/schedule/open-shifts/{post_id}/claim",
        headers=claimant_hdr,
    )
    assert resp.status_code == 200, resp.text
    claim = resp.json()
    assert claim["request_type"] == "pickup"
    assert claim["status"] == "pending"
    assert claim["open_shift_post_id"] == post_id
    req_id = claim["id"]

    # A second staffer also claims — they should lose the race on approval.
    resp = client.post(
        f"/api/sales/schedule/open-shifts/{post_id}/claim",
        headers=other_hdr,
    )
    assert resp.status_code == 200, resp.text
    other_req_id = resp.json()["id"]

    print("===== admin approves the first claim =====")
    assert _published_count(claimant_id, bdate) == 0
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    # A published entry now exists for the claimant.
    assert _published_count(claimant_id, bdate) == 1, (
        "approving the pickup should create a published entry for the claimant"
    )
    # Post is claimed and linked to the winning request.
    state = _post_state(post_id)
    assert state["status"] == "claimed", state
    assert state["claimed_by"] == claimant_id, state
    assert state["claimed_request_id"] == req_id, state
    # The losing claim is expired.
    assert _req_status(other_req_id) == "expired", _req_status(other_req_id)

    print("open_shift_pickup smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
