"""Smoke for Phase 10.5 per-participant buyer breakdown on GET /events/{id}.

Seeds one quinceanera event with two participants and a mixed set of
linked rows, then asserts the per-participant counts and the outstanding
balance returned in `EventDetailResponse.participants`. Matches the
shape consumed by the admin quick-view drawer.

Shape under test (per participant on the GET /events/{id} payload):

  - linked_appointment_count: rows in `appointments` tagged to this
    participant on this event (any status — mirrors the board's
    `named_buyer_count` semantics).
  - linked_quote_count: same, against `quotes`.
  - linked_invoice_count: same, against `invoices` (also any status).
  - outstanding_balance_cents: sum of `balance_cents` for invoices
    tagged to this participant where status IN ('sent','partial') and
    deleted_at IS NULL — matches the card's outstanding rollup so the
    quick-view per-buyer figure and the card headline never disagree.

Event layout:

  - participant P1 (parent): 2 appointments, 1 quote, 2 invoices
    (one live 'sent' with $200 balance, one 'draft' with $0 outstanding).
    Expected: appts=2, quotes=1, invoices=2, outstanding=20000.
  - participant P2 (dama): 1 quote, no other rows.
    Expected: appts=0, quotes=1, invoices=0, outstanding=0.
  - One untagged appointment (event_participant_id NULL) — should NOT
    affect any participant's counts.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    Contact,
    Event,
    EventParticipant,
    Invoice,
    Quote,
    QuoteLineItem,
    User,
)

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_participant_ids: list[int] = []
_created_appt_ids: list[int] = []
_created_quote_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _admin_token() -> str:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"admin-smoke-detail-buyers-{suffix}",
            email=f"phase10-smoke-detail-buyers-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 10 Smoke Detail Buyers Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return create_access_token(u)
    finally:
        db.close()


def _make_contact(suffix: str) -> int:
    db = SessionLocal()
    try:
        digits = f"55514{uuid.uuid4().int % 100_000:05d}"
        c = Contact(
            display_name=f"Phase 10 Smoke Detail Buyers {suffix}",
            email=f"phase10-smoke-detail-buyers-c-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["phase10-smoke"],
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _created_contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _make_event(*, primary_contact_id: int, name: str) -> int:
    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=primary_contact_id,
            event_type="quinceanera",
            event_name=name,
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        _created_event_ids.append(e.id)
        return e.id
    finally:
        db.close()


def _make_participant(*, event_id: int, contact_id: int, role: str, label: str) -> int:
    db = SessionLocal()
    try:
        p = EventParticipant(
            event_id=event_id,
            contact_id=contact_id,
            role=role,
            display_name=f"{role.title()} {label}",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        _created_participant_ids.append(p.id)
        return p.id
    finally:
        db.close()


def _make_appointment(
    *,
    event_id: int,
    contact_id: int,
    event_participant_id: int | None,
    code: str,
) -> int:
    db = SessionLocal()
    try:
        digits = f"55515{uuid.uuid4().int % 100_000:05d}"
        slot = datetime.now(timezone.utc) + timedelta(days=30)
        a = Appointment(
            confirmation_code=code,
            slot_start_at=slot,
            slot_end_at=slot + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name="Cel",
            party_size_bucket="pair",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            email=f"phase10-smoke-detail-buyers-appt-{uuid.uuid4().hex[:6]}@example.com",
            status="confirmed",
            contact_id=contact_id,
            crm_event_id=event_id,
            event_participant_id=event_participant_id,
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _created_appt_ids.append(a.id)
        return a.id
    finally:
        db.close()


def _make_quote(
    *,
    event_id: int,
    contact_id: int,
    event_participant_id: int | None,
) -> int:
    db = SessionLocal()
    try:
        q = Quote(
            event_id=event_id,
            contact_id=contact_id,
            event_participant_id=event_participant_id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=10000,
            discount_cents=0,
            tax_cents=0,
            total_cents=10000,
            terms="net 0",
        )
        db.add(q)
        db.flush()
        line = QuoteLineItem(
            quote_id=q.id,
            sort_order=0,
            kind="product",
            description="Sample",
            quantity=1,
            unit_price_cents=10000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=10000,
            line_tax_cents=0,
            line_total_cents=10000,
        )
        db.add(line)
        db.commit()
        db.refresh(q)
        _created_quote_ids.append(q.id)
        return q.id
    finally:
        db.close()


def _make_invoice(
    *,
    event_id: int,
    contact_id: int,
    event_participant_id: int | None,
    status: str,
    balance_cents: int,
) -> int:
    db = SessionLocal()
    try:
        # `chk_invoice_number_when_not_draft` requires invoice_number once
        # status leaves 'draft'; stamp a smoke-unique number for safety.
        invoice_number = (
            None
            if status == "draft"
            else f"DBP10-{uuid.uuid4().hex[:10].upper()}"
        )
        i = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            event_participant_id=event_participant_id,
            invoice_number=invoice_number,
            status=status,
            issue_date=date.today(),
            subtotal_cents=20000,
            discount_cents=0,
            tax_cents=0,
            total_cents=20000,
            paid_to_date_cents=20000 - balance_cents,
            balance_cents=balance_cents,
        )
        db.add(i)
        db.commit()
        db.refresh(i)
        _created_invoice_ids.append(i.id)
        return i.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
        if _created_invoice_ids:
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                {"ids": _created_invoice_ids},
            )
        if _created_quote_ids:
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _created_quote_ids},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": _created_quote_ids},
            )
        if _created_appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appt_ids},
            )
        if _created_participant_ids:
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE id = ANY(:ids)"
                ),
                {"ids": _created_participant_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
        if _created_contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _created_contact_ids},
            )
        if _created_user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _created_user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    flush_for_testing()

    token = _admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    suffix = uuid.uuid4().hex[:6].upper()
    contact_id = _make_contact(suffix)
    event_id = _make_event(
        primary_contact_id=contact_id,
        name=f"Phase 10 Smoke Detail Buyers {suffix}",
    )

    p1 = _make_participant(
        event_id=event_id, contact_id=contact_id, role="parent", label=f"1 {suffix}"
    )
    p2 = _make_participant(
        event_id=event_id, contact_id=contact_id, role="dama", label=f"2 {suffix}"
    )

    # P1: 2 appts + 1 quote + 2 invoices (one 'sent' with $200 balance,
    # one 'draft' with $0 outstanding). Outstanding rollup should equal
    # the live invoice only (20000 cents).
    _make_appointment(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        code=f"DB1{suffix[:6]}",
    )
    _make_appointment(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        code=f"DB2{suffix[:6]}",
    )
    _make_quote(event_id=event_id, contact_id=contact_id, event_participant_id=p1)
    _make_invoice(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        status="sent",
        balance_cents=20000,
    )
    _make_invoice(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        status="draft",
        balance_cents=0,
    )

    # P2: 1 quote only — no appts, no invoices, $0 outstanding.
    _make_quote(event_id=event_id, contact_id=contact_id, event_participant_id=p2)

    # Untagged appointment — must NOT inflate any participant's counts.
    _make_appointment(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=None,
        code=f"DB0{suffix[:6]}",
    )

    resp = client.get(f"/api/events/{event_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    by_id = {p["id"]: p for p in body["participants"]}
    assert p1 in by_id and p2 in by_id, list(by_id.keys())

    got_p1 = by_id[p1]
    assert got_p1["linked_appointment_count"] == 2, got_p1
    assert got_p1["linked_quote_count"] == 1, got_p1
    assert got_p1["linked_invoice_count"] == 2, got_p1
    assert got_p1["outstanding_balance_cents"] == 20000, got_p1

    got_p2 = by_id[p2]
    assert got_p2["linked_appointment_count"] == 0, got_p2
    assert got_p2["linked_quote_count"] == 1, got_p2
    assert got_p2["linked_invoice_count"] == 0, got_p2
    assert got_p2["outstanding_balance_cents"] == 0, got_p2

    print("event_detail_buyer_breakdown smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
