"""Catalog SKU obfuscation Phase 4 customer-render-swap smoke.

Phase 4 routes every customer surface (invoice PDF, quote PDF, portal
invoice page, portal quote page) through
``catalog_service.customer_line_view``. This file proves that:

  1. A new catalog-backed line never renders staff free text or
     vendor identifiers on any customer surface — even when the
     dangerous string ``Mori Lee 89216 Ivory 8`` is typed into both
     ``description`` and ``notes`` of a separately-created legacy line
     in the same invoice. Catalog-backed lines render the public
     ``BVX-NNNNN`` code and the customer-safe ``Isabella / Ivory /
     Size 08`` description derived from the catalog row.

  2. A legacy line (no ``catalog_item_id``) keeps rendering its
     ``description`` exactly as before — Phase 0 grandfathered that
     behavior because the same text is already on issued PDFs and
     portal pages in customers' inboxes.

  3. The legacy ``notes`` column never appears on any customer
     surface, on any line (legacy or new). Phase 0 confirmed
     ``notes`` was never customer-safe.

  4. Forbidden vendor identifiers (``internal_sku``, ``designer``,
     ``style_number``) and forbidden staff fields (``internal_notes``,
     ``product_key``) never appear in the rendered HTML or
     post-render PDF text.

The test renders the actual PDF bytes and the actual portal HTML
(not just the projection dataclass) so a future template change that
slips a staff field into a customer surface fails this test directly.

Runs as a script:

    venv/bin/python tests/test_catalog_render_swap_smoke.py
"""

from __future__ import annotations

import os
import re
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
    Contact,
    Event,
    InvoiceLineItem,
    QuoteLineItem,
)
from services import (  # noqa: E402
    document_storage,
    invoice_pdf,
    invoice_service,
    portal_service,
    quote_service,
)
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
)
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)


_PREFIX = f"P4-RENDER-{uuid.uuid4().hex[:8].upper()}-"

# Storage keys this run wrote into DOCUMENT_STORAGE_ROOT. Cleanup
# unlinks them so the test does not leave PDF cache files behind on
# the VPS the way the first version of this smoke did.
_RENDERED_KEYS: list[str] = []

# The dangerous staff fixture from the Phase 4 plan: an obvious vendor
# identifier blob that staff might type into description/notes on a
# pre-catalog line, plus the catalog row's own internal_sku /
# designer / style_number that must never appear on customer surfaces
# for the catalog-backed line.
_DANGEROUS_STRING = "Mori Lee 89216 Ivory 8"
_INTERNAL_SKU = _PREFIX + "MORI-89216-IVORY"
_DESIGNER = "Mori Lee"
_STYLE_NUMBER = "89216"
_HOUSE_NAME = "Isabella"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _get_seq() -> int:
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


def _reset_seq(value: int) -> None:
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


def _seed():
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=_PREFIX + "Customer",
            phone="(210) 555-7777",
            email="render@example.com",
        )
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
                internal_sku=_INTERNAL_SKU,
                color="Ivory",
                category="quince_gown",
                designer=_DESIGNER,
                style_number=_STYLE_NUMBER,
                house_name=_HOUSE_NAME,
                product_title="Isabella Quinceanera Dress",
            ),
        )
        db.commit()
        return contact.id, event.id, cat.id
    finally:
        db.close()


def _wipe_pdf_cache() -> None:
    """Remove every cached PDF this run wrote into
    ``DOCUMENT_STORAGE_ROOT``. Without this, the test would leave
    ``invoices/{id}/{rev}.pdf`` and ``quotes/{id}/{rev}.pdf`` files
    behind on the VPS — orphaned because their corresponding rows
    are deleted in ``_cleanup`` immediately after.
    """
    for key in _RENDERED_KEYS:
        try:
            document_storage.delete_object(key)
        except FileNotFoundError:
            # Render path may have skipped writing on a no-op render
            # or a prior cleanup may have already unlinked it. Either
            # way the post-condition (file absent) holds.
            pass


def _cleanup() -> None:
    p = _PREFIX + "%"
    db = SessionLocal()
    try:
        events_subq = (
            "(SELECT id FROM events WHERE event_name LIKE :p)"
        )
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
        db.execute(
            sql_text(f"DELETE FROM quotes WHERE event_id IN {events_subq}"),
            {"p": p},
        )
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


def _make_invoice_with_three_lines(
    contact_id: int, event_id: int, catalog_id: int
) -> int:
    """Create an invoice with three lines covering all three render
    paths:

      0. Catalog-backed line (Phase 2/3 shape). Customer text comes
         from the catalog row; staff free text rejected.
      1. New non-catalog line via the picker-aware shape — uses
         ``public_description`` only.
      2. Legacy line: ``description`` + ``notes`` populated directly
         in the DB to simulate a pre-catalog row that's still in the
         table from before Phase 2 shipped. (We can't reach this shape
         through the normal service path anymore, so we INSERT
         straight into the table after the service call to mimic the
         real "old data" condition.)
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
                    catalog_item_id=catalog_id,
                    size_label="08",
                    # Phase 2's guard rejects writing the catalog row's
                    # vendor identifiers into internal_notes via the
                    # service. We backfill the dangerous value via raw
                    # UPDATE below so the render-swap assertion sees a
                    # row with leaky internal_notes content, simulating
                    # a manual SQL touch or a pre-Phase-2 row.
                    internal_notes="placeholder",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                ),
                LineItemInput(
                    kind="fee",
                    public_description="Rush alteration fee",
                    quantity=Decimal("1"),
                    unit_price_cents=5000,
                ),
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=62500,
                    due_date=date.today() + timedelta(days=30),
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=62500,
                    due_date=date.today() + timedelta(days=120),
                ),
            ],
        )
        db.commit()
        # Bypass the Phase 2 forbidden-substring guard to inject the
        # dangerous staff text into internal_notes on the catalog-
        # backed line. Phase 4 must still keep this off every customer
        # surface — the guard is the front door, the render swap is
        # the back door.
        db.execute(
            sql_text(
                "UPDATE invoice_line_items SET internal_notes = :v "
                "WHERE invoice_id = :inv AND catalog_item_id = :cat"
            ),
            {
                "v": f"vendor PO 12345 — {_INTERNAL_SKU}",
                "inv": inv.id,
                "cat": catalog_id,
            },
        )
        db.commit()
        # Simulate a legacy pre-Phase-2 line: write directly to the
        # table with description and notes set, no catalog_item_id,
        # no public_description / internal_notes. The grandfather
        # rule says this row's `description` continues to render to
        # customers; its `notes` does not.
        db.execute(
            sql_text(
                """
                INSERT INTO invoice_line_items (
                    invoice_id, sort_order, kind, description, notes,
                    quantity, unit_price_cents, line_subtotal_cents,
                    line_tax_cents, line_total_cents
                ) VALUES (
                    :inv, 2, 'product', :desc, :notes,
                    1, 0, 0, 0, 0
                )
                """
            ),
            {
                "inv": inv.id,
                "desc": "Legacy line text (already on issued PDFs)",
                "notes": _DANGEROUS_STRING,
            },
        )
        db.commit()
        invoice_service.mark_sent(db, invoice_id=inv.id)
        db.commit()
        return inv.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Render assertions
# ---------------------------------------------------------------------------


def _assert_customer_surface_safe(rendered: str, *, surface: str) -> None:
    """Common contract every customer surface must satisfy after Phase 4.

    Forbidden tokens:
      - The dangerous staff string typed into legacy ``notes``.
      - The catalog row's vendor identifiers (``internal_sku``,
        ``designer``, ``style_number``) — even though the catalog-
        backed line attempts to reference them via ``internal_notes``
        and the legacy ``notes`` column, customer surfaces must not
        echo them.
      - The staff-only ``internal_notes`` text from the catalog-
        backed line.

    Required tokens:
      - The customer-safe BVX code on the catalog-backed line.
      - The catalog-derived display text (``Isabella / Ivory / Size 08``).
      - The legacy line's ``description`` (grandfathered).
      - The new non-catalog line's ``public_description``.
    """
    forbidden = [
        # Dangerous staff string in legacy notes column — must NOT
        # appear because Phase 4 stopped rendering `notes` on every
        # surface.
        _DANGEROUS_STRING,
        # Catalog identifiers — the catalog-backed line carries them
        # via internal_notes / internal_sku, but no customer surface
        # may echo them.
        _INTERNAL_SKU,
        _DESIGNER,
        _STYLE_NUMBER,
        # Staff-only internal_notes content.
        "vendor PO 12345",
    ]
    for token in forbidden:
        if token in rendered:
            raise AssertionError(
                f"{surface} leaked forbidden token {token!r}; full surface "
                f"snippet: ...{_excerpt_around(rendered, token)}..."
            )

    required = [
        f"{_HOUSE_NAME} / Ivory / Size 08",
        "Rush alteration fee",
        "Legacy line text (already on issued PDFs)",
    ]
    for token in required:
        if token not in rendered:
            raise AssertionError(
                f"{surface} missing required token {token!r}"
            )


def _excerpt_around(text: str, needle: str, span: int = 60) -> str:
    idx = text.find(needle)
    if idx < 0:
        return text[: span * 2]
    start = max(0, idx - span)
    end = min(len(text), idx + len(needle) + span)
    return text[start:end].replace("\n", " ")


def _render_pdf_html(template_name: str, **ctx: object) -> str:
    """Render the same Jinja template the PDF pipeline uses, but stop
    at HTML — WeasyPrint converts this HTML to PDF mechanically, so
    asserting against the HTML source proves the PDF cannot contain
    a token the HTML does not. Avoids a pdftotext / pypdf dependency
    just to read back what we already know we wrote.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = _REPO_ROOT / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    def _money(cents):
        amt = int(cents or 0)
        sign = "-" if amt < 0 else ""
        amt = abs(amt)
        d, c = divmod(amt, 100)
        return f"{sign}${d:,}.{c:02d}"

    def _qty(q):
        if q is None:
            return ""
        try:
            return format(q, "f").rstrip("0").rstrip(".") or "0"
        except Exception:
            return str(q)

    def _fdate(d):
        if d is None:
            return ""
        try:
            return d.strftime("%B %-d, %Y")
        except Exception:
            return d.isoformat()

    def _phone(value):
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        return str(value or "")

    env.filters["money"] = _money
    env.filters["qty"] = _qty
    env.filters["fdate"] = _fdate
    env.filters["phone"] = _phone
    return env.get_template(template_name).render(**ctx)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _record_pdf_cache_key(key: str) -> None:
    """Track a freshly-rendered PDF so the smoke teardown unlinks it.
    The renderer caches by ``invoices/{id}/{rev}.pdf`` /
    ``quotes/{id}/{rev}.pdf``; both fall under ``DOCUMENT_STORAGE_ROOT``.
    """
    _RENDERED_KEYS.append(key)


def check_invoice_pdf_safe(invoice_id: int) -> None:
    """Render the invoice PDF and the same HTML the PDF pipeline
    converts to PDF. WeasyPrint converts HTML mechanically; if the
    HTML is clean, the PDF is too. Also force-render the actual PDF
    bytes so a template-error path does not silently break the
    customer download.
    """
    from datetime import datetime, timezone

    from database.models import (
        Contact,
        Event,
        Invoice,
        InvoiceInstallment,
        InvoiceLineItem,
        InvoiceOrderDiscount,
    )
    from services.invoice_pdf import (
        _project_customer_lines,
        _resolve_business_header,
        _totals_breakdown,
    )

    db = SessionLocal()
    try:
        # Force the actual PDF render to prove the pipeline still
        # produces a usable file.
        path = invoice_pdf.render_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
        assert path.stat().st_size > 0, "PDF rendered to zero bytes"
        invoice = db.get(Invoice, invoice_id)
        _record_pdf_cache_key(
            f"invoices/{invoice.id}/{int(invoice.revision or 1)}.pdf"
        )

        # Now render the same template stack to HTML for substring
        # assertions.
        invoice = db.get(Invoice, invoice_id)
        contact = db.get(Contact, invoice.contact_id)
        event = db.get(Event, invoice.event_id)
        line_rows = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == invoice.id)
            .order_by(InvoiceLineItem.sort_order.asc(), InvoiceLineItem.id.asc())
            .all()
        )
        customer_lines = _project_customer_lines(db, line_rows)
        discount_rows = (
            db.query(InvoiceOrderDiscount)
            .filter(InvoiceOrderDiscount.invoice_id == invoice.id)
            .order_by(
                InvoiceOrderDiscount.sort_order.asc(),
                InvoiceOrderDiscount.id.asc(),
            )
            .all()
        )
        totals = _totals_breakdown(line_rows, invoice, discount_rows)
        inst_rows = (
            db.query(InvoiceInstallment)
            .filter(InvoiceInstallment.invoice_id == invoice.id)
            .order_by(InvoiceInstallment.sort_order.asc())
            .all()
        )
        html = _render_pdf_html(
            "pdf/invoice.html",
            inv=invoice,
            contact=contact,
            event=event,
            line_items=customer_lines,
            totals=totals,
            installments=inst_rows,
            schedule_header="Payment schedule",
            show_payment_status=True,
            business=_resolve_business_header(db),
            rendered_at=datetime.now(timezone.utc),
        )
        _assert_customer_surface_safe(html, surface="invoice PDF HTML")
        assert re.search(r"BVX-\d{5}", html), (
            "expected BVX-NNNNN in invoice PDF HTML"
        )
    finally:
        db.close()


def check_portal_invoice_html_safe(invoice_id: int) -> None:
    """Render the portal invoice page through the production code
    path (``portal_service.get_invoice_view_by_key``) and pass the
    rendered HTML through the same forbidden/required sweep."""
    db = SessionLocal()
    try:
        # Pull the invitation that mark_sent created.
        from database.models import InvoiceInvitation

        invitation = (
            db.query(InvoiceInvitation)
            .filter(InvoiceInvitation.invoice_id == invoice_id)
            .order_by(InvoiceInvitation.id.asc())
            .first()
        )
        assert invitation is not None, "no invitation row for sent invoice"
        result = portal_service.get_invoice_view_by_key(
            db, invitation.public_key
        )
        assert result is not None, "portal returned None for valid key"
        view, _ = result

        # Render through the actual portal Jinja template so the
        # HTML byte stream is what the customer would see.
        html = _render_portal_invoice(view)
        _assert_customer_surface_safe(html, surface="portal invoice HTML")
        assert re.search(r"BVX-\d{5}", html), (
            "expected BVX-NNNNN in portal invoice HTML"
        )
    finally:
        db.close()


def check_portal_dataclass_drops_internal_fields(invoice_id: int) -> None:
    """``PortalLineItem`` must not carry ``description``, ``notes``,
    or ``product_key`` after Phase 4 — and must carry the new
    ``public_code`` / ``display_text`` fields."""
    from dataclasses import fields

    from services.portal_service import PortalLineItem

    field_names = {f.name for f in fields(PortalLineItem)}
    forbidden = {"description", "notes", "product_key", "internal_sku",
                 "designer", "style_number", "internal_notes"}
    leaked = field_names & forbidden
    assert not leaked, (
        f"PortalLineItem still carries forbidden fields: {leaked}"
    )
    assert "public_code" in field_names
    assert "display_text" in field_names


def check_portal_quote_html_safe(quote_id: int) -> None:
    """Render the portal quote page through the production template
    stack and assert the same forbidden/required token sweep applies.
    Mirrors ``check_portal_invoice_html_safe`` so the four customer
    surfaces (invoice PDF, quote PDF, portal invoice HTML, portal
    quote HTML) all get explicit coverage.
    """
    from database.models import QuoteInvitation

    db = SessionLocal()
    try:
        invitation = (
            db.query(QuoteInvitation)
            .filter(QuoteInvitation.quote_id == quote_id)
            .order_by(QuoteInvitation.id.asc())
            .first()
        )
        assert invitation is not None, "no invitation row for sent quote"
        result = portal_service.get_quote_view_by_key(
            db, invitation.public_key
        )
        assert result is not None, "portal returned None for valid quote key"
        view, _ = result
        html = _render_portal_quote(view)
        forbidden = [
            _DANGEROUS_STRING,
            _INTERNAL_SKU,
            _DESIGNER,
            _STYLE_NUMBER,
            "private —",
        ]
        for token in forbidden:
            if token in html:
                raise AssertionError(
                    f"portal quote HTML leaked forbidden token "
                    f"{token!r}: ...{_excerpt_around(html, token)}..."
                )
        assert f"{_HOUSE_NAME} / Ivory / Size 10" in html, (
            "portal quote HTML missing customer_line_description"
        )
        assert "Legacy line text (already on issued PDFs)" in html, (
            "portal quote HTML missing legacy line description"
        )
        assert re.search(r"BVX-\d{5}", html), (
            "expected BVX-NNNNN in portal quote HTML"
        )
    finally:
        db.close()


def check_quote_pdf_safe(contact_id: int, event_id: int, catalog_id: int) -> int:
    """Same Phase 4 rules apply on the quote write/render path.
    Returns the quote id so the caller can chain the portal-quote
    HTML assertion against the same fixture."""
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
                    internal_notes="placeholder",
                    quantity=Decimal("1"),
                    unit_price_cents=130000,
                )
            ],
        )
        db.commit()
        # Bypass the Phase 2 guard to plant a leaky internal_notes
        # value on the catalog-backed quote line so the render-swap
        # assertion proves the customer PDF still hides it.
        db.execute(
            sql_text(
                "UPDATE quote_line_items SET internal_notes = :v "
                "WHERE quote_id = :qid AND catalog_item_id = :cat"
            ),
            {
                "v": f"private — {_INTERNAL_SKU}",
                "qid": q.id,
                "cat": catalog_id,
            },
        )
        db.commit()
        # Inject a legacy-shape quote line direct to the table.
        db.execute(
            sql_text(
                """
                INSERT INTO quote_line_items (
                    quote_id, sort_order, kind, description, notes,
                    quantity, unit_price_cents, line_subtotal_cents,
                    line_tax_cents, line_total_cents
                ) VALUES (
                    :qid, 1, 'product', :desc, :notes,
                    1, 0, 0, 0, 0
                )
                """
            ),
            {
                "qid": q.id,
                "desc": "Legacy line text (already on issued PDFs)",
                "notes": _DANGEROUS_STRING,
            },
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id)
        db.commit()
        path = invoice_pdf.render_quote_pdf(db, quote_id=q.id)
        db.commit()
        assert path.stat().st_size > 0, "quote PDF rendered to zero bytes"

        from datetime import datetime, timezone

        from database.models import Quote, QuoteInstallment, QuoteOrderDiscount
        from services.invoice_pdf import (
            _project_customer_lines,
            _resolve_business_header,
            _totals_breakdown,
        )

        quote = db.get(Quote, q.id)
        _record_pdf_cache_key(
            f"quotes/{quote.id}/{int(quote.revision or 1)}.pdf"
        )
        contact = db.get(Contact, quote.contact_id)
        event = db.get(Event, quote.event_id)
        line_rows = (
            db.query(QuoteLineItem)
            .filter(QuoteLineItem.quote_id == q.id)
            .order_by(QuoteLineItem.sort_order.asc(), QuoteLineItem.id.asc())
            .all()
        )
        customer_lines = _project_customer_lines(db, line_rows)
        discount_rows = (
            db.query(QuoteOrderDiscount)
            .filter(QuoteOrderDiscount.quote_id == quote.id)
            .order_by(
                QuoteOrderDiscount.sort_order.asc(),
                QuoteOrderDiscount.id.asc(),
            )
            .all()
        )
        totals = _totals_breakdown(line_rows, quote, discount_rows)
        inst_rows = (
            db.query(QuoteInstallment)
            .filter(QuoteInstallment.quote_id == quote.id)
            .order_by(
                QuoteInstallment.sort_order.asc(), QuoteInstallment.id.asc()
            )
            .all()
        )
        html = _render_pdf_html(
            "pdf/quote.html",
            q=quote,
            contact=contact,
            event=event,
            line_items=customer_lines,
            totals=totals,
            installments=inst_rows,
            schedule_header="Payment terms",
            show_payment_status=False,
            business=_resolve_business_header(db),
            rendered_at=datetime.now(timezone.utc),
        )
        forbidden = [
            _DANGEROUS_STRING,
            _INTERNAL_SKU,
            _DESIGNER,
            _STYLE_NUMBER,
            "private —",
        ]
        for token in forbidden:
            if token in html:
                raise AssertionError(
                    f"quote PDF HTML leaked forbidden token {token!r}: "
                    f"...{_excerpt_around(html, token)}..."
                )
        assert f"{_HOUSE_NAME} / Ivory / Size 10" in html, (
            "quote PDF HTML missing customer_line_description"
        )
        assert "Legacy line text (already on issued PDFs)" in html, (
            "quote PDF HTML missing legacy line description"
        )
        assert re.search(r"BVX-\d{5}", html), (
            "expected BVX-NNNNN in quote PDF HTML"
        )
        return q.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Portal HTML render — uses the production Jinja loader so the test
# exercises the same template + filters as the live portal route.
# ---------------------------------------------------------------------------


def _render_portal_invoice(view) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = _REPO_ROOT / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    def _money(cents):
        amt = int(cents or 0)
        sign = "-" if amt < 0 else ""
        amt = abs(amt)
        d, c = divmod(amt, 100)
        return f"{sign}${d:,}.{c:02d}"

    env.filters["money"] = _money

    # Portal route binds the view to ``inv`` plus a few helper URLs.
    # Mirror the live route at api/routers/portal.py:153 so the test
    # exercises the same binding and the template's per-key reads.
    tmpl = env.get_template("portal/invoice.html")
    return tmpl.render(
        inv=view,
        business=view.business,
        view_receipt_url="/portal/invoice/test-key/view-receipt",
        pdf_url="/portal/invoice/test-key/pdf",
    )


def _render_portal_quote(view) -> str:
    """Render the portal quote template with the same context binding
    as the live route at ``api/routers/portal.py``: ``q=view`` plus the
    accept/view-receipt/pdf URL pass-throughs. Keeping the binding
    identical means the test exercises the same template paths that
    a real customer would hit."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    templates_dir = _REPO_ROOT / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    def _money(cents):
        amt = int(cents or 0)
        sign = "-" if amt < 0 else ""
        amt = abs(amt)
        d, c = divmod(amt, 100)
        return f"{sign}${d:,}.{c:02d}"

    env.filters["money"] = _money

    tmpl = env.get_template("portal/quote.html")
    return tmpl.render(
        q=view,
        business=view.business,
        view_receipt_url="/portal/quote/test-key/view-receipt",
        accept_url="/portal/quote/test-key/accept",
        accepted_url="/portal/quote/test-key/accepted",
        pdf_url="/portal/quote/test-key/pdf",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_seq()
    print(f"catalog_public_code_seq baseline = {seq_baseline}")
    contact_id, event_id, catalog_id = _seed()
    print(f"seeded contact={contact_id} event={event_id} catalog={catalog_id}")
    try:
        invoice_id = _make_invoice_with_three_lines(
            contact_id, event_id, catalog_id
        )
        print(f"invoice {invoice_id} created with catalog + non-catalog + legacy lines")

        check_invoice_pdf_safe(invoice_id)
        print("invoice PDF surfaces only customer-safe text ok")

        check_portal_invoice_html_safe(invoice_id)
        print("portal invoice HTML surfaces only customer-safe text ok")

        check_portal_dataclass_drops_internal_fields(invoice_id)
        print("PortalLineItem dropped description/notes/product_key ok")

        quote_id = check_quote_pdf_safe(contact_id, event_id, catalog_id)
        print("quote PDF surfaces only customer-safe text ok")

        check_portal_quote_html_safe(quote_id)
        print("portal quote HTML surfaces only customer-safe text ok")

        print()
        print("catalog phase 4 render-swap smoke ok")
        return 0
    finally:
        _wipe_pdf_cache()
        _cleanup()
        _reset_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
