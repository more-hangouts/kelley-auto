"""Real-DML smoke for migration 079.

The migration itself runs information_schema probes inside its own
transaction to assert the column + FK + index were created, but a
schema-level check doesn't prove writes work end-to-end. This smoke
exercises:

  1. Inserting an appointment, quote, and invoice with
     ``event_participant_id`` set reads back the value correctly.
  2. Deleting the event_participant row triggers ``ON DELETE SET NULL``
     on every dependent row — the FK behavior the migration claims.

Per [feedback_validate_schema_with_real_inserts]: a phase that touches
schema is not done until real INSERTs / UPDATEs / DELETEs exercise the
new shape. The migration's schema probe is necessary but not
sufficient on its own.

Pure ORM/SQL — no API surface. Phase 10.3 will exercise the columns
via real service paths.
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

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    Contact,
    Event,
    EventParticipant,
    Invoice,
    Quote,
)

_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_participant_ids: list[int] = []
_created_appointment_ids: list[int] = []
_created_quote_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _seed() -> dict:
    """Create the minimum graph the smoke needs: contact, event,
    event_participant, and one each of appointment/quote/invoice
    attached to the participant."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55509{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Phase 10 Smoke {tag}",
            email=f"phase10-smoke-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["phase10-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Phase 10 Smoke Quince {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        # The participant we'll attach buyer-journey rows to. A
        # ``chambelan`` is the realistic case: a court member buying
        # his own tuxedo while staying tied to the shared event.
        participant = EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="chambelan",
            display_name=f"Chambelan {tag}",
        )
        db.add(participant)
        db.flush()
        _created_participant_ids.append(participant.id)

        slot = datetime.now(timezone.utc) + timedelta(days=14)
        appt = Appointment(
            confirmation_code=f"P10{tag}",
            slot_start_at=slot,
            slot_end_at=slot + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone="America/Chicago",
            celebrant_first_name=f"Cel {tag}",
            party_size_bucket="pair",
            phone=contact.phone,
            phone_e164=contact.phone_e164,
            email=contact.email,
            status="confirmed",
            contact_id=contact.id,
            crm_event_id=event.id,
            event_participant_id=participant.id,
        )
        db.add(appt)
        db.flush()
        _created_appointment_ids.append(appt.id)

        quote = Quote(
            event_id=event.id,
            contact_id=contact.id,
            event_participant_id=participant.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=30000,
            discount_cents=0,
            tax_cents=0,
            total_cents=30000,
            terms="net 0",
        )
        db.add(quote)
        db.flush()
        _created_quote_ids.append(quote.id)

        invoice = Invoice(
            event_id=event.id,
            contact_id=contact.id,
            event_participant_id=participant.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=30000,
            discount_cents=0,
            tax_cents=0,
            total_cents=30000,
            paid_to_date_cents=0,
            # chk_invoice_balance_consistent requires balance_cents to
            # equal total - paid_to_date for live invoices.
            balance_cents=30000,
        )
        db.add(invoice)
        db.flush()
        _created_invoice_ids.append(invoice.id)

        db.commit()
        return {
            "event_id": event.id,
            "participant_id": participant.id,
            "appointment_id": appt.id,
            "quote_id": quote.id,
            "invoice_id": invoice.id,
        }
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _created_invoice_ids:
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                {"ids": _created_invoice_ids},
            )
        if _created_quote_ids:
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": _created_quote_ids},
            )
        if _created_appointment_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appointment_ids},
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
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
        if _created_contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _created_contact_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    seed = _seed()

    # ---- Read-back: every dependent row points at the participant.
    db = SessionLocal()
    try:
        appt = db.get(Appointment, seed["appointment_id"])
        quote = db.get(Quote, seed["quote_id"])
        invoice = db.get(Invoice, seed["invoice_id"])
        assert appt.event_participant_id == seed["participant_id"], (
            appt.event_participant_id, seed
        )
        assert quote.event_participant_id == seed["participant_id"], (
            quote.event_participant_id, seed
        )
        assert invoice.event_participant_id == seed["participant_id"], (
            invoice.event_participant_id, seed
        )
        # The event link is unchanged — buyer journeys live UNDER the
        # shared event, not as duplicate events.
        assert appt.crm_event_id == seed["event_id"], appt.crm_event_id
        assert quote.event_id == seed["event_id"], quote.event_id
        assert invoice.event_id == seed["event_id"], invoice.event_id
    finally:
        db.close()

    # ---- ON DELETE SET NULL: deleting the participant nulls the FK on
    # every dependent row but leaves the rows themselves intact (so
    # commission / history / customer-facing artifacts are preserved
    # even if the participant relationship is later retracted).
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM event_participants WHERE id = :p"),
            {"p": seed["participant_id"]},
        )
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        appt = db.get(Appointment, seed["appointment_id"])
        quote = db.get(Quote, seed["quote_id"])
        invoice = db.get(Invoice, seed["invoice_id"])
        assert appt is not None, "appointment row was deleted unexpectedly"
        assert quote is not None, "quote row was deleted unexpectedly"
        assert invoice is not None, "invoice row was deleted unexpectedly"
        assert appt.event_participant_id is None, appt.event_participant_id
        assert quote.event_participant_id is None, quote.event_participant_id
        assert invoice.event_participant_id is None, (
            invoice.event_participant_id
        )
        # Event linkage still intact.
        assert appt.crm_event_id == seed["event_id"], appt.crm_event_id
        assert quote.event_id == seed["event_id"], quote.event_id
        assert invoice.event_id == seed["event_id"], invoice.event_id
    finally:
        db.close()

    # Participant ID is gone from the bookkeeping list — the cleanup
    # already deleted it.
    _created_participant_ids.clear()

    print("event_participant_fk smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
