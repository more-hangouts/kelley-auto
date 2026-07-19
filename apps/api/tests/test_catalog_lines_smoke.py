"""Catalog SKU obfuscation Phase 2 smoke tests.

Exercises the new line-item write rules against a real Postgres
connection:

  - A catalog-backed invoice/quote line writes ``catalog_item_id``,
    ``size_label``, ``internal_notes`` and leaves ``description``,
    ``notes``, and ``public_description`` NULL.
  - A non-catalog line writes ``public_description`` and mirrors it
    into legacy ``description`` (Phase 4 will stop the mirror).
  - Service rejects: catalog-backed line that tries to set
    ``public_description``, ``description``, or ``notes``.
  - Service rejects: missing customer copy on a non-catalog line.
  - Service rejects: legacy description and public_description
    disagree.
  - Forbidden-substring guard fires when ``internal_notes`` on a
    catalog-backed line contains the catalog row's ``internal_sku``,
    ``designer``, or ``style_number``.
  - Convert-to-invoice carries the catalog linkage forward so the
    resulting invoice line stays catalog-backed.
  - Staff read view exposes the catalog snapshot; the snapshot fields
    do NOT appear in any portal/customer surface (covered separately
    by the Phase 4 render swap; this file's scope is the service
    layer).

Runs as a script:

    venv/bin/python tests/test_catalog_lines_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
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
    CatalogItem,
    Contact,
    Event,
    InvoiceLineItem,
    QuoteLineItem,
)
from services import invoice_service, quote_service  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
)
from services.invoice_service import (  # noqa: E402
    InvoiceServiceError,
    LineItemInput,
)
from services.quote_service import QuoteServiceError  # noqa: E402


_PREFIX = f"TEST-P2-{uuid.uuid4().hex[:8].upper()}-"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _get_catalog_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state "
                    "WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _reset_catalog_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": value},
        )
        db.commit()
    finally:
        db.close()


def _seed() -> tuple[int, int, int]:
    """Create a contact, event, and one catalog row. Returns
    ``(contact_id, event_id, catalog_id)``. Cleanup wipes by prefix.
    """
    db = SessionLocal()
    try:
        contact = Contact(display_name=_PREFIX + "Customer", phone="(210) 555-7777")
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=_PREFIX + "Quince",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        cat = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "MORI-89216-IVORY",
                color="Ivory",
                category="quince_gown",
                designer="Mori Lee",
                style_number="89216",
                house_name="Isabella",
                product_title="Isabella Quinceanera Dress",
            ),
        )
        db.commit()
        return contact.id, event.id, cat.id
    finally:
        db.close()


def _cleanup() -> None:
    """Delete in dependency order. Quotes go first so the
    quote→invoice CHECK constraint (chk_quote_converted_consistent)
    doesn't fire when ON DELETE SET NULL would otherwise leave a
    'converted' quote pointing at NULL.
    """
    p = _PREFIX + "%"
    db = SessionLocal()
    try:
        events_subq = (
            "(SELECT id FROM events WHERE event_name LIKE :p)"
        )
        # Children of the quote first.
        db.execute(
            sql_text(
                f"DELETE FROM quote_invitations WHERE quote_id IN "
                f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM quote_line_items WHERE quote_id IN "
                f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        # Quotes themselves before any invoice they were converted into,
        # so the chk_quote_converted_consistent CHECK never sees a
        # transitional state.
        db.execute(
            sql_text(f"DELETE FROM quotes WHERE event_id IN {events_subq}"),
            {"p": p},
        )
        # Now invoice children + invoices.
        db.execute(
            sql_text(
                f"DELETE FROM invoice_invitations WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM invoice_installments WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM invoice_line_items WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(f"DELETE FROM invoices WHERE event_id IN {events_subq}"),
            {"p": p},
        )
        # Finally events, contacts, catalog rows.
        db.execute(
            sql_text("DELETE FROM events WHERE event_name LIKE :p"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM contacts WHERE display_name LIKE :p"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": p},
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_catalog_backed_line_writes_only_new_columns(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=catalog_id,
                    size_label="08",
                    internal_notes="vendor PO 12345",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
        )
        db.commit()

        row = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv.id)
            .one()
        )
        assert row.catalog_item_id == catalog_id, row.catalog_item_id
        assert row.size_label == "08", row.size_label
        assert row.internal_notes == "vendor PO 12345", row.internal_notes
        assert row.description is None, (
            f"catalog-backed line wrote description: {row.description!r}"
        )
        assert row.notes is None, (
            f"catalog-backed line wrote notes: {row.notes!r}"
        )
        assert row.public_description is None, (
            "catalog-backed line wrote public_description: "
            f"{row.public_description!r}"
        )
    finally:
        db.close()


def check_non_catalog_line_writes_public_description(
    contact_id: int, event_id: int
) -> None:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="fee",
                    public_description="Rush alteration fee",
                    internal_notes="seamstress overtime",
                    quantity=Decimal("1"),
                    unit_price_cents=5000,
                )
            ],
        )
        db.commit()

        row = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv.id)
            .one()
        )
        assert row.catalog_item_id is None
        assert row.public_description == "Rush alteration fee"
        assert row.internal_notes == "seamstress overtime"
        # Phase 4 stopped the legacy mirror. New lines (catalog-backed
        # AND non-catalog) leave `description` and `notes` NULL;
        # PDFs and portal pages read `public_description` through
        # `catalog_service.customer_line_view`. Legacy invoices keep
        # their existing `description` and stay grandfathered.
        assert row.description is None, (
            f"new non-catalog line wrote legacy description: "
            f"{row.description!r}"
        )
        assert row.notes is None, (
            f"new non-catalog line wrote legacy notes: {row.notes!r}"
        )
    finally:
        db.close()


def check_legacy_description_input_still_works(
    contact_id: int, event_id: int
) -> None:
    """Existing callers passing only ``description=`` (no
    ``public_description``) keep working: the service routes the
    text through ``public_description`` so customer renderers read
    the same one-liner. After Phase 4 the legacy column stays NULL
    on new rows; only the new ``public_description`` carries the copy.
    """
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    description="Free-form alteration line",
                    quantity=Decimal("1"),
                    unit_price_cents=2000,
                )
            ],
        )
        db.commit()
        row = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv.id)
            .one()
        )
        assert row.public_description == "Free-form alteration line", (
            f"expected legacy description routed into public_description, "
            f"got {row.public_description!r}"
        )
        assert row.description is None, (
            f"expected legacy description column to stay NULL on new rows "
            f"after Phase 4, got {row.description!r}"
        )
        assert row.catalog_item_id is None
    finally:
        db.close()


def check_catalog_line_rejects_legacy_text(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    db = SessionLocal()
    try:
        for bad in (
            LineItemInput(
                kind="product",
                catalog_item_id=catalog_id,
                public_description="leaks here",
                quantity=Decimal("1"),
                unit_price_cents=120000,
            ),
            LineItemInput(
                kind="product",
                catalog_item_id=catalog_id,
                description="staff freetext",
                quantity=Decimal("1"),
                unit_price_cents=120000,
            ),
            LineItemInput(
                kind="product",
                catalog_item_id=catalog_id,
                notes="staff side note",
                quantity=Decimal("1"),
                unit_price_cents=120000,
            ),
        ):
            try:
                invoice_service.create_invoice(
                    db,
                    event_id=event_id,
                    contact_id=contact_id,
                    line_items=[bad],
                )
                db.commit()
                raise AssertionError(
                    "catalog-backed line accepted legacy text "
                    f"(public_description={bad.public_description!r}, "
                    f"description={bad.description!r}, "
                    f"notes={bad.notes!r})"
                )
            except InvoiceServiceError as exc:
                db.rollback()
                assert exc.code == "catalog_line_legacy_text", (
                    f"unexpected error code {exc.code!r}"
                )
    finally:
        db.close()


def check_non_catalog_line_requires_public_copy(
    contact_id: int, event_id: int
) -> None:
    db = SessionLocal()
    try:
        try:
            invoice_service.create_invoice(
                db,
                event_id=event_id,
                contact_id=contact_id,
                line_items=[
                    LineItemInput(
                        kind="fee",
                        quantity=Decimal("1"),
                        unit_price_cents=1000,
                    )
                ],
            )
            db.commit()
            raise AssertionError(
                "non-catalog line with no public copy was accepted"
            )
        except InvoiceServiceError as exc:
            db.rollback()
            assert exc.code == "public_description_required", exc.code
    finally:
        db.close()


def check_public_description_legacy_mismatch_rejected(
    contact_id: int, event_id: int
) -> None:
    db = SessionLocal()
    try:
        try:
            invoice_service.create_invoice(
                db,
                event_id=event_id,
                contact_id=contact_id,
                line_items=[
                    LineItemInput(
                        kind="product",
                        public_description="Customer-safe text",
                        description="Different staff text",
                        quantity=Decimal("1"),
                        unit_price_cents=10000,
                    )
                ],
            )
            db.commit()
            raise AssertionError(
                "mismatched public_description / description accepted"
            )
        except InvoiceServiceError as exc:
            db.rollback()
            assert exc.code == "line_public_description_conflict", exc.code
    finally:
        db.close()


def check_forbidden_substring_guard_fires(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    """Catalog-backed line whose ``internal_notes`` contains the catalog
    row's ``internal_sku``, ``designer``, or ``style_number`` is
    rejected. Staff-only fields are still guarded because Phase 0 listed
    them as future export risk."""
    db = SessionLocal()
    try:
        cat = db.get(CatalogItem, catalog_id)
        for needle, ident_kind in (
            (cat.internal_sku, "internal_sku"),
            (cat.designer, "designer"),
            (cat.style_number, "style_number"),
        ):
            if not needle:
                continue
            try:
                invoice_service.create_invoice(
                    db,
                    event_id=event_id,
                    contact_id=contact_id,
                    line_items=[
                        LineItemInput(
                            kind="product",
                            catalog_item_id=catalog_id,
                            internal_notes=f"see {needle} in shipment",
                            quantity=Decimal("1"),
                            unit_price_cents=120000,
                        )
                    ],
                )
                db.commit()
                raise AssertionError(
                    f"forbidden-substring guard let {ident_kind} through"
                )
            except InvoiceServiceError as exc:
                db.rollback()
                assert exc.code == "catalog_leak", exc.code
                assert exc.extra.get("identifier_kind") == ident_kind, exc.extra
    finally:
        db.close()


def check_quote_catalog_line_then_convert(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    """A catalog-backed quote line must convert into a catalog-backed
    invoice line so customer copy keeps coming from the catalog row, not
    from staff text."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=catalog_id,
                    size_label="10",
                    internal_notes="vendor confirmation pending",
                    quantity=Decimal("1"),
                    unit_price_cents=130000,
                )
            ],
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id)
        db.commit()
        quote_service.approve_quote(
            db,
            quote_id=q.id,
            signature_base64="data:image/png;base64,AAAA",
            signature_name="Customer",
            signature_ip=None,
        )
        db.commit()
        invoice = quote_service.convert_to_invoice(db, quote_id=q.id)
        db.commit()

        row = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == invoice.id)
            .one()
        )
        assert row.catalog_item_id == catalog_id, row.catalog_item_id
        assert row.size_label == "10", row.size_label
        assert row.internal_notes == "vendor confirmation pending"
        assert row.description is None
        assert row.public_description is None
    finally:
        db.close()


def check_quote_line_rejects_catalog_legacy_text(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    db = SessionLocal()
    try:
        try:
            quote_service.create_quote(
                db,
                event_id=event_id,
                contact_id=contact_id,
                line_items=[
                    LineItemInput(
                        kind="product",
                        catalog_item_id=catalog_id,
                        description="leaks via quote",
                        quantity=Decimal("1"),
                        unit_price_cents=120000,
                    )
                ],
            )
            db.commit()
            raise AssertionError(
                "quote catalog-backed line accepted legacy description"
            )
        except QuoteServiceError as exc:
            db.rollback()
            assert exc.code == "catalog_line_legacy_text", exc.code
    finally:
        db.close()


def check_staff_detail_exposes_catalog_snapshot(
    contact_id: int, event_id: int, catalog_id: int
) -> None:
    """``get_invoice_detail`` joins the catalog row so the staff API can
    show the real internal SKU alongside the public code."""
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=catalog_id,
                    size_label="12",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
        )
        db.commit()
        detail = invoice_service.get_invoice_detail(db, inv.id)
        line = detail.line_items[0]
        assert line.catalog is not None, "catalog snapshot missing on detail"
        cat = db.get(CatalogItem, catalog_id)
        assert line.catalog.internal_sku == cat.internal_sku
        assert line.catalog.public_code == cat.public_code
        assert line.catalog.designer == cat.designer
        assert line.catalog.style_number == cat.style_number
    finally:
        db.close()


def check_inactive_catalog_item_rejected(
    contact_id: int, event_id: int
) -> None:
    """A catalog row flipped inactive cannot be attached to a NEW line.
    Existing lines that already point at the row continue to render via
    ``ON DELETE RESTRICT`` and the row stays in the catalog for read-back.
    """
    db = SessionLocal()
    try:
        inactive = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "INACTIVE",
                color="Black",
                category="quince_gown",
                designer="Some Vendor",
                style_number="0001",
                active=False,
            ),
        )
        db.commit()
        try:
            invoice_service.create_invoice(
                db,
                event_id=event_id,
                contact_id=contact_id,
                line_items=[
                    LineItemInput(
                        kind="product",
                        catalog_item_id=inactive.id,
                        quantity=Decimal("1"),
                        unit_price_cents=10000,
                    )
                ],
            )
            db.commit()
            raise AssertionError(
                "inactive catalog item accepted on a new line"
            )
        except InvoiceServiceError as exc:
            db.rollback()
            assert exc.code == "catalog_item_inactive", exc.code
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_catalog_seq()
    print(f"catalog_public_code_seq baseline = {seq_baseline}")
    contact_id, event_id, catalog_id = _seed()
    print(f"seeded contact={contact_id} event={event_id} catalog={catalog_id}")
    try:
        check_catalog_backed_line_writes_only_new_columns(
            contact_id, event_id, catalog_id
        )
        print("catalog-backed line writes only new columns ok")
        check_non_catalog_line_writes_public_description(contact_id, event_id)
        print("non-catalog line writes public_description (mirror) ok")
        check_legacy_description_input_still_works(contact_id, event_id)
        print("legacy description-only input still routes through ok")
        check_catalog_line_rejects_legacy_text(
            contact_id, event_id, catalog_id
        )
        print("catalog-backed line rejects legacy text fields ok")
        check_non_catalog_line_requires_public_copy(contact_id, event_id)
        print("non-catalog line requires public_description ok")
        check_public_description_legacy_mismatch_rejected(
            contact_id, event_id
        )
        print("conflicting public_description vs description rejected ok")
        check_forbidden_substring_guard_fires(
            contact_id, event_id, catalog_id
        )
        print("forbidden-substring guard fires for all 3 identifiers ok")
        check_quote_catalog_line_then_convert(
            contact_id, event_id, catalog_id
        )
        print("quote catalog line + convert preserves catalog linkage ok")
        check_quote_line_rejects_catalog_legacy_text(
            contact_id, event_id, catalog_id
        )
        print("quote rejects catalog-backed legacy text ok")
        check_staff_detail_exposes_catalog_snapshot(
            contact_id, event_id, catalog_id
        )
        print("staff detail surfaces catalog snapshot ok")
        check_inactive_catalog_item_rejected(contact_id, event_id)
        print("inactive catalog item rejected on new line ok")
        print()
        print("catalog phase 2 line-write smoke ok")
        return 0
    finally:
        _cleanup()
        _reset_catalog_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
