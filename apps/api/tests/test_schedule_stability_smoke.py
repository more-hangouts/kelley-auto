"""Smoke for Phase 10 Slice 4 (stability patches).

Four edge-case behaviors landed in this slice; this smoke covers
each one end-to-end:

  1. **missing_out_punch cron** — flips published entries whose
     stylist clocked in but never clocked out, once business_date
     has passed. Leaves entries that auto-close already closed
     (because auto-close now stamps the entry) alone. Resolve
     endpoint inserts a paired out-punch, links it, re-derives
     attendance_status, and writes an audit row.
  2. **60-second idempotency debounce** — a rapid second clock-in
     (or clock-out) within 60s returns the existing punch instead
     of inserting a duplicate row. Catches shared-iPad double-tap
     and network-retry cases.
  3. **publish vs. time-off race** — `publish_week` is per-shift:
     drafts that overlap an approved time-off are skipped (listed
     in `skipped`); non-conflicting drafts in the same publish
     still go through. The /publish HTTP route returns 200 with
     the skipped list, not 409.
  4. **Cross-day shift math** — a 21:00 → 01:00 shift records a
     business_date tied to the START day and `hours_variance`
     credits exactly 4 scheduled hours and 4 actual hours when
     clock-in/out punches bracket the interval.
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    StaffLocation,
    StaffPunch,
    StaffPunchAuditEvent,
    StaffScheduleEntry,
    TimeOffRequest,
    User,
)
from services import clock_in, missing_out_punch_cron, staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_entry_ids: list[int] = []
_punch_ids: list[int] = []
_location_ids: list[int] = []
_parked_location_ids: list[int] = []
_tor_ids: list[int] = []

PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p10s4-{suffix}",
            email=f"{role}-p10s4-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P10S4 {role.title()} {suffix}",
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


def _token_admin(user_id: int) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_access_token(u)
    finally:
        db.close()


def _seed_location() -> int:
    db = SessionLocal()
    try:
        existing = db.execute(
            sql_text("SELECT id FROM staff_locations WHERE active = TRUE")
        ).all()
        for row in existing:
            _parked_location_ids.append(int(row[0]))
        if existing:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = FALSE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [int(r[0]) for r in existing]},
            )
        loc = StaffLocation(
            name=f"P10S4 Probe {uuid.uuid4().hex[:6]}",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=200,
            active=True,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _publish_entry(
    *,
    actor_user_id: int,
    user_id: int,
    business_date_: date,
    starts_at_local: datetime,
    ends_at_local: datetime,
    late_grace_minutes: int = 10,
) -> dict:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_user_id,
            user_id=user_id,
            business_date_=business_date_,
            starts_at_local=starts_at_local,
            ends_at_local=ends_at_local,
            late_grace_minutes=late_grace_minutes,
            publish=True,
        )
        db.commit()
        _entry_ids.append(d["id"])
        return d
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE actor_user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_punches WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_decision_events "
                    "WHERE request_id IN (SELECT id FROM time_off_requests "
                    "WHERE user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_requests "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _parked_location_ids:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = TRUE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _parked_location_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    sales_a_id = _make_user(role="sales")
    sales_b_id = _make_user(role="sales")
    _seed_location()
    admin_hdr = {"Authorization": f"Bearer {_token_admin(admin_id)}"}

    # ============================================================
    # 1) missing_out_punch CRON + RESOLVE
    # ============================================================
    print("===== missing_out_punch cron =====")
    # Seed an entry on a past business_date and a paired in-punch only.
    # The cron's "business_date < today" guard means we have to use a
    # real-time past date.
    past_local = datetime.now(tz) - timedelta(days=2)
    past_date = past_local.date()
    entry = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_a_id,
        business_date_=past_date,
        starts_at_local=datetime(
            past_date.year, past_date.month, past_date.day, 9, 0, tzinfo=tz
        ),
        ends_at_local=datetime(
            past_date.year, past_date.month, past_date.day, 17, 0, tzinfo=tz
        ),
        late_grace_minutes=10,
    )

    # Insert a synthetic in-punch and link it to the entry. We
    # bypass clock_in.punch_in here because the geofence dance isn't
    # the subject under test for this case.
    db = SessionLocal()
    try:
        in_punch = StaffPunch(
            user_id=sales_a_id,
            direction="in",
            punched_at=datetime(
                past_date.year, past_date.month, past_date.day, 9, 5,
                tzinfo=tz,
            ).astimezone(timezone.utc),
            status="recorded",
            location_id=_location_ids[0],
        )
        db.add(in_punch)
        db.commit()
        db.refresh(in_punch)
        _punch_ids.append(in_punch.id)

        entry_row = db.get(StaffScheduleEntry, entry["id"])
        entry_row.actual_clock_in_punch_id = in_punch.id
        entry_row.attendance_status = "present"
        db.commit()

        # Sanity: pre-cron, status is 'present'.
        assert entry_row.attendance_status == "present"

        # Also seed a paired in+out entry the cron must NOT flip.
        clean_entry = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_b_id,
            business_date_=past_date,
            starts_at_local=datetime(
                past_date.year, past_date.month, past_date.day, 9, 0,
                tzinfo=tz,
            ),
            ends_at_local=datetime(
                past_date.year, past_date.month, past_date.day, 17, 0,
                tzinfo=tz,
            ),
            publish=True,
        )
        db.commit()
        _entry_ids.append(clean_entry["id"])

        clean_in = StaffPunch(
            user_id=sales_b_id,
            direction="in",
            punched_at=datetime(
                past_date.year, past_date.month, past_date.day, 9, 5,
                tzinfo=tz,
            ).astimezone(timezone.utc),
            status="recorded",
            location_id=_location_ids[0],
        )
        clean_out = StaffPunch(
            user_id=sales_b_id,
            direction="out",
            punched_at=datetime(
                past_date.year, past_date.month, past_date.day, 17, 0,
                tzinfo=tz,
            ).astimezone(timezone.utc),
            status="recorded",
            location_id=_location_ids[0],
        )
        db.add_all([clean_in, clean_out])
        db.commit()
        db.refresh(clean_in)
        db.refresh(clean_out)
        _punch_ids.extend([clean_in.id, clean_out.id])

        clean_row = db.get(StaffScheduleEntry, clean_entry["id"])
        clean_row.actual_clock_in_punch_id = clean_in.id
        clean_row.actual_clock_out_punch_id = clean_out.id
        clean_row.attendance_status = "present"
        db.commit()
    finally:
        db.close()

    # Tick the cron — flips entry, leaves clean_entry alone.
    db = SessionLocal()
    try:
        missing_out_punch_cron.tick(db)
        flipped = db.get(StaffScheduleEntry, entry["id"])
        untouched = db.get(StaffScheduleEntry, clean_entry["id"])
        assert flipped.attendance_status == "missing_out_punch", (
            f"expected missing_out_punch, got {flipped.attendance_status}"
        )
        assert untouched.attendance_status == "present", (
            f"clean entry should not have flipped, got "
            f"{untouched.attendance_status}"
        )

        # Re-tick is idempotent — the flipped entry now has
        # attendance_status='missing_out_punch' which the candidate
        # query excludes.
        missing_out_punch_cron.tick(db)
        again = db.get(StaffScheduleEntry, entry["id"])
        assert again.attendance_status == "missing_out_punch"
    finally:
        db.close()

    # Cron health row written.
    db = SessionLocal()
    try:
        from services.cron_state import SCHEDULE_MISSING_OUT_PUNCH, all_states

        states = {s["name"]: s for s in all_states(db)}
        row = states[SCHEDULE_MISSING_OUT_PUNCH]
        assert row["last_finished_at"] is not None
        assert row["last_error"] is None
    finally:
        db.close()

    # Flagged-exceptions endpoint surfaces it alongside no_show.
    resp = client.get(
        "/api/admin/schedule/flagged-exceptions",
        headers=admin_hdr,
        params={
            "from_date": (past_date - timedelta(days=1)).isoformat(),
            "to_date": (past_date + timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    exc_ids = {row["id"] for row in resp.json()["exceptions"]}
    assert entry["id"] in exc_ids
    statuses = {
        row["id"]: row["attendance_status"]
        for row in resp.json()["exceptions"]
    }
    assert statuses[entry["id"]] == "missing_out_punch"

    # Resolve endpoint: manager supplies the missing out time.
    resolve_resp = client.post(
        f"/api/admin/schedule/entries/{entry['id']}/resolve-missing-out",
        headers=admin_hdr,
        json={
            "out_at_local": datetime(
                past_date.year, past_date.month, past_date.day, 17, 30,
                tzinfo=tz,
            ).isoformat(),
            "notes": "stylist left without clocking out",
        },
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    body = resolve_resp.json()
    assert body["attendance_status"] == "present", (
        "in-punch was at 9:05 with 10m grace → 'present' on resolve"
    )
    assert body["actual_clock_out_punch_id"] is not None

    # Audit row was written.
    db = SessionLocal()
    try:
        audit = (
            db.query(StaffPunchAuditEvent)
            .filter(
                StaffPunchAuditEvent.action
                == "punch.missing_out_resolved"
            )
            .filter(StaffPunchAuditEvent.actor_user_id == admin_id)
            .first()
        )
        assert audit is not None, "resolve must write an audit row"
        assert audit.actor_kind == "owner"
    finally:
        db.close()

    # 409 on a non-missing-out entry.
    bad_resolve = client.post(
        f"/api/admin/schedule/entries/{clean_entry['id']}/resolve-missing-out",
        headers=admin_hdr,
        json={
            "out_at_local": datetime(
                past_date.year, past_date.month, past_date.day, 18, 0,
                tzinfo=tz,
            ).isoformat(),
        },
    )
    assert bad_resolve.status_code == 409, bad_resolve.text
    assert (
        bad_resolve.json()["detail"]["code"] == "entry_not_missing_out_punch"
    )

    # ============================================================
    # 2) 60-SECOND IDEMPOTENCY DEBOUNCE
    # ============================================================
    print("===== 60-second debounce =====")
    sales_c_id = _make_user(role="sales")
    db = SessionLocal()
    try:
        user_c = db.get(User, sales_c_id)
        now_override = datetime.now(tz).astimezone(timezone.utc)

        # First clock-in writes a row.
        first = clock_in.punch_in(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override,
        )
        db.commit()
        _punch_ids.append(first.id)

        # Rapid second clock-in 5 seconds later returns the SAME row,
        # no insert. This is what would otherwise raise
        # 'already_punched_in' 409 under the pre-Slice-4 behavior.
        second = clock_in.punch_in(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override + timedelta(seconds=5),
        )
        db.commit()
        assert second.id == first.id, (
            f"debounce should return existing punch, got new id {second.id}"
        )

        # Confirm no duplicate row exists.
        count = (
            db.query(StaffPunch)
            .filter(StaffPunch.user_id == sales_c_id)
            .filter(StaffPunch.direction == "in")
            .count()
        )
        assert count == 1, (
            f"expected exactly 1 in-punch after debounce, got {count}"
        )

        # Rapid clock-out also debounces.
        out_first = clock_in.punch_out(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override + timedelta(seconds=10),
        )
        db.commit()
        _punch_ids.append(out_first.id)

        out_second = clock_in.punch_out(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override + timedelta(seconds=20),
        )
        db.commit()
        assert out_second.id == out_first.id

        out_count = (
            db.query(StaffPunch)
            .filter(StaffPunch.user_id == sales_c_id)
            .filter(StaffPunch.direction == "out")
            .count()
        )
        assert out_count == 1, (
            f"expected exactly 1 out-punch after debounce, got {out_count}"
        )

        # Outside the window, a legitimate new same-direction punch
        # IS allowed (clock back in 70 seconds later).
        third = clock_in.punch_in(
            db,
            user=user_c,
            client_lat=PROBE_LAT,
            client_lng=PROBE_LNG,
            now_override=now_override + timedelta(seconds=90),
        )
        db.commit()
        _punch_ids.append(third.id)
        assert third.id != first.id, (
            "after the 60s window, a new in-punch must be inserted"
        )
    finally:
        db.close()

    # ============================================================
    # 3) PUBLISH vs. TIME-OFF (per-shift partial publish)
    # ============================================================
    print("===== publish vs. time-off race =====")
    sales_d_id = _make_user(role="sales")
    # Pick a future-ish Monday week so we don't collide with other
    # smokes' simulated dates.
    week_start_d = date(2026, 7, 6)  # Monday
    assert week_start_d.isoweekday() == 1

    db = SessionLocal()
    try:
        # Draft 1: Tuesday — will be allowed
        tue = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_d_id,
            business_date_=week_start_d + timedelta(days=1),
            starts_at_local=datetime(2026, 7, 7, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 7, 7, 17, 0, tzinfo=tz),
        )
        # Draft 2: Wednesday — will conflict with a freshly-approved TOR
        wed = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_d_id,
            business_date_=week_start_d + timedelta(days=2),
            starts_at_local=datetime(2026, 7, 8, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 7, 8, 17, 0, tzinfo=tz),
        )
        db.commit()
        _entry_ids.extend([tue["id"], wed["id"]])

        # Approve a time-off for Wednesday AFTER the drafts were
        # created — simulates the race.
        tor = TimeOffRequest(
            user_id=sales_d_id,
            starts_at=datetime(2026, 7, 8, 0, 0, tzinfo=tz),
            ends_at=datetime(2026, 7, 9, 0, 0, tzinfo=tz),
            reason="family",
            status="approved",
            decided_by_user_id=admin_id,
            decided_at=datetime.now(timezone.utc),
        )
        db.add(tor)
        db.commit()
        db.refresh(tor)
        _tor_ids.append(tor.id)
    finally:
        db.close()

    # publish_week returns 200 with skipped[]; Tuesday publishes,
    # Wednesday is in skipped.
    pub = client.post(
        "/api/admin/schedule/publish",
        headers=admin_hdr,
        json={
            "week_start": week_start_d.isoformat(),
            "user_ids": [sales_d_id],
        },
    )
    assert pub.status_code == 200, pub.text
    body = pub.json()
    published_ids = set(body["entry_ids"])
    skipped_ids = {row["entry_id"] for row in body["skipped"]}
    assert tue["id"] in published_ids, (
        "Tuesday draft (no conflict) should have published"
    )
    assert wed["id"] in skipped_ids, (
        "Wednesday draft (conflict) should be in skipped[]"
    )
    # The skipped row carries the conflicting TOR id so the UI can
    # name it.
    wed_skip = next(r for r in body["skipped"] if r["entry_id"] == wed["id"])
    assert wed_skip["time_off_request_id"] == tor.id

    # Wednesday must remain a draft after the partial publish.
    db = SessionLocal()
    try:
        wed_row = db.get(StaffScheduleEntry, wed["id"])
        assert wed_row.status == "draft"
        tue_row = db.get(StaffScheduleEntry, tue["id"])
        assert tue_row.status == "published"
    finally:
        db.close()

    # ============================================================
    # 4) CROSS-MIDNIGHT SHIFT MATH
    # ============================================================
    print("===== cross-midnight shift =====")
    sales_e_id = _make_user(role="sales")
    # 2026-07-10 (Friday) 21:00 → 2026-07-11 (Sat) 01:00 — 4 hours.
    overnight_start = datetime(2026, 7, 10, 21, 0, tzinfo=tz)
    overnight_end = datetime(2026, 7, 11, 1, 0, tzinfo=tz)
    overnight = _publish_entry(
        actor_user_id=admin_id,
        user_id=sales_e_id,
        business_date_=date(2026, 7, 10),
        starts_at_local=overnight_start,
        ends_at_local=overnight_end,
        late_grace_minutes=10,
    )

    # Invariant 1: business_date is the START day, not the end day.
    db = SessionLocal()
    try:
        row = db.get(StaffScheduleEntry, overnight["id"])
        assert row.business_date == date(2026, 7, 10), (
            f"business_date should be Fri (start), got {row.business_date}"
        )
        # Invariant 2: the duration is exactly 4 hours, computed
        # against absolute TIMESTAMPTZ values (not wall-clock day math).
        delta_hours = (
            row.ends_at_local - row.starts_at_local
        ).total_seconds() / 3600.0
        assert abs(delta_hours - 4.0) < 1e-6, (
            f"overnight shift duration should be 4h, got {delta_hours}"
        )

        # Seed a clock-in at 21:00 and clock-out at 01:00 against the
        # entry so hours_variance has an actual pair to credit.
        in_p = StaffPunch(
            user_id=sales_e_id,
            direction="in",
            punched_at=overnight_start.astimezone(timezone.utc),
            status="recorded",
            location_id=_location_ids[0],
        )
        out_p = StaffPunch(
            user_id=sales_e_id,
            direction="out",
            punched_at=overnight_end.astimezone(timezone.utc),
            status="recorded",
            location_id=_location_ids[0],
        )
        db.add_all([in_p, out_p])
        db.commit()
        db.refresh(in_p)
        db.refresh(out_p)
        _punch_ids.extend([in_p.id, out_p.id])

        row.actual_clock_in_punch_id = in_p.id
        row.actual_clock_out_punch_id = out_p.id
        row.attendance_status = "present"
        db.commit()
    finally:
        db.close()

    # Invariant 3: hours_variance credits exactly 4 scheduled + 4 actual.
    var_resp = client.get(
        "/api/admin/schedule/variance",
        headers=admin_hdr,
        params={
            "from_date": "2026-07-10",
            "to_date": "2026-07-11",
        },
    )
    assert var_resp.status_code == 200, var_resp.text
    rows = {r["user_id"]: r for r in var_resp.json()["rows"]}
    assert sales_e_id in rows
    e_row = rows[sales_e_id]
    assert abs(e_row["scheduled_hours"] - 4.0) < 1e-6, (
        f"overnight scheduled_hours should be 4, got {e_row['scheduled_hours']}"
    )
    assert abs(e_row["actual_hours"] - 4.0) < 1e-6, (
        f"overnight actual_hours should be 4, got {e_row['actual_hours']}"
    )
    assert abs(e_row["variance_hours"]) < 1e-6

    # ============================================================
    # 5) PUBLISH vs. PENDING APPROVAL — full bidirectional race fix
    # ============================================================
    # The Slice-4 audit caught two gaps in #3's race protection:
    #   (a) `_conflicting_time_off_locked` only locked rows already
    #       `'approved'`, so a concurrent pending→approved flip
    #       could slip past.
    #   (b) `time_off.decide_request` never checked / locked
    #       overlapping schedule_entries, so an approval on top of
    #       an already-published entry silently drifted.
    # This block verifies the synchronous shape of both fixes; true
    # parallel race coverage would require a multi-process smoke,
    # which the locks themselves are designed to handle once both
    # sides take SELECT FOR UPDATE on the relevant rows.
    print("===== publish/approve race fixes =====")
    from services import time_off
    from services.time_off import TimeOffServiceError

    sales_f_id = _make_user(role="sales")
    db = SessionLocal()
    try:
        # (a) Pending TOR + draft entry that overlaps. Publish should
        # not skip the draft because pending isn't yet a conflict;
        # the lock just *serializes* against the pending row.
        pending_tor = TimeOffRequest(
            user_id=sales_f_id,
            starts_at=datetime(2026, 7, 20, 0, 0, tzinfo=tz),
            ends_at=datetime(2026, 7, 21, 0, 0, tzinfo=tz),
            reason="vacation",
            status="pending",
        )
        db.add(pending_tor)
        db.commit()
        db.refresh(pending_tor)
        _tor_ids.append(pending_tor.id)

        # Draft Mon entry overlapping the pending TOR.
        draft_mon = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_f_id,
            business_date_=date(2026, 7, 20),
            starts_at_local=datetime(2026, 7, 20, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 7, 20, 17, 0, tzinfo=tz),
        )
        db.commit()
        _entry_ids.append(draft_mon["id"])

        # publish_week against this draft completes (pending != approved
        # → not a conflict), and the FOR UPDATE on the pending row is
        # released on commit.
        result = staff_schedule.publish_week(
            db,
            actor_user_id=admin_id,
            week_start=date(2026, 7, 20),
            user_ids=[sales_f_id],
        )
        db.commit()
        published_ids = set(result["entry_ids"])
        skipped_ids = {row["entry_id"] for row in result["skipped"]}
        assert draft_mon["id"] in published_ids, (
            "publish over a PENDING (not approved) TOR should succeed"
        )
        assert draft_mon["id"] not in skipped_ids

        # (b) Approving the still-pending TOR over the now-published
        # entry must be REJECTED with schedule_entry_conflict and the
        # offending entry id in extra.entries — proves Slice-5-fix
        # #2b is wired.
        try:
            time_off.decide_request(
                db,
                actor_user_id=admin_id,
                request_id=pending_tor.id,
                decision="approved",
                decision_notes=None,
            )
        except TimeOffServiceError as exc:
            assert exc.code == "schedule_entry_conflict", (
                f"expected schedule_entry_conflict, got {exc.code}"
            )
            assert exc.http_status == 409
            offending = {
                row["entry_id"] for row in exc.extra.get("entries", [])
            }
            assert draft_mon["id"] in offending, (
                f"published entry id should be in the conflict report, "
                f"got {offending}"
            )
        else:
            raise AssertionError(
                "decide_request approved over a published conflict "
                "without raising — Slice-5-fix #2b regressed"
            )
        db.rollback()

        # Re-loaded TOR is still pending — the rejected approval did
        # not mutate the row.
        again = db.get(TimeOffRequest, pending_tor.id)
        assert again.status == "pending", (
            "rejected approval should not have flipped status"
        )

        # DENYING the same TOR is allowed even though a published
        # entry overlaps — denial doesn't create a scheduling
        # conflict.
        denied = time_off.decide_request(
            db,
            actor_user_id=admin_id,
            request_id=pending_tor.id,
            decision="denied",
            decision_notes="overlaps a published shift",
        )
        db.commit()
        assert denied["status"] == "denied"
    finally:
        db.close()

    # ============================================================
    # 6) HTTP surface of schedule_entry_conflict on /decide
    # ============================================================
    # The admin time-off router must surface the new `extra.entries`
    # in the response body so the UI can name the offending shifts.
    print("===== /decide schedule_entry_conflict HTTP shape =====")
    sales_g_id = _make_user(role="sales")
    tor_g_id: int
    pub_g_id: int
    db = SessionLocal()
    try:
        tor = TimeOffRequest(
            user_id=sales_g_id,
            starts_at=datetime(2026, 7, 22, 0, 0, tzinfo=tz),
            ends_at=datetime(2026, 7, 23, 0, 0, tzinfo=tz),
            reason="vacation",
            status="pending",
        )
        db.add(tor)
        db.commit()
        db.refresh(tor)
        tor_g_id = tor.id
        _tor_ids.append(tor_g_id)

        # Publish a Wed entry that overlaps the TOR.
        pub = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_g_id,
            business_date_=date(2026, 7, 22),
            starts_at_local=datetime(2026, 7, 22, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 7, 22, 17, 0, tzinfo=tz),
            publish=True,
        )
        db.commit()
        pub_g_id = pub["id"]
        _entry_ids.append(pub_g_id)
    finally:
        db.close()

    decide_resp = client.post(
        f"/api/admin/time-off/{tor_g_id}/decide",
        headers=admin_hdr,
        json={"status": "approved", "decision_notes": None},
    )
    assert decide_resp.status_code == 409, decide_resp.text
    body = decide_resp.json()["detail"]
    assert body["code"] == "schedule_entry_conflict"
    offending_ids = {row["entry_id"] for row in body.get("entries", [])}
    assert pub_g_id in offending_ids, (
        f"/decide response missing the offending entry id: {body}"
    )

    print("phase10_stability smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
