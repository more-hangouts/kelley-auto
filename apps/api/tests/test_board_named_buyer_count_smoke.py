"""Smoke for Phase 10.4 board ``named_buyer_count`` signal.

Seeds four quinceanera events shaped to exercise every branch of the
counting logic:

  - Event A: no tagged rows at all  → count = 0 (legacy / untagged case).
  - Event B: one appointment tagged to one participant
            → count = 1 (single-row buyer).
  - Event C: appointments tagged to participant1, quote tagged to
            participant2, invoice tagged to participant1
            → count = 2 (two distinct participants across mixed tables;
            participant1 appears twice but counts once).
  - Event D: one appointment AND one quote both tagged to the SAME
            participant
            → count = 1 (single buyer, multiple row types — UNION dedup).

The board GET also returns hundreds of pre-existing events from the dev
DB; the smoke filters to the seeded IDs only and ignores everything
else (per [feedback_global_pass_smokes]).
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
            username=f"admin-smoke-board-buyer-{suffix}",
            email=f"phase10-smoke-board-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 10 Smoke Admin Board {suffix}",
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


def _make_contact(tag: str, suffix: str) -> int:
    db = SessionLocal()
    try:
        digits = f"55512{uuid.uuid4().int % 100_000:05d}"
        c = Contact(
            display_name=f"Phase 10 Smoke Board {tag} {suffix}",
            email=f"phase10-smoke-board-{tag.lower()}-{suffix}@example.com",
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
        digits = f"55513{uuid.uuid4().int % 100_000:05d}"
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
            email=f"phase10-smoke-board-appt-{uuid.uuid4().hex[:6]}@example.com",
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
) -> int:
    db = SessionLocal()
    try:
        i = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            event_participant_id=event_participant_id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=10000,
            discount_cents=0,
            tax_cents=0,
            total_cents=10000,
            paid_to_date_cents=0,
            balance_cents=10000,
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

    # ===== Event A: no tagged rows. Expected count = 0. =====
    contact_a = _make_contact("A", suffix)
    event_a = _make_event(
        primary_contact_id=contact_a,
        name=f"Phase 10 Smoke Board A {suffix}",
    )
    _make_appointment(
        event_id=event_a,
        contact_id=contact_a,
        event_participant_id=None,
        code=f"BA{suffix[:6]}",
    )

    # ===== Event B: one tagged appointment. Expected count = 1. =====
    contact_b = _make_contact("B", suffix)
    event_b = _make_event(
        primary_contact_id=contact_b,
        name=f"Phase 10 Smoke Board B {suffix}",
    )
    part_b = _make_participant(
        event_id=event_b, contact_id=contact_b, role="chambelan", label=f"B {suffix}"
    )
    _make_appointment(
        event_id=event_b,
        contact_id=contact_b,
        event_participant_id=part_b,
        code=f"BB{suffix[:6]}",
    )

    # ===== Event C: two distinct buyers across mixed tables.
    #              Expected count = 2. =====
    contact_c = _make_contact("C", suffix)
    event_c = _make_event(
        primary_contact_id=contact_c,
        name=f"Phase 10 Smoke Board C {suffix}",
    )
    part_c1 = _make_participant(
        event_id=event_c, contact_id=contact_c, role="chambelan", label=f"C1 {suffix}"
    )
    part_c2 = _make_participant(
        event_id=event_c, contact_id=contact_c, role="dama", label=f"C2 {suffix}"
    )
    _make_appointment(
        event_id=event_c,
        contact_id=contact_c,
        event_participant_id=part_c1,
        code=f"BC{suffix[:6]}",
    )
    _make_quote(
        event_id=event_c, contact_id=contact_c, event_participant_id=part_c2
    )
    _make_invoice(
        event_id=event_c, contact_id=contact_c, event_participant_id=part_c1
    )

    # ===== Event D: one buyer with two row types (dedup case).
    #              Expected count = 1. =====
    contact_d = _make_contact("D", suffix)
    event_d = _make_event(
        primary_contact_id=contact_d,
        name=f"Phase 10 Smoke Board D {suffix}",
    )
    part_d = _make_participant(
        event_id=event_d, contact_id=contact_d, role="chambelan", label=f"D {suffix}"
    )
    _make_appointment(
        event_id=event_d,
        contact_id=contact_d,
        event_participant_id=part_d,
        code=f"BD{suffix[:6]}",
    )
    _make_quote(
        event_id=event_d, contact_id=contact_d, event_participant_id=part_d
    )

    # ===== Hit the board endpoint =====
    resp = client.get(
        "/api/events/board?event_type=quinceanera", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Flatten cards from all columns into a single dict keyed by id.
    cards_by_id: dict[int, dict] = {}
    for col in body["columns"]:
        for card in col["cards"]:
            cards_by_id[card["id"]] = card

    expected = {
        event_a: 0,
        event_b: 1,
        event_c: 2,
        event_d: 1,
    }
    for event_id, want in expected.items():
        assert event_id in cards_by_id, (event_id, list(cards_by_id.keys())[:5])
        card = cards_by_id[event_id]
        assert "named_buyer_count" in card, card
        assert card["named_buyer_count"] == want, (
            f"event {event_id}: got {card['named_buyer_count']}, want {want}"
        )

    print("board_named_buyer_count smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
