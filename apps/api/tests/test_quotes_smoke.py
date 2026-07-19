"""Smoke tests for the quotes service (Phase 5 of the invoicing plan).

Drives `services/quote_service.py` directly — the router smoke comes
later (after `api/routers/quotes.py` ships). Covers:

- create + update + line-item replacement + revision bump on sent edit
- mark_sent: number allocated, invitation row created, rejects empty quotes
- approve_quote: signature captured, status flips, idempotent on re-call
- approve rejects on draft, rejects without signature payload
- reject_quote, cancel_quote, soft_delete_quote rules
- convert_to_invoice: copies lines, generates default 50/50 schedule
  anchored to event_date, stamps converted_at + converted_invoice_id,
  flips status to 'converted' (terminal)
- convert refused on non-approved status
- convert is idempotent (returns the same invoice on a second call)
- list_quotes_for_event + search_quotes

Cleans up every row it created. Runs as a script:
`venv/bin/python tests/test_quotes_smoke.py`. Internal helpers are
named `check_*` so pytest does not collect them.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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

from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    InvoiceLineItem,
    Quote,
    QuoteInvitation,
    QuoteLineItem,
    User,
)
from services import invoice_service, quote_service  # noqa: E402
from services.invoice_service import LineItemInput  # noqa: E402


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _seed():
    """Mint a fresh contact + event + admin user for this run so the
    smoke is independent of repo state."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        contact = Contact(
            display_name=f"Quotes Smoke {suffix}",
            phone="(210) 555-1414",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Quotes Smoke Quince {suffix}",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        from database.auth import hash_password

        user = User(
            username=f"quotes-smoke-{suffix}",
            email=f"quotes-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Quotes Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        db.refresh(user)
        return contact.id, event.id, user.id
    finally:
        db.close()


def _cleanup(contact_id, event_id, user_id):
    db = SessionLocal()
    try:
        # Quotes + their children
        db.execute(
            sql_text(
                "DELETE FROM quote_invitations WHERE quote_id IN "
                "(SELECT id FROM quotes WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM quote_line_items WHERE quote_id IN "
                "(SELECT id FROM quotes WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text("DELETE FROM quotes WHERE event_id = :e"),
            {"e": event_id},
        )
        # Any invoices the conversion produced
        db.execute(
            sql_text(
                "DELETE FROM invoice_installments WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_line_items WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_invitations WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text("DELETE FROM invoices WHERE event_id = :e"),
            {"e": event_id},
        )
        db.execute(sql_text("DELETE FROM events WHERE id = :e"), {"e": event_id})
        db.execute(
            sql_text("DELETE FROM contacts WHERE id = :c"), {"c": contact_id}
        )
        db.execute(
            sql_text("DELETE FROM users WHERE id = :u"), {"u": user_id}
        )
        db.commit()
    finally:
        db.close()


def _two_lines() -> list[LineItemInput]:
    """One product + one service. Tax 8.25% on both. Round-half-even
    rules apply at the cents boundary."""
    return [
        LineItemInput(
            kind="product",
            description="Quinceañera dress",
            quantity=Decimal("1"),
            unit_price_cents=125000,
            tax_rate=Decimal("0.0825"),
            tax_name="TX Sales",
        ),
        LineItemInput(
            kind="service",
            description="Alterations",
            quantity=Decimal("1"),
            unit_price_cents=15000,
            tax_rate=Decimal("0.0825"),
            tax_name="TX Sales",
        ),
    ]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_create_and_totals(contact_id, event_id, user_id) -> int:
    """create_quote computes per-line totals and recomputes the parent
    quote totals. No number, status='draft'."""
    db = SessionLocal()
    try:
        quote = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            actor_user_id=user_id,
            expires_at=date.today() + timedelta(days=30),
            terms="Standard quince terms.",
        )
        db.commit()
        assert quote.quote_number is None
        assert quote.status == "draft"
        # 125000 + 15000 = 140000 subtotal, 8.25% tax = 11550, total = 151550
        assert int(quote.subtotal_cents) == 140000, quote.subtotal_cents
        assert int(quote.tax_cents) == 11550, quote.tax_cents
        assert int(quote.total_cents) == 151550, quote.total_cents
        return quote.id
    finally:
        db.close()


def check_update_and_revision(quote_id):
    """Update a draft: no revision bump. Update a sent quote (later in
    test sequence): revision bumps."""
    db = SessionLocal()
    try:
        # Drop the alterations line and discount the dress 5000.
        quote_service.update_quote(
            db,
            quote_id=quote_id,
            patch={
                "line_items": [
                    LineItemInput(
                        kind="product",
                        description="Quinceañera dress",
                        quantity=Decimal("1"),
                        unit_price_cents=125000,
                        discount_cents=5000,
                        tax_rate=Decimal("0.0825"),
                        tax_name="TX Sales",
                    ),
                ],
                "private_notes": "internal note for staff",
            },
        )
        db.commit()
        quote = db.get(Quote, quote_id)
        assert quote.revision == 1, "draft edits don't bump revision"
        # 125000 - 5000 discount = 120000 subtotal, tax 9900, total 129900.
        assert int(quote.subtotal_cents) == 120000, quote.subtotal_cents
        assert int(quote.tax_cents) == 9900, quote.tax_cents
        assert int(quote.total_cents) == 129900, quote.total_cents
        assert quote.private_notes == "internal note for staff"
    finally:
        db.close()


def check_send_empty_rejected(contact_id, event_id, user_id):
    """mark_sent rejects a quote with zero line items."""
    db = SessionLocal()
    try:
        empty = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            actor_user_id=user_id,
        )
        db.commit()
        empty_id = empty.id
        try:
            quote_service.mark_sent(db, quote_id=empty_id, actor_user_id=user_id)
            print("  FAIL: mark_sent accepted a no-line-items quote")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "line_items_required", exc.code
        finally:
            db.execute(sql_text("DELETE FROM quotes WHERE id = :id"), {"id": empty_id})
            db.commit()
    finally:
        db.close()


def check_send_allocates_number(quote_id, user_id):
    """mark_sent allocates Q-YYYY-NNNNNN, stamps sent_at, creates one
    invitation row with a non-trivial public_key."""
    db = SessionLocal()
    try:
        quote_service.mark_sent(db, quote_id=quote_id, actor_user_id=user_id)
        db.commit()
        quote = db.get(Quote, quote_id)
        assert quote.status == "sent"
        assert quote.quote_number is not None
        assert quote.quote_number.startswith(f"Q-{datetime.now(timezone.utc).year}-")
        assert quote.sent_at is not None

        rows = db.execute(
            sql_text("SELECT public_key FROM quote_invitations WHERE quote_id = :id"),
            {"id": quote_id},
        ).all()
        assert len(rows) == 1, rows
        key = rows[0][0]
        assert len(key) >= 32, "public_key looks too short to be unguessable"
    finally:
        db.close()


def check_sent_edit_bumps_revision(quote_id):
    """Editing a sent quote bumps revision."""
    db = SessionLocal()
    try:
        before = db.get(Quote, quote_id).revision
        quote_service.update_quote(
            db,
            quote_id=quote_id,
            patch={"public_notes": "edited after send"},
        )
        db.commit()
        after = db.get(Quote, quote_id).revision
        assert after == before + 1, (before, after)
    finally:
        db.close()


def check_approve_requires_signature(quote_id):
    """approve_quote without signature payload rejected."""
    db = SessionLocal()
    try:
        try:
            quote_service.approve_quote(
                db,
                quote_id=quote_id,
                signature_base64="",
                signature_name="",
                signature_ip=None,
            )
            print("  FAIL: approve_quote accepted empty signature")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "signature_required", exc.code
            db.rollback()
    finally:
        db.close()


def check_approve_captures_signature(quote_id):
    """Happy path: sent → approved with signature stamped."""
    db = SessionLocal()
    try:
        quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64="iVBORw0KGgoAAAANS=",
            signature_name="Debbie Q. Customer",
            signature_ip="192.0.2.42",
        )
        db.commit()
        quote = db.get(Quote, quote_id)
        assert quote.status == "approved"
        assert quote.approved_at is not None
        assert quote.signature_signed_at is not None
        assert quote.signature_name == "Debbie Q. Customer"
        # signature_ip stored as INET — string comparison after coerce.
        assert str(quote.signature_ip) == "192.0.2.42"
        assert quote.signature_base64.startswith("iVBOR")

        # Idempotent: re-call returns the same row, no signature overwrite.
        before_sig_at = quote.signature_signed_at
        quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64="DIFFERENT_DATA=",
            signature_name="Imposter",
            signature_ip="203.0.113.7",
        )
        db.commit()
        quote2 = db.get(Quote, quote_id)
        assert quote2.signature_signed_at == before_sig_at, "idempotent re-approve must not overwrite"
        assert quote2.signature_name == "Debbie Q. Customer"
    finally:
        db.close()


def check_approve_in_store_from_draft(contact_id, event_id, user_id):
    """approve_in_store: draft → approved without going through send.
    Records signature + emits QUOTE_APPROVED_IN_STORE with staff actor."""
    db = SessionLocal()
    try:
        draft = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            actor_user_id=user_id,
        )
        db.commit()
        draft_id = draft.id
        try:
            quote_service.approve_in_store(
                db,
                quote_id=draft_id,
                signature_base64="iVBORinstore=",
                signature_name="In-Store Customer",
                signature_ip="198.51.100.7",
                actor_user_id=user_id,
            )
            db.commit()
            quote = db.get(Quote, draft_id)
            assert quote.status == "approved", quote.status
            assert quote.signature_name == "In-Store Customer"
            assert str(quote.signature_ip) == "198.51.100.7"
            assert quote.signature_signed_at is not None
            assert quote.approved_at is not None

            # Verify activity log: QUOTE_APPROVED_IN_STORE emitted with
            # actor_kind='staff' and the staff user_id, plus the mirror
            # QUOTE_APPROVED row so existing readers keep working.
            rows = db.execute(
                sql_text(
                    "SELECT activity_type, actor_kind, actor_user_id "
                    "FROM activity_log WHERE subject_kind = 'quote' "
                    "AND subject_id = :q ORDER BY id"
                ),
                {"q": draft_id},
            ).fetchall()
            types = [r[0] for r in rows]
            assert "quote.approved_in_store" in types, types
            assert "quote.approved" in types, types
            in_store_row = next(
                r for r in rows if r[0] == "quote.approved_in_store"
            )
            assert in_store_row[1] == "staff", in_store_row
            assert in_store_row[2] == user_id, in_store_row

            # Idempotent: a second call leaves the original signature.
            before_sig_at = quote.signature_signed_at
            quote_service.approve_in_store(
                db,
                quote_id=draft_id,
                signature_base64="DIFFERENT=",
                signature_name="Imposter",
                signature_ip="203.0.113.99",
                actor_user_id=user_id,
            )
            db.commit()
            again = db.get(Quote, draft_id)
            assert again.signature_signed_at == before_sig_at
            assert again.signature_name == "In-Store Customer"
        finally:
            db.execute(
                sql_text(
                    "DELETE FROM activity_log WHERE subject_kind='quote' "
                    "AND subject_id = :q"
                ),
                {"q": draft_id},
            )
            db.execute(
                sql_text("DELETE FROM quote_line_items WHERE quote_id = :q"),
                {"q": draft_id},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = :q"),
                {"q": draft_id},
            )
            db.commit()
    finally:
        db.close()


def check_approve_rejects_on_draft(contact_id, event_id, user_id):
    """approve on a draft (never sent) is invalid_transition."""
    db = SessionLocal()
    try:
        draft = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            actor_user_id=user_id,
        )
        db.commit()
        draft_id = draft.id
        try:
            quote_service.approve_quote(
                db,
                quote_id=draft_id,
                signature_base64="aGVsbG8=",
                signature_name="N",
                signature_ip=None,
            )
            print("  FAIL: approved a draft quote")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "invalid_transition", exc.code
            db.rollback()
        finally:
            db.execute(sql_text("DELETE FROM quote_line_items WHERE quote_id = :q"), {"q": draft_id})
            db.execute(sql_text("DELETE FROM quotes WHERE id = :q"), {"q": draft_id})
            db.commit()
    finally:
        db.close()


def check_reject_quote(contact_id, event_id, user_id):
    """Send a fresh quote, then reject it."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        quote_service.reject_quote(
            db, quote_id=q.id, reason="customer chose another vendor"
        )
        db.commit()
        row = db.get(Quote, q.id)
        assert row.status == "rejected"
        assert row.rejected_at is not None
        assert row.rejection_reason == "customer chose another vendor"
    finally:
        db.close()


def check_cancel_quote(contact_id, event_id, user_id):
    """Cancel a sent quote (drafts must be soft-deleted instead;
    chk_quote_number_when_not_draft makes 'draft → cancelled' invalid)."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            actor_user_id=user_id,
        )
        db.commit()

        # Cancel-on-draft refused
        try:
            quote_service.cancel_quote(db, quote_id=q.id, reason="too soon")
            print("  FAIL: cancelled a draft quote (CHECK would have tripped)")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "cancel_draft_not_allowed", exc.code
            db.rollback()

        # Send, then cancel — happy path
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        quote_service.cancel_quote(db, quote_id=q.id, reason="duplicate")
        db.commit()
        row = db.get(Quote, q.id)
        assert row.status == "cancelled"
        assert row.cancelled_at is not None
        assert row.cancellation_reason == "duplicate"
        # Number preserved on cancellation (mirrors invoices).
        assert row.quote_number is not None
    finally:
        db.close()


def check_soft_delete_rules(contact_id, event_id, user_id):
    """soft_delete on draft works; on sent fails."""
    db = SessionLocal()
    try:
        # Draft — deletable
        q1 = quote_service.create_quote(
            db, event_id=event_id, contact_id=contact_id,
            line_items=_two_lines(), actor_user_id=user_id,
        )
        db.commit()
        quote_service.soft_delete_quote(db, quote_id=q1.id)
        db.commit()
        row = db.get(Quote, q1.id)
        assert row.deleted_at is not None

        # Sent — refused
        q2 = quote_service.create_quote(
            db, event_id=event_id, contact_id=contact_id,
            line_items=_two_lines(), actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q2.id, actor_user_id=user_id)
        db.commit()
        try:
            quote_service.soft_delete_quote(db, quote_id=q2.id)
            print("  FAIL: soft-deleted a sent quote")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "quote_not_deletable", exc.code
            db.rollback()
    finally:
        db.close()


def check_convert_to_invoice(quote_id, user_id) -> int:
    """Convert an approved quote to a draft invoice. Lines copy 1:1.
    Default 50/50 schedule. Quote becomes terminal 'converted'."""
    db = SessionLocal()
    try:
        invoice = quote_service.convert_to_invoice(
            db, quote_id=quote_id, actor_user_id=user_id
        )
        db.commit()
        invoice_id = invoice.id
        quote = db.get(Quote, quote_id)
        assert quote.status == "converted"
        assert quote.converted_invoice_id == invoice_id
        assert quote.converted_at is not None

        inv = db.get(Invoice, invoice_id)
        assert inv.status == "draft"
        assert inv.invoice_number is None  # number waits for send
        # Total mirrors the quote total exactly (lines copied verbatim).
        assert int(inv.total_cents) == int(quote.total_cents), (
            inv.total_cents, quote.total_cents,
        )

        line_count = db.execute(
            sql_text("SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = :id"),
            {"id": invoice_id},
        ).scalar()
        q_line_count = db.execute(
            sql_text("SELECT COUNT(*) FROM quote_line_items WHERE quote_id = :id"),
            {"id": quote_id},
        ).scalar()
        assert line_count == q_line_count, (line_count, q_line_count)

        installments = db.execute(
            sql_text(
                "SELECT label, amount_cents, due_date FROM invoice_installments "
                "WHERE invoice_id = :id ORDER BY sort_order"
            ),
            {"id": invoice_id},
        ).all()
        assert len(installments) == 2, installments
        deposit, balance = installments
        assert deposit.label == "Deposit"
        assert balance.label == "Balance"
        # 50/50 split (with banker's halving): deposit + balance == total.
        half_floor = inv.total_cents // 2
        expected_deposit = (
            half_floor + 1
            if inv.total_cents % 2 and half_floor % 2
            else half_floor
        )
        assert deposit.amount_cents == expected_deposit, (
            deposit.amount_cents, expected_deposit,
        )
        assert deposit.amount_cents + balance.amount_cents == inv.total_cents
        # Deposit due 14 days after issue (matches InvoiceEditor default).
        assert deposit.due_date == inv.issue_date + timedelta(days=14), (
            deposit.due_date, inv.issue_date,
        )
        # Balance falls 60 days before event_date when far enough out.
        event_date = db.execute(
            sql_text("SELECT event_date FROM events WHERE id = :e"),
            {"e": inv.event_id},
        ).scalar()
        expected = event_date - timedelta(days=60)
        assert balance.due_date == expected, (balance.due_date, expected)
        return invoice_id
    finally:
        db.close()


def check_convert_idempotent(quote_id, expected_invoice_id):
    """Calling convert_to_invoice twice on the same converted quote
    returns the same invoice — does not duplicate."""
    db = SessionLocal()
    try:
        inv = quote_service.convert_to_invoice(db, quote_id=quote_id)
        db.commit()
        assert inv.id == expected_invoice_id, (inv.id, expected_invoice_id)
    finally:
        db.close()


def check_invoice_delete_unlinks_quote(quote_id, invoice_id, user_id) -> int:
    """Soft-deleting a draft invoice that was created from a quote
    returns the quote to 'approved' and clears converted_invoice_id /
    converted_at — the quote becomes re-convertible. Also verifies
    the source_quote backref is exposed on the invoice detail before
    deletion, and that the second conversion produces a NEW invoice."""
    db = SessionLocal()
    try:
        # Detail before delete: source_quote backref is populated.
        detail = invoice_service.get_invoice_detail(db, invoice_id)
        assert detail.source_quote_id == quote_id, detail.source_quote_id
        assert detail.source_quote_number is not None, detail

        invoice_service.soft_delete_invoice(
            db, invoice_id=invoice_id, actor_user_id=user_id
        )
        db.commit()

        quote = db.get(Quote, quote_id)
        db.refresh(quote)
        assert quote.status == "approved", quote.status
        assert quote.converted_invoice_id is None, quote.converted_invoice_id
        assert quote.converted_at is None, quote.converted_at

        # quote.unconverted activity row was logged.
        rows = db.execute(
            sql_text(
                "SELECT activity_type FROM activity_log "
                "WHERE event_id = :e ORDER BY id DESC LIMIT 5"
            ),
            {"e": quote.event_id},
        ).all()
        types = [r[0] for r in rows]
        assert "quote.unconverted" in types, types

        # Reconvert: produces a fresh invoice with a new id.
        new_invoice = quote_service.convert_to_invoice(
            db, quote_id=quote_id, actor_user_id=user_id
        )
        db.commit()
        assert new_invoice.id != invoice_id, (new_invoice.id, invoice_id)
        assert new_invoice.status == "draft"
        return new_invoice.id
    finally:
        db.close()


def check_convert_heals_soft_deleted_link(contact_id, event_id, user_id):
    """Defensive backstop: if a quote points at an invoice that was
    soft-deleted (e.g., direct DB intervention bypassed the unlink
    path), convert_to_invoice should auto-heal the linkage and create
    a fresh invoice rather than handing back the dead one."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db, event_id=event_id, contact_id=contact_id,
            line_items=_two_lines(), actor_user_id=user_id,
        )
        quote_service.approve_in_store(
            db,
            quote_id=q.id,
            signature_base64="iVBORheal=",
            signature_name="Heal Smoke",
            signature_ip="127.0.0.1",
            actor_user_id=user_id,
        )
        inv = quote_service.convert_to_invoice(
            db, quote_id=q.id, actor_user_id=user_id
        )
        db.commit()
        original_invoice_id = inv.id

        # Simulate stale state: stamp deleted_at on the invoice WITHOUT
        # going through soft_delete_invoice (which would already unlink).
        db.execute(
            sql_text(
                "UPDATE invoices SET deleted_at = NOW() WHERE id = :i"
            ),
            {"i": original_invoice_id},
        )
        db.commit()

        # Quote is still 'converted' pointing at the dead invoice.
        stuck = db.get(Quote, q.id)
        db.refresh(stuck)
        assert stuck.status == "converted"
        assert stuck.converted_invoice_id == original_invoice_id

        healed_invoice = quote_service.convert_to_invoice(
            db, quote_id=q.id, actor_user_id=user_id
        )
        db.commit()
        assert healed_invoice.id != original_invoice_id, healed_invoice.id
        assert healed_invoice.deleted_at is None
    finally:
        db.close()


def check_convert_refused_on_non_approved(contact_id, event_id, user_id):
    """convert refused unless status == 'approved'."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db, event_id=event_id, contact_id=contact_id,
            line_items=_two_lines(), actor_user_id=user_id,
        )
        db.commit()
        try:
            quote_service.convert_to_invoice(db, quote_id=q.id)
            print("  FAIL: converted a draft quote")
        except quote_service.QuoteServiceError as exc:
            assert exc.code == "invalid_transition", exc.code
            db.rollback()
    finally:
        db.close()


def check_quote_pdf_renders_phase6_schedule(contact_id, event_id, user_id):
    """A quote with an installment schedule renders the shared
    schedule partial under the "Payment terms" header — quotes do
    not surface a Status column.
    """
    from datetime import timezone as _tz

    from database.models import QuoteInstallment as _QI
    from services.invoice_pdf import (
        _project_customer_lines,
        _render_html,
        _resolve_business_header,
        _totals_breakdown,
    )
    from services.quote_service import QuoteInstallmentInput

    db = SessionLocal()
    try:
        quote = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=_two_lines(),
            installments=[
                QuoteInstallmentInput(
                    label="Deposit",
                    amount_cents=int(151550 // 2) + 1,
                    due_date=date.today() + timedelta(days=14),
                ),
                QuoteInstallmentInput(
                    label="Balance",
                    amount_cents=151550 - (int(151550 // 2) + 1),
                    due_date=date.today() + timedelta(days=120),
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        line_rows = (
            db.query(QuoteLineItem)
            .filter(QuoteLineItem.quote_id == quote.id)
            .order_by(QuoteLineItem.sort_order, QuoteLineItem.id)
            .all()
        )
        inst_rows = (
            db.query(_QI)
            .filter(_QI.quote_id == quote.id)
            .order_by(_QI.sort_order, _QI.id)
            .all()
        )
        html = _render_html(
            "pdf/quote.html",
            q=quote,
            contact=db.get(Contact, contact_id),
            event=db.get(Event, event_id),
            line_items=_project_customer_lines(db, line_rows),
            totals=_totals_breakdown(line_rows, quote),
            installments=inst_rows,
            schedule_header="Payment terms",
            show_payment_status=False,
            business=_resolve_business_header(db),
            rendered_at=datetime.now(_tz.utc),
        )
        assert "Payment terms" in html
        assert "Payment schedule" not in html
        assert "Deposit" in html and "Balance" in html
        assert ">Paid<" not in html and ">Due<" not in html
    finally:
        db.close()


def check_list_and_search(contact_id, event_id):
    """list_quotes_for_event filters by event + status. search_quotes
    matches q against quote_number prefix or contact display_name."""
    db = SessionLocal()
    try:
        all_quotes = quote_service.list_quotes_for_event(
            db, event_id=event_id
        )
        assert len(all_quotes) >= 1
        # search by status='converted' should hit at least one
        converted = quote_service.search_quotes(db, status="converted")
        assert any(q.event_id == event_id for q in converted)
        # search by event name fragment via contact_name match
        contact_row = db.get(Contact, contact_id)
        hits = quote_service.search_quotes(db, q=contact_row.display_name[:5])
        assert any(q.event_id == event_id for q in hits)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    contact_id, event_id, user_id = _seed()
    print(f"seeded contact={contact_id} event={event_id} user={user_id}")
    try:
        quote_id = check_create_and_totals(contact_id, event_id, user_id)
        print(f"create + per-line totals ok (quote={quote_id})")

        check_update_and_revision(quote_id)
        print("draft update no revision bump ok")

        check_send_empty_rejected(contact_id, event_id, user_id)
        print("send empty quote rejected ok")

        check_send_allocates_number(quote_id, user_id)
        print("send allocates Q-YYYY-NNNNNN + invitation ok")

        check_sent_edit_bumps_revision(quote_id)
        print("sent edit bumps revision ok")

        check_approve_requires_signature(quote_id)
        print("approve without signature rejected ok")

        check_approve_captures_signature(quote_id)
        print("approve captures signature + idempotent ok")

        check_approve_rejects_on_draft(contact_id, event_id, user_id)
        print("approve on draft rejected ok")

        check_approve_in_store_from_draft(contact_id, event_id, user_id)
        print("approve_in_store draft → approved + activity log ok")

        check_reject_quote(contact_id, event_id, user_id)
        print("reject quote ok")

        check_cancel_quote(contact_id, event_id, user_id)
        print("cancel quote ok")

        check_soft_delete_rules(contact_id, event_id, user_id)
        print("soft delete (draft ok / sent rejected) ok")

        invoice_id = check_convert_to_invoice(quote_id, user_id)
        print(f"convert to invoice ok (invoice={invoice_id}) — schedule + line copy verified")

        check_convert_idempotent(quote_id, invoice_id)
        print("convert idempotent ok")

        invoice_id = check_invoice_delete_unlinks_quote(quote_id, invoice_id, user_id)
        print(f"delete-then-reconvert returns quote to approved ok (new invoice={invoice_id})")

        check_convert_heals_soft_deleted_link(contact_id, event_id, user_id)
        print("convert heals stale soft-deleted link ok")

        check_convert_refused_on_non_approved(contact_id, event_id, user_id)
        print("convert refused on non-approved ok")

        check_list_and_search(contact_id, event_id)
        print("list + search ok")

        check_quote_pdf_renders_phase6_schedule(contact_id, event_id, user_id)
        print("quote PDF surfaces Phase 6 schedule partial ok")

        print()
        print("quotes smoke ok")
        return 0
    finally:
        _cleanup(contact_id, event_id, user_id)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
