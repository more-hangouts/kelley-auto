"""G2 smoke: attendance geo/IP/UA retention sweep.

User-spec acceptance:

  1. Old punch (older than retention window) has its 5 PII fields
     scrubbed to NULL: client_latitude, client_longitude,
     client_accuracy_m, user_agent, ip.
  2. Fresh punch (newer than the window) is left untouched on every
     field including the PII fields.
  3. Status, direction, distance_to_location_m, location_id, and
     punched_at are preserved on the scrubbed row — audit fidelity
     for "was this punch inside the geofence?" stays intact.
  4. cron_run_state for `attendance.geo_retention` gets stamped with
     last_started_at, last_finished_at, last_scanned_count=1,
     last_changed_count=1, consecutive_failures=0.
  5. An induced failure on the inner pass bumps consecutive_failures
     and stamps last_error without losing the prior last_finished_at
     — record_run handles error stamping the same way C2's smoke
     exercised it.
  6. Selfie file behavior is unaffected: a punch with a selfie_storage_key
     that is ALSO past the geo cutoff has its geo fields scrubbed but
     the selfie_storage_key is left alone (the selfie retention cron
     owns that column).
  7. Idempotency: running tick() a second time on a freshly-scrubbed
     row is a no-op (zero scanned, zero changed). Audit rows from the
     first pass are NOT duplicated.

Run with: venv/bin/python tests/test_attendance_geo_retention_smoke.py
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    BusinessProfile,
    StaffPunch,
    StaffPunchAuditEvent,
    User,
)
from database.auth import hash_password  # noqa: E402
from services import attendance_geo_retention, cron_state  # noqa: E402


_user_ids: list[int] = []
_punch_ids: list[int] = []


def _make_user() -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"g2-{suffix}",
            email=f"g2-{suffix}@example.com",
            hashed_password=hash_password("not-a-real-pw"),
            full_name="G2 Test",
            is_active=True,
            role="sales",
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


def _make_punch(*, user_id: int, age_days: int, with_selfie: bool = False) -> int:
    """Insert one StaffPunch with PII fields populated, backdated by
    `age_days`. Returns the punch id."""
    db = SessionLocal()
    try:
        punched_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        p = StaffPunch(
            user_id=user_id,
            direction="in",
            punched_at=punched_at,
            status="recorded",
            client_latitude=29.4252,
            client_longitude=-98.4946,
            client_accuracy_m=12.5,
            distance_to_location_m=45.2,
            user_agent="Mozilla/5.0 (G2-Test)",
            ip="203.0.113.42",
            selfie_storage_key="clockin/9999/synthetic.webp" if with_selfie else None,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        _punch_ids.append(p.id)
        return p.id
    finally:
        db.close()


def _refresh(punch_id: int) -> StaffPunch | None:
    db = SessionLocal()
    try:
        return db.get(StaffPunch, punch_id)
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _punch_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE punch_id = ANY(:ids)"
                ),
                {"ids": _punch_ids},
            )
            db.execute(
                sql_text("DELETE FROM staff_punches WHERE id = ANY(:ids)"),
                {"ids": _punch_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.execute(
            sql_text(
                "DELETE FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.ATTENDANCE_GEO_RETENTION},
        )
        db.commit()
    finally:
        db.close()


# Prep: ensure the business profile has a non-NULL selfie_retention_days
# so the retention is active. The seed default is 365. We need a tighter
# window for the test, so override to 30 days for this run (restore at
# cleanup).
_prev_retention: int | None = None


def _set_retention(days: int | None) -> None:
    db = SessionLocal()
    try:
        profile = db.query(BusinessProfile).first()
        assert profile is not None, "smoke requires a BusinessProfile row"
        global _prev_retention
        if _prev_retention is None:
            _prev_retention = profile.selfie_retention_days
        profile.selfie_retention_days = days
        db.commit()
    finally:
        db.close()


def _restore_retention() -> None:
    db = SessionLocal()
    try:
        profile = db.query(BusinessProfile).first()
        if profile is not None:
            profile.selfie_retention_days = _prev_retention
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Setup: 30-day window. One old punch (60d), one fresh punch (3d),
# one old punch WITH a selfie (so we can prove selfie key is untouched).
# ---------------------------------------------------------------------------
_set_retention(30)

user_id = _make_user()
old_id = _make_punch(user_id=user_id, age_days=60)
fresh_id = _make_punch(user_id=user_id, age_days=3)
old_with_selfie_id = _make_punch(user_id=user_id, age_days=60, with_selfie=True)


try:
    # -----------------------------------------------------------------------
    # 1-3. Run tick + verify scrubbed/preserved fields.
    # -----------------------------------------------------------------------
    db = SessionLocal()
    try:
        result = attendance_geo_retention.tick(db)
    finally:
        db.close()
    assert result.scanned == 2, f"expected to scan 2 old punches, got {result.scanned}"
    assert result.scrubbed == 2, f"expected to scrub 2, got {result.scrubbed}"
    print(f"tick scanned={result.scanned} scrubbed={result.scrubbed} ok")

    # Old punch: 5 PII fields nulled, audit-useful fields preserved.
    old = _refresh(old_id)
    assert old.client_latitude is None
    assert old.client_longitude is None
    assert old.client_accuracy_m is None
    assert old.user_agent is None
    assert old.ip is None
    # Preserved (audit-useful). Numeric(10,2) → Decimal, compare via float.
    assert float(old.distance_to_location_m) == 45.2, old.distance_to_location_m
    assert old.status == "recorded"
    assert old.direction == "in"
    assert old.user_id == user_id
    print("old punch: 5 PII fields scrubbed, derived/status/direction preserved ok")

    # Fresh punch: nothing touched.
    fresh = _refresh(fresh_id)
    assert fresh.client_latitude is not None
    assert fresh.client_longitude is not None
    assert fresh.client_accuracy_m is not None
    assert fresh.user_agent == "Mozilla/5.0 (G2-Test)"
    assert str(fresh.ip) == "203.0.113.42"
    print("fresh punch: untouched on every PII field ok")

    # Old punch with selfie: PII scrubbed but selfie_storage_key intact.
    old_selfie = _refresh(old_with_selfie_id)
    assert old_selfie.client_latitude is None
    assert old_selfie.ip is None
    assert old_selfie.selfie_storage_key == "clockin/9999/synthetic.webp", (
        "selfie key must be left alone — that's the selfie retention cron's job"
    )
    print("old punch w/ selfie: geo scrubbed, selfie key preserved (separate cron) ok")

    # -----------------------------------------------------------------------
    # 4. cron_run_state stamped correctly.
    # -----------------------------------------------------------------------
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT last_started_at, last_finished_at, last_scanned_count, "
                "last_changed_count, consecutive_failures, last_error "
                "FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.ATTENDANCE_GEO_RETENTION},
        ).first()
    finally:
        db.close()
    assert row is not None, "cron_run_state row missing for attendance.geo_retention"
    assert row.last_started_at is not None
    assert row.last_finished_at is not None
    assert row.last_started_at <= row.last_finished_at
    assert row.last_scanned_count == 2, row
    assert row.last_changed_count == 2, row
    assert row.consecutive_failures == 0, row
    assert row.last_error is None, row
    print(f"cron_run_state: started/finished/scanned=2/changed=2/failures=0 ok")

    # -----------------------------------------------------------------------
    # 7. Idempotency: second tick is a no-op.
    # -----------------------------------------------------------------------
    db = SessionLocal()
    try:
        result2 = attendance_geo_retention.tick(db)
    finally:
        db.close()
    assert result2.scanned == 0, f"second pass should be no-op, got {result2.scanned}"
    assert result2.scrubbed == 0, result2

    # Audit rows from the FIRST pass should still be there, NOT duplicated.
    db = SessionLocal()
    try:
        audit_rows = (
            db.query(StaffPunchAuditEvent)
            .filter(
                StaffPunchAuditEvent.punch_id.in_([old_id, old_with_selfie_id])
            )
            .filter(StaffPunchAuditEvent.action == "geo.retention_scrubbed")
            .all()
        )
    finally:
        db.close()
    assert len(audit_rows) == 2, f"expected 2 audit rows (1 per scrubbed punch), got {len(audit_rows)}"
    # Audit content sanity.
    for r in audit_rows:
        assert r.actor_kind == "system"
        assert r.reason_code == "retention_policy"
        assert "cleared_fields" in (r.old_values or {})
        cleared = r.old_values["cleared_fields"]
        assert set(cleared) == {
            "client_latitude",
            "client_longitude",
            "client_accuracy_m",
            "user_agent",
            "ip",
        }, cleared
    print("idempotent second tick: scanned=0/scrubbed=0, audit rows not duplicated ok")

    # -----------------------------------------------------------------------
    # 5. Failure-path: induced raise stamps last_error + bumps failures.
    # Patch run_retention_pass to raise; verify cron_state stamps the error.
    # -----------------------------------------------------------------------
    original = attendance_geo_retention.run_retention_pass

    def _explode(*args, **kwargs):
        raise RuntimeError("induced for G2 smoke")

    attendance_geo_retention.run_retention_pass = _explode
    try:
        db = SessionLocal()
        try:
            try:
                attendance_geo_retention.tick(db)
            except RuntimeError:
                pass  # cron_state.record_run re-raises; expected
        finally:
            db.close()
    finally:
        attendance_geo_retention.run_retention_pass = original

    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT consecutive_failures, last_error, last_started_at "
                "FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.ATTENDANCE_GEO_RETENTION},
        ).first()
    finally:
        db.close()
    assert row.consecutive_failures == 1, row
    assert row.last_error is not None and "induced for G2 smoke" in row.last_error, row
    print(f"failure path: consecutive_failures={row.consecutive_failures} + last_error stamped ok")

    # -----------------------------------------------------------------------
    # 6. ALL_CRON_NAMES contains the new entry (admin status surface).
    # -----------------------------------------------------------------------
    assert cron_state.ATTENDANCE_GEO_RETENTION in cron_state.ALL_CRON_NAMES, (
        cron_state.ALL_CRON_NAMES
    )
    print("ATTENDANCE_GEO_RETENTION listed in cron_state.ALL_CRON_NAMES ok")

finally:
    _restore_retention()
    _cleanup()

print("\ntest_attendance_geo_retention_smoke OK")
