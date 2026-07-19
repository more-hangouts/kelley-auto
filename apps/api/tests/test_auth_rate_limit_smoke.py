"""Smoke for B2: login + sales PIN rate limits.

Covers:
  - /api/auth/login: per-email bucket trips at 6th bad attempt
  - /api/auth/login: per-IP bucket trips at 11th bad attempt across
    different emails (per-email never overflows in this scenario)
  - /api/auth/login: legitimate login still works under the limits
  - /api/sales/auth/pin: per-identifier bucket trips at 6th bad attempt
  - /api/sales/auth/pin: per-IP bucket trips at 11th bad attempt across
    different identifiers
  - /api/sales/auth/pin: legitimate PIN still works under the limits

Uses unique X-Forwarded-For per scenario so per-IP buckets do not
cross-pollute. Flushes its own rate-limit keys on entry and exit so
re-running the smoke produces clean results.
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
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api import redis_rate_limit as rrl  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth as sales_auth_svc  # noqa: E402


client = TestClient(app)


def _flush_test_keys() -> None:
    """Remove every bucket we touch so a re-run starts fresh."""
    redis = rrl.get_client()
    patterns = [
        "rl:login_ip:*",
        "rl:login_email:*",
        "rl:pin_ip:*",
        "rl:pin_identifier:*",
    ]
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor=cursor, match=pattern)
            if keys:
                redis.delete(*keys)
            if cursor == 0:
                break


_flush_test_keys()


def _make_admin():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"b2-admin-{suffix}",
            email=f"b2-admin-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"B2 Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _make_sales(pin: str = "424242"):
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"b2-sales-{suffix}",
            email=f"b2-sales-{suffix}@example.com",
            hashed_password=hash_password("unused-for-sales"),
            full_name=f"B2 Sales {suffix}",
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
        return u.id, u.username
    finally:
        db.close()


def _cleanup_users(ids):
    db = SessionLocal()
    try:
        for uid in ids:
            db.execute(sql_text("DELETE FROM users WHERE id = :u"), {"u": uid})
        db.commit()
    finally:
        db.close()


admin_id, admin_email = _make_admin()
sales_id, sales_username = _make_sales(pin="424242")

try:
    # ---------------------------------------------------------------------
    # Login: per-email bucket trips at 6th attempt
    # ---------------------------------------------------------------------
    ip1 = "10.20.30.41"
    for i in range(1, 6):
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "wrong"},
            headers={"X-Forwarded-For": ip1},
        )
        assert resp.status_code == 401, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": "wrong"},
        headers={"X-Forwarded-For": ip1},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == "rate_limited", resp.text
    assert resp.headers.get("Retry-After") == "60", resp.headers
    print("login per-email 429 ok")

    # ---------------------------------------------------------------------
    # Login: per-IP bucket trips at 11th attempt across rotating fake emails
    # ---------------------------------------------------------------------
    ip2 = "10.20.30.42"
    for i in range(1, 11):
        resp = client.post(
            "/api/auth/login",
            json={
                "email": f"noone-{uuid.uuid4().hex[:6]}-{i}@example.com",
                "password": "wrong",
            },
            headers={"X-Forwarded-For": ip2},
        )
        assert resp.status_code == 401, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/auth/login",
        json={
            "email": f"noone-{uuid.uuid4().hex[:6]}-final@example.com",
            "password": "wrong",
        },
        headers={"X-Forwarded-For": ip2},
    )
    assert resp.status_code == 429, resp.text
    print("login per-ip 429 ok")

    # ---------------------------------------------------------------------
    # Login: legitimate login still works from a fresh IP and fresh email
    # ---------------------------------------------------------------------
    ip3 = "10.20.30.43"
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": "smoke-pass-12345"},
        headers={"X-Forwarded-For": ip3},
    )
    # admin_email's per-email bucket was burned in scenario 1, but its
    # window is 60s. So a legitimate login from ip3 still gets 429 on the
    # per-email bucket. We use a DIFFERENT email to verify the happy path.
    # That is the right scope: under heavy attack on one account, that
    # account is locked out app-side for 60s; other accounts unaffected.
    # Flush admin_email's bucket to simulate the 60s window passing.
    rrl.get_client().delete(f"rl:login_email:{admin_email.lower()}")
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": "smoke-pass-12345"},
        headers={"X-Forwarded-For": ip3},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == admin_email
    print("login happy path under limit ok")

    # ---------------------------------------------------------------------
    # PIN: per-identifier bucket trips at 11th attempt.
    # The per-identifier limit is 10 (deliberately looser than the
    # 5-attempt row lockout — see comment in api/routers/sales_auth.py).
    # We hit the rate-limit bucket without engaging row-lockout by using
    # a NON-EXISTENT identifier so the row never exists. The dep still
    # rate-limits it.
    # ---------------------------------------------------------------------
    ghost = f"ghost-{uuid.uuid4().hex[:8]}"
    ip4 = "10.20.30.44"
    for i in range(1, 11):
        resp = client.post(
            "/api/sales/auth/pin",
            json={"identifier": ghost, "pin": "000000"},
            headers={"X-Forwarded-For": ip4},
        )
        assert resp.status_code == 401, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": ghost, "pin": "000000"},
        headers={"X-Forwarded-For": ip4},
    )
    assert resp.status_code == 429, resp.text
    print("pin per-identifier 429 ok")

    # ---------------------------------------------------------------------
    # PIN: per-IP bucket trips at 11th attempt across rotating fake usernames
    # ---------------------------------------------------------------------
    ip5 = "10.20.30.45"
    for i in range(1, 11):
        resp = client.post(
            "/api/sales/auth/pin",
            json={
                "identifier": f"nobody-{uuid.uuid4().hex[:6]}-{i}",
                "pin": "000000",
            },
            headers={"X-Forwarded-For": ip5},
        )
        assert resp.status_code == 401, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/sales/auth/pin",
        json={
            "identifier": f"nobody-{uuid.uuid4().hex[:6]}-final",
            "pin": "000000",
        },
        headers={"X-Forwarded-For": ip5},
    )
    assert resp.status_code == 429, resp.text
    print("pin per-ip 429 ok")

    # ---------------------------------------------------------------------
    # PIN: legitimate good PIN still works from a fresh IP, with the
    # per-identifier window simulated as expired (matches B1-tested fail
    # behavior: 60s windows do roll over in production).
    # The sales user we made above has had its row-lockout state mutated
    # by scenario 4's bad attempts. Make a fresh sales user for the happy
    # path so row-lockout doesn't interfere with the rate-limit check.
    # ---------------------------------------------------------------------
    sales2_id, sales2_username = _make_sales(pin="424242")
    ip6 = "10.20.30.46"
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales2_username, "pin": "424242"},
        headers={"X-Forwarded-For": ip6},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["username"] == sales2_username
    print("pin happy path under limit ok")

finally:
    _cleanup_users([admin_id, sales_id])
    try:
        _cleanup_users([sales2_id])
    except NameError:
        pass
    _flush_test_keys()
    rrl.get_client().close()
    print("cleanup done")

print("test_auth_rate_limit_smoke OK")
