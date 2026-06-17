"""Smoke test for admin-side password management from Staff Profiles."""

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
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
    verify_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import PasswordResetToken, User  # noqa: E402
from services import password_reset  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_captured_emails = []


class _CapturingTransport:
    def send(self, msg):
        _captured_emails.append(msg)


def _make_user(*, role: str, password: str = "Smoke-Pass-12345!") -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"admin-pw-{role}-{suffix}",
            email=f"admin-pw-{role}-{suffix}@example.com",
            hashed_password=hash_password(password),
            full_name=f"Admin Password {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        _user_ids.append(user.id)
        return user.id, password
    finally:
        db.close()


def _admin_headers(user_id: int) -> dict[str, str]:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        return {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        db.close()


def _sales_headers(user_id: int) -> dict[str, str]:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        return {"Authorization": f"Bearer {create_sales_token(user)}"}
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


_original_get_transport = password_reset.get_email_transport
password_reset.get_email_transport = lambda: _CapturingTransport()


def main() -> None:
    print("===== self-service password change rejects wrong current password =====")
    admin_id, old_password = _make_user(role="admin")
    hdr = _admin_headers(admin_id)

    db = SessionLocal()
    try:
        before_hash = db.get(User, admin_id).hashed_password
    finally:
        db.close()

    bad_resp = client.post(
        "/api/admin/me/change-password",
        json={
            "current_password": "not-the-current-password",
            "new_password": "New-Smoke-Pass-12345!",
        },
        headers=hdr,
    )
    assert bad_resp.status_code == 400, bad_resp.text
    assert bad_resp.json()["detail"] == "current_password_incorrect"

    db = SessionLocal()
    try:
        user = db.get(User, admin_id)
        assert user.hashed_password == before_hash
        assert verify_password(old_password, user.hashed_password)
    finally:
        db.close()

    # Phase 12.1: capture mail count BEFORE the successful change so the
    # tripwire-email assertion below isolates the delta from any other
    # captures in this smoke run.
    email_count_before = len(_captured_emails)

    good_resp = client.post(
        "/api/admin/me/change-password",
        json={
            "current_password": old_password,
            "new_password": "New-Smoke-Pass-12345!",
        },
        headers=hdr,
    )
    assert good_resp.status_code == 204, good_resp.text

    db = SessionLocal()
    try:
        user = db.get(User, admin_id)
        assert verify_password("New-Smoke-Pass-12345!", user.hashed_password)
    finally:
        db.close()

    # Phase 12.1 security tripwire: the self-service change-password
    # path mirrors the reset-confirm path and dispatches a
    # "your password was changed" email so the account holder has an
    # out-of-band signal if the change was not theirs.
    assert len(_captured_emails) == email_count_before + 1, (
        "tripwire email was not dispatched after self-service change",
        len(_captured_emails),
    )
    tripwire = _captured_emails[-1]
    db = SessionLocal()
    try:
        admin_email = db.get(User, admin_id).email
    finally:
        db.close()
    assert tripwire.to == admin_email, tripwire.to
    assert tripwire.subject == "Your Bella's XV password was changed", (
        tripwire.subject
    )
    assert "was just changed" in tripwire.text, tripwire.text

    print("===== authenticated admin reset trigger dispatches reset email =====")
    owner_id, _ = _make_user(role="admin")
    target_id, _ = _make_user(role="admin")
    owner_hdr = _admin_headers(owner_id)

    reset_resp = client.post(
        f"/api/admin/staff/{target_id}/send-password-reset",
        headers=owner_hdr,
    )
    assert reset_resp.status_code == 204, reset_resp.text
    assert _captured_emails, "reset email was not dispatched"

    db = SessionLocal()
    try:
        target = db.get(User, target_id)
        row = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == target_id)
            .order_by(PasswordResetToken.id.desc())
            .first()
        )
        assert row is not None
        assert row.used_at is None
        assert _captured_emails[-1].to == target.email
        assert "To set a new password" in _captured_emails[-1].text
        assert "?token=" in _captured_emails[-1].text
    finally:
        db.close()

    sales_id, _ = _make_user(role="sales")
    forbidden_resp = client.post(
        f"/api/admin/staff/{target_id}/send-password-reset",
        headers=_sales_headers(sales_id),
    )
    assert forbidden_resp.status_code == 403, forbidden_resp.text

    non_admin_resp = client.post(
        f"/api/admin/staff/{sales_id}/send-password-reset",
        headers=owner_hdr,
    )
    assert non_admin_resp.status_code == 422, non_admin_resp.text
    assert non_admin_resp.json()["detail"] == "target_not_admin"

    print("admin_password_management smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        password_reset.get_email_transport = _original_get_transport
        _cleanup()
