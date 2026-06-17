"""Smoke for D4: password reset flow.

Covers the user-specified acceptance plus a couple of nearby
invariants. The reset email is captured via a monkey-patched
transport so the smoke can extract the plaintext token from the URL
without inspecting prod logs.

  1. Request for existing AND non-existent email return identical
     `204` with empty body.
  2. The DB row stores only the SHA-256 hash; the plaintext token
     never appears in `password_reset_tokens`.
  3. Valid token → 204, password is updated, and every existing JWT
     for that user becomes 401 (token_version was bumped).
  4. Reused token → 400 `reset_invalid_or_expired`.
  5. Expired token → 400 `reset_invalid_or_expired` (backdated row).
  6. Malformed / unknown token → 400 `reset_invalid_or_expired`.
  7. Per-email request limiter trips at the 4th attempt, returns 429
     without leaking whether the account exists.
  8. Per-IP request limiter trips at the 11th attempt across rotating
     fake emails.
  9. New token request invalidates a still-fresh prior token.
 10. Reset on a deactivated user → silently 204 on request,
     400 on confirm even with a valid token.

Run with: venv/bin/python tests/test_password_reset_smoke.py
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

import hashlib  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api import redis_rate_limit as rrl  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    hash_password,
    verify_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import PasswordResetToken, User  # noqa: E402
from services import password_reset  # noqa: E402


client = TestClient(app)


_user_ids: list[int] = []
_captured_emails: list = []


class _CapturingTransport:
    """Replace the real transport for the smoke run.

    The reset URL contains the plaintext token; capturing the rendered
    message gives the test access to it without any service-layer
    leakage. Service code never returns plaintext to the caller.
    """

    def send(self, msg):
        _captured_emails.append(msg)


def _flush_buckets() -> None:
    rrl.flush_for_testing(
        patterns=[
            "rl:password_reset_ip:*",
            "rl:password_reset_email:*",
            "rl:password_reset_confirm_ip:*",
        ]
    )


def _make_admin(*, pwd: str = "Smoke-Pass-12345!", active: bool = True) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d4-admin-{suffix}",
            email=f"d4-admin-{suffix}@example.com",
            hashed_password=hash_password(pwd),
            full_name=f"D4 Admin {suffix}",
            is_active=active,
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


def _extract_token_from_last_email() -> str:
    """Pull `?token=...` from the most recent captured email."""
    assert _captured_emails, "no email captured"
    text = _captured_emails[-1].text
    assert "?token=" in text, text
    return text.split("?token=", 1)[1].splitlines()[0].strip()


def _latest_reset_row(user_id: int) -> PasswordResetToken | None:
    db = SessionLocal()
    try:
        return (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user_id)
            .order_by(PasswordResetToken.id.desc())
            .first()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM password_reset_tokens WHERE user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.commit()
    finally:
        db.close()


# Monkey-patch the transport once for the whole smoke run. The service
# imports `get_email_transport` lazily inside `_send_reset_email` so a
# module-level swap below is sufficient.
_original_get_transport = password_reset.get_email_transport
password_reset.get_email_transport = lambda: _CapturingTransport()


_flush_buckets()

try:
    # ---------------------------------------------------------------------
    # 1. Identical response for existing + non-existent email.
    # Background-task email send means we wait for the task to finish
    # before inspecting captured state; TestClient does that for us.
    # ---------------------------------------------------------------------
    admin_id, admin_email, admin_pw = _make_admin()
    resp1 = client.post(
        "/api/auth/password-reset/request",
        json={"email": admin_email},
    )
    resp2 = client.post(
        "/api/auth/password-reset/request",
        json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com"},
    )
    assert resp1.status_code == 204 and resp2.status_code == 204, (resp1.text, resp2.text)
    assert resp1.content == b"" and resp2.content == b""
    assert resp1.headers.get("content-length") in (None, "0"), resp1.headers
    print("request: existing + non-existent email both 204 + empty body ok")

    # Captured emails: exactly one (for the real user).
    assert len(_captured_emails) == 1, len(_captured_emails)
    assert _captured_emails[0].to == admin_email
    plaintext = _extract_token_from_last_email()
    assert len(plaintext) >= 32, len(plaintext)
    print(f"only the existing-email branch sent an email (len(token)={len(plaintext)}) ok")

    # ---------------------------------------------------------------------
    # 2. DB stores hash only, not plaintext.
    # ---------------------------------------------------------------------
    row = _latest_reset_row(admin_id)
    assert row is not None
    assert row.token_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert row.token_hash != plaintext, "stored value must differ from plaintext"
    assert len(row.token_hash) == 64, len(row.token_hash)
    # Direct SQL check: no column anywhere on the row equals the plaintext.
    db = SessionLocal()
    try:
        leak = db.execute(
            sql_text(
                "SELECT id FROM password_reset_tokens "
                "WHERE token_hash = :p OR id::text = :p"
            ),
            {"p": plaintext},
        ).first()
        assert leak is None, leak
    finally:
        db.close()
    print("DB stores SHA-256 hex only; plaintext token never persisted ok")

    # ---------------------------------------------------------------------
    # 3. Valid token → password updated + old JWT dies (token_version bump).
    # ---------------------------------------------------------------------
    # Mint a JWT for the user BEFORE the reset, then prove it dies after.
    db = SessionLocal()
    try:
        u = db.get(User, admin_id)
        pre_reset_token = create_access_token(u)
        pre_reset_tv = u.token_version
    finally:
        db.close()
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {pre_reset_token}"}
    )
    assert resp.status_code == 200, resp.text

    new_pwd = "New-Password-After-Reset!22"
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": plaintext, "new_password": new_pwd},
    )
    assert resp.status_code == 204, resp.text

    # JWT minted before the reset is now 401
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {pre_reset_token}"}
    )
    assert resp.status_code == 401, resp.text

    # Old password rejected on login; new one accepted.
    resp = client.post(
        "/api/auth/login", json={"email": admin_email, "password": admin_pw}
    )
    assert resp.status_code == 401, resp.text
    resp = client.post(
        "/api/auth/login", json={"email": admin_email, "password": new_pwd}
    )
    assert resp.status_code == 200, resp.text

    # token_version actually advanced
    db = SessionLocal()
    try:
        u = db.get(User, admin_id)
        assert u.token_version == pre_reset_tv + 1, (u.token_version, pre_reset_tv)
        assert verify_password(new_pwd, u.hashed_password)
        assert not verify_password(admin_pw, u.hashed_password)
    finally:
        db.close()
    print("valid token → 204 + password swapped + old JWT 401 + token_version+1 ok")

    # ---------------------------------------------------------------------
    # 4. Reused token → 400. The just-consumed plaintext should fail.
    # ---------------------------------------------------------------------
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": plaintext, "new_password": "Another-Pass-12345!"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "reset_invalid_or_expired"
    print("reused token → 400 reset_invalid_or_expired ok")

    # ---------------------------------------------------------------------
    # 5. Expired token → 400. Backdate a freshly-minted token row.
    # ---------------------------------------------------------------------
    _flush_buckets()
    _captured_emails.clear()
    resp = client.post(
        "/api/auth/password-reset/request", json={"email": admin_email}
    )
    assert resp.status_code == 204
    expired_plaintext = _extract_token_from_last_email()
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE password_reset_tokens SET expires_at = :t "
                "WHERE token_hash = :h"
            ),
            {
                "t": datetime.now(timezone.utc) - timedelta(minutes=5),
                "h": hashlib.sha256(expired_plaintext.encode()).hexdigest(),
            },
        )
        db.commit()
    finally:
        db.close()
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": expired_plaintext, "new_password": "Yet-Another-Pass!22"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "reset_invalid_or_expired"
    print("expired token → 400 reset_invalid_or_expired ok")

    # ---------------------------------------------------------------------
    # 6. Malformed / unknown token → 400 same uniform error.
    # ---------------------------------------------------------------------
    for label, bad in (
        ("unknown", "totally-unknown-token-with-enough-length-here"),
        ("garbage", "!!!" + "?" * 30),
        ("near-real-shape", "a" * 43),  # plausible length, wrong content
    ):
        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": bad, "new_password": "Some-New-Pass-99!"},
        )
        assert resp.status_code == 400, (label, resp.text)
        assert resp.json()["detail"] == "reset_invalid_or_expired", (label, resp.text)
    print("malformed / unknown tokens → uniform 400 ok")

    # ---------------------------------------------------------------------
    # 7. Per-email request limiter trips at the 4th attempt. Uses
    # X-Forwarded-For to engage the limiter (TestClient default would
    # bypass per the project test pattern). 3 successful 204s, then
    # 429.
    # ---------------------------------------------------------------------
    _flush_buckets()
    _captured_emails.clear()
    target_email = f"d4-flood-{uuid.uuid4().hex[:6]}@example.com"  # nonexistent
    fake_ip = "203.0.113.55"
    for i in range(3):
        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": target_email},
            headers={"X-Forwarded-For": fake_ip},
        )
        assert resp.status_code == 204, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/auth/password-reset/request",
        json={"email": target_email},
        headers={"X-Forwarded-For": fake_ip},
    )
    assert resp.status_code == 429, resp.text
    # Crucial: the 429 detail must NOT reveal account existence.
    assert resp.json()["detail"] == "rate_limited", resp.text
    print("per-email request limiter trips at 4th attempt; no enumeration leak ok")

    # ---------------------------------------------------------------------
    # 8. Per-IP request limiter trips at the 11th rotating-email attempt.
    # ---------------------------------------------------------------------
    _flush_buckets()
    fake_ip2 = "203.0.113.66"
    for i in range(10):
        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": f"flood-{i}-{uuid.uuid4().hex[:4]}@example.com"},
            headers={"X-Forwarded-For": fake_ip2},
        )
        assert resp.status_code == 204, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/auth/password-reset/request",
        json={"email": f"flood-final-{uuid.uuid4().hex[:4]}@example.com"},
        headers={"X-Forwarded-For": fake_ip2},
    )
    assert resp.status_code == 429, resp.text
    print("per-IP request limiter trips at 11th rotating-email attempt ok")

    # ---------------------------------------------------------------------
    # 9. New request invalidates a prior still-fresh token.
    # ---------------------------------------------------------------------
    _flush_buckets()
    _captured_emails.clear()
    admin2_id, admin2_email, admin2_pw = _make_admin()
    resp = client.post(
        "/api/auth/password-reset/request", json={"email": admin2_email}
    )
    assert resp.status_code == 204
    first_plaintext = _extract_token_from_last_email()
    resp = client.post(
        "/api/auth/password-reset/request", json={"email": admin2_email}
    )
    assert resp.status_code == 204
    second_plaintext = _extract_token_from_last_email()
    assert first_plaintext != second_plaintext

    # The first token must now fail.
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": first_plaintext, "new_password": "Should-Not-Work!11"},
    )
    assert resp.status_code == 400, resp.text
    # The second works.
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": second_plaintext, "new_password": "Replaces-First-22!"},
    )
    assert resp.status_code == 204, resp.text
    print("re-issuing a reset invalidates the prior unused token ok")

    # ---------------------------------------------------------------------
    # 10. Deactivated user: request silently 204 (no email), confirm 400.
    # ---------------------------------------------------------------------
    _flush_buckets()
    _captured_emails.clear()
    admin3_id, admin3_email, admin3_pw = _make_admin()
    # First request gets a token while the user is still active.
    resp = client.post(
        "/api/auth/password-reset/request", json={"email": admin3_email}
    )
    assert resp.status_code == 204
    live_token = _extract_token_from_last_email()
    # Deactivate the user.
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE users SET is_active = FALSE WHERE id = :i"),
            {"i": admin3_id},
        )
        db.commit()
    finally:
        db.close()
    # New request after deactivation: 204 but no email captured.
    _captured_emails.clear()
    resp = client.post(
        "/api/auth/password-reset/request", json={"email": admin3_email}
    )
    assert resp.status_code == 204
    assert _captured_emails == [], _captured_emails
    # Confirm with the previously-minted valid token: 400 (user not eligible)
    resp = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": live_token, "new_password": "Deactivated-User-Pass!"},
    )
    assert resp.status_code == 400, resp.text
    print("deactivated user: silent 204 on request, 400 on confirm ok")

finally:
    password_reset.get_email_transport = _original_get_transport
    _flush_buckets()
    _cleanup()
    rrl.get_client().close()
    print("cleanup done")

print("\ntest_password_reset_smoke OK")
