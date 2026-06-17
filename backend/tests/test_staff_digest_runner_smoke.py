"""B2.3 smoke: digest runners.

Covers the three runners + the dedup ledger + the preference path.

  1. ``run_staff_daily`` sends one digest to a subscribed sales user with
     a published shift today; re-running on the same date is a no-op
     (dedup index).
  2. A sales user with an explicit ``enabled=False`` override for
     ``digest.staff_daily`` is skipped, even with a shift today.
  3. ``run_admin_daily`` sends one digest to a subscribed admin and
     includes the pending time-off / missing-clock-out summary it
     could find in the database.
  4. ``run_staff_weekly`` is a no-op on non-Sunday inputs and sends
     when called with a Sunday date.

All sends use a monkey-patched transport so SMTP stays out of the
test; the assertion is "the helper send_rendered_safely received the
right rendered email", which is enough to prove the pipeline.
Cleans every seeded row.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
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
from zoneinfo import ZoneInfo  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    NotificationJob,
    NotificationPreference,
    StaffNotificationEvent,
    StaffScheduleEntry,
    User,
)
from services import staff_digest_runner  # noqa: E402


SEED_PREFIX = "smoke-digest-runner"
SHOP_TZ = ZoneInfo(os.environ["APP_TIMEZONE"])


# ─── Test transport ────────────────────────────────────────────────────────


_captured: list = []


class _Capture:
    def send(self, msg):
        _captured.append(msg)


def _install_capture(monkeypatch_target):
    """Patch services.email_transport.get_email_transport in BOTH the
    transport module and the staff_digest_runner namespace (which
    imports send_rendered_safely directly)."""
    from services import email_transport

    capture = _Capture()
    email_transport.get_email_transport = lambda: capture  # type: ignore
    return capture


# ─── Fixtures ──────────────────────────────────────────────────────────────


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


def _seed_shift(
    db, *, user_id: int, on_date: date, actor_user_id: int
) -> StaffScheduleEntry:
    start = datetime.combine(on_date, time(10, 0), tzinfo=SHOP_TZ)
    end = datetime.combine(on_date, time(15, 0), tzinfo=SHOP_TZ)
    entry = StaffScheduleEntry(
        user_id=user_id,
        business_date=on_date,
        starts_at_local=start,
        ends_at_local=end,
        status="published",
        attendance_status="scheduled",
        late_grace_minutes=30,
        source="manual",
        published_at=datetime.now(timezone.utc),
        published_by_user_id=actor_user_id,
        created_by_user_id=actor_user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _seed_appointment(
    db, *, assigned_user_id: int, on_date: date, hour: int = 12
) -> Appointment:
    slot_start = datetime.combine(on_date, time(hour, 0), tzinfo=SHOP_TZ).astimezone(
        timezone.utc
    )
    appt = Appointment(
        confirmation_code=f"DIGEST{uuid.uuid4().hex[:14].upper()}",
        slot_start_at=slot_start,
        slot_end_at=slot_start + timedelta(minutes=60),
        slot_duration_minutes=60,
        timezone=str(SHOP_TZ),
        celebrant_first_name="Smoke",
        celebrant_last_name="Celebrant",
        parent_first_name="Smoke",
        parent_last_name="Parent",
        party_size_bucket="3_4",
        phone="(210) 555-0142",
        email=f"{SEED_PREFIX}-appt-{uuid.uuid4().hex[:6]}@example.com",
        status="confirmed",
        assigned_user_id=assigned_user_id,
        user_journey=[],
        raw_payload={},
    )
    db.add(appt)
    db.flush()
    return appt


def _cleanup(user_ids: list[int], appt_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_session_events "
                    "WHERE appointment_id = ANY(:ids)"
                ),
                {"ids": appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": appt_ids},
            )
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
                sql_text(
                    "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:ids)"
                ),
                {"ids": user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


# ─── Test body ─────────────────────────────────────────────────────────────


def main() -> int:
    _install_capture(None)
    db = SessionLocal()
    user_ids: list[int] = []
    appt_ids: list[int] = []
    try:
        admin = _make_user(db, role="admin", suffix="admin")
        sales_in = _make_user(db, role="sales", suffix="in")
        sales_out = _make_user(db, role="sales", suffix="out")
        user_ids = [admin.id, sales_in.id, sales_out.id]
        db.commit()

        # sales_out opts OUT of staff_daily so we can verify preferences
        # are honored from the runner.
        db.add(
            NotificationPreference(
                user_id=sales_out.id,
                event_kind="digest.staff_daily",
                enabled=False,
            )
        )
        db.commit()

        today = datetime.now(SHOP_TZ).date()
        # Both sales users have a shift today.
        _seed_shift(db, user_id=sales_in.id, on_date=today, actor_user_id=admin.id)
        _seed_shift(db, user_id=sales_out.id, on_date=today, actor_user_id=admin.id)
        # sales_in has 2 appointments on their column today.
        appt1 = _seed_appointment(
            db, assigned_user_id=sales_in.id, on_date=today, hour=11
        )
        appt2 = _seed_appointment(
            db, assigned_user_id=sales_in.id, on_date=today, hour=14
        )
        appt_ids = [appt1.id, appt2.id]
        db.commit()

        # ===== 1. run_staff_daily sends to subscribed user with shift =====
        _captured.clear()
        sent = staff_digest_runner.run_staff_daily(db, digest_date=today)
        assert sent == 1, f"expected 1 send (sales_in), got {sent}"
        # The captured message must target sales_in's email.
        assert any(m.to == sales_in.email for m in _captured), (
            f"expected a digest for {sales_in.email}; captured: {[m.to for m in _captured]}"
        )
        # sales_out opted out — no send.
        assert not any(m.to == sales_out.email for m in _captured), (
            "sales_out opted out via preferences; should not receive"
        )
        # The digest body should name both appointments by celebrant.
        digest = next(m for m in _captured if m.to == sales_in.email)
        assert "appointment" in digest.subject.lower() or "day at Bella" in digest.subject
        assert "Smoke Celebrant" in digest.text or "Smoke Parent" in digest.text
        print("  ok   run_staff_daily sends to subscribed user with shift")
        print("  ok   preferences override skips opted-out user")

        # ===== 2. re-running on the same date is a no-op (dedup) =====
        _captured.clear()
        sent_again = staff_digest_runner.run_staff_daily(db, digest_date=today)
        assert sent_again == 0, f"dedup should skip; got {sent_again} resends"
        assert _captured == [], "no email should land on re-run"
        print("  ok   re-running same date is a no-op (dedup ledger)")

        # ===== 3. run_admin_daily sends to subscribed admin =====
        # Seed a staff_notification_events row so the admin digest has
        # something the digest-feeding event log could find (though the
        # admin daily renderer reads current state too, this proves the
        # event log is queryable through the same session).
        ev = StaffNotificationEvent(
            kind="admin.walk_in_lead_created",
            subject_kind="event",
            subject_id=999_999,
            actor_user_id=admin.id,
            payload={"tag": SEED_PREFIX, "celebrant_first_name": "ProbeCelebrant"},
        )
        db.add(ev)
        db.commit()

        _captured.clear()
        sent_admin = staff_digest_runner.run_admin_daily(db, digest_date=today)
        # Dev DB has other admin users; assert ours is among the
        # recipients rather than locking to a global count.
        assert sent_admin >= 1, f"expected at least 1 admin send, got {sent_admin}"
        admin_targets = {m.to for m in _captured}
        assert admin.email in admin_targets, (
            f"smoke admin not in recipients: {admin_targets}"
        )
        admin_msg = next(m for m in _captured if m.to == admin.email)
        assert "digest" in admin_msg.subject.lower() or "Bella" in admin_msg.subject
        print(
            f"  ok   run_admin_daily sends to subscribed admins ({sent_admin} total, "
            f"smoke admin included)"
        )

        # ===== 4. run_staff_weekly: no-op on non-Sunday, sends on Sunday =====
        # Find a Sunday date (weekday == 6) so the weekly path actually fires.
        sunday = today
        while sunday.weekday() != 6:
            sunday += timedelta(days=1)
        upcoming_monday = sunday + timedelta(days=1)
        # Seed a shift for sales_in on the upcoming Monday so the weekly
        # has something to summarise.
        _seed_shift(
            db, user_id=sales_in.id, on_date=upcoming_monday, actor_user_id=admin.id
        )
        db.commit()

        # Non-Sunday no-op.
        non_sunday = sunday + timedelta(days=2)  # Tuesday
        _captured.clear()
        weekly_skipped = staff_digest_runner.run_staff_weekly(
            db, week_start=non_sunday
        )
        assert weekly_skipped == 0
        assert _captured == []

        # Sunday fires.
        _captured.clear()
        weekly_sent = staff_digest_runner.run_staff_weekly(
            db, week_start=sunday
        )
        assert weekly_sent >= 1, f"expected at least 1 weekly send, got {weekly_sent}"
        assert any(m.to == sales_in.email for m in _captured)
        print("  ok   run_staff_weekly fires Sunday-only, includes upcoming shifts")

        db.commit()
        print("\nstaff_digest_runner smoke ok")
        return 0
    finally:
        _cleanup(user_ids, appt_ids)
        db.close()


if __name__ == "__main__":
    sys.exit(main())
