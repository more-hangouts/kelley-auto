"""Payment domain service.

Holds the four-way money split (`amount = applied + refunded + unapplied`),
the allocation lifecycle (record/apply/unapply/refund), and the
recomputation engine that keeps invoice `paid_to_date_cents`,
`balance_cents`, status, and per-installment `paid_at` in lockstep.

By spec: no triggers. Every mutation calls `_recompute_payment_totals` for
the touched payment and `_recompute_invoice_totals` for every affected
invoice in the same transaction. The chk_payment_amount_consistent and
chk_invoice_balance_consistent CHECKs are defense-in-depth — the service
is the source of truth.

Refund model:
- Refunds never produce a new negative-amount payment row. They bump
  `payments.refunded_cents` and the per-allocation `refunded_cents` slice
  it claws back from, plus optionally an unapplied-pool slice.
- Each refund operation appends a `refund_events` audit row with the
  per-allocation breakdown so the activity timeline (Phase 9) can render
  the history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from database.models import (
    Contact,
    Invoice,
    Payment,
    PaymentAllocation,
    RefundEvent,
    User,
)
from services import activity_log
from services.email_transport import send_rendered_safely


def _event_ids_for_invoices(
    db: Session, invoice_ids
) -> list[int]:
    """Distinct event_ids for the given invoice_ids. Used by the
    activity logger so a single payment that touches invoices on more
    than one event leaves a trail on each event's timeline."""
    if not invoice_ids:
        return []
    rows = db.execute(
        sql_text(
            "SELECT DISTINCT event_id FROM invoices "
            "WHERE id = ANY(:ids) AND deleted_at IS NULL"
        ),
        {"ids": list(invoice_ids)},
    ).all()
    return [int(r[0]) for r in rows]


_PAYMENT_METHODS = frozenset(
    {"cash", "check", "card", "transfer", "zelle", "other"}
)


class PaymentServiceError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "payment_error",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class AllocationInput:
    """A request to apply `applied_cents` of a payment to one invoice."""

    invoice_id: int
    applied_cents: int


@dataclass
class AllocationRefundInput:
    """A request to claw back `refund_cents` from one existing allocation."""

    allocation_id: int
    refund_cents: int


# ---------------------------------------------------------------------------
# Create / Apply
# ---------------------------------------------------------------------------


def record_payment(
    db: Session,
    *,
    contact_id: int,
    amount_cents: int,
    method: str,
    payment_date: date | None = None,
    transaction_reference: str | None = None,
    notes: str | None = None,
    allocations: list[AllocationInput] | None = None,
    actor_user_id: int | None = None,
) -> Payment:
    """Record gross funds received and apply some/all to invoices.

    `SUM(allocations) <= amount_cents` (the rest goes to the unapplied
    pool). Each allocation must keep its target invoice's
    `paid_to_date_cents <= total_cents`. Drafts cannot receive
    allocations — by Phase 6 spec, only sent/partial/paid invoices have
    money state."""
    if amount_cents <= 0:
        raise PaymentServiceError(
            "amount must be positive",
            code="invalid_amount",
            amount_cents=amount_cents,
        )
    if method not in _PAYMENT_METHODS:
        raise PaymentServiceError(
            f"unknown payment method: {method}", code="invalid_method"
        )
    if db.get(Contact, contact_id) is None:
        raise PaymentServiceError("contact not found", code="contact_not_found")

    allocations = list(allocations or [])
    for a in allocations:
        if a.applied_cents <= 0:
            raise PaymentServiceError(
                "allocation must be positive",
                code="invalid_allocation",
                invoice_id=a.invoice_id,
                applied_cents=a.applied_cents,
            )
    alloc_sum = sum(a.applied_cents for a in allocations)
    if alloc_sum > amount_cents:
        raise PaymentServiceError(
            f"allocation sum {alloc_sum} exceeds amount {amount_cents}",
            code="over_allocation",
            allocation_sum_cents=alloc_sum,
            amount_cents=amount_cents,
        )

    # Per-invoice cap: each allocation must fit under the invoice's
    # remaining balance. We also reject allocation to drafts and
    # soft-deleted invoices since they have no business carrying money.
    seen_invoice_ids: set[int] = set()
    for a in allocations:
        if a.invoice_id in seen_invoice_ids:
            raise PaymentServiceError(
                "duplicate invoice in allocations",
                code="duplicate_allocation",
                invoice_id=a.invoice_id,
            )
        seen_invoice_ids.add(a.invoice_id)
        inv = db.get(Invoice, a.invoice_id)
        if inv is None:
            raise PaymentServiceError(
                "invoice not found",
                code="invoice_not_found",
                invoice_id=a.invoice_id,
            )
        if inv.deleted_at is not None:
            raise PaymentServiceError(
                "cannot allocate to deleted invoice",
                code="invalid_allocation_target",
                invoice_id=a.invoice_id,
            )
        if inv.status in ("draft", "cancelled", "reversed"):
            raise PaymentServiceError(
                f"cannot allocate to {inv.status} invoice",
                code="invalid_allocation_target",
                invoice_id=a.invoice_id,
                invoice_status=inv.status,
            )
        new_paid = int(inv.paid_to_date_cents or 0) + a.applied_cents
        if new_paid > int(inv.total_cents or 0):
            raise PaymentServiceError(
                f"allocation would push invoice {a.invoice_id} paid "
                f"to {new_paid} > total {inv.total_cents}",
                code="invoice_overallocation",
                invoice_id=a.invoice_id,
                requested_paid_cents=new_paid,
                total_cents=int(inv.total_cents or 0),
            )

    payment = Payment(
        contact_id=contact_id,
        payment_number=_assign_payment_number(db),
        amount_cents=int(amount_cents),
        applied_cents=alloc_sum,
        unapplied_cents=int(amount_cents) - alloc_sum,
        refunded_cents=0,
        payment_date=payment_date or date.today(),
        method=method,
        transaction_reference=transaction_reference,
        notes=notes,
        status="completed",
        created_by_user_id=actor_user_id,
    )
    db.add(payment)
    db.flush()  # need payment.id for allocations

    for a in allocations:
        db.add(
            PaymentAllocation(
                payment_id=payment.id,
                invoice_id=a.invoice_id,
                applied_cents=int(a.applied_cents),
            )
        )
    db.flush()

    # Recompute each affected invoice. The payment itself was set up
    # consistently above and doesn't need a recompute.
    for invoice_id in seen_invoice_ids:
        _recompute_invoice_totals(db, invoice_id)

    for ev_id in _event_ids_for_invoices(db, seen_invoice_ids):
        activity_log.log_activity(
            db,
            event_id=ev_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.PAYMENT_CREATED,
            subject_kind="payment",
            subject_id=payment.id,
            payload={
                "payment_number": payment.payment_number,
                "amount_cents": int(payment.amount_cents),
                "method": payment.method,
            },
        )

    _send_payment_received_emails(
        db,
        payment=payment,
        contact_id=contact_id,
        invoice_ids=seen_invoice_ids,
    )
    return payment


def _send_payment_received_emails(
    db: Session,
    *,
    payment: Payment,
    contact_id: int,
    invoice_ids: set[int],
) -> None:
    """Notify each unique invoice owner that a payment landed against
    their invoice. A single payment can be allocated across multiple
    invoices owned by different staff; we dedupe owners so each one
    receives at most one email per payment. Owner resolution prefers
    ``invoices.sold_by_user_id``, falling back to ``created_by_user_id``.

    Unallocated payments (no allocations) fire nothing here — they're a
    rare bookkeeping case better surfaced through the admin daily digest.
    """
    if not invoice_ids:
        return
    contact = db.get(Contact, contact_id)
    customer_name = (contact.display_name if contact else None) or "Customer"

    owners_by_id: dict[int, User] = {}
    invoice_label_for_owner: dict[int, str | None] = {}
    invoices = (
        db.query(Invoice).filter(Invoice.id.in_(invoice_ids)).all()
    )
    for inv in invoices:
        owner_id = inv.sold_by_user_id or inv.created_by_user_id
        if owner_id is None or owner_id in owners_by_id:
            continue
        owner = db.get(User, owner_id)
        if owner is None or not owner.email or not owner.is_active:
            continue
        owners_by_id[owner_id] = owner
        # First invoice this owner appears on becomes the labeled one.
        # For multi-invoice payments where the same owner owns several,
        # one label is more informative than "(multiple)".
        invoice_label_for_owner[owner_id] = inv.invoice_number

    if not owners_by_id:
        return

    from config.settings import ADMIN_BASE_URL
    from services import notification_templates

    for owner_id, owner in owners_by_id.items():
        rendered = notification_templates.render_staff_payment_received(
            staff_user=owner,
            payment_amount_cents=int(payment.amount_cents or 0),
            payment_method=payment.method,
            customer_name=customer_name,
            invoice_number=invoice_label_for_owner.get(owner_id),
            payment_number=payment.payment_number,
            received_at=datetime.now(timezone.utc),
            admin_url=f"{ADMIN_BASE_URL}/payments/{payment.id}",
        )
        send_rendered_safely(
            to=owner.email,
            rendered=rendered,
            scope="payment.received",
        )


def apply_unapplied(
    db: Session,
    *,
    payment_id: int,
    invoice_id: int,
    applied_cents: int,
    actor_user_id: int | None = None,
) -> Payment:
    """Move funds from the unapplied pool to a specific invoice. Used
    when a customer overpaid earlier and now staff want to credit it
    against a new bill."""
    payment = _get_payment_or_raise(db, payment_id)
    if payment.status not in ("completed", "partially_refunded"):
        raise PaymentServiceError(
            f"cannot apply funds on payment in status {payment.status}",
            code="invalid_payment_state",
        )
    if applied_cents <= 0:
        raise PaymentServiceError(
            "applied amount must be positive", code="invalid_allocation"
        )
    if applied_cents > int(payment.unapplied_cents or 0):
        raise PaymentServiceError(
            f"applied {applied_cents} > unapplied pool {payment.unapplied_cents}",
            code="exceeds_unapplied",
            applied_cents=applied_cents,
            unapplied_cents=int(payment.unapplied_cents or 0),
        )

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise PaymentServiceError("invoice not found", code="invoice_not_found")
    if inv.deleted_at is not None or inv.status in ("draft", "cancelled", "reversed"):
        raise PaymentServiceError(
            "invoice is not eligible for an allocation",
            code="invalid_allocation_target",
        )
    new_paid = int(inv.paid_to_date_cents or 0) + applied_cents
    if new_paid > int(inv.total_cents or 0):
        raise PaymentServiceError(
            "would exceed invoice total",
            code="invoice_overallocation",
            invoice_id=invoice_id,
            requested_paid_cents=new_paid,
            total_cents=int(inv.total_cents or 0),
        )

    # UNIQUE (payment, invoice) means at most one row exists; bump it if
    # so, otherwise create a fresh allocation.
    existing = db.execute(
        sql_text(
            "SELECT id, applied_cents FROM payment_allocations "
            "WHERE payment_id = :p AND invoice_id = :i"
        ),
        {"p": payment_id, "i": invoice_id},
    ).first()
    if existing is not None:
        db.execute(
            sql_text(
                "UPDATE payment_allocations "
                "SET applied_cents = applied_cents + :a, updated_at = NOW() "
                "WHERE id = :id"
            ),
            {"a": applied_cents, "id": existing.id},
        )
    else:
        db.add(
            PaymentAllocation(
                payment_id=payment_id,
                invoice_id=invoice_id,
                applied_cents=int(applied_cents),
            )
        )
    db.flush()

    _recompute_payment_totals(db, payment)
    _recompute_invoice_totals(db, invoice_id)
    for ev_id in _event_ids_for_invoices(db, [invoice_id]):
        activity_log.log_activity(
            db,
            event_id=ev_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.PAYMENT_APPLIED,
            subject_kind="payment",
            subject_id=payment.id,
            payload={
                "payment_number": payment.payment_number,
                "invoice_id": invoice_id,
                "applied_cents": int(applied_cents),
            },
        )
    return payment


def unapply_allocation(
    db: Session,
    *,
    allocation_id: int,
    actor_user_id: int | None = None,
) -> Payment:
    """Remove an allocation that hasn't been refunded yet. Useful when
    staff allocated to the wrong invoice. The freed funds return to the
    payment's unapplied pool."""
    alloc = db.get(PaymentAllocation, allocation_id)
    if alloc is None:
        raise PaymentServiceError(
            "allocation not found", code="allocation_not_found"
        )
    if int(alloc.refunded_cents or 0) > 0:
        raise PaymentServiceError(
            "cannot unapply an allocation that has been refunded; "
            "the refund history would be lost",
            code="allocation_partially_refunded",
        )
    payment_id = alloc.payment_id
    invoice_id = alloc.invoice_id
    payment = _get_payment_or_raise(db, payment_id)

    db.execute(
        sql_text("DELETE FROM payment_allocations WHERE id = :id"),
        {"id": allocation_id},
    )
    db.flush()

    _recompute_payment_totals(db, payment)
    _recompute_invoice_totals(db, invoice_id)
    for ev_id in _event_ids_for_invoices(db, [invoice_id]):
        activity_log.log_activity(
            db,
            event_id=ev_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.PAYMENT_UNAPPLIED,
            subject_kind="payment",
            subject_id=payment.id,
            payload={
                "payment_number": payment.payment_number,
                "invoice_id": invoice_id,
            },
        )
    return payment


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


def record_refund(
    db: Session,
    *,
    payment_id: int,
    amount_cents: int,
    refund_method: str,
    refund_reference: str | None = None,
    notes: str | None = None,
    allocation_refunds: list[AllocationRefundInput] | None = None,
    from_unapplied_cents: int = 0,
    actor_user_id: int | None = None,
) -> RefundEvent:
    """Refund money from a payment. The amount splits into two slices:

    - `from_unapplied_cents` comes out of the payment's unapplied pool.
    - `allocation_refunds` claws back per-allocation slices.

    The two must sum exactly to `amount_cents`. The split is required
    because refunding from the unapplied pool does not change any
    invoice's paid_to_date, while refunding from an allocation does.
    """
    payment = _get_payment_or_raise(db, payment_id)
    if payment.status not in ("completed", "partially_refunded"):
        raise PaymentServiceError(
            f"cannot refund payment in status {payment.status}",
            code="invalid_payment_state",
        )
    if amount_cents <= 0:
        raise PaymentServiceError("amount must be positive", code="invalid_amount")
    if refund_method not in _PAYMENT_METHODS:
        raise PaymentServiceError(
            f"unknown refund method: {refund_method}", code="invalid_method"
        )

    refundable = int(payment.amount_cents or 0) - int(payment.refunded_cents or 0)
    if amount_cents > refundable:
        raise PaymentServiceError(
            f"requested refund {amount_cents} exceeds remaining {refundable}",
            code="refund_exceeds_remaining",
            amount_cents=amount_cents,
            refundable_cents=refundable,
        )

    allocation_refunds = list(allocation_refunds or [])
    from_unapplied_cents = int(from_unapplied_cents)
    if from_unapplied_cents < 0:
        raise PaymentServiceError(
            "from_unapplied_cents must be nonneg",
            code="invalid_amount",
        )
    if from_unapplied_cents > int(payment.unapplied_cents or 0):
        raise PaymentServiceError(
            f"from_unapplied {from_unapplied_cents} exceeds pool "
            f"{payment.unapplied_cents}",
            code="refund_unapplied_exceeds_pool",
        )

    alloc_sum = sum(int(ar.refund_cents) for ar in allocation_refunds)
    if alloc_sum + from_unapplied_cents != amount_cents:
        raise PaymentServiceError(
            f"refund split must sum to amount: "
            f"alloc_sum={alloc_sum} + unapplied={from_unapplied_cents} "
            f"!= amount={amount_cents}",
            code="refund_split_mismatch",
        )

    # Each allocation_refund must reference a live allocation on this
    # payment, and may not exceed the per-allocation remaining
    # (applied - already_refunded).
    seen_alloc_ids: set[int] = set()
    affected_invoice_ids: set[int] = set()
    for ar in allocation_refunds:
        if ar.refund_cents <= 0:
            raise PaymentServiceError(
                "refund_cents must be positive",
                code="invalid_amount",
                allocation_id=ar.allocation_id,
            )
        if ar.allocation_id in seen_alloc_ids:
            raise PaymentServiceError(
                "duplicate allocation in refund",
                code="duplicate_allocation_refund",
                allocation_id=ar.allocation_id,
            )
        seen_alloc_ids.add(ar.allocation_id)
        alloc = db.get(PaymentAllocation, ar.allocation_id)
        if alloc is None or alloc.payment_id != payment_id:
            raise PaymentServiceError(
                f"allocation {ar.allocation_id} not on this payment",
                code="allocation_not_on_payment",
                allocation_id=ar.allocation_id,
            )
        alloc_remaining = int(alloc.applied_cents or 0) - int(alloc.refunded_cents or 0)
        if ar.refund_cents > alloc_remaining:
            raise PaymentServiceError(
                f"refund {ar.refund_cents} exceeds allocation remaining "
                f"{alloc_remaining}",
                code="refund_exceeds_allocation_remaining",
                allocation_id=ar.allocation_id,
                refund_cents=ar.refund_cents,
                allocation_remaining_cents=alloc_remaining,
            )
        affected_invoice_ids.add(alloc.invoice_id)

    # Compute the FULL post-refund state Python-side BEFORE any DB
    # write. Otherwise an auto-flush between mutating refunded_cents and
    # mutating applied_cents would leave the row inconsistent and trip
    # chk_payment_amount_consistent. Building the new state up front lets
    # us write all four payment columns in one shot.
    refund_by_alloc: dict[int, int] = {
        ar.allocation_id: int(ar.refund_cents) for ar in allocation_refunds
    }
    all_allocs = (
        db.query(PaymentAllocation)
        .filter(PaymentAllocation.payment_id == payment_id)
        .all()
    )
    new_applied = 0
    for a in all_allocs:
        bumped = refund_by_alloc.get(a.id, 0)
        new_applied += int(a.applied_cents) - int(a.refunded_cents or 0) - bumped

    new_refunded = int(payment.refunded_cents or 0) + amount_cents
    new_unapplied = int(payment.amount_cents) - new_applied - new_refunded
    # Defense in depth — derives the same invariant the CHECK enforces.
    assert new_unapplied >= 0, (
        f"computed unapplied negative: amount={payment.amount_cents} "
        f"applied={new_applied} refunded={new_refunded}"
    )

    # Now apply: alloc updates first, then the consistent payments row.
    for ar in allocation_refunds:
        db.execute(
            sql_text(
                "UPDATE payment_allocations "
                "SET refunded_cents = refunded_cents + :r, updated_at = NOW() "
                "WHERE id = :id"
            ),
            {"r": int(ar.refund_cents), "id": ar.allocation_id},
        )

    payment.applied_cents = new_applied
    payment.refunded_cents = new_refunded
    payment.unapplied_cents = new_unapplied
    if payment.status not in ("pending", "failed", "cancelled"):
        if new_refunded == 0:
            payment.status = "completed"
        elif new_refunded < int(payment.amount_cents):
            payment.status = "partially_refunded"
        else:
            payment.status = "refunded"
    payment.updated_at = datetime.now(timezone.utc)

    refund_event = RefundEvent(
        payment_id=payment_id,
        amount_cents=int(amount_cents),
        from_unapplied_cents=from_unapplied_cents,
        from_allocations_json=[
            {
                "allocation_id": ar.allocation_id,
                "refund_cents": int(ar.refund_cents),
            }
            for ar in allocation_refunds
        ],
        refund_method=refund_method,
        refund_reference=refund_reference,
        notes=notes,
        actor_user_id=actor_user_id,
    )
    db.add(refund_event)
    db.flush()

    for invoice_id in affected_invoice_ids:
        _recompute_invoice_totals(db, invoice_id)

    for ev_id in _event_ids_for_invoices(db, list(affected_invoice_ids)):
        activity_log.log_activity(
            db,
            event_id=ev_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.PAYMENT_REFUNDED,
            subject_kind="payment",
            subject_id=payment.id,
            payload={
                "payment_number": payment.payment_number,
                "amount_cents": int(amount_cents),
                "method": refund_method,
            },
        )
    return refund_event


def void_payment(
    db: Session,
    *,
    payment_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Payment:
    """Void a `pending` payment. Refuses any other status — `completed`
    payments must be undone via `record_refund`. Cancelled payments keep
    their number."""
    payment = _get_payment_or_raise(db, payment_id)
    if payment.status == "cancelled":
        return payment  # idempotent
    if payment.status != "pending":
        raise PaymentServiceError(
            f"cannot void payment in status {payment.status}; "
            "use record_refund for completed payments",
            code="invalid_payment_state",
        )
    payment.status = "cancelled"
    if reason:
        existing = (payment.notes or "").rstrip()
        suffix = f"[voided: {reason}]"
        payment.notes = f"{existing}\n{suffix}".lstrip() if existing else suffix
    payment.updated_at = datetime.now(timezone.utc)
    db.flush()
    # Voided payments may not have any allocations (pending state); fall
    # back to logging against any event that ever held an allocation
    # from this payment.
    alloc_invoice_ids = [
        int(r[0])
        for r in db.execute(
            sql_text(
                "SELECT DISTINCT invoice_id FROM payment_allocations "
                "WHERE payment_id = :p"
            ),
            {"p": payment_id},
        ).all()
    ]
    for ev_id in _event_ids_for_invoices(db, alloc_invoice_ids):
        activity_log.log_activity(
            db,
            event_id=ev_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.PAYMENT_VOIDED,
            subject_kind="payment",
            subject_id=payment.id,
            payload={
                "payment_number": payment.payment_number,
                "reason": reason,
            },
        )
    return payment


def soft_delete_payment(
    db: Session,
    *,
    payment_id: int,
    actor_user_id: int | None = None,
) -> None:
    """Soft-delete a cancelled or pending payment. Refuses anything that
    has touched an invoice — those need a refund first to keep the AR
    audit trail intact."""
    payment = _get_payment_or_raise(db, payment_id)
    if payment.status not in ("cancelled", "pending", "failed"):
        raise PaymentServiceError(
            f"cannot delete payment in status {payment.status}; "
            "refund any allocations first",
            code="payment_not_deletable",
        )
    if int(payment.applied_cents or 0) > 0 or int(payment.refunded_cents or 0) > 0:
        raise PaymentServiceError(
            "payment has allocations or refunds; cannot delete",
            code="payment_not_deletable",
        )
    payment.deleted_at = datetime.now(timezone.utc)
    db.flush()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@dataclass
class AllocationView:
    id: int
    invoice_id: int
    invoice_number: str | None
    applied_cents: int
    refunded_cents: int


@dataclass
class RefundEventView:
    id: int
    amount_cents: int
    from_unapplied_cents: int
    from_allocations: list[dict]
    refund_method: str
    refund_reference: str | None
    notes: str | None
    actor_user_id: int | None
    created_at: datetime


@dataclass
class PaymentDetail:
    id: int
    contact_id: int
    payment_number: str | None
    amount_cents: int
    applied_cents: int
    unapplied_cents: int
    refunded_cents: int
    payment_date: date
    method: str
    transaction_reference: str | None
    status: str
    notes: str | None
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    allocations: list[AllocationView] = field(default_factory=list)
    refund_events: list[RefundEventView] = field(default_factory=list)


@dataclass
class PaymentSummary:
    id: int
    contact_id: int
    contact_name: str
    payment_number: str | None
    amount_cents: int
    applied_cents: int
    unapplied_cents: int
    refunded_cents: int
    payment_date: date
    method: str
    status: str
    created_at: datetime


def get_payment_detail(db: Session, payment_id: int) -> PaymentDetail:
    payment = _get_payment_or_raise(db, payment_id)
    alloc_rows = db.execute(
        sql_text(
            "SELECT pa.id, pa.invoice_id, pa.applied_cents, pa.refunded_cents, "
            "       i.invoice_number "
            "FROM payment_allocations pa "
            "JOIN invoices i ON i.id = pa.invoice_id "
            "WHERE pa.payment_id = :p "
            "ORDER BY pa.id ASC"
        ),
        {"p": payment_id},
    ).all()
    refund_rows = db.execute(
        sql_text(
            "SELECT id, amount_cents, from_unapplied_cents, from_allocations_json, "
            "       refund_method, refund_reference, notes, actor_user_id, created_at "
            "FROM refund_events WHERE payment_id = :p ORDER BY created_at DESC, id DESC"
        ),
        {"p": payment_id},
    ).all()
    return PaymentDetail(
        id=payment.id,
        contact_id=payment.contact_id,
        payment_number=payment.payment_number,
        amount_cents=int(payment.amount_cents),
        applied_cents=int(payment.applied_cents or 0),
        unapplied_cents=int(payment.unapplied_cents or 0),
        refunded_cents=int(payment.refunded_cents or 0),
        payment_date=payment.payment_date,
        method=payment.method,
        transaction_reference=payment.transaction_reference,
        status=payment.status,
        notes=payment.notes,
        created_by_user_id=payment.created_by_user_id,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        deleted_at=payment.deleted_at,
        allocations=[
            AllocationView(
                id=r.id,
                invoice_id=r.invoice_id,
                invoice_number=r.invoice_number,
                applied_cents=int(r.applied_cents),
                refunded_cents=int(r.refunded_cents or 0),
            )
            for r in alloc_rows
        ],
        refund_events=[
            RefundEventView(
                id=r.id,
                amount_cents=int(r.amount_cents),
                from_unapplied_cents=int(r.from_unapplied_cents or 0),
                from_allocations=list(r.from_allocations_json or []),
                refund_method=r.refund_method,
                refund_reference=r.refund_reference,
                notes=r.notes,
                actor_user_id=r.actor_user_id,
                created_at=r.created_at,
            )
            for r in refund_rows
        ],
    )


def list_payments_for_contact(
    db: Session,
    *,
    contact_id: int,
    include_deleted: bool = False,
) -> list[PaymentSummary]:
    q = sql_text(
        "SELECT p.id, p.contact_id, c.display_name AS contact_name, "
        "       p.payment_number, p.amount_cents, p.applied_cents, "
        "       p.unapplied_cents, p.refunded_cents, p.payment_date, "
        "       p.method, p.status, p.created_at, p.deleted_at "
        "FROM payments p "
        "JOIN contacts c ON c.id = p.contact_id "
        "WHERE p.contact_id = :c "
        "ORDER BY p.payment_date DESC, p.id DESC"
    )
    rows = db.execute(q, {"c": contact_id}).all()
    return [_to_summary(r) for r in rows if include_deleted or r.deleted_at is None]


def list_payments_for_invoice(
    db: Session, *, invoice_id: int
) -> list[PaymentSummary]:
    """Lists every payment that has at least one allocation against this
    invoice. The Invoice editor uses this to render the per-invoice
    Payments sub-section."""
    rows = db.execute(
        sql_text(
            "SELECT DISTINCT p.id, p.contact_id, c.display_name AS contact_name, "
            "       p.payment_number, p.amount_cents, p.applied_cents, "
            "       p.unapplied_cents, p.refunded_cents, p.payment_date, "
            "       p.method, p.status, p.created_at, p.deleted_at "
            "FROM payments p "
            "JOIN contacts c ON c.id = p.contact_id "
            "JOIN payment_allocations pa ON pa.payment_id = p.id "
            "WHERE pa.invoice_id = :i AND p.deleted_at IS NULL "
            "ORDER BY p.payment_date DESC, p.id DESC"
        ),
        {"i": invoice_id},
    ).all()
    return [_to_summary(r) for r in rows]


def list_payments_for_event(
    db: Session, *, event_id: int
) -> list[PaymentSummary]:
    """Every payment for the event's primary contact (Phase 6 v1: payments
    are per-contact). The Payments tab uses this."""
    rows = db.execute(
        sql_text(
            "SELECT p.id, p.contact_id, c.display_name AS contact_name, "
            "       p.payment_number, p.amount_cents, p.applied_cents, "
            "       p.unapplied_cents, p.refunded_cents, p.payment_date, "
            "       p.method, p.status, p.created_at, p.deleted_at "
            "FROM payments p "
            "JOIN contacts c ON c.id = p.contact_id "
            "JOIN events e ON e.primary_contact_id = c.id "
            "WHERE e.id = :e AND p.deleted_at IS NULL "
            "ORDER BY p.payment_date DESC, p.id DESC"
        ),
        {"e": event_id},
    ).all()
    return [_to_summary(r) for r in rows]


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------


def _recompute_payment_totals(
    db: Session,
    payment: Payment,
    *,
    recompute_unapplied: bool = True,
) -> None:
    """`applied_cents = SUM(allocations.applied - allocations.refunded)`.
    If `recompute_unapplied`, derive `unapplied_cents` from the invariant
    `amount - applied - refunded` (used by the apply/unapply paths). On
    the refund path, the caller maintains unapplied directly because the
    from_unapplied_cents slice can't be derived from allocation rows
    alone."""
    row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(applied_cents - refunded_cents), 0) "
            "FROM payment_allocations WHERE payment_id = :p"
        ),
        {"p": payment.id},
    ).scalar()
    new_applied = int(row or 0)
    payment.applied_cents = new_applied
    if recompute_unapplied:
        payment.unapplied_cents = (
            int(payment.amount_cents)
            - int(payment.refunded_cents or 0)
            - new_applied
        )

    # Derived status only for completed/refund states. Explicit
    # pending/failed/cancelled are sticky — staff set them and the
    # service does not auto-flip.
    if payment.status not in ("pending", "failed", "cancelled"):
        refunded = int(payment.refunded_cents or 0)
        amount = int(payment.amount_cents)
        if refunded == 0:
            payment.status = "completed"
        elif refunded < amount:
            payment.status = "partially_refunded"
        else:
            payment.status = "refunded"

    payment.updated_at = datetime.now(timezone.utc)


def _recompute_invoice_totals(db: Session, invoice_id: int) -> None:
    """Pull invoice money columns back in sync with the
    payment_allocations against it. Status transitions:
    - paid_to_date == 0 → 'sent' (only when prior was 'partial' or 'paid'
      after a refund pulled it to zero — drafts and cancelled are sticky)
    - 0 < paid_to_date < total → 'partial', clear paid_at
    - paid_to_date >= total → 'paid', stamp paid_at if not set
    Cancelled / reversed / draft never auto-derive."""
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        return

    row = db.execute(
        sql_text(
            "SELECT COALESCE(SUM(pa.applied_cents - pa.refunded_cents), 0) "
            "FROM payment_allocations pa "
            "JOIN payments p ON p.id = pa.payment_id "
            "WHERE pa.invoice_id = :i "
            "  AND p.deleted_at IS NULL "
            "  AND p.status NOT IN ('cancelled', 'failed') "
        ),
        {"i": invoice_id},
    ).scalar()
    new_paid = int(row or 0)

    inv.paid_to_date_cents = new_paid
    inv.balance_cents = int(inv.total_cents or 0) - new_paid

    # Status transitions only on the live payment-bearing states.
    if inv.status not in ("draft", "cancelled", "reversed"):
        total = int(inv.total_cents or 0)
        now = datetime.now(timezone.utc)
        if total > 0 and new_paid >= total:
            if inv.status != "paid":
                inv.status = "paid"
            if inv.paid_at is None:
                inv.paid_at = now
        elif new_paid > 0:
            inv.status = "partial"
            inv.paid_at = None
        else:
            # Refund pulled paid_to_date back to zero. Flip back to
            # 'sent' (the only pre-payment live status that allocations
            # could have come from). 'draft' invoices can't have
            # allocations, so we never end up here from a draft.
            inv.status = "sent"
            inv.paid_at = None
        inv.updated_at = datetime.now(timezone.utc)

    # Walk installments by due_date; stamp/unstamp paid_at idempotently
    # so the reminder cron in Phase 11 has accurate per-row state.
    insts = db.execute(
        sql_text(
            "SELECT id, amount_cents, paid_at FROM invoice_installments "
            "WHERE invoice_id = :i "
            "ORDER BY due_date ASC, sort_order ASC, id ASC"
        ),
        {"i": invoice_id},
    ).all()
    cumulative = 0
    now = datetime.now(timezone.utc)
    for ins in insts:
        cumulative += int(ins.amount_cents)
        currently_paid = ins.paid_at is not None
        should_be_paid = cumulative <= new_paid and new_paid > 0
        if should_be_paid and not currently_paid:
            db.execute(
                sql_text(
                    "UPDATE invoice_installments "
                    "SET paid_at = :now, updated_at = :now WHERE id = :id"
                ),
                {"now": now, "id": ins.id},
            )
        elif not should_be_paid and currently_paid:
            db.execute(
                sql_text(
                    "UPDATE invoice_installments "
                    "SET paid_at = NULL, updated_at = :now WHERE id = :id"
                ),
                {"now": now, "id": ins.id},
            )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_payment_or_raise(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or payment.deleted_at is not None:
        raise PaymentServiceError("payment not found", code="payment_not_found")
    return payment


def _assign_payment_number(db: Session) -> str:
    """Allocate `PMT-YYYY-NNNNNN` under a row-level lock on
    `numbering_state`. Year rollover resets the sequence to 1."""
    row = db.execute(
        sql_text(
            "SELECT payment_year, payment_seq FROM numbering_state "
            "WHERE id = 1 FOR UPDATE"
        )
    ).one()
    current_year = datetime.now(timezone.utc).year
    if int(row.payment_year) != current_year:
        new_year, new_seq = current_year, 1
    else:
        new_year, new_seq = int(row.payment_year), int(row.payment_seq) + 1
    db.execute(
        sql_text(
            "UPDATE numbering_state SET payment_year = :y, payment_seq = :s, "
            "updated_at = NOW() WHERE id = 1"
        ),
        {"y": new_year, "s": new_seq},
    )
    return f"PMT-{new_year}-{new_seq:06d}"


def _to_summary(row) -> PaymentSummary:
    return PaymentSummary(
        id=row.id,
        contact_id=row.contact_id,
        contact_name=row.contact_name,
        payment_number=row.payment_number,
        amount_cents=int(row.amount_cents),
        applied_cents=int(row.applied_cents or 0),
        unapplied_cents=int(row.unapplied_cents or 0),
        refunded_cents=int(row.refunded_cents or 0),
        payment_date=row.payment_date,
        method=row.method,
        status=row.status,
        created_at=row.created_at,
    )
