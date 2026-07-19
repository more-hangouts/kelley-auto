"""Open-shift pickup conflict smoke for Scheduling Phase 3.

  1. Approving a pickup hard-blocks when the claimant can't work the post:
     overlapping published shift / approved time off / recurring
     unavailability -> candidate_conflict.
  2. A post that isn't open (cancelled / claimed / expired) can't be
     claimed -> post_not_open.
"""

import os
import sys
import uuid
from datetime import date, datetime, timezone
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
from database.models import TimeOffRequest, User  # noqa: E402
from services import recurring_availability, staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_admin_id = 0


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p0sched-{suffix}",
            email=f"{role}-p0sched-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P0Sched {role.title()} {suffix}",
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


def _post_open(bdate: str, tz, start_h=9, end_h=17) -> int:
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    y, m, d = [int(x) for x in bdate.split("-")]
    resp = client.post(
        "/api/admin/schedule/open-shifts",
        headers=admin_hdr,
        json={
            "business_date": bdate,
            "starts_at_local": datetime(y, m, d, start_h, 0, tzinfo=tz).isoformat(),
            "ends_at_local": datetime(y, m, d, end_h, 0, tzinfo=tz).isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _claim(user_id: int, post_id: int):
    hdr = {"Authorization": f"Bearer {_token(user_id, sales=True)}"}
    return client.post(
        f"/api/sales/schedule/open-shifts/{post_id}/claim", headers=hdr
    )


def _publish(user_id, bdate, tz, start_h, end_h):
    y, m, d = [int(x) for x in bdate.split("-")]
    db = SessionLocal()
    try:
        staff_schedule.create_entry(
            db,
            actor_user_id=_admin_id,
            user_id=user_id,
            business_date_=date(y, m, d),
            starts_at_local=datetime(y, m, d, start_h, 0, tzinfo=tz),
            ends_at_local=datetime(y, m, d, end_h, 0, tzinfo=tz),
            publish=True,
        )
        db.commit()
    finally:
        db.close()


def _approve_expect_conflict(req_id: int, conflict_type: str) -> None:
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{req_id}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "candidate_conflict", detail
    types = {c["type"] for c in detail.get("conflicts", [])}
    assert conflict_type in types, (detail, conflict_type)


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            for stmt in (
                "DELETE FROM staff_shift_request_events WHERE request_id IN "
                "(SELECT id FROM staff_shift_requests WHERE requester_user_id = ANY(:u))",
                "DELETE FROM staff_shift_requests WHERE requester_user_id = ANY(:u)",
                "DELETE FROM open_shift_posts WHERE created_by_user_id = ANY(:u) OR claimed_by_user_id = ANY(:u)",
                "DELETE FROM notification_jobs WHERE recipient_user_id = ANY(:u)",
                "DELETE FROM staff_notification_events WHERE actor_user_id = ANY(:u)",
                "DELETE FROM time_off_decision_events WHERE request_id IN (SELECT id FROM time_off_requests WHERE user_id = ANY(:u))",
                "DELETE FROM time_off_requests WHERE user_id = ANY(:u)",
                "DELETE FROM recurring_unavailability WHERE user_id = ANY(:u)",
                "DELETE FROM staff_schedule_entries WHERE user_id = ANY(:u)",
                "DELETE FROM users WHERE id = ANY(:u)",
            ):
                db.execute(sql_text(stmt), {"u": _user_ids})
        db.commit()
    finally:
        db.close()


def main() -> None:
    global _admin_id
    tz = ZoneInfo(APP_TIMEZONE)
    _admin_id = _make_user(role="admin")

    # --- overlapping published shift ---
    print("===== overlap blocks pickup approval =====")
    c1 = _make_user(role="sales")
    p1 = _post_open("2026-11-09", tz)
    _publish(c1, "2026-11-09", tz, 12, 20)
    r = _claim(c1, p1)
    assert r.status_code == 200, r.text
    _approve_expect_conflict(r.json()["id"], "published_overlap")

    # --- approved time off ---
    print("===== time off blocks pickup approval =====")
    c2 = _make_user(role="sales")
    p2 = _post_open("2026-11-16", tz)
    db = SessionLocal()
    try:
        db.add(
            TimeOffRequest(
                user_id=c2,
                starts_at=datetime(2026, 11, 16, 0, 0, tzinfo=tz).astimezone(
                    timezone.utc
                ),
                ends_at=datetime(2026, 11, 17, 0, 0, tzinfo=tz).astimezone(
                    timezone.utc
                ),
                reason="p0sched smoke",
                status="approved",
                decided_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()
    r = _claim(c2, p2)
    assert r.status_code == 200, r.text
    _approve_expect_conflict(r.json()["id"], "approved_time_off")

    # --- recurring unavailability ---
    print("===== recurring unavailability blocks pickup approval =====")
    c3 = _make_user(role="sales")
    bdate3 = "2026-11-23"
    p3 = _post_open(bdate3, tz)
    db = SessionLocal()
    try:
        recurring_availability.create_block(
            db,
            user_id=c3,
            weekday=date(2026, 11, 23).isoweekday(),
            start_time_local="08:00",
            end_time_local="18:00",
            effective_from=date(2026, 10, 1),
            reason="p0sched smoke",
        )
        db.commit()
    finally:
        db.close()
    r = _claim(c3, p3)
    assert r.status_code == 200, r.text
    _approve_expect_conflict(r.json()["id"], "recurring_unavailability")

    # --- cancelled post can't be claimed ---
    print("===== cancelled post can't be claimed =====")
    c4 = _make_user(role="sales")
    p4 = _post_open("2026-11-30", tz)
    admin_hdr = {"Authorization": f"Bearer {_token(_admin_id, sales=False)}"}
    resp = client.post(
        f"/api/admin/schedule/open-shifts/{p4}/cancel", headers=admin_hdr
    )
    assert resp.status_code == 200, resp.text
    r = _claim(c4, p4)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "post_not_open"

    # --- claimed post can't be claimed again ---
    print("===== claimed post can't be claimed again =====")
    c5 = _make_user(role="sales")
    c6 = _make_user(role="sales")
    p5 = _post_open("2026-12-07", tz)
    r = _claim(c5, p5)
    assert r.status_code == 200, r.text
    resp = client.post(
        f"/api/admin/schedule/shift-requests/{r.json()['id']}/decide",
        headers=admin_hdr,
        json={"status": "approved"},
    )
    assert resp.status_code == 200, resp.text
    r2 = _claim(c6, p5)
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["code"] == "post_not_open"

    # --- expired post can't be claimed ---
    print("===== expired post can't be claimed =====")
    c7 = _make_user(role="sales")
    p6 = _post_open("2026-12-14", tz)
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE open_shift_posts SET status = 'expired' WHERE id = :id"
            ),
            {"id": p6},
        )
        db.commit()
    finally:
        db.close()
    r = _claim(c7, p6)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "post_not_open"

    print("open_shift_pickup_conflicts smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
