"""Invoices router.

Two routers because the routes split across `/api/events/{id}/invoices` and
`/api/invoices/...`. server.py mounts each at the matching prefix.

Auth: every route requires `get_current_user`. No public routes here; the
customer-facing portal lives in Phase 7 and reads `invoice_invitations`
under a different prefix.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from api.rate_limit import staff_money_rate_limit
from database.auth import require_admin_scope, require_any_scope
from services.attendance_gate import require_floor_access
from database.connection import get_db
from database.models import Invoice, User
from services import buyer_journey, invoice_pdf, invoice_service, portal_email
from services.buyer_journey import BuyerJourneyError
from services.invoice_pdf import PdfRenderError
from services.invoice_service import (
    InstallmentInput,
    InvoiceServiceError,
    LineItemInput,
)
from services.portal_email import PortalEmailError

log = logging.getLogger(__name__)

event_invoices_router = APIRouter()
invoices_router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_LineKind = Literal["product", "service", "alteration", "fee"]
_InvoiceStatus = Literal[
    "draft", "sent", "partial", "paid", "cancelled", "reversed"
]


class LineItemPayload(BaseModel):
    """Wire shape for one invoice line.

    Phase 2 catalog rules (validated server-side in invoice_service):

    - **Catalog-backed line.** ``catalog_item_id`` is set; the API
      rejects ``description``, ``notes``, and ``public_description`` on
      the same line. ``size_label`` and ``internal_notes`` are
      optional. Customer-facing copy comes from the catalog row at
      render time, not from staff text.

    - **Non-catalog line.** ``catalog_item_id`` is null. Either
      ``public_description`` or the legacy ``description`` is required;
      sending both with different values is rejected. Staff context
      goes into ``internal_notes`` (preferred) or the legacy ``notes``.

    The legacy ``description`` and ``notes`` fields stay accepted so
    the existing UI keeps working through the Phase 3 picker rollout.
    Phase 4's render swap stops the back-compat mirror that the
    service writes into the legacy columns.
    """

    description: str | None = Field(default=None, max_length=2000)
    quantity: Decimal = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    kind: _LineKind = "product"
    sort_order: int | None = None
    product_key: str | None = Field(default=None, max_length=120)
    discount_cents: int = Field(default=0, ge=0)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    tax_name: str | None = Field(default=None, max_length=40)
    notes: str | None = None
    catalog_item_id: int | None = None
    size_label: str | None = Field(default=None, max_length=40)
    public_description: str | None = Field(default=None, max_length=2000)
    internal_notes: str | None = None

    @field_validator("quantity")
    @classmethod
    def _quantity_must_be_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("quantity must be a whole number")
        return value


class InstallmentPayload(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    amount_cents: int = Field(gt=0)
    due_date: date
    sort_order: int | None = None
    staff_notes: str | None = None


class OrderDiscountPayload(BaseModel):
    """Phase 7 stacked order-level discount input.

    Either `preset_id` or `percent` must be present. When `preset_id` is
    set, the server snapshots the preset's current label and percent
    onto the row so a later rename does not rewrite history. When only
    `percent` is set, the row is a custom one-off and `label` defaults
    to "Custom" if omitted.
    """

    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=60)
    percent: Decimal | None = Field(default=None, ge=0, le=50)


class InvoiceCreate(BaseModel):
    contact_id: int
    line_items: list[LineItemPayload] = Field(default_factory=list)
    installments: list[InstallmentPayload] = Field(default_factory=list)
    discount_cents: int = Field(default=0, ge=0)
    # Phase 7: stacked order-level discounts. Each row snapshotted on
    # write; sum capped at 50% combined. Empty list = no discount.
    order_discounts: list[OrderDiscountPayload] = Field(default_factory=list)
    # Phase 5 custom-amounts escape hatch: when true, the deposit floor
    # check is skipped for this write. Per-request flag, not stored.
    custom_amounts: bool = False
    issue_date: date | None = None
    terms: str | None = None
    footer: str | None = None
    public_notes: str | None = None
    private_notes: str | None = None
    po_number: str | None = Field(default=None, max_length=64)


class InvoiceUpdate(BaseModel):
    """Partial update. Fields omitted are left unchanged. `line_items`,
    `installments`, and `order_discounts` arrays REPLACE all rows when
    present."""

    model_config = ConfigDict(extra="forbid")

    line_items: list[LineItemPayload] | None = None
    installments: list[InstallmentPayload] | None = None
    discount_cents: int | None = Field(default=None, ge=0)
    # Send a list to replace the discount stack; send `[]` to clear.
    order_discounts: list[OrderDiscountPayload] | None = None
    custom_amounts: bool | None = None
    issue_date: date | None = None
    terms: str | None = None
    footer: str | None = None
    public_notes: str | None = None
    private_notes: str | None = None
    po_number: str | None = Field(default=None, max_length=64)


class CancelPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ResendPayload(BaseModel):
    contact_ids: list[int] = Field(min_length=1)


class CatalogLineSnapshotResponse(BaseModel):
    id: int
    internal_sku: str
    public_code: str
    designer: str | None
    style_number: str | None
    color: str
    house_name: str | None
    category: str
    product_title: str | None


class LineItemResponse(BaseModel):
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
    # Catalog snapshot is staff-only — public APIs and customer documents
    # never include this, regardless of how a future contributor reuses
    # this response model. The Phase 7 lint will catch a customer-facing
    # template that pokes at .catalog.internal_sku.
    catalog: CatalogLineSnapshotResponse | None


class InstallmentResponse(BaseModel):
    id: int
    sort_order: int
    label: str
    amount_cents: int
    due_date: date
    paid_at: datetime | None
    staff_notes: str | None


class InvitationResponse(BaseModel):
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


class OrderDiscountResponse(BaseModel):
    id: int
    sort_order: int
    preset_id: str | None
    label: str
    percent: Decimal


class InvoiceDetailResponse(BaseModel):
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
    order_discounts: list[OrderDiscountResponse]
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
    source_quote_id: int | None = None
    source_quote_number: str | None = None
    line_items: list[LineItemResponse]
    installments: list[InstallmentResponse]
    invitations: list[InvitationResponse]


class InvoiceSummaryResponse(BaseModel):
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


class InvoiceListResponse(BaseModel):
    invoices: list[InvoiceSummaryResponse]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


_ERROR_STATUS_MAP: dict[str, int] = {
    "event_not_found": 404,
    "contact_not_found": 404,
    "invoice_not_found": 404,
    "invoice_locked": 422,
    "invalid_transition": 422,
    "line_items_required": 422,
    "schedule_required": 422,
    "schedule_unbalanced": 422,
    # Phase 5 plan validity rejections.
    "plan_count_invalid": 422,
    "deposit_below_floor": 422,
    "paid_installment_dropped": 422,
    "unknown_fields": 422,
    "contact_ids_required": 422,
    # Phase 2 catalog rejections.
    "catalog_item_not_found": 404,
    "catalog_item_inactive": 422,
    "catalog_line_legacy_text": 422,
    "catalog_leak": 422,
    "line_public_description_conflict": 422,
    "public_description_required": 422,
    # Phase 2a discount snapshot rejections.
    "discount_preset_not_found": 422,
    "invalid_discount_percent": 422,
    "discount_percent_required": 422,
    # Phase 7 stacked discount cap.
    "combined_discount_too_high": 422,
}


def _raise_for(exc: InvoiceServiceError) -> None:
    status = _ERROR_STATUS_MAP.get(exc.code, 400)
    detail: dict[str, object] = {"code": exc.code}
    if exc.extra:
        detail.update(exc.extra)
    raise HTTPException(status_code=status, detail=detail) from exc


def _peek_invoice(db: Session, invoice_id: int) -> Invoice | None:
    """Read the row for log context (event_id, from_status) without
    raising. Used by the router's logging path; the service still does the
    authoritative existence checks."""
    return db.get(Invoice, invoice_id)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _to_line_input(p: LineItemPayload) -> LineItemInput:
    return LineItemInput(
        quantity=p.quantity,
        unit_price_cents=p.unit_price_cents,
        kind=p.kind,
        sort_order=p.sort_order,
        product_key=p.product_key,
        discount_cents=p.discount_cents,
        tax_rate=p.tax_rate,
        tax_name=p.tax_name,
        catalog_item_id=p.catalog_item_id,
        size_label=p.size_label,
        public_description=p.public_description,
        internal_notes=p.internal_notes,
        description=p.description,
        notes=p.notes,
    )


def _to_installment_input(p: InstallmentPayload) -> InstallmentInput:
    return InstallmentInput(
        label=p.label,
        amount_cents=p.amount_cents,
        due_date=p.due_date,
        sort_order=p.sort_order,
        staff_notes=p.staff_notes,
    )


def _detail_to_response(detail) -> InvoiceDetailResponse:
    return InvoiceDetailResponse(
        id=detail.id,
        event_id=detail.event_id,
        contact_id=detail.contact_id,
        invoice_number=detail.invoice_number,
        status=detail.status,
        issue_date=detail.issue_date,
        due_date=detail.due_date,
        subtotal_cents=detail.subtotal_cents,
        discount_cents=detail.discount_cents,
        tax_cents=detail.tax_cents,
        total_cents=detail.total_cents,
        paid_to_date_cents=detail.paid_to_date_cents,
        balance_cents=detail.balance_cents,
        order_discounts=[
            OrderDiscountResponse(
                id=od.id,
                sort_order=od.sort_order,
                preset_id=od.preset_id,
                label=od.label,
                percent=od.percent,
            )
            for od in detail.order_discounts
        ],
        terms=detail.terms,
        footer=detail.footer,
        public_notes=detail.public_notes,
        private_notes=detail.private_notes,
        po_number=detail.po_number,
        revision=detail.revision,
        sent_at=detail.sent_at,
        viewed_at=detail.viewed_at,
        paid_at=detail.paid_at,
        cancelled_at=detail.cancelled_at,
        cancellation_reason=detail.cancellation_reason,
        last_pdf_rendered_revision=detail.last_pdf_rendered_revision,
        last_pdf_rendered_at=detail.last_pdf_rendered_at,
        last_pdf_render_error=detail.last_pdf_render_error,
        created_by_user_id=detail.created_by_user_id,
        sold_by_user_id=detail.sold_by_user_id,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        deleted_at=detail.deleted_at,
        source_quote_id=detail.source_quote_id,
        source_quote_number=detail.source_quote_number,
        line_items=[
            LineItemResponse(
                id=li.id,
                sort_order=li.sort_order,
                kind=li.kind,
                product_key=li.product_key,
                description=li.description,
                quantity=li.quantity,
                unit_price_cents=li.unit_price_cents,
                discount_cents=li.discount_cents,
                tax_rate=li.tax_rate,
                tax_name=li.tax_name,
                line_subtotal_cents=li.line_subtotal_cents,
                line_tax_cents=li.line_tax_cents,
                line_total_cents=li.line_total_cents,
                notes=li.notes,
                catalog_item_id=li.catalog_item_id,
                size_label=li.size_label,
                public_description=li.public_description,
                internal_notes=li.internal_notes,
                catalog=(
                    CatalogLineSnapshotResponse(
                        id=li.catalog.id,
                        internal_sku=li.catalog.internal_sku,
                        public_code=li.catalog.public_code,
                        designer=li.catalog.designer,
                        style_number=li.catalog.style_number,
                        color=li.catalog.color,
                        house_name=li.catalog.house_name,
                        category=li.catalog.category,
                        product_title=li.catalog.product_title,
                    )
                    if li.catalog
                    else None
                ),
            )
            for li in detail.line_items
        ],
        installments=[
            InstallmentResponse(
                id=inst.id,
                sort_order=inst.sort_order,
                label=inst.label,
                amount_cents=inst.amount_cents,
                due_date=inst.due_date,
                paid_at=inst.paid_at,
                staff_notes=inst.staff_notes,
            )
            for inst in detail.installments
        ],
        invitations=[
            InvitationResponse(
                id=inv.id,
                contact_id=inv.contact_id,
                public_key=inv.public_key,
                sent_at=inv.sent_at,
                last_resent_at=inv.last_resent_at,
                viewed_at=inv.viewed_at,
                last_viewed_at=inv.last_viewed_at,
                view_count=inv.view_count,
                expires_at=inv.expires_at,
                revoked_at=inv.revoked_at,
            )
            for inv in detail.invitations
        ],
    )


def _summary_to_response(s) -> InvoiceSummaryResponse:
    return InvoiceSummaryResponse(
        id=s.id,
        event_id=s.event_id,
        contact_id=s.contact_id,
        contact_name=s.contact_name,
        invoice_number=s.invoice_number,
        status=s.status,
        issue_date=s.issue_date,
        due_date=s.due_date,
        total_cents=s.total_cents,
        paid_to_date_cents=s.paid_to_date_cents,
        balance_cents=s.balance_cents,
        sent_at=s.sent_at,
        paid_at=s.paid_at,
        sold_by_user_id=s.sold_by_user_id,
        created_at=s.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@event_invoices_router.post(
    "/{event_id}/invoices",
    response_model=InvoiceDetailResponse,
    status_code=201,
)
def create_invoice(
    event_id: int,
    payload: InvoiceCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> InvoiceDetailResponse:
    try:
        invoice = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=payload.contact_id,
            line_items=[_to_line_input(li) for li in payload.line_items],
            installments=[
                _to_installment_input(i) for i in payload.installments
            ],
            discount_cents=payload.discount_cents,
            order_discounts=[od.model_dump() for od in payload.order_discounts],
            custom_amounts=payload.custom_amounts,
            issue_date=payload.issue_date,
            terms=payload.terms,
            footer=payload.footer,
            public_notes=payload.public_notes,
            private_notes=payload.private_notes,
            po_number=payload.po_number,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "invoice.created",
            extra={
                "user_id": user.id,
                "event_id": event_id,
                "invoice_id": invoice.id,
                "from_status": None,
                "to_status": invoice.status,
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = invoice_service.get_invoice_detail(db, invoice.id)
    return _detail_to_response(detail)


@event_invoices_router.get(
    "/{event_id}/invoices",
    response_model=InvoiceListResponse,
)
def list_invoices_for_event(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    status: _InvoiceStatus | None = Query(default=None),
    include_deleted: bool = Query(default=False),
) -> InvoiceListResponse:
    summaries = invoice_service.list_invoices_for_event(
        db,
        event_id=event_id,
        include_deleted=include_deleted,
        status=status,
    )
    return InvoiceListResponse(
        invoices=[_summary_to_response(s) for s in summaries]
    )


@invoices_router.get("", response_model=InvoiceListResponse)
def search_invoices(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    q: str | None = Query(default=None, max_length=200),
    status: _InvoiceStatus | None = Query(default=None),
    event_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> InvoiceListResponse:
    summaries = invoice_service.search_invoices(
        db,
        q=q,
        status=status,
        event_id=event_id,
        date_from=date_from,
        date_to=date_to,
        include_deleted=include_deleted,
        limit=limit,
    )
    return InvoiceListResponse(
        invoices=[_summary_to_response(s) for s in summaries]
    )


@invoices_router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> InvoiceDetailResponse:
    try:
        detail = invoice_service.get_invoice_detail(db, invoice_id)
    except InvoiceServiceError as exc:
        _raise_for(exc)
    return _detail_to_response(detail)


@invoices_router.patch("/{invoice_id}", response_model=InvoiceDetailResponse)
def patch_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> InvoiceDetailResponse:
    raw_patch = payload.model_dump(exclude_unset=True)
    # Convert nested arrays from Pydantic models to dataclass inputs.
    if "line_items" in raw_patch and raw_patch["line_items"] is not None:
        raw_patch["line_items"] = [
            _to_line_input(LineItemPayload(**li))
            for li in raw_patch["line_items"]
        ]
    if "installments" in raw_patch and raw_patch["installments"] is not None:
        raw_patch["installments"] = [
            _to_installment_input(InstallmentPayload(**i))
            for i in raw_patch["installments"]
        ]
    pre = _peek_invoice(db, invoice_id)
    pre_status = pre.status if pre else None
    try:
        invoice = invoice_service.update_invoice(
            db,
            invoice_id=invoice_id,
            patch=raw_patch,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "invoice.updated",
            extra={
                "user_id": user.id,
                "event_id": invoice.event_id,
                "invoice_id": invoice_id,
                "from_status": pre_status,
                "to_status": invoice.status,
                "revision": invoice.revision,
                "fields": sorted(raw_patch.keys()),
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = invoice_service.get_invoice_detail(db, invoice_id)
    return _detail_to_response(detail)


@invoices_router.post("/{invoice_id}/send", response_model=InvoiceDetailResponse)
def send_invoice(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    # Floor-access dep runs first (scope + attendance gate), rate
    # limiter runs second. Both deps wrap `get_current_user`-style
    # decoding which FastAPI deduplicates per request.
    _floor: Annotated[
        User, Depends(require_floor_access("admin", "sales"))
    ],
    user: Annotated[User, Depends(staff_money_rate_limit)],
) -> InvoiceDetailResponse:
    pre = _peek_invoice(db, invoice_id)
    pre_status = pre.status if pre else None
    try:
        invoice = invoice_service.mark_sent(
            db, invoice_id=invoice_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "invoice.sent",
            extra={
                "user_id": user.id,
                "event_id": invoice.event_id,
                "invoice_id": invoice_id,
                "from_status": pre_status,
                "to_status": invoice.status,
                "invoice_number": invoice.invoice_number,
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)

    # Email send happens AFTER commit so a transient SMTP failure does
    # not roll back the status change. Staff already saw the invoice as
    # 'sent'; if the email failed they can resend from the same UI. The
    # 502 is a hint to retry, not a state-machine reset.
    try:
        sent_count = portal_email.send_invoice_invitations(db, invoice=invoice)
        log.info(
            "invoice.sent.email",
            extra={
                "invoice_id": invoice_id,
                "emails_sent": sent_count,
            },
        )
    except PortalEmailError as exc:
        log.warning(
            "invoice.sent.email_failed",
            extra={"invoice_id": invoice_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "email_send_failed",
                "message": (
                    "Invoice was marked sent but the email failed to deliver. "
                    "Use Resend to retry."
                ),
            },
        )

    detail = invoice_service.get_invoice_detail(db, invoice_id)
    return _detail_to_response(detail)


@invoices_router.post(
    "/{invoice_id}/resend", response_model=InvoiceDetailResponse
)
def resend_invoice(
    invoice_id: int,
    payload: ResendPayload,
    db: Annotated[Session, Depends(get_db)],
    _floor: Annotated[
        User, Depends(require_floor_access("admin", "sales"))
    ],
    user: Annotated[User, Depends(staff_money_rate_limit)],
) -> InvoiceDetailResponse:
    try:
        invoice = invoice_service.resend_invoice(
            db,
            invoice_id=invoice_id,
            contact_ids=payload.contact_ids,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "invoice.resent",
            extra={
                "user_id": user.id,
                "event_id": invoice.event_id,
                "invoice_id": invoice_id,
                "from_status": invoice.status,
                "to_status": invoice.status,
                "contact_ids": payload.contact_ids,
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)

    # Resend the email body for the targeted contacts. We need the
    # specific invitation_ids that were just touched, but the service
    # doesn't return them. Re-derive from the contact_ids the caller
    # passed — those are the only invitation rows being resent.
    invitation_ids = invoice_service.invitation_ids_for_contacts(
        db, invoice_id=invoice_id, contact_ids=payload.contact_ids
    )
    try:
        sent_count = portal_email.send_invoice_invitations(
            db, invoice=invoice, invitation_ids=invitation_ids
        )
        log.info(
            "invoice.resent.email",
            extra={
                "invoice_id": invoice_id,
                "contact_ids": payload.contact_ids,
                "emails_sent": sent_count,
            },
        )
    except PortalEmailError as exc:
        log.warning(
            "invoice.resent.email_failed",
            extra={"invoice_id": invoice_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "email_send_failed",
                "message": (
                    "Resend was recorded but the email failed to deliver. "
                    "Try again in a moment."
                ),
            },
        )

    detail = invoice_service.get_invoice_detail(db, invoice_id)
    return _detail_to_response(detail)


@invoices_router.post(
    "/{invoice_id}/cancel", response_model=InvoiceDetailResponse
)
def cancel_invoice(
    invoice_id: int,
    payload: CancelPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> InvoiceDetailResponse:
    pre = _peek_invoice(db, invoice_id)
    pre_status = pre.status if pre else None
    try:
        invoice = invoice_service.cancel_invoice(
            db,
            invoice_id=invoice_id,
            reason=payload.reason,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "invoice.cancelled",
            extra={
                "user_id": user.id,
                "event_id": invoice.event_id,
                "invoice_id": invoice_id,
                "from_status": pre_status,
                "to_status": invoice.status,
                "reason": payload.reason,
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = invoice_service.get_invoice_detail(db, invoice_id)
    return _detail_to_response(detail)


@invoices_router.delete("/{invoice_id}", status_code=204, response_class=Response)
def delete_invoice(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    pre = _peek_invoice(db, invoice_id)
    pre_status = pre.status if pre else None
    pre_event_id = pre.event_id if pre else None
    try:
        invoice_service.soft_delete_invoice(
            db, invoice_id=invoice_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "invoice.deleted",
            extra={
                "user_id": user.id,
                "event_id": pre_event_id,
                "invoice_id": invoice_id,
                "from_status": pre_status,
                "to_status": pre_status,
            },
        )
    except InvoiceServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# PDF download (Phase 8)
# ---------------------------------------------------------------------------


def _pdf_file_response(path, filename: str) -> FileResponse:
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=filename,
        headers={
            # Inline so the staff PDF viewer opens in-tab; the browser
            # download button still works because filename is set.
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


@invoices_router.get("/{invoice_id}/pdf")
def get_invoice_pdf(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(staff_money_rate_limit)],
) -> FileResponse:
    """Lazy render. The first download after a revision bump pays the
    render cost; subsequent downloads serve the cached file."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found"})
    try:
        path = invoice_pdf.ensure_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
    except PdfRenderError as exc:
        # The service has already stamped the error onto the invoice;
        # commit so the staff editor can show the Retry button next
        # time it loads.
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    return _pdf_file_response(path, invoice_pdf.invoice_pdf_filename(invoice))


@invoices_router.post("/{invoice_id}/pdf/retry")
def retry_invoice_pdf(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(staff_money_rate_limit)],
) -> InvoiceDetailResponse:
    """Force-re-render the current revision. Used by the staff Retry
    button when ``last_pdf_render_error`` is populated."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "invoice_not_found"})
    try:
        invoice_pdf.render_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
        log.info(
            "invoice.pdf_retried",
            extra={"user_id": user.id, "invoice_id": invoice_id},
        )
    except PdfRenderError as exc:
        db.commit()  # error stamp persists
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    detail = invoice_service.get_invoice_detail(db, invoice_id)
    return _detail_to_response(detail)


# ---------------------------------------------------------------------------
# Phase 10.3c: participant tagging
# ---------------------------------------------------------------------------


class InvoiceParticipantTagPatch(BaseModel):
    event_participant_id: int | None = None


class InvoiceParticipantTagResponse(BaseModel):
    invoice_id: int
    event_participant_id: int | None


_INVOICE_PARTICIPANT_ERROR_STATUS = {
    "invoice_not_found": 404,
    "participant_not_found": 404,
    "participant_event_mismatch": 400,
}


@invoices_router.patch(
    "/{invoice_id}/participant",
    response_model=InvoiceParticipantTagResponse,
)
def tag_invoice_participant(
    invoice_id: int,
    payload: InvoiceParticipantTagPatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> InvoiceParticipantTagResponse:
    """Tag an invoice with a specific event_participant (or clear).

    Shared admin+sales surface (matches the other invoice mutation
    routes). Cross-event participants are rejected as
    participant_event_mismatch — buyer journeys live UNDER the event.
    """
    try:
        invoice = buyer_journey.attach_invoice_to_participant(
            db,
            invoice_id=invoice_id,
            event_participant_id=payload.event_participant_id,
            actor_user_id=user.id,
        )
    except BuyerJourneyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_INVOICE_PARTICIPANT_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(invoice)
    return InvoiceParticipantTagResponse(
        invoice_id=invoice.id,
        event_participant_id=invoice.event_participant_id,
    )
