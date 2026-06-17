"""Swap privacy smoke for Scheduling Phase 4.

A swap is a direct request: only the people involved (the requester and
the target's owner) can see it; an uninvolved coworker cannot, and admin
sees everything.
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


def _publish(actor_id, user_id, d, tz) -> int:
    db = SessionLocal()
    try:
        out = staff_schedule.create_entry(
            db,
            actor_user_id=actor_id,
            user_id=user_id,
            business_date_=d,
            starts_at_local=datetime(d.year, d.month, d.day, 9, 0, tzinfo=tz),
            ends_at_local=datetime(d.year, d.month, d.day, 17, 0, tzinfo=tz),
            publish=True,
        )
        db.commit()
        return out["id"]
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            for stmt in (
                "DELETE FROM staff_shift_request_events WHERE request_id IN (SELECT id FROM staff_shift_requests WHERE requester_user_id = ANY(:u))",
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
    c_id = _make_user(role="sales")  # bystander
    a_hdr = {"Authorization": f"Bearer {_token(a_id, sales=True)}"}
    b_hdr = {"Authorization": f"Bearer {_token(b_id, sales=True)}"}
    c_hdr = {"Authorization": f"Bearer {_token(c_id, sales=True)}"}
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    a_src = _publish(admin_id, a_id, date(2027, 3, 8), tz)
    b_tgt = _publish(admin_id, b_id, date(2027, 3, 9), tz)

    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=a_hdr,
        json={"request_type": "swap", "source_entry_id": a_src,
              "target_entry_id": b_tgt},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["id"]

    print("===== involved parties can see; bystander cannot =====")
    # Requester A and target-owner B can read it.
    assert client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=a_hdr
    ).status_code == 200
    assert client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=b_hdr
    ).status_code == 200
    # Bystander C cannot (404, no existence leak).
    resp = client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=c_hdr
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "request_not_found"

    # List visibility matches.
    b_list = client.get(
        "/api/sales/schedule/shift-requests", headers=b_hdr
    ).json()["requests"]
    assert any(r["id"] == req_id for r in b_list)
    c_list = client.get(
        "/api/sales/schedule/shift-requests", headers=c_hdr
    ).json()["requests"]
    assert all(r["id"] != req_id for r in c_list)

    # Admin sees it.
    assert client.get(
        f"/api/admin/schedule/shift-requests/{req_id}", headers=admin_hdr
    ).status_code == 200

    # Bystander can't act on it either.
    assert client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/accept", headers=c_hdr
    ).status_code == 404

    print("shift_swap_privacy smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
