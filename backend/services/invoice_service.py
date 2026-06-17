"""Invoice domain service.

Holds money math, schedule validation, numbering, and the lifecycle
transitions for invoices. Pure-Python; no FastAPI imports. The router is
a thin translation layer over this.

Money math rule: every multiplication that produces cents
(`quantity * unit_price`, `subtotal * tax_rate`) runs in `Decimal` with
`ROUND_HALF_EVEN` to integer cents. Never `float`. The line-level rounding
is final — `total_cents = SUM(line_total_cents) - invoice.discount_cents`
so the printed invoice and the stored total agree by construction.

Numbering: `invoice_number` is allocated only at first send via a
`SELECT ... FOR UPDATE` row lock on `numbering_state`. Drafts have no
number. Cancelled invoices keep their number forever.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from database.models import (
    CatalogItem,
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    InvoiceInvitation,
    InvoiceLineItem,
    InvoiceOrderDiscount,
    Quote,
)
from services import activity_log
from services.catalog_service import (
    CatalogServiceError,
    assert_no_catalog_leak,
    assert_no_public_catalog_leaks,
)
from services.discount_snapshot import (
    DiscountRowInput,
    DiscountRowSnapshot,
    DiscountSnapshotError,
    snapshot_order_discounts,
)


class InvoiceServiceError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invoice_error",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Inputs (router builds these from Pydantic models)
# ---------------------------------------------------------------------------


@dataclass
class LineItemInput:
    """Line-item input shape shared by the invoice and quote services.

    Catalog SKU obfuscation Phase 2 introduces four catalog-aware fields
    (``catalog_item_id``, ``size_label``, ``public_description``,
    ``internal_notes``) and demotes the legacy ``description`` /
    ``notes`` to optional fields so existing callers keep working
    through the transition.

    Two valid shapes:

    1. **Catalog-backed line.** ``catalog_item_id`` is set; staff-typed
       customer copy is rejected (``description``, ``notes``, and
       ``public_description`` must all be ``None``). Customer-facing
       text comes from ``services.catalog_service.customer_line_description``
       at render time. ``size_label`` and ``internal_notes`` are
       optional staff-side context.

    2. **Non-catalog line.** ``catalog_item_id`` is ``None``. The
       customer-facing copy comes from ``public_description`` (preferred)
       or, transitionally, the legacy ``description`` field. Staff
       context goes into ``internal_notes`` (preferred) or the legacy
       ``notes`` field. The service routes either spelling into the new
       columns; until Phase 4's render swap ships, it also mirrors the
       chosen public copy into the legacy ``description`` column so
       PDFs and portal pages keep rendering customer text.
    """

    quantity: Decimal
    unit_price_cents: int
    kind: str = "product"
    sort_order: int | None = None
    product_key: str | None = None
    discount_cents: int = 0
    tax_rate: Decimal = Decimal("0")
    tax_name: str | None = None

    catalog_item_id: int | None = None
    size_label: str | None = None
    public_description: str | None = None
    internal_notes: str | None = None

    # Legacy fields kept for backward compatibility. New callers should
    # prefer ``public_description`` and ``internal_notes``. Routers and
    # tests that still pass ``description=`` continue to work; the
    # service translates them at write time.
    description: str | None = None
    notes: str | None = None


@dataclass
class InstallmentInput:
    label: str
    amount_cents: int
    due_date: date
    sort_order: int | None = None
    staff_notes: str | None = None


# Editable fields the service will accept on update_invoice. The
# stacked order-discount list (`order_discounts`) is handled out-of-
# band via `discount_snapshot.snapshot_order_discounts` because it
# cross-validates against BusinessProfile, but it lives in the same
# `extra="forbid"` allowlist so unknown fields still get rejected.
_INVOICE_SCALAR_FIELDS = {
    "discount_cents",
    "issue_date",
    "terms",
    "footer",
    "public_notes",
    "private_notes",
    "po_number",
}


# Statuses where the invoice is locked against further edits.
_LOCKED_STATUSES = frozenset({"paid", "cancelled", "reversed"})


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


def create_invoice(
    db: Session,
    *,
    event_id: int,
    contact_id: int,
    line_items: list[LineItemInput] | None = None,
    installments: list[InstallmentInput] | None = None,
    discount_cents: int = 0,
    order_discounts: list[DiscountRowInput] | list[dict] | None = None,
    issue_date: date | None = None,
    terms: str | None = None,
    footer: str | None = None,
    public_notes: str | None = None,
    private_notes: str | None = None,
    po_number: str | None = None,
    custom_amounts: bool = False,
    actor_user_id: int | None = None,
) -> Invoice:
    """Create a draft invoice. Number is NOT assigned (mark_sent does that).

    Empty line items and empty schedule are allowed for drafts. The send
    transition checks both. If a non-empty schedule is provided, its sum
    must already equal the computed total.
    """
    if db.get(Event, event_id) is None:
        raise InvoiceServiceError("event not found", code="event_not_found")
    if db.get(Contact, contact_id) is None:
        raise InvoiceServiceError("contact not found", code="contact_not_found")
    try:
        assert_no_public_catalog_leaks(
            db,
            {
                "terms": terms,
                "footer": footer,
                "public_notes": public_notes,
            },
        )
    except CatalogServiceError as exc:
        raise InvoiceServiceError(str(exc), code=exc.code, **exc.extra) from exc

    try:
        discount_snaps = snapshot_order_discounts(db, order_discounts)
    except DiscountSnapshotError as exc:
        raise InvoiceServiceError(
            str(exc), code=exc.code, **exc.extra
        ) from exc

    invoice = Invoice(
        event_id=event_id,
        contact_id=contact_id,
        status="draft",
        issue_date=issue_date or date.today(),
        revision=1,
        # On the percent path (one or more order discounts), discount_cents
        # is derived in _recompute_totals; the caller-provided value is
        # ignored. With zero discount rows, the legacy flat-amount path
        # honours the caller's discount_cents.
        discount_cents=int(discount_cents or 0) if not discount_snaps else 0,
        terms=terms,
        footer=footer,
        public_notes=public_notes,
        private_notes=private_notes,
        po_number=po_number,
        created_by_user_id=actor_user_id,
        sold_by_user_id=actor_user_id,
    )
    db.add(invoice)
    db.flush()  # need invoice.id for FKs

    _replace_order_discounts(db, invoice, discount_snaps)
    _replace_line_items(db, invoice, line_items or [])
    _replace_installments(db, invoice, installments or [])
    _recompute_totals(db, invoice)
    _refresh_due_date(db, invoice)

    if installments:
        # Schedule shape (count + sum) is the more fundamental check;
        # the deposit floor only matters once the schedule balances.
        _validate_schedule(db, invoice)
        _validate_plan_inputs(
            installments,
            int(invoice.total_cents or 0),
            custom_amounts=custom_amounts,
        )

    db.flush()
    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_CREATED,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={"total_cents": int(invoice.total_cents or 0)},
    )
    return invoice


def update_invoice(
    db: Session,
    *,
    invoice_id: int,
    patch: dict[str, Any],
    actor_user_id: int | None = None,
) -> Invoice:
    """Apply a partial update.

    `patch` carries only the fields the caller actually sent (router uses
    `model_dump(exclude_unset=True)`). Special keys: `line_items` (list,
    replaces all rows when present) and `installments` (list, replaces).

    Refuses to edit a locked invoice. Bumps `revision` only if the invoice
    has already been sent.
    """
    invoice = _get_invoice_or_raise(db, invoice_id)

    if invoice.status in _LOCKED_STATUSES:
        raise InvoiceServiceError(
            f"cannot edit invoice in status {invoice.status}",
            code="invoice_locked",
        )

    unknown = (
        set(patch)
        - _INVOICE_SCALAR_FIELDS
        - {"line_items", "installments", "order_discounts", "custom_amounts"}
    )
    if unknown:
        raise InvoiceServiceError(
            f"unknown fields: {sorted(unknown)}",
            code="unknown_fields",
        )

    # `custom_amounts` is a per-write request flag, not a stored field.
    # It controls whether the deposit-floor check runs for this PATCH.
    custom_amounts = bool(patch.pop("custom_amounts", False))

    line_items_changed = "line_items" in patch
    installments_changed = "installments" in patch
    discount_changed = "order_discounts" in patch
    public_patch = {
        field_name: patch.get(field_name)
        for field_name in ("terms", "footer", "public_notes")
        if field_name in patch
    }
    if public_patch:
        try:
            assert_no_public_catalog_leaks(db, public_patch)
        except CatalogServiceError as exc:
            raise InvoiceServiceError(
                str(exc), code=exc.code, **exc.extra
            ) from exc

    # Apply the discount stack first: line-item math reads the stack's
    # combined percent, so it must be replaced before
    # `_replace_line_items` recomputes per-line cents.
    if discount_changed:
        raw_rows = patch.get("order_discounts") or []
        try:
            new_snaps = snapshot_order_discounts(
                db,
                raw_rows,
                existing_snapshots=_current_order_discount_snapshots(
                    db, invoice
                ),
            )
        except DiscountSnapshotError as exc:
            raise InvoiceServiceError(
                str(exc), code=exc.code, **exc.extra
            ) from exc
        _replace_order_discounts(db, invoice, new_snaps)
        # Switching onto the percent path zeroes the legacy
        # `discount_cents`; `_recompute_totals` rederives it. Switching
        # to an empty stack clears the prior derived value so it does
        # not become a legacy flat discount.
        if new_snaps or raw_rows == []:
            invoice.discount_cents = 0

    for field_name in _INVOICE_SCALAR_FIELDS:
        if field_name in patch:
            value = patch[field_name]
            if field_name == "discount_cents":
                # Server-derived when the percent path is in use; the
                # body's value is ignored to keep totals canonical.
                if _has_order_discounts(db, invoice):
                    continue
                if value is None:
                    value = 0
            setattr(invoice, field_name, value)

    if line_items_changed:
        _replace_line_items(db, invoice, patch["line_items"] or [])
    elif discount_changed:
        # Existing line rows must be re-rounded against the new order
        # percent (or back to legacy when toggling off).
        _rerate_existing_line_items(db, invoice)

    if installments_changed:
        _ensure_no_paid_installment_invalidated(
            db, invoice, patch["installments"] or []
        )
        _replace_installments(db, invoice, patch["installments"] or [])

    if line_items_changed or "discount_cents" in patch or discount_changed:
        _recompute_totals(db, invoice)

    if installments_changed:
        _refresh_due_date(db, invoice)
        # Plan validity (count + deposit floor) runs on every write that
        # touches the schedule, regardless of status. The sum-balance
        # check only applies once the invoice is sent — drafts can be
        # transiently imbalanced while the editor is open.
        _validate_plan_inputs(
            patch["installments"] or [],
            int(invoice.total_cents or 0),
            custom_amounts=custom_amounts,
        )

    if (
        installments_changed
        or line_items_changed
        or "discount_cents" in patch
        or discount_changed
    ) and invoice.status in ("sent", "partial"):
        # Sent invoices must keep schedule consistent. Drafts can be
        # transiently out-of-balance while the editor is open.
        _validate_schedule(db, invoice)

    bumped_revision = False
    if invoice.status != "draft":
        invoice.revision = int(invoice.revision or 0) + 1
        bumped_revision = True
        # PDF cache invalidates; Phase 8 reads `last_pdf_rendered_revision`
        # to know whether the cached PDF matches the current revision.

    invoice.updated_at = datetime.now(timezone.utc)
    db.flush()
    if bumped_revision:
        # Only emit on a revision bump — draft tweaks aren't a state
        # change worth a timeline row, but a revision bump on a sent
        # invoice changes the contract terms and should be visible.
        activity_log.log_activity(
            db,
            event_id=invoice.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.INVOICE_UPDATED,
            subject_kind="invoice",
            subject_id=invoice.id,
            payload={
                "revision": int(invoice.revision),
                "fields": sorted(set(patch.keys())),
            },
        )
    return invoice


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


def mark_sent(
    db: Session,
    *,
    invoice_id: int,
    actor_user_id: int | None = None,
    contact_ids: list[int] | None = None,
) -> Invoice:
    """Transition draft → sent. Allocates `invoice_number`. Creates one
    `invoice_invitations` row per contact (defaulting to the event's
    primary contact) with a freshly minted `public_key`."""
    invoice = _get_invoice_or_raise(db, invoice_id)

    if invoice.status != "draft":
        raise InvoiceServiceError(
            f"cannot send invoice in status {invoice.status}",
            code="invalid_transition",
        )

    line_count = db.execute(
        sql_text(
            "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id = :id"
        ),
        {"id": invoice_id},
    ).scalar()
    if not line_count:
        raise InvoiceServiceError(
            "invoice has no line items", code="line_items_required"
        )

    inst_count = db.execute(
        sql_text(
            "SELECT COUNT(*) FROM invoice_installments WHERE invoice_id = :id"
        ),
        {"id": invoice_id},
    ).scalar()
    if not inst_count:
        raise InvoiceServiceError(
            "invoice has no payment schedule", code="schedule_required"
        )

    _validate_schedule(db, invoice)

    # Allocate the number BEFORE flipping status so
    # chk_invoice_number_when_not_draft holds at flush time.
    invoice.invoice_number = _assign_invoice_number(db)
    invoice.status = "sent"
    invoice.sent_at = datetime.now(timezone.utc)
    db.flush()

    target_contact_ids = (
        list(contact_ids) if contact_ids else [invoice.contact_id]
    )
    _ensure_invitations(db, invoice_id, target_contact_ids, sending_now=True)

    db.flush()
    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_SENT,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "invoice_number": invoice.invoice_number,
            "contact_ids": target_contact_ids,
        },
    )
    return invoice


def resend_invoice(
    db: Session,
    *,
    invoice_id: int,
    contact_ids: list[int],
    actor_user_id: int | None = None,
) -> Invoice:
    """Re-emit invitations for an already-sent invoice. Existing rows keep
    their public_key (so prior bookmarks still work); only `sent_at` and
    `last_resent_at` update. Missing rows are created fresh."""
    invoice = _get_invoice_or_raise(db, invoice_id)
    if invoice.status not in ("sent", "partial"):
        raise InvoiceServiceError(
            f"cannot resend invoice in status {invoice.status}",
            code="invalid_transition",
        )
    if not contact_ids:
        raise InvoiceServiceError(
            "contact_ids must be non-empty for resend",
            code="contact_ids_required",
        )

    _ensure_invitations(db, invoice_id, contact_ids, sending_now=True)
    db.flush()
    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_RESENT,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "invoice_number": invoice.invoice_number,
            "contact_ids": list(contact_ids),
        },
    )
    return invoice


def cancel_invoice(
    db: Session,
    *,
    invoice_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Invoice:
    """Move any non-paid invoice to `cancelled`. Number is preserved."""
    invoice = _get_invoice_or_raise(db, invoice_id)
    if invoice.status == "paid":
        raise InvoiceServiceError(
            "cannot cancel a paid invoice", code="invoice_locked"
        )
    if invoice.status == "cancelled":
        return invoice  # idempotent
    try:
        assert_no_public_catalog_leaks(
            db, {"cancellation_reason": reason}
        )
    except CatalogServiceError as exc:
        raise InvoiceServiceError(str(exc), code=exc.code, **exc.extra) from exc
    invoice.status = "cancelled"
    invoice.cancelled_at = datetime.now(timezone.utc)
    invoice.cancellation_reason = reason
    invoice.updated_at = datetime.now(timezone.utc)
    db.flush()
    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_CANCELLED,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "invoice_number": invoice.invoice_number,
            "reason": reason,
        },
    )
    return invoice


def soft_delete_invoice(
    db: Session,
    *,
    invoice_id: int,
    actor_user_id: int | None = None,
) -> None:
    """Mark `deleted_at` on a draft invoice.

    If a quote points at this invoice via `converted_invoice_id`, also
    unlink it in the same transaction: status flips back to 'approved',
    `converted_invoice_id` and `converted_at` clear. The lifecycle rule
    is that a quote should only stay 'converted' while there is an
    active invoice attached — without this, deleting a draft invoice
    would leave the quote permanently locked.
    """
    invoice = _get_invoice_or_raise(db, invoice_id)
    if invoice.deleted_at is not None:
        return
    if invoice.status != "draft":
        raise InvoiceServiceError(
            "only draft invoices can be deleted",
            code="invoice_locked",
        )
    quote = (
        db.query(Quote)
        .filter(Quote.converted_invoice_id == invoice_id)
        .first()
    )
    if quote is not None:
        quote.status = "approved"
        quote.converted_invoice_id = None
        quote.converted_at = None
        quote.updated_at = datetime.now(timezone.utc)
    invoice.deleted_at = datetime.now(timezone.utc)
    invoice.updated_at = datetime.now(timezone.utc)
    db.flush()
    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_DELETED,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "invoice_number": invoice.invoice_number,
            "unlinked_quote_id": quote.id if quote else None,
            "unlinked_quote_number": quote.quote_number if quote else None,
        },
    )
    if quote is not None:
        activity_log.log_activity(
            db,
            event_id=quote.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.QUOTE_UNCONVERTED,
            subject_kind="quote",
            subject_id=quote.id,
            payload={
                "quote_number": quote.quote_number,
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
            },
        )


def append_late_fee(
    db: Session,
    *,
    invoice_id: int,
    fee_cents: int,
    actor_user_id: int | None = None,
) -> Invoice:
    """Phase 11. Append a ``kind='fee'`` line item to a sent or partial
    invoice and roll the fee onto the **next unpaid installment** so
    the schedule stays balanced.

    Refuses on any other status — paid / cancelled / draft / reversed
    are not eligible for a late fee. Bumps ``revision`` (the invoice
    has materially changed; PDF cache should re-render).

    The schedule rebalance is intentionally narrow: rather than
    proportionally redistributing the fee across all unpaid rows
    (which is harder to explain on the customer-facing PDF), we add
    the fee to the single next-unpaid installment in sort order. If
    every installment is paid the call raises — there's no
    installment to take the fee — and staff has to add a new schedule
    row manually. That's a rare edge in v1; v1.1 can add the
    new-row path if it shows up.
    """
    invoice = _get_invoice_or_raise(db, invoice_id)
    if invoice.deleted_at is not None:
        raise InvoiceServiceError(
            "cannot append late fee to a deleted invoice",
            code="invoice_locked",
        )
    if invoice.status not in ("sent", "partial"):
        raise InvoiceServiceError(
            f"late fee only applies to sent/partial invoices, not {invoice.status}",
            code="invalid_transition",
        )
    if int(fee_cents) <= 0:
        raise InvoiceServiceError(
            "fee must be positive", code="invalid_amount"
        )

    # Pick the next-unpaid installment in sort_order (paid_at IS NULL).
    next_unpaid = (
        db.query(InvoiceInstallment)
        .filter(InvoiceInstallment.invoice_id == invoice_id)
        .filter(InvoiceInstallment.paid_at.is_(None))
        .order_by(
            InvoiceInstallment.sort_order.asc(),
            InvoiceInstallment.id.asc(),
        )
        .first()
    )
    if next_unpaid is None:
        raise InvoiceServiceError(
            "no unpaid installment to absorb the fee",
            code="no_target_installment",
        )

    # Append the fee line at the bottom (highest sort_order + 1).
    last_sort_row = db.execute(
        sql_text(
            "SELECT COALESCE(MAX(sort_order), -1) AS s "
            "FROM invoice_line_items WHERE invoice_id = :id"
        ),
        {"id": invoice_id},
    ).first()
    next_sort = int(last_sort_row.s or -1) + 1

    fee_line = LineItemInput(
        kind="fee",
        public_description="Late payment fee",
        quantity=Decimal("1"),
        unit_price_cents=int(fee_cents),
        sort_order=next_sort,
    )
    sub, tax, total = _compute_line_amounts(
        fee_line, _read_order_discount_total(db, invoice)
    )
    fee_kwargs = _resolve_line_kwargs(db, fee_line)
    db.add(
        InvoiceLineItem(
            invoice_id=invoice_id,
            sort_order=next_sort,
            kind=fee_line.kind,
            product_key=None,
            quantity=fee_line.quantity,
            unit_price_cents=int(fee_line.unit_price_cents),
            discount_cents=0,
            tax_rate=fee_line.tax_rate,
            tax_name=None,
            line_subtotal_cents=sub,
            line_tax_cents=tax,
            line_total_cents=total,
            **fee_kwargs,
        )
    )
    db.flush()

    # Re-aggregate totals from line items, then push the same delta
    # onto the next unpaid installment so the schedule stays balanced.
    _recompute_totals(db, invoice)
    next_unpaid.amount_cents = int(next_unpaid.amount_cents) + int(fee_cents)
    invoice.revision = int(invoice.revision or 1) + 1
    invoice.updated_at = datetime.now(timezone.utc)
    db.flush()
    _validate_schedule(db, invoice)

    activity_log.log_activity(
        db,
        event_id=invoice.event_id,
        actor_kind="system" if actor_user_id is None else "staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.INVOICE_UPDATED,
        subject_kind="invoice",
        subject_id=invoice.id,
        payload={
            "revision": int(invoice.revision),
            "fields": ["late_fee"],
            "fee_cents": int(fee_cents),
            "installment_id": int(next_unpaid.id),
        },
    )
    return invoice


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@dataclass
class CatalogLineSnapshot:
    """Catalog-row fields the staff API surfaces alongside a catalog-
    backed line. Public APIs never return this — Phase 4's render swap
    and Phase 7's lint enforce that.
    """

    id: int
    internal_sku: str
    public_code: str
    designer: str | None
    style_number: str | None
    color: str
    house_name: str | None
    category: str
    product_title: str | None


@dataclass
class LineItemView:
    id: int
    sort_order: int
    kind: str
    product_key: str | None
    description: str | None
    quantity: Decimal
    unit_price_cents: int
    discount_cents: int
    tax_rate: Decimal
    tax_name: str | None
    line_subtotal_cents: int
    line_tax_cents: int
    line_total_cents: int
    notes: str | None
    catalog_item_id: int | None
    size_label: str | None
    public_description: str | None
    internal_notes: str | None
    catalog: CatalogLineSnapshot | None


@dataclass
class InstallmentView:
    id: int
    sort_order: int
    label: str
    amount_cents: int
    due_date: date
    paid_at: datetime | None
    staff_notes: str | None


@dataclass
class InvitationView:
    id: int
    contact_id: int
    public_key: str
    sent_at: datetime | None
    last_resent_at: datetime | None
    viewed_at: datetime | None
    last_viewed_at: datetime | None
    view_count: int
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass
class OrderDiscountView:
    id: int
    sort_order: int
    preset_id: str | None
    label: str
    percent: Decimal


@dataclass
class InvoiceDetail:
    id: int
    event_id: int
    contact_id: int
    invoice_number: str | None
    status: str
    issue_date: date
    due_date: date | None
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    paid_to_date_cents: int
    balance_cents: int
    order_discounts: list[OrderDiscountView]
    terms: str | None
    footer: str | None
    public_notes: str | None
    private_notes: str | None
    po_number: str | None
    revision: int
    sent_at: datetime | None
    viewed_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    last_pdf_rendered_revision: int | None
    last_pdf_rendered_at: datetime | None
    last_pdf_render_error: str | None
    created_by_user_id: int | None
    sold_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    # Reverse linkage to the originating quote, when this invoice was
    # produced via convert_to_invoice. Used by the UI to warn that
    # deleting this draft will return the quote to 'approved'.
    source_quote_id: int | None = None
    source_quote_number: str | None = None
    line_items: list[LineItemView] = field(default_factory=list)
    installments: list[InstallmentView] = field(default_factory=list)
    invitations: list[InvitationView] = field(default_factory=list)


@dataclass
class InvoiceSummary:
    id: int
    event_id: int
    contact_id: int
    contact_name: str
    invoice_number: str | None
    status: str
    issue_date: date
    due_date: date | None
    total_cents: int
    paid_to_date_cents: int
    balance_cents: int
    sent_at: datetime | None
    paid_at: datetime | None
    sold_by_user_id: int | None
    created_at: datetime


def get_invoice_detail(db: Session, invoice_id: int) -> InvoiceDetail:
    invoice = _get_invoice_or_raise(db, invoice_id)
    line_rows = (
        db.query(InvoiceLineItem)
        .filter(InvoiceLineItem.invoice_id == invoice_id)
        .order_by(InvoiceLineItem.sort_order.asc(), InvoiceLineItem.id.asc())
        .all()
    )
    catalog_by_id = _load_catalog_snapshots(
        db, [r.catalog_item_id for r in line_rows if r.catalog_item_id]
    )
    inst_rows = (
        db.query(InvoiceInstallment)
        .filter(InvoiceInstallment.invoice_id == invoice_id)
        .order_by(InvoiceInstallment.sort_order.asc(), InvoiceInstallment.id.asc())
        .all()
    )
    inv_rows = (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.invoice_id == invoice_id)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .order_by(InvoiceInvitation.id.asc())
        .all()
    )
    source_quote = (
        db.query(Quote.id, Quote.quote_number)
        .filter(Quote.converted_invoice_id == invoice_id)
        .first()
    )
    discount_rows = (
        db.query(InvoiceOrderDiscount)
        .filter(InvoiceOrderDiscount.invoice_id == invoice_id)
        .order_by(
            InvoiceOrderDiscount.sort_order.asc(),
            InvoiceOrderDiscount.id.asc(),
        )
        .all()
    )
    return InvoiceDetail(
        id=invoice.id,
        event_id=invoice.event_id,
        contact_id=invoice.contact_id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        subtotal_cents=int(invoice.subtotal_cents or 0),
        discount_cents=int(invoice.discount_cents or 0),
        tax_cents=int(invoice.tax_cents or 0),
        total_cents=int(invoice.total_cents or 0),
        paid_to_date_cents=int(invoice.paid_to_date_cents or 0),
        balance_cents=int(invoice.balance_cents or 0),
        order_discounts=[
            OrderDiscountView(
                id=int(r.id),
                sort_order=int(r.sort_order),
                preset_id=r.preset_id,
                label=r.label,
                percent=Decimal(str(r.percent)),
            )
            for r in discount_rows
        ],
        terms=invoice.terms,
        footer=invoice.footer,
        public_notes=invoice.public_notes,
        private_notes=invoice.private_notes,
        po_number=invoice.po_number,
        revision=int(invoice.revision or 1),
        sent_at=invoice.sent_at,
        viewed_at=invoice.viewed_at,
        paid_at=invoice.paid_at,
        cancelled_at=invoice.cancelled_at,
        cancellation_reason=invoice.cancellation_reason,
        last_pdf_rendered_revision=(
            int(invoice.last_pdf_rendered_revision)
            if invoice.last_pdf_rendered_revision is not None
            else None
        ),
        last_pdf_rendered_at=invoice.last_pdf_rendered_at,
        last_pdf_render_error=invoice.last_pdf_render_error,
        created_by_user_id=invoice.created_by_user_id,
        sold_by_user_id=invoice.sold_by_user_id,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        deleted_at=invoice.deleted_at,
        source_quote_id=source_quote[0] if source_quote else None,
        source_quote_number=source_quote[1] if source_quote else None,
        line_items=[
            _to_line_view(r, catalog_by_id.get(r.catalog_item_id))
            for r in line_rows
        ],
        installments=[_to_installment_view(r) for r in inst_rows],
        invitations=[_to_invitation_view(r) for r in inv_rows],
    )


def list_invoices_for_event(
    db: Session,
    *,
    event_id: int,
    include_deleted: bool = False,
    status: str | None = None,
) -> list[InvoiceSummary]:
    q = (
        db.query(Invoice, Contact.display_name.label("contact_name"))
        .join(Contact, Contact.id == Invoice.contact_id)
        .filter(Invoice.event_id == event_id)
    )
    if not include_deleted:
        q = q.filter(Invoice.deleted_at.is_(None))
    if status:
        q = q.filter(Invoice.status == status)
    q = q.order_by(Invoice.created_at.desc())
    return [_to_summary(inv, name) for inv, name in q.all()]


def search_invoices(
    db: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    event_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_deleted: bool = False,
    limit: int = 100,
) -> list[InvoiceSummary]:
    """Global staff search. Matches `q` against invoice_number prefix or
    contact display_name (case-insensitive)."""
    query = (
        db.query(Invoice, Contact.display_name.label("contact_name"))
        .join(Contact, Contact.id == Invoice.contact_id)
    )
    if not include_deleted:
        query = query.filter(Invoice.deleted_at.is_(None))
    if status:
        query = query.filter(Invoice.status == status)
    if event_id is not None:
        query = query.filter(Invoice.event_id == event_id)
    if date_from is not None:
        query = query.filter(Invoice.issue_date >= date_from)
    if date_to is not None:
        query = query.filter(Invoice.issue_date <= date_to)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            (func.lower(Invoice.invoice_number).like(like))
            | (func.lower(Contact.display_name).like(like))
        )
    query = (
        query.order_by(
            Invoice.sent_at.desc().nulls_last(),
            Invoice.created_at.desc(),
        )
        .limit(min(int(limit), 500))
    )
    return [_to_summary(inv, name) for inv, name in query.all()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_invoice_or_raise(db: Session, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise InvoiceServiceError("invoice not found", code="invoice_not_found")
    return invoice


def _read_order_discount_total(db: Session, invoice: Invoice) -> Decimal | None:
    """Sum of per-row percents on `invoice_order_discounts` for this
    invoice, or `None` when the stack is empty (legacy flat-amount
    path). The combined cap is enforced at write time, so callers can
    treat the returned Decimal as already in [0, 50]."""
    row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(percent), 0), COUNT(*) "
            "FROM invoice_order_discounts WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).one()
    if int(row[1]) == 0:
        return None
    return Decimal(str(row[0]))


def _has_order_discounts(db: Session, invoice: Invoice) -> bool:
    return _read_order_discount_total(db, invoice) is not None


def _current_order_discount_snapshots(
    db: Session, invoice: Invoice
) -> list[DiscountRowSnapshot]:
    rows = (
        db.query(InvoiceOrderDiscount)
        .filter(InvoiceOrderDiscount.invoice_id == invoice.id)
        .order_by(
            InvoiceOrderDiscount.sort_order.asc(),
            InvoiceOrderDiscount.id.asc(),
        )
        .all()
    )
    return [
        DiscountRowSnapshot(
            preset_id=r.preset_id,
            label=r.label,
            percent=Decimal(str(r.percent)),
        )
        for r in rows
    ]


def _replace_order_discounts(
    db: Session,
    invoice: Invoice,
    snaps: list,
) -> None:
    """Wipe and re-insert the invoice's order-discount stack.

    `snaps` is the list returned by
    `discount_snapshot.snapshot_order_discounts` — already validated
    against per-row 0..50 and combined 50% cap. An empty list clears
    the stack and puts the invoice back on the legacy flat-amount path.
    """
    db.execute(
        sql_text(
            "DELETE FROM invoice_order_discounts WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    )
    for idx, snap in enumerate(snaps):
        db.add(
            InvoiceOrderDiscount(
                invoice_id=invoice.id,
                sort_order=idx,
                preset_id=snap.preset_id,
                label=snap.label,
                percent=snap.percent,
            )
        )
    db.flush()


def _rerate_existing_line_items(db: Session, invoice: Invoice) -> None:
    """Re-round per-line cents in place against the parent's current
    combined order-discount percent.

    Called from update_invoice when the discount stack changes but
    the line list itself does not. Reads each row, recomputes
    `(line_subtotal_cents, line_tax_cents, line_total_cents)` using the
    same per-line math as the create path, and writes them back.
    """
    order_pct = _read_order_discount_total(db, invoice)
    rows = db.execute(
        sql_text(
            "SELECT id, quantity, unit_price_cents, discount_cents, tax_rate "
            "FROM invoice_line_items WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).all()
    for r in rows:
        synthetic = LineItemInput(
            quantity=Decimal(str(r.quantity)),
            unit_price_cents=int(r.unit_price_cents),
            discount_cents=int(r.discount_cents or 0),
            tax_rate=Decimal(str(r.tax_rate or 0)),
        )
        sub, tax, total = _compute_line_amounts(synthetic, order_pct)
        db.execute(
            sql_text(
                "UPDATE invoice_line_items SET "
                "line_subtotal_cents = :sub, "
                "line_tax_cents = :tax, "
                "line_total_cents = :total "
                "WHERE id = :id"
            ),
            {"id": r.id, "sub": sub, "tax": tax, "total": total},
        )
    db.flush()


def _replace_line_items(
    db: Session, invoice: Invoice, line_items: list[LineItemInput]
) -> None:
    order_pct = _read_order_discount_total(db, invoice)
    db.execute(
        sql_text("DELETE FROM invoice_line_items WHERE invoice_id = :id"),
        {"id": invoice.id},
    )
    for idx, li in enumerate(line_items):
        sub, tax, total = _compute_line_amounts(li, order_pct)
        sort_order = li.sort_order if li.sort_order is not None else idx
        kwargs = _resolve_line_kwargs(db, li)
        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                sort_order=sort_order,
                kind=li.kind,
                product_key=li.product_key,
                quantity=li.quantity,
                unit_price_cents=int(li.unit_price_cents),
                discount_cents=int(li.discount_cents or 0),
                tax_rate=li.tax_rate,
                tax_name=li.tax_name,
                line_subtotal_cents=sub,
                line_tax_cents=tax,
                line_total_cents=total,
                **kwargs,
            )
        )
    db.flush()


def _resolve_line_kwargs(
    db: Session, li: LineItemInput
) -> dict[str, Any]:
    """Translate a ``LineItemInput`` into the column kwargs for the
    insert.

    Catalog-backed line:
      - ``catalog_item_id`` resolves to an active catalog row.
      - Reject if any of ``description``, ``notes``, or
        ``public_description`` is set — the catalog row owns the
        customer copy and staff-typed text must not leak.
      - Run the forbidden-substring guard on ``internal_notes``: it is
        staff-only and not rendered to customers, but Phase 0 listed it
        as a place a future export could leak vendor SKU strings if
        someone pasted them in. The guard catches obvious cases now;
        Phase 7 broadens the sweep.
      - Persist ``catalog_item_id``, ``size_label``, ``internal_notes``;
        ``description``, ``notes``, ``public_description`` stay NULL.

    Non-catalog new line (Phase 2 transitional):
      - Customer copy comes from ``public_description`` (preferred) or
        the legacy ``description`` field.
      - Staff context comes from ``internal_notes`` (preferred) or the
        legacy ``notes`` field.
      - Persist ``public_description`` and ``internal_notes`` on the
        new columns. Until the Phase 4 render swap lands, also mirror
        the chosen public copy into the legacy ``description`` column
        so the existing PDF and portal templates render customer text
        for new lines. The mirror disappears in Phase 4 once
        renderers read ``public_description`` directly.
    """
    if li.catalog_item_id is not None:
        item = db.get(CatalogItem, li.catalog_item_id)
        if item is None:
            raise InvoiceServiceError(
                f"catalog item {li.catalog_item_id} not found",
                code="catalog_item_not_found",
                catalog_item_id=int(li.catalog_item_id),
            )
        if not item.active:
            raise InvoiceServiceError(
                "catalog item is inactive",
                code="catalog_item_inactive",
                catalog_item_id=int(li.catalog_item_id),
            )
        if (
            li.public_description is not None
            or li.description is not None
            or li.notes is not None
        ):
            raise InvoiceServiceError(
                "catalog-backed line must not set public_description, "
                "description, or notes",
                code="catalog_line_legacy_text",
                catalog_item_id=int(li.catalog_item_id),
            )
        try:
            assert_no_catalog_leak(
                item, li.internal_notes, field_name="internal_notes"
            )
        except CatalogServiceError as exc:
            raise InvoiceServiceError(
                str(exc), code=exc.code, **exc.extra
            ) from exc
        return {
            "catalog_item_id": int(li.catalog_item_id),
            "size_label": li.size_label,
            "internal_notes": li.internal_notes,
            "description": None,
            "notes": None,
            "public_description": None,
        }

    public = li.public_description if li.public_description is not None else li.description
    internal = li.internal_notes if li.internal_notes is not None else li.notes
    if (
        li.public_description is not None
        and li.description is not None
        and li.public_description != li.description
    ):
        raise InvoiceServiceError(
            "public_description and legacy description disagree; "
            "send only one",
            code="line_public_description_conflict",
        )
    if not public:
        raise InvoiceServiceError(
            "non-catalog line requires public_description",
            code="public_description_required",
        )
    try:
        assert_no_public_catalog_leaks(db, {"public_description": public})
    except CatalogServiceError as exc:
        raise InvoiceServiceError(str(exc), code=exc.code, **exc.extra) from exc
    return {
        "catalog_item_id": None,
        "size_label": li.size_label,
        "public_description": public,
        "internal_notes": internal,
        # Phase 4's render swap stops the legacy mirror. PDFs and
        # portal pages now read `public_description` (or, for legacy
        # lines, the existing `description`) through
        # `catalog_service.customer_line_view`, so writing the
        # customer copy into both columns is dead weight. Existing
        # legacy rows still carry their `description` text and keep
        # rendering as grandfathered.
        "description": None,
        "notes": None,
    }


def _replace_installments(
    db: Session,
    invoice: Invoice,
    installments: list[InstallmentInput],
) -> None:
    db.execute(
        sql_text("DELETE FROM invoice_installments WHERE invoice_id = :id"),
        {"id": invoice.id},
    )
    for idx, inst in enumerate(installments):
        sort_order = inst.sort_order if inst.sort_order is not None else idx
        db.add(
            InvoiceInstallment(
                invoice_id=invoice.id,
                sort_order=sort_order,
                label=inst.label,
                amount_cents=int(inst.amount_cents),
                due_date=inst.due_date,
                staff_notes=inst.staff_notes,
            )
        )
    db.flush()


def _ensure_no_paid_installment_invalidated(
    db: Session,
    invoice: Invoice,
    new_installments: list[InstallmentInput],
) -> None:
    """When the schedule is replaced on a sent/partial invoice, we must not
    silently drop a row that already has a paid_at stamp. Phase 6 will lean
    on this so a refund can't orphan paid history."""
    if invoice.status not in ("sent", "partial"):
        return
    paid_count = db.execute(
        sql_text(
            "SELECT COUNT(*) FROM invoice_installments "
            "WHERE invoice_id = :id AND paid_at IS NOT NULL"
        ),
        {"id": invoice.id},
    ).scalar()
    if paid_count and len(new_installments) < int(paid_count):
        raise InvoiceServiceError(
            "schedule replacement would drop a paid installment",
            code="paid_installment_dropped",
        )


def _compute_line_amounts(
    li: LineItemInput,
    order_discount_percent: Decimal | None = None,
) -> tuple[int, int, int]:
    """Per-line money math, in cents.

    `order_discount_percent` is the parent's snapshotted percent (Phase
    2a). When set, the order discount shrinks the taxable base BEFORE
    tax is applied. When `None`, the legacy post-tax `discount_cents`
    on the parent is used; lines compute as before.
    """
    qty = (
        li.quantity if isinstance(li.quantity, Decimal) else Decimal(str(li.quantity))
    )
    unit = Decimal(int(li.unit_price_cents))
    disc = Decimal(int(li.discount_cents or 0))
    rate = (
        li.tax_rate
        if isinstance(li.tax_rate, Decimal)
        else Decimal(str(li.tax_rate or 0))
    )
    gross_cents = qty * unit
    pre_order_sub = (gross_cents - disc)
    if order_discount_percent is not None:
        # Pre-tax order discount: shrink the taxable base. Round once
        # per line so the per-line cents add up to the documented totals.
        factor = Decimal(1) - (Decimal(order_discount_percent) / Decimal(100))
        sub = (pre_order_sub * factor).to_integral_value(rounding=ROUND_HALF_EVEN)
    else:
        sub = pre_order_sub.to_integral_value(rounding=ROUND_HALF_EVEN)
    tax = (sub * rate).to_integral_value(rounding=ROUND_HALF_EVEN)
    total = sub + tax
    return int(sub), int(tax), int(total)


def _recompute_totals(db: Session, invoice: Invoice) -> None:
    """Recompute the parent's money columns from the line rows.

    Two paths, gated on the presence of rows in
    `invoice_order_discounts`:

    - **New (one or more order discounts).** Lines were written with
      the combined order discount already baked into
      `line_subtotal_cents`. We reconstruct the pre-order-discount
      subtotal from `(qty * unit - line_discount)` so `subtotal_cents`
      keeps its legacy meaning ("post-line-discount, pre-order-discount
      taxable base"). The derived display value `discount_cents` is
      the dollars-off the combined order discount produced.
      `total_cents` is `SUM(line_total_cents)` directly because the
      line totals already reflect the discount.
    - **Legacy (zero discount rows).** `discount_cents` is the
      caller-provided post-tax flat amount; `total_cents =
      SUM(line_total) - discount_cents`. Untouched from before this
      work.
    """
    rows = db.execute(
        sql_text(
            "SELECT quantity, unit_price_cents, discount_cents, "
            "       line_subtotal_cents, line_tax_cents, line_total_cents "
            "FROM invoice_line_items WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).all()

    pre_order_sub_total = 0
    tax_total = 0
    line_total_sum = 0
    for r in rows:
        qty = Decimal(str(r.quantity))
        unit = Decimal(int(r.unit_price_cents))
        line_disc = int(r.discount_cents or 0)
        # Per-line rounding mirrors `_compute_line_amounts` so the
        # reconstructed pre-order subtotal agrees with the line column
        # the per-line path produced.
        line_pre_order = int(
            (qty * unit - Decimal(line_disc)).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )
        pre_order_sub_total += line_pre_order
        tax_total += int(r.line_tax_cents)
        line_total_sum += int(r.line_total_cents)

    invoice.tax_cents = tax_total
    invoice.subtotal_cents = pre_order_sub_total

    pct = _read_order_discount_total(db, invoice)
    if pct is not None:
        invoice.discount_cents = int(
            (Decimal(pre_order_sub_total) * pct / Decimal(100))
            .to_integral_value(rounding=ROUND_HALF_EVEN)
        )
        invoice.total_cents = line_total_sum
    else:
        invoice.total_cents = line_total_sum - int(invoice.discount_cents or 0)
    invoice.balance_cents = invoice.total_cents - int(
        invoice.paid_to_date_cents or 0
    )


def _refresh_due_date(db: Session, invoice: Invoice) -> None:
    row = db.execute(
        sql_text(
            "SELECT MAX(due_date) FROM invoice_installments WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).one()
    invoice.due_date = row[0]


def _validate_schedule(db: Session, invoice: Invoice) -> None:
    row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(amount_cents), 0), COUNT(*) "
            "FROM invoice_installments WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).one()
    sched_sum, count = int(row[0]), int(row[1])
    if count == 0:
        raise InvoiceServiceError(
            "schedule is empty",
            code="schedule_required",
        )
    if count not in (1, 2, 3):
        # Phase 5: plan counts above 3 are blocked at send time so a
        # legacy draft with a 4-payment free-form schedule cannot ship.
        raise InvoiceServiceError(
            f"plan count {count} not in 1, 2, or 3",
            code="plan_count_invalid",
            count=count,
        )
    if sched_sum != int(invoice.total_cents or 0):
        raise InvoiceServiceError(
            f"schedule sum {sched_sum} != invoice total {int(invoice.total_cents)}",
            code="schedule_unbalanced",
            schedule_sum_cents=sched_sum,
            total_cents=int(invoice.total_cents or 0),
        )


def _validate_plan_inputs(
    installments: list[InstallmentInput],
    total_cents: int,
    *,
    custom_amounts: bool,
) -> None:
    """Phase 5 plan-validity check, run on every write that touches the
    schedule. Enforces count in {1,2,3} and (unless `custom_amounts`)
    that the first installment is at least 50% of the total. The
    sum-balance check is left to `_validate_schedule` so the existing
    `schedule_unbalanced` wire-code keeps firing for drift cases.

    The deposit floor uses integer floor division so a default 50/50
    split on an odd-cent total (e.g., $100.01 → 50/51) still passes —
    the under-by-half-a-cent gap is rounding, not policy."""
    count = len(installments)
    if count == 0:
        return  # drafts are allowed to have no schedule yet
    if count not in (1, 2, 3):
        raise InvoiceServiceError(
            f"plan count {count} not in 1, 2, or 3",
            code="plan_count_invalid",
            count=count,
        )
    if custom_amounts:
        return
    deposit = int(installments[0].amount_cents or 0)
    floor = int(total_cents) // 2
    if deposit < floor:
        raise InvoiceServiceError(
            f"deposit {deposit} below 50% floor {floor}",
            code="deposit_below_floor",
            deposit_cents=deposit,
            floor_cents=floor,
        )


def _assign_invoice_number(db: Session) -> str:
    """Allocate the next invoice number under a row-level lock on
    `numbering_state`. Resets the sequence on year rollover."""
    row = db.execute(
        sql_text(
            "SELECT invoice_year, invoice_seq FROM numbering_state "
            "WHERE id = 1 FOR UPDATE"
        )
    ).one()
    current_year = datetime.now(timezone.utc).year
    if int(row.invoice_year) != current_year:
        new_year, new_seq = current_year, 1
    else:
        new_year, new_seq = int(row.invoice_year), int(row.invoice_seq) + 1
    db.execute(
        sql_text(
            "UPDATE numbering_state SET invoice_year = :y, invoice_seq = :s, "
            "updated_at = NOW() WHERE id = 1"
        ),
        {"y": new_year, "s": new_seq},
    )
    return f"INV-{new_year}-{new_seq:06d}"


def _ensure_invitations(
    db: Session,
    invoice_id: int,
    contact_ids: list[int],
    *,
    sending_now: bool,
) -> None:
    """Idempotent: if a row already exists for (invoice, contact), reuse the
    public_key and bump `sent_at` / `last_resent_at`. Otherwise insert with
    a fresh key. Revoked or soft-deleted rows are treated as absent so a
    rotation produces a new key."""
    now = datetime.now(timezone.utc)
    for cid in contact_ids:
        row = db.execute(
            sql_text(
                "SELECT id FROM invoice_invitations "
                "WHERE invoice_id = :i AND contact_id = :c "
                "AND deleted_at IS NULL AND revoked_at IS NULL"
            ),
            {"i": invoice_id, "c": cid},
        ).first()
        if row is None:
            db.add(
                InvoiceInvitation(
                    invoice_id=invoice_id,
                    contact_id=cid,
                    public_key=secrets.token_urlsafe(32),
                    sent_at=now if sending_now else None,
                )
            )
        elif sending_now:
            db.execute(
                sql_text(
                    "UPDATE invoice_invitations "
                    "SET sent_at = COALESCE(sent_at, :now), "
                    "    last_resent_at = :now, "
                    "    updated_at = :now "
                    "WHERE id = :id"
                ),
                {"now": now, "id": row.id},
            )


def invitation_ids_for_contacts(
    db: Session, *, invoice_id: int, contact_ids: list[int]
) -> list[int]:
    """Return the active invitation row ids for the given contacts on
    this invoice. Used by the router to scope a resend email dispatch
    after the service-level resend has bumped the timestamps."""
    if not contact_ids:
        return []
    rows = (
        db.query(InvoiceInvitation.id)
        .filter(InvoiceInvitation.invoice_id == invoice_id)
        .filter(InvoiceInvitation.contact_id.in_(contact_ids))
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
        .all()
    )
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# View converters
# ---------------------------------------------------------------------------


def _to_line_view(
    r: InvoiceLineItem, catalog: CatalogItem | None = None
) -> LineItemView:
    return LineItemView(
        id=r.id,
        sort_order=int(r.sort_order or 0),
        kind=r.kind,
        product_key=r.product_key,
        description=r.description,
        quantity=Decimal(str(r.quantity)),
        unit_price_cents=int(r.unit_price_cents),
        discount_cents=int(r.discount_cents or 0),
        tax_rate=Decimal(str(r.tax_rate or 0)),
        tax_name=r.tax_name,
        line_subtotal_cents=int(r.line_subtotal_cents),
        line_tax_cents=int(r.line_tax_cents),
        line_total_cents=int(r.line_total_cents),
        notes=r.notes,
        catalog_item_id=r.catalog_item_id,
        size_label=r.size_label,
        public_description=r.public_description,
        internal_notes=r.internal_notes,
        catalog=_catalog_snapshot(catalog) if catalog is not None else None,
    )


def _load_catalog_snapshots(
    db: Session, ids: list[int]
) -> dict[int, CatalogItem]:
    """Single-query batch lookup of catalog rows referenced by a detail's
    line items. Returns a dict keyed by id so the caller can pair each
    line with its catalog row in a list comprehension. Empty input
    returns an empty dict without touching the DB."""
    unique_ids = {int(i) for i in ids if i is not None}
    if not unique_ids:
        return {}
    rows = (
        db.query(CatalogItem)
        .filter(CatalogItem.id.in_(unique_ids))
        .all()
    )
    return {int(r.id): r for r in rows}


def _catalog_snapshot(item: CatalogItem) -> CatalogLineSnapshot:
    return CatalogLineSnapshot(
        id=int(item.id),
        internal_sku=item.internal_sku,
        public_code=item.public_code,
        designer=item.designer,
        style_number=item.style_number,
        color=item.color,
        house_name=item.house_name,
        category=item.category,
        product_title=item.product_title,
    )


def _to_installment_view(r: InvoiceInstallment) -> InstallmentView:
    return InstallmentView(
        id=r.id,
        sort_order=int(r.sort_order or 0),
        label=r.label,
        amount_cents=int(r.amount_cents),
        due_date=r.due_date,
        paid_at=r.paid_at,
        staff_notes=r.staff_notes,
    )


def _to_invitation_view(r: InvoiceInvitation) -> InvitationView:
    return InvitationView(
        id=r.id,
        contact_id=r.contact_id,
        public_key=r.public_key,
        sent_at=r.sent_at,
        last_resent_at=r.last_resent_at,
        viewed_at=r.viewed_at,
        last_viewed_at=r.last_viewed_at,
        view_count=int(r.view_count or 0),
        expires_at=r.expires_at,
        revoked_at=r.revoked_at,
    )


def _to_summary(invoice: Invoice, contact_name: str) -> InvoiceSummary:
    return InvoiceSummary(
        id=invoice.id,
        event_id=invoice.event_id,
        contact_id=invoice.contact_id,
        contact_name=contact_name,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        total_cents=int(invoice.total_cents or 0),
        paid_to_date_cents=int(invoice.paid_to_date_cents or 0),
        balance_cents=int(invoice.balance_cents or 0),
        sent_at=invoice.sent_at,
        paid_at=invoice.paid_at,
        sold_by_user_id=invoice.sold_by_user_id,
        created_at=invoice.created_at,
    )
