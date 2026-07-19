"""B2.5 smoke: sales-portal notification preferences API.

Covers the round-trip:

  1. GET /api/sales/me/notifications/preferences returns the role-default
     bundle for a sales user (digest.staff_daily + digest.staff_weekly),
     each with source='role_default' and enabled=True.
  2. PUT flips one off, response carries the new effective state.
  3. GET reflects the override (enabled=False, source='override').
  4. ``recipients_for`` honors the override end-to-end — a follow-up
     ``record_event`` for that kind no longer reaches this user.
  5. PUT rejects a kind that isn't in the user's configurable bundle
     (intrinsic-only kinds + customer-facing kinds) with 422.

Cleans every seeded row in the finally block.
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
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    NotificationPreference,
    StaffNotificationEvent,
    User,
)
from services import notification_routing  # noqa: E402


SEED_PREFIX = "smoke-notif-prefs"
client = TestClient(app)


def _make_sales_user(db) -> User:
    user = User(
        username=f"{SEED_PREFIX}-{uuid.uuid4().hex[:8]}",
        email=f"{SEED_PREFIX}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("smoke-pw-not-real-1234567890"),
        full_name="Smoke Sales",
        is_active=True,
        role="sales",
        permissions=[],
    )
    db.add(user)
    db.flush()
    return user


def _cleanup(user_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM notification_preferences "
                    "WHERE user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE actor_user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.execute(
            sql_text(
                "DELETE FROM staff_notification_events "
                "WHERE payload ->> 'tag' = :tag"
            ),
            {"tag": SEED_PREFIX},
        )
        db.commit()
    finally:
        db.close()


def main() -> int:
    db = SessionLocal()
    user_ids: list[int] = []
    try:
        user = _make_sales_user(db)
        user_ids = [user.id]
        db.commit()
        token = create_sales_token(user)
        headers = {"Authorization": f"Bearer {token}"}

        # ===== 1. GET returns the role-default bundle =====
        resp = client.get(
            "/api/sales/me/notifications/preferences", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        kinds = {p["event_kind"]: p for p in body["preferences"]}
        assert "digest.staff_daily" in kinds, kinds
        assert "digest.staff_weekly" in kinds, kinds
        assert kinds["digest.staff_daily"]["source"] == "role_default"
        assert kinds["digest.staff_daily"]["enabled"] is True
        assert kinds["digest.staff_weekly"]["enabled"] is True
        # Categories + labels come through so the UI can render without
        # a second lookup.
        assert kinds["digest.staff_daily"]["category"] == "Digests"
        assert "Daily" in kinds["digest.staff_daily"]["label"]
        print("  ok   GET returns sales role-default bundle")

        # ===== 2. PUT flips digest.staff_daily off =====
        resp = client.put(
            "/api/sales/me/notifications/preferences",
            headers=headers,
            json={"updates": [{"event_kind": "digest.staff_daily", "enabled": False}]},
        )
        assert resp.status_code == 200, resp.text
        body_after = resp.json()
        after_kinds = {p["event_kind"]: p for p in body_after["preferences"]}
        assert after_kinds["digest.staff_daily"]["enabled"] is False
        assert after_kinds["digest.staff_daily"]["source"] == "override"
        # The other kind is untouched.
        assert after_kinds["digest.staff_weekly"]["enabled"] is True
        assert after_kinds["digest.staff_weekly"]["source"] == "role_default"
        print("  ok   PUT flips digest.staff_daily off, returns new state")

        # ===== 3. GET reflects the override =====
        resp = client.get(
            "/api/sales/me/notifications/preferences", headers=headers
        )
        assert resp.status_code == 200
        reread = {p["event_kind"]: p for p in resp.json()["preferences"]}
        assert reread["digest.staff_daily"]["enabled"] is False
        assert reread["digest.staff_daily"]["source"] == "override"
        print("  ok   GET reflects the persisted override")

        # ===== 4. recipients_for honors the override =====
        # The override is for digest.staff_daily, which sales users
        # subscribe to by default. After enabled=False, this user
        # should drop out of recipients_for results.
        #
        # Construct the event in memory without adding to the session —
        # recipients_for only reads .kind / .subject_*, and leaving an
        # uncommitted insert open here would block the next API request's
        # connection pool slot in step 5.
        event = StaffNotificationEvent(
            kind="digest.staff_daily",
            subject_kind="digest",
            actor_user_id=user.id,
            payload={"tag": SEED_PREFIX, "digest_window": "2099-05-18"},
        )
        recipients = notification_routing.recipients_for(db, event)
        recipient_ids = {r.user_id for r in recipients}
        assert user.id not in recipient_ids, (
            f"user opted out of digest.staff_daily; should not be a recipient. "
            f"got {recipient_ids}"
        )
        print("  ok   recipients_for honors the persisted override")

        # ===== 5. PUT rejects a kind outside the user's role bundle =====
        # admin.new_booking is in KIND_DESCRIPTORS but only in the admin
        # role's default bundle. For a sales user, the API rejects it
        # rather than silently letting them subscribe to admin alerts.
        resp = client.put(
            "/api/sales/me/notifications/preferences",
            headers=headers,
            json={"updates": [{"event_kind": "admin.new_booking", "enabled": True}]},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "kind_not_configurable", detail
        print("  ok   PUT rejects out-of-role kind with 422 kind_not_configurable")

        # ===== 6. PUT rejects an unknown / out-of-catalog kind =====
        resp = client.put(
            "/api/sales/me/notifications/preferences",
            headers=headers,
            json={"updates": [{"event_kind": "totally.fictional", "enabled": True}]},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "kind_not_in_catalog", detail
        print("  ok   PUT rejects unknown kind with 422 kind_not_in_catalog")

        db.commit()
        print("\nnotification_preferences smoke ok")
        return 0
    finally:
        _cleanup(user_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
