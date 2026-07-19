"""Smoke test for Phase 10 Slice 1 (per-day published schedule).

Covers the three layers the slice introduces:

  1. Schema/ORM round-trip on `staff_schedule_entries` — the migration's
     own DML probes already exercise every CHECK; this re-runs the
     headlines through the SQLAlchemy model so a future ORM change
     can't quietly bypass them.
  2. Service-level invariants in `services.staff_schedule`:
        - invalid_date_range
        - business_date_mismatch
        - late_grace_out_of_range
        - duplicate_entry
        - time_off_conflict on publish (both single-entry create and
          bulk publish_week paths)
        - mark_excused only flips no_show rows
  3. Resolver precedence: a published entry beats an override beats a
     base template. Same test seeds all three on overlapping dates
     and asserts the resolver picks the entry.
  4. End-to-end via the admin router:
        - sales token → 403 on every admin verb
        - GET /week returns staff + entries + time_off_blocks
        - POST /entries (draft) → PATCH → POST /publish
        - POST /entries/{id}/notes works on a published row
        - POST /entries/{id}/excuse on a hand-seeded no_show row
"""

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
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    StaffScheduleEntry,
    StaffShift,
    StaffShiftOverride,
    TimeOffRequest,
    User,
)
from services import shift_resolver, staff_schedule  # noqa: E402
from services.staff_schedule import StaffScheduleError  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_shift_ids: list[int] = []
_override_ids: list[int] = []
_entry_ids: list[int] = []
_tor_ids: list[int] = []


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p10-{suffix}",
            email=f"{role}-p10-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P10 {role.title()} {suffix}",
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


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM time_off_decision_events "
                    "WHERE request_id IN ("
                    "SELECT id FROM time_off_requests "
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
                sql_text(
                    "DELETE FROM staff_shift_overrides "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shifts WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _expect_error(
    callable_, code: str, *, http_status: int | None = None
) -> StaffScheduleError:
    """Run `callable_()` expecting a StaffScheduleError; assert its
    code (and optional http_status) match. Returns the exception so
    extra context can be inspected."""
    try:
        callable_()
    except StaffScheduleError as exc:
        assert exc.code == code, (
            f"expected code={code!r}, got {exc.code!r}"
        )
        if http_status is not None:
            assert exc.http_status == http_status, (
                f"expected http_status={http_status}, got {exc.http_status}"
            )
        return exc
    raise AssertionError(f"expected StaffScheduleError({code}) — got none")


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)

    admin_id = _make_user(role="admin")
    sales_a_id = _make_user(role="sales")
    sales_b_id = _make_user(role="sales")
    admin_hdr = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sales_hdr = {"Authorization": f"Bearer {_token(sales_a_id, sales=True)}"}

    week_start = date(2026, 6, 1)  # Monday
    assert week_start.isoweekday() == 1

    # ============================================================
    # 1) SCHEMA + ORM ROUND-TRIP
    # ============================================================
    print("===== schema round-trip =====")
    db = SessionLocal()
    try:
        entry = StaffScheduleEntry(
            user_id=sales_a_id,
            business_date=week_start,
            starts_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
            late_grace_minutes=10,
            source="manual",
            created_by_user_id=admin_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        _entry_ids.append(entry.id)
        # Server defaults.
        assert entry.status == "draft"
        assert entry.attendance_status == "scheduled"
        assert entry.published_at is None
        assert entry.published_by_user_id is None
        # Round-trip values.
        assert int(entry.late_grace_minutes) == 10
        # Cleanup so the rest of the suite has a clean slate for this user.
        db.delete(entry)
        db.commit()
        _entry_ids.remove(entry.id)
    finally:
        db.close()

    # ============================================================
    # 2) SERVICE-LEVEL VALIDATION
    # ============================================================
    print("===== service validation =====")
    db = SessionLocal()
    try:
        # 2a) invalid_date_range — ends < starts.
        _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=week_start,
                starts_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
                ends_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
            ),
            "invalid_date_range",
            http_status=422,
        )

        # 2b) business_date_mismatch — date doesn't match start's local date.
        _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=date(2026, 6, 2),  # Tue
                starts_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),  # Mon
                ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
            ),
            "business_date_mismatch",
            http_status=422,
        )

        # 2c) late_grace_out_of_range
        _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=week_start,
                starts_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
                ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
                late_grace_minutes=999,
            ),
            "late_grace_out_of_range",
            http_status=422,
        )

        # 2d) naive datetime
        _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=week_start,
                starts_at_local=datetime(2026, 6, 1, 9, 0),  # naive
                ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
            ),
            "naive_datetime",
            http_status=422,
        )

        # 2e) Real create, default grace.
        created = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a_id,
            business_date_=week_start,
            starts_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
        )
        db.commit()
        _entry_ids.append(created["id"])
        assert created["status"] == "draft"
        assert created["late_grace_minutes"] == staff_schedule.DEFAULT_LATE_GRACE_MINUTES
        assert created["source"] == "manual"

        # 2f) duplicate_entry — identical (user, starts, ends) rejected.
        _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=week_start,
                starts_at_local=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
                ends_at_local=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
            ),
            "duplicate_entry",
            http_status=409,
        )

        # 2g) Split-shift on the same day with different interval — allowed.
        split = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a_id,
            business_date_=week_start,
            starts_at_local=datetime(2026, 6, 1, 18, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 1, 22, 0, tzinfo=tz),
        )
        db.commit()
        _entry_ids.append(split["id"])

        # 2h) update_entry: cannot edit a published row.
        publish_test = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a_id,
            business_date_=week_start + timedelta(days=1),
            starts_at_local=datetime(2026, 6, 2, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 2, 17, 0, tzinfo=tz),
            publish=True,
        )
        db.commit()
        _entry_ids.append(publish_test["id"])
        assert publish_test["status"] == "published"
        assert publish_test["published_at"] is not None
        assert publish_test["published_by_user_id"] == admin_id

        _expect_error(
            lambda: staff_schedule.update_entry(
                db,
                entry_id=publish_test["id"],
                fields={"manager_notes": "blocked"},
            ),
            "entry_already_published",
            http_status=409,
        )

        # 2i) delete_entry: cannot delete a published row.
        _expect_error(
            lambda: staff_schedule.delete_entry(
                db, entry_id=publish_test["id"]
            ),
            "entry_already_published",
            http_status=409,
        )

        # 2j) mark_excused only flips no_show.
        _expect_error(
            lambda: staff_schedule.mark_excused(
                db,
                actor_user_id=admin_id,
                entry_id=publish_test["id"],
            ),
            "entry_not_no_show",
            http_status=409,
        )

        # Hand-seed a no_show row (Slice 2's cron is what actually writes
        # this in prod) so we can exercise the excuse path here.
        no_show = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a_id,
            business_date_=week_start + timedelta(days=2),
            starts_at_local=datetime(2026, 6, 3, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 3, 17, 0, tzinfo=tz),
            publish=True,
            manager_notes="No call no show",
        )
        db.commit()
        _entry_ids.append(no_show["id"])
        no_show_row = db.get(StaffScheduleEntry, no_show["id"])
        no_show_row.attendance_status = "no_show"
        db.commit()

        excused = staff_schedule.mark_excused(
            db,
            actor_user_id=admin_id,
            entry_id=no_show["id"],
            notes="Doctor's note received",
        )
        db.commit()
        assert excused["attendance_status"] == "excused"
        # Notes get APPENDED, not overwritten.
        assert "No call no show" in (excused["manager_notes"] or "")
        assert "Doctor's note received" in (excused["manager_notes"] or "")
    finally:
        db.close()

    # ============================================================
    # 3) TIME-OFF CONFLICT — publish path rejects
    # ============================================================
    print("===== time-off conflict =====")
    db = SessionLocal()
    try:
        # Approve a time-off for sales_a on Friday 2026-06-05.
        tor = TimeOffRequest(
            user_id=sales_a_id,
            starts_at=datetime(2026, 6, 5, 0, 0, tzinfo=tz),
            ends_at=datetime(2026, 6, 6, 0, 0, tzinfo=tz),  # all of Fri
            reason="family",
            status="approved",
            decided_by_user_id=admin_id,
            decided_at=datetime.now(timezone.utc),
        )
        db.add(tor)
        db.commit()
        db.refresh(tor)
        _tor_ids.append(tor.id)

        # Draft entry on Friday OK.
        draft_fri = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_a_id,
            business_date_=date(2026, 6, 5),
            starts_at_local=datetime(2026, 6, 5, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 5, 17, 0, tzinfo=tz),
        )
        db.commit()
        _entry_ids.append(draft_fri["id"])

        # Publishing it directly → conflict.
        exc = _expect_error(
            lambda: staff_schedule.create_entry(
                db,
                actor_user_id=admin_id,
                user_id=sales_a_id,
                business_date_=date(2026, 6, 5),
                starts_at_local=datetime(2026, 6, 5, 18, 0, tzinfo=tz),
                ends_at_local=datetime(2026, 6, 5, 22, 0, tzinfo=tz),
                publish=True,
            ),
            "time_off_conflict",
            http_status=409,
        )
        assert exc.extra.get("time_off_request_id") == tor.id

        # Slice-4: publish_week is now per-shift partial-publish. It
        # publishes the non-conflicting drafts and lists the
        # conflicting ones in `skipped`. The conflicting draft stays a
        # draft and the rest go through.
        result = staff_schedule.publish_week(
            db,
            actor_user_id=admin_id,
            week_start=week_start,
            user_ids=[sales_a_id],
        )
        db.commit()
        skipped_ids = {row["entry_id"] for row in result.get("skipped", [])}
        assert draft_fri["id"] in skipped_ids, (
            f"expected draft_fri.id in skipped, got {skipped_ids}"
        )
        # The draft must still be a draft after the partial publish.
        from database.models import StaffScheduleEntry as _SSE

        fri_row = db.get(_SSE, draft_fri["id"])
        assert fri_row.status == "draft", (
            "Slice-4 partial-publish should leave conflicting drafts alone"
        )

        # Drop the conflicting draft so the next test can publish_week cleanly.
        staff_schedule.delete_entry(db, entry_id=draft_fri["id"])
        db.commit()
        _entry_ids.remove(draft_fri["id"])
    finally:
        db.close()

    # ============================================================
    # 4) RESOLVER PRECEDENCE: published entry > override > template
    # ============================================================
    print("===== resolver precedence =====")
    db = SessionLocal()
    try:
        # Seed a template that would otherwise cover Thursday 2026-06-04.
        template = StaffShift(
            user_id=sales_b_id,
            starts_at=datetime(2026, 6, 1, 9, 0, tzinfo=tz),
            ends_at=datetime(2026, 6, 1, 17, 0, tzinfo=tz),
            working_days=[1, 2, 3, 4, 5],  # Mon-Fri
            late_grace_period_minutes=5,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        _shift_ids.append(template.id)

        thursday = date(2026, 6, 4)
        as_of = datetime(2026, 6, 4, 10, 0, tzinfo=tz)

        # Template only — resolver picks the template.
        r = shift_resolver.resolve_active_shift(
            db, user_id=sales_b_id, as_of_local=as_of
        )
        assert r is not None
        assert r.shift_id == template.id
        assert r.is_override is False
        assert r.schedule_entry_id is None
        assert r.late_grace_period_minutes == 5

        # Add an OVERRIDE: same template, 11-15 on Thursday → override wins.
        # First we need a different template the override points at to make
        # the wins observable; reuse the existing one with override range.
        override = StaffShiftOverride(
            user_id=sales_b_id,
            shift_id=template.id,
            starts_on=thursday,
            ends_on=thursday,
            reason="cover swap",
        )
        db.add(override)
        db.commit()
        db.refresh(override)
        _override_ids.append(override.id)

        r = shift_resolver.resolve_active_shift(
            db, user_id=sales_b_id, as_of_local=as_of
        )
        assert r is not None
        assert r.is_override is True
        assert r.shift_id == template.id

        # Add a PUBLISHED ENTRY for the same day — entry wins outright.
        entry = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_b_id,
            business_date_=thursday,
            starts_at_local=datetime(2026, 6, 4, 12, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 4, 20, 0, tzinfo=tz),
            source="template_clone",
            source_shift_id=template.id,
            publish=True,
        )
        db.commit()
        _entry_ids.append(entry["id"])

        r = shift_resolver.resolve_active_shift(
            db, user_id=sales_b_id, as_of_local=as_of
        )
        assert r is not None
        assert r.schedule_entry_id == entry["id"], (
            f"expected entry to win, got entry_id={r.schedule_entry_id}, "
            f"is_override={r.is_override}, shift_id={r.shift_id}"
        )
        assert r.is_override is False, "entry must not flag as override"
        # `late_grace_period_minutes` from the entry equals the template's
        # grace because the entry was cloned from the template.
        assert r.late_grace_period_minutes == 5
        # source template's start was 9-17; entry's is 12-20. Resolver
        # respects the ENTRY's interval.
        assert r.starts_at_local.hour == 12
        assert r.ends_at_local.hour == 20

        # Manual entry (no source_shift_id) → shift_id falls through to None.
        manual_day = date(2026, 6, 6)  # Saturday
        manual = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_b_id,
            business_date_=manual_day,
            starts_at_local=datetime(2026, 6, 6, 10, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 6, 6, 14, 0, tzinfo=tz),
            source="manual",
            publish=True,
        )
        db.commit()
        _entry_ids.append(manual["id"])
        r = shift_resolver.resolve_active_shift(
            db,
            user_id=sales_b_id,
            as_of_local=datetime(2026, 6, 6, 11, 0, tzinfo=tz),
        )
        assert r is not None
        assert r.schedule_entry_id == manual["id"]
        assert r.shift_id is None, (
            f"manual entry should expose shift_id=None, got {r.shift_id}"
        )
    finally:
        db.close()

    # ============================================================
    # 5) ADMIN ROUTER end-to-end
    # ============================================================
    print("===== admin router =====")

    # 5a) Scope gating — sales token rejected on every verb.
    resp = client.get(
        "/api/admin/schedule/week",
        headers=sales_hdr,
        params={"week_start": week_start.isoformat()},
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        "/api/admin/schedule/entries",
        headers=sales_hdr,
        json={
            "user_id": sales_a_id,
            "business_date": week_start.isoformat(),
            "starts_at_local": datetime(
                2026, 6, 1, 9, 0, tzinfo=tz
            ).isoformat(),
            "ends_at_local": datetime(
                2026, 6, 1, 17, 0, tzinfo=tz
            ).isoformat(),
        },
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        "/api/admin/schedule/publish",
        headers=sales_hdr,
        json={"week_start": week_start.isoformat()},
    )
    assert resp.status_code == 403, resp.text

    # 5b) GET /week returns staff + entries + time_off_blocks.
    week_resp = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={
            "week_start": week_start.isoformat(),
            "user_ids": [sales_a_id, sales_b_id],
        },
    )
    assert week_resp.status_code == 200, week_resp.text
    week_body = week_resp.json()
    assert week_body["week_start"] == week_start.isoformat()
    assert len(week_body["days"]) == 7
    staff_ids = {s["id"] for s in week_body["staff"]}
    assert {sales_a_id, sales_b_id} <= staff_ids
    # sales_a has entries from earlier seeding.
    a_entries = [e for e in week_body["entries"] if e["user_id"] == sales_a_id]
    assert len(a_entries) >= 1
    # time_off_blocks includes the Friday approval seeded earlier.
    a_blocks = [
        b for b in week_body["time_off_blocks"] if b["user_id"] == sales_a_id
    ]
    assert len(a_blocks) == 1

    # 5c) week_start not Monday → 422.
    bad_week = client.get(
        "/api/admin/schedule/week",
        headers=admin_hdr,
        params={"week_start": (week_start + timedelta(days=1)).isoformat()},
    )
    assert bad_week.status_code == 422, bad_week.text
    assert bad_week.json()["detail"]["code"] == "week_start_not_monday"

    # 5d) POST /entries (draft).
    create_resp = client.post(
        "/api/admin/schedule/entries",
        headers=admin_hdr,
        json={
            "user_id": sales_b_id,
            "business_date": (week_start + timedelta(days=1)).isoformat(),
            "starts_at_local": datetime(
                2026, 6, 2, 13, 0, tzinfo=tz
            ).isoformat(),
            "ends_at_local": datetime(
                2026, 6, 2, 18, 0, tzinfo=tz
            ).isoformat(),
            "late_grace_minutes": 20,
            "manager_notes": "afternoon coverage",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    _entry_ids.append(created["id"])
    assert created["status"] == "draft"
    assert created["late_grace_minutes"] == 20

    # 5e) PATCH /entries/{id} on a draft.
    patch_resp = client.patch(
        f"/api/admin/schedule/entries/{created['id']}",
        headers=admin_hdr,
        json={"manager_notes": "amended"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["manager_notes"] == "amended"

    # 5f) Empty PATCH → 422.
    empty_patch = client.patch(
        f"/api/admin/schedule/entries/{created['id']}",
        headers=admin_hdr,
        json={},
    )
    assert empty_patch.status_code == 422

    # 5g) POST /publish for the week (sales_b only — sales_a still has
    # the time-off conflict on Friday).
    publish_resp = client.post(
        "/api/admin/schedule/publish",
        headers=admin_hdr,
        json={
            "week_start": week_start.isoformat(),
            "user_ids": [sales_b_id],
        },
    )
    assert publish_resp.status_code == 200, publish_resp.text
    pub_body = publish_resp.json()
    assert pub_body["published_count"] >= 1
    assert created["id"] in pub_body["entry_ids"]

    # 5h) DELETE on a now-published row → 409.
    del_resp = client.delete(
        f"/api/admin/schedule/entries/{created['id']}",
        headers=admin_hdr,
    )
    assert del_resp.status_code == 409, del_resp.text
    assert del_resp.json()["detail"]["code"] == "entry_already_published"

    # 5i) POST /notes works on a published row.
    notes_resp = client.post(
        f"/api/admin/schedule/entries/{created['id']}/notes",
        headers=admin_hdr,
        json={"notes": "verified attendance"},
    )
    assert notes_resp.status_code == 200, notes_resp.text
    assert notes_resp.json()["manager_notes"] == "verified attendance"

    # 5j) POST /publish — full week for sales_a. Slice-4 changed the
    # semantics from wholesale-abort to partial-publish: a conflict
    # surfaces in `skipped` instead of erroring, the non-conflicting
    # drafts still go through.
    publish_resp = client.post(
        "/api/admin/schedule/publish",
        headers=admin_hdr,
        json={
            "week_start": week_start.isoformat(),
            "user_ids": [sales_a_id],
        },
    )
    assert publish_resp.status_code == 200, publish_resp.text
    body = publish_resp.json()
    assert "skipped" in body, (
        "Slice-4 partial-publish response must always include skipped[]"
    )

    # ============================================================
    # 6) SINGLE-ENTRY PUBLISH (POST /entries/{id}/publish)
    # ============================================================
    # Companion to publish_week. Same time-off lock semantics applied
    # per-entry so the grid's detail dialog can publish a saved
    # draft without forcing a whole-week publish.
    print("===== /entries/{id}/publish =====")
    sales_h_id = _make_user(role="sales")
    publish_week_start = date(2026, 8, 3)  # Monday
    assert publish_week_start.isoweekday() == 1

    db = SessionLocal()
    h_draft_id: int
    h_conflicting_draft_id: int
    h_tor_id: int
    try:
        h_draft = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_h_id,
            business_date_=publish_week_start,
            starts_at_local=datetime(2026, 8, 3, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 8, 3, 17, 0, tzinfo=tz),
        )
        h_conflicting_draft = staff_schedule.create_entry(
            db,
            actor_user_id=admin_id,
            user_id=sales_h_id,
            business_date_=publish_week_start + timedelta(days=2),
            starts_at_local=datetime(2026, 8, 5, 9, 0, tzinfo=tz),
            ends_at_local=datetime(2026, 8, 5, 17, 0, tzinfo=tz),
        )
        # Approve a TOR that overlaps the second draft.
        tor = TimeOffRequest(
            user_id=sales_h_id,
            starts_at=datetime(2026, 8, 5, 0, 0, tzinfo=tz),
            ends_at=datetime(2026, 8, 6, 0, 0, tzinfo=tz),
            reason="appointment",
            status="approved",
            decided_by_user_id=admin_id,
            decided_at=datetime.now(timezone.utc),
        )
        db.add(tor)
        db.commit()
        db.refresh(tor)
        _entry_ids.extend([h_draft["id"], h_conflicting_draft["id"]])
        _tor_ids.append(tor.id)
        h_draft_id = h_draft["id"]
        h_conflicting_draft_id = h_conflicting_draft["id"]
        h_tor_id = tor.id
    finally:
        db.close()

    # 6a) sales token rejected.
    resp = client.post(
        f"/api/admin/schedule/entries/{h_draft_id}/publish",
        headers=sales_hdr,
    )
    assert resp.status_code == 403, resp.text

    # 6b) Bogus id → 404.
    resp = client.post(
        "/api/admin/schedule/entries/999999999/publish",
        headers=admin_hdr,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "entry_not_found"

    # 6c) Happy path — draft becomes published.
    resp = client.post(
        f"/api/admin/schedule/entries/{h_draft_id}/publish",
        headers=admin_hdr,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["status"] == "published"
    assert payload["published_at"] is not None
    assert payload["published_by_user_id"] == admin_id

    # Re-loading via the week endpoint confirms the row is published
    # (and the updated_at moved).
    db = SessionLocal()
    try:
        from database.models import StaffScheduleEntry as _SSE

        row = db.get(_SSE, h_draft_id)
        assert row.status == "published"
        assert row.published_at is not None
        assert row.published_by_user_id == admin_id
    finally:
        db.close()

    # 6d) Re-publishing the now-published entry → 409 entry_already_published.
    resp = client.post(
        f"/api/admin/schedule/entries/{h_draft_id}/publish",
        headers=admin_hdr,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_already_published"

    # 6e) Conflicting draft → 409 time_off_conflict, with the TOR id
    # surfaced in extra.
    resp = client.post(
        f"/api/admin/schedule/entries/{h_conflicting_draft_id}/publish",
        headers=admin_hdr,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "time_off_conflict"
    assert detail.get("time_off_request_id") == h_tor_id

    # The conflicting draft must remain a draft after the rejection.
    db = SessionLocal()
    try:
        from database.models import StaffScheduleEntry as _SSE

        row = db.get(_SSE, h_conflicting_draft_id)
        assert row.status == "draft", (
            "rejected single-entry publish must leave draft unchanged"
        )
    finally:
        db.close()

    print("phase10_schedule smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
