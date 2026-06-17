"""Smoke tests for the Sales Portal Phase 1 auth surface.

Covers:
  - PIN mint by admin, PIN login round-trip, /sales/auth/me
  - Wrong-PIN attempts increment the failure counter
  - 5 failed attempts trigger lockout with Retry-After
  - Successful login resets failure counter
  - Unknown identifier returns the same 401 as a wrong PIN (no enumeration)
  - Sales token gets 403 from admin-only routes (dashboard, payments, etc.)
  - Admin token gets 403 from sales-only routes (/sales/auth/me)
  - Admin token still passes the `require_any_scope` dual-gate
    (POST /api/sales/events/{event_id}/participants)
  - force_pin_change flow: admin mint → first login flagged → change-pin clears it

Mints its own ephemeral admin and sales users; cleans up on exit.
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

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_created_user_ids: list[int] = []


def _make_user(*, role: str, with_pin: str | None = None) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-{suffix}"
        email = f"{role}-smoke-{suffix}@example.com"
        u = User(
            username=username,
            email=email,
            hashed_password=hash_password("not-the-pin-not-the-password"),
            full_name=f"Smoke {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        if with_pin is not None:
            u.pin_hash = hash_password(with_pin)
            u.force_pin_change = False
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username, email
    finally:
        db.close()


def _admin_token(user_id: int) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_access_token(u)
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


def _refresh(user_id: int) -> User:
    db = SessionLocal()
    try:
        return db.get(User, user_id)
    finally:
        db.close()


def main() -> None:
    # B2 wired rate limits onto /auth/pin. This smoke exercises the
    # per-row lockout path (5 bad attempts → 423), which is a different
    # mechanism. Clear any inherited rate-limit state so the production
    # limiter does not 429-mask the row-lockout assertions.
    flush_for_testing()

    # ---- Round-trip: PIN mint by admin, PIN login, /sales/auth/me ----
    admin_id, admin_username, _admin_email = _make_user(role="admin")
    admin_token = _admin_token(admin_id)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    sales_id, sales_username, sales_email = _make_user(role="sales")

    # Admin mints a PIN.
    resp = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    minted_pin = body["pin"]
    assert len(minted_pin) == 6 and minted_pin.isdigit()
    assert body["user"]["force_pin_change"] is True
    assert body["user"]["has_pin"] is True

    # Public kiosk picker exposes display names/usernames for active
    # sales users with minted PINs, but never sequential user ids.
    resp = client.get("/api/sales/auth/staff-picker")
    assert resp.status_code == 200, resp.text
    picker_rows = resp.json()
    picker_row = next(
        (r for r in picker_rows if r["username"] == sales_username),
        None,
    )
    assert picker_row is not None, picker_rows
    assert picker_row["full_name"]
    assert "id" not in picker_row

    # Sales token rejected by admin-only listing endpoint? First, let's
    # exchange the PIN for a sales token.
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 200, resp.text
    sales_data = resp.json()
    assert sales_data["scope"] == "sales"
    assert sales_data["force_pin_change"] is True
    sales_token = sales_data["access_token"]
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # /sales/auth/me works for sales token.
    resp = client.get("/api/sales/auth/me", headers=sales_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == sales_username
    assert resp.json()["force_pin_change"] is True

    # Sales token rejected from admin-only routes.
    for path in ("/api/dashboard/ar-summary", "/api/business-profile",
                 "/api/admin/sales-staff"):
        resp = client.get(path, headers=sales_headers)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)

    # Admin token rejected from sales-only routes.
    resp = client.get("/api/sales/auth/me", headers=admin_headers)
    assert resp.status_code == 403, resp.text

    # Sales token rejected from admin /me — the two surfaces must not
    # cross-read each other's identity payloads.
    resp = client.get("/api/auth/me", headers=sales_headers)
    assert resp.status_code == 403, resp.text

    # Sanity: admin token DOES still work on admin /me.
    resp = client.get("/api/auth/me", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == admin_username

    # Admin token PASSES the dual-gate participant route. Use a fake
    # event_id (404 is fine; we only care that auth let us through).
    resp = client.post(
        "/api/sales/events/9999999/participants",
        headers=admin_headers,
        json={
            "parent_first_name": "Smoke",
            "celebrant_first_name": "Admin",
            "phone": "(210) 555-0123",
        },
    )
    assert resp.status_code in (404, 422), (
        f"admin token should be allowed past the scope gate; got {resp.status_code}: {resp.text}"
    )

    # ---- Identifier privacy: unknown identifier == wrong PIN (both 401) ----
    bogus = client.post(
        "/api/sales/auth/pin",
        json={"identifier": f"does-not-exist-{uuid.uuid4().hex[:8]}", "pin": "111111"},
    )
    assert bogus.status_code == 401, bogus.text
    bad_pin = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "999999"},
    )
    assert bad_pin.status_code == 401, bad_pin.text
    assert bogus.json() == bad_pin.json(), (bogus.json(), bad_pin.json())

    # ---- Successful login resets failure counter ----
    # The wrong-PIN attempt above incremented the counter to 1.
    user = _refresh(sales_id)
    assert user.pin_failed_count >= 1

    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 200, resp.text
    user = _refresh(sales_id)
    assert user.pin_failed_count == 0
    assert user.pin_locked_until is None

    # ---- 5 failed attempts trigger lockout with Retry-After ----
    # Clear rate-limit counters so the bad-PIN loop tests row-lockout in
    # isolation. The per-identifier bucket is at 5/min in production and
    # the loop fires 5 attempts plus a 6th below — flushing here moves
    # the test boundary onto the row-lockout mechanism it is exercising.
    flush_for_testing()
    for i in range(5):
        resp = client.post(
            "/api/sales/auth/pin",
            json={"identifier": sales_username, "pin": "000000"},
        )
        # First 4 → 401; 5th may be 401 (last attempt) or 423 immediately.
        # The implementation locks AFTER incrementing past the cap, so the
        # first 5 returns 401 and the 6th returns 423 — let's confirm.
        assert resp.status_code in (401, 423), (i, resp.status_code, resp.text)

    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "000000"},
    )
    assert resp.status_code == 423, resp.text
    assert "retry-after" in {k.lower() for k in resp.headers.keys()}, resp.headers
    retry_after = int(resp.headers["Retry-After"])
    assert retry_after > 0

    # Even the correct PIN can't unlock during the window.
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 423, resp.text

    # ---- Owner unlock clears the lock ----
    resp = client.post(
        f"/api/admin/sales-staff/{sales_id}/unlock",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pin_locked"] is False

    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 200, resp.text
    sales_token = resp.json()["access_token"]
    sales_headers = {"Authorization": f"Bearer {sales_token}"}

    # ---- force_pin_change flow ----
    user = _refresh(sales_id)
    assert user.force_pin_change is True  # admin mint left this on

    # Change-pin: wrong current PIN → 401.
    resp = client.post(
        "/api/sales/auth/change-pin",
        headers=sales_headers,
        json={"current_pin": "111111", "new_pin": "234567"},
    )
    assert resp.status_code == 401, resp.text

    # Change-pin: same current and new → 400.
    resp = client.post(
        "/api/sales/auth/change-pin",
        headers=sales_headers,
        json={"current_pin": minted_pin, "new_pin": minted_pin},
    )
    assert resp.status_code == 400, resp.text

    # Change-pin: success.
    new_pin = "234567"
    resp = client.post(
        "/api/sales/auth/change-pin",
        headers=sales_headers,
        json={"current_pin": minted_pin, "new_pin": new_pin},
    )
    assert resp.status_code == 204, resp.text

    user = _refresh(sales_id)
    assert user.force_pin_change is False

    # Old PIN no longer works.
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert resp.status_code == 401, resp.text

    # New PIN works.
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": new_pin},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["force_pin_change"] is False

    # Login by EMAIL also works.
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_email, "pin": new_pin},
    )
    assert resp.status_code == 200, resp.text

    # ---- Admin can clear PIN; PIN login then 401s ----
    resp = client.delete(
        f"/api/admin/sales-staff/{sales_id}/pin",
        headers=admin_headers,
    )
    assert resp.status_code == 204, resp.text

    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": new_pin},
    )
    assert resp.status_code == 401, resp.text

    # ---- Listing returns the staff row ----
    resp = client.get("/api/admin/sales-staff", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    listed = next((r for r in rows if r["id"] == sales_id), None)
    assert listed is not None
    assert listed["has_pin"] is False

    print("sales_auth smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
