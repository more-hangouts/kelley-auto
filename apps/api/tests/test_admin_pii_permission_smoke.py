"""Smoke test for granting / revoking BHPH application-PII access.

The `lead_applications:read_sensitive` permission is what stands between
an admin and a customer's decrypted DOB / driver's license / SSN. Before
this surface existed it could only be changed by hand-editing the
`users.permissions` JSONB in psql, so the rules it now enforces are worth
pinning down:

  1. The roster exposes `can_view_application_pii` so the UI can render a
     switch without parsing the raw permission list.
  2. PATCH grants and revokes it, and the change actually reaches the
     gated endpoint (403 before a grant, not-403 after).
  3. Only admins may hold it — granting to a sales/user role is a 422,
     because `require_lead_application_pii` checks admin scope first and
     the permission would otherwise be invisible dead weight.
  4. Demotion out of admin strips the grant, so promoting someone back to
     admin later does not silently restore PII access.
  5. Revoking invalidates live tokens (token_version bump), so access
     ends immediately instead of whenever the old JWT happened to expire.
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
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    LEAD_APPLICATION_PII_PERMISSION,
    create_access_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


def _make_user(*, role: str, permissions: list[str] | None = None) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-pii-{suffix}",
            email=f"{role}-pii-{suffix}@example.com",
            hashed_password=hash_password("not-a-real-password"),
            full_name=f"PII {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=list(permissions or []),
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_access_token(db.get(User, user_id))
    finally:
        db.close()


def _permissions_of(user_id: int) -> list[str]:
    db = SessionLocal()
    try:
        return list(db.get(User, user_id).permissions or [])
    finally:
        db.close()


def _token_version_of(user_id: int) -> int:
    db = SessionLocal()
    try:
        return db.get(User, user_id).token_version or 0
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for uid in _user_ids:
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
        db.commit()
    finally:
        db.close()


def main() -> None:
    # The admin doing the granting. Holds the permission itself so it can
    # also exercise the gated read path.
    actor_id = _make_user(
        role="admin", permissions=[LEAD_APPLICATION_PII_PERMISSION]
    )
    actor_hdr = {"Authorization": f"Bearer {_token(actor_id)}"}

    print("===== roster exposes the flag =====")
    target_id = _make_user(role="admin")
    resp = client.get("/api/admin/sales-staff", headers=actor_hdr)
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()}
    assert rows[target_id]["can_view_application_pii"] is False, rows[target_id]
    assert rows[actor_id]["can_view_application_pii"] is True, rows[actor_id]

    print("===== gated endpoint refuses before the grant =====")
    target_hdr = {"Authorization": f"Bearer {_token(target_id)}"}
    # Event id is irrelevant here: the permission gate runs as a dependency,
    # before the handler ever looks for the event. A 403 proves the gate bit.
    resp = client.get("/api/events/1/application", headers=target_hdr)
    assert resp.status_code == 403, resp.text

    print("===== PATCH grants it =====")
    resp = client.patch(
        f"/api/admin/sales-staff/{target_id}",
        json={"can_view_application_pii": True},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_view_application_pii"] is True, resp.text
    assert LEAD_APPLICATION_PII_PERMISSION in _permissions_of(target_id)

    # The grant is live immediately — the gate is read from the DB row per
    # request, so the pre-existing token now passes it. Anything other than
    # 403 means the permission check no longer rejects this user.
    resp = client.get("/api/events/1/application", headers=target_hdr)
    assert resp.status_code != 403, resp.text

    print("===== granting to a non-admin is rejected =====")
    sales_id = _make_user(role="sales")
    resp = client.patch(
        f"/api/admin/sales-staff/{sales_id}",
        json={"can_view_application_pii": True},
        headers=actor_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert (
        resp.json()["detail"]["code"] == "pii_permission_requires_admin"
    ), resp.text
    assert LEAD_APPLICATION_PII_PERMISSION not in _permissions_of(sales_id)

    print("===== revoking bumps token_version =====")
    before_tv = _token_version_of(target_id)
    resp = client.patch(
        f"/api/admin/sales-staff/{target_id}",
        json={"can_view_application_pii": False},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_view_application_pii"] is False, resp.text
    assert LEAD_APPLICATION_PII_PERMISSION not in _permissions_of(target_id)
    assert _token_version_of(target_id) > before_tv, "revoke must bump tokens"

    # The token issued before the revoke is now dead, not merely
    # permission-less: a bumped version fails JWT validation outright.
    resp = client.get("/api/events/1/application", headers=target_hdr)
    assert resp.status_code == 401, resp.text

    print("===== no-op writes do not churn tokens =====")
    steady_tv = _token_version_of(target_id)
    resp = client.patch(
        f"/api/admin/sales-staff/{target_id}",
        json={"can_view_application_pii": False},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert _token_version_of(target_id) == steady_tv, "no-op must not bump"

    print("===== demotion strips the grant =====")
    demote_id = _make_user(
        role="admin", permissions=[LEAD_APPLICATION_PII_PERMISSION]
    )
    resp = client.patch(
        f"/api/admin/sales-staff/{demote_id}",
        json={"role": "sales"},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_view_application_pii"] is False, resp.text
    assert LEAD_APPLICATION_PII_PERMISSION not in _permissions_of(demote_id)

    # Promoting back to admin must NOT resurrect the stripped permission.
    resp = client.patch(
        f"/api/admin/sales-staff/{demote_id}",
        json={"role": "admin"},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_view_application_pii"] is False, resp.text

    print("===== role + permission in one request =====")
    # Promote to admin and grant in the same PATCH: the grant is validated
    # against the role the request lands on, not the one it replaced.
    combo_id = _make_user(role="sales")
    resp = client.patch(
        f"/api/admin/sales-staff/{combo_id}",
        json={"role": "admin", "can_view_application_pii": True},
        headers=actor_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_view_application_pii"] is True, resp.text

    # The mirror case must fail atomically: demoting while granting is a
    # contradiction, and the rejection must leave the row untouched rather
    # than half-applying the demotion.
    contra_id = _make_user(
        role="admin", permissions=[LEAD_APPLICATION_PII_PERMISSION]
    )
    resp = client.patch(
        f"/api/admin/sales-staff/{contra_id}",
        json={"role": "sales", "can_view_application_pii": True},
        headers=actor_hdr,
    )
    assert resp.status_code == 422, resp.text
    db = SessionLocal()
    try:
        row = db.get(User, contra_id)
        assert row.role == "admin", f"rolled-back role, got {row.role}"
        assert LEAD_APPLICATION_PII_PERMISSION in (row.permissions or [])
    finally:
        db.close()

    print("===== sales tokens cannot reach this surface =====")
    resp = client.patch(
        f"/api/admin/sales-staff/{target_id}",
        json={"can_view_application_pii": True},
        headers={"Authorization": f"Bearer {_token(sales_id)}"},
    )
    assert resp.status_code in (401, 403), resp.text

    print("admin_pii_permission smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
