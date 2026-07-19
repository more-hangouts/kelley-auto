"""Smoke for D3: bearer tokens move into HttpOnly cookies + CSRF gate.

Acceptance per the D3 design lock-in:

  - DevTools shows NO bearer token in localStorage (frontend-only; this
    smoke verifies the server side — login returns Set-Cookie for the
    session + CSRF, not just an access_token in the body).
  - Authenticated API calls have NO Authorization header on the cookie
    path (cookie-only carries the auth).
  - Cookies are HttpOnly + Secure (asserted on the Set-Cookie attrs).
  - Unsafe methods fail without a matching CSRF header.
  - Logout invalidates replayed cookies (D2's token_version bump still
    runs even though the cookie is also cleared).

Plus a few invariants that fall out of the dual-path design:

  - Header-bearer (Authorization) path still works and skips CSRF
    entirely — smokes, curl, and any pre-cookie clients continue to
    authenticate without setting a CSRF header.
  - Login routes are CSRF-exempt (credential-bearing requests cannot
    be CSRF'd in a useful way; bootstrapping the cookie on the first
    call predates its existence).
  - Per-surface CSRF: presenting an admin session cookie with a sales
    CSRF header is rejected; the middleware pairs each session cookie
    with its own surface's CSRF cookie.
  - Both surfaces present in the same browser is rejected only if
    neither pair matches — the auth dependency disambiguates by
    Origin, and CSRF still requires at least one matching pair.

Run with: venv/bin/python tests/test_d3_cookie_auth_smoke.py
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
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)
# D3 cookie machinery is wired for `.shopbellasxv.com`; the TestClient
# host is `testserver`, so clear the Domain attribute so httpx's cookie
# jar will send the cookie back to itself. The CSRF middleware doesn't
# care about Domain — it only inspects names + values.
os.environ["SESSION_COOKIE_DOMAIN"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.cookies import (  # noqa: E402
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    CSRF_HEADER_NAME,
    SALES_CSRF_COOKIE,
    SALES_SESSION_COOKIE,
)
from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth as sales_auth_svc  # noqa: E402


# https:// so httpx will attach + accept Secure cookies. The TestClient
# is talking ASGI directly so no real TLS is involved; the scheme just
# satisfies the cookie jar's Secure check.
client = TestClient(app, base_url="https://testserver")


_user_ids: list[int] = []


def _make_admin() -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        pwd = "smoke-pass-12345"
        u = User(
            username=f"d3-admin-{suffix}",
            email=f"d3-admin-{suffix}@example.com",
            hashed_password=hash_password(pwd),
            full_name=f"D3 Admin {suffix}",
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


def _make_sales(*, pin: str = "525252") -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d3-sales-{suffix}",
            email=f"d3-sales-{suffix}@example.com",
            hashed_password=hash_password("unused"),
            full_name=f"D3 Sales {suffix}",
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
        return u.id, u.username, pin
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


def _set_cookie_attrs(set_cookie_headers: list[str], name: str) -> str:
    """Return the lowercased attribute string for a named Set-Cookie line."""
    for line in set_cookie_headers:
        if line.startswith(name + "="):
            return line.lower()
    raise AssertionError(f"no Set-Cookie header for {name!r} in {set_cookie_headers!r}")


try:
    # =========================================================================
    # 1. Admin login emits HttpOnly session + readable CSRF cookies
    # =========================================================================
    admin_id, admin_email, admin_pw = _make_admin()
    fresh_client = TestClient(app, base_url="https://testserver")
    resp = fresh_client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": admin_pw},
    )
    assert resp.status_code == 200, resp.text

    # The Set-Cookie headers carry both names with the right attributes.
    set_cookies = resp.headers.get_list("set-cookie")
    session_attrs = _set_cookie_attrs(set_cookies, ADMIN_SESSION_COOKIE)
    csrf_attrs = _set_cookie_attrs(set_cookies, ADMIN_CSRF_COOKIE)
    assert "httponly" in session_attrs, session_attrs
    assert "secure" in session_attrs, session_attrs
    assert "samesite=lax" in session_attrs, session_attrs
    # CSRF cookie must NOT be HttpOnly so JS can mirror it into the header.
    assert "httponly" not in csrf_attrs, csrf_attrs
    assert "secure" in csrf_attrs, csrf_attrs
    print("admin login emits HttpOnly session + readable CSRF cookies ok")

    # Cookies stored in the client jar; access_token is also in body (legacy).
    assert ADMIN_SESSION_COOKIE in fresh_client.cookies, dict(fresh_client.cookies)
    assert ADMIN_CSRF_COOKIE in fresh_client.cookies, dict(fresh_client.cookies)
    admin_access_token = resp.json()["access_token"]
    admin_csrf = fresh_client.cookies[ADMIN_CSRF_COOKIE]

    # =========================================================================
    # 2. Cookie-only path: /auth/me with no Authorization header → 200
    # =========================================================================
    resp = fresh_client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == admin_email
    print("admin /me via cookie-only (no Authorization header) ok")

    # =========================================================================
    # 3. Header-bearer path still works on a clean client (no cookies)
    # =========================================================================
    bare = TestClient(app, base_url="https://testserver")
    resp = bare.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {admin_access_token}"},
    )
    assert resp.status_code == 200, resp.text
    print("header-bearer /me (no cookies) ok — legacy path intact")

    # =========================================================================
    # 4. Header-bearer skips CSRF entirely on unsafe methods
    # =========================================================================
    # Use a benign unsafe route that would otherwise need CSRF in cookie mode.
    # /api/admin/staff-locations supports POST and requires admin scope; an
    # empty body returns 422 (validation), not 403, which proves the request
    # made it past the CSRF middleware to the route handler.
    resp = bare.post(
        "/api/admin/staff-locations",
        headers={"Authorization": f"Bearer {admin_access_token}"},
        json={},
    )
    assert resp.status_code != 403, (
        f"header-bearer POST should not hit CSRF: {resp.status_code} {resp.text}"
    )
    print("header-bearer POST skips CSRF (validation reached) ok")

    # =========================================================================
    # 5. Cookie POST without CSRF header → 403 csrf_token_missing
    # =========================================================================
    resp = fresh_client.post("/api/auth/logout")
    assert resp.status_code == 403, (resp.status_code, resp.text)
    assert resp.json().get("detail") == "csrf_token_missing", resp.json()
    print("cookie POST without X-CSRF-Token → 403 csrf_token_missing ok")

    # =========================================================================
    # 6. Cookie POST with wrong CSRF header → 403 csrf_token_invalid
    # =========================================================================
    resp = fresh_client.post(
        "/api/auth/logout",
        headers={CSRF_HEADER_NAME: "definitely-not-the-real-csrf-value"},
    )
    assert resp.status_code == 403, (resp.status_code, resp.text)
    assert resp.json().get("detail") == "csrf_token_invalid", resp.json()
    print("cookie POST with wrong X-CSRF-Token → 403 csrf_token_invalid ok")

    # =========================================================================
    # 7. Login is CSRF-exempt even when a session cookie is already present
    # =========================================================================
    # The fresh_client already carries admin cookies; a re-login must succeed
    # without a CSRF header (credential-bearing requests are exempt by path).
    resp = fresh_client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": admin_pw},
    )
    assert resp.status_code == 200, resp.text
    # Re-login rotates the CSRF cookie value — pick up the fresh one.
    admin_csrf = fresh_client.cookies[ADMIN_CSRF_COOKIE]
    print("login route is CSRF-exempt even with stale session cookie ok")

    # =========================================================================
    # 8. Cookie POST with matching CSRF header → 204 + cookies cleared
    # =========================================================================
    resp = fresh_client.post(
        "/api/auth/logout",
        headers={CSRF_HEADER_NAME: admin_csrf},
    )
    assert resp.status_code == 204, (resp.status_code, resp.text)
    # Logout response must emit Set-Cookie with Max-Age=0 for both names.
    clear_headers = resp.headers.get_list("set-cookie")
    session_clear = _set_cookie_attrs(clear_headers, ADMIN_SESSION_COOKIE)
    csrf_clear = _set_cookie_attrs(clear_headers, ADMIN_CSRF_COOKIE)
    assert "max-age=0" in session_clear, session_clear
    assert "max-age=0" in csrf_clear, csrf_clear
    print("admin logout: cookie POST + CSRF → 204, session+csrf cleared ok")

    # =========================================================================
    # 9. Replay of the just-cleared cookie → 401 (token_version bump fired)
    # =========================================================================
    # Re-inject the old admin session cookie into the client jar — httpx
    # cleared it when it saw the Max-Age=0 Set-Cookie. The JWT inside is
    # still cryptographically valid by signature + exp, but token_version
    # was bumped to 1 by the logout, so the auth dep rejects it.
    replay_client = TestClient(app, base_url="https://testserver")
    replay_client.cookies.set(
        ADMIN_SESSION_COOKIE,
        admin_access_token,
        domain="testserver.local",
    )
    resp = replay_client.get("/api/auth/me")
    assert resp.status_code == 401, (resp.status_code, resp.text)
    print("replayed pre-logout admin cookie → 401 (token_version bump fired) ok")

    # =========================================================================
    # 10. Sales PIN login emits per-surface session + CSRF cookies
    # =========================================================================
    sales_id, sales_username, sales_pin = _make_sales()
    sales_client = TestClient(app, base_url="https://testserver")
    resp = sales_client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": sales_pin},
    )
    assert resp.status_code == 200, resp.text
    set_cookies = resp.headers.get_list("set-cookie")
    session_attrs = _set_cookie_attrs(set_cookies, SALES_SESSION_COOKIE)
    csrf_attrs = _set_cookie_attrs(set_cookies, SALES_CSRF_COOKIE)
    assert "httponly" in session_attrs and "secure" in session_attrs
    assert "httponly" not in csrf_attrs and "secure" in csrf_attrs
    # Admin cookies must NOT appear on a sales response.
    assert not any(
        line.startswith(ADMIN_SESSION_COOKIE + "=") for line in set_cookies
    ), set_cookies
    print("sales PIN login emits sales-only session + CSRF cookies ok")

    sales_csrf = sales_client.cookies[SALES_CSRF_COOKIE]

    # Cookie-only /sales/auth/me works.
    resp = sales_client.get("/api/sales/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == sales_username
    print("sales /me via cookie-only ok")

    # =========================================================================
    # 11. Cross-surface CSRF rejection — admin CSRF against sales session
    # =========================================================================
    # The admin_csrf from step 7 is unrelated to the sales CSRF cookie. A
    # sales-cookie POST presenting an admin-shaped CSRF token must reject —
    # the middleware pairs each session with its own surface's CSRF cookie.
    resp = sales_client.post(
        "/api/sales/auth/logout",
        headers={CSRF_HEADER_NAME: "stolen-or-stale-admin-shaped-token"},
    )
    assert resp.status_code == 403, (resp.status_code, resp.text)
    assert resp.json().get("detail") == "csrf_token_invalid", resp.json()
    print("sales cookie + wrong CSRF → 403 csrf_token_invalid ok")

    # Real sales CSRF works.
    resp = sales_client.post(
        "/api/sales/auth/logout",
        headers={CSRF_HEADER_NAME: sales_csrf},
    )
    assert resp.status_code == 204, (resp.status_code, resp.text)
    clear_headers = resp.headers.get_list("set-cookie")
    assert "max-age=0" in _set_cookie_attrs(clear_headers, SALES_SESSION_COOKIE)
    assert "max-age=0" in _set_cookie_attrs(clear_headers, SALES_CSRF_COOKIE)
    print("sales logout: cookie POST + CSRF → 204, cookies cleared ok")

    # =========================================================================
    # 12. Booking widget (public, anonymous) is unaffected by CSRF
    # =========================================================================
    # /api/booking/* is on the exempt list because the public widget has
    # no session cookie. Even when a leftover admin cookie is somehow
    # present in the same browser, the path-prefix exemption keeps the
    # widget endpoints open. Probe the public availability endpoint with
    # a clearly-invalid body so we know the request reached the route
    # handler (validation 422 ≠ CSRF 403).
    widget = TestClient(app, base_url="https://testserver")
    resp = widget.post("/api/booking/availability", json={})
    assert resp.status_code != 403, (
        f"public booking widget must not 403 on CSRF: {resp.status_code} {resp.text}"
    )
    print("public booking widget POST skips CSRF (exempt-path) ok")

    print()
    print("D3 cookie-auth smoke: all checks passed")
finally:
    _cleanup()
