"""Smoke tests for Phase 3 (PDF discount breakdown).

Renders the customer-facing PDF templates via Jinja directly (no
WeasyPrint disk write) so we can assert which totals rows appear for
each of the four representative cases the plan calls out:

- No discounts: only Subtotal, Tax, Total render.
- Order discount only: Subtotal, Order discount, Taxable subtotal, Tax,
  Total, You save all render. Line discount rows do NOT appear.
- Line discount only: Line discounts and Subtotal after line discounts
  render. The Order discount row does not.
- Both: every row renders, and "You save" sums both savings.

Also covers:

- Legacy records (`discount_percent IS NULL`, `discount_cents > 0`)
  keep the four-row layout (Subtotal, Discount, Tax, Total) so they do
  not visually drift from what the customer already received.
- Snapshotted `discount_label` is read off the row (renaming a preset
  on BusinessProfile must not change the printed copy here).
- No em dashes anywhere in the rendered totals copy.

Pure HTML render — no DB session and no FastAPI client needed. Runs as
a script: `venv/bin/python tests/test_pdf_totals_smoke.py`.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from services.invoice_pdf import _render_html, _totals_breakdown  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _line(price_cents: int, *, discount: int = 0, line_total: int | None = None):
    """A duck-typed line row good enough for the templates and the
    `_totals_breakdown` helper."""
    if line_total is None:
        line_total = max(0, price_cents - discount)
    return SimpleNamespace(
        quantity=Decimal("1"),
        unit_price_cents=price_cents,
        discount_cents=discount,
        line_subtotal_cents=max(0, price_cents - discount),
        line_tax_cents=0,
        line_total_cents=line_total,
        # CustomerLineView fields the template touches directly.
        public_code=None,
        display_text="Test line",
        kind="product",
        catalog_item_id=None,
    )


def _customer_lines_from(rows):
    """Project to the customer-facing dict shape `_lineitems.html` reads
    directly. The duck type already exposes those keys, so we just pass
    the rows through."""
    return rows


def _business():
    return SimpleNamespace(
        legal_name="Bella's XV",
        address_lines=[],
        phone=None,
        email=None,
        website=None,
        logo_path=None,
    )


def _contact():
    return SimpleNamespace(
        display_name="Test Customer",
        email=None,
        phone=None,
    )


def _event():
    return SimpleNamespace(
        event_name="Test Quince",
        event_date=date(2026, 12, 5),
    )


def _discount_row(label, percent):
    return SimpleNamespace(label=label, percent=Decimal(str(percent)))


def _invoice(*, subtotal, discount_cents, tax, total):
    return SimpleNamespace(
        id=1,
        invoice_number="INV-TEST-0001",
        status="draft",
        issue_date=date(2026, 5, 1),
        due_date=date(2026, 6, 1),
        revision=1,
        subtotal_cents=subtotal,
        discount_cents=discount_cents,
        tax_cents=tax,
        total_cents=total,
        paid_to_date_cents=0,
        balance_cents=total,
        terms=None,
        footer=None,
        public_notes=None,
    )


def _quote(*, subtotal, discount_cents, tax, total):
    return SimpleNamespace(
        id=1,
        quote_number="Q-TEST-0001",
        status="draft",
        issue_date=date(2026, 5, 1),
        expires_at=date(2026, 6, 1),
        revision=1,
        subtotal_cents=subtotal,
        discount_cents=discount_cents,
        tax_cents=tax,
        total_cents=total,
        terms=None,
        footer=None,
        public_notes=None,
        signature_signed_at=None,
        signature_base64=None,
        signature_name=None,
        signature_ip=None,
    )


def _render_invoice(rows, parent, discount_rows=None):
    return _render_html(
        "pdf/invoice.html",
        inv=parent,
        contact=_contact(),
        event=_event(),
        line_items=_customer_lines_from(rows),
        totals=_totals_breakdown(rows, parent, discount_rows or []),
        installments=[],
        schedule_header="Payment schedule",
        show_payment_status=True,
        business=_business(),
        rendered_at=datetime.now(timezone.utc),
    )


def _render_quote(rows, parent, discount_rows=None):
    return _render_html(
        "pdf/quote.html",
        q=parent,
        contact=_contact(),
        event=_event(),
        line_items=_customer_lines_from(rows),
        totals=_totals_breakdown(rows, parent, discount_rows or []),
        installments=[],
        schedule_header="Payment terms",
        show_payment_status=False,
        business=_business(),
        rendered_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _row(label_text: str) -> str:
    """Marker for an actual totals row in the rendered HTML.

    Searching for the bare label leaks into stylesheet comments and
    document-meta blocks; matching the `<span class="label">…</span>`
    pair pins the search to the totals partial output we care about.
    """
    return f'<span class="label">{label_text}'


def _assert_in(html: str, needle: str, label: str) -> None:
    if needle not in html:
        raise AssertionError(
            f"expected to find {needle!r} in {label}; not present"
        )


def _assert_not_in(html: str, needle: str, label: str) -> None:
    if needle in html:
        raise AssertionError(
            f"unexpectedly found {needle!r} in {label}"
        )


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def check_no_discount_invoice():
    """No line discount, no order discount: only Subtotal, Tax, Total."""
    rows = [_line(100000, line_total=107000)]
    parent = _invoice(
        subtotal=100000, discount_cents=0, tax=7000, total=107000,
    )
    # Lines compute tax in real life; simulate by tagging the row.
    rows[0].line_tax_cents = 7000
    html = _render_invoice(rows, parent)
    _assert_in(html, _row("Subtotal"), "no-discount invoice")
    _assert_in(html, _row("Tax"), "no-discount invoice")
    _assert_in(html, _row("Total"), "no-discount invoice")
    _assert_not_in(html, _row("Line discounts"), "no-discount invoice")
    _assert_not_in(html, _row("Order discount"), "no-discount invoice")
    _assert_not_in(html, _row("You save"), "no-discount invoice")
    _assert_not_in(html, _row("Taxable subtotal"), "no-discount invoice")


def check_order_discount_only_invoice():
    """Percent path with no per-line discount: Subtotal, Order discount,
    Taxable subtotal, Tax, Total, You save."""
    rows = [_line(400000, line_total=385200)]
    rows[0].line_subtotal_cents = 360000
    rows[0].line_tax_cents = 25200
    parent = _invoice(
        subtotal=400000, discount_cents=40000, tax=25200, total=385200,
    )
    html = _render_invoice(
        rows, parent, [_discount_row("Moonlight Ballroom", 10)]
    )
    _assert_in(html, _row("Subtotal"), "order-only invoice")
    _assert_in(html, _row("Order discount"), "order-only invoice")
    _assert_in(html, "Moonlight Ballroom", "order-only invoice")
    _assert_in(html, _row("Taxable subtotal"), "order-only invoice")
    _assert_in(html, _row("Tax"), "order-only invoice")
    _assert_in(html, _row("Total"), "order-only invoice")
    _assert_in(html, _row("You save"), "order-only invoice")
    _assert_not_in(html, _row("Line discounts"), "order-only invoice")
    _assert_not_in(
        html, _row("Subtotal after line discounts"), "order-only invoice"
    )


def check_line_discount_only_invoice():
    """Percent path with line discount but zero order discount. Should
    show Line discounts + Subtotal after line discounts; no Order
    discount row; "You save" still renders."""
    # Line: $1000 unit, $50 line discount, percent path with 0% order.
    rows = [_line(100000, discount=5000, line_total=95000)]
    rows[0].line_subtotal_cents = 95000
    rows[0].line_tax_cents = 0
    parent = _invoice(
        subtotal=95000, discount_cents=0, tax=0, total=95000,
    )
    html = _render_invoice(rows, parent, [_discount_row("Custom", 0)])
    _assert_in(html, _row("Line discounts"), "line-only invoice")
    _assert_in(
        html, _row("Subtotal after line discounts"), "line-only invoice"
    )
    _assert_not_in(html, _row("Order discount"), "line-only invoice")
    # Taxable subtotal row only renders when discount_cents > 0; with a
    # 0% order discount and the percent path, Order discount row is
    # skipped and the Taxable subtotal row is too.
    _assert_not_in(html, _row("Taxable subtotal"), "line-only invoice")
    _assert_in(html, _row("You save"), "line-only invoice")


def check_both_discounts_invoice():
    """Percent path with BOTH line and order discount. Every conditional
    row renders; 'You save' equals line_discount + order_discount."""
    # Line: $1000 unit, $50 line discount; 10% order.
    # pre_order_sub = 95000; subtotal_cents (post-line, pre-order) = 95000.
    # discount_cents (order) = 9500. taxable = 85500. tax = 5985. total = 91485.
    rows = [_line(100000, discount=5000, line_total=91485)]
    rows[0].line_subtotal_cents = 85500
    rows[0].line_tax_cents = 5985
    parent = _invoice(
        subtotal=95000, discount_cents=9500, tax=5985, total=91485,
    )
    html = _render_invoice(
        rows, parent, [_discount_row("Moonlight Ballroom", 10)]
    )
    _assert_in(html, _row("Subtotal"), "stacking invoice")
    _assert_in(html, _row("Line discounts"), "stacking invoice")
    _assert_in(html, _row("Subtotal after line discounts"), "stacking invoice")
    _assert_in(html, _row("Order discount"), "stacking invoice")
    _assert_in(html, "Moonlight Ballroom", "stacking invoice")
    _assert_in(html, _row("Taxable subtotal"), "stacking invoice")
    _assert_in(html, _row("You save"), "stacking invoice")
    # You save = $50 line + $95 order = $145.
    _assert_in(html, "$145.00", "stacking invoice")


def check_legacy_record_keeps_old_layout():
    """Legacy: zero rows in invoice_order_discounts AND discount_cents > 0
    keeps the four-row layout. Customers do not see the new breakdown
    rows. Pre-Phase-7 records that backfilled their snapshot fields
    remain on the percent path; only true legacy rows (never had a
    snapshot) hit this branch."""
    rows = [_line(400000, line_total=400000)]
    rows[0].line_subtotal_cents = 400000
    rows[0].line_tax_cents = 28000
    parent = _invoice(
        subtotal=400000, discount_cents=40000, tax=28000, total=388000,
    )
    html = _render_invoice(rows, parent)
    _assert_in(html, _row("Subtotal"), "legacy invoice")
    _assert_in(html, _row("Discount"), "legacy invoice")
    _assert_in(html, _row("Tax"), "legacy invoice")
    _assert_in(html, _row("Total"), "legacy invoice")
    _assert_not_in(html, _row("Line discounts"), "legacy invoice")
    _assert_not_in(html, _row("Order discount"), "legacy invoice")
    _assert_not_in(
        html, _row("Subtotal after line discounts"), "legacy invoice"
    )
    _assert_not_in(html, _row("Taxable subtotal"), "legacy invoice")


def check_snapshot_label_renders_verbatim():
    """The order-discount row uses the row's snapshotted `label` even
    when it differs from any current BusinessProfile preset."""
    rows = [_line(100000, line_total=99000)]
    rows[0].line_subtotal_cents = 99000
    rows[0].line_tax_cents = 0
    parent = _invoice(
        subtotal=100000, discount_cents=1000, tax=0, total=99000,
    )
    html = _render_invoice(
        rows, parent, [_discount_row("Moonlight Ballroom (original)", 1)]
    )
    _assert_in(html, "Moonlight Ballroom (original)", "snapshot-label invoice")


def check_no_em_dashes_in_totals():
    """The totals partial copy must not contain em dashes per the
    project's customer-copy rule. Forward slashes / plain phrasing
    only."""
    src = (_REPO_ROOT / "templates" / "pdf" / "_totals.html").read_text()
    if "—" in src:
        raise AssertionError("found em dash in templates/pdf/_totals.html")


def check_quote_uses_same_breakdown():
    """The same breakdown logic drives the quote PDF too."""
    rows = [_line(400000, line_total=385200)]
    rows[0].line_subtotal_cents = 360000
    rows[0].line_tax_cents = 25200
    parent = _quote(
        subtotal=400000, discount_cents=40000, tax=25200, total=385200,
    )
    html = _render_quote(
        rows, parent, [_discount_row("Moonlight Ballroom", 10)]
    )
    _assert_in(html, _row("Order discount"), "quote")
    _assert_in(html, "Moonlight Ballroom", "quote")
    _assert_in(html, _row("You save"), "quote")


def check_taxable_subtotal_arithmetic():
    """The taxable subtotal must equal pre-order subtotal minus order
    discount in the new path, so the printed $ values add up."""
    rows = [_line(400000, line_total=385200)]
    rows[0].line_subtotal_cents = 360000
    rows[0].line_tax_cents = 25200
    parent = _invoice(
        subtotal=400000, discount_cents=40000, tax=25200, total=385200,
    )
    breakdown = _totals_breakdown(
        rows, parent, [_discount_row("Moonlight Ballroom", 10)]
    )
    assert breakdown["taxable_subtotal_cents"] == 360000, breakdown
    assert breakdown["you_save_cents"] == 40000, breakdown


def check_stacked_discounts_render_separately():
    """Phase 7: each row in the discount stack gets its own line
    in the PDF totals block, with per-row dollars-off."""
    rows = [_line(400000, line_total=372000)]  # 400000 * 0.93 = 372000
    rows[0].line_subtotal_cents = 372000
    rows[0].line_tax_cents = 0
    # 5% Military + 2% Same-day = 7% combined off the taxable base.
    # 400000 - 28000 = 372000.
    parent = _invoice(
        subtotal=400000, discount_cents=28000, tax=0, total=372000,
    )
    html = _render_invoice(
        rows,
        parent,
        [_discount_row("Military", 5), _discount_row("Same-day", 2)],
    )
    _assert_in(html, "Military", "stacked discounts")
    _assert_in(html, "Same-day", "stacked discounts")
    _assert_in(html, _row("Taxable subtotal"), "stacked discounts")
    _assert_in(html, _row("You save"), "stacked discounts")
    # Per-row savings: 5% of 400000 = 20000; remainder 8000 = 2%.
    # The template renders each on its own row.
    _assert_in(html, "$200.00", "stacked discounts")  # Military savings
    _assert_in(html, "$80.00", "stacked discounts")  # Same-day savings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    check_no_discount_invoice()
    print("no-discount invoice: only Subtotal/Tax/Total ok")

    check_order_discount_only_invoice()
    print("order-only invoice: 6-row breakdown + You save ok")

    check_line_discount_only_invoice()
    print("line-only invoice: line discount rows render, no order row ok")

    check_both_discounts_invoice()
    print("both-discounts invoice: every row, You save sums both ok")

    check_legacy_record_keeps_old_layout()
    print("legacy record keeps the old four-row layout ok")

    check_snapshot_label_renders_verbatim()
    print("snapshotted discount_label renders verbatim ok")

    check_quote_uses_same_breakdown()
    print("quote PDF reuses the same breakdown ok")

    check_taxable_subtotal_arithmetic()
    print("taxable subtotal math agrees with line totals ok")

    check_stacked_discounts_render_separately()
    print("stacked discounts each render on their own row ok")

    check_no_em_dashes_in_totals()
    print("no em dashes in customer copy ok")

    print()
    print("phase 3 PDF totals smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
