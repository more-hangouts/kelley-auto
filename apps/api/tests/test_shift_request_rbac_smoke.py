"""RBAC smoke for Scheduling Phase 1.

Covers the scope + visibility boundaries on the shift-request endpoints:

  1. A sales token cannot hit the admin queue (403).
  2. An admin token cannot hit the sales create endpoint (403).
  3. Visibility of a direct request: only involved parties (requester +
     named candidate) can read it; an uninvolved coworker gets 404 on GET
     and never sees it in their list. Admin sees every request.
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


def _published(actor_id: int, user_id: int, bdate: date,
               starts: datetime, ends: datetime) -> int:
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


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_request_events "
                    "WHERE request_id IN ("
                    "SELECT id FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    requester_id = _make_user(role="sales")
    candidate_id = _make_user(role="sales")
    bystander_id = _make_user(role="sales")

    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    requester_hdr = {
        "Authorization": f"Bearer {_token(requester_id, sales=True)}"
    }
    candidate_hdr = {
        "Authorization": f"Bearer {_token(candidate_id, sales=True)}"
    }
    bystander_hdr = {
        "Authorization": f"Bearer {_token(bystander_id, sales=True)}"
    }

    shift = _published(
        admin_id, requester_id, date(2026, 9, 14),
        datetime(2026, 9, 14, 9, 0, tzinfo=tz),
        datetime(2026, 9, 14, 17, 0, tzinfo=tz),
    )

    # Requester files a DIRECT cover naming the candidate.
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=requester_hdr,
        json={"request_type": "cover", "source_entry_id": shift,
              "candidate_user_id": candidate_id},
    )
    assert resp.status_code == 200, resp.text
    req = resp.json()
    req_id = req["id"]
    assert req["candidate_user_id"] == candidate_id

    # ============================================================
    # 1) Scope boundaries
    # ============================================================
    print("===== scope boundaries =====")
    # Sales token cannot hit the admin queue.
    resp = client.get(
        "/api/admin/schedule/shift-requests", headers=requester_hdr
    )
    assert resp.status_code == 403, resp.text
    assert "scope_forbidden" in resp.text

    # Admin token cannot hit the sales create endpoint.
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=admin_hdr,
        json={"request_type": "cover", "source_entry_id": shift},
    )
    assert resp.status_code == 403, resp.text
    assert "scope_forbidden" in resp.text

    # Admin CAN read the queue and the specific request.
    resp = client.get(
        "/api/admin/schedule/shift-requests", headers=admin_hdr
    )
    assert resp.status_code == 200, resp.text
    assert any(r["id"] == req_id for r in resp.json()["requests"])
    resp = client.get(
        f"/api/admin/schedule/shift-requests/{req_id}", headers=admin_hdr
    )
    assert resp.status_code == 200, resp.text

    # ============================================================
    # 2) Visibility of a direct request
    # ============================================================
    print("===== direct-request visibility =====")
    # Requester sees it.
    resp = client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=requester_hdr
    )
    assert resp.status_code == 200, resp.text

    # Named candidate sees it (they're involved).
    resp = client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=candidate_hdr
    )
    assert resp.status_code == 200, resp.text

    # Uninvolved coworker gets 404 (no existence leak).
    resp = client.get(
        f"/api/sales/schedule/shift-requests/{req_id}", headers=bystander_hdr
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "request_not_found"

    # List visibility matches: candidate's list includes it, bystander's
    # does not.
    resp = client.get(
        "/api/sales/schedule/shift-requests", headers=candidate_hdr
    )
    assert resp.status_code == 200, resp.text
    assert any(r["id"] == req_id for r in resp.json()["requests"])

    resp = client.get(
        "/api/sales/schedule/shift-requests", headers=bystander_hdr
    )
    assert resp.status_code == 200, resp.text
    assert all(r["id"] != req_id for r in resp.json()["requests"])

    # Bystander also cannot cancel it (hidden → 404).
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{req_id}/cancel",
        headers=bystander_hdr,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "request_not_found"

    print("shift_request_rbac smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
