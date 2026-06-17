"""Smoke for D2: server-side logout via token_version bump.

Acceptance per the user spec:

  1. login → protected route 200
  2. logout → 204
  3. old token → 401
  4. new login → protected route 200
  5. inactive user / bumped token_version behavior still matches D5

Plus a few sanity checks that fall naturally out of the design:

  - sales path: PIN-login token → logout → 204 → old sales token → 401
  - cross-scope: an admin /api/auth/logout call carrying a sales token
    is 403 (scope mismatch), not a silent bump
  - idempotency: a second logout call from the now-stale token fails
    with 401, and the user's token_version is NOT bumped a second time
    (so a still-active session on another device isn't re-burned)
  - revocation is per-user: logging out one user doesn't affect a
    different user's valid token

Run with: venv/bin/python tests/test_logout_smoke.py
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth as sales_auth_svc  # noqa: E402


client = TestClient(app)


_user_ids: list[int] = []


def _make_admin(*, pwd: str = "smoke-pass-12345") -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d2-admin-{suffix}",
            email=f"d2-admin-{suffix}@example.com",
            hashed_password=hash_password(pwd),
            full_name=f"D2 Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, u.email, pwd
    finally:
        db.close()


def _make_sales(*, pin: str = "424242") -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d2-sales-{suffix}",
            email=f"d2-sales-{suffix}@example.com",
            hashed_password=hash_password("unused"),
            full_name=f"D2 Sales {suffix}",
            is_active=True,
            role="sales",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.flush()
        sales_auth_svc.set_pin(db, u, pin, force_change=False)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, u.username
    finally:
        db.close()


def _token_version_of(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text("SELECT token_version FROM users WHERE id = :i"),
            {"i": user_id},
        ).scalar()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.commit()
    finally:
        db.close()


try:
    # ---------------------------------------------------------------------
    # 1. Admin: login → /me → logout → /me with old token → 401
    # ---------------------------------------------------------------------
    admin_id, admin_email, admin_pw = _make_admin()
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": admin_pw},
    )
    assert resp.status_code == 200, resp.text
    admin_token = resp.json()["access_token"]
    assert _token_version_of(admin_id) == 0

    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == admin_email
    print("admin login + /me happy path ok")

    resp = client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 204, (resp.status_code, resp.text)
    assert _token_version_of(admin_id) == 1, _token_version_of(admin_id)
    print("admin logout → 204; token_version bumped 0→1 ok")

    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 401, resp.text
    print("admin stale token → 401 ok")

    # ---------------------------------------------------------------------
    # 2. New login works; token_version stayed at 1 (login does not bump).
    # ---------------------------------------------------------------------
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": admin_pw},
    )
    assert resp.status_code == 200, resp.text
    new_admin_token = resp.json()["access_token"]
    assert new_admin_token != admin_token, "should mint a fresh token"
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {new_admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert _token_version_of(admin_id) == 1, _token_version_of(admin_id)
    print("admin re-login + /me ok (token_version unchanged at 1)")

    # ---------------------------------------------------------------------
    # 3. Idempotency: a second logout from the NOW-STALE first token
    # fails with 401 (auth dependency rejects before bump runs), so the
    # counter does not advance a second time and a parallel still-active
    # session is not re-burned.
    # ---------------------------------------------------------------------
    resp = client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 401, resp.text
    assert _token_version_of(admin_id) == 1, _token_version_of(admin_id)
    print("admin double-logout from stale token: 401 + no second bump ok")

    # ---------------------------------------------------------------------
    # 4. Sales path mirrors admin path.
    # ---------------------------------------------------------------------
    sales_id, sales_username = _make_sales(pin="424242")
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "424242"},
    )
    assert resp.status_code == 200, resp.text
    sales_token = resp.json()["access_token"]
    assert _token_version_of(sales_id) == 0

    resp = client.get(
        "/api/sales/auth/me", headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert resp.status_code == 200, resp.text
    print("sales PIN login + /me ok")

    resp = client.post(
        "/api/sales/auth/logout",
        headers={"Authorization": f"Bearer {sales_token}"},
    )
    assert resp.status_code == 204, (resp.status_code, resp.text)
    assert _token_version_of(sales_id) == 1
    resp = client.get(
        "/api/sales/auth/me", headers={"Authorization": f"Bearer {sales_token}"}
    )
    assert resp.status_code == 401, resp.text
    print("sales logout → 204 → stale token → 401 ok")

    # ---------------------------------------------------------------------
    # 5. Cross-scope: an admin token should NOT pass /api/sales/auth/logout
    # (require_sales_scope rejects), and a sales token should not pass
    # /api/auth/logout (require_admin_scope rejects). 403 in both cases
    # so a misrouted client doesn't silently revoke against the wrong
    # surface.
    # ---------------------------------------------------------------------
    resp = client.post(
        "/api/sales/auth/logout",
        headers={"Authorization": f"Bearer {new_admin_token}"},
    )
    assert resp.status_code == 403, resp.text

    # Fresh sales token (we just burned the previous one).
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "424242"},
    )
    assert resp.status_code == 200, resp.text
    fresh_sales_token = resp.json()["access_token"]

    resp = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {fresh_sales_token}"},
    )
    assert resp.status_code == 403, resp.text
    print("cross-scope logout calls return 403 (no silent bump) ok")

    # ---------------------------------------------------------------------
    # 6. Inactive user → 401 even with a valid token_version, matching
    # the D5 baseline. Logout has no special path here — the standard
    # auth dependency rejects before the route handler runs.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE users SET is_active = FALSE WHERE id = :i"),
            {"i": admin_id},
        )
        db.commit()
    finally:
        db.close()
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {new_admin_token}"}
    )
    assert resp.status_code == 401, resp.text
    resp = client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {new_admin_token}"}
    )
    assert resp.status_code == 401, resp.text
    print("inactive user → 401 on /me AND /logout ok")

    # ---------------------------------------------------------------------
    # 7. Revocation is per-user: a second admin's token is unaffected.
    # ---------------------------------------------------------------------
    admin2_id, admin2_email, admin2_pw = _make_admin()
    resp = client.post(
        "/api/auth/login",
        json={"email": admin2_email, "password": admin2_pw},
    )
    admin2_token = resp.json()["access_token"]
    # Make admin2 a separate user; admin1's bumps must not affect them.
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {admin2_token}"}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {admin2_token}"}
    )
    assert resp.status_code == 204, resp.text
    assert _token_version_of(admin2_id) == 1
    assert _token_version_of(admin_id) == 1, "admin1 bump must not have advanced"
    print("revocation is per-user ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_logout_smoke OK")
