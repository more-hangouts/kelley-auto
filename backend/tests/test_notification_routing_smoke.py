"""B1 smoke: services/notification_routing + the staff_notification_events /
notification_preferences models migration 077 just created.

Schema-only foundation slice: we exercise the Python contracts on top
of the new tables. Specifically:

  1. ``record_event`` writes a ``staff_notification_events`` row with
     the right shape and returns the persisted object.
  2. ``recipients_for`` resolves the recipient set across the three
     layers (intrinsic, role defaults, per-user overrides) in the
     right order — preferences win over role defaults; an explicit
     ``enabled=False`` flips someone off.
  3. ``record_event`` can enqueue a real-time staff job and
     ``dispatch_job`` can render/send it through the staff registry.
  4. The ``uq_one_digest_per_user_per_window`` partial index rejects
     a duplicate digest job for the same (recipient, kind, window)
     but allows a different window on the same kind.

No event surfaces touched. The send path uses an in-memory fake transport.
The test cleans every row it inserts so a re-run never sees prior state.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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

import uuid  # noqa: E402

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    NotificationJob,
    NotificationPreference,
    StaffNotificationEvent,
    User,
)
from services import notification_routing  # noqa: E402
from services import notification_service  # noqa: E402


# All seeded data shares this prefix so cleanup can target it without
# touching production rows.
SEED_PREFIX = "smoke-notif-routing"


class _FakeEmailTransport:
    def __init__(self) -> None:
        self.sent = []

    def send(self, msg) -> None:
        self.sent.append(msg)


class _FakeSmsTransport:
    def send(self, msg) -> None:  # pragma: no cover - staff path never calls it
        raise AssertionError("sms transport should not be used")


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


def _cleanup(db, *, user_ids: list[int]) -> None:
    """Wipe every smoke-seeded row. Ordered by FK dependency."""
    db.rollback()
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


def main() -> int:
    db = SessionLocal()
    user_ids: list[int] = []
    try:
        # ===== seed: an admin + a sales user =====
        admin = _make_user(db, role="admin", suffix="admin")
        sales = _make_user(db, role="sales", suffix="sales")
        user_ids = [admin.id, sales.id]
        db.commit()

        # ===== 1. record_event writes the row =====
        event = notification_routing.record_event(
            db,
            kind="digest.admin_daily",
            subject_kind="digest",
            actor_user_id=admin.id,
            payload={"tag": SEED_PREFIX, "confirmation": "DEMO123"},
        )
        db.commit()
        assert event.id is not None, "event id should be populated after flush"
        reloaded = (
            db.query(StaffNotificationEvent)
            .filter(StaffNotificationEvent.id == event.id)
            .one()
        )
        assert reloaded.kind == "digest.admin_daily"
        assert reloaded.subject_kind == "digest"
        assert reloaded.subject_id is None
        assert reloaded.actor_user_id == admin.id
        assert reloaded.payload["tag"] == SEED_PREFIX
        assert reloaded.daily_digest_consumed_at is None
        assert reloaded.weekly_digest_consumed_at is None
        print("  ok   record_event writes the row")

        # ===== 2. recipients_for: role default subscription =====
        # digest.admin_daily is in ROLE_DEFAULTS['admin'], so the admin
        # user should be in the recipient set; the sales user shouldn't.
        recipients = notification_routing.recipients_for(db, reloaded)
        recipient_ids = {r.user_id for r in recipients}
        assert admin.id in recipient_ids, (
            f"admin should subscribe by role default; got {recipient_ids}"
        )
        assert sales.id not in recipient_ids, (
            f"sales should not see digest.admin_daily; got {recipient_ids}"
        )
        print("  ok   role default subscribes admin to digest.admin_daily")

        # ===== 3. preferences override: admin opts out =====
        db.add(
            NotificationPreference(
                user_id=admin.id,
                event_kind="digest.admin_daily",
                enabled=False,
            )
        )
        db.flush()
        recipients_after_opt_out = notification_routing.recipients_for(
            db, reloaded
        )
        assert admin.id not in {r.user_id for r in recipients_after_opt_out}, (
            "explicit enabled=False should remove admin from recipients"
        )
        print("  ok   preference enabled=False overrides role default")

        # ===== 4. preferences override: sales opts INTO an admin-default kind =====
        db.add(
            NotificationPreference(
                user_id=sales.id,
                event_kind="digest.admin_daily",
                enabled=True,
            )
        )
        db.flush()
        recipients_after_opt_in = notification_routing.recipients_for(
            db, reloaded
        )
        assert sales.id in {r.user_id for r in recipients_after_opt_in}, (
            "explicit enabled=True should add sales as a recipient"
        )
        print("  ok   preference enabled=True overrides absent role default")

        # ===== 5. record_event enqueues + worker dispatches staff jobs =====
        db.add(
            NotificationPreference(
                user_id=sales.id,
                event_kind="staff.pin_reset",
                enabled=True,
            )
        )
        db.flush()
        staff_event = notification_routing.record_event(
            db,
            kind="staff.pin_reset",
            subject_kind="user",
            subject_id=sales.id,
            actor_user_id=admin.id,
            payload={
                "set_pin_url": "https://sales.example.test/set-pin",
                "ttl_minutes": 12,
            },
        )
        db.flush()
        staff_job = (
            db.query(NotificationJob)
            .filter(
                NotificationJob.kind == "staff.pin_reset",
                NotificationJob.recipient_user_id == sales.id,
                NotificationJob.subject_kind == "user",
                NotificationJob.subject_id == sales.id,
            )
            .one()
        )
        assert staff_job.payload["set_pin_url"].endswith("/set-pin")
        assert staff_job.status == "pending"

        fake_email = _FakeEmailTransport()
        notification_service.dispatch_job(
            db,
            staff_job,
            email_transport=fake_email,
            sms_transport=_FakeSmsTransport(),
        )
        assert staff_job.status == "sent"
        assert len(fake_email.sent) == 1
        assert fake_email.sent[0].to == sales.email
        assert "PIN was reset" in fake_email.sent[0].subject
        print("  ok   record_event enqueues and dispatches staff email jobs")

        db.commit()

        # ===== 6. intrinsic targeting resolves the subject's user =====
        # staff.role_changed has no role default in any bundle and no
        # preference seeded for it, so the only way recipients_for adds
        # anyone is via _the_user_themselves. Build an event for admin
        # and confirm admin (and only admin) is the recipient.
        role_change_event = StaffNotificationEvent(
            kind="staff.role_changed",
            subject_kind="user",
            subject_id=admin.id,
            payload={
                "tag": SEED_PREFIX,
                "old_role": "sales",
                "new_role": "admin",
            },
        )
        db.add(role_change_event)
        db.flush()
        intrinsic_recipients = notification_routing.recipients_for(
            db, role_change_event
        )
        intrinsic_ids = {r.user_id for r in intrinsic_recipients}
        assert admin.id in intrinsic_ids, (
            f"_the_user_themselves should add the subject user; got {intrinsic_ids}"
        )
        assert sales.id not in intrinsic_ids, (
            f"sales is not the subject and has no preference for "
            f"staff.role_changed; should be absent. got {intrinsic_ids}"
        )
        print("  ok   intrinsic _the_user_themselves resolves the subject user")

        # ===== 7. digest dedup index =====
        digest_window = "2099-01-01"  # far future to never collide with real
        first_job = NotificationJob(
            kind="digest.staff_daily",
            channel="email",
            recipient=sales.email,
            recipient_user_id=sales.id,
            payload={"digest_window": digest_window, "tag": SEED_PREFIX},
            subject_kind="digest",
            status="pending",
        )
        db.add(first_job)
        db.flush()

        duplicate = NotificationJob(
            kind="digest.staff_daily",
            channel="email",
            recipient=sales.email,
            recipient_user_id=sales.id,
            payload={"digest_window": digest_window, "tag": SEED_PREFIX},
            subject_kind="digest",
            status="pending",
        )
        db.add(duplicate)
        try:
            db.flush()
        except Exception:
            db.rollback()
            print(
                "  ok   uq_one_digest_per_user_per_window rejects duplicate"
            )
        else:
            raise AssertionError(
                "duplicate (recipient, kind, window) digest job should have "
                "been rejected by uq_one_digest_per_user_per_window"
            )

        # ===== 7. different window on same kind is allowed =====
        different_window = NotificationJob(
            kind="digest.staff_daily",
            channel="email",
            recipient=sales.email,
            recipient_user_id=sales.id,
            payload={"digest_window": "2099-01-02", "tag": SEED_PREFIX},
            subject_kind="digest",
            status="pending",
        )
        db.add(different_window)
        db.flush()
        print("  ok   different digest_window on same kind is allowed")

        db.commit()
        print("\nnotification_routing smoke ok")
        return 0
    finally:
        _cleanup(db, user_ids=user_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
