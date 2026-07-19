"""Smoke for staff.shift_added / shift_edited / shift_deleted wiring
(B2 schedule event-bus cleanup — #18, #19, #20).

Verifies that the three schedule-mutation kinds fire through
``services.notification_routing.record_event`` rather than the legacy
``send_rendered_safely`` direct-send path, and that the payload
snapshots are shaped correctly for the dispatcher's payload-driven
renderer dispatch.

  1. ``staff.shift_added``  — ``staff_schedule.create_entry(publish=True)``
     emits one event for the affected staffer with the shift snapshot
     in payload.
  2. ``staff.shift_added``  — ``staff_schedule.publish_entry`` (per-cell
     publish of a draft) emits one event with the snapshot.
  3. ``staff.shift_edited`` — ``staff_schedule.update_published_entry``
     emits one event with ``old_shift`` + ``new_shift`` in payload; a
     no-op update (same times) emits no event.
  4. ``staff.shift_deleted``— ``staff_schedule.retract_published_entry``
     emits one event with the published-shift snapshot in payload and
     flips ``status`` back to draft (so the row survives as audit).
  5. Dispatcher renders all three end-to-end against the payload (not
     by hydrating the entry — proves that a later mutation/deletion of
     the row wouldn't change the email content).

Seeds users with username prefix ``smoke-shift-events-`` so the
post-suite cleanup sweep in
``scripts/cleanup_admin_smoke_pollution.sql`` can pick up leaks.
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

from sqlalchemy import text as sql_text  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    NotificationJob,
    StaffNotificationEvent,
    StaffScheduleEntry,
    User,
)
from services import (  # noqa: E402
    notification_service,
    staff_schedule,
)

SEED_PREFIX = "smoke-shift-events"
SHOP_TZ = ZoneInfo(os.environ["APP_TIMEZONE"])

_user_ids: list[int] = []


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


class _RejectingSmsTransport:
    def send(self, msg) -> None:  # pragma: no cover
        raise AssertionError("sms transport should not be used")


def _mkuser(*, role: str, label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"{SEED_PREFIX}-{role}-{label}-{suffix}",
            email=f"{SEED_PREFIX}-{role}-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pw-not-real-1234567890"),
            full_name=f"Shift Events Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        _user_ids.append(user.id)
        return user.id
    finally:
        db.close()


def _next_monday(from_date: date) -> date:
    """Pick a Monday at least two weeks out. Deterministic regardless
    of which weekday the smoke runs on."""
    days_until_monday = (1 - from_date.isoweekday()) % 7
    monday = from_date + timedelta(days=days_until_monday + 14)
    assert monday.isoweekday() == 1
    return monday


def _shift_window(base_date: date, hour: int) -> tuple[datetime, datetime]:
    start = datetime.combine(base_date, time(hour, 0), tzinfo=SHOP_TZ)
    end = datetime.combine(base_date, time(hour + 5, 0), tzinfo=SHOP_TZ)
    return start, end


def _events(*, kind: str, subject_id: int) -> list[StaffNotificationEvent]:
    db = SessionLocal()
    try:
        return (
            db.query(StaffNotificationEvent)
            .filter(StaffNotificationEvent.kind == kind)
            .filter(StaffNotificationEvent.subject_id == subject_id)
            .order_by(StaffNotificationEvent.id.asc())
            .all()
        )
    finally:
        db.close()


def _jobs(*, recipient_user_id: int, kind: str) -> list[NotificationJob]:
    db = SessionLocal()
    try:
        return (
            db.query(NotificationJob)
            .filter(NotificationJob.recipient_user_id == recipient_user_id)
            .filter(NotificationJob.kind == kind)
            .order_by(NotificationJob.id.asc())
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    if not _user_ids:
        return
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM notification_jobs "
                "WHERE recipient_user_id = ANY(:ids)"
            ),
            {"ids": _user_ids},
        )
        db.execute(
            sql_text(
                "DELETE FROM staff_notification_events "
                "WHERE actor_user_id = ANY(:ids)"
            ),
            {"ids": _user_ids},
        )
        db.execute(
            sql_text(
                "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:ids)"
            ),
            {"ids": _user_ids},
        )
        db.execute(
            sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
            {"ids": _user_ids},
        )
        db.commit()
    finally:
        db.close()


def main() -> None:
    admin_id = _mkuser(role="admin", label="actor")
    staff_id = _mkuser(role="sales", label="target")

    monday = _next_monday(date.today())

    # ============================================================
    # 1. create_entry(publish=True) fires staff.shift_added
    # ============================================================
    db = SessionLocal()
    try:
        starts, ends = _shift_window(monday, 10)
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=staff_id,
            business_date_=monday,
            starts_at_local=starts,
            ends_at_local=ends,
        )
        db.commit()
        entry1_id = result["id"]
    finally:
        db.close()

    added_events = _events(kind="staff.shift_added", subject_id=entry1_id)
    assert len(added_events) == 0, (
        "draft create must NOT fire shift_added"
    )

    # Re-create with publish=True; pick a different hour so the duplicate
    # check doesn't trip.
    db = SessionLocal()
    try:
        starts, ends = _shift_window(monday, 16)
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=staff_id,
            business_date_=monday,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
        )
        db.commit()
        entry2_id = result["id"]
    finally:
        db.close()

    added_events = _events(kind="staff.shift_added", subject_id=entry2_id)
    assert len(added_events) == 1, added_events
    payload = added_events[0].payload
    assert "shift" in payload, payload
    # Payload datetimes are ISO strings; round-trip via fromisoformat to
    # compare instants regardless of tz representation (Postgres may hand
    # back UTC even though we passed SHOP_TZ in).
    assert datetime.fromisoformat(payload["shift"]["starts_at"]) == starts
    assert datetime.fromisoformat(payload["shift"]["ends_at"]) == ends
    added_jobs = _jobs(
        recipient_user_id=staff_id, kind="staff.shift_added"
    )
    assert len(added_jobs) == 1, added_jobs
    assert added_jobs[0].subject_kind == "shift"
    assert added_jobs[0].subject_id == entry2_id
    print("  ok   create_entry(publish=True) fires shift_added via event bus")

    # ============================================================
    # 2. publish_entry (per-cell publish of a draft) fires shift_added
    # ============================================================
    publish_day = monday + timedelta(days=1)
    db = SessionLocal()
    try:
        starts, ends = _shift_window(publish_day, 9)
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=staff_id,
            business_date_=publish_day,
            starts_at_local=starts,
            ends_at_local=ends,
        )
        db.commit()
        draft_id = result["id"]
    finally:
        db.close()

    db = SessionLocal()
    try:
        staff_schedule.publish_entry(
            db, actor_user_id=admin_id, entry_id=draft_id
        )
        db.commit()
    finally:
        db.close()

    added_events = _events(kind="staff.shift_added", subject_id=draft_id)
    assert len(added_events) == 1, added_events
    print(
        "  ok   publish_entry fires shift_added (per-cell publish path)"
    )

    # ============================================================
    # 3. update_published_entry fires shift_edited; same-times no-op
    #    emits nothing
    # ============================================================
    edit_day = monday + timedelta(days=2)
    db = SessionLocal()
    try:
        starts, ends = _shift_window(edit_day, 10)
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=staff_id,
            business_date_=edit_day,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
        )
        db.commit()
        edit_id = result["id"]
    finally:
        db.close()

    original_starts = starts
    original_ends = ends

    # Real edit: move both endpoints by 30 min.
    db = SessionLocal()
    try:
        new_starts = original_starts + timedelta(minutes=30)
        new_ends = original_ends + timedelta(minutes=30)
        staff_schedule.update_published_entry(
            db,
            actor_user_id=admin_id,
            entry_id=edit_id,
            fields={
                "starts_at_local": new_starts,
                "ends_at_local": new_ends,
            },
        )
        db.commit()
    finally:
        db.close()

    edited_events = _events(kind="staff.shift_edited", subject_id=edit_id)
    assert len(edited_events) == 1, edited_events
    payload = edited_events[0].payload
    assert "old_shift" in payload and "new_shift" in payload, payload
    assert (
        datetime.fromisoformat(payload["old_shift"]["starts_at"])
        == original_starts
    )
    assert (
        datetime.fromisoformat(payload["new_shift"]["starts_at"])
        == new_starts
    )
    assert (
        datetime.fromisoformat(payload["new_shift"]["ends_at"])
        == original_ends + timedelta(minutes=30)
    )
    edited_jobs = _jobs(
        recipient_user_id=staff_id, kind="staff.shift_edited"
    )
    assert any(j.subject_id == edit_id for j in edited_jobs), edited_jobs

    # No-op edit: same times again. Note: update_published_entry rejects
    # an empty fields dict with `nothing_to_update`, so the "no event"
    # case is "we passed fields that didn't actually change the shift
    # dict" — e.g. setting manager_notes to the same value (currently
    # None → None after strip). Test by re-PATCHing the same times.
    db = SessionLocal()
    try:
        staff_schedule.update_published_entry(
            db,
            actor_user_id=admin_id,
            entry_id=edit_id,
            fields={
                "starts_at_local": new_starts,
                "ends_at_local": new_ends,
            },
        )
        db.commit()
    finally:
        db.close()
    edited_events_after = _events(
        kind="staff.shift_edited", subject_id=edit_id
    )
    assert len(edited_events_after) == 1, (
        "no-op update must NOT fire a second event "
        f"(got {len(edited_events_after)})"
    )
    print(
        "  ok   update_published_entry fires shift_edited (and is silent on no-op)"
    )

    # ============================================================
    # 4. retract_published_entry fires shift_deleted; row survives
    #    as draft so audit + intrinsic targeting still resolve.
    # ============================================================
    retract_day = monday + timedelta(days=3)
    db = SessionLocal()
    try:
        starts, ends = _shift_window(retract_day, 11)
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=staff_id,
            business_date_=retract_day,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
        )
        db.commit()
        retract_id = result["id"]
    finally:
        db.close()

    db = SessionLocal()
    try:
        staff_schedule.retract_published_entry(
            db, actor_user_id=admin_id, entry_id=retract_id
        )
        db.commit()
    finally:
        db.close()

    deleted_events = _events(
        kind="staff.shift_deleted", subject_id=retract_id
    )
    assert len(deleted_events) == 1, deleted_events
    payload = deleted_events[0].payload
    assert "shift" in payload, payload
    assert datetime.fromisoformat(payload["shift"]["starts_at"]) == starts
    assert datetime.fromisoformat(payload["shift"]["ends_at"]) == ends

    db = SessionLocal()
    try:
        retracted_row = db.get(StaffScheduleEntry, retract_id)
        assert retracted_row is not None
        assert retracted_row.status == "draft"
        assert retracted_row.published_at is None
        assert retracted_row.published_by_user_id is None
    finally:
        db.close()

    deleted_jobs = _jobs(
        recipient_user_id=staff_id, kind="staff.shift_deleted"
    )
    assert any(j.subject_id == retract_id for j in deleted_jobs), deleted_jobs
    print(
        "  ok   retract_published_entry fires shift_deleted; row survives as draft"
    )

    # ============================================================
    # 5. Dispatcher renders all three end-to-end against payload
    # ============================================================
    for expected_kind, subject_id, expected_phrase in (
        ("staff.shift_added", entry2_id, "new bella"),
        ("staff.shift_edited", edit_id, "updated"),
        ("staff.shift_deleted", retract_id, "removed"),
    ):
        db = SessionLocal()
        try:
            job = (
                db.query(NotificationJob)
                .filter(NotificationJob.kind == expected_kind)
                .filter(NotificationJob.subject_id == subject_id)
                .filter(NotificationJob.recipient_user_id == staff_id)
                .order_by(NotificationJob.id.asc())
                .first()
            )
            assert job is not None, (
                f"expected a queued {expected_kind} job for entry {subject_id}"
            )
            fake_email = _RecordingTransport()
            notification_service.dispatch_job(
                db,
                job,
                email_transport=fake_email,
                sms_transport=_RejectingSmsTransport(),
            )
            db.commit()
            assert len(fake_email.sent) == 1, (
                expected_kind, fake_email.sent
            )
            subj = fake_email.sent[0].subject.lower()
            assert expected_phrase in subj, (expected_kind, subj)
        finally:
            db.close()
    print("  ok   dispatcher renders shift_added/edited/deleted end-to-end")

    print("\nstaff_shift_events_wiring smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
