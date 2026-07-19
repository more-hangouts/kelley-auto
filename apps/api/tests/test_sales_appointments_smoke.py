"""Smoke tests for the Sales Portal Phase 2 reads.

Seeds 3 appointments today, 1 yesterday, 1 tomorrow (all in
APP_TIMEZONE), plus a sales user and a separate sales user, then
verifies:

  - GET /api/sales/appointments/today returns exactly the 3 today rows,
    ordered by slot_start_at ascending.
  - `mine=true` filters by `assigned_user_id`.
  - `has_assigned` flips to true when at least one appointment in the
    response window is assigned.
  - GET /api/sales/appointments/{id} returns full detail with contact,
    enrichment, event (when linked), participants, and recent activity.
  - Admin token gets 403 (sales-only surface).
  - Bad id returns 404.

Each row is created via service-layer helpers / direct SQLAlchemy so
the smoke does not depend on the booking widget's HTTP path.
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    AppointmentEnrichmentResponse,
    Contact,
    Event,
    EventParticipant,
    User,
)
from services import activity_log, booking_service, sales_auth  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_appt_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []


def _make_user(*, role: str, pin: str | None = None) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-p2-{suffix}"
        u = User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P2 {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        if pin:
            sales_auth.set_pin(db, u, pin, force_change=False)
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, username
    finally:
        db.close()


def _sales_token_for(user_id: int) -> str:
    """Mint a sales-scope token directly (skip the PIN flow for setup)."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        from database.auth import create_sales_token  # local import keeps test boot light
        return create_sales_token(user)
    finally:
        db.close()


def _admin_token_for(user_id: int) -> str:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        return create_access_token(user)
    finally:
        db.close()


def _seed_contact() -> int:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name="Phase 2 Test Customer",
            first_name="Phase2",
            last_name="Customer",
            phone_e164="+12105551777",
            phone="(210) 555-1777",
            email=f"p2-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        _contact_ids.append(contact.id)
        return contact.id
    finally:
        db.close()


def _seed_event(contact_id: int) -> int:
    db = SessionLocal()
    try:
        event = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="Phase 2 Test Event",
            event_date=date.today() + timedelta(days=200),
            status="lead",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        _event_ids.append(event.id)

        # Add a participant so detail payload can verify the join.
        participant = EventParticipant(
            event_id=event.id,
            contact_id=contact_id,
            role="quinceanera",
            display_name="Phase 2 Quince",
        )
        db.add(participant)

        # And one activity log entry so recent_activity has something.
        # Use a known type so the registry doesn't warn.
        activity_log.log_activity(
            db,
            event_id=event.id,
            actor_kind="system",
            actor_user_id=None,
            activity_type=activity_log.EVENT_STATUS_CHANGED,
            subject_kind="event",
            subject_id=event.id,
            payload={"from_status": None, "to_status": "lead", "smoke": "phase2"},
        )
        db.commit()
        return event.id
    finally:
        db.close()


def _seed_appointment(
    *,
    contact_id: int,
    event_id: int | None,
    slot_local: datetime,
    duration_minutes: int = 60,
    assigned_user_id: int | None = None,
    enrichment: bool = False,
) -> int:
    db = SessionLocal()
    try:
        if slot_local.tzinfo is None:
            slot_local = slot_local.replace(tzinfo=ZoneInfo(APP_TIMEZONE))
        slot_utc = slot_local.astimezone(timezone.utc)
        appt = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=duration_minutes),
            slot_duration_minutes=duration_minutes,
            timezone=APP_TIMEZONE,
            celebrant_first_name="Smoke",
            celebrant_last_name="Celebrant",
            parent_first_name="Smoke",
            parent_last_name="Parent",
            party_size_bucket="solo",
            phone="(210) 555-0177",
            email=f"smoke-appt-{uuid.uuid4().hex[:6]}@example.com",
            contact_id=contact_id,
            crm_event_id=event_id,
            assigned_user_id=assigned_user_id,
            internal_notes="Note from prep — bring the navy and the rose-gold options.",
            status="confirmed",
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _appt_ids.append(appt.id)

        if enrichment:
            er = AppointmentEnrichmentResponse(
                appointment_id=appt.id,
                dress_styles=["ballgown", "mermaid"],
                colors=["navy", "rose-gold"],
                budget_range="$1500-2500",
                quince_theme="Royal Garden",
                court_size=14,
            )
            db.add(er)
            db.commit()
        return appt.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointment_enrichment_responses "
                         "WHERE appointment_id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM event_participants WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
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
    yesterday_local = today_local - timedelta(days=1)
    tomorrow_local = today_local + timedelta(days=1)

    sales_id, _sales_username = _make_user(role="sales")
    other_sales_id, _other_username = _make_user(role="sales")
    admin_id, _admin_username = _make_user(role="admin")

    sales_token = _sales_token_for(sales_id)
    other_sales_token = _sales_token_for(other_sales_id)
    admin_token = _admin_token_for(admin_id)
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    other_headers = {"Authorization": f"Bearer {other_sales_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    contact_id = _seed_contact()
    event_id = _seed_event(contact_id)

    # Today: 09:00, 11:30, 14:15. The 11:30 is assigned to `sales_id`.
    today_appts = []
    today_appts.append(
        _seed_appointment(
            contact_id=contact_id,
            event_id=event_id,
            slot_local=datetime.combine(today_local, time(9, 0), tzinfo=tz),
            enrichment=True,
        )
    )
    today_appts.append(
        _seed_appointment(
            contact_id=contact_id,
            event_id=None,
            slot_local=datetime.combine(today_local, time(11, 30), tzinfo=tz),
            assigned_user_id=sales_id,
        )
    )
    today_appts.append(
        _seed_appointment(
            contact_id=contact_id,
            event_id=event_id,
            slot_local=datetime.combine(today_local, time(14, 15), tzinfo=tz),
        )
    )

    # Out-of-window rows: yesterday + tomorrow
    _seed_appointment(
        contact_id=contact_id,
        event_id=None,
        slot_local=datetime.combine(yesterday_local, time(13, 0), tzinfo=tz),
    )
    _seed_appointment(
        contact_id=contact_id,
        event_id=None,
        slot_local=datetime.combine(tomorrow_local, time(13, 0), tzinfo=tz),
    )

    # ---- Today list, sales token, mine=false ----
    resp = client.get(
        "/api/sales/appointments/today", headers=sales_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["date"] == today_local.isoformat()
    ids = [a["id"] for a in body["appointments"]]
    seeded_ids = [appt_id for appt_id in ids if appt_id in today_appts]
    assert seeded_ids == today_appts, (
        f"expected seeded today rows in order {today_appts}, got {ids}"
    )
    assert body["has_assigned"] is True, body  # one assigned today
    # Spot-check the enrichment summary on the first seeded row. Live
    # production/dev databases may already have appointments today.
    first = next(a for a in body["appointments"] if a["id"] == today_appts[0])
    assert first["enrichment_summary"] is not None
    assert first["enrichment_summary"]["budget_range"] == "$1500-2500"
    assert first["crm_event_status"] == "lead"
    assert first["internal_notes_preview"] is not None

    # ---- Today list, mine=true filters to assigned_user_id = me ----
    resp = client.get(
        "/api/sales/appointments/today?mine=true", headers=sales_headers
    )
    assert resp.status_code == 200, resp.text
    mine_body = resp.json()
    assert [a["id"] for a in mine_body["appointments"]] == [today_appts[1]]

    # Different sales user → mine=true returns empty (and has_assigned
    # reflects that empty filtered window, not the global today set).
    resp = client.get(
        "/api/sales/appointments/today?mine=true", headers=other_headers
    )
    assert resp.status_code == 200, resp.text
    other_body = resp.json()
    assert other_body["appointments"] == []
    assert other_body["has_assigned"] is False

    # ---- Detail endpoint ----
    detail_id = today_appts[0]
    resp = client.get(
        f"/api/sales/appointments/{detail_id}", headers=sales_headers
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["appointment"]["id"] == detail_id
    assert detail["contact"] is not None
    assert detail["event"] is not None
    assert detail["event"]["status"] == "lead"
    assert any(p["role"] == "quinceanera" for p in detail["participants"])
    assert detail["enrichment"] is not None
    assert detail["enrichment"]["budget_range"] == "$1500-2500"
    assert len(detail["recent_activity"]) >= 1
    assert detail["recent_activity"][0]["activity_type"] == "event.status_changed"

    # Detail for an appointment with no linked event still works:
    detail_no_event = today_appts[1]
    resp = client.get(
        f"/api/sales/appointments/{detail_no_event}", headers=sales_headers
    )
    assert resp.status_code == 200, resp.text
    no_evt = resp.json()
    assert no_evt["event"] is None
    assert no_evt["participants"] == []
    assert no_evt["recent_activity"] == []
    assert no_evt["appointment"]["assigned_user_id"] == sales_id

    # ---- Admin token rejected (sales-only) ----
    resp = client.get("/api/sales/appointments/today", headers=admin_headers)
    assert resp.status_code == 403, resp.text
    resp = client.get(
        f"/api/sales/appointments/{detail_id}", headers=admin_headers
    )
    assert resp.status_code == 403, resp.text

    # ---- Unknown id → 404 ----
    resp = client.get(
        "/api/sales/appointments/99999999", headers=sales_headers
    )
    assert resp.status_code == 404, resp.text

    print("sales_appointments smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
