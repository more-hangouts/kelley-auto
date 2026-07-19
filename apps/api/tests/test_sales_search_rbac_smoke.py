"""Smoke for the RBAC + response-shape guards on /api/sales/search/leads.

Verifies:
  - Unauthenticated request: 401.
  - Admin token: 403 (sales-scope only — admin global search is /api/search).
  - Sales token: 200, even when no results match (empty list, not 404).
  - Response keys are limited to {type, id, label, sublabel, contact_id,
    assigned_user_id, route}. The sales surface must never expose
    monetary fields (`total`, `balance`, `paid`, `discount`, `amount`,
    `subtotal`, `tax`) — assert recursively on every result to catch
    accidental leakage.
  - Query length < 2 → 422 from router validation.

Seeds an admin + sales user with the standard `*-smoke-` prefixes
already covered by cleanup_admin_smoke_pollution.sql; no extra rows.
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

# Forbidden keys anywhere in a sales search result. Future schema drift
# that adds any of these to the response shape will fail this smoke.
_FORBIDDEN_KEYS = frozenset(
    {"total", "balance", "paid", "discount", "amount", "subtotal", "tax"}
)


def _make_user(*, role: str, with_pin: str | None = None) -> tuple[int, str]:
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
        if with_pin is not None:
            u.pin_hash = hash_password(with_pin)
            u.force_pin_change = False
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


def _assert_no_forbidden_keys(value, path: str) -> None:
    """Walk a JSON-ish value and assert no `_FORBIDDEN_KEYS` appear."""
    if isinstance(value, dict):
        for k, v in value.items():
            assert k not in _FORBIDDEN_KEYS, (
                f"forbidden key {k!r} at {path}: monetary fields must not "
                f"appear in sales search responses"
            )
            _assert_no_forbidden_keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _assert_no_forbidden_keys(item, f"{path}[{i}]")


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

    # ---- Unauthenticated: 401 ----
    resp = client.get("/api/sales/search/leads", params={"q": "anything"})
    assert resp.status_code == 401, resp.text

    # ---- Mint admin and sales users; admin token gets 403 ----
    admin_id, _ = _make_user(role="admin")
    admin_headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": "anything"},
        headers=admin_headers,
    )
    assert resp.status_code == 403, resp.text

    # ---- Sales user: PIN login, sales token works ----
    sales_id, sales_username = _make_user(role="sales")
    pin_resp = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin", headers=admin_headers
    )
    assert pin_resp.status_code == 200, pin_resp.text
    minted_pin = pin_resp.json()["pin"]
    login = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": minted_pin},
    )
    assert login.status_code == 200, login.text
    sales_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # ---- Sales token gets 200 even when no results match ----
    no_match_q = f"zzzz-no-match-{uuid.uuid4().hex[:6]}"
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": no_match_q},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == no_match_q, body
    assert body["results"] == [], body

    # ---- q under MIN_QUERY_LENGTH → 422 from router validation ----
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": "x"},
        headers=sales_headers,
    )
    assert resp.status_code == 422, resp.text

    # ---- Full response shape has no monetary keys ----
    # Even a broad query (e.g. "smoke" which may hit any seed contact)
    # must come back without invoice/quote-shape fields. Walk the whole
    # body, not just results, to catch accidental top-level leakage too.
    resp = client.get(
        "/api/sales/search/leads",
        params={"q": "smoke"},
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_no_forbidden_keys(body, "$")

    # ---- Every result has the exact key set we documented ----
    expected_keys = {
        "type",
        "id",
        "label",
        "sublabel",
        "contact_id",
        "assigned_user_id",
        "route",
    }
    for i, r in enumerate(body["results"]):
        assert set(r.keys()) == expected_keys, (i, r)
        assert r["type"] in {"appointment", "contact", "event"}, r
        # Route must be empty-string-free and look like a sales path.
        assert r["route"].startswith("/appointments/"), r["route"]

    print("sales_search_rbac smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
