"""Create/cancel smoke for Scheduling Phase 1 (HTTP end-to-end).

Exercises the sales-side shift-request endpoints under
`/api/sales/schedule/shift-requests`:

  1. Staff can create cover / drop / swap requests against their own
     future published shift; each writes a 'requested' audit event.
  2. Staff cannot request against a coworker's shift (entry_not_yours).
  3. A draft (unpublished) source is rejected (entry_not_published).
  4. A started/past shift is rejected (entry_started).
  5. Staff can cancel their own pending request; a second cancel on the
     now-terminal request returns request_terminal.
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
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import StaffScheduleEntry, User  # noqa: E402
from services import staff_schedule  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []


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


def _sales_token(user_id: int) -> str:
    db = SessionLocal()
    try:
        return create_sales_token(db.get(User, user_id))
    finally:
        db.close()


def _published(
    actor_id: int, user_id: int, bdate: date, starts: datetime, ends: datetime
) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_id,
            user_id=user_id,
            business_date_=bdate,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=True,
        )
        db.commit()
        return d["id"]
    finally:
        db.close()


def _draft(
    actor_id: int, user_id: int, bdate: date, starts: datetime, ends: datetime
) -> int:
    db = SessionLocal()
    try:
        d = staff_schedule.create_entry(
            db,
            actor_user_id=actor_id,
            user_id=user_id,
            business_date_=bdate,
            starts_at_local=starts,
            ends_at_local=ends,
            publish=False,
        )
        db.commit()
        return d["id"]
    finally:
        db.close()


def _published_in_past(user_id: int, bdate: date, starts: datetime,
                       ends: datetime) -> int:
    """Insert a published entry whose start is in the past. We bypass the
    service here because create_entry rejects past business dates; the
    schema allows it and that's what we need to exercise entry_started."""
    db = SessionLocal()
    try:
        e = StaffScheduleEntry(
            user_id=user_id,
            business_date=bdate,
            starts_at_local=starts,
            ends_at_local=ends,
            status="published",
            published_at=datetime.now(timezone.utc),
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        return e.id
    finally:
        db.close()


def _event_count(request_id: int, action: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_shift_request_events "
                "WHERE request_id = :rid AND action = :a"
            ),
            {"rid": request_id, "a": action},
        ).scalar()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_request_events "
                    "WHERE request_id IN ("
                    "SELECT id FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids)"
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
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(APP_TIMEZONE)
    admin_id = _make_user(role="admin")
    me_id = _make_user(role="sales")
    coworker_id = _make_user(role="sales")
    me_hdr = {"Authorization": f"Bearer {_sales_token(me_id)}"}

    week = date(2026, 9, 7)  # Monday, comfortably future
    mine_mon = _published(
        admin_id, me_id, week,
        datetime(2026, 9, 7, 9, 0, tzinfo=tz),
        datetime(2026, 9, 7, 17, 0, tzinfo=tz),
    )
    mine_tue = _published(
        admin_id, me_id, week + timedelta(days=1),
        datetime(2026, 9, 8, 9, 0, tzinfo=tz),
        datetime(2026, 9, 8, 17, 0, tzinfo=tz),
    )
    mine_wed = _published(
        admin_id, me_id, week + timedelta(days=2),
        datetime(2026, 9, 9, 9, 0, tzinfo=tz),
        datetime(2026, 9, 9, 17, 0, tzinfo=tz),
    )
    coworker_thu = _published(
        admin_id, coworker_id, week + timedelta(days=3),
        datetime(2026, 9, 10, 9, 0, tzinfo=tz),
        datetime(2026, 9, 10, 17, 0, tzinfo=tz),
    )
    mine_draft = _draft(
        admin_id, me_id, week + timedelta(days=4),
        datetime(2026, 9, 11, 9, 0, tzinfo=tz),
        datetime(2026, 9, 11, 17, 0, tzinfo=tz),
    )

    # ============================================================
    # 1) Create cover / drop / swap on own future published shifts
    # ============================================================
    print("===== create cover/drop/swap =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "cover", "source_entry_id": mine_mon,
              "reason": "dentist"},
    )
    assert resp.status_code == 200, resp.text
    cover = resp.json()
    assert cover["status"] == "pending"
    assert cover["request_type"] == "cover"
    assert cover["source_entry_id"] == mine_mon
    assert cover["source_entry"]["id"] == mine_mon
    assert _event_count(cover["id"], "requested") == 1

    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "drop", "source_entry_id": mine_tue},
    )
    assert resp.status_code == 200, resp.text
    drop = resp.json()
    assert drop["request_type"] == "drop"
    assert drop["target_entry_id"] is None

    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "swap", "source_entry_id": mine_wed,
              "target_entry_id": coworker_thu},
    )
    assert resp.status_code == 200, resp.text
    swap = resp.json()
    assert swap["request_type"] == "swap"
    assert swap["target_entry_id"] == coworker_thu
    # Swap auto-resolves the candidate to the target shift's owner.
    assert swap["candidate_user_id"] == coworker_id

    # ============================================================
    # 2) Cannot request against a coworker's shift
    # ============================================================
    print("===== coworker shift rejected =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "cover", "source_entry_id": coworker_thu},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "entry_not_yours"

    # ============================================================
    # 3) Draft source rejected
    # ============================================================
    print("===== draft source rejected =====")
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "cover", "source_entry_id": mine_draft},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_not_published"

    # ============================================================
    # 4) Started/past shift rejected
    # ============================================================
    print("===== started shift rejected =====")
    past_id = _published_in_past(
        me_id, date(2026, 1, 5),
        datetime(2026, 1, 5, 9, 0, tzinfo=tz),
        datetime(2026, 1, 5, 17, 0, tzinfo=tz),
    )
    resp = client.post(
        "/api/sales/schedule/shift-requests",
        headers=me_hdr,
        json={"request_type": "cover", "source_entry_id": past_id},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "entry_started"

    # ============================================================
    # 5) Cancel own pending; second cancel is terminal
    # ============================================================
    print("===== cancel + terminal =====")
    resp = client.post(
        f"/api/sales/schedule/shift-requests/{cover['id']}/cancel",
        headers=me_hdr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert _event_count(cover["id"], "cancelled") == 1

    resp = client.post(
        f"/api/sales/schedule/shift-requests/{cover['id']}/cancel",
        headers=me_hdr,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "request_terminal"

    print("shift_request_create_cancel smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
