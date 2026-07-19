"""Smoke tests for Phase 7 Slice 2B-3 (cron family).

Covers four crons + the cron health surface:

  1. Selfie retention: deletes files older than the configured
     `selfie_retention_days`, preserves punch metadata, NULLs the
     storage key, writes an audit row, and is idempotent on a second
     run.
  2. Auto-close (the user's headline ask): two ticks against the
     same open punch must produce identical state. The smoke runs
     the tick twice in a row and asserts no duplicate `out` row, no
     duplicate audit row, and `hours_confirmation_status` doesn't
     double-stamp.
  3. Pre-close reminder: fires once for an open session inside the
     30-minute lead window, the marker row prevents a second send,
     and is skipped when the cutoff is too far in the future.
  4. Cron health: `GET /api/admin/cron-health` returns one entry per
     known cron name with `last_finished_at` populated after a tick,
     `is_stale=false` immediately after, and `is_stale=true` when
     `last_finished_at` is older than the staleness window.

All four ticks bypass the daily-worker scheduler and call `*.tick(db)`
directly so the smoke runs in seconds.
"""

import io
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    AttendancePreCloseReminder,
    BusinessProfile,
    CronRunState,
    StaffLocation,
    StaffPunch,
    StaffPunchAuditEvent,
    User,
)
from services import (  # noqa: E402
    attendance_close,
    attendance_pre_close,
    clock_selfie,
    clock_selfie_retention,
    cron_state,
    document_storage,
)

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []
_punch_ids: list[int] = []
_selfie_keys: list[str] = []
_pre_close_marker_ids: list[int] = []


PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000
PROBE_RADIUS_M = 100


def _make_user(*, role: str = "sales", email_suffix: str | None = None) -> int:
    db = SessionLocal()
    try:
        suffix = email_suffix or uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-cron-{suffix}",
            email=f"{role}-cron-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"Cron {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _seed_location(*, default_close: time | None = None) -> int:
    db = SessionLocal()
    try:
        loc = StaffLocation(
            name="Cron Probe",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=PROBE_RADIUS_M,
            default_auto_session_close_time=default_close,
            active=True,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _seed_punch(
    *,
    user_id: int,
    direction: str,
    punched_at: datetime,
    location_id: int | None = None,
    selfie_storage_key: str | None = None,
    status: str = "unscheduled",
) -> int:
    db = SessionLocal()
    try:
        p = StaffPunch(
            user_id=user_id,
            direction=direction,
            punched_at=punched_at.astimezone(timezone.utc),
            status=status,
            location_id=location_id,
            client_latitude=PROBE_LAT,
            client_longitude=PROBE_LNG,
            distance_to_location_m=10.0,
            selfie_storage_key=selfie_storage_key,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        _punch_ids.append(p.id)
        return p.id
    finally:
        db.close()


def _write_real_selfie(user_id: int, punch_id: int) -> str:
    """Drop a small WebP under the configured storage root for the
    retention happy-path. Returns the storage key for the punch row."""
    img = Image.new("RGB", (200, 200), color=(80, 200, 120))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    buf.seek(0)
    key = f"clockin/{user_id}/{punch_id}.webp"
    document_storage.put_object(key, buf)
    _selfie_keys.append(key)
    return key


def _capture_business_profile() -> dict:
    db = SessionLocal()
    try:
        row = db.query(BusinessProfile).first()
        if row is None:
            raise AssertionError(
                "test prerequisite: business_profile row must exist; run "
                "the business profile smoke first."
            )
        return {
            "id": row.id,
            "selfie_retention_days": row.selfie_retention_days,
            "selfie_policy": row.selfie_policy,
            "attendance_gate_enabled": row.attendance_gate_enabled,
        }
    finally:
        db.close()


def _set_retention_days(days: int | None) -> None:
    db = SessionLocal()
    try:
        row = db.query(BusinessProfile).first()
        row.selfie_retention_days = days
        db.commit()
    finally:
        db.close()


def _restore_business_profile(snapshot: dict) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, snapshot["id"])
        if row is None:
            return
        row.selfie_retention_days = snapshot["selfie_retention_days"]
        row.selfie_policy = snapshot["selfie_policy"]
        row.attendance_gate_enabled = snapshot["attendance_gate_enabled"]
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for key in list(_selfie_keys):
            try:
                document_storage.delete_object(key)
            except Exception:
                pass
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM attendance_pre_close_reminders "
                    "WHERE punch_id IN ("
                    "SELECT id FROM staff_punches WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE actor_user_id = ANY(:uids) "
                    "   OR punch_id IN ("
                    "SELECT id FROM staff_punches WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_punches WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        # Cron run state rows are global. Don't delete them — other
        # smokes may rely on the state surface being populated. We
        # also can't easily snapshot them without competing with the
        # daily worker.
        db.commit()
    finally:
        db.close()


def main() -> None:
    profile_snapshot = _capture_business_profile()

    tz = ZoneInfo(APP_TIMEZONE)

    # ============================================================
    # 1. SELFIE RETENTION
    # ============================================================
    print("===== retention =====")
    _set_retention_days(30)

    user_id = _make_user(role="sales", email_suffix="ret")
    location_id = _seed_location()

    # Old punch with a real selfie file → should be deleted.
    old_at = datetime.now(timezone.utc) - timedelta(days=60)
    old_id = _seed_punch(
        user_id=user_id, direction="in", punched_at=old_at,
        location_id=location_id,
    )
    old_key = _write_real_selfie(user_id, old_id)

    db = SessionLocal()
    try:
        old_punch = db.get(StaffPunch, old_id)
        old_punch.selfie_storage_key = old_key
        db.commit()
    finally:
        db.close()

    # Recent punch with a real selfie file → should NOT be deleted.
    recent_at = datetime.now(timezone.utc) - timedelta(days=2)
    recent_id = _seed_punch(
        user_id=user_id, direction="out", punched_at=recent_at,
        location_id=location_id,
    )
    recent_key = _write_real_selfie(user_id, recent_id)
    db = SessionLocal()
    try:
        rp = db.get(StaffPunch, recent_id)
        rp.selfie_storage_key = recent_key
        db.commit()
    finally:
        db.close()

    # Run the retention pass.
    db = SessionLocal()
    try:
        result = clock_selfie_retention.run_retention_pass(db)
        db.commit()
    finally:
        db.close()

    assert result.scanned == 1, result
    assert result.deleted_files == 1, result
    assert result.cleared_keys == 1, result
    assert not document_storage.object_exists(old_key)
    assert document_storage.object_exists(recent_key)

    # The punch row stayed; only the storage key was nulled.
    db = SessionLocal()
    try:
        old_punch = db.get(StaffPunch, old_id)
        assert old_punch is not None
        assert old_punch.selfie_storage_key is None
        # Audit row recorded the deletion.
        evs = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.punch_id == old_id
                )
            )
            .scalars()
            .all()
        )
        assert any(
            e.action == "selfie.retention_deleted"
            and e.actor_kind == "system"
            for e in evs
        ), [e.action for e in evs]
    finally:
        db.close()

    # Idempotent: a second run finds no candidates because the key is
    # already NULLed.
    db = SessionLocal()
    try:
        result2 = clock_selfie_retention.run_retention_pass(db)
        db.commit()
    finally:
        db.close()
    assert result2.scanned == 0, result2
    assert result2.cleared_keys == 0, result2

    # NULL retention = forever: even an ancient punch is not deleted.
    _set_retention_days(None)
    ancient_at = datetime.now(timezone.utc) - timedelta(days=900)
    ancient_id = _seed_punch(
        user_id=user_id, direction="in", punched_at=ancient_at,
        location_id=location_id,
    )
    ancient_key = _write_real_selfie(user_id, ancient_id)
    db = SessionLocal()
    try:
        ap = db.get(StaffPunch, ancient_id)
        ap.selfie_storage_key = ancient_key
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        result3 = clock_selfie_retention.run_retention_pass(db)
        db.commit()
    finally:
        db.close()
    assert result3.scanned == 0, result3
    assert document_storage.object_exists(ancient_key)

    # Reset retention so later sections can rely on it.
    _set_retention_days(365)

    # ============================================================
    # 2. AUTO-CLOSE — IDEMPOTENCY
    # ============================================================
    print("===== auto-close =====")

    # Fresh user + location with a known auto-close cutoff at 22:00
    # local. Open a session at 11:00 local "yesterday" so the cutoff
    # is solidly past `now`. We pin `now` to today 09:00 local via
    # `now_override` so the past_date branch fires deterministically
    # regardless of what wall time the smoke runs at — without the
    # override, a smoke run after 11:00 today crosses the 24h
    # MAX_SESSION_HOURS line and `max_time_reached` fires first.
    auto_user = _make_user(role="sales", email_suffix="auto")
    auto_loc = _seed_location(default_close=time(22, 0))

    today_local_date = datetime.now(tz).date()
    yesterday_local_date = today_local_date - timedelta(days=1)
    in_local = datetime.combine(yesterday_local_date, time(11, 0), tzinfo=tz)
    fake_now_today_morning = datetime.combine(
        today_local_date, time(9, 0), tzinfo=tz
    ).astimezone(timezone.utc)
    auto_in_id = _seed_punch(
        user_id=auto_user,
        direction="in",
        punched_at=in_local,
        location_id=auto_loc,
        status="recorded",
    )

    # First tick: closes the session.
    db = SessionLocal()
    try:
        r1 = attendance_close.run_auto_close_pass(
            db, now_override=fake_now_today_morning
        )
        db.commit()
    finally:
        db.close()
    # `run_auto_close_pass` walks every user, so residue open sessions
    # from earlier partial runs inflate both counters. The per-user
    # query below is the actual idempotency proof; here we just assert
    # that at least our seeded session was scanned and closed.
    assert r1.scanned_open_sessions >= 1, r1
    assert r1.closed >= 1, r1

    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(StaffPunch).where(StaffPunch.user_id == auto_user)
            )
            .scalars()
            .all()
        )
        # Must be exactly two punches now: the original in + one auto out.
        assert len(rows) == 2, [r.direction for r in rows]
        outs = [r for r in rows if r.direction == "out"]
        assert len(outs) == 1
        out_punch = outs[0]
        assert out_punch.auto_closed is True
        assert out_punch.auto_close_reason == "past_date"
        assert out_punch.hours_confirmation_status == "needs_review"
        # Original in stamped needs_review too.
        ins = [r for r in rows if r.direction == "in"]
        assert ins[0].hours_confirmation_status == "needs_review"

        first_audit_count = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.action == "punch.auto_closed"
                )
            )
            .scalars()
            .all()
        )
        first_audit_for_user = [
            e for e in first_audit_count if e.punch_id == out_punch.id
        ]
        assert len(first_audit_for_user) == 1, first_audit_for_user
    finally:
        db.close()

    # Second tick: must be a no-op. THIS IS THE USER'S HEADLINE ASK.
    db = SessionLocal()
    try:
        r2 = attendance_close.run_auto_close_pass(
            db, now_override=fake_now_today_morning
        )
        db.commit()
    finally:
        db.close()
    # The user is now `out`, so the candidate scan returns zero open
    # sessions. The cron correctly does NOT close them again.
    assert r2.closed == 0, r2

    # Re-pull the rows: still exactly 2 punches, still exactly 1
    # auto-close audit row. No duplicates.
    db = SessionLocal()
    try:
        rows_after_second = (
            db.execute(
                select(StaffPunch).where(StaffPunch.user_id == auto_user)
            )
            .scalars()
            .all()
        )
        assert len(rows_after_second) == 2, (
            f"second tick added punches: {[r.direction for r in rows_after_second]}"
        )

        audit_after_second = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.action == "punch.auto_closed"
                )
            )
            .scalars()
            .all()
        )
        for_user = [
            e
            for e in audit_after_second
            if e.punch_id in {p.id for p in rows_after_second}
        ]
        assert len(for_user) == 1, (
            f"second tick wrote duplicate audit rows: {for_user}"
        )

        # `hours_confirmation_status` did NOT double-stamp — still
        # 'needs_review' on both rows, not promoted/demoted.
        statuses = {r.hours_confirmation_status for r in rows_after_second}
        assert statuses == {"needs_review"}, statuses
    finally:
        db.close()

    # Third tick: make a NEW open session for a fresh user with a
    # cutoff that has passed; the cron must close that one even
    # though `auto_user`'s previous session was already closed and
    # stays closed.
    fresh_user = _make_user(role="sales", email_suffix="fresh")
    fresh_in_local = datetime.combine(
        yesterday_local_date, time(11, 30), tzinfo=tz
    )
    _ = _seed_punch(
        user_id=fresh_user,
        direction="in",
        punched_at=fresh_in_local,
        location_id=auto_loc,
        status="recorded",
    )
    db = SessionLocal()
    try:
        r3 = attendance_close.run_auto_close_pass(
            db, now_override=fake_now_today_morning
        )
        db.commit()
    finally:
        db.close()
    assert r3.closed == 1, r3

    # Auto-close also fires on `max_time_reached` for a session past
    # 24h with NO location cutoff.
    no_loc_user = _make_user(role="sales", email_suffix="maxhrs")
    no_cutoff_loc = _seed_location(default_close=None)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=30)
    long_in_id = _seed_punch(
        user_id=no_loc_user,
        direction="in",
        punched_at=long_ago,
        location_id=no_cutoff_loc,
        status="recorded",
    )

    db = SessionLocal()
    try:
        r4 = attendance_close.run_auto_close_pass(db)
        db.commit()
    finally:
        db.close()
    assert r4.closed >= 1, r4
    db = SessionLocal()
    try:
        outs = (
            db.execute(
                select(StaffPunch)
                .where(StaffPunch.user_id == no_loc_user)
                .where(StaffPunch.direction == "out")
            )
            .scalars()
            .all()
        )
        assert len(outs) == 1
        assert outs[0].auto_close_reason == "max_time_reached"
    finally:
        db.close()

    # ============================================================
    # 3. PRE-CLOSE REMINDER
    # ============================================================
    print("===== pre-close =====")

    pre_user = _make_user(role="sales", email_suffix="pre")
    pre_loc = _seed_location(default_close=time(22, 0))

    # Punch in TODAY at 13:00 local, fixed `now` to 21:45 local so
    # the cutoff is 15 minutes away — inside the 30-minute lead window.
    today_local = datetime.now(tz).date()
    pre_in_local = datetime.combine(today_local, time(13, 0), tzinfo=tz)
    pre_in_id = _seed_punch(
        user_id=pre_user,
        direction="in",
        punched_at=pre_in_local,
        location_id=pre_loc,
        status="recorded",
    )

    # `now_override` lets us pin the wall clock to a deterministic time.
    fake_now = datetime.combine(today_local, time(21, 45), tzinfo=tz).astimezone(
        timezone.utc
    )

    # Capture the email transport so we can verify a send happened
    # without requiring SMTP. The default test transport is the null
    # transport, which exposes a `.sent` list when patched.
    sent_emails: list = []
    real_get_transport = attendance_pre_close.email_transport.get_email_transport

    class _RecordingTransport:
        def send(self, msg):
            sent_emails.append(msg)

    attendance_pre_close.email_transport.get_email_transport = (
        lambda: _RecordingTransport()
    )

    try:
        db = SessionLocal()
        try:
            r5 = attendance_pre_close.run_pre_close_pass(
                db, now_override=fake_now
            )
            db.commit()
        finally:
            db.close()
        assert r5.scanned >= 1, r5
        assert r5.sent == 1, r5

        # The marker row landed.
        db = SessionLocal()
        try:
            markers = (
                db.execute(
                    select(AttendancePreCloseReminder).where(
                        AttendancePreCloseReminder.punch_id == pre_in_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(markers) == 1, markers
            _pre_close_marker_ids.extend([m.id for m in markers])
        finally:
            db.close()

        assert len(sent_emails) == 1
        sent_emails.clear()

        # Second tick same window: the marker prevents the second send.
        db = SessionLocal()
        try:
            r6 = attendance_pre_close.run_pre_close_pass(
                db, now_override=fake_now
            )
            db.commit()
        finally:
            db.close()
        assert r6.sent == 0, r6
        assert r6.skipped_already_sent == 1, r6
        assert len(sent_emails) == 0

        # A run far before the cutoff (08:00 local, 14h pre-cutoff) is
        # outside the lead window: skip without sending and without
        # writing a marker. We use a fresh user so no leftover marker
        # confuses the count.
        early_user = _make_user(role="sales", email_suffix="early")
        early_in_id = _seed_punch(
            user_id=early_user,
            direction="in",
            punched_at=datetime.combine(
                today_local, time(8, 0), tzinfo=tz
            ),
            location_id=pre_loc,
            status="recorded",
        )
        early_now = datetime.combine(
            today_local, time(8, 5), tzinfo=tz
        ).astimezone(timezone.utc)
        db = SessionLocal()
        try:
            r7 = attendance_pre_close.run_pre_close_pass(
                db, now_override=early_now
            )
            db.commit()
        finally:
            db.close()
        # Both pre-existing in-punches are scanned, but the early one
        # is way before its cutoff. No new sends. No new markers.
        assert r7.sent == 0, r7
        db = SessionLocal()
        try:
            early_markers = (
                db.execute(
                    select(AttendancePreCloseReminder).where(
                        AttendancePreCloseReminder.punch_id == early_in_id
                    )
                )
                .scalars()
                .all()
            )
            assert early_markers == [], early_markers
        finally:
            db.close()
    finally:
        attendance_pre_close.email_transport.get_email_transport = (
            real_get_transport
        )

    # ============================================================
    # 4. CRON HEALTH SURFACE
    # ============================================================
    print("===== cron health =====")

    # Run every cron through its `tick()` so the state rows update.
    db = SessionLocal()
    try:
        clock_selfie_retention.tick(db)
    finally:
        db.close()
    db = SessionLocal()
    try:
        attendance_close.tick(db)
    finally:
        db.close()
    db = SessionLocal()
    try:
        attendance_pre_close.tick(db)
    finally:
        db.close()

    admin_id = _make_user(role="admin", email_suffix="cronh")
    db = SessionLocal()
    try:
        admin = db.get(User, admin_id)
        admin_token = create_access_token(admin)
    finally:
        db.close()
    sales_id = _make_user(role="sales", email_suffix="cronh-s")
    db = SessionLocal()
    try:
        sales_user = db.get(User, sales_id)
        sales_token = create_sales_token(sales_user)
    finally:
        db.close()

    # Sales token rejected.
    resp = client.get(
        "/api/admin/cron-health",
        headers={"Authorization": f"Bearer {sales_token}"},
    )
    assert resp.status_code == 403, resp.text

    resp = client.get(
        "/api/admin/cron-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cron_names = {c["name"] for c in body["crons"]}
    attendance_crons = {
        "attendance.auto_close",
        "attendance.pre_close_reminder",
        "attendance.selfie_retention",
    }
    assert attendance_crons.issubset(cron_names), cron_names

    # Only assert against the three attendance crons this smoke ticked.
    # Other crons in the registry (e.g. webhooks.retention) may legitimately
    # be unticked here — their own smokes cover their state.
    for c in body["crons"]:
        if c["name"] not in attendance_crons:
            continue
        assert c["last_finished_at"] is not None, c
        assert c["is_stale"] is False, c
        assert c["last_error"] is None
        assert c["consecutive_failures"] == 0

    # Force a stale state by rolling back finished_at well into the
    # past, then re-querying.
    db = SessionLocal()
    try:
        row = (
            db.query(CronRunState)
            .filter(CronRunState.name == cron_state.AUTO_CLOSE)
            .first()
        )
        row.last_finished_at = datetime.now(timezone.utc) - timedelta(days=10)
        db.commit()
    finally:
        db.close()

    resp = client.get(
        "/api/admin/cron-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    auto_entry = next(
        c for c in body["crons"] if c["name"] == cron_state.AUTO_CLOSE
    )
    assert auto_entry["is_stale"] is True
    assert auto_entry["ok"] is False

    # Failure path: a tick that raises lights up the error fields.
    real_pass = attendance_close.run_auto_close_pass

    def _boom(*args, **kwargs):
        raise RuntimeError("boom from smoke")

    attendance_close.run_auto_close_pass = _boom
    try:
        db = SessionLocal()
        try:
            try:
                attendance_close.tick(db)
            except RuntimeError:
                pass
        finally:
            db.close()
    finally:
        attendance_close.run_auto_close_pass = real_pass

    resp = client.get(
        "/api/admin/cron-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = resp.json()
    auto_entry = next(
        c for c in body["crons"] if c["name"] == cron_state.AUTO_CLOSE
    )
    assert auto_entry["last_error"] is not None
    assert "boom from smoke" in auto_entry["last_error"]
    assert auto_entry["consecutive_failures"] >= 1
    assert auto_entry["ok"] is False

    # Recover: a successful tick clears `last_error` and the failure counter.
    db = SessionLocal()
    try:
        attendance_close.tick(db)
    finally:
        db.close()
    resp = client.get(
        "/api/admin/cron-health",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    body = resp.json()
    auto_entry = next(
        c for c in body["crons"] if c["name"] == cron_state.AUTO_CLOSE
    )
    assert auto_entry["last_error"] is None
    assert auto_entry["consecutive_failures"] == 0
    assert auto_entry["is_stale"] is False
    assert auto_entry["ok"] is True

    print("attendance_crons smoke ok")
    _restore_business_profile(profile_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
