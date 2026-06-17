"""Smoke for POST /api/sales/auth/kiosk-lock.

Verifies the shared-tablet quick-lock route:
  - Returns 204 with no session required (idempotent + unauthenticated).
  - Clears both sales cookies (session + CSRF) via empty Max-Age=0 Set-Cookie.
  - Does NOT bump users.token_version, so the just-issued sales JWT
    still authenticates on subsequent requests. This is the load-bearing
    invariant: locking one tablet must not sign the stylist out on every
    other device.
  - Contrast: POST /api/sales/auth/logout DOES bump token_version and the
    same JWT becomes 401 immediately after.
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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.cookies import SALES_CSRF_COOKIE, SALES_SESSION_COOKIE  # noqa: E402
from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_created_user_ids: list[int] = []


def _make_user(*, role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{suffix}"
        u = User(
            username=username,
            email=f"{role}-smoke-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin-not-the-password"),
            full_name=f"Smoke {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username
    finally:
        db.close()


def _admin_token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_access_token(db.get(User, user_id))
    finally:
        db.close()


def _token_version(user_id: int) -> int:
    db = SessionLocal()
    try:
        return int(db.get(User, user_id).token_version or 0)
    finally:
        db.close()


def _set_cookie_lines(resp) -> list[str]:
    # httpx exposes each Set-Cookie header as its own entry via
    # `Headers.get_list(name)`; the joined `response.headers["set-cookie"]`
    # would concatenate them and break the per-cookie assertions below.
    return resp.headers.get_list("set-cookie")


def _cookie_cleared(set_cookie_lines: list[str], cookie_name: str) -> bool:
    """True if some Set-Cookie line clears `cookie_name` (empty + Max-Age=0).

    FastAPI emits empty values as `=""` (quoted empty string); a future
    Starlette release could plausibly emit bare `=` instead, so both
    forms count as cleared.
    """
    for line in set_cookie_lines:
        if not line.startswith(f"{cookie_name}="):
            continue
        first_attr = line.split(";", 1)[0]
        value = first_attr.split("=", 1)[1]
        if value in ("", '""') and "Max-Age=0" in line:
            return True
    return False


def _cleanup() -> None:
    if not _created_user_ids:
        return
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": _created_user_ids},
        )
        db.commit()
    finally:
        db.close()


def main() -> None:
    flush_for_testing()

    # ---- Mint admin + sales user, exchange PIN for a sales JWT ----
    admin_id, _ = _make_user(role="admin")
    admin_headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    sales_id, sales_username = _make_user(role="sales")

    resp = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    minted_pin = resp.json()["pin"]

    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 200, resp.text
    sales_token = resp.json()["access_token"]
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    tv_before = _token_version(sales_id)

    # ---- Authenticated kiosk-lock: 204, clears cookies, no bump ----
    resp = client.post("/api/sales/auth/kiosk-lock", headers=sales_headers)
    assert resp.status_code == 204, resp.text

    set_cookies = _set_cookie_lines(resp)
    assert _cookie_cleared(set_cookies, SALES_SESSION_COOKIE), set_cookies
    assert _cookie_cleared(set_cookies, SALES_CSRF_COOKIE), set_cookies

    tv_after_lock = _token_version(sales_id)
    assert tv_after_lock == tv_before, (
        f"kiosk-lock must NOT bump token_version: before={tv_before}, "
        f"after={tv_after_lock}"
    )

    # The bearer token is still alive because token_version did not move.
    resp = client.get("/api/sales/auth/me", headers=sales_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == sales_username

    # ---- Unauthenticated kiosk-lock still 204 + still clears cookies ----
    bare = TestClient(app)
    resp = bare.post("/api/sales/auth/kiosk-lock")
    assert resp.status_code == 204, resp.text
    set_cookies = _set_cookie_lines(resp)
    assert _cookie_cleared(set_cookies, SALES_SESSION_COOKIE), set_cookies
    assert _cookie_cleared(set_cookies, SALES_CSRF_COOKIE), set_cookies

    # ---- Contrast: /auth/logout DOES bump token_version ----
    resp = client.post("/api/sales/auth/logout", headers=sales_headers)
    assert resp.status_code == 204, resp.text
    tv_after_logout = _token_version(sales_id)
    assert tv_after_logout > tv_before, (tv_before, tv_after_logout)

    # And the old sales token is now dead — proves the two routes have
    # genuinely different revocation semantics, not just different names.
    resp = client.get("/api/sales/auth/me", headers=sales_headers)
    assert resp.status_code == 401, resp.text

    print("sales_kiosk_lock smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
