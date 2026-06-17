"""Quote domain service.

Mirrors `services/invoice_service.py` for the quote lifecycle. A signed
quote IS the contract in this shop, so the signature lives on the quote
record itself (not on the invitation row, where Invoice Ninja attaches
it). Quotes never carry money state — they convert into a draft invoice
that does.

Reuses `LineItemInput` and `_compute_line_amounts` from invoice_service
because the per-line money math is identical between the two surfaces.
This is the only cross-module coupling; everything else in this file is
quote-specific.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
    InvoiceLineItem,
    InvoiceOrderDiscount,
    Quote,
    QuoteInstallment,
    QuoteInvitation,
    QuoteLineItem,
    QuoteOrderDiscount,
)
from decimal import ROUND_HALF_EVEN

# Shared with invoice_service: identical dataclass shape and identical
# per-line money math. Pulling them in keeps the two services agreeing on
# rounding by construction.
from services import (
    activity_log,
    invoice_pdf,
    notification_routing,
    quote_signature_hmac,
)
from services.catalog_service import CatalogServiceError, assert_no_public_catalog_leaks
from services.discount_snapshot import (
    DiscountRowInput,
    DiscountRowSnapshot,
    DiscountSnapshotError,
    snapshot_order_discounts,
)
from services.invoice_service import (  # noqa: F401  (LineItemInput re-exported for routers)
    CatalogLineSnapshot,
    LineItemInput,
    _catalog_snapshot,
    _compute_line_amounts,
    _load_catalog_snapshots,
    _resolve_line_kwargs,
)


class QuoteServiceError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "quote_error",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# Editable scalar fields on update_quote.
_QUOTE_SCALAR_FIELDS = {
    "discount_cents",
    "issue_date",
    "expires_at",
    "terms",
    "footer",
    "public_notes",
    "private_notes",
    "po_number",
}


@dataclass
class QuoteInstallmentInput:
    """Phase 4 of the discount/payment-term refactor.

    Mirrors `invoice_service.InstallmentInput` minus the payment-state
    fields. Quotes never carry payment state; the conversion path is
    where labels/amounts/due dates flow into a real invoice.
    """

    amount_cents: int
    due_date: date
    label: str | None = None
    sort_order: int | None = None


# Statuses where the quote is locked against further edits. `draft` and
# `sent` remain editable; everything terminal is frozen. A signed quote
# (status='approved') is a contract — to change anything, staff convert
# it and edit the invoice instead.
_LOCKED_STATUSES = frozenset(
    {"approved", "rejected", "converted", "expired", "cancelled"}
)


def _split_half_even(total_cents: int) -> int:
    """Return half of cents using the frontend's banker-rounding rule."""
    half_floor = total_cents // 2
    if total_cents % 2 and half_floor % 2:
        return half_floor + 1
    return half_floor


def _validate_plan_inputs(
    installments: list[QuoteInstallmentInput],
    total_cents: int,
    *,
    custom_amounts: bool,
) -> None:
    """Phase 5 plan-validity check for quotes. Mirrors
    `invoice_service._validate_plan_inputs`: count in {1,2,3} and
    deposit floor at 50% of total (skipped only when `custom_amounts`
    is explicitly flagged on the request)."""
    count = len(installments)
    if count == 0:
        return
    if count not in (1, 2, 3):
        raise QuoteServiceError(
            f"plan count {count} not in 1, 2, or 3",
            code="plan_count_invalid",
            count=count,
        )
    if custom_amounts:
        return
    deposit = int(installments[0].amount_cents or 0)
    floor = int(total_cents) // 2
    if deposit < floor:
        raise QuoteServiceError(
            f"deposit {deposit} below 50% floor {floor}",
            code="deposit_below_floor",
            deposit_cents=deposit,
            floor_cents=floor,
        )


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


def create_quote(
    db: Session,
    *,
    event_id: int,
    contact_id: int,
    line_items: list[LineItemInput] | None = None,
    installments: list[QuoteInstallmentInput] | None = None,
    discount_cents: int = 0,
    order_discounts: list[DiscountRowInput] | list[dict] | None = None,
    issue_date: date | None = None,
    expires_at: date | None = None,
    terms: str | None = None,
    footer: str | None = None,
    public_notes: str | None = None,
    private_notes: str | None = None,
    po_number: str | None = None,
    custom_amounts: bool = False,
    actor_user_id: int | None = None,
) -> Quote:
    """Create a draft quote. Number is NOT assigned (mark_sent does that).

    Empty line items are allowed for drafts. The send transition checks
    that the line list is non-empty and totals are nonzero.
    """
    if db.get(Event, event_id) is None:
        raise QuoteServiceError("event not found", code="event_not_found")
    if db.get(Contact, contact_id) is None:
        raise QuoteServiceError("contact not found", code="contact_not_found")
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
        raise QuoteServiceError(str(exc), code=exc.code, **exc.extra) from exc

    try:
        discount_snaps = snapshot_order_discounts(db, order_discounts)
    except DiscountSnapshotError as exc:
        raise QuoteServiceError(
            str(exc), code=exc.code, **exc.extra
        ) from exc

    quote = Quote(
        event_id=event_id,
        contact_id=contact_id,
        status="draft",
        issue_date=issue_date or date.today(),
        expires_at=expires_at,
        revision=1,
        discount_cents=int(discount_cents or 0) if not discount_snaps else 0,
        terms=terms,
        footer=footer,
        public_notes=public_notes,
        private_notes=private_notes,
        po_number=po_number,
        created_by_user_id=actor_user_id,
    )
    db.add(quote)
    db.flush()  # need quote.id for FKs

    _replace_order_discounts(db, quote, discount_snaps)
    _replace_line_items(db, quote, line_items or [])
    _replace_installments(db, quote, installments or [])
    _recompute_totals(db, quote)

    if installments:
        _validate_plan_inputs(
            installments,
            int(quote.total_cents or 0),
            custom_amounts=custom_amounts,
        )

    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_CREATED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"total_cents": int(quote.total_cents or 0)},
    )
    return quote


def update_quote(
    db: Session,
    *,
    quote_id: int,
    patch: dict[str, Any],
    actor_user_id: int | None = None,
) -> Quote:
    """Apply a partial update.

    `patch` carries only the fields the caller sent (router uses
    `model_dump(exclude_unset=True)`). Special key: `line_items` (list,
    replaces all rows when present).

    Refuses to edit a locked quote. Bumps `revision` only if the quote
    has already been sent — drafts edit freely without revisioning.
    """
    quote = _get_quote_or_raise(db, quote_id)

    if quote.status in _LOCKED_STATUSES:
        raise QuoteServiceError(
            f"quote is {quote.status}; cannot edit",
            code="quote_locked",
        )

    unknown = (
        set(patch)
        - _QUOTE_SCALAR_FIELDS
        - {"line_items", "installments", "order_discounts", "custom_amounts"}
    )
    if unknown:
        raise QuoteServiceError(
            f"unknown fields: {sorted(unknown)}",
            code="unknown_fields",
        )

    # `custom_amounts` is a per-write request flag, not a stored field.
    custom_amounts = bool(patch.pop("custom_amounts", False))

    bumped = False
    public_patch = {
        field_name: patch.get(field_name)
        for field_name in ("terms", "footer", "public_notes")
        if field_name in patch
    }
    if public_patch:
        try:
            assert_no_public_catalog_leaks(db, public_patch)
        except CatalogServiceError as exc:
            raise QuoteServiceError(
                str(exc), code=exc.code, **exc.extra
            ) from exc

    discount_changed = "order_discounts" in patch
    # Replace the discount stack first so line-item math reads the new
    # combined percent.
    if discount_changed:
        raw_rows = patch.get("order_discounts") or []
        try:
            new_snaps = snapshot_order_discounts(
                db,
                raw_rows,
                existing_snapshots=_current_order_discount_snapshots(
                    db, quote
                ),
            )
        except DiscountSnapshotError as exc:
            raise QuoteServiceError(
                str(exc), code=exc.code, **exc.extra
            ) from exc
        _replace_order_discounts(db, quote, new_snaps)
        if new_snaps or raw_rows == []:
            quote.discount_cents = 0
        bumped = True

    if "line_items" in patch:
        _replace_line_items(db, quote, patch["line_items"] or [])
        bumped = True
    elif discount_changed:
        _rerate_existing_line_items(db, quote)

    installments_changed = "installments" in patch
    if installments_changed:
        _replace_installments(db, quote, patch["installments"] or [])
        bumped = True

    for field_name, value in patch.items():
        if field_name in ("line_items", "installments", "order_discounts"):
            continue
        if field_name in _QUOTE_SCALAR_FIELDS:
            if field_name == "discount_cents":
                # Server-derived when the percent path is in use.
                if _has_order_discounts(db, quote):
                    continue
                if value is None:
                    value = 0
            setattr(quote, field_name, value)
            bumped = True

    bumped_revision = False
    if bumped:
        _recompute_totals(db, quote)
        if installments_changed:
            # Phase 5 plan validity gate; runs at write time so the
            # editor can not stash an unbalanced or out-of-bounds
            # schedule on a draft and have it discovered at conversion.
            _validate_plan_inputs(
                patch["installments"] or [],
                int(quote.total_cents or 0),
                custom_amounts=custom_amounts,
            )
        if quote.status == "sent":
            quote.revision = int(quote.revision or 1) + 1
            bumped_revision = True
        quote.updated_at = datetime.now(timezone.utc)

    db.flush()
    if bumped_revision:
        activity_log.log_activity(
            db,
            event_id=quote.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.QUOTE_UPDATED,
            subject_kind="quote",
            subject_id=quote.id,
            payload={
                "revision": int(quote.revision),
                "fields": sorted(set(patch.keys())),
            },
        )
    return quote


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


def mark_sent(
    db: Session,
    *,
    quote_id: int,
    actor_user_id: int | None = None,
    contact_ids: list[int] | None = None,
) -> Quote:
    """Transition draft → sent. Allocates `quote_number`. Creates one
    `quote_invitations` row per contact (defaulting to the quote's
    contact) with a freshly minted `public_key`."""
    quote = _get_quote_or_raise(db, quote_id)

    if quote.status != "draft":
        raise QuoteServiceError(
            f"cannot send quote in status {quote.status}",
            code="invalid_transition",
        )

    line_count = db.execute(
        sql_text("SELECT COUNT(*) FROM quote_line_items WHERE quote_id = :id"),
        {"id": quote_id},
    ).scalar()
    if not line_count:
        raise QuoteServiceError(
            "quote has no line items", code="line_items_required"
        )

    quote.quote_number = _assign_quote_number(db)
    quote.status = "sent"
    quote.sent_at = datetime.now(timezone.utc)
    db.flush()

    target_contact_ids = (
        list(contact_ids) if contact_ids else [quote.contact_id]
    )
    _ensure_invitations(db, quote_id, target_contact_ids, sending_now=True)

    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_SENT,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_number": quote.quote_number,
            "contact_ids": target_contact_ids,
        },
    )
    return quote


def resend_quote(
    db: Session,
    *,
    quote_id: int,
    contact_ids: list[int] | None = None,
    actor_user_id: int | None = None,
) -> Quote:
    """Re-send a sent quote. Bumps each invitation's `last_resent_at` (or
    creates new invitations for newly added contacts) without rotating
    the public_key — the existing portal link keeps working."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status != "sent":
        raise QuoteServiceError(
            f"cannot resend a quote in status {quote.status}",
            code="invalid_transition",
        )
    target_contact_ids = (
        list(contact_ids) if contact_ids else [quote.contact_id]
    )
    _ensure_invitations(db, quote_id, target_contact_ids, sending_now=True)
    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_RESENT,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_number": quote.quote_number,
            "contact_ids": target_contact_ids,
        },
    )
    return quote


def approve_quote(
    db: Session,
    *,
    quote_id: int,
    signature_base64: str,
    signature_name: str,
    signature_ip: str | None,
    actor_user_id: int | None = None,
) -> Quote:
    """Customer-signature path. Captures the signature and flips status
    to 'approved'. Refuses if status != 'sent'. Idempotent on a
    re-submission of the same status (the second call returns without
    overwriting the original signature)."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status == "approved":
        return quote  # idempotent
    if quote.status != "sent":
        raise QuoteServiceError(
            f"cannot approve quote in status {quote.status}",
            code="invalid_transition",
        )
    if not signature_base64 or not signature_name:
        raise QuoteServiceError(
            "signature_base64 and signature_name are required",
            code="signature_required",
        )

    now = datetime.now(timezone.utc)
    quote.signature_base64 = signature_base64
    quote.signature_signed_at = now
    quote.signature_ip = signature_ip
    quote.signature_name = signature_name[:120]
    quote.approved_at = now
    quote.status = "approved"
    quote.updated_at = now
    # C3: stamp HMAC over the canonical signed payload before flush so
    # the row commits with its signature_hmac populated. The CHECK
    # constraint refuses to land a signed row without one; the
    # immutability trigger then locks the field for life.
    quote_signature_hmac.stamp(quote)
    db.flush()
    # The cached PDF (if any) was rendered before the signature existed,
    # so the signature block would be missing. Drop it; next view re-
    # renders with the signature.
    invoice_pdf.invalidate_quote_pdf(quote)
    # Customer signed the quote in the portal — actor is the customer,
    # not staff. Two activity rows: one for the signature event itself
    # (audit), one for the status flip (timeline-friendly label).
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="customer",
        actor_user_id=None,
        activity_type=activity_log.QUOTE_SIGNED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_number": quote.quote_number,
            "signature_name": quote.signature_name,
            "signature_ip": str(quote.signature_ip) if quote.signature_ip else None,
        },
    )
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="customer",
        actor_user_id=None,
        activity_type=activity_log.QUOTE_APPROVED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"quote_number": quote.quote_number},
    )
    return quote


def approve_in_store(
    db: Session,
    *,
    quote_id: int,
    signature_base64: str,
    signature_name: str,
    signature_ip: str | None,
    actor_user_id: int,
    signature_user_agent: str | None = None,
) -> Quote:
    """Staff-witnessed in-store approval. The customer signs on a staff
    device at the counter, so this path:

    - accepts ``draft`` *or* ``sent`` as the source status (no need to
      bounce through the customer-email flow first);
    - records the signature exactly like the portal path
      (``signature_base64`` + ``signature_name`` + IP);
    - logs ``actor_kind='staff'`` with ``QUOTE_APPROVED_IN_STORE`` so the
      audit trail can distinguish in-store closes from customer self-sign.

    Idempotent on a re-submission (already-approved quote returns
    unchanged). Refuses any status outside ``draft``/``sent``.
    """
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status == "approved":
        return quote
    if quote.status not in ("draft", "sent"):
        raise QuoteServiceError(
            f"cannot approve quote in status {quote.status}",
            code="invalid_transition",
        )
    if not signature_base64 or not signature_name:
        raise QuoteServiceError(
            "signature_base64 and signature_name are required",
            code="signature_required",
        )

    now = datetime.now(timezone.utc)
    from_status = quote.status
    # Draft path skips the customer-email round-trip but still needs a
    # quote_number (the chk_quote_number_when_not_draft check rejects
    # non-draft rows without one) and at least one line item.
    if from_status == "draft":
        line_count = db.execute(
            sql_text("SELECT COUNT(*) FROM quote_line_items WHERE quote_id = :id"),
            {"id": quote_id},
        ).scalar()
        if not line_count:
            raise QuoteServiceError(
                "quote has no line items", code="line_items_required"
            )
        quote.quote_number = _assign_quote_number(db)
        quote.sent_at = now
    quote.signature_base64 = signature_base64
    quote.signature_signed_at = now
    quote.signature_ip = signature_ip
    quote.signature_name = signature_name[:120]
    if signature_user_agent:
        quote.signature_user_agent = signature_user_agent[:255]
    quote.approved_at = now
    quote.status = "approved"
    quote.updated_at = now
    # C3: stamp HMAC after every signature field is set, before flush.
    # Same justification as approve_quote (portal path) above.
    quote_signature_hmac.stamp(quote)
    db.flush()
    # Drop any cached pre-signature PDF so the next view re-renders with
    # the signature block. Same reason as the customer-portal path.
    invoice_pdf.invalidate_quote_pdf(quote)
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_APPROVED_IN_STORE,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_number": quote.quote_number,
            "signature_name": quote.signature_name,
            "signature_ip": str(quote.signature_ip) if quote.signature_ip else None,
            "from_status": from_status,
        },
    )
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_APPROVED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"quote_number": quote.quote_number},
    )

    # Phase 9.4 D3: surface the staff-witnessed in-store approval on the
    # staff notification event bus. ``digest`` timing means record_event
    # writes the event log row only; the daily digest worker summarises
    # for the lead/event owner (intrinsic targeting). The staff who
    # witnessed the signature already saw it happen — the owner who
    # wasn't on the floor is the audience for this signal.
    notification_routing.record_event(
        db,
        kind="quote.approved_in_store",
        subject_kind="event",
        subject_id=quote.event_id,
        actor_user_id=actor_user_id,
        payload={
            "quote_id": quote.id,
            "quote_number": quote.quote_number,
            "signature_name": quote.signature_name,
            "approved_at": (
                quote.approved_at.isoformat()
                if quote.approved_at is not None
                else None
            ),
        },
    )
    return quote


def reject_quote(
    db: Session,
    *,
    quote_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Quote:
    """Move a sent quote to 'rejected'. Customer can decline in portal,
    or staff can mark rejected on their behalf. Idempotent."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status == "rejected":
        return quote
    if quote.status != "sent":
        raise QuoteServiceError(
            f"cannot reject quote in status {quote.status}",
            code="invalid_transition",
        )
    try:
        assert_no_public_catalog_leaks(db, {"rejection_reason": reason})
    except CatalogServiceError as exc:
        raise QuoteServiceError(str(exc), code=exc.code, **exc.extra) from exc
    now = datetime.now(timezone.utc)
    quote.status = "rejected"
    quote.rejected_at = now
    quote.rejection_reason = reason
    quote.updated_at = now
    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "customer",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_REJECTED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"quote_number": quote.quote_number, "reason": reason},
    )
    return quote


def cancel_quote(
    db: Session,
    *,
    quote_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Quote:
    """Staff-initiated cancellation of a sent quote. Drafts have no
    quote_number yet — chk_quote_number_when_not_draft would reject the
    transition — so callers must soft-delete drafts instead. Approved
    quotes are already a contract; void by converting and cancelling
    the resulting invoice."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status == "cancelled":
        return quote
    if quote.status == "draft":
        raise QuoteServiceError(
            "cannot cancel a draft quote — soft-delete it instead",
            code="cancel_draft_not_allowed",
        )
    if quote.status != "sent":
        raise QuoteServiceError(
            f"cannot cancel quote in status {quote.status}",
            code="invalid_transition",
        )
    try:
        assert_no_public_catalog_leaks(
            db, {"cancellation_reason": reason}
        )
    except CatalogServiceError as exc:
        raise QuoteServiceError(str(exc), code=exc.code, **exc.extra) from exc
    now = datetime.now(timezone.utc)
    quote.status = "cancelled"
    quote.cancelled_at = now
    quote.cancellation_reason = reason
    quote.updated_at = now
    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_CANCELLED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"quote_number": quote.quote_number, "reason": reason},
    )
    return quote


def soft_delete_quote(
    db: Session,
    *,
    quote_id: int,
    actor_user_id: int | None = None,
) -> None:
    """Drafts can be soft-deleted outright. Sent quotes must be cancelled
    or rejected first — soft-delete on a live quote would leave dangling
    portal links."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status not in ("draft", "rejected", "expired", "cancelled"):
        raise QuoteServiceError(
            f"cannot delete quote in status {quote.status}; "
            "cancel or reject first",
            code="quote_not_deletable",
        )
    quote.deleted_at = datetime.now(timezone.utc)
    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_DELETED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={"quote_number": quote.quote_number},
    )


def convert_to_invoice(
    db: Session,
    *,
    quote_id: int,
    actor_user_id: int | None = None,
) -> Invoice:
    """Approved quote → draft invoice. Copies line items, terms, footer,
    notes, PO number. Creates a default two-row installment schedule
    (50/50 deposit + balance) anchored to the event date. Stamps
    `converted_at` and `converted_invoice_id` on the quote and flips its
    status to 'converted' — terminal."""
    quote = _get_quote_or_raise(db, quote_id)
    if quote.status == "converted":
        # Idempotent: return the existing converted invoice.
        if quote.converted_invoice_id is None:
            # Should be impossible per chk_quote_converted_consistent.
            raise QuoteServiceError(
                "converted quote has no invoice link",
                code="conversion_inconsistent",
            )
        existing = db.get(Invoice, quote.converted_invoice_id)
        if existing is not None and existing.deleted_at is None:
            return existing
        # The linked invoice is gone (hard-deleted, leaving the FK null
        # via ON DELETE SET NULL) or soft-deleted. Auto-heal the linkage
        # and let the rest of this function create a fresh invoice.
        # `soft_delete_invoice` already unlinks in the well-behaved
        # path; this is a defensive backstop for direct DB intervention
        # or older rows that pre-date that fix.
        quote.status = "approved"
        quote.converted_invoice_id = None
        quote.converted_at = None
        db.flush()
    if quote.status != "approved":
        raise QuoteServiceError(
            f"cannot convert quote in status {quote.status}; must be approved",
            code="invalid_transition",
        )

    # Pull line rows in stable order so the new invoice's lines mirror
    # the quote's exactly.
    quote_lines = (
        db.query(QuoteLineItem)
        .filter(QuoteLineItem.quote_id == quote_id)
        .order_by(QuoteLineItem.sort_order.asc(), QuoteLineItem.id.asc())
        .all()
    )

    # Pull event_date for the schedule anchor.
    event_row = db.execute(
        sql_text("SELECT event_date FROM events WHERE id = :id"),
        {"id": quote.event_id},
    ).first()
    event_date = event_row[0] if event_row is not None else None

    invoice = Invoice(
        event_id=quote.event_id,
        contact_id=quote.contact_id,
        status="draft",
        issue_date=date.today(),
        revision=1,
        # Carry the legacy flat-amount field forward; the stacked
        # order discounts copy below replaces the Phase 2a single
        # snapshot with one row per discount, snapshotted verbatim
        # so a later preset rename cannot rewrite history.
        discount_cents=int(quote.discount_cents or 0),
        terms=quote.terms,
        footer=quote.footer,
        public_notes=quote.public_notes,
        private_notes=quote.private_notes,
        po_number=quote.po_number,
        created_by_user_id=actor_user_id,
    )
    db.add(invoice)
    db.flush()

    # Copy the order-discount stack verbatim so the printed copy and
    # the historical money math survive a later edit on the source
    # preset.
    quote_discounts = (
        db.query(QuoteOrderDiscount)
        .filter(QuoteOrderDiscount.quote_id == quote_id)
        .order_by(
            QuoteOrderDiscount.sort_order.asc(),
            QuoteOrderDiscount.id.asc(),
        )
        .all()
    )
    for qd in quote_discounts:
        db.add(
            InvoiceOrderDiscount(
                invoice_id=invoice.id,
                sort_order=int(qd.sort_order or 0),
                preset_id=qd.preset_id,
                label=qd.label,
                percent=qd.percent,
            )
        )
    db.flush()

    for ql in quote_lines:
        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                sort_order=int(ql.sort_order or 0),
                kind=ql.kind,
                product_key=ql.product_key,
                description=ql.description,
                quantity=ql.quantity,
                unit_price_cents=int(ql.unit_price_cents),
                discount_cents=int(ql.discount_cents or 0),
                tax_rate=ql.tax_rate,
                tax_name=ql.tax_name,
                line_subtotal_cents=int(ql.line_subtotal_cents),
                line_tax_cents=int(ql.line_tax_cents),
                line_total_cents=int(ql.line_total_cents),
                notes=ql.notes,
                # Carry the catalog linkage forward so a converted quote's
                # invoice keeps catalog-backed customer rendering. Without
                # this copy, an approved quote with a Mori Lee dress
                # would convert into a non-catalog invoice line that
                # leaks staff free text.
                catalog_item_id=ql.catalog_item_id,
                size_label=ql.size_label,
                public_description=ql.public_description,
                internal_notes=ql.internal_notes,
            )
        )
    db.flush()

    # Recompute on the invoice from its now-populated lines so the
    # totals come from the canonical SUM, not from the quote's stored
    # values (defends against any drift between quote.total_cents and
    # SUM(quote_line_items.line_total_cents)).
    _recompute_invoice_totals_from_lines(db, invoice)

    # Schedule. If the quote already carries installments (Phase 4),
    # copy them line for line so the customer's chosen plan survives
    # conversion. Falls through to the legacy default 50/50 schedule
    # only when the quote has no installments — preserves the pre-
    # Phase-4 behavior for older quotes that never collected a plan.
    quote_installments = (
        db.query(QuoteInstallment)
        .filter(QuoteInstallment.quote_id == quote.id)
        .order_by(QuoteInstallment.sort_order.asc(), QuoteInstallment.id.asc())
        .all()
    )
    total = int(invoice.total_cents or 0)
    if quote_installments:
        for idx, qi in enumerate(quote_installments):
            db.add(
                InvoiceInstallment(
                    invoice_id=invoice.id,
                    sort_order=int(qi.sort_order or idx),
                    # `invoice_installments.label` is NOT NULL — fall
                    # back to a numbered default when the quote row
                    # left it blank. Customer-facing copy stays neutral
                    # and matches what `defaultSchedule` produces.
                    label=(qi.label or f"Installment {idx + 1}"),
                    amount_cents=int(qi.amount_cents),
                    due_date=qi.due_date,
                )
            )
        invoice.due_date = max(qi.due_date for qi in quote_installments)
        db.flush()
    elif total > 0:
        # Default two-row schedule. Skip when total is zero (no
        # installments makes sense; staff can still draft and add
        # lines/schedule before sending). Defaults match
        # InvoiceEditor.defaultSchedule so the converted-quote and
        # from-scratch flows produce the same plan: deposit due 14d
        # after issue, balance due 60d before the event (floored at
        # deposit + 14d so it can't land before the deposit).
        deposit = _split_half_even(total)
        balance = total - deposit
        deposit_due = invoice.issue_date + timedelta(days=14)
        min_balance_due = deposit_due + timedelta(days=14)
        if event_date is not None:
            balance_due = event_date - timedelta(days=60)
            if balance_due < min_balance_due:
                balance_due = deposit_due + timedelta(days=30)
        else:
            balance_due = deposit_due + timedelta(days=30)
        db.add(
            InvoiceInstallment(
                invoice_id=invoice.id,
                sort_order=0,
                label="Deposit",
                amount_cents=deposit,
                due_date=deposit_due,
            )
        )
        db.add(
            InvoiceInstallment(
                invoice_id=invoice.id,
                sort_order=1,
                label="Balance",
                amount_cents=balance,
                due_date=balance_due,
            )
        )
        invoice.due_date = balance_due
        db.flush()

    # Stamp the conversion on the quote AFTER the invoice is committed
    # to a real id. Otherwise chk_quote_converted_consistent would fire
    # at flush time mid-transformation.
    now = datetime.now(timezone.utc)
    quote.converted_invoice_id = invoice.id
    quote.converted_at = now
    quote.status = "converted"
    quote.updated_at = now
    db.flush()
    activity_log.log_activity(
        db,
        event_id=quote.event_id,
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.QUOTE_CONVERTED,
        subject_kind="quote",
        subject_id=quote.id,
        payload={
            "quote_number": quote.quote_number,
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
        },
    )
    return invoice


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@dataclass
class QuoteLineView:
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
class QuoteInvitationView:
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
class QuoteInstallmentView:
    """Customer-/staff-safe projection of one quote installment row.

    Mirrors `invoice_service.InstallmentView` minus the payment-state
    fields that don't exist on the quote table.
    """

    id: int
    sort_order: int
    label: str | None
    amount_cents: int
    due_date: date


@dataclass
class OrderDiscountView:
    id: int
    sort_order: int
    preset_id: str | None
    label: str
    percent: Decimal


@dataclass
class QuoteDetail:
    id: int
    event_id: int
    contact_id: int
    quote_number: str | None
    status: str
    issue_date: date
    expires_at: date | None
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    order_discounts: list["OrderDiscountView"]
    terms: str | None
    footer: str | None
    public_notes: str | None
    private_notes: str | None
    po_number: str | None
    revision: int
    sent_at: datetime | None
    viewed_at: datetime | None
    approved_at: datetime | None
    rejected_at: datetime | None
    rejection_reason: str | None
    converted_at: datetime | None
    converted_invoice_id: int | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    signature_signed_at: datetime | None
    signature_name: str | None
    signature_ip: str | None
    # signature_base64 deliberately omitted from the detail dataclass —
    # the router exposes it on a separate signed-quote PDF/render path so
    # a 50KB image isn't dragged through every list response.
    last_pdf_rendered_revision: int | None
    last_pdf_rendered_at: datetime | None
    last_pdf_render_error: str | None
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    line_items: list[QuoteLineView] = field(default_factory=list)
    installments: list[QuoteInstallmentView] = field(default_factory=list)
    invitations: list[QuoteInvitationView] = field(default_factory=list)


@dataclass
class QuoteSummary:
    id: int
    event_id: int
    contact_id: int
    contact_name: str
    quote_number: str | None
    status: str
    issue_date: date
    expires_at: date | None
    total_cents: int
    sent_at: datetime | None
    approved_at: datetime | None
    converted_at: datetime | None
    converted_invoice_id: int | None
    created_at: datetime


def get_quote_detail(db: Session, quote_id: int) -> QuoteDetail:
    quote = _get_quote_or_raise(db, quote_id)
    line_rows = (
        db.query(QuoteLineItem)
        .filter(QuoteLineItem.quote_id == quote_id)
        .order_by(QuoteLineItem.sort_order.asc(), QuoteLineItem.id.asc())
        .all()
    )
    catalog_by_id = _load_catalog_snapshots(
        db, [r.catalog_item_id for r in line_rows if r.catalog_item_id]
    )
    inv_rows = (
        db.query(QuoteInvitation)
        .filter(QuoteInvitation.quote_id == quote_id)
        .filter(QuoteInvitation.deleted_at.is_(None))
        .order_by(QuoteInvitation.id.asc())
        .all()
    )
    inst_rows = (
        db.query(QuoteInstallment)
        .filter(QuoteInstallment.quote_id == quote_id)
        .order_by(QuoteInstallment.sort_order.asc(), QuoteInstallment.id.asc())
        .all()
    )
    discount_rows = (
        db.query(QuoteOrderDiscount)
        .filter(QuoteOrderDiscount.quote_id == quote_id)
        .order_by(
            QuoteOrderDiscount.sort_order.asc(),
            QuoteOrderDiscount.id.asc(),
        )
        .all()
    )
    return QuoteDetail(
        id=quote.id,
        event_id=quote.event_id,
        contact_id=quote.contact_id,
        quote_number=quote.quote_number,
        status=quote.status,
        issue_date=quote.issue_date,
        expires_at=quote.expires_at,
        subtotal_cents=int(quote.subtotal_cents or 0),
        discount_cents=int(quote.discount_cents or 0),
        tax_cents=int(quote.tax_cents or 0),
        total_cents=int(quote.total_cents or 0),
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
        terms=quote.terms,
        footer=quote.footer,
        public_notes=quote.public_notes,
        private_notes=quote.private_notes,
        po_number=quote.po_number,
        revision=int(quote.revision or 1),
        sent_at=quote.sent_at,
        viewed_at=quote.viewed_at,
        approved_at=quote.approved_at,
        rejected_at=quote.rejected_at,
        rejection_reason=quote.rejection_reason,
        converted_at=quote.converted_at,
        converted_invoice_id=quote.converted_invoice_id,
        cancelled_at=quote.cancelled_at,
        cancellation_reason=quote.cancellation_reason,
        signature_signed_at=quote.signature_signed_at,
        signature_name=quote.signature_name,
        signature_ip=str(quote.signature_ip) if quote.signature_ip else None,
        last_pdf_rendered_revision=(
            int(quote.last_pdf_rendered_revision)
            if quote.last_pdf_rendered_revision is not None
            else None
        ),
        last_pdf_rendered_at=quote.last_pdf_rendered_at,
        last_pdf_render_error=quote.last_pdf_render_error,
        created_by_user_id=quote.created_by_user_id,
        created_at=quote.created_at,
        updated_at=quote.updated_at,
        deleted_at=quote.deleted_at,
        line_items=[
            _to_line_view(r, catalog_by_id.get(r.catalog_item_id))
            for r in line_rows
        ],
        installments=[
            QuoteInstallmentView(
                id=int(r.id),
                sort_order=int(r.sort_order or 0),
                label=r.label,
                amount_cents=int(r.amount_cents),
                due_date=r.due_date,
            )
            for r in inst_rows
        ],
        invitations=[_to_invitation_view(r) for r in inv_rows],
    )


def list_quotes_for_event(
    db: Session,
    *,
    event_id: int,
    include_deleted: bool = False,
    status: str | None = None,
) -> list[QuoteSummary]:
    q = (
        db.query(Quote, Contact.display_name.label("contact_name"))
        .join(Contact, Contact.id == Quote.contact_id)
        .filter(Quote.event_id == event_id)
    )
    if not include_deleted:
        q = q.filter(Quote.deleted_at.is_(None))
    if status:
        q = q.filter(Quote.status == status)
    q = q.order_by(Quote.created_at.desc())
    return [_to_summary(quote, name) for quote, name in q.all()]


def search_quotes(
    db: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    event_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_deleted: bool = False,
    limit: int = 100,
) -> list[QuoteSummary]:
    query = (
        db.query(Quote, Contact.display_name.label("contact_name"))
        .join(Contact, Contact.id == Quote.contact_id)
    )
    if not include_deleted:
        query = query.filter(Quote.deleted_at.is_(None))
    if status:
        query = query.filter(Quote.status == status)
    if event_id is not None:
        query = query.filter(Quote.event_id == event_id)
    if date_from is not None:
        query = query.filter(Quote.issue_date >= date_from)
    if date_to is not None:
        query = query.filter(Quote.issue_date <= date_to)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            (func.lower(Quote.quote_number).like(like))
            | (func.lower(Contact.display_name).like(like))
        )
    query = (
        query.order_by(
            Quote.sent_at.desc().nulls_last(),
            Quote.created_at.desc(),
        )
        .limit(min(int(limit), 500))
    )
    return [_to_summary(quote, name) for quote, name in query.all()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_quote_or_raise(db: Session, quote_id: int) -> Quote:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise QuoteServiceError("quote not found", code="quote_not_found")
    return quote


def _read_order_discount_total(db: Session, quote: Quote) -> Decimal | None:
    """Sum of per-row percents on `quote_order_discounts` for this
    quote, or `None` when the stack is empty (legacy flat-amount
    path)."""
    row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(percent), 0), COUNT(*) "
            "FROM quote_order_discounts WHERE quote_id = :id"
        ),
        {"id": quote.id},
    ).one()
    if int(row[1]) == 0:
        return None
    return Decimal(str(row[0]))


def _has_order_discounts(db: Session, quote: Quote) -> bool:
    return _read_order_discount_total(db, quote) is not None


def _current_order_discount_snapshots(
    db: Session, quote: Quote
) -> list[DiscountRowSnapshot]:
    rows = (
        db.query(QuoteOrderDiscount)
        .filter(QuoteOrderDiscount.quote_id == quote.id)
        .order_by(
            QuoteOrderDiscount.sort_order.asc(),
            QuoteOrderDiscount.id.asc(),
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
    quote: Quote,
    snaps: list,
) -> None:
    """Wipe and re-insert the quote's order-discount stack."""
    db.execute(
        sql_text(
            "DELETE FROM quote_order_discounts WHERE quote_id = :id"
        ),
        {"id": quote.id},
    )
    for idx, snap in enumerate(snaps):
        db.add(
            QuoteOrderDiscount(
                quote_id=quote.id,
                sort_order=idx,
                preset_id=snap.preset_id,
                label=snap.label,
                percent=snap.percent,
            )
        )
    db.flush()


def _rerate_existing_line_items(db: Session, quote: Quote) -> None:
    """Re-round per-line cents in place against the parent's current
    combined order-discount percent.

    Mirror of `invoice_service._rerate_existing_line_items` for the
    `update_quote` path that toggles only the order discount stack.
    """
    order_pct = _read_order_discount_total(db, quote)
    rows = db.execute(
        sql_text(
            "SELECT id, quantity, unit_price_cents, discount_cents, tax_rate "
            "FROM quote_line_items WHERE quote_id = :id"
        ),
        {"id": quote.id},
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
                "UPDATE quote_line_items SET "
                "line_subtotal_cents = :sub, "
                "line_tax_cents = :tax, "
                "line_total_cents = :total "
                "WHERE id = :id"
            ),
            {"id": r.id, "sub": sub, "tax": tax, "total": total},
        )
    db.flush()


def _replace_line_items(
    db: Session, quote: Quote, line_items: list[LineItemInput]
) -> None:
    order_pct = _read_order_discount_total(db, quote)
    db.execute(
        sql_text("DELETE FROM quote_line_items WHERE quote_id = :id"),
        {"id": quote.id},
    )
    for idx, li in enumerate(line_items):
        sub, tax, total = _compute_line_amounts(li, order_pct)
        sort_order = li.sort_order if li.sort_order is not None else idx
        # Reuse the invoice service's resolver so catalog-backed line
        # validation, the forbidden-substring guard, and the
        # public_description vs legacy description routing all behave
        # identically across the two write paths.
        try:
            kwargs = _resolve_line_kwargs(db, li)
        except Exception as exc:
            # Translate invoice-service domain errors to quote-service
            # codes so router error mapping stays consistent. The codes
            # themselves stay stable; only the wrapping exception type
            # changes.
            inv_code = getattr(exc, "code", "line_validation_failed")
            inv_extra = getattr(exc, "extra", {}) or {}
            raise QuoteServiceError(
                str(exc), code=inv_code, **inv_extra
            ) from exc
        db.add(
            QuoteLineItem(
                quote_id=quote.id,
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


def _replace_installments(
    db: Session,
    quote: Quote,
    installments: list[QuoteInstallmentInput],
) -> None:
    """Wipe and reinsert the quote's payment schedule.

    Mirrors `invoice_service._replace_installments` minus the payment-
    state passthroughs (no `paid_at`, no `staff_notes` — quotes never
    carry payment state).
    """
    db.execute(
        sql_text("DELETE FROM quote_installments WHERE quote_id = :id"),
        {"id": quote.id},
    )
    for idx, inst in enumerate(installments):
        sort_order = inst.sort_order if inst.sort_order is not None else idx
        label = (inst.label or "").strip() or None
        db.add(
            QuoteInstallment(
                quote_id=quote.id,
                sort_order=sort_order,
                label=label,
                amount_cents=int(inst.amount_cents),
                due_date=inst.due_date,
            )
        )
    db.flush()


def _recompute_totals(db: Session, quote: Quote) -> None:
    """Mirror of `invoice_service._recompute_totals` for quotes.

    `subtotal_cents` is the post-line-discount, pre-order-discount
    taxable base in both the legacy and new-percent paths so the existing
    PDF totals block keeps rendering correctly until Phase 3 reworks it.
    """
    rows = db.execute(
        sql_text(
            "SELECT quantity, unit_price_cents, discount_cents, "
            "       line_subtotal_cents, line_tax_cents, line_total_cents "
            "FROM quote_line_items WHERE quote_id = :id"
        ),
        {"id": quote.id},
    ).all()

    pre_order_sub_total = 0
    tax_total = 0
    line_total_sum = 0
    for r in rows:
        qty = Decimal(str(r.quantity))
        unit = Decimal(int(r.unit_price_cents))
        line_disc = int(r.discount_cents or 0)
        line_pre_order = int(
            (qty * unit - Decimal(line_disc)).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )
        pre_order_sub_total += line_pre_order
        tax_total += int(r.line_tax_cents)
        line_total_sum += int(r.line_total_cents)

    quote.tax_cents = tax_total
    quote.subtotal_cents = pre_order_sub_total

    pct = _read_order_discount_total(db, quote)
    if pct is not None:
        quote.discount_cents = int(
            (Decimal(pre_order_sub_total) * pct / Decimal(100))
            .to_integral_value(rounding=ROUND_HALF_EVEN)
        )
        quote.total_cents = line_total_sum
    else:
        quote.total_cents = line_total_sum - int(quote.discount_cents or 0)


def _recompute_invoice_totals_from_lines(db: Session, invoice: Invoice) -> None:
    """Mirror of invoice_service._recompute_totals so convert_to_invoice
    can finalize the new invoice's totals without depending on the
    invoice service's private internals being stable."""
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

    # Sum percents from the new invoice's order-discount stack (just
    # populated by convert_to_invoice from the quote's stack).
    inv_pct_row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(percent), 0), COUNT(*) "
            "FROM invoice_order_discounts WHERE invoice_id = :id"
        ),
        {"id": invoice.id},
    ).one()
    if int(inv_pct_row[1]) > 0:
        pct = Decimal(str(inv_pct_row[0]))
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


def _assign_quote_number(db: Session) -> str:
    """Allocate the next quote number under a row-level lock on
    `numbering_state`. Resets the sequence on year rollover. Mirrors
    `_assign_invoice_number` in shape so concurrent quote+invoice sends
    don't deadlock — they hit the same row, in line."""
    row = db.execute(
        sql_text(
            "SELECT quote_year, quote_seq FROM numbering_state "
            "WHERE id = 1 FOR UPDATE"
        )
    ).one()
    current_year = datetime.now(timezone.utc).year
    if int(row.quote_year) != current_year:
        new_year, new_seq = current_year, 1
    else:
        new_year, new_seq = int(row.quote_year), int(row.quote_seq) + 1
    db.execute(
        sql_text(
            "UPDATE numbering_state SET quote_year = :y, quote_seq = :s, "
            "updated_at = NOW() WHERE id = 1"
        ),
        {"y": new_year, "s": new_seq},
    )
    return f"Q-{new_year}-{new_seq:06d}"


def _ensure_invitations(
    db: Session,
    quote_id: int,
    contact_ids: list[int],
    *,
    sending_now: bool,
) -> None:
    """Idempotent: if a row already exists for (quote, contact), reuse
    the public_key and bump `sent_at` / `last_resent_at`. Revoked or
    soft-deleted rows are treated as absent so a rotation produces a
    new key."""
    now = datetime.now(timezone.utc)
    for cid in contact_ids:
        row = db.execute(
            sql_text(
                "SELECT id FROM quote_invitations "
                "WHERE quote_id = :q AND contact_id = :c "
                "AND deleted_at IS NULL AND revoked_at IS NULL"
            ),
            {"q": quote_id, "c": cid},
        ).first()
        if row is None:
            db.add(
                QuoteInvitation(
                    quote_id=quote_id,
                    contact_id=cid,
                    public_key=secrets.token_urlsafe(32),
                    sent_at=now if sending_now else None,
                )
            )
        elif sending_now:
            db.execute(
                sql_text(
                    "UPDATE quote_invitations "
                    "SET sent_at = COALESCE(sent_at, :now), "
                    "    last_resent_at = :now, "
                    "    updated_at = :now "
                    "WHERE id = :id"
                ),
                {"now": now, "id": row.id},
            )


def invitation_ids_for_contacts(
    db: Session, *, quote_id: int, contact_ids: list[int]
) -> list[int]:
    """Return the active invitation row ids for the given contacts on
    this quote. Used by the resend router so it can scope the email
    dispatch after the timestamps have already been bumped."""
    if not contact_ids:
        return []
    rows = (
        db.query(QuoteInvitation.id)
        .filter(QuoteInvitation.quote_id == quote_id)
        .filter(QuoteInvitation.contact_id.in_(contact_ids))
        .filter(QuoteInvitation.deleted_at.is_(None))
        .filter(QuoteInvitation.revoked_at.is_(None))
        .all()
    )
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# View converters
# ---------------------------------------------------------------------------


def _to_line_view(
    r: QuoteLineItem, catalog: CatalogItem | None = None
) -> QuoteLineView:
    return QuoteLineView(
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


def _to_invitation_view(r: QuoteInvitation) -> QuoteInvitationView:
    return QuoteInvitationView(
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


def _to_summary(quote: Quote, contact_name: str) -> QuoteSummary:
    return QuoteSummary(
        id=quote.id,
        event_id=quote.event_id,
        contact_id=quote.contact_id,
        contact_name=contact_name,
        quote_number=quote.quote_number,
        status=quote.status,
        issue_date=quote.issue_date,
        expires_at=quote.expires_at,
        total_cents=int(quote.total_cents or 0),
        sent_at=quote.sent_at,
        approved_at=quote.approved_at,
        converted_at=quote.converted_at,
        converted_invoice_id=quote.converted_invoice_id,
        created_at=quote.created_at,
    )
