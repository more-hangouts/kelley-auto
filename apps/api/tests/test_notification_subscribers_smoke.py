"""Smoke: the notification subscriber registry (migration 093) + its
integration as the fourth layer of ``notification_routing.recipients_for``
(Omnichannel Inbox Plan Part 1).

Exercises the Python contracts on the two tables migration 093 created:

  1. An **external** (login-less) subscriber can be created with just a name
     + email, and shows up in ``recipients_for`` for a kind it's subscribed
     to — with ``user_id=None`` (the "email alert, no CRM account" case).
  2. A **linked** subscriber resolves to the user's current email and is
     dropped when the user is deactivated.
  3. Dedup by email: a role-default recipient who is ALSO an explicit
     subscriber appears exactly once.
  4. Deactivating a subscriber stops their delivery without losing toggles.
  5. Non-subscribable kinds are rejected, and a duplicate external email 409s.

Requires migration 093 applied. Seeds under a prefix and cleans every row on
exit so a re-run never sees prior state. No event surfaces touched.
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

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import StaffNotificationEvent, User  # noqa: E402
from modules.core.services import notification_routing  # noqa: E402
from modules.core.services import notification_subscriber_service as svc  # noqa: E402

SEED_PREFIX = "smoke-notif-subscribers"
KIND = "admin.new_booking"  # in ROLE_DEFAULTS['admin'] and SUBSCRIBABLE_KINDS


def _make_user(db, *, role: str, suffix: str) -> User:
    user = User(
        username=f"{SEED_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}",
        email=f"{SEED_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("smoke-pw-not-real-1234567890"),
        full_name=f"Smoke {suffix.title()}",
        is_active=True,
        role=role,
        permissions=[],
    )
    db.add(user)
    db.flush()
    return user


def _cleanup(db, *, user_ids: list[int], subscriber_ids: list[int]) -> None:
    db.rollback()
    if subscriber_ids:
        db.execute(
            sql_text(
                "DELETE FROM notification_subscribers WHERE id = ANY(:ids)"
            ),
            {"ids": subscriber_ids},
        )
    if user_ids:
        # A subscriber linked to a seeded user cascades, but wipe any that
        # were keyed to these users defensively before the users go.
        db.execute(
            sql_text(
                "DELETE FROM notification_subscribers "
                "WHERE user_id = ANY(:ids)"
            ),
            {"ids": user_ids},
        )
        db.execute(
            sql_text(
                "DELETE FROM staff_notification_events "
                "WHERE payload ->> 'tag' = :tag"
            ),
            {"tag": SEED_PREFIX},
        )
        db.execute(
            sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": user_ids},
        )
    db.commit()


def _event(db) -> StaffNotificationEvent:
    evt = StaffNotificationEvent(kind=KIND, payload={"tag": SEED_PREFIX})
    db.add(evt)
    db.flush()
    return evt


def main() -> int:
    db = SessionLocal()
    user_ids: list[int] = []
    subscriber_ids: list[int] = []
    try:
        admin = _make_user(db, role="admin", suffix="admin")
        user_ids = [admin.id]
        db.commit()

        # ===== 1. external subscriber shows up with user_id=None =====
        ext_email = f"{SEED_PREFIX}-frontdesk-{uuid.uuid4().hex[:8]}@ex.com"
        ext = svc.create_subscriber(
            db, display_name="Front Desk", email=ext_email
        )
        subscriber_ids.append(ext["id"])
        assert ext["has_login"] is False and ext["user_id"] is None
        svc.update_subscriptions(db, ext["id"], [(KIND, True)])
        db.commit()

        recips = notification_routing.recipients_for(db, _event(db))
        by_email = {r.email: r for r in recips}
        assert ext_email in by_email, f"external not routed; got {list(by_email)}"
        assert by_email[ext_email].user_id is None, "external must have no user_id"
        assert admin.email in by_email, "role-default admin should be present"
        print("  ok   external subscriber routed with user_id=None")

        # ===== 2. dedup by email: admin as role default AND subscriber =====
        linked = svc.create_subscriber(db, display_name="Admin", user_id=admin.id)
        subscriber_ids.append(linked["id"])
        svc.update_subscriptions(db, linked["id"], [(KIND, True)])
        db.commit()
        recips = notification_routing.recipients_for(db, _event(db))
        admin_hits = [r for r in recips if r.email == admin.email]
        assert len(admin_hits) == 1, f"admin should appear once; got {admin_hits}"
        print("  ok   dedup by email: role default + subscription = one send")

        # ===== 3. deactivate stops delivery =====
        svc.set_active(db, ext["id"], is_active=False)
        db.commit()
        recips = notification_routing.recipients_for(db, _event(db))
        assert ext_email not in {r.email for r in recips}
        print("  ok   deactivated subscriber removed from recipients")

        # ===== 4. linked subscriber drops when user is deactivated =====
        admin.is_active = False
        db.flush()
        recips = notification_routing.recipients_for(db, _event(db))
        assert admin.email not in {r.email for r in recips}, (
            "inactive linked user's email must not resolve"
        )
        admin.is_active = True
        db.flush()
        print("  ok   linked subscriber follows the user's active state")

        # ===== 5. rejects non-subscribable kind + duplicate external email =====
        try:
            svc.update_subscriptions(db, linked["id"], [("staff.shift_edited", True)])
            raise AssertionError("non-subscribable kind should raise")
        except svc.SubscriberError as exc:
            assert exc.code == "kind_not_subscribable"
        db.rollback()

        try:
            svc.create_subscriber(db, display_name="Dup", email=ext_email.upper())
            raise AssertionError("duplicate external email should 409")
        except svc.SubscriberError as exc:
            assert exc.code == "subscriber_already_exists"
        db.rollback()
        print("  ok   rejects non-subscribable kind + duplicate external email")

        db.commit()
        print("\nnotification_subscribers smoke ok")
        return 0
    finally:
        _cleanup(db, user_ids=user_ids, subscriber_ids=subscriber_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
