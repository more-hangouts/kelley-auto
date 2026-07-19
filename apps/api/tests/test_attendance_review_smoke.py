"""Smoke tests for Phase 7 Slice 2B-2 (owner attendance review).

Covers the bounded read path, the staff-totals derivation, the
confirm/adjust/void writes (each with the matching audit row), and
the correction-request workflow end-to-end. Also probes the user's
explicit Slice 2B-2 directives:

  - dates filter via business_date (so a punch at 11:30pm local on a
    Saturday counts toward Saturday, not the UTC-shifted Sunday)
  - API responses expose both UTC and business-local timestamps
  - reads are bounded (no unbounded "all punches" endpoint)
  - owner adjustments are append-only/audited; no DELETE on punches
  - correction approve/deny is its own action separate from manual
    adjust

Run order: any time after `test_clock_in_smoke.py` lays down basics.
The smoke seeds its own users + locations + punches and cleans up
after itself.
"""

import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
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
    StaffLocation,
    StaffPunch,
    StaffPunchAuditEvent,
    StaffPunchCorrectionRequest,
    User,
)

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []
_punch_ids: list[int] = []
_correction_ids: list[int] = []


PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000
PROBE_RADIUS_M = 100


def _make_user(*, role: str, suffix: str | None = None) -> int:
    db = SessionLocal()
    try:
        s = suffix or uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p7s2b2-{s}",
            email=f"{role}-p7s2b2-{s}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P7S2B2 {role.title()} {s}",
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


def _token_for(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _seed_location() -> int:
    db = SessionLocal()
    try:
        loc = StaffLocation(
            name="P7S2B2 Probe",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=PROBE_RADIUS_M,
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
    local_punch_at: datetime,
    location_id: int | None = None,
    status: str = "unscheduled",
    auto_closed: bool = False,
    auto_close_reason: str | None = None,
    hours_confirmation_status: str = "not_required",
) -> int:
    """Insert a punch row at a known business-local wall time, with
    the option to mark it auto-closed/needs-review for the queue
    tests. Returns the punch id."""
    db = SessionLocal()
    try:
        utc = local_punch_at.astimezone(timezone.utc)
        p = StaffPunch(
            user_id=user_id,
            direction=direction,
            punched_at=utc,
            status=status,
            location_id=location_id,
            auto_closed=auto_closed,
            auto_close_reason=auto_close_reason,
            hours_confirmation_status=hours_confirmation_status,
            client_latitude=PROBE_LAT,
            client_longitude=PROBE_LNG,
            distance_to_location_m=12.0,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        _punch_ids.append(p.id)
        return p.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _correction_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_correction_requests "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _correction_ids},
            )
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_correction_requests "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE actor_user_id = ANY(:uids) "
                    "   OR punch_id IN (SELECT id FROM staff_punches "
                    "                   WHERE user_id = ANY(:uids))"
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
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    today_local = datetime.now(tz).date()

    # ---- Seed users + tokens. ----
    sales_id_a = _make_user(role="sales", suffix="aaaa")
    sales_id_b = _make_user(role="sales", suffix="bbbb")
    admin_id = _make_user(role="admin")
    sales_a_headers = {
        "Authorization": f"Bearer {_token_for(sales_id_a, sales=True)}"
    }
    sales_b_headers = {
        "Authorization": f"Bearer {_token_for(sales_id_b, sales=True)}"
    }
    admin_headers = {
        "Authorization": f"Bearer {_token_for(admin_id, sales=False)}"
    }

    location_id = _seed_location()

    # Stylist A worked yesterday 9-5 (one paired session).
    yesterday_local = today_local - timedelta(days=1)
    yest_in = datetime.combine(yesterday_local, time(9, 0), tzinfo=tz)
    yest_out = datetime.combine(yesterday_local, time(17, 0), tzinfo=tz)
    p_a_in = _seed_punch(
        user_id=sales_id_a,
        direction="in",
        local_punch_at=yest_in,
        location_id=location_id,
        status="recorded",
    )
    p_a_out = _seed_punch(
        user_id=sales_id_a,
        direction="out",
        local_punch_at=yest_out,
        location_id=location_id,
        status="recorded",
    )

    # Stylist A also has a punch at 11:30pm local — the business_date
    # boundary check. In UTC this falls on the next day, but the
    # service should attribute it to the local Saturday.
    late_local = datetime.combine(yesterday_local, time(23, 30), tzinfo=tz)
    p_a_late = _seed_punch(
        user_id=sales_id_a,
        direction="in",
        local_punch_at=late_local,
        location_id=location_id,
        status="recorded",
    )

    # Stylist B has an auto-closed open session from yesterday — needs
    # owner review.
    p_b_late_in = _seed_punch(
        user_id=sales_id_b,
        direction="in",
        local_punch_at=datetime.combine(yesterday_local, time(10, 0), tzinfo=tz),
        location_id=location_id,
        status="recorded",
    )
    p_b_auto_out = _seed_punch(
        user_id=sales_id_b,
        direction="out",
        local_punch_at=datetime.combine(yesterday_local, time(22, 0), tzinfo=tz),
        location_id=location_id,
        status="unscheduled",
        auto_closed=True,
        auto_close_reason="past_date",
        hours_confirmation_status="needs_review",
    )

    # ---- 1) Sales token gets 403 on the admin attendance routes. ----
    resp = client.get(
        "/api/admin/attendance/punches", headers=sales_a_headers
    )
    assert resp.status_code == 403, resp.text

    # ---- 2) Bounded reads: default range (today) must NOT include
    #    yesterday's punches. ----
    resp = client.get(
        "/api/admin/attendance/punches", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    today_iso = today_local.isoformat()
    assert body["from_date"] == today_iso
    assert body["to_date"] == today_iso
    today_seeded_ids = {p_a_in, p_a_out, p_a_late, p_b_late_in, p_b_auto_out}
    returned_ids = {p["id"] for p in body["punches"]}
    assert returned_ids.isdisjoint(today_seeded_ids), (
        f"today range should not include yesterday's punches; got {returned_ids}"
    )

    # ---- 3) Explicit from/to range covering yesterday → all 5 punches. ----
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            "to_date": yesterday_local.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    returned_ids = {p["id"] for p in body["punches"]}
    assert today_seeded_ids.issubset(returned_ids), (
        f"all yesterday punches expected, got {returned_ids}"
    )

    # ---- 4) business_date attribution: the 23:30 local punch should
    #    be tagged business_date == yesterday_local even though its
    #    UTC date is the next calendar day. ----
    late_row = next(p for p in body["punches"] if p["id"] == p_a_late)
    assert late_row["business_date"] == yesterday_local.isoformat(), (
        f"expected late punch to attribute to {yesterday_local}, got "
        f"{late_row['business_date']}"
    )
    # Both UTC and local are exposed.
    assert late_row["punched_at"].endswith("+00:00") or late_row[
        "punched_at"
    ].endswith("Z"), late_row["punched_at"]
    assert late_row["punched_at_local"] != late_row["punched_at"]

    # ---- 5) Review queue filter shows only the auto-closed/needs-
    #    review punch. ----
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            "to_date": yesterday_local.isoformat(),
            "review_queue_only": "true",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    queue_ids = {p["id"] for p in body["punches"]}
    assert p_b_auto_out in queue_ids, queue_ids
    # The clean recorded punches must NOT show up in the review queue.
    assert p_a_in not in queue_ids
    assert p_a_out not in queue_ids
    assert body["review_queue_count"] == len(queue_ids)

    # ---- 6) Staff filter: scoping to sales_id_a yields only A's
    #    punches even with the wide range. ----
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            "to_date": yesterday_local.isoformat(),
            "staff_user_id": sales_id_a,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert all(p["user_id"] == sales_id_a for p in body["punches"])

    # ---- 7) Totals: Stylist A's yesterday total = 8 hours from the
    #    9-5 paired session; the late 23:30 unmatched in does NOT add
    #    hours since there's no matching out. ----
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            "to_date": yesterday_local.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    a_total = next(t for t in body["totals"] if t["user_id"] == sales_id_a)
    assert abs(a_total["total_hours"] - 8.0) < 0.01, a_total["total_hours"]
    # The 8 hours land on yesterday's local date.
    assert any(
        d["business_date"] == yesterday_local.isoformat()
        and abs(d["hours"] - 8.0) < 0.01
        for d in a_total["by_day"]
    ), a_total["by_day"]

    # ---- 8) current_week range_key includes yesterday + today
    #    (Monday-anchored). ----
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={"range_key": "current_week"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_date"] <= yesterday_local.isoformat()
    assert body["to_date"] >= today_iso

    # ---- 9) Confirm hours on the auto-closed B punch. ----
    resp = client.post(
        f"/api/admin/attendance/punches/{p_b_auto_out}/confirm",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hours_confirmation_status"] == "confirmed"
    assert body["hours_confirmed_by_user_id"] == admin_id

    # Audit row was written.
    db = SessionLocal()
    try:
        evs = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.punch_id == p_b_auto_out
                )
            )
            .scalars()
            .all()
        )
        assert any(
            e.action == "punch.hours_confirmed" and e.actor_user_id == admin_id
            for e in evs
        ), [e.action for e in evs]
    finally:
        db.close()

    # Idempotent: re-confirming returns 200 with the same state.
    resp = client.post(
        f"/api/admin/attendance/punches/{p_b_auto_out}/confirm",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hours_confirmation_status"] == "confirmed"

    # ---- 10) Manual adjust on stylist A's 9am clock-in: bump it 30
    #    minutes earlier with a reason. ----
    new_in = (yest_in - timedelta(minutes=30)).isoformat()
    resp = client.post(
        f"/api/admin/attendance/punches/{p_a_in}/adjust",
        headers=admin_headers,
        json={"new_punched_at": new_in, "reason": "Camera footage shows 8:30 entry"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "manual_adjusted"
    assert body["hours_confirmation_status"] == "adjusted"

    # Old timestamp captured in the audit row.
    db = SessionLocal()
    try:
        evs = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.punch_id == p_a_in
                )
            )
            .scalars()
            .all()
        )
        adj = [e for e in evs if e.action == "punch.manual_adjusted"]
        assert len(adj) == 1, [e.action for e in evs]
        assert adj[0].old_values.get("status") == "recorded"
        assert "punched_at" in adj[0].old_values
    finally:
        db.close()

    # Reason required.
    resp = client.post(
        f"/api/admin/attendance/punches/{p_a_in}/adjust",
        headers=admin_headers,
        json={"new_punched_at": new_in, "reason": "   "},
    )
    assert resp.status_code in (400, 422), resp.text

    # ---- 11) Void a punch (the late 23:30 unpaired in). Audit row
    #    records the prior status. ----
    resp = client.post(
        f"/api/admin/attendance/punches/{p_a_late}/void",
        headers=admin_headers,
        json={"reason": "Stylist confirmed they didn't actually clock in"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "void"

    # Re-totaling should NOT include the voided punch (and the original
    # 9-5 pair stays intact even after the in was adjusted to 8:30).
    resp = client.get(
        "/api/admin/attendance/totals",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            "to_date": yesterday_local.isoformat(),
            "staff_user_id": sales_id_a,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    a_total = next(t for t in body["totals"] if t["user_id"] == sales_id_a)
    # 8.5 hours after the 30-minute adjustment, 0 hours for the voided
    # late punch.
    assert abs(a_total["total_hours"] - 8.5) < 0.01, a_total["total_hours"]

    # ---- 12) Stylist files a correction request against their own
    #    auto-closed B punch (proposing 6:15pm instead of 10pm). ----
    proposed_out = datetime.combine(
        yesterday_local, time(18, 15), tzinfo=tz
    ).astimezone(timezone.utc)
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_b_headers,
        json={
            "punch_id": p_b_auto_out,
            "requested_check_out_at": proposed_out.isoformat(),
            "reason": "I left at 6:15. Auto-close set 10pm by mistake.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    correction_id = body["id"]
    _correction_ids.append(correction_id)

    # No proposed times → 422.
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_b_headers,
        json={"reason": "no times"},
    )
    assert resp.status_code in (400, 422), resp.text

    # Cannot file a request against another stylist's punch.
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_a_headers,
        json={
            "punch_id": p_b_auto_out,
            "requested_check_out_at": proposed_out.isoformat(),
            "reason": "trying to game stylist B's timesheet",
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "punch_not_yours"

    # ---- 13) Owner sees the pending request in the queue. ----
    resp = client.get(
        "/api/admin/attendance/correction-requests", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pending = [
        r for r in body["correction_requests"] if r["id"] == correction_id
    ]
    assert len(pending) == 1
    # Both UTC and local timestamps surface.
    assert pending[0]["requested_check_out_at"] is not None
    assert pending[0]["requested_check_out_at_local"] is not None

    # Sales token is rejected from the admin queue.
    resp = client.get(
        "/api/admin/attendance/correction-requests",
        headers=sales_b_headers,
    )
    assert resp.status_code == 403, resp.text

    # ---- 14) Owner approves the correction. The linked punch picks
    #    up the proposed time, status flips to manual_adjusted, and
    #    an audit row records the prior values. ----
    resp = client.post(
        f"/api/admin/attendance/correction-requests/{correction_id}/decide",
        headers=admin_headers,
        json={"status": "approved", "decision_notes": "Confirmed by camera"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["decided_by_user_id"] == admin_id

    db = SessionLocal()
    try:
        punch = db.get(StaffPunch, p_b_auto_out)
        # Punched_at moved to the proposed 6:15pm.
        assert punch.punched_at == proposed_out, (
            punch.punched_at,
            proposed_out,
        )
        assert punch.status == "manual_adjusted"
        # Audit row landed.
        evs = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.punch_id == p_b_auto_out
                )
            )
            .scalars()
            .all()
        )
        assert any(
            e.action == "punch.correction_applied" for e in evs
        ), [e.action for e in evs]
    finally:
        db.close()

    # Re-deciding the same request → 409 (no double-application).
    resp = client.post(
        f"/api/admin/attendance/correction-requests/{correction_id}/decide",
        headers=admin_headers,
        json={"status": "denied"},
    )
    assert resp.status_code == 409, resp.text

    # ---- 15) A second correction request denied (no punch change).
    #    Verifies denial is purely record-only. ----
    proposed_in = datetime.combine(
        yesterday_local, time(9, 30), tzinfo=tz
    ).astimezone(timezone.utc)
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_b_headers,
        json={
            "punch_id": p_b_late_in,
            "requested_check_in_at": proposed_in.isoformat(),
            "reason": "Actually arrived at 9:30",
        },
    )
    assert resp.status_code == 200, resp.text
    second_id = resp.json()["id"]
    _correction_ids.append(second_id)

    resp = client.post(
        f"/api/admin/attendance/correction-requests/{second_id}/decide",
        headers=admin_headers,
        json={"status": "denied", "decision_notes": "Not enough evidence"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "denied"

    # The denied request must NOT have moved the punch.
    db = SessionLocal()
    try:
        punch = db.get(StaffPunch, p_b_late_in)
        assert punch.punched_at != proposed_in
        assert punch.status == "recorded"
    finally:
        db.close()

    # ---- 16) Stylist confirms one of their own punches via the
    #    sales-side route. Mark another punch as needs-review for
    #    this. ----
    extra_review = _seed_punch(
        user_id=sales_id_a,
        direction="out",
        local_punch_at=datetime.combine(
            yesterday_local, time(20, 0), tzinfo=tz
        ),
        location_id=location_id,
        status="unscheduled",
        auto_closed=True,
        auto_close_reason="max_time_reached",
        hours_confirmation_status="needs_review",
    )

    # Stylist B cannot confirm A's punch.
    resp = client.post(
        f"/api/sales/attendance/punches/{extra_review}/confirm",
        headers=sales_b_headers,
        json={},
    )
    assert resp.status_code == 403, resp.text

    # Stylist A confirms their own.
    resp = client.post(
        f"/api/sales/attendance/punches/{extra_review}/confirm",
        headers=sales_a_headers,
        json={},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hours_confirmation_status"] == "confirmed"

    # ---- 17) Stylist cancels a pending correction. ----
    proposed_local_cancel = datetime.combine(
        yesterday_local, time(11, 0), tzinfo=tz
    ).astimezone(timezone.utc)
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_a_headers,
        json={
            "requested_check_in_at": proposed_local_cancel.isoformat(),
            "reason": "Mind changed",
        },
    )
    assert resp.status_code == 200, resp.text
    cancel_id = resp.json()["id"]
    _correction_ids.append(cancel_id)

    resp = client.post(
        f"/api/sales/attendance/correction-requests/{cancel_id}/cancel",
        headers=sales_a_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"

    # Cannot cancel someone else's.
    resp = client.post(
        "/api/sales/attendance/correction-requests",
        headers=sales_b_headers,
        json={
            "requested_check_in_at": proposed_local_cancel.isoformat(),
            "reason": "yet another",
        },
    )
    assert resp.status_code == 200, resp.text
    third_id = resp.json()["id"]
    _correction_ids.append(third_id)

    resp = client.post(
        f"/api/sales/attendance/correction-requests/{third_id}/cancel",
        headers=sales_a_headers,
    )
    assert resp.status_code == 403, resp.text

    # ---- 18) No DELETE route on punches: the spec is append-only.
    #    FastAPI returns 404 for paths with no matching method (no
    #    DELETE handler exists at all) and 405 when DELETE is explicitly
    #    excluded; either is fine — the point is "not deletable". ----
    resp = client.delete(
        f"/api/admin/attendance/punches/{p_b_auto_out}",
        headers=admin_headers,
    )
    assert resp.status_code in (404, 405), resp.text

    # ---- 19) Bad date range → 422. ----
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={
            "from_date": yesterday_local.isoformat(),
            # to_date < from_date
            "to_date": (yesterday_local - timedelta(days=2)).isoformat(),
        },
    )
    assert resp.status_code == 422, resp.text

    # Incomplete range (only one of from/to).
    resp = client.get(
        "/api/admin/attendance/punches",
        headers=admin_headers,
        params={"from_date": yesterday_local.isoformat()},
    )
    assert resp.status_code == 422, resp.text

    # ---- 20) Admin clock-out of a currently-open session. Seed a
    #    fresh stylist with an open in-punch (today, no matching out) so
    #    they show up in /open-sessions. ----
    open_in = _seed_punch(
        user_id=sales_id_a,
        direction="in",
        local_punch_at=datetime.combine(today_local, time(9, 0), tzinfo=tz),
        location_id=location_id,
        status="unscheduled",
    )
    # NB: sales_id_a's prior punches are all earlier than this new in,
    # so A's current state is now "in" against `open_in`.

    # /open-sessions lists everyone currently clocked in (global, not
    # date-bounded). Assert our seeded open user is present — never a
    # global count, since the shared dev DB may hold other real sessions.
    resp = client.get(
        "/api/admin/attendance/open-sessions", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    mine = [s for s in body["open_sessions"] if s["user_id"] == sales_id_a]
    assert len(mine) == 1, mine
    assert mine[0]["in_punch_id"] == open_in
    assert mine[0]["hours_open"] >= 0

    # Sales token is rejected from the admin clock-out surfaces.
    resp = client.get(
        "/api/admin/attendance/open-sessions", headers=sales_a_headers
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        f"/api/admin/attendance/punches/{open_in}/clock-out",
        headers=sales_a_headers,
        json={},
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        "/api/admin/attendance/clock-everyone-out",
        headers=sales_a_headers,
        json={},
    )
    assert resp.status_code == 403, resp.text

    # Clocking out on a punch that isn't an open in-punch → 409. p_a_out
    # is an out-direction row.
    resp = client.post(
        f"/api/admin/attendance/punches/{p_a_out}/clock-out",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "not_an_in_punch"

    # Owner clocks the stylist out. Returns the new out-punch flagged
    # for review.
    resp = client.post(
        f"/api/admin/attendance/punches/{open_in}/clock-out",
        headers=admin_headers,
        json={"reason": "End of day, stylist forgot to clock out"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["direction"] == "out"
    assert body["hours_confirmation_status"] == "needs_review"
    out_punch_id = body["id"]
    _punch_ids.append(out_punch_id)

    # The session is now closed → no longer in /open-sessions, and an
    # owner audit row links the close.
    resp = client.get(
        "/api/admin/attendance/open-sessions", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    still_open = [
        s for s in resp.json()["open_sessions"] if s["user_id"] == sales_id_a
    ]
    assert still_open == [], still_open

    db = SessionLocal()
    try:
        evs = (
            db.execute(
                select(StaffPunchAuditEvent).where(
                    StaffPunchAuditEvent.punch_id == out_punch_id
                )
            )
            .scalars()
            .all()
        )
        assert any(
            e.action == "punch.admin_clock_out" and e.actor_user_id == admin_id
            for e in evs
        ), [e.action for e in evs]
    finally:
        db.close()

    # Re-clocking-out the same in-punch → 409 (already closed).
    resp = client.post(
        f"/api/admin/attendance/punches/{open_in}/clock-out",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "not_currently_open"

    print("attendance_review smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
