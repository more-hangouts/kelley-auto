"""Smoke for Phase 10.6 buyer-journey payload on GET /events/{id}.

Validates the new `quotes` and `invoices` lists on EventDetailResponse
and the deliberate asymmetry with `participants[].linked_*_count`:

  - Journey lists (quotes/invoices) exclude soft-deleted rows. The
    operator's timeline must not surface a quote/invoice they've
    deleted; this is a UX surface, not a count signal.
  - ParticipantSummary counts match the board's `named_buyer_count`
    semantics, which include soft-deletes. The quick-view drawer
    consumes these counts and the board's headline must align.
  - `outstanding_balance_cents` on ParticipantSummary filters to live
    invoices only (status IN ('sent','partial') AND deleted_at IS NULL),
    matching the board card's outstanding rollup.

Event layout:

  - participant P1 (parent):
      * 1 appointment (tagged)
      * 1 live quote + 1 soft-deleted quote
      * 1 live 'sent' invoice ($300 balance) + 1 soft-deleted invoice
  - participant P2 (dama):
      * 1 live quote
  - Untagged: 1 appointment (event_participant_id NULL).

Expected:

  - body.quotes        : 2 entries (P1 live + P2 live)
  - body.invoices      : 1 entry  (P1 live)
  - body.appointments  : 2 entries (P1 appt + untagged appt)
  - P1.linked_quote_count   == 2  (live + soft-deleted)
  - P1.linked_invoice_count == 2  (live + soft-deleted)
  - P1.outstanding_balance  == 30000  (live 'sent' only)
  - P2.linked_quote_count   == 1
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
            username=f"admin-smoke-journey-payload-{suffix}",
            email=f"phase10-smoke-journey-payload-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 10 Smoke Journey Payload Admin {suffix}",
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
        digits = f"55516{uuid.uuid4().int % 100_000:05d}"
        c = Contact(
            display_name=f"Phase 10 Smoke Journey Payload {suffix}",
            email=f"phase10-smoke-journey-payload-c-{suffix}@example.com",
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
        digits = f"55517{uuid.uuid4().int % 100_000:05d}"
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
            email=f"phase10-smoke-jp-appt-{uuid.uuid4().hex[:6]}@example.com",
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
    soft_deleted: bool = False,
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
            deleted_at=(
                datetime.now(timezone.utc) if soft_deleted else None
            ),
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
    soft_deleted: bool = False,
) -> int:
    db = SessionLocal()
    try:
        invoice_number = (
            None
            if status == "draft"
            else f"JPP10-{uuid.uuid4().hex[:10].upper()}"
        )
        i = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            event_participant_id=event_participant_id,
            invoice_number=invoice_number,
            status=status,
            issue_date=date.today(),
            subtotal_cents=30000,
            discount_cents=0,
            tax_cents=0,
            total_cents=30000,
            paid_to_date_cents=30000 - balance_cents,
            balance_cents=balance_cents,
            deleted_at=(
                datetime.now(timezone.utc) if soft_deleted else None
            ),
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
        name=f"Phase 10 Smoke Journey Payload {suffix}",
    )

    p1 = _make_participant(
        event_id=event_id, contact_id=contact_id, role="parent", label=f"1 {suffix}"
    )
    p2 = _make_participant(
        event_id=event_id, contact_id=contact_id, role="dama", label=f"2 {suffix}"
    )

    appt_p1 = _make_appointment(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        code=f"JP1{suffix[:6]}",
    )
    quote_p1_live = _make_quote(
        event_id=event_id, contact_id=contact_id, event_participant_id=p1
    )
    _make_quote(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        soft_deleted=True,
    )
    invoice_p1_live = _make_invoice(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        status="sent",
        balance_cents=30000,
    )
    _make_invoice(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=p1,
        status="sent",
        balance_cents=15000,
        soft_deleted=True,
    )

    quote_p2_live = _make_quote(
        event_id=event_id, contact_id=contact_id, event_participant_id=p2
    )

    appt_untagged = _make_appointment(
        event_id=event_id,
        contact_id=contact_id,
        event_participant_id=None,
        code=f"JP0{suffix[:6]}",
    )

    resp = client.get(f"/api/events/{event_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # ----- Journey lists exclude soft-deletes -----
    quote_ids = {q["id"] for q in body["quotes"]}
    invoice_ids = {i["id"] for i in body["invoices"]}
    appt_ids = {a["id"] for a in body["appointments"]}

    assert quote_ids == {quote_p1_live, quote_p2_live}, body["quotes"]
    assert invoice_ids == {invoice_p1_live}, body["invoices"]
    assert appt_ids == {appt_p1, appt_untagged}, body["appointments"]

    # Spot-check event_participant_id on a journey row.
    p1_quote = next(q for q in body["quotes"] if q["id"] == quote_p1_live)
    assert p1_quote["event_participant_id"] == p1, p1_quote
    assert p1_quote["total_cents"] == 10000, p1_quote

    p1_invoice = next(i for i in body["invoices"] if i["id"] == invoice_p1_live)
    assert p1_invoice["event_participant_id"] == p1, p1_invoice
    assert p1_invoice["balance_cents"] == 30000, p1_invoice

    # ----- ParticipantSummary counts include soft-deletes (board parity) -----
    by_id = {p["id"]: p for p in body["participants"]}
    got_p1 = by_id[p1]
    assert got_p1["linked_appointment_count"] == 1, got_p1
    # 1 live + 1 soft-deleted quote — counts include the soft-delete.
    assert got_p1["linked_quote_count"] == 2, got_p1
    # 1 live + 1 soft-deleted invoice — counts include the soft-delete.
    assert got_p1["linked_invoice_count"] == 2, got_p1
    # Outstanding rollup is live-only.
    assert got_p1["outstanding_balance_cents"] == 30000, got_p1

    got_p2 = by_id[p2]
    assert got_p2["linked_quote_count"] == 1, got_p2
    assert got_p2["linked_invoice_count"] == 0, got_p2
    assert got_p2["outstanding_balance_cents"] == 0, got_p2

    print("event_detail_journey_payload smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
