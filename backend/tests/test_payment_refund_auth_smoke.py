"""Smoke for refund-route authorization.

Security remediation A2. Refunds are money-changing and destructive
enough that sales-scope tokens must not reach the route body. Admin
tokens should pass the auth gate; this smoke uses a non-existent
payment id and expects the service-layer 404 to prove the admin request
made it past authorization.
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402


client = TestClient(app)
_created_user_ids: list[int] = []


def _make_user(*, role: str) -> User:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"refund-auth-{role}-{suffix}",
            email=f"refund-auth-{role}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Refund Auth {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        _created_user_ids.append(user.id)
        return user
    finally:
        db.close()


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
    admin = _make_user(role="admin")
    sales = _make_user(role="sales")
    admin_headers = {"Authorization": f"Bearer {create_access_token(admin)}"}
    sales_headers = {"Authorization": f"Bearer {create_sales_token(sales)}"}
    payload = {
        "amount_cents": 100,
        "refund_method": "cash",
        "from_unapplied_cents": 100,
        "allocation_refunds": [],
    }

    resp = client.post(
        "/api/payments/999999999/refunds",
        headers=sales_headers,
        json=payload,
    )
    assert resp.status_code == 403, (resp.status_code, resp.text)
    assert resp.json().get("detail") == "scope_forbidden", resp.text

    resp = client.post(
        "/api/payments/999999999/refunds",
        headers=admin_headers,
        json=payload,
    )
    assert resp.status_code == 404, (resp.status_code, resp.text)
    assert resp.json().get("detail", {}).get("code") == "payment_not_found", resp.text


try:
    main()
    print("payment refund auth smoke ok")
finally:
    _cleanup()
