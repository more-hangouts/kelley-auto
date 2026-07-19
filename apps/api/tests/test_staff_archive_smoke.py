"""Smoke for staff archive (soft delete) + restore.

  1. Archiving a staffer hides them from the default roster, flips
     is_active off, and stamps deleted_at/deleted_by; they show up under
     ?archived=true.
  2. Restore returns them to the active roster (is_active back on).
  3. Guards: an admin can't archive themselves; a terminal re-archive /
     restore-of-active returns a clean 409.
  4. Sales token can't reach the archive endpoint (admin scope only).
"""

import os
import sys
import uuid
from pathlib import Path

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
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

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


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:u)"),
                {"u": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _in_list(hdr, *, archived: bool, user_id: int) -> bool:
    resp = client.get(
        "/api/admin/sales-staff",
        headers=hdr,
        params={"archived": str(archived).lower()},
    )
    assert resp.status_code == 200, resp.text
    return any(r["id"] == user_id for r in resp.json())


def main() -> None:
    admin_id = _make_user(role="admin")
    target_id = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_hdr = {"Authorization": f"Bearer {_token(target_id, sales=True)}"}

    print("===== target starts on the active roster =====")
    assert _in_list(admin_hdr, archived=False, user_id=target_id)
    assert not _in_list(admin_hdr, archived=True, user_id=target_id)

    print("===== sales token is rejected =====")
    resp = client.post(
        f"/api/admin/sales-staff/{target_id}/archive",
        headers=sales_hdr,
        json={},
    )
    assert resp.status_code == 403, resp.text

    print("===== archive hides + deactivates =====")
    resp = client.post(
        f"/api/admin/sales-staff/{target_id}/archive",
        headers=admin_hdr,
        json={"reason": "left the shop"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_archived"] is True
    assert body["is_active"] is False
    assert body["deleted_at"] is not None
    # Hidden from default roster, visible under archived.
    assert not _in_list(admin_hdr, archived=False, user_id=target_id)
    assert _in_list(admin_hdr, archived=True, user_id=target_id)
    # deleted_by + token bump persisted.
    db = SessionLocal()
    try:
        row = db.get(User, target_id)
        assert row.deleted_by_user_id == admin_id
        assert row.token_version == 1
        assert row.deleted_reason == "left the shop"
    finally:
        db.close()

    print("===== re-archive is a clean 409 =====")
    resp = client.post(
        f"/api/admin/sales-staff/{target_id}/archive",
        headers=admin_hdr,
        json={},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "already_archived"

    print("===== restore returns to active roster =====")
    resp = client.post(
        f"/api/admin/sales-staff/{target_id}/restore", headers=admin_hdr
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_archived"] is False
    assert body["is_active"] is True
    assert _in_list(admin_hdr, archived=False, user_id=target_id)

    print("===== restore-of-active is a clean 409 =====")
    resp = client.post(
        f"/api/admin/sales-staff/{target_id}/restore", headers=admin_hdr
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "not_archived"

    print("===== admin can't archive themselves =====")
    resp = client.post(
        f"/api/admin/sales-staff/{admin_id}/archive",
        headers=admin_hdr,
        json={},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "cannot_archive_self"

    print("staff_archive smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
