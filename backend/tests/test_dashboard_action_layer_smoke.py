"""Smoke tests for the dashboard action-layer slice.

Coverage:

  - POST /api/contacts inserts a new contact when no match exists and
    returns ``was_new=true`` plus the canonical ContactResponse shape.
  - Re-POSTing with the same phone returns the existing contact id and
    ``was_new=false`` (no duplicate row created). This is the dedup
    contract the palette's "Create contact" fallback relies on so an
    admin who tries to create a duplicate quietly lands on the
    existing record instead of producing twins.
  - Auth gate: POST without an admin token is rejected.

Cleans up every contact + user it creates.

    venv/bin/python tests/test_dashboard_action_layer_smoke.py
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
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"action-layer-smoke-{suffix}",
            email=f"action-layer-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Action Layer Smoke Admin",
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


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(user_ids, contact_ids):
    db = SessionLocal()
    try:
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def check_create_contact_inserts_new_row(headers, *, unique_suffix):
    body = {
        "first_name": "Palette",
        "last_name": "Created",
        "display_name": "Palette Created",
        "phone": f"(210) 555-{unique_suffix}",
        "email": f"palette-{unique_suffix}@example.com",
    }
    r = client.post("/api/contacts", headers=headers, json=body)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["was_new"] is True
    assert payload["contact"]["display_name"] == "Palette Created"
    assert payload["contact"]["phone_e164"], "phone should normalize"
    return payload["contact"]["id"]


def check_create_contact_dedups_on_phone(headers, *, unique_suffix, first_id):
    # Same phone, different display name — the dedup result should keep
    # the original row's display name (Phase B convention: re-posting is
    # idempotent, not destructive).
    body = {
        "first_name": "Duplicate",
        "last_name": "Attempt",
        "display_name": "Should Not Win",
        "phone": f"(210) 555-{unique_suffix}",
    }
    r = client.post("/api/contacts", headers=headers, json=body)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["was_new"] is False
    assert payload["contact"]["id"] == first_id, (
        payload["contact"]["id"], first_id
    )
    assert payload["contact"]["display_name"] == "Palette Created"


def check_create_contact_requires_admin_auth():
    body = {"display_name": "No Auth", "phone": "(210) 555-0000"}
    r = client.post("/api/contacts", json=body)
    assert r.status_code in (401, 403), r.status_code


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []

    user_id, email = _seed_admin()
    user_ids.append(user_id)
    headers = _login(email)

    suffix = f"{uuid.uuid4().int % 10000:04d}"

    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    def run(name, fn, *args, **kwargs):
        nonlocal failed
        try:
            result = fn(*args, **kwargs)
            checks.append((name, True, None))
            return result
        except AssertionError as exc:
            failed += 1
            checks.append((name, False, str(exc)))
        except Exception as exc:
            failed += 1
            checks.append((name, False, f"unexpected: {exc!r}"))
        return None

    first_id = run(
        "create_contact_inserts_new_row",
        check_create_contact_inserts_new_row,
        headers, unique_suffix=suffix,
    )
    if first_id is not None:
        contact_ids.append(first_id)
        run(
            "create_contact_dedups_on_phone",
            check_create_contact_dedups_on_phone,
            headers, unique_suffix=suffix, first_id=first_id,
        )

    run("create_contact_requires_admin_auth",
        check_create_contact_requires_admin_auth)

    print()
    for name, ok, err in checks:
        if ok:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _cleanup(user_ids, contact_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
