"""Smoke tests for the Sales Portal Phase 3 actions.

Covers the composite status handler and the notes patch.

Each scenario seeds its own appointment row so the smokes can run in
the same DB without cross-contamination, and cleans up at the end.
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
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    Appointment,
    Contact,
    Event,
    EventParticipant,
    EventStatusChangeEvent,
    User,
)
from services import activity_log, booking_service  # noqa: E402
from tests._attendance_helpers import (  # noqa: E402
    restore_gate,
    snapshot_and_disable_gate,
)

client = TestClient(app)

_user_ids: list[int] = []
_appt_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p3-{suffix}",
            email=f"{role}-p3-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P3 {role.title()}",
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


def _seed_contact() -> int:
    db = SessionLocal()
    try:
        c = Contact(
            display_name="P3 Customer",
            first_name="P3",
            last_name="Customer",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"p3-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _seed_event(contact_id: int, status: str = "lead") -> int:
    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="P3 Test Event",
            event_date=date.today() + timedelta(days=200),
            status=status,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        _event_ids.append(e.id)
        # add a participant so the appointment promote path doesn't
        # have to seed one for us
        db.add(
            EventParticipant(
                event_id=e.id,
                contact_id=contact_id,
                role="quinceanera",
                display_name="P3 Quince",
            )
        )
        db.commit()
        return e.id
    finally:
        db.close()


def _seed_appointment(
    *, contact_id: int, event_id: int | None, internal_notes: str | None = None
) -> int:
    db = SessionLocal()
    try:
        tz = ZoneInfo(APP_TIMEZONE)
        slot_local = datetime.combine(
            date.today(), time(10, 0), tzinfo=tz
        ) + timedelta(minutes=len(_appt_ids))  # nudge so codes stay unique
        slot_utc = slot_local.astimezone(timezone.utc)
        a = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=60),
            slot_duration_minutes=60,
            timezone=APP_TIMEZONE,
            celebrant_first_name="P3",
            celebrant_last_name="Smoke",
            parent_first_name="P3",
            parent_last_name="Parent",
            party_size_bucket="solo",
            phone="(210) 555-0123",
            email=f"p3-{uuid.uuid4().hex[:6]}@example.com",
            contact_id=contact_id,
            crm_event_id=event_id,
            status="confirmed",
            internal_notes=internal_notes,
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _appt_ids.append(a.id)
        return a.id
    finally:
        db.close()


def _activity_for_event(event_id: int, activity_type: str) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(ActivityLog)
                .where(ActivityLog.event_id == event_id)
                .where(ActivityLog.activity_type == activity_type)
                .order_by(ActivityLog.id)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()


def _event_status_changes(event_id: int) -> list[EventStatusChangeEvent]:
    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(EventStatusChangeEvent)
                .where(EventStatusChangeEvent.event_id == event_id)
                .order_by(EventStatusChangeEvent.id)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()


def _refresh_appt(appt_id: int) -> Appointment:
    db = SessionLocal()
    try:
        return db.get(Appointment, appt_id)
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        # appointments may be linked to events that we'll clean up too,
        # so wipe activity_log for those events first to avoid orphan
        # rows hanging around if anything fails midstream.
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_status_change_events "
                         "WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_participants "
                         "WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        # Some scenarios let the service create extra events via
        # promote_appointment_to_event; collect them via the contact ids
        # we seeded.
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id IN "
                         "(SELECT id FROM events WHERE primary_contact_id = ANY(:ids))"),
                {"ids": _contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events WHERE event_id IN "
                    "(SELECT id FROM events WHERE primary_contact_id = ANY(:ids))"
                ),
                {"ids": _contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id IN "
                    "(SELECT id FROM events WHERE primary_contact_id = ANY(:ids))"
                ),
                {"ids": _contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _event_ids:
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


_gate_snapshot: dict | None = None


def main() -> None:
    global _gate_snapshot
    # Phase 7 Slice 2 gates sales mutations on attendance. This smoke
    # exercises the composite handler, not the gate itself, so we
    # disable the gate around the run.
    _gate_snapshot = snapshot_and_disable_gate()

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_token = _token_for(sales_id, sales=True)
    admin_token = _token_for(admin_id, sales=False)
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # ------------------------------------------------------------------
    # Scenario A: arrived on appointment with NO event.
    # Expectation: event is created (status starts at 'lead', then
    # transitions to 'consulted'); both event.status_changed and
    # appointment.arrived rows land in activity_log.
    # ------------------------------------------------------------------
    contact_a = _seed_contact()
    appt_a = _seed_appointment(contact_id=contact_a, event_id=None)

    resp = client.post(
        f"/api/sales/appointments/{appt_a}/status",
        headers=sales_headers,
        json={"action": "arrived", "notes": "walk-in arrived on time"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["appointment_status"] == "attended"
    assert body["promoted_event"] is True
    assert body["prior_event_status"] is None
    assert body["new_event_status"] == "consulted"
    assert body["changed"] is True

    appt_a_row = _refresh_appt(appt_a)
    new_event_id = appt_a_row.crm_event_id
    assert new_event_id is not None
    assert appt_a_row.attended_at is not None

    arrived_rows = _activity_for_event(new_event_id, "appointment.arrived")
    assert len(arrived_rows) == 1
    assert arrived_rows[0].subject_kind == "appointment"
    assert arrived_rows[0].subject_id == appt_a
    assert arrived_rows[0].payload["promoted_event"] is True
    assert arrived_rows[0].payload["new_event_status"] == "consulted"

    status_changed_rows = _activity_for_event(new_event_id, "event.status_changed")
    assert len(status_changed_rows) == 1
    assert status_changed_rows[0].payload["from_status"] == "lead"
    assert status_changed_rows[0].payload["to_status"] == "consulted"

    # promote_appointment_to_event seeds an initial null→lead audit row
    # in event_status_change_events; change_event_status appends another
    # row (lead→consulted) in the same transaction. So we expect 2.
    assert len(_event_status_changes(new_event_id)) == 2

    # Idempotent re-tap: second `arrived` is a no-op (no new rows).
    resp = client.post(
        f"/api/sales/appointments/{appt_a}/status",
        headers=sales_headers,
        json={"action": "arrived"},
    )
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["changed"] is False
    assert len(_activity_for_event(new_event_id, "appointment.arrived")) == 1
    assert len(_activity_for_event(new_event_id, "event.status_changed")) == 1
    assert len(_event_status_changes(new_event_id)) == 2

    # ------------------------------------------------------------------
    # Scenario B: arrived on appointment with an EXISTING lead event.
    # Expectation: event transitions lead → consulted; no new event row.
    # ------------------------------------------------------------------
    contact_b = _seed_contact()
    event_b = _seed_event(contact_b, status="lead")
    appt_b = _seed_appointment(contact_id=contact_b, event_id=event_b)

    resp = client.post(
        f"/api/sales/appointments/{appt_b}/status",
        headers=sales_headers,
        json={"action": "arrived"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["promoted_event"] is False
    assert body["prior_event_status"] == "lead"
    assert body["new_event_status"] == "consulted"

    # ------------------------------------------------------------------
    # Scenario C: arrived on appointment with an ALREADY consulted event.
    # Expectation: no event.status_changed row written; appointment.arrived
    # is still recorded for the appointment-level audit trail.
    # ------------------------------------------------------------------
    contact_c = _seed_contact()
    event_c = _seed_event(contact_c, status="consulted")
    appt_c = _seed_appointment(contact_id=contact_c, event_id=event_c)

    before = len(_activity_for_event(event_c, "event.status_changed"))
    resp = client.post(
        f"/api/sales/appointments/{appt_c}/status",
        headers=sales_headers,
        json={"action": "arrived"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prior_event_status"] == "consulted"
    assert body["new_event_status"] == "consulted"
    after = len(_activity_for_event(event_c, "event.status_changed"))
    assert after == before, (before, after)
    arrived_rows_c = _activity_for_event(event_c, "appointment.arrived")
    assert len(arrived_rows_c) == 1

    # ------------------------------------------------------------------
    # Scenario D: no_show / cancelled don't touch event status.
    # ------------------------------------------------------------------
    contact_d = _seed_contact()
    event_d = _seed_event(contact_d, status="lead")
    appt_d = _seed_appointment(contact_id=contact_d, event_id=event_d)

    resp = client.post(
        f"/api/sales/appointments/{appt_d}/status",
        headers=sales_headers,
        json={"action": "no_show"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prior_event_status"] is None
    assert body["new_event_status"] is None  # we do not touch event status
    assert _refresh_appt(appt_d).status == "no_show"
    assert len(_activity_for_event(event_d, "event.status_changed")) == 0
    assert len(_activity_for_event(event_d, "appointment.no_show")) == 1

    contact_e = _seed_contact()
    event_e = _seed_event(contact_e, status="lead")
    appt_e = _seed_appointment(contact_id=contact_e, event_id=event_e)
    resp = client.post(
        f"/api/sales/appointments/{appt_e}/status",
        headers=sales_headers,
        json={"action": "cancelled"},
    )
    assert resp.status_code == 200, resp.text
    assert _refresh_appt(appt_e).status == "cancelled"
    assert len(_activity_for_event(event_e, "event.status_changed")) == 0
    assert len(_activity_for_event(event_e, "appointment.cancelled")) == 1

    # ------------------------------------------------------------------
    # Scenario F: notes patch.
    # ------------------------------------------------------------------
    contact_f = _seed_contact()
    event_f = _seed_event(contact_f, status="lead")
    appt_f = _seed_appointment(
        contact_id=contact_f, event_id=event_f, internal_notes="initial seed note"
    )

    resp = client.patch(
        f"/api/sales/appointments/{appt_f}/notes",
        headers=sales_headers,
        json={"internal_notes": "Bring the navy ballgown and ivory mermaid options."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True
    assert body["internal_notes"].startswith("Bring the navy")

    notes_rows = _activity_for_event(event_f, "appointment.notes_edited")
    assert len(notes_rows) == 1
    payload = notes_rows[0].payload
    assert "internal_notes" not in payload
    assert payload["prior_length"] == len("initial seed note")
    assert payload["new_length"] == len(
        "Bring the navy ballgown and ivory mermaid options."
    )

    # Idempotent: same value, no new activity row.
    resp = client.patch(
        f"/api/sales/appointments/{appt_f}/notes",
        headers=sales_headers,
        json={"internal_notes": "Bring the navy ballgown and ivory mermaid options."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is False
    assert len(_activity_for_event(event_f, "appointment.notes_edited")) == 1

    # Notes patch on appointment with no event: no activity row written.
    contact_g = _seed_contact()
    appt_g = _seed_appointment(contact_id=contact_g, event_id=None)
    resp = client.patch(
        f"/api/sales/appointments/{appt_g}/notes",
        headers=sales_headers,
        json={"internal_notes": "no event yet"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["internal_notes"] == "no event yet"

    # ------------------------------------------------------------------
    # Scenario G: scope rejection.
    # ------------------------------------------------------------------
    resp = client.post(
        f"/api/sales/appointments/{appt_a}/status",
        headers=admin_headers,
        json={"action": "arrived"},
    )
    assert resp.status_code == 403, resp.text
    resp = client.patch(
        f"/api/sales/appointments/{appt_a}/notes",
        headers=admin_headers,
        json={"internal_notes": "x"},
    )
    assert resp.status_code == 403, resp.text

    # ------------------------------------------------------------------
    # Scenario H: 404 + 422 handling.
    # ------------------------------------------------------------------
    resp = client.post(
        "/api/sales/appointments/99999999/status",
        headers=sales_headers,
        json={"action": "arrived"},
    )
    assert resp.status_code == 404, resp.text

    resp = client.post(
        f"/api/sales/appointments/{appt_a}/status",
        headers=sales_headers,
        json={"action": "complete"},  # not in the StatusAction Literal
    )
    assert resp.status_code == 422, resp.text

    print("sales_appointments_actions smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
        restore_gate(_gate_snapshot)
