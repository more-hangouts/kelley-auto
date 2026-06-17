"""Smoke test for Phase 10 Slice 7 — SPLH leaderboard (Epic 6.5).

Runs as a script:

    venv/bin/python tests/test_splh_leaderboard_smoke.py
"""

from __future__ import annotations

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
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event, Invoice, StaffPunch, User  # noqa: E402
from services import dashboard, invoice_service  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_contact_ids: list[int] = []
_event_ids: list[int] = []


def _make_user(role: str, full_name: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"splh-{role}-{suffix}",
            email=f"splh-{role}-{suffix}@example.com",
            hashed_password=hash_password("splh-pass-12345"),
            full_name=full_name,
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(int(u.id))
        return int(u.id)
    finally:
        db.close()


def _token(user_id: int) -> str:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user is not None
        return create_access_token(user)
    finally:
        db.close()


def _make_event(label: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=f"{label} Contact",
            email=f"{label.lower()}-{uuid.uuid4().hex[:6]}@example.com",
            phone=f"(210) 555-{uuid.uuid4().int % 10000:04d}",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{label} Event",
            event_date=date(2027, 6, 15),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        _contact_ids.append(int(contact.id))
        _event_ids.append(int(event.id))
        return int(contact.id), int(event.id)
    finally:
        db.close()


def _invoice(
    *,
    user_id: int,
    contact_id: int,
    event_id: int,
    issue_date: date,
    status: str,
    paid_cents: int,
    total_cents: int,
) -> int:
    db = SessionLocal()
    try:
        inv = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            invoice_number=f"SPLH-{uuid.uuid4().hex[:10]}",
            status=status,
            issue_date=issue_date,
            total_cents=total_cents,
            paid_to_date_cents=paid_cents,
            balance_cents=max(0, total_cents - paid_cents),
            created_by_user_id=user_id,
            sold_by_user_id=user_id,
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        return int(inv.id)
    finally:
        db.close()


def _punch_pair(user_id: int, start_local: datetime, hours: float) -> None:
    db = SessionLocal()
    try:
        end_local = start_local + timedelta(hours=hours)
        db.add(
            StaffPunch(
                user_id=user_id,
                direction="in",
                punched_at=start_local.astimezone(timezone.utc),
                status="recorded",
            )
        )
        db.add(
            StaffPunch(
                user_id=user_id,
                direction="out",
                punched_at=end_local.astimezone(timezone.utc),
                status="recorded",
            )
        )
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM staff_punches WHERE user_id = ANY(:uids)"),
                {"uids": _user_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM invoices WHERE event_id = ANY(:eids)"),
                {"eids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    tz = ZoneInfo(os.environ["APP_TIMEZONE"])
    week_start = date(2026, 10, 5)
    week_end = week_start + timedelta(days=6)

    admin_id = _make_user("admin", "SPLH Admin")
    stylist_a = _make_user("sales", "SPLH Stylist A")
    stylist_b = _make_user("sales", "SPLH Stylist B")
    contact_a, event_a = _make_event("SPLHA")
    contact_b, event_b = _make_event("SPLHB")

    _invoice(
        user_id=stylist_a,
        contact_id=contact_a,
        event_id=event_a,
        issue_date=week_start,
        status="partial",
        paid_cents=40000,
        total_cents=100000,
    )
    _invoice(
        user_id=stylist_b,
        contact_id=contact_b,
        event_id=event_b,
        issue_date=week_start + timedelta(days=1),
        status="paid",
        paid_cents=60000,
        total_cents=60000,
    )
    _invoice(
        user_id=stylist_a,
        contact_id=contact_a,
        event_id=event_a,
        issue_date=week_start,
        status="draft",
        paid_cents=90000,
        total_cents=90000,
    )
    _invoice(
        user_id=stylist_b,
        contact_id=contact_b,
        event_id=event_b,
        issue_date=week_start,
        status="sent",
        paid_cents=0,
        total_cents=50000,
    )
    _punch_pair(
        stylist_a,
        datetime.combine(week_start, time(9, 0), tzinfo=tz),
        2,
    )
    _punch_pair(
        stylist_b,
        datetime.combine(week_start, time(10, 0), tzinfo=tz),
        4,
    )

    try:
        db = SessionLocal()
        try:
            created = invoice_service.create_invoice(
                db,
                event_id=event_a,
                contact_id=contact_a,
                actor_user_id=stylist_a,
            )
            assert created.sold_by_user_id == stylist_a
            db.rollback()

            payload = dashboard.splh_leaderboard(
                db,
                from_date=week_start,
                to_date=week_end,
                limit=5,
            )
            assert payload.revenue_basis == "paid_to_date_cents"
            assert payload.rows[0].user_id == stylist_a
            assert payload.rows[0].revenue_cents == 40000
            assert payload.rows[0].actual_hours == 2
            assert payload.rows[0].splh_cents_per_hour == 20000
            assert payload.rows[1].user_id == stylist_b
            assert payload.rows[1].revenue_cents == 60000
            assert payload.rows[1].actual_hours == 4
            assert payload.rows[1].splh_cents_per_hour == 15000
        finally:
            db.close()

        headers = {"Authorization": f"Bearer {_token(admin_id)}"}
        resp = client.get(
            "/api/dashboard/splh-leaderboard",
            params={
                "from_date": week_start.isoformat(),
                "to_date": week_end.isoformat(),
                "limit": 5,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["revenue_basis"] == "paid_to_date_cents"
        assert body["rows"][0]["user_id"] == stylist_a, body
        assert body["rows"][0]["splh_cents_per_hour"] == 20000, body

        unauth = client.get("/api/dashboard/splh-leaderboard")
        assert unauth.status_code in (401, 403), unauth.text
        print("SPLH leaderboard smoke passed")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
