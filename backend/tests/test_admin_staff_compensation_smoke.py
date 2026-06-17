"""Smoke test for admin Staff Profiles compensation surface.

Covers what the Phase 11 staff-profile UX is paying for:

  1. ORM round-trip on the new `users.hourly_wage` /
     `users.commission_rate` columns, including NULL → value → NULL
     and the CHECK constraints (negative wage, commission > 1).
  2. The admin list / create / PATCH endpoints expose compensation
     fields exactly the way the drawer needs them.
  3. Invalid wage / commission values get the right stable error
     codes back (so the dialog can surface them inline).
  4. PIN endpoints keep working with the new SalesStaffOut shape.
  5. Compensation does NOT leak to sales-scoped endpoints —
     `/api/sales/auth/me`, the staff picker, and the sales-side `me`
     payload are all asserted to omit the columns.
"""

import os
import sys
import uuid
from decimal import Decimal
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
from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


def _make_user(*, role: str, with_pin: str | None = None) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-comp-{suffix}"
        u = User(
            username=username,
            email=f"{role}-comp-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"Comp {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        if with_pin is not None:
            sales_auth.set_pin(db, u, with_pin, force_change=False)
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, username
    finally:
        db.close()


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _cleanup() -> None:
    if not _user_ids:
        return
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": _user_ids},
        )
        db.commit()
    finally:
        db.close()


def main() -> None:
    # ============================================================
    # 1) ORM round-trip + CHECK constraints
    # ============================================================
    print("===== ORM round-trip =====")
    user_id, _ = _make_user(role="sales")

    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        # Default: both columns NULL.
        assert u.hourly_wage is None
        assert u.commission_rate is None

        # Valid values round-trip as Decimal.
        u.hourly_wage = Decimal("18.50")
        u.commission_rate = Decimal("0.0750")
        db.commit()
        db.refresh(u)
        assert u.hourly_wage == Decimal("18.50"), u.hourly_wage
        assert u.commission_rate == Decimal("0.0750"), u.commission_rate

        # Back to NULL.
        u.hourly_wage = None
        u.commission_rate = None
        db.commit()
        db.refresh(u)
        assert u.hourly_wage is None
        assert u.commission_rate is None
    finally:
        db.close()

    # Negative wage rejected at the DB.
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        u.hourly_wage = Decimal("-1.00")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("negative hourly_wage was accepted")
    finally:
        db.close()

    # Commission > 1 rejected at the DB.
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        u.commission_rate = Decimal("1.5000")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("commission_rate > 1 was accepted")
    finally:
        db.close()

    # ============================================================
    # 2) Admin endpoints expose + accept compensation
    # ============================================================
    print("===== admin endpoints expose compensation =====")
    admin_id, _ = _make_user(role="admin")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    # Create a sales user via the admin endpoint with compensation set
    # at create time.
    create_resp = client.post(
        "/api/admin/sales-staff",
        json={
            "username": f"created-{uuid.uuid4().hex[:8]}",
            "email": f"created-{uuid.uuid4().hex[:8]}@example.com",
            "full_name": "Comp Smoke Created",
            "role": "sales",
            "hourly_wage": 22.00,
            "commission_rate": 0.0500,
        },
        headers=admin_hdr,
    )
    assert create_resp.status_code == 201, create_resp.text
    created_body = create_resp.json()
    created_id = created_body["id"]
    _user_ids.append(created_id)
    assert created_body["hourly_wage"] == 22.0, created_body
    assert created_body["commission_rate"] == 0.05, created_body
    assert created_body["role"] == "sales"

    # List should include the new user with compensation populated.
    list_resp = client.get("/api/admin/sales-staff", headers=admin_hdr)
    assert list_resp.status_code == 200, list_resp.text
    by_id = {row["id"]: row for row in list_resp.json()}
    assert created_id in by_id
    row = by_id[created_id]
    assert row["hourly_wage"] == 22.0
    assert row["commission_rate"] == 0.05
    assert row["role"] == "sales"
    assert "last_login" in row

    # PATCH compensation: bump wage and commission.
    patch_resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"hourly_wage": 25.5, "commission_rate": 0.075},
        headers=admin_hdr,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched = patch_resp.json()
    assert patched["hourly_wage"] == 25.5
    assert patched["commission_rate"] == 0.075

    # PATCH name + role: an admin can promote a sales user.
    patch_resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"full_name": "Comp Smoke Promoted", "role": "admin"},
        headers=admin_hdr,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["full_name"] == "Comp Smoke Promoted"
    assert patch_resp.json()["role"] == "admin"

    # PATCH compensation back to NULL.
    patch_resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"hourly_wage": None, "commission_rate": None},
        headers=admin_hdr,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["hourly_wage"] is None
    assert patch_resp.json()["commission_rate"] is None

    # PATCH with empty body → 422 nothing_to_update.
    resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={},
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "nothing_to_update"

    # ============================================================
    # 3) Invalid values rejected with stable codes
    # ============================================================
    print("===== invalid-value coercer codes =====")
    # Negative wage.
    resp = client.post(
        "/api/admin/sales-staff",
        json={
            "username": f"bad-{uuid.uuid4().hex[:8]}",
            "email": f"bad-{uuid.uuid4().hex[:8]}@example.com",
            "hourly_wage": -1,
        },
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_hourly_wage"

    # commission > 1.
    resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"commission_rate": 1.5},
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_commission_rate"

    # commission < 0.
    resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"commission_rate": -0.01},
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_commission_rate"

    # invalid role.
    resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"role": "owner"},
        headers=admin_hdr,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_role"

    # Demote back to sales so PIN endpoints work in step 4.
    patch_resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"role": "sales"},
        headers=admin_hdr,
    )
    assert patch_resp.status_code == 200, patch_resp.text

    # ============================================================
    # 4) PIN endpoints still functional with new shape
    # ============================================================
    print("===== PIN endpoints still functional =====")
    pin_resp = client.post(
        f"/api/admin/sales-staff/{created_id}/pin", headers=admin_hdr
    )
    assert pin_resp.status_code == 200, pin_resp.text
    pin_body = pin_resp.json()
    assert isinstance(pin_body["pin"], str) and pin_body["pin"].isdigit()
    assert pin_body["user"]["has_pin"] is True
    assert pin_body["user"]["force_pin_change"] is True

    clear_resp = client.delete(
        f"/api/admin/sales-staff/{created_id}/pin", headers=admin_hdr
    )
    assert clear_resp.status_code == 204, clear_resp.text

    # ============================================================
    # 5) Sales endpoints do NOT leak compensation
    # ============================================================
    print("===== sales surfaces stay compensation-free =====")
    # Re-mint a PIN so we can log in as the sales user.
    pin_resp = client.post(
        f"/api/admin/sales-staff/{created_id}/pin", headers=admin_hdr
    )
    pin = pin_resp.json()["pin"]
    # Also stamp some compensation on the sales user so we can prove
    # the sales-side serializers are filtering it out.
    client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"hourly_wage": 30.0, "commission_rate": 0.10},
        headers=admin_hdr,
    )
    # Need the username to PIN-login.
    db = SessionLocal()
    try:
        sales_username = db.get(User, created_id).username
    finally:
        db.close()

    login_resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": pin},
    )
    assert login_resp.status_code == 200, login_resp.text
    login_body = login_resp.json()
    assert "hourly_wage" not in login_body["user"], login_body["user"]
    assert "commission_rate" not in login_body["user"], login_body["user"]
    # First-login PIN forces a change; verify that and use the
    # change-pin endpoint to clear the flag so /me works.
    assert login_body["user"]["force_pin_change"] is True
    sales_hdr = {"Authorization": f"Bearer {login_body['access_token']}"}

    me_resp = client.get("/api/sales/auth/me", headers=sales_hdr)
    assert me_resp.status_code == 200, me_resp.text
    me_body = me_resp.json()
    assert "hourly_wage" not in me_body, me_body
    assert "commission_rate" not in me_body, me_body

    # Public staff picker must not include compensation either.
    picker_resp = client.get("/api/sales/auth/staff-picker")
    assert picker_resp.status_code == 200, picker_resp.text
    for row in picker_resp.json():
        assert "hourly_wage" not in row, row
        assert "commission_rate" not in row, row

    # Admin /api/auth/me (the operator's own profile) intentionally
    # does NOT include compensation either — that surface is for who
    # YOU are, not for payroll.
    me_admin = client.get("/api/auth/me", headers=admin_hdr)
    assert me_admin.status_code == 200, me_admin.text
    assert "hourly_wage" not in me_admin.json()
    assert "commission_rate" not in me_admin.json()

    # And sales tokens are still rejected from admin compensation
    # endpoints.
    resp = client.get("/api/admin/sales-staff", headers=sales_hdr)
    assert resp.status_code == 403, resp.text

    resp = client.patch(
        f"/api/admin/sales-staff/{created_id}",
        json={"hourly_wage": 99.99},
        headers=sales_hdr,
    )
    assert resp.status_code == 403, resp.text

    print("admin_staff_compensation smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
