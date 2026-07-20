"""Special-order domain service.

Phase 5 of the catalog SKU obfuscation plan. Owns the lifecycle for
"where is my dress?" tracking against catalog-backed invoice lines.

Status flow (validated in :func:`_assert_transition`):

    needed ──> ordered ──> received ──> picked_up
                  │           ▲
                  └──> delayed┘
                              │
                  cancelled <─┴── (any non-terminal state)

Rules:

  - ``needed`` is the initial state when staff create a special order
    before the vendor has been contacted. The Phase 5 plan defaults
    new rows to ``needed`` so the same row can move through ordered →
    received → picked_up without needing to reuse a separate "draft"
    row.
  - ``picked_up`` is terminal; the dress is in the customer's hands.
  - ``cancelled`` is terminal in either direction (vendor stockout,
    customer change of heart). The service rejects re-opening a
    cancelled row to keep the audit trail clean — staff create a new
    row instead.
  - ``delayed`` is a side-state of ``ordered`` for "we ordered it but
    the vendor pushed the ETA"; it shares ``ordered_at`` with
    ``ordered`` and can flip back to ``ordered`` (ETA confirmed) or
    forward to ``received``.

Vendor and internal-notes fields stay staff-only by construction:
nothing in this module returns them via a public-facing helper, and
Phase 5's router never exposes them through a customer route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from database.models import (
    CatalogItem,
    Event,
    InvoiceLineItem,
    SpecialOrder,
)


class SpecialOrderError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "special_order_error",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# Statuses the lifecycle accepts. Source of truth lives in the
# migration's CHECK constraint; this set keeps the service in sync.
_STATUSES = frozenset(
    {"needed", "ordered", "delayed", "received", "picked_up", "cancelled"}
)
_TERMINAL = frozenset({"picked_up", "cancelled"})

# Allowed transitions. The boundary cases are deliberate:
#   - delayed ↔ ordered swap is fine (ETA changes, vendor reconfirms).
#   - any non-terminal state can flip to cancelled.
#   - received → ordered is forbidden because that would erase the
#     received_at stamp and make the timeline lie. If a return-to-
#     vendor case shows up, model it explicitly later.
_ALLOWED: dict[str, frozenset[str]] = {
    "needed": frozenset({"ordered", "cancelled"}),
    "ordered": frozenset({"delayed", "received", "cancelled"}),
    "delayed": frozenset({"ordered", "received", "cancelled"}),
    "received": frozenset({"picked_up", "cancelled"}),
    "picked_up": frozenset(),
    "cancelled": frozenset(),
}


# ---------------------------------------------------------------------------
# Inputs / views
# ---------------------------------------------------------------------------


@dataclass
class CreateSpecialOrderInput:
    """Service-level input for ``create_special_order``.

    A special order is always anchored to a catalog row + size; the
    invoice line is the typical entry point but optional so staff can
    log a row before the customer's invoice exists (a deposit-only
    flow, an inherited backorder).
    """

    event_id: int
    catalog_item_id: int
    size_label: str
    invoice_line_item_id: int | None = None
    status: str = "needed"
    ordered_at: datetime | None = None
    eta_date: date | None = None
    vendor_order_number: str | None = None
    internal_notes: str | None = None


@dataclass
class CatalogSnapshot:
    """Staff-only catalog identifiers attached to a special order
    view. The router returns this on staff endpoints. Customer
    surfaces never see this dataclass."""

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
class SpecialOrderView:
    id: int
    event_id: int
    invoice_line_item_id: int | None
    catalog_item_id: int
    size_label: str
    status: str
    ordered_at: datetime | None
    eta_date: date | None
    received_at: datetime | None
    picked_up_at: datetime | None
    vendor_order_number: str | None
    internal_notes: str | None
    created_at: datetime
    updated_at: datetime
    catalog: CatalogSnapshot | None = None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_special_order(
    db: Session,
    data: CreateSpecialOrderInput,
) -> SpecialOrder:
    """Create one ``special_orders`` row.

    Validation:
      - event must exist (FK is RESTRICT but we surface a friendlier
        404 first).
      - catalog row must exist and be active (an inactive row should
        not be ordered fresh; staff can reactivate it from the admin
        catalog screen if needed).
      - if ``invoice_line_item_id`` is supplied, the line must
        already point at the same catalog item; otherwise the special
        order would silently disagree with the invoice it claims to
        track.
      - status must satisfy the same CHECK constraints the DB enforces
        (status ↔ timestamp coupling). The service raises before
        flushing so the caller sees a domain code instead of a raw
        IntegrityError.
    """
    if data.status not in _STATUSES:
        raise SpecialOrderError(
            f"unknown status {data.status!r}",
            code="invalid_status",
        )
    if not data.size_label or not data.size_label.strip():
        raise SpecialOrderError(
            "size_label is required",
            code="size_label_required",
        )
    event = db.get(Event, data.event_id)
    if event is None or event.deleted_at is not None:
        raise SpecialOrderError(
            f"event {data.event_id} not found",
            code="event_not_found",
        )
    catalog = db.get(CatalogItem, data.catalog_item_id)
    if catalog is None:
        raise SpecialOrderError(
            f"catalog item {data.catalog_item_id} not found",
            code="catalog_item_not_found",
        )
    if not catalog.active:
        raise SpecialOrderError(
            "catalog item is inactive",
            code="catalog_item_inactive",
            catalog_item_id=int(data.catalog_item_id),
        )
    if data.invoice_line_item_id is not None:
        line = db.get(InvoiceLineItem, data.invoice_line_item_id)
        if line is None:
            raise SpecialOrderError(
                f"invoice line {data.invoice_line_item_id} not found",
                code="invoice_line_not_found",
            )
        if line.catalog_item_id != data.catalog_item_id:
            raise SpecialOrderError(
                "invoice line does not reference the same catalog item",
                code="invoice_line_catalog_mismatch",
                invoice_line_item_id=int(data.invoice_line_item_id),
                line_catalog_item_id=line.catalog_item_id,
                requested_catalog_item_id=int(data.catalog_item_id),
            )
        # Event consistency: the linked invoice line must belong to
        # the same event the special order is being filed under.
        # Without this, a staff caller (or a misdirected API client)
        # could attach an invoice line from event A to a special-order
        # row under event B and the cross-event link would silently
        # ship. The codebase uses raw FK-id columns + db.get() rather
        # than ORM relationships, so resolve the invoice's event_id
        # explicitly.
        from database.models import Invoice

        invoice = db.get(Invoice, line.invoice_id)
        line_event_id = invoice.event_id if invoice is not None else None
        if line_event_id is None:
            raise SpecialOrderError(
                "invoice line has no event link",
                code="invoice_line_event_missing",
            )
        if int(line_event_id) != int(data.event_id):
            raise SpecialOrderError(
                "invoice line belongs to a different event",
                code="invoice_line_event_mismatch",
                invoice_line_item_id=int(data.invoice_line_item_id),
                line_event_id=int(line_event_id),
                requested_event_id=int(data.event_id),
            )
        # Size consistency: the special order's size_label must match
        # the invoice line's size_label. Mismatched sizes here would
        # let staff order an 08 against an invoice line that promised
        # the customer a 10 — the line and the order would silently
        # disagree and "where is my dress?" would answer with the
        # wrong size.
        if line.size_label and (
            (data.size_label or "").strip() != line.size_label.strip()
        ):
            raise SpecialOrderError(
                "size_label does not match the invoice line's size",
                code="invoice_line_size_mismatch",
                invoice_line_item_id=int(data.invoice_line_item_id),
                line_size_label=line.size_label,
                requested_size_label=data.size_label,
            )

    # Status ↔ timestamp coupling. The DB checks the same rules; we
    # raise here so the API returns a friendly code instead of a
    # generic chk_special_orders_* violation.
    ordered_at = data.ordered_at
    if data.status in {"ordered", "delayed"} and ordered_at is None:
        ordered_at = datetime.now(timezone.utc)
    if data.status in {"received", "picked_up"}:
        # Initial-create of received/picked_up is allowed (a manual
        # backfill of an already-fulfilled row). Caller must supply
        # the timestamps; we don't auto-fill those because the
        # historical accuracy matters.
        raise SpecialOrderError(
            "create with status received or picked_up requires "
            "transitioning through ordered first",
            code="invalid_initial_status",
        )

    row = SpecialOrder(
        event_id=data.event_id,
        invoice_line_item_id=data.invoice_line_item_id,
        catalog_item_id=data.catalog_item_id,
        size_label=data.size_label.strip(),
        status=data.status,
        ordered_at=ordered_at,
        eta_date=data.eta_date,
        vendor_order_number=data.vendor_order_number,
        internal_notes=data.internal_notes,
    )
    db.add(row)
    db.flush()
    return row


def create_from_invoice_line(
    db: Session,
    *,
    invoice_line_item_id: int,
    status: str = "needed",
    eta_date: date | None = None,
    vendor_order_number: str | None = None,
    internal_notes: str | None = None,
) -> SpecialOrder:
    """Convenience entry point used by the invoice editor's "mark as
    order-needed" action. Looks up the invoice line, copies its
    catalog snapshot + size_label, and creates the special order.
    Raises if the line is not catalog-backed — non-catalog lines
    (alterations, fees) cannot be ordered through this flow.
    """
    line = db.get(InvoiceLineItem, invoice_line_item_id)
    if line is None:
        raise SpecialOrderError(
            f"invoice line {invoice_line_item_id} not found",
            code="invoice_line_not_found",
        )
    if line.catalog_item_id is None:
        raise SpecialOrderError(
            "non-catalog invoice line cannot start a special order",
            code="invoice_line_not_catalog_backed",
            invoice_line_item_id=int(invoice_line_item_id),
        )
    if not line.size_label:
        raise SpecialOrderError(
            "invoice line has no size_label; set the size before "
            "starting a special order",
            code="invoice_line_size_required",
            invoice_line_item_id=int(invoice_line_item_id),
        )
    # The codebase uses raw FK columns + db.get() instead of ORM
    # relationships, so resolve event_id with one extra fetch.
    from database.models import Invoice

    invoice = db.get(Invoice, line.invoice_id)
    event_id = invoice.event_id if invoice is not None else None
    if event_id is None:
        raise SpecialOrderError(
            "invoice line has no event link",
            code="invoice_line_event_missing",
        )
    return create_special_order(
        db,
        CreateSpecialOrderInput(
            event_id=int(event_id),
            catalog_item_id=int(line.catalog_item_id),
            size_label=line.size_label,
            invoice_line_item_id=int(line.id),
            status=status,
            eta_date=eta_date,
            vendor_order_number=vendor_order_number,
            internal_notes=internal_notes,
        ),
    )


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _assert_transition(current: str, target: str) -> None:
    if target not in _STATUSES:
        raise SpecialOrderError(
            f"unknown status {target!r}",
            code="invalid_status",
        )
    if current == target:
        # Idempotency lives at the action verbs (mark_ordered etc.);
        # callers reaching this guard with current==target are doing
        # something unusual and we let them know.
        return
    if target not in _ALLOWED.get(current, frozenset()):
        raise SpecialOrderError(
            f"cannot transition {current!r} → {target!r}",
            code="invalid_transition",
            current=current,
            target=target,
        )


def _get_or_raise(db: Session, special_order_id: int) -> SpecialOrder:
    row = db.get(SpecialOrder, special_order_id)
    if row is None or row.deleted_at is not None:
        raise SpecialOrderError(
            f"special order {special_order_id} not found",
            code="special_order_not_found",
        )
    return row


def mark_ordered(
    db: Session,
    *,
    special_order_id: int,
    eta_date: date | None = None,
    vendor_order_number: str | None = None,
    when: datetime | None = None,
) -> SpecialOrder:
    """Move a needed/delayed row to ordered. Idempotent on already-
    ordered rows (stamps update, status stays). When called from
    delayed it returns the row to ``ordered`` with the new ETA."""
    row = _get_or_raise(db, special_order_id)
    if row.status == "ordered":
        # Idempotent metadata refresh.
        if eta_date is not None:
            row.eta_date = eta_date
        if vendor_order_number is not None:
            row.vendor_order_number = vendor_order_number
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return row
    _assert_transition(row.status, "ordered")
    row.status = "ordered"
    row.ordered_at = row.ordered_at or when or datetime.now(timezone.utc)
    if eta_date is not None:
        row.eta_date = eta_date
    if vendor_order_number is not None:
        row.vendor_order_number = vendor_order_number
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def mark_delayed(
    db: Session,
    *,
    special_order_id: int,
    eta_date: date | None = None,
) -> SpecialOrder:
    """ordered → delayed when the vendor pushes the ETA. Captures the
    new ETA when supplied."""
    row = _get_or_raise(db, special_order_id)
    if row.status == "delayed":
        if eta_date is not None:
            row.eta_date = eta_date
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        return row
    _assert_transition(row.status, "delayed")
    row.status = "delayed"
    if eta_date is not None:
        row.eta_date = eta_date
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def mark_received(
    db: Session,
    *,
    special_order_id: int,
    when: datetime | None = None,
) -> SpecialOrder:
    """ordered/delayed → received. Stamps received_at if absent.
    Idempotent on already-received rows."""
    row = _get_or_raise(db, special_order_id)
    if row.status == "received":
        return row
    _assert_transition(row.status, "received")
    row.status = "received"
    row.received_at = row.received_at or when or datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def mark_picked_up(
    db: Session,
    *,
    special_order_id: int,
    when: datetime | None = None,
) -> SpecialOrder:
    """received → picked_up. Stamps picked_up_at if absent.
    Idempotent on already-picked-up rows."""
    row = _get_or_raise(db, special_order_id)
    if row.status == "picked_up":
        return row
    _assert_transition(row.status, "picked_up")
    row.status = "picked_up"
    row.picked_up_at = (
        row.picked_up_at or when or datetime.now(timezone.utc)
    )
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def mark_cancelled(
    db: Session,
    *,
    special_order_id: int,
) -> SpecialOrder:
    """Any non-terminal state → cancelled. Idempotent on already-
    cancelled rows. picked_up is terminal and cannot be cancelled."""
    row = _get_or_raise(db, special_order_id)
    if row.status == "cancelled":
        return row
    _assert_transition(row.status, "cancelled")
    row.status = "cancelled"
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Patch (metadata-only)
# ---------------------------------------------------------------------------


_PATCHABLE_FIELDS = {
    "size_label",
    "eta_date",
    "vendor_order_number",
    "internal_notes",
}


def patch_special_order(
    db: Session,
    *,
    special_order_id: int,
    patch: dict[str, Any],
) -> SpecialOrder:
    """Update metadata that does not change lifecycle status.
    Lifecycle changes go through the ``mark_*`` verbs above so the
    timestamp side-effects stay consistent."""
    row = _get_or_raise(db, special_order_id)
    unknown = set(patch) - _PATCHABLE_FIELDS
    if unknown:
        raise SpecialOrderError(
            f"cannot patch fields: {sorted(unknown)}",
            code="unknown_fields",
        )
    if "size_label" in patch:
        new_size = (patch["size_label"] or "").strip()
        if not new_size:
            raise SpecialOrderError(
                "size_label cannot be blank",
                code="size_label_required",
            )
        row.size_label = new_size
    for f in ("eta_date", "vendor_order_number", "internal_notes"):
        if f in patch:
            setattr(row, f, patch[f])
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _to_view(
    row: SpecialOrder, catalog: CatalogItem | None = None
) -> SpecialOrderView:
    return SpecialOrderView(
        id=int(row.id),
        event_id=int(row.event_id),
        invoice_line_item_id=row.invoice_line_item_id,
        catalog_item_id=int(row.catalog_item_id),
        size_label=row.size_label,
        status=row.status,
        ordered_at=row.ordered_at,
        eta_date=row.eta_date,
        received_at=row.received_at,
        picked_up_at=row.picked_up_at,
        vendor_order_number=row.vendor_order_number,
        internal_notes=row.internal_notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
        catalog=(
            CatalogSnapshot(
                id=int(catalog.id),
                internal_sku=catalog.internal_sku,
                public_code=catalog.public_code,
                designer=catalog.designer,
                style_number=catalog.style_number,
                color=catalog.color,
                house_name=catalog.house_name,
                category=catalog.category,
                product_title=catalog.product_title,
            )
            if catalog is not None
            else None
        ),
    )


def get_special_order(db: Session, special_order_id: int) -> SpecialOrderView:
    row = _get_or_raise(db, special_order_id)
    catalog = db.get(CatalogItem, row.catalog_item_id)
    return _to_view(row, catalog)


def list_for_event(
    db: Session,
    *,
    event_id: int,
    include_terminal: bool = True,
) -> list[SpecialOrderView]:
    """Staff-side listing for the event detail screen. Newest open
    rows first; terminal rows at the bottom unless filtered out."""
    q = (
        db.query(SpecialOrder)
        .filter(SpecialOrder.event_id == event_id)
        .filter(SpecialOrder.deleted_at.is_(None))
    )
    if not include_terminal:
        q = q.filter(~SpecialOrder.status.in_(_TERMINAL))
    rows = q.order_by(
        SpecialOrder.status.in_(list(_TERMINAL)).asc(),
        desc(SpecialOrder.created_at),
    ).all()
    if not rows:
        return []
    catalog_ids = {int(r.catalog_item_id) for r in rows}
    catalog_by_id: dict[int, CatalogItem] = {}
    if catalog_ids:
        cats = (
            db.query(CatalogItem)
            .filter(CatalogItem.id.in_(catalog_ids))
            .all()
        )
        catalog_by_id = {int(c.id): c for c in cats}
    return [_to_view(r, catalog_by_id.get(int(r.catalog_item_id))) for r in rows]


# ---------------------------------------------------------------------------
# Archive / restore (D3 of docs/CRM_RECORD_DELETION_PLAN.md)
# ---------------------------------------------------------------------------


def archive_special_order(
    db: Session,
    *,
    special_order_id: int,
    actor_user_id: int | None,
    reason: str,
    note: str | None = None,
) -> SpecialOrder:
    """Soft-delete a special order. Idempotent on already-archived rows.

    The dependency report blocks archive while the order's status is
    ``'ordered'`` or ``'received'`` — staff cancel-by-status first,
    then archive. This keeps "in flight" rows out of the Recycle Bin
    where someone might restore them mid-shipment."""
    from services import activity_log, record_dependencies

    record_dependencies.validate_archive_reason(reason)

    row = db.get(SpecialOrder, special_order_id)
    if row is None:
        raise SpecialOrderError(
            f"special order {special_order_id} not found",
            code="special_order_not_found",
        )
    if row.deleted_at is not None:
        return row

    report = record_dependencies.get_record_dependencies(
        db, entity_type="special_order", entity_id=special_order_id
    )
    if not report.can_archive:
        raise SpecialOrderError(
            "archive blocked: " + "; ".join(report.block_reasons),
            code="archive_blocked",
        )

    now = datetime.now(timezone.utc)
    row.deleted_at = now
    row.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(row.event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.SPECIAL_ORDER_ARCHIVED,
        subject_kind="special_order",
        subject_id=int(special_order_id),
        payload={
            "reason": reason,
            "note": note,
            "status_at_archive": row.status,
            "size_label": row.size_label,
            "catalog_item_id": int(row.catalog_item_id),
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return row


def restore_special_order(
    db: Session,
    *,
    special_order_id: int,
    actor_user_id: int | None,
) -> SpecialOrder:
    """Lift the archive on a special order. Idempotent on live rows.

    Refuses when the parent event is archived (same orphan rule the
    pipeline already enforces on participants)."""
    from services import activity_log, record_dependencies

    row = db.get(SpecialOrder, special_order_id)
    if row is None:
        raise SpecialOrderError(
            f"special order {special_order_id} not found",
            code="special_order_not_found",
        )
    if row.deleted_at is None:
        return row

    event = db.get(Event, row.event_id)
    if event is None or event.deleted_at is not None:
        raise SpecialOrderError(
            "restore blocked: parent event is archived",
            code="parent_archived",
        )

    report = record_dependencies.get_record_dependencies(
        db, entity_type="special_order", entity_id=special_order_id
    )

    now = datetime.now(timezone.utc)
    row.deleted_at = None
    row.updated_at = now
    db.flush()

    activity_log.log_activity(
        db,
        event_id=int(row.event_id),
        actor_kind="staff" if actor_user_id else "system",
        actor_user_id=actor_user_id,
        activity_type=activity_log.SPECIAL_ORDER_RESTORED,
        subject_kind="special_order",
        subject_id=int(special_order_id),
        payload={
            "status_at_restore": row.status,
            "size_label": row.size_label,
            "catalog_item_id": int(row.catalog_item_id),
            "dependency_snapshot": record_dependencies.dependency_snapshot(
                report
            ),
        },
    )
    return row
