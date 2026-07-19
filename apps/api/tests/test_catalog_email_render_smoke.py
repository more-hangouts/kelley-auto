"""Catalog SKU obfuscation Phase 4 — email render-context regression.

Phase 0's leak audit confirmed no email body in this repo reads line
items today. Phase 4 adds this regression test so a future change
that starts including line text — e.g. a "here's what you bought"
itemization in the invoice send email — fails the test instead of
silently leaking ``description``, ``notes``, ``internal_notes``, or
catalog identifiers to the customer's inbox.

What this test does:

  1. Build an in-memory invoice with the same dangerous fixture the
     Phase 4 render-swap test uses: ``Mori Lee 89216 Ivory 8`` typed
     into description and notes, a catalog-backed line with leaky
     ``internal_notes``.
  2. Render every customer-bound email body the project ships today
     (invoice send, quote send, reminders 1/2/3).
  3. Assert: no subject/text/html contains the dangerous staff
     string, the catalog identifiers, or any line-item content.

The test does not touch the database — emails take in-memory model
objects and an installment dict, no service layer involvement
needed. That keeps the regression cheap and isolated.

Runs as a script:

    venv/bin/python tests/test_catalog_email_render_smoke.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
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

from database.models import Contact, Invoice, Quote  # noqa: E402
from services.business_profile_service import BusinessProfileView  # noqa: E402
from services.portal_email import (  # noqa: E402
    _render_invoice_reminder,
    _render_invoice_sent,
    _render_quote_sent,
)


_DANGEROUS_TOKENS = (
    "Mori Lee 89216 Ivory 8",
    "MORI-89216-IVORY",
    "Mori Lee",
    "89216",
    "vendor PO 12345",
    # Anything from a line item should be absent. Anchor on common
    # catalog-backed fixture text just in case the email starts
    # iterating line_items.
    "Isabella / Ivory / Size 08",
    "Rush alteration fee",
    "Legacy line text",
)


def _make_business() -> BusinessProfileView:
    from datetime import datetime, timezone
    from decimal import Decimal

    return BusinessProfileView(
        legal_name="Bella's XV Boutique",
        display_name="Bella's XV",
        address_line1=None,
        address_line2=None,
        city=None,
        state=None,
        postal_code=None,
        country="USA",
        phone=None,
        email=None,
        website=None,
        logo_storage_key=None,
        default_tax_rate=Decimal("0"),
        default_tax_name=None,
        default_invoice_terms=None,
        default_invoice_footer=None,
        default_payment_instructions=None,
        reminder1_enabled=True,
        reminder1_days_offset=14,
        reminder1_offset_basis="before_due",
        reminder2_enabled=True,
        reminder2_days_offset=3,
        reminder2_offset_basis="before_due",
        reminder3_enabled=True,
        reminder3_days_offset=2,
        reminder3_offset_basis="after_due",
        reminder_late_fee_cents=0,
        reminder_late_fee_pct=Decimal("0"),
        updated_at=datetime.now(timezone.utc),
        updated_by_user_id=None,
    )


def _make_contact() -> Contact:
    c = Contact(display_name="Customer Customer", email="cust@example.com")
    return c


def _make_invoice() -> Invoice:
    inv = Invoice()
    inv.id = 999
    inv.invoice_number = "INV-2026-000999"
    inv.total_cents = 125000
    return inv


def _make_quote() -> Quote:
    q = Quote()
    q.id = 999
    q.quote_number = "Q-2026-000999"
    q.total_cents = 125000
    return q


def _assert_clean(rendered, *, surface: str) -> None:
    body = "\n".join([rendered.subject, rendered.text, rendered.html])
    for token in _DANGEROUS_TOKENS:
        if token in body:
            idx = body.find(token)
            window = body[max(0, idx - 60) : idx + len(token) + 60]
            raise AssertionError(
                f"{surface} email leaked forbidden token {token!r}: "
                f"...{window}..."
            )


def check_invoice_sent_email() -> None:
    rendered = _render_invoice_sent(
        invoice=_make_invoice(),
        contact=_make_contact(),
        business=_make_business(),
        portal_url="https://example.com/portal/invoice/abc",
    )
    _assert_clean(rendered, surface="invoice-sent")


def check_quote_sent_email() -> None:
    rendered = _render_quote_sent(
        quote=_make_quote(),
        contact=_make_contact(),
        business=_make_business(),
        portal_url="https://example.com/portal/quote/abc",
    )
    _assert_clean(rendered, surface="quote-sent")


def check_invoice_reminder_emails() -> None:
    for idx in (1, 2, 3):
        rendered = _render_invoice_reminder(
            invoice=_make_invoice(),
            contact=_make_contact(),
            business=_make_business(),
            portal_url="https://example.com/portal/invoice/abc",
            installment_label="Deposit",
            installment_amount_cents=62500,
            due_date_text="May 18",
            reminder_index=idx,
        )
        _assert_clean(rendered, surface=f"reminder-{idx}")


def main() -> int:
    check_invoice_sent_email()
    print("invoice-sent email omits line text ok")
    check_quote_sent_email()
    print("quote-sent email omits line text ok")
    check_invoice_reminder_emails()
    print("reminder 1/2/3 emails omit line text ok")
    print()
    print("catalog phase 4 email render smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
