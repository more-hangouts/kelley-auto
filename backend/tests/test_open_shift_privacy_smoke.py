"""Open-shift board privacy smoke for Scheduling Phase 3.

The staff-facing board (`GET /api/sales/schedule/open-shifts`) must only
expose display copy — no manager/audit plumbing. Also confirms only
`open` posts appear (claimed/cancelled drop off the board).
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

client = TestClient(app)

_user_ids: list[int] = []

ALLOWED_KEYS = {
    "id",
    "business_date",
    "starts_at_local",
    "ends_at_local",
    "late_grace_minutes",
    "note",
}
FORBIDDEN_KEYS = {
    "manager_notes",
    "created_by_user_id",
    "claimed_by_user_id",
    "claimed_request_id",
    "source",
    "status",
    "created_at",
    "updated_at",
}


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
                sql_text(
                    "DELETE FROM open_shift_posts "
                    "WHERE created_by_user_id = ANY(:u)"
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
    sales_id = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_hdr = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    SECRET = "P0SCHED-INTERNAL-COVERAGE-NOTE"
    resp = client.post(
        "/api/admin/schedule/open-shifts",
        headers=admin_hdr,
        json={
            "business_date": "2026-12-21",
            "starts_at_local": datetime(2026, 12, 21, 9, 0, tzinfo=tz).isoformat(),
            "ends_at_local": datetime(2026, 12, 21, 17, 0, tzinfo=tz).isoformat(),
            "manager_notes": SECRET,
        },
    )
    assert resp.status_code == 201, resp.text
    open_id = resp.json()["id"]

    # A cancelled post that must NOT show on the board.
    resp = client.post(
        "/api/admin/schedule/open-shifts",
        headers=admin_hdr,
        json={
            "business_date": "2026-12-22",
            "starts_at_local": datetime(2026, 12, 22, 9, 0, tzinfo=tz).isoformat(),
            "ends_at_local": datetime(2026, 12, 22, 17, 0, tzinfo=tz).isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    cancelled_id = resp.json()["id"]
    resp = client.post(
        f"/api/admin/schedule/open-shifts/{cancelled_id}/cancel",
        headers=admin_hdr,
    )
    assert resp.status_code == 200, resp.text

    print("===== board exposes only display copy =====")
    resp = client.get(
        "/api/sales/schedule/open-shifts",
        headers=sales_hdr,
        params={"from_date": "2026-12-14", "to_date": "2026-12-28"},
    )
    assert resp.status_code == 200, resp.text
    posts = resp.json()["posts"]
    ids = {p["id"] for p in posts}
    assert open_id in ids, "open post missing from the board"
    assert cancelled_id not in ids, "cancelled post must not appear on the board"

    row = next(p for p in posts if p["id"] == open_id)
    keys = set(row.keys())
    assert keys == ALLOWED_KEYS, (
        f"board row keys drift: extra={keys - ALLOWED_KEYS} "
        f"missing={ALLOWED_KEYS - keys}"
    )
    assert not (keys & FORBIDDEN_KEYS), keys & FORBIDDEN_KEYS
    # The note IS shown (it's the manager's posting copy) but only under
    # the sanitized "note" key, never as raw manager_notes.
    assert row["note"] == SECRET
    assert "manager_notes" not in row

    print("open_shift_privacy smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
