"""PDF generation for invoices, quotes, and payment receipts.

Phase 8. Renders HTML+CSS templates from ``templates/pdf/`` through
WeasyPrint and caches the bytes in ``document_storage``. The cache keys
are revision-scoped so a ``patch_invoice`` that bumps ``revision`` does
not need to actively invalidate anything — the next download just looks
at a different key. Receipts have no revision because they are
immutable once rendered.

Design notes:

  - **Atomic writes.** Render to a temp file in the same directory,
    fsync, then ``os.rename`` to the final cache key. WeasyPrint can
    raise mid-render; without atomic write a half-PDF can sit at the
    final key forever and serve to a customer. The rename is the cache
    insert.
  - **Lazy.** Routes call ``ensure_invoice_pdf`` (which renders if
    missing for the current revision, returns the path either way).
    A fresh deploy doesn't have to backfill PDFs for every existing
    invoice.
  - **Render error stamping.** On WeasyPrint or storage failure, the
    error message gets written to ``last_pdf_render_error`` on the
    invoice/quote so the staff editor can show a Retry button. On
    success the field is cleared. Never silently log-and-continue —
    the error needs to be visible somewhere the staff actually look.
  - **Business profile fallbacks.** A missing singleton (or missing
    logo) renders to a text-only header rather than crashing the
    download. Customers care about the numbers, not the brand asset.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from database.models import (
    CatalogItem,
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    InvoiceLineItem,
    InvoiceOrderDiscount,
    Payment,
    PaymentAllocation,
    Quote,
    QuoteInstallment,
    QuoteLineItem,
    QuoteOrderDiscount,
)
from services import document_storage
from services.business_profile_service import (
    BusinessProfileError,
    BusinessProfileView,
    get_profile,
)
from services.catalog_service import assert_public_render_keys, customer_line_view

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"


class PdfRenderError(Exception):
    """Render-time failure. Surfaced as 503 by the router with code
    ``pdf_render_failed``. The error message has already been stamped
    onto the invoice/quote when this is raised so the staff UI can
    show a Retry render action."""

    def __init__(self, message: str, *, code: str = "pdf_render_failed") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Jinja environment — separate from the portal templates so a portal
# template change can't accidentally regress the PDF layout
# ---------------------------------------------------------------------------


def _money(cents: Any) -> str:
    try:
        amt = int(cents)
    except (TypeError, ValueError):
        return "$0.00"
    sign = "-" if amt < 0 else ""
    amt = abs(amt)
    dollars, c = divmod(amt, 100)
    return f"{sign}${dollars:,}.{c:02d}"


def _format_date(d: Any) -> str:
    if d is None:
        return ""
    try:
        return d.strftime("%B %-d, %Y")
    except Exception:  # pragma: no cover — non-POSIX strftime
        return d.isoformat()


def _format_qty(q: Any) -> str:
    """Render a Decimal quantity as a clean string (no E-notation)."""
    if q is None:
        return ""
    try:
        return format(q, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return str(q)


def _format_phone(raw: Any) -> str:
    """Render a stored phone for human display.

    US 10- or 11-digit (with leading ``1``) numbers render as
    ``+1 (210) 310-4945``. Any other E.164-shaped value is preserved
    with a ``+`` prefix and its digits ungrouped (we don't try to guess
    grouping for non-NANP numbering plans). Anything unparseable falls
    back to the raw input so we never blank out a stored value.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"+1 ({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if s.startswith("+"):
        return f"+{digits}"
    return s


_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
_jinja.filters["money"] = _money
_jinja.filters["fdate"] = _format_date
_jinja.filters["qty"] = _format_qty
_jinja.filters["phone"] = _format_phone


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _totals_breakdown(
    line_rows: list[Any],
    parent: Any,
    order_discounts: list[Any] | None = None,
) -> dict[str, Any]:
    """Derive the customer-facing totals dict the PDF totals partial reads.

    Two paths gated on whether the record carries any rows in
    ``invoice_order_discounts`` / ``quote_order_discounts``:

    - **New (one or more order discounts).** The seven-row layout.
      ``subtotal_pre_discount_cents`` is the gross sum (no discounts at
      all); ``parent.subtotal_cents`` is the post-line-discount,
      pre-order-discount taxable base. The combined order discount
      shrinks the taxable base; ``parent.discount_cents`` is the
      derived combined dollars-off; ``you_save_cents`` sums line and
      order discounts so the customer sees one total-savings line.
      Each order-discount row gets a per-row dollars-off entry under
      ``order_discount_rows`` so the template can list them
      individually.
    - **Legacy (no order discounts).** The four-row layout from before
      this work. The template branches on ``pct_path`` and renders
      ``Subtotal / Discount / Tax / Total`` exactly as it did
      pre-Phase-3 / pre-Phase-7.
    """
    line_disc_total = sum(int(li.discount_cents or 0) for li in line_rows)
    pre_discount = 0
    for li in line_rows:
        qty = Decimal(str(li.quantity))
        unit = Decimal(int(li.unit_price_cents))
        pre_discount += int(
            (qty * unit).to_integral_value(rounding=ROUND_HALF_EVEN)
        )

    discount_cents = int(parent.discount_cents or 0)
    discounts = order_discounts or []
    pct_path = len(discounts) > 0

    # Per-row dollars-off so the template can list each discount.
    # Math mirrors `_recompute_totals`: each row's savings = round(
    # taxable_subtotal * row_pct / 100). Rounding crumbs land on the
    # last row so the row sum equals the parent's `discount_cents`
    # exactly, no matter how many discounts are stacked.
    #
    # Zero-percent rows are filtered out of the rendered list — they
    # contribute no savings and would just clutter the totals block.
    # They still mark the record as percent-path so the rest of the
    # layout (taxable subtotal, you-save) reflects the new math.
    rows_breakdown: list[dict[str, Any]] = []
    if pct_path:
        taxable = int(parent.subtotal_cents or 0)
        running = 0
        for idx, row in enumerate(discounts):
            row_pct = Decimal(str(row.percent))
            if idx == len(discounts) - 1:
                row_savings = discount_cents - running
            else:
                row_savings = int(
                    (Decimal(taxable) * row_pct / Decimal(100))
                    .to_integral_value(rounding=ROUND_HALF_EVEN)
                )
                running += row_savings
            if row_pct == 0:
                continue
            rows_breakdown.append(
                {
                    "label": row.label,
                    "percent": row_pct,
                    "savings_cents": row_savings,
                }
            )

    you_save = line_disc_total + discount_cents

    return {
        "pct_path": pct_path,
        "order_discount_rows": rows_breakdown,
        "subtotal_pre_discount_cents": pre_discount,
        "line_discount_total_cents": line_disc_total,
        # Post-line-discount sub stored on the parent in both paths.
        "subtotal_after_line_discounts_cents": int(parent.subtotal_cents or 0),
        # Taxable base only meaningful in the percent path.
        "taxable_subtotal_cents": (
            int(parent.subtotal_cents or 0) - discount_cents
            if pct_path
            else int(parent.subtotal_cents or 0)
        ),
        "discount_cents": discount_cents,
        "tax_cents": int(parent.tax_cents or 0),
        "total_cents": int(parent.total_cents or 0),
        "you_save_cents": you_save,
    }


def _project_customer_lines(
    db: Session, line_rows: list[Any]
) -> list[Any]:
    """Resolve catalog snapshots in one batch and project every line
    through ``catalog_service.customer_line_view`` so the PDF partial
    reads only customer-safe fields. The partial never sees
    ``internal_sku``, ``designer``, ``style_number``, ``internal_notes``,
    ``product_key``, or the legacy ``notes`` column on any code path.
    """
    catalog_ids = {
        int(li.catalog_item_id) for li in line_rows if li.catalog_item_id
    }
    catalog_by_id: dict[int, CatalogItem] = {}
    if catalog_ids:
        rows = (
            db.query(CatalogItem)
            .filter(CatalogItem.id.in_(catalog_ids))
            .all()
        )
        catalog_by_id = {int(r.id): r for r in rows}
    projected = [
        customer_line_view(li, catalog_by_id.get(li.catalog_item_id))
        for li in line_rows
    ]
    assert_public_render_keys(projected)
    return projected


@dataclass
class _BusinessHeader:
    legal_name: str
    address_lines: list[str]
    phone: str | None
    email: str | None
    website: str | None
    logo_path: str | None  # Absolute fs path or None — WeasyPrint reads via file://


def _resolve_business_header(db: Session) -> _BusinessHeader:
    try:
        view: BusinessProfileView = get_profile(db)
    except BusinessProfileError:
        return _BusinessHeader(
            legal_name="Bella's XV",
            address_lines=[],
            phone=None,
            email=None,
            website=None,
            logo_path=None,
        )
    address_lines: list[str] = []
    if view.address_line1:
        address_lines.append(view.address_line1)
    if view.address_line2:
        address_lines.append(view.address_line2)
    state_zip = " ".join(filter(None, [view.state, view.postal_code]))
    city_state = ", ".join(filter(None, [view.city, state_zip]))
    if city_state:
        address_lines.append(city_state)

    logo_path: str | None = None
    if view.logo_storage_key:
        try:
            candidate = document_storage.resolve_path(view.logo_storage_key)
        except ValueError:
            candidate = None
        if candidate and candidate.is_file():
            logo_path = str(candidate)

    return _BusinessHeader(
        legal_name=view.legal_name,
        address_lines=address_lines,
        phone=view.phone,
        email=view.email,
        website=view.website,
        logo_path=logo_path,
    )


def _render_html(template_name: str, **ctx: Any) -> str:
    return _jinja.get_template(template_name).render(**ctx)


def _write_pdf_atomic(html_string: str, target_key: str) -> Path:
    """Render `html_string` to PDF and write it to `target_key` via a
    same-directory temp file + rename. Returns the final path."""
    # Defer the import so a fresh checkout that hasn't installed
    # weasyprint yet only fails on the call path, not at module load.
    from weasyprint import HTML  # noqa: WPS433  (intentional local import)

    final_path = document_storage.resolve_path(target_key)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".pdf-", suffix=".tmp", dir=str(final_path.parent)
    )
    try:
        os.close(fd)
        # base_url = the templates dir so file:// references and
        # logo paths can be resolved relative to a known root. Logo
        # paths in our templates are absolute so this is only a
        # belt-and-suspenders default.
        HTML(string=html_string, base_url=str(_TEMPLATES_DIR)).write_pdf(
            target=tmp_name
        )
        with open(tmp_name, "rb") as rendered:
            os.fsync(rendered.fileno())
        os.replace(tmp_name, final_path)
    except Exception:
        # Clean up the temp file on failure so we don't accumulate
        # orphans in document_storage.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return final_path


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------


def _invoice_cache_key(invoice: Invoice, revision: int | None = None) -> str:
    rev = revision if revision is not None else int(invoice.revision or 1)
    return f"invoices/{invoice.id}/{rev}.pdf"


def _stamp_invoice_success(invoice: Invoice, revision: int) -> None:
    invoice.last_pdf_rendered_revision = revision
    invoice.last_pdf_rendered_at = datetime.now(timezone.utc)
    invoice.last_pdf_render_error = None
    invoice.updated_at = datetime.now(timezone.utc)


def _stamp_invoice_failure(invoice: Invoice, error: str) -> None:
    invoice.last_pdf_render_error = (error or "")[:500]
    invoice.updated_at = datetime.now(timezone.utc)


def render_invoice_pdf(db: Session, *, invoice_id: int) -> Path:
    """Force-render the invoice PDF at the current revision. Used by
    the staff Retry path. Caching writes the result to the
    revision-scoped cache key — a successful retry overwrites the
    stale cached file at the same key (since we can only retry the
    *current* revision)."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise PdfRenderError("invoice not found", code="invoice_not_found")
    return _render_invoice_to_disk(db, invoice=invoice)


def ensure_invoice_pdf(db: Session, *, invoice_id: int) -> Path:
    """Lazy: return the cached path if the file already exists for the
    current revision; render otherwise. The first download after a
    revision bump triggers the re-render."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise PdfRenderError("invoice not found", code="invoice_not_found")
    key = _invoice_cache_key(invoice)
    if document_storage.object_exists(key):
        return document_storage.resolve_path(key)
    return _render_invoice_to_disk(db, invoice=invoice)


def _render_invoice_to_disk(db: Session, *, invoice: Invoice) -> Path:
    revision = int(invoice.revision or 1)
    key = _invoice_cache_key(invoice, revision)

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
        .order_by(
            InvoiceInstallment.sort_order.asc(), InvoiceInstallment.id.asc()
        )
        .all()
    )
    business = _resolve_business_header(db)

    html_string = _render_html(
        "pdf/invoice.html",
        inv=invoice,
        contact=contact,
        event=event,
        line_items=customer_lines,
        totals=totals,
        installments=inst_rows,
        # Phase 6: shared schedule partial. Invoices show a Status
        # column; the historical "Payment schedule" header stays.
        schedule_header="Payment schedule",
        show_payment_status=True,
        business=business,
        rendered_at=datetime.now(timezone.utc),
    )
    try:
        path = _write_pdf_atomic(html_string, key)
    except Exception as exc:
        _stamp_invoice_failure(invoice, f"{type(exc).__name__}: {exc!s}")
        db.flush()
        log.exception(
            "invoice_pdf.render_failed",
            extra={"invoice_id": invoice.id, "revision": revision},
        )
        raise PdfRenderError(str(exc)) from exc
    _stamp_invoice_success(invoice, revision)
    db.flush()
    log.info(
        "invoice_pdf.rendered",
        extra={
            "invoice_id": invoice.id,
            "revision": revision,
            "bytes": path.stat().st_size,
        },
    )
    return path


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


def _quote_cache_key(quote: Quote, revision: int | None = None) -> str:
    rev = revision if revision is not None else int(quote.revision or 1)
    return f"quotes/{quote.id}/{rev}.pdf"


def invalidate_quote_pdf(quote: Quote) -> None:
    """Drop the cached quote PDF for the current revision.

    Approve paths (customer portal sign + staff in-store sign) call this
    after stamping the signature so the next ``ensure_quote_pdf`` re-
    renders with the signature block instead of returning the cached
    pre-signature copy. Best-effort: no-op if the cache file is gone.
    """
    document_storage.delete_object(_quote_cache_key(quote))


def _stamp_quote_success(quote: Quote, revision: int) -> None:
    quote.last_pdf_rendered_revision = revision
    quote.last_pdf_rendered_at = datetime.now(timezone.utc)
    quote.last_pdf_render_error = None
    quote.updated_at = datetime.now(timezone.utc)


def _stamp_quote_failure(quote: Quote, error: str) -> None:
    quote.last_pdf_render_error = (error or "")[:500]
    quote.updated_at = datetime.now(timezone.utc)


def render_quote_pdf(db: Session, *, quote_id: int) -> Path:
    quote = db.get(Quote, quote_id)
    if quote is None:
        raise PdfRenderError("quote not found", code="quote_not_found")
    return _render_quote_to_disk(db, quote=quote)


def ensure_quote_pdf(db: Session, *, quote_id: int) -> Path:
    quote = db.get(Quote, quote_id)
    if quote is None:
        raise PdfRenderError("quote not found", code="quote_not_found")
    key = _quote_cache_key(quote)
    if document_storage.object_exists(key):
        return document_storage.resolve_path(key)
    return _render_quote_to_disk(db, quote=quote)


def _render_quote_to_disk(db: Session, *, quote: Quote) -> Path:
    revision = int(quote.revision or 1)
    key = _quote_cache_key(quote, revision)

    contact = db.get(Contact, quote.contact_id)
    event = db.get(Event, quote.event_id)
    line_rows = (
        db.query(QuoteLineItem)
        .filter(QuoteLineItem.quote_id == quote.id)
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
    # Phase 6: quotes carry an optional payment schedule (Phase 4
    # added the table). When present, the PDF renders the same
    # schedule block invoices use, minus the Status column —
    # quotes never carry payment state.
    inst_rows = (
        db.query(QuoteInstallment)
        .filter(QuoteInstallment.quote_id == quote.id)
        .order_by(
            QuoteInstallment.sort_order.asc(), QuoteInstallment.id.asc()
        )
        .all()
    )
    business = _resolve_business_header(db)

    html_string = _render_html(
        "pdf/quote.html",
        q=quote,
        contact=contact,
        event=event,
        line_items=customer_lines,
        totals=totals,
        installments=inst_rows,
        schedule_header="Payment terms",
        show_payment_status=False,
        business=business,
        rendered_at=datetime.now(timezone.utc),
    )
    try:
        path = _write_pdf_atomic(html_string, key)
    except Exception as exc:
        _stamp_quote_failure(quote, f"{type(exc).__name__}: {exc!s}")
        db.flush()
        log.exception(
            "quote_pdf.render_failed",
            extra={"quote_id": quote.id, "revision": revision},
        )
        raise PdfRenderError(str(exc)) from exc
    _stamp_quote_success(quote, revision)
    db.flush()
    log.info(
        "quote_pdf.rendered",
        extra={
            "quote_id": quote.id,
            "revision": revision,
            "bytes": path.stat().st_size,
        },
    )
    return path


# ---------------------------------------------------------------------------
# Payment receipt
# ---------------------------------------------------------------------------


def _receipt_cache_key(payment: Payment) -> str:
    return f"receipts/{payment.id}.pdf"


def render_payment_receipt_pdf(db: Session, *, payment_id: int) -> Path:
    """Receipts are immutable. The cache key has no revision; once
    rendered we always serve the cached bytes. Re-rendering is a noop
    if the file exists."""
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise PdfRenderError("payment not found", code="payment_not_found")
    key = _receipt_cache_key(payment)
    if document_storage.object_exists(key):
        return document_storage.resolve_path(key)
    return _render_receipt_to_disk(db, payment=payment)


def ensure_payment_receipt_pdf(db: Session, *, payment_id: int) -> Path:
    return render_payment_receipt_pdf(db, payment_id=payment_id)


def _render_receipt_to_disk(db: Session, *, payment: Payment) -> Path:
    contact = db.get(Contact, payment.contact_id)
    business = _resolve_business_header(db)
    allocation_rows = (
        db.query(PaymentAllocation, Invoice)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .filter(PaymentAllocation.payment_id == payment.id)
        .order_by(PaymentAllocation.id.asc())
        .all()
    )
    allocations = [
        {
            "invoice_number": inv.invoice_number or f"#{inv.id}",
            "applied_cents": int(alloc.applied_cents),
            "refunded_cents": int(alloc.refunded_cents or 0),
            "invoice_total_cents": int(inv.total_cents),
            "invoice_balance_cents": int(inv.balance_cents),
        }
        for alloc, inv in allocation_rows
    ]
    html_string = _render_html(
        "pdf/receipt.html",
        p=payment,
        contact=contact,
        allocations=allocations,
        business=business,
        rendered_at=datetime.now(timezone.utc),
    )
    key = _receipt_cache_key(payment)
    try:
        path = _write_pdf_atomic(html_string, key)
    except Exception as exc:
        log.exception(
            "payment_receipt_pdf.render_failed",
            extra={"payment_id": payment.id},
        )
        # Receipts have no error column on the model; surface as
        # PdfRenderError so the route returns 503. The next render
        # attempt simply re-runs.
        raise PdfRenderError(str(exc)) from exc
    log.info(
        "payment_receipt_pdf.rendered",
        extra={"payment_id": payment.id, "bytes": path.stat().st_size},
    )
    return path


# ---------------------------------------------------------------------------
# Filename helpers — used by the routers' Content-Disposition headers
# ---------------------------------------------------------------------------


def invoice_pdf_filename(invoice: Invoice) -> str:
    label = invoice.invoice_number or f"draft-{invoice.id}"
    return f"invoice-{label}.pdf"


def quote_pdf_filename(quote: Quote) -> str:
    label = quote.quote_number or f"draft-{quote.id}"
    return f"quote-{label}.pdf"


def receipt_pdf_filename(payment: Payment) -> str:
    label = payment.payment_number or f"payment-{payment.id}"
    return f"receipt-{label}.pdf"
