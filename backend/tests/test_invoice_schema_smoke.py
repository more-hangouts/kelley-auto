"""Schema smoke tests for Phase 1 of the invoicing plan.

Exercises every CHECK constraint, every UNIQUE constraint, and the
numbering_state row-lock semantics that Phase 2's invoice_service will
build on top of. No services or routers exist yet at this phase, so the
tests use SessionLocal + raw SQL directly.

Per the [validate-schema-with-real-INSERTs](docs/INVOICING_PHASES.md)
standing rule, every constraint is provoked with a real INSERT/UPDATE
that must fail.

This file runs as a script (`venv/bin/python tests/test_invoice_schema_smoke.py`).
The internal helpers are named `check_*` rather than `test_*` so pytest
does not collect them as parameterless tests; running pytest broadly
across `tests/` should not flag this file.
"""

import os
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

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

from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.exc import DataError, IntegrityError  # noqa: E402

from database.connection import SessionLocal, engine  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    InvoiceInvitation,
    InvoiceLineItem,
)


def _seed_event():
    db = SessionLocal()
    try:
        contact = Contact(
            display_name="Invoice Schema Smoke",
            phone="(210) 555-1212",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name="Schema Smoke Quince",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        return contact.id, event.id
    finally:
        db.close()


def _cleanup(contact_id, event_id):
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM invoice_invitations WHERE contact_id = :cid"),
            {"cid": contact_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoices WHERE event_id = :eid OR contact_id = :cid"
            ),
            {"eid": event_id, "cid": contact_id},
        )
        db.execute(sql_text("DELETE FROM events WHERE id = :id"), {"id": event_id})
        db.execute(sql_text("DELETE FROM contacts WHERE id = :id"), {"id": contact_id})
        db.commit()
    finally:
        db.close()


def _expect_integrity(db, label, constraint_substring, action=None):
    """Provoke an IntegrityError matching one of the accepted constraint
    markers.

    `constraint_substring` may be a string or a tuple of strings. When a
    tuple, any one of them appearing in the error message counts as a pass —
    used when more than one CHECK validly fires for the same bad row and
    Postgres deterministically picks one to report.

    Two call shapes:
      - `_expect_integrity(db, label, marker)` — assumes the caller already
        staged the bad row via `db.add(...)`; we call `db.commit()` and
        expect it to raise.
      - `_expect_integrity(db, label, marker, action=callable)` — the
        callable runs and is expected to raise itself (used for direct
        `db.execute(sql_text(INSERT...))` paths where Postgres rejects at
        statement time, not at commit).
    """
    markers = (
        (constraint_substring,)
        if isinstance(constraint_substring, str)
        else tuple(constraint_substring)
    )

    def _check(exc):
        if not any(m in str(exc) for m in markers):
            raise AssertionError(
                f"{label}: expected one of {markers!r} in error, got: {exc}"
            ) from exc

    if action is not None:
        try:
            action()
        except IntegrityError as exc:
            db.rollback()
            _check(exc)
            return
        try:
            db.commit()
            raise AssertionError(f"{label}: expected rejection, all clean")
        except IntegrityError as exc:
            db.rollback()
            _check(exc)
            return

    try:
        db.commit()
        raise AssertionError(f"{label}: expected rejection, commit succeeded")
    except IntegrityError as exc:
        db.rollback()
        _check(exc)


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------


def check_draft_invoice_with_line_item(contact_id, event_id):
    """draft has no invoice_number; totals match line math after a
    Phase 2-like recompute run by hand."""
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
        )
        db.add(invoice)
        db.flush()
        assert invoice.invoice_number is None, "draft should have no number"

        line = InvoiceLineItem(
            invoice_id=invoice.id,
            sort_order=0,
            kind="product",
            description="Sample dress",
            quantity=1,
            unit_price_cents=125000,
            discount_cents=0,
            tax_rate=0.08250,
            tax_name="TX Sales",
            line_subtotal_cents=125000,
            line_tax_cents=10313,
            line_total_cents=135313,
        )
        db.add(line)
        db.flush()

        invoice.subtotal_cents = 125000
        invoice.tax_cents = 10313
        invoice.discount_cents = 0
        invoice.total_cents = 135313
        invoice.paid_to_date_cents = 0
        invoice.balance_cents = 135313
        db.commit()

        db.refresh(invoice)
        assert invoice.total_cents == 135313
        assert invoice.balance_cents == 135313
        return invoice.id
    finally:
        db.close()


def check_invalid_status_rejected(contact_id, event_id):
    """chk_invoice_status: status='wat' rejected.

    Provide an invoice_number so we exercise the status CHECK in isolation
    rather than tripping chk_invoice_number_when_not_draft first."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "invalid status",
            "chk_invoice_status",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoices (event_id, contact_id, status, invoice_number) "
                    "VALUES (:e, :c, 'wat', :n)"
                ),
                {
                    "e": event_id,
                    "c": contact_id,
                    "n": f"INV-TEST-STATUS-{uuid.uuid4().hex[:8]}",
                },
            ),
        )
    finally:
        db.close()


def check_send_without_number_rejected(contact_id, event_id):
    """chk_invoice_number_when_not_draft: status='sent' with NULL number."""
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
        )
        db.add(invoice)
        db.commit()
        invoice_id = invoice.id

        invoice.status = "sent"
        _expect_integrity(
            db, "send without number", "chk_invoice_number_when_not_draft"
        )

        db.execute(sql_text("DELETE FROM invoices WHERE id = :id"), {"id": invoice_id})
        db.commit()
    finally:
        db.close()


def check_paid_exceeds_total_rejected(contact_id, event_id):
    """chk_invoice_paid_le_total + chk_invoice_balance_consistent +
    chk_invoice_amounts_nonneg jointly enforce the paid-le-total floor.
    Any of the three is enough to catch this; we assert at least one
    trips."""
    db = SessionLocal()
    try:
        bad = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
            total_cents=10000,
            paid_to_date_cents=15000,
            balance_cents=-5000,  # forced to satisfy the consistency CHECK
        )
        db.add(bad)
        try:
            db.commit()
            raise AssertionError("paid > total should have been rejected")
        except IntegrityError as exc:
            db.rollback()
            if not any(
                marker in str(exc)
                for marker in (
                    "chk_invoice_paid_le_total",
                    "chk_invoice_balance_consistent",
                    "chk_invoice_amounts_nonneg",
                )
            ):
                raise AssertionError(
                    f"unexpected error for paid>total: {exc}"
                ) from exc
    finally:
        db.close()


def check_balance_inconsistent_rejected(contact_id, event_id):
    """chk_invoice_balance_consistent: balance != total - paid_to_date."""
    db = SessionLocal()
    try:
        bad = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
            total_cents=10000,
            paid_to_date_cents=2000,
            balance_cents=9999,  # should be 8000
        )
        db.add(bad)
        _expect_integrity(
            db, "balance inconsistent", "chk_invoice_balance_consistent"
        )
    finally:
        db.close()


def check_revision_zero_rejected(contact_id, event_id):
    """chk_invoice_revision_pos: revision must be >= 1."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "revision=0",
            "chk_invoice_revision_pos",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoices (event_id, contact_id, revision) "
                    "VALUES (:e, :c, 0)"
                ),
                {"e": event_id, "c": contact_id},
            ),
        )
    finally:
        db.close()


def check_invoice_number_unique(contact_id, event_id):
    """invoice_number UNIQUE: two invoices with the same number rejected."""
    db = SessionLocal()
    try:
        first = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="sent",
            invoice_number="INV-TEST-DUP-1",
            issue_date=date.today(),
            sent_at=None,
        )
        db.add(first)
        db.commit()
        first_id = first.id

        try:
            dup = Invoice(
                event_id=event_id,
                contact_id=contact_id,
                status="sent",
                invoice_number="INV-TEST-DUP-1",
                issue_date=date.today(),
            )
            db.add(dup)
            try:
                db.commit()
                raise AssertionError(
                    "duplicate invoice_number should have been rejected"
                )
            except IntegrityError as exc:
                db.rollback()
                # Postgres surfaces UNIQUE violations via the index name.
                assert "invoices_invoice_number_key" in str(exc) or "unique" in str(exc).lower(), str(exc)
        finally:
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = :id"), {"id": first_id}
            )
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# invoice_line_items
# ---------------------------------------------------------------------------


def check_invalid_line_kind_rejected(invoice_id):
    """chk_line_kind: kind='not_a_kind' rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "invalid line kind",
            "chk_line_kind",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_line_items "
                    "(invoice_id, kind, description, quantity, unit_price_cents, "
                    "line_subtotal_cents, line_tax_cents, line_total_cents) "
                    "VALUES (:i, 'not_a_kind', 'x', 1, 100, 100, 0, 100)"
                ),
                {"i": invoice_id},
            ),
        )
    finally:
        db.close()


def check_zero_quantity_line_rejected(invoice_id):
    """chk_line_quantity_pos: quantity = 0 rejected."""
    db = SessionLocal()
    try:
        bad = InvoiceLineItem(
            invoice_id=invoice_id,
            sort_order=99,
            kind="product",
            description="Zero qty line",
            quantity=0,
            unit_price_cents=10000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=0,
            line_tax_cents=0,
            line_total_cents=0,
        )
        db.add(bad)
        _expect_integrity(db, "quantity=0 line", "chk_line_quantity_pos")
    finally:
        db.close()


def check_negative_unit_price_rejected(invoice_id):
    """chk_line_unit_price_nonneg: unit_price_cents = -1 rejected.

    A negative unit price with discount=0 also trips
    chk_line_discount_le_subtotal (since 0 is not <= 1 * -1). Both
    constraints validly enforce the floor; Postgres picks one to report.
    Accept either."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "negative unit price",
            ("chk_line_unit_price_nonneg", "chk_line_discount_le_subtotal"),
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_line_items "
                    "(invoice_id, kind, description, quantity, unit_price_cents, "
                    "line_subtotal_cents, line_tax_cents, line_total_cents) "
                    "VALUES (:i, 'product', 'x', 1, -1, 0, 0, 0)"
                ),
                {"i": invoice_id},
            ),
        )
    finally:
        db.close()


def check_discount_over_subtotal_rejected(invoice_id):
    """chk_line_discount_le_subtotal: discount > qty*price rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "discount over subtotal",
            "chk_line_discount_le_subtotal",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_line_items "
                    "(invoice_id, kind, description, quantity, unit_price_cents, "
                    "discount_cents, line_subtotal_cents, line_tax_cents, "
                    "line_total_cents) "
                    "VALUES (:i, 'product', 'x', 1, 1000, 5000, 0, 0, 0)"
                ),
                {"i": invoice_id},
            ),
        )
    finally:
        db.close()


def check_tax_rate_one_or_more_rejected(invoice_id):
    """chk_line_tax_rate_range: tax_rate >= 1 rejected."""
    db = SessionLocal()
    try:
        # Inserting via ORM with tax_rate=1.0 hits Postgres NUMERIC bounds
        # before the CHECK because the column type is NUMERIC(7,5) which is
        # bounded at 99.99999. So 1.0 is in-range for the type and we get
        # to test the CHECK constraint, not the type cast.
        bad = InvoiceLineItem(
            invoice_id=invoice_id,
            sort_order=98,
            kind="product",
            description="Bad tax",
            quantity=1,
            unit_price_cents=100,
            discount_cents=0,
            tax_rate=1,
            line_subtotal_cents=100,
            line_tax_cents=100,
            line_total_cents=200,
        )
        db.add(bad)
        _expect_integrity(db, "tax_rate=1", "chk_line_tax_rate_range")
    finally:
        db.close()


def check_negative_tax_rate_rejected(invoice_id):
    """chk_line_tax_rate_range: tax_rate < 0 rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "tax_rate=-0.01",
            "chk_line_tax_rate_range",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_line_items "
                    "(invoice_id, kind, description, quantity, unit_price_cents, "
                    "tax_rate, line_subtotal_cents, line_tax_cents, line_total_cents) "
                    "VALUES (:i, 'product', 'x', 1, 100, -0.01, 100, 0, 100)"
                ),
                {"i": invoice_id},
            ),
        )
    finally:
        db.close()


def check_negative_line_money_rejected(invoice_id):
    """Migration 024 follow-up: discount, subtotal, tax, total all >= 0.

    Before migration 024, all four of these were silently accepted. Each one
    is provoked individually so a regression in a future ALTER would be
    obvious in the failing label."""
    cases = [
        (
            "negative discount",
            "chk_line_discount_nonneg",
            "INSERT INTO invoice_line_items "
            "(invoice_id, kind, description, quantity, unit_price_cents, "
            "discount_cents, line_subtotal_cents, line_tax_cents, line_total_cents) "
            "VALUES (:i, 'product', 'x', 1, 100, -1, 100, 0, 100)",
        ),
        (
            "negative subtotal",
            "chk_line_subtotal_nonneg",
            "INSERT INTO invoice_line_items "
            "(invoice_id, kind, description, quantity, unit_price_cents, "
            "line_subtotal_cents, line_tax_cents, line_total_cents) "
            "VALUES (:i, 'product', 'x', 1, 100, -1, 0, 0)",
        ),
        (
            "negative tax",
            "chk_line_tax_nonneg",
            "INSERT INTO invoice_line_items "
            "(invoice_id, kind, description, quantity, unit_price_cents, "
            "line_subtotal_cents, line_tax_cents, line_total_cents) "
            "VALUES (:i, 'product', 'x', 1, 100, 100, -1, 100)",
        ),
        (
            "negative line total",
            "chk_line_total_nonneg",
            "INSERT INTO invoice_line_items "
            "(invoice_id, kind, description, quantity, unit_price_cents, "
            "line_subtotal_cents, line_tax_cents, line_total_cents) "
            "VALUES (:i, 'product', 'x', 1, 100, 100, 0, -1)",
        ),
    ]
    for label, marker, sql in cases:
        db = SessionLocal()
        try:
            _expect_integrity(
                db,
                label,
                marker,
                action=lambda db=db, sql=sql: db.execute(
                    sql_text(sql), {"i": invoice_id}
                ),
            )
        finally:
            db.close()


# ---------------------------------------------------------------------------
# invoice_installments
# ---------------------------------------------------------------------------


def check_two_installments_sum(invoice_id):
    """Insert two rows summing to total. Sum check is the service's job
    in Phase 2; this just confirms the rows persist and add up."""
    db = SessionLocal()
    try:
        db.add_all(
            [
                InvoiceInstallment(
                    invoice_id=invoice_id,
                    sort_order=0,
                    label="Deposit",
                    amount_cents=20000,
                    due_date=date.today() + timedelta(days=14),
                ),
                InvoiceInstallment(
                    invoice_id=invoice_id,
                    sort_order=1,
                    label="Balance",
                    amount_cents=115313,
                    due_date=date.today() + timedelta(days=120),
                ),
            ]
        )
        db.commit()

        total = db.execute(
            sql_text(
                "SELECT SUM(amount_cents) FROM invoice_installments "
                "WHERE invoice_id = :id"
            ),
            {"id": invoice_id},
        ).scalar()
        assert total == 135313, f"installment sum {total} != invoice total"
    finally:
        db.close()


def check_zero_installment_rejected(invoice_id):
    """chk_installment_amount_pos: amount_cents = 0 rejected."""
    db = SessionLocal()
    try:
        bad = InvoiceInstallment(
            invoice_id=invoice_id,
            sort_order=99,
            label="Zero",
            amount_cents=0,
            due_date=date.today(),
        )
        db.add(bad)
        _expect_integrity(db, "zero installment", "chk_installment_amount_pos")
    finally:
        db.close()


def check_negative_installment_rejected(invoice_id):
    """chk_installment_amount_pos: amount_cents = -1 rejected."""
    db = SessionLocal()
    try:
        bad = InvoiceInstallment(
            invoice_id=invoice_id,
            sort_order=98,
            label="Neg",
            amount_cents=-1,
            due_date=date.today(),
        )
        db.add(bad)
        _expect_integrity(db, "negative installment", "chk_installment_amount_pos")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# invoice_invitations
# ---------------------------------------------------------------------------


def check_duplicate_invitation_rejected(invoice_id, contact_id):
    """uq_invitation_invoice_contact: two rows with the same (invoice, contact)."""
    db = SessionLocal()
    try:
        first = InvoiceInvitation(
            invoice_id=invoice_id,
            contact_id=contact_id,
            public_key=uuid.uuid4().hex,
        )
        db.add(first)
        db.commit()

        dup = InvoiceInvitation(
            invoice_id=invoice_id,
            contact_id=contact_id,
            public_key=uuid.uuid4().hex,
        )
        db.add(dup)
        _expect_integrity(
            db, "duplicate invitation", "uq_invitation_invoice_contact"
        )
    finally:
        db.close()


def check_duplicate_public_key_rejected(invoice_id, contact_id, second_contact_id):
    """public_key UNIQUE: a second invitation with the same key rejected
    even when (invoice, contact) is different."""
    db = SessionLocal()
    shared_key = uuid.uuid4().hex
    a_id = None
    try:
        a = InvoiceInvitation(
            invoice_id=invoice_id,
            contact_id=second_contact_id,
            public_key=shared_key,
        )
        db.add(a)
        db.commit()
        a_id = a.id

        # Same key, different (invoice, contact) — the composite UNIQUE is
        # satisfied; only public_key UNIQUE catches this.
        _expect_integrity(
            db,
            "duplicate public_key",
            ("invoice_invitations_public_key_key", "unique"),
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_invitations (invoice_id, contact_id, public_key) "
                    "VALUES (:i, :c, :k)"
                ),
                {"i": invoice_id, "c": contact_id, "k": shared_key},
            ),
        )
    finally:
        if a_id is not None:
            db.execute(
                sql_text("DELETE FROM invoice_invitations WHERE id = :id"),
                {"id": a_id},
            )
            db.commit()
        db.close()


def check_negative_view_count_rejected(invoice_id, contact_id):
    """chk_invitation_view_count_nonneg: view_count = -1 rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "negative view_count",
            "chk_invitation_view_count_nonneg",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO invoice_invitations "
                    "(invoice_id, contact_id, public_key, view_count) "
                    "VALUES (:i, :c, :k, -1)"
                ),
                {"i": invoice_id, "c": contact_id, "k": uuid.uuid4().hex},
            ),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# numbering_state
# ---------------------------------------------------------------------------


def check_numbering_singleton():
    """chk_numbering_singleton: id != 1 rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "second numbering row",
            "chk_numbering_singleton",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO numbering_state (id, invoice_year, quote_year) "
                    "VALUES (2, 2026, 2026)"
                )
            ),
        )
    finally:
        db.close()


def check_numbering_seq_nonneg():
    """chk_numbering_seq_nonneg: invoice_seq cannot go below zero."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "invoice_seq=-1",
            "chk_numbering_seq_nonneg",
            action=lambda: db.execute(
                sql_text(
                    "UPDATE numbering_state SET invoice_seq = -1 WHERE id = 1"
                )
            ),
        )
    finally:
        db.close()


def check_concurrent_numbering_state():
    """Two concurrent SELECT ... FOR UPDATE bumps produce sequential
    invoice_seq values with no duplicates. Mirrors what Phase 2's
    _assign_invoice_number will do."""
    results = []
    barrier = threading.Barrier(2)

    def bump():
        with engine.begin() as conn:
            barrier.wait()
            row = conn.execute(
                sql_text(
                    "SELECT invoice_seq FROM numbering_state WHERE id = 1 "
                    "FOR UPDATE"
                )
            ).one()
            current = row[0]
            time.sleep(0.05)
            new_seq = current + 1
            conn.execute(
                sql_text(
                    "UPDATE numbering_state SET invoice_seq = :s, "
                    "updated_at = NOW() WHERE id = 1"
                ),
                {"s": new_seq},
            )
            results.append(new_seq)

    db = SessionLocal()
    try:
        starting = db.execute(
            sql_text("SELECT invoice_seq FROM numbering_state WHERE id = 1")
        ).scalar()
    finally:
        db.close()

    t1 = threading.Thread(target=bump)
    t2 = threading.Thread(target=bump)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [starting + 1, starting + 2], (
        f"concurrent bumps produced {sorted(results)!r}, expected "
        f"{[starting + 1, starting + 2]!r}"
    )

    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET invoice_seq = :s WHERE id = 1"
            ),
            {"s": starting},
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# business_profile
# ---------------------------------------------------------------------------


def check_business_profile_singleton():
    """chk_business_profile_singleton: only id=1 allowed."""
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text("SELECT id, legal_name FROM business_profile")
        ).all()
        assert len(rows) == 1, f"expected one row, got {rows}"
        assert rows[0][0] == 1
        assert rows[0][1] == "Bellas XV"

        _expect_integrity(
            db,
            "second business_profile row",
            "chk_business_profile_singleton",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO business_profile (id, legal_name) "
                    "VALUES (2, 'Other')"
                )
            ),
        )
    finally:
        db.close()


def check_business_profile_tax_rate_range():
    """chk_business_profile_tax_rate_range: default_tax_rate >= 1 rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "default_tax_rate=1.0",
            "chk_business_profile_tax_rate_range",
            action=lambda: db.execute(
                sql_text(
                    "UPDATE business_profile SET default_tax_rate = 1.0 WHERE id = 1"
                )
            ),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# quotes Phase 5 (migrations 027–029)
# ---------------------------------------------------------------------------


def check_quote_status_enum(contact_id, event_id):
    """chk_quote_status: bogus status rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "bogus quote status",
            "chk_quote_status",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO quotes (event_id, contact_id, status, quote_number) "
                    "VALUES (:e, :c, 'wat', :n)"
                ),
                {
                    "e": event_id,
                    "c": contact_id,
                    "n": f"Q-TEST-WAT-{uuid.uuid4().hex[:8]}",
                },
            ),
        )
    finally:
        db.close()


def check_quote_number_required_when_not_draft(contact_id, event_id):
    """chk_quote_number_when_not_draft."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "sent quote without number",
            "chk_quote_number_when_not_draft",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO quotes (event_id, contact_id, status) "
                    "VALUES (:e, :c, 'sent')"
                ),
                {"e": event_id, "c": contact_id},
            ),
        )
    finally:
        db.close()


def check_quote_signature_pairing(contact_id, event_id):
    """chk_quote_signature_paired: base64 alone (no signed_at) rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "signature half-pair",
            "chk_quote_signature_paired",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO quotes (event_id, contact_id, status, signature_base64) "
                    "VALUES (:e, :c, 'draft', 'aGVsbG8=')"
                ),
                {"e": event_id, "c": contact_id},
            ),
        )
    finally:
        db.close()


def check_quote_approved_requires_signature(contact_id, event_id):
    """chk_quote_approved_has_signature: status='approved' without signed_at rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "approved sans signature",
            "chk_quote_approved_has_signature",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO quotes (event_id, contact_id, status, quote_number) "
                    "VALUES (:e, :c, 'approved', :n)"
                ),
                {
                    "e": event_id,
                    "c": contact_id,
                    "n": f"Q-TEST-NOSIG-{uuid.uuid4().hex[:8]}",
                },
            ),
        )
    finally:
        db.close()


def check_quote_converted_consistency(contact_id, event_id):
    """chk_quote_converted_consistent: status='converted' iff
    converted_invoice_id IS NOT NULL. Both directions rejected."""
    db = SessionLocal()
    try:
        # Make a real invoice we can point at.
        inv_id = db.execute(
            sql_text(
                "INSERT INTO invoices (event_id, contact_id) "
                "VALUES (:e, :c) RETURNING id"
            ),
            {"e": event_id, "c": contact_id},
        ).scalar()
        db.commit()
        try:
            # converted status with no FK -> rejected
            _expect_integrity(
                db,
                "converted without FK",
                "chk_quote_converted_consistent",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO quotes (event_id, contact_id, status, quote_number) "
                        "VALUES (:e, :c, 'converted', :n)"
                    ),
                    {
                        "e": event_id,
                        "c": contact_id,
                        "n": f"Q-TEST-CNVNOFK-{uuid.uuid4().hex[:8]}",
                    },
                ),
            )
            # draft with FK -> rejected
            _expect_integrity(
                db,
                "non-converted with FK",
                "chk_quote_converted_consistent",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO quotes (event_id, contact_id, status, "
                        "                    converted_invoice_id) "
                        "VALUES (:e, :c, 'draft', :i)"
                    ),
                    {"e": event_id, "c": contact_id, "i": inv_id},
                ),
            )
        finally:
            db.execute(sql_text("DELETE FROM invoices WHERE id = :i"), {"i": inv_id})
            db.commit()
    finally:
        db.close()


def check_quote_line_negative_money(contact_id, event_id):
    """The four nonneg-money checks on quote_line_items, baked in from
    creation (mirrors the Phase 1 invoice_line_items + migration 024 set)."""
    db = SessionLocal()
    try:
        # Need a real quote to attach lines to.
        quote_id = db.execute(
            sql_text(
                "INSERT INTO quotes (event_id, contact_id, status) "
                "VALUES (:e, :c, 'draft') RETURNING id"
            ),
            {"e": event_id, "c": contact_id},
        ).scalar()
        db.commit()
        try:
            for col, marker in [
                ("discount_cents", "chk_quote_line_discount_nonneg"),
                ("line_subtotal_cents", "chk_quote_line_subtotal_nonneg"),
                ("line_tax_cents", "chk_quote_line_tax_nonneg"),
                ("line_total_cents", "chk_quote_line_total_nonneg"),
            ]:
                params = {
                    "quote_id": quote_id,
                    "kind": "product",
                    "description": "x",
                    "quantity": 1,
                    "unit_price_cents": 100,
                    "discount_cents": 0,
                    "tax_rate": 0,
                    "line_subtotal_cents": 100,
                    "line_tax_cents": 0,
                    "line_total_cents": 100,
                }
                params[col] = -1
                _expect_integrity(
                    db,
                    f"line {col} negative",
                    marker,
                    action=lambda p=params: db.execute(
                        sql_text(
                            "INSERT INTO quote_line_items "
                            "(quote_id, kind, description, quantity, "
                            " unit_price_cents, discount_cents, tax_rate, "
                            " line_subtotal_cents, line_tax_cents, line_total_cents) "
                            "VALUES (:quote_id, :kind, :description, :quantity, "
                            "        :unit_price_cents, :discount_cents, :tax_rate, "
                            "        :line_subtotal_cents, :line_tax_cents, "
                            "        :line_total_cents)"
                        ),
                        p,
                    ),
                )
        finally:
            db.execute(
                sql_text("DELETE FROM quote_line_items WHERE quote_id = :q"),
                {"q": quote_id},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = :q"), {"q": quote_id}
            )
            db.commit()
    finally:
        db.close()


def check_quote_invitation_unique(contact_id, event_id):
    """uq_quote_invitation_quote_contact: same (quote, contact) twice rejected."""
    db = SessionLocal()
    try:
        quote_id = db.execute(
            sql_text(
                "INSERT INTO quotes (event_id, contact_id, status) "
                "VALUES (:e, :c, 'draft') RETURNING id"
            ),
            {"e": event_id, "c": contact_id},
        ).scalar()
        db.commit()
        try:
            db.execute(
                sql_text(
                    "INSERT INTO quote_invitations (quote_id, contact_id, public_key) "
                    "VALUES (:q, :c, :k)"
                ),
                {
                    "q": quote_id,
                    "c": contact_id,
                    "k": f"phase5-{uuid.uuid4().hex[:16]}",
                },
            )
            db.commit()
            _expect_integrity(
                db,
                "duplicate (quote, contact) invitation",
                "uq_quote_invitation_quote_contact",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO quote_invitations "
                        "(quote_id, contact_id, public_key) "
                        "VALUES (:q, :c, :k)"
                    ),
                    {
                        "q": quote_id,
                        "c": contact_id,
                        "k": f"phase5-{uuid.uuid4().hex[:16]}",
                    },
                ),
            )
        finally:
            db.execute(
                sql_text("DELETE FROM quote_invitations WHERE quote_id = :q"),
                {"q": quote_id},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = :q"), {"q": quote_id}
            )
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# payments / payment_allocations / refund_events Phase 6 (migrations 030–032)
# ---------------------------------------------------------------------------


def _seed_invoice(db, contact_id, event_id, total=50000):
    """Inline a minimum-viable invoice the payment CHECKs can hang off."""
    return db.execute(
        sql_text(
            "INSERT INTO invoices (event_id, contact_id, total_cents, balance_cents) "
            "VALUES (:e, :c, :t, :t) RETURNING id"
        ),
        {"e": event_id, "c": contact_id, "t": total},
    ).scalar()


def check_payment_amount_pos(contact_id, event_id):
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "payment amount=0",
            "chk_payment_amount_pos",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                    "  unapplied_cents, method) "
                    "VALUES (:c, :n, 0, 0, 'cash')"
                ),
                {"c": contact_id, "n": f"PMT-T-{uuid.uuid4().hex[:8]}"},
            ),
        )
    finally:
        db.close()


def check_payment_amount_consistent(contact_id, event_id):
    """The amount = applied + refunded + unapplied invariant."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "amount != applied + refunded + unapplied",
            "chk_payment_amount_consistent",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                    "  applied_cents, unapplied_cents, refunded_cents, method) "
                    "VALUES (:c, :n, 10000, 5000, 4000, 0, 'cash')"
                ),
                {"c": contact_id, "n": f"PMT-T-{uuid.uuid4().hex[:8]}"},
            ),
        )
    finally:
        db.close()


def check_payment_method_enum(contact_id, event_id):
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "bogus method",
            "chk_payment_method",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                    "  unapplied_cents, method) "
                    "VALUES (:c, :n, 100, 100, 'bitcoin')"
                ),
                {"c": contact_id, "n": f"PMT-T-{uuid.uuid4().hex[:8]}"},
            ),
        )
    finally:
        db.close()


def check_payment_status_enum(contact_id, event_id):
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "bogus payment status",
            "chk_payment_status",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                    "  unapplied_cents, method, status) "
                    "VALUES (:c, :n, 100, 100, 'cash', 'maybe')"
                ),
                {"c": contact_id, "n": f"PMT-T-{uuid.uuid4().hex[:8]}"},
            ),
        )
    finally:
        db.close()


def check_payment_number_when_not_pending(contact_id, event_id):
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "completed without payment_number",
            "chk_payment_number_when_not_pending",
            action=lambda: db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, amount_cents, unapplied_cents, "
                    "  method, status) "
                    "VALUES (:c, 100, 100, 'cash', 'completed')"
                ),
                {"c": contact_id},
            ),
        )
    finally:
        db.close()


def check_payment_alloc_invariants(contact_id, event_id):
    """chk_alloc_applied_pos + chk_alloc_refunded_le_applied + UNIQUE
    (payment, invoice) + ON DELETE RESTRICT on invoice from
    payment_allocations."""
    db = SessionLocal()
    try:
        invoice_id = _seed_invoice(db, contact_id, event_id, total=50000)
        db.commit()
        try:
            payment_id = db.execute(
                sql_text(
                    "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                    "  applied_cents, unapplied_cents, method) "
                    "VALUES (:c, :n, 5000, 5000, 0, 'cash') RETURNING id"
                ),
                {"c": contact_id, "n": f"PMT-T-{uuid.uuid4().hex[:8]}"},
            ).scalar()
            db.commit()
            alloc_id = db.execute(
                sql_text(
                    "INSERT INTO payment_allocations (payment_id, invoice_id, applied_cents) "
                    "VALUES (:p, :i, 5000) RETURNING id"
                ),
                {"p": payment_id, "i": invoice_id},
            ).scalar()
            db.commit()

            # zero applied
            _expect_integrity(
                db,
                "alloc applied=0",
                "chk_alloc_applied_pos",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO payment_allocations (payment_id, invoice_id, applied_cents) "
                        "VALUES (:p, :i, 0)"
                    ),
                    {"p": payment_id, "i": invoice_id},
                ),
            )

            # refund > applied
            other_invoice = _seed_invoice(db, contact_id, event_id, total=1000)
            db.commit()
            _expect_integrity(
                db,
                "alloc refund > applied",
                "chk_alloc_refunded_le_applied",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO payment_allocations (payment_id, invoice_id, "
                        "  applied_cents, refunded_cents) "
                        "VALUES (:p, :i, 100, 200)"
                    ),
                    {"p": payment_id, "i": other_invoice},
                ),
            )
            db.execute(sql_text("DELETE FROM invoices WHERE id = :i"), {"i": other_invoice})
            db.commit()

            # duplicate (payment, invoice)
            _expect_integrity(
                db,
                "duplicate (payment, invoice) alloc",
                "uq_payment_alloc_payment_invoice",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO payment_allocations (payment_id, invoice_id, applied_cents) "
                        "VALUES (:p, :i, 100)"
                    ),
                    {"p": payment_id, "i": invoice_id},
                ),
            )

            # ON DELETE RESTRICT on invoice
            _expect_integrity(
                db,
                "invoice DELETE blocked while alloc exists",
                "payment_allocations",
                action=lambda: db.execute(
                    sql_text("DELETE FROM invoices WHERE id = :i"), {"i": invoice_id}
                ),
            )

            db.execute(
                sql_text("DELETE FROM payment_allocations WHERE id = :a"),
                {"a": alloc_id},
            )
            db.execute(sql_text("DELETE FROM payments WHERE id = :p"), {"p": payment_id})
        finally:
            db.execute(sql_text("DELETE FROM invoices WHERE id = :i"), {"i": invoice_id})
            db.commit()
    finally:
        db.close()


def check_refund_event_invariants(contact_id, event_id):
    """chk_refund_amount_pos, chk_refund_from_unapplied_le_amount,
    chk_refund_method, plus ON DELETE RESTRICT on payment_id."""
    db = SessionLocal()
    try:
        payment_id = db.execute(
            sql_text(
                "INSERT INTO payments (contact_id, payment_number, amount_cents, "
                "  unapplied_cents, method) "
                "VALUES (:c, :n, 1000, 1000, 'cash') RETURNING id"
            ),
            {"c": contact_id, "n": f"PMT-RT-{uuid.uuid4().hex[:8]}"},
        ).scalar()
        db.commit()
        try:
            refund_id = db.execute(
                sql_text(
                    "INSERT INTO refund_events (payment_id, amount_cents, "
                    "  from_unapplied_cents, refund_method) "
                    "VALUES (:p, 100, 50, 'cash') RETURNING id"
                ),
                {"p": payment_id},
            ).scalar()
            db.commit()

            _expect_integrity(
                db,
                "refund amount=0",
                "chk_refund_amount_pos",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO refund_events (payment_id, amount_cents, refund_method) "
                        "VALUES (:p, 0, 'cash')"
                    ),
                    {"p": payment_id},
                ),
            )

            _expect_integrity(
                db,
                "refund from_unapplied > amount",
                "chk_refund_from_unapplied_le_amount",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO refund_events (payment_id, amount_cents, "
                        "  from_unapplied_cents, refund_method) "
                        "VALUES (:p, 100, 200, 'cash')"
                    ),
                    {"p": payment_id},
                ),
            )

            _expect_integrity(
                db,
                "refund bogus method",
                "chk_refund_method",
                action=lambda: db.execute(
                    sql_text(
                        "INSERT INTO refund_events (payment_id, amount_cents, refund_method) "
                        "VALUES (:p, 100, 'bitcoin')"
                    ),
                    {"p": payment_id},
                ),
            )

            # ON DELETE RESTRICT on payment via refund_events
            _expect_integrity(
                db,
                "payment DELETE blocked while refund_event exists",
                "refund_events",
                action=lambda: db.execute(
                    sql_text("DELETE FROM payments WHERE id = :p"), {"p": payment_id}
                ),
            )

            db.execute(
                sql_text("DELETE FROM refund_events WHERE id = :r"), {"r": refund_id}
            )
            db.commit()
        finally:
            db.execute(sql_text("DELETE FROM payments WHERE id = :p"), {"p": payment_id})
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# event_documents Phase 4a split (migration 025)
# ---------------------------------------------------------------------------


_DOC_INSERT_SQL = sql_text(
    """
    INSERT INTO event_documents
        (event_id, uploaded_by_user_id, kind, filename, content_type,
         byte_size, storage_key, label,
         invoice_amount_cents, invoice_status, invoice_issued_at, invoice_paid_at,
         linked_invoice_id)
    VALUES
        (:event_id, :user_id, :kind, :filename, :content_type,
         :byte_size, :storage_key, :label,
         :invoice_amount_cents, :invoice_status, :invoice_issued_at, :invoice_paid_at,
         :linked_invoice_id)
    RETURNING id
    """
)


def _doc_params(event_id, user_id, **overrides):
    p = dict(
        event_id=event_id,
        user_id=user_id,
        kind="document",
        filename="x.pdf",
        content_type="application/pdf",
        byte_size=10,
        storage_key=f"smoke/{uuid.uuid4().hex}.pdf",
        label=None,
        invoice_amount_cents=None,
        invoice_status=None,
        invoice_issued_at=None,
        invoice_paid_at=None,
        linked_invoice_id=None,
    )
    p.update(overrides)
    return p


def _make_canonical_invoice(contact_id, event_id):
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
        )
        db.add(invoice)
        db.commit()
        return invoice.id
    finally:
        db.close()


def check_document_kinds_4a(contact_id, event_id, user_id):
    """Migration 025: kind enum now accepts 'external_invoice', still
    accepts legacy 'invoice', still rejects bogus values."""
    db = SessionLocal()
    inserted = []
    try:
        for kind in ("document", "invoice", "external_invoice"):
            row = db.execute(
                _DOC_INSERT_SQL,
                _doc_params(
                    event_id,
                    user_id,
                    kind=kind,
                    invoice_amount_cents=1500 if kind != "document" else None,
                    invoice_status="sent" if kind != "document" else None,
                ),
            ).first()
            inserted.append(row.id)
        db.commit()

        _expect_integrity(
            db,
            "bogus document kind rejected",
            "chk_event_documents_kind",
            action=lambda: db.execute(
                _DOC_INSERT_SQL,
                _doc_params(event_id, user_id, kind="bogus"),
            ),
        )
    finally:
        if inserted:
            db.execute(
                sql_text("DELETE FROM event_documents WHERE id = ANY(:ids)"),
                {"ids": inserted},
            )
            db.commit()
        db.close()


def check_invoice_fields_only_on_invoice_kinds_4a(contact_id, event_id, user_id):
    """chk_event_documents_invoice_fields_only_on_invoice: the four legacy
    invoice_* columns may be populated only on kind IN ('invoice',
    'external_invoice'). A plain 'document' row carrying invoice_amount_cents
    is still rejected."""
    db = SessionLocal()
    try:
        _expect_integrity(
            db,
            "money on plain document rejected",
            "chk_event_documents_invoice_fields_only_on_invoice",
            action=lambda: db.execute(
                _DOC_INSERT_SQL,
                _doc_params(
                    event_id,
                    user_id,
                    kind="document",
                    invoice_amount_cents=999,
                ),
            ),
        )
    finally:
        db.close()


def check_linked_invoice_id_only_on_external_invoice_4a(
    contact_id, event_id, user_id
):
    """chk_event_documents_linked_invoice_only_on_external: the new
    linked_invoice_id may only be populated when kind='external_invoice'.
    Legacy 'invoice' and plain 'document' rows must keep it NULL."""
    invoice_id = _make_canonical_invoice(contact_id, event_id)
    db = SessionLocal()
    inserted_doc_ids = []
    try:
        # Happy path: external_invoice with linked_invoice_id.
        row = db.execute(
            _DOC_INSERT_SQL,
            _doc_params(
                event_id,
                user_id,
                kind="external_invoice",
                linked_invoice_id=invoice_id,
            ),
        ).first()
        inserted_doc_ids.append(row.id)
        db.commit()
        linked_doc_id = row.id

        # Reject linked_invoice_id on plain document.
        _expect_integrity(
            db,
            "linked_invoice_id on plain document rejected",
            "chk_event_documents_linked_invoice_only_on_external",
            action=lambda: db.execute(
                _DOC_INSERT_SQL,
                _doc_params(
                    event_id,
                    user_id,
                    kind="document",
                    linked_invoice_id=invoice_id,
                ),
            ),
        )

        # Reject linked_invoice_id on legacy 'invoice' kind.
        _expect_integrity(
            db,
            "linked_invoice_id on legacy invoice rejected",
            "chk_event_documents_linked_invoice_only_on_external",
            action=lambda: db.execute(
                _DOC_INSERT_SQL,
                _doc_params(
                    event_id,
                    user_id,
                    kind="invoice",
                    invoice_amount_cents=10,
                    linked_invoice_id=invoice_id,
                ),
            ),
        )

        # ON DELETE SET NULL: deleting the canonical invoice nulls the FK
        # rather than cascading the document delete.
        db.execute(sql_text("DELETE FROM invoices WHERE id = :i"), {"i": invoice_id})
        db.commit()
        remaining = db.execute(
            sql_text(
                "SELECT linked_invoice_id FROM event_documents WHERE id = :id"
            ),
            {"id": linked_doc_id},
        ).scalar()
        assert remaining is None, (
            f"expected linked_invoice_id NULL after invoice delete, got {remaining}"
        )
    finally:
        if inserted_doc_ids:
            db.execute(
                sql_text("DELETE FROM event_documents WHERE id = ANY(:ids)"),
                {"ids": inserted_doc_ids},
            )
            db.commit()
        db.close()


def check_legacy_migration_run_id_handle(contact_id, event_id):
    """Phase 4b rollback handle: select/delete by legacy_migration_run_id."""
    run_id = uuid.uuid4()
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            status="draft",
            issue_date=date.today(),
            legacy_migration_run_id=run_id,
        )
        db.add(invoice)
        db.commit()
        invoice_id = invoice.id

        rows = db.execute(
            sql_text(
                "SELECT id FROM invoices WHERE legacy_migration_run_id = :r"
            ),
            {"r": str(run_id)},
        ).all()
        assert [r[0] for r in rows] == [invoice_id], rows

        deleted = db.execute(
            sql_text(
                "DELETE FROM invoices WHERE legacy_migration_run_id = :r RETURNING id"
            ),
            {"r": str(run_id)},
        ).all()
        db.commit()
        assert [r[0] for r in deleted] == [invoice_id], deleted
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    contact_id, event_id = _seed_event()

    # A second contact for the duplicate-public_key test, plus a user id
    # the Phase 4a event_documents checks can hang their uploaded_by_user_id
    # off of.
    db = SessionLocal()
    try:
        second = Contact(
            display_name="Schema Smoke Second",
            phone="(210) 555-1213",
        )
        db.add(second)
        db.commit()
        second_contact_id = second.id

        user_id = db.execute(
            sql_text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).scalar()
        assert user_id is not None, "Phase 4a checks need at least one user row"
    finally:
        db.close()

    try:
        # invoices
        invoice_id = check_draft_invoice_with_line_item(contact_id, event_id)
        print("draft invoice + one line item ok")
        check_invalid_status_rejected(contact_id, event_id)
        print("invalid invoice status rejected ok")
        check_send_without_number_rejected(contact_id, event_id)
        print("send without invoice_number rejected ok")
        check_paid_exceeds_total_rejected(contact_id, event_id)
        print("paid > total rejected ok")
        check_balance_inconsistent_rejected(contact_id, event_id)
        print("balance inconsistent rejected ok")
        check_revision_zero_rejected(contact_id, event_id)
        print("revision=0 rejected ok")
        check_invoice_number_unique(contact_id, event_id)
        print("duplicate invoice_number rejected ok")

        # line items
        check_invalid_line_kind_rejected(invoice_id)
        print("invalid line kind rejected ok")
        check_zero_quantity_line_rejected(invoice_id)
        print("zero-quantity line rejected ok")
        check_negative_unit_price_rejected(invoice_id)
        print("negative unit price rejected ok")
        check_discount_over_subtotal_rejected(invoice_id)
        print("discount over subtotal rejected ok")
        check_tax_rate_one_or_more_rejected(invoice_id)
        print("tax_rate>=1 rejected ok")
        check_negative_tax_rate_rejected(invoice_id)
        print("negative tax_rate rejected ok")
        check_negative_line_money_rejected(invoice_id)
        print("negative line money (discount/subtotal/tax/total) rejected ok")

        # installments
        check_two_installments_sum(invoice_id)
        print("two-installment sum ok")
        check_zero_installment_rejected(invoice_id)
        print("zero installment rejected ok")
        check_negative_installment_rejected(invoice_id)
        print("negative installment rejected ok")

        # invitations
        check_duplicate_invitation_rejected(invoice_id, contact_id)
        print("duplicate (invoice, contact) invitation rejected ok")
        check_duplicate_public_key_rejected(invoice_id, contact_id, second_contact_id)
        print("duplicate public_key rejected ok")
        check_negative_view_count_rejected(invoice_id, contact_id)
        print("negative view_count rejected ok")

        # numbering
        check_numbering_singleton()
        print("numbering_state singleton CHECK ok")
        check_numbering_seq_nonneg()
        print("numbering_state seq nonneg CHECK ok")
        check_concurrent_numbering_state()
        print("concurrent SELECT ... FOR UPDATE sequential ok")

        # business profile
        check_business_profile_singleton()
        print("business_profile singleton CHECK ok")
        check_business_profile_tax_rate_range()
        print("business_profile tax_rate range CHECK ok")

        # quotes Phase 5 (migrations 027–029)
        check_quote_status_enum(contact_id, event_id)
        print("quote status enum CHECK ok")
        check_quote_number_required_when_not_draft(contact_id, event_id)
        print("quote number-when-not-draft CHECK ok")
        check_quote_signature_pairing(contact_id, event_id)
        print("quote signature-paired CHECK ok")
        check_quote_approved_requires_signature(contact_id, event_id)
        print("quote approved-needs-signature CHECK ok")
        check_quote_converted_consistency(contact_id, event_id)
        print("quote converted-consistent CHECK ok (both directions)")
        check_quote_line_negative_money(contact_id, event_id)
        print("quote_line_items nonneg-money CHECKs ok")
        check_quote_invitation_unique(contact_id, event_id)
        print("quote_invitations UNIQUE (quote, contact) ok")

        # payments / payment_allocations / refund_events Phase 6
        check_payment_amount_pos(contact_id, event_id)
        print("payment amount > 0 CHECK ok")
        check_payment_amount_consistent(contact_id, event_id)
        print("payment amount = applied + refunded + unapplied invariant ok")
        check_payment_method_enum(contact_id, event_id)
        print("payment method enum CHECK ok")
        check_payment_status_enum(contact_id, event_id)
        print("payment status enum CHECK ok")
        check_payment_number_when_not_pending(contact_id, event_id)
        print("payment number-when-not-pending CHECK ok")
        check_payment_alloc_invariants(contact_id, event_id)
        print(
            "payment_allocations: applied>0 / refund<=applied / UNIQUE / "
            "RESTRICT on invoice ok"
        )
        check_refund_event_invariants(contact_id, event_id)
        print("refund_events: amount>0 / from_unapplied<=amount / method enum / RESTRICT ok")

        # event_documents Phase 4a split (migration 025)
        check_document_kinds_4a(contact_id, event_id, user_id)
        print("event_documents kind enum (incl. external_invoice) ok")
        check_invoice_fields_only_on_invoice_kinds_4a(contact_id, event_id, user_id)
        print("invoice_* columns scoped to invoice/external_invoice kinds ok")
        check_linked_invoice_id_only_on_external_invoice_4a(
            contact_id, event_id, user_id
        )
        print("linked_invoice_id scoped to external_invoice + ON DELETE SET NULL ok")

        # misc
        check_legacy_migration_run_id_handle(contact_id, event_id)
        print("legacy_migration_run_id rollback handle ok")

        print()
        print("invoice schema smoke ok")
        return 0
    finally:
        # Clean up the extra contact we made for the public_key test.
        db = SessionLocal()
        try:
            db.execute(
                sql_text("DELETE FROM invoice_invitations WHERE contact_id = :c"),
                {"c": second_contact_id},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :c"),
                {"c": second_contact_id},
            )
            db.commit()
        finally:
            db.close()
        _cleanup(contact_id, event_id)


if __name__ == "__main__":
    sys.exit(main())
