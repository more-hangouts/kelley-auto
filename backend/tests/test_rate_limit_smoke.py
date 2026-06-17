"""Smoke for the staff money-changing rate limiter.

Phase 13. Two checks:

  - **Helper enforces the limit.** Calling ``_check_user`` 60 times
    in a row succeeds. The 61st raises ``HTTPException(429)``. After
    ``_reset_state``, the bucket is clean again.
  - **Wired on a real route.** ``GET /api/invoices/{id}/pdf`` is one
    of the rate-limited routes. After draining the bucket via
    ``_check_user``, a single PDF GET returns 429 not 200/404.

The PDF smoke covers the route's success path; this file's job is the
limit, not the render.
"""

from __future__ import annotations

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

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api import rate_limit  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        email = f"rate-smoke-{suffix}@example.com"
        u = User(
            username=f"rate-smoke-{suffix}",
            email=email,
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Rate Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, email
    finally:
        db.close()


def _login(email: str) -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(user_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": user_ids},
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_helper_blocks_after_limit(user_id: int):
    rate_limit._reset_state()
    for i in range(rate_limit._LIMIT_PER_MIN):
        rate_limit._check_user(user_id)
    try:
        rate_limit._check_user(user_id)
    except HTTPException as exc:
        assert exc.status_code == 429, exc.status_code
        assert isinstance(exc.detail, dict) and exc.detail.get("code") == "rate_limited"
    else:
        raise AssertionError("expected HTTPException at limit+1")
    rate_limit._reset_state()


def check_pdf_route_returns_429_when_bucket_full(user_id: int, headers: dict):
    """Drain the bucket, then a single PDF GET should 429.

    We use a non-existent invoice id so we don't accidentally render
    a real PDF; the rate-limit dependency runs before the route body,
    so a 429 from the limiter beats any 404 the body would return.
    """
    rate_limit._reset_state()
    # Fill the bucket directly so we don't need to make 60 HTTP calls.
    for _ in range(rate_limit._LIMIT_PER_MIN):
        rate_limit._check_user(user_id)
    resp = client.get("/api/invoices/999999999/pdf", headers=headers)
    assert resp.status_code == 429, (resp.status_code, resp.text)
    body = resp.json()
    assert body.get("detail", {}).get("code") == "rate_limited", body
    rate_limit._reset_state()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids = []
    user_id, email = _seed_admin()
    user_ids.append(user_id)
    headers = _login(email)

    failed = 0

    def run(name, fn, *args, **kwargs):
        nonlocal failed
        try:
            fn(*args, **kwargs)
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {name}: {exc!r}")

    try:
        run("helper_blocks_after_limit", check_helper_blocks_after_limit, user_id)
        run(
            "pdf_route_429_when_bucket_full",
            check_pdf_route_returns_429_when_bucket_full,
            user_id,
            headers,
        )
    finally:
        rate_limit._reset_state()
        _cleanup(user_ids)

    print(f"\nchecks: 2, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
