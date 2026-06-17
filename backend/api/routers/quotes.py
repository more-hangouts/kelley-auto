"""Quotes router.

Two routers because the routes split across `/api/events/{id}/quotes`
and `/api/quotes/...`. server.py mounts each at the matching prefix.

Auth: every staff route requires `get_current_user`. The customer-facing
portal (Phase 7) reads `quote_invitations` under a different prefix.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from services.attendance_gate import require_floor_access
from database.connection import get_db
from database.models import Quote, User
from services import buyer_journey, invoice_pdf, portal_email, quote_service
from services.buyer_journey import BuyerJourneyError
from services.invoice_pdf import PdfRenderError
from services.invoice_service import LineItemInput
from services.portal_email import PortalEmailError
from services.quote_service import QuoteInstallmentInput

# Reuse the invoice router's detail shape verbatim — `convert_to_invoice`
# returns a fully-populated invoice detail and clients already know how
# to render it.
from api.routers.invoices import (
    InvoiceDetailResponse,
    _detail_to_response as _invoice_detail_to_response,
)
from services.invoice_service import get_invoice_detail
from services.quote_service import QuoteServiceError

log = logging.getLogger(__name__)

event_quotes_router = APIRouter()
quotes_router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_LineKind = Literal["product", "service", "alteration", "fee"]
_QuoteStatus = Literal[
    "draft", "sent", "approved", "rejected", "converted", "expired", "cancelled"
]


class QuoteLineItemPayload(BaseModel):
    """See ``api.routers.invoices.LineItemPayload`` for the catalog-aware
    rules. Quote and invoice line write paths share the same shape so a
    converted quote's lines preserve the catalog linkage."""

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


class QuoteInstallmentPayload(BaseModel):
    """Phase 4 of the discount/payment-term refactor.

    Mirrors ``api.routers.invoices.InstallmentPayload`` minus the
    payment-state fields (``staff_notes``) — quotes never carry
    payment state. Label is optional on quotes; the conversion path
    fills in a default when copying into invoice_installments.
    """

    label: str | None = Field(default=None, max_length=60)
    amount_cents: int = Field(gt=0)
    due_date: date
    sort_order: int | None = None


class OrderDiscountPayload(BaseModel):
    """Phase 7 stacked order-level discount input. Same shape as the
    invoice router's variant; declared here so the quote router can
    keep its own forbid-extras gate."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=60)
    percent: Decimal | None = Field(default=None, ge=0, le=50)


class QuoteCreate(BaseModel):
    contact_id: int
    line_items: list[QuoteLineItemPayload] = Field(default_factory=list)
    installments: list[QuoteInstallmentPayload] = Field(default_factory=list)
    discount_cents: int = Field(default=0, ge=0)
    # Phase 7 stacked order-level discounts; combined cap at 50%.
    order_discounts: list[OrderDiscountPayload] = Field(default_factory=list)
    # Phase 5 custom-amounts escape hatch. Per-request flag, not stored.
    custom_amounts: bool = False
    issue_date: date | None = None
    expires_at: date | None = None
    terms: str | None = None
    footer: str | None = None
    public_notes: str | None = None
    private_notes: str | None = None
    po_number: str | None = Field(default=None, max_length=64)


class QuoteUpdate(BaseModel):
    """Partial update. Fields omitted are left unchanged. `line_items`,
    `installments`, and `order_discounts` arrays REPLACE all rows when
    present."""

    model_config = ConfigDict(extra="forbid")

    line_items: list[QuoteLineItemPayload] | None = None
    installments: list[QuoteInstallmentPayload] | None = None
    discount_cents: int | None = Field(default=None, ge=0)
    order_discounts: list[OrderDiscountPayload] | None = None
    custom_amounts: bool | None = None
    issue_date: date | None = None
    expires_at: date | None = None
    terms: str | None = None
    footer: str | None = None
    public_notes: str | None = None
    private_notes: str | None = None
    po_number: str | None = Field(default=None, max_length=64)


class ApprovePayload(BaseModel):
    """Customer-signature submission. Stripe-style: signature is opaque
    base64 PNG bytes; we don't validate the image format here, the portal
    is the single producer."""

    signature_base64: str = Field(min_length=1)
    signature_name: str = Field(min_length=1, max_length=120)
    # signature_ip is optional from the staff-side router (admin staff
    # marking it on a customer's behalf) — the portal route in Phase 7
    # always forwards request.client.host.
    signature_ip: str | None = None


class RejectPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


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


class QuoteLineItemResponse(BaseModel):
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
    catalog: CatalogLineSnapshotResponse | None


class QuoteInstallmentResponse(BaseModel):
    id: int
    sort_order: int
    label: str | None
    amount_cents: int
    due_date: date


class QuoteInvitationResponse(BaseModel):
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


class QuoteDetailResponse(BaseModel):
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
    order_discounts: list[OrderDiscountResponse]
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
    # signature_base64 deliberately not serialized in detail responses —
    # the rendered PDF (Phase 8) is the only consumer; staff-side UI
    # shows "signed by Debbie at 2026-...".
    last_pdf_rendered_revision: int | None
    last_pdf_rendered_at: datetime | None
    last_pdf_render_error: str | None
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    line_items: list[QuoteLineItemResponse]
    installments: list[QuoteInstallmentResponse]
    invitations: list[QuoteInvitationResponse]


class QuoteSummaryResponse(BaseModel):
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


class QuoteListResponse(BaseModel):
    quotes: list[QuoteSummaryResponse]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


_ERROR_STATUS_MAP: dict[str, int] = {
    "event_not_found": 404,
    "contact_not_found": 404,
    "quote_not_found": 404,
    "quote_locked": 422,
    "quote_not_deletable": 422,
    "invalid_transition": 422,
    "line_items_required": 422,
    "signature_required": 422,
    "cancel_draft_not_allowed": 422,
    "conversion_inconsistent": 500,
    # Phase 2 catalog rejections (translated from invoice_service codes
    # by quote_service._replace_line_items so the wire codes stay the
    # same across both write paths).
    "catalog_item_not_found": 404,
    "catalog_item_inactive": 422,
    "catalog_line_legacy_text": 422,
    "catalog_leak": 422,
    "line_public_description_conflict": 422,
    "public_description_required": 422,
    "unknown_fields": 422,
    # Phase 2a discount snapshot rejections.
    "discount_preset_not_found": 422,
    "invalid_discount_percent": 422,
    "discount_percent_required": 422,
    # Phase 7 stacked discount cap.
    "combined_discount_too_high": 422,
    # Phase 5 plan validity rejections.
    "plan_count_invalid": 422,
    "deposit_below_floor": 422,
}


def _raise_for(exc: QuoteServiceError) -> None:
    status = _ERROR_STATUS_MAP.get(exc.code, 400)
    detail: dict[str, object] = {"code": exc.code}
    if exc.extra:
        detail.update(exc.extra)
    raise HTTPException(status_code=status, detail=detail) from exc


def _peek_quote(db: Session, quote_id: int) -> Quote | None:
    return db.get(Quote, quote_id)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _to_line_input(p: QuoteLineItemPayload) -> LineItemInput:
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


def _to_installment_input(p: QuoteInstallmentPayload) -> QuoteInstallmentInput:
    return QuoteInstallmentInput(
        label=p.label,
        amount_cents=p.amount_cents,
        due_date=p.due_date,
        sort_order=p.sort_order,
    )


def _detail_to_response(detail) -> QuoteDetailResponse:
    return QuoteDetailResponse(
        id=detail.id,
        event_id=detail.event_id,
        contact_id=detail.contact_id,
        quote_number=detail.quote_number,
        status=detail.status,
        issue_date=detail.issue_date,
        expires_at=detail.expires_at,
        subtotal_cents=detail.subtotal_cents,
        discount_cents=detail.discount_cents,
        tax_cents=detail.tax_cents,
        total_cents=detail.total_cents,
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
        approved_at=detail.approved_at,
        rejected_at=detail.rejected_at,
        rejection_reason=detail.rejection_reason,
        converted_at=detail.converted_at,
        converted_invoice_id=detail.converted_invoice_id,
        cancelled_at=detail.cancelled_at,
        cancellation_reason=detail.cancellation_reason,
        signature_signed_at=detail.signature_signed_at,
        signature_name=detail.signature_name,
        signature_ip=detail.signature_ip,
        last_pdf_rendered_revision=detail.last_pdf_rendered_revision,
        last_pdf_rendered_at=detail.last_pdf_rendered_at,
        last_pdf_render_error=detail.last_pdf_render_error,
        created_by_user_id=detail.created_by_user_id,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        deleted_at=detail.deleted_at,
        line_items=[
            QuoteLineItemResponse(
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
            QuoteInstallmentResponse(
                id=inst.id,
                sort_order=inst.sort_order,
                label=inst.label,
                amount_cents=inst.amount_cents,
                due_date=inst.due_date,
            )
            for inst in detail.installments
        ],
        invitations=[
            QuoteInvitationResponse(
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


def _summary_to_response(s) -> QuoteSummaryResponse:
    return QuoteSummaryResponse(
        id=s.id,
        event_id=s.event_id,
        contact_id=s.contact_id,
        contact_name=s.contact_name,
        quote_number=s.quote_number,
        status=s.status,
        issue_date=s.issue_date,
        expires_at=s.expires_at,
        total_cents=s.total_cents,
        sent_at=s.sent_at,
        approved_at=s.approved_at,
        converted_at=s.converted_at,
        converted_invoice_id=s.converted_invoice_id,
        created_at=s.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@event_quotes_router.post(
    "/{event_id}/quotes",
    response_model=QuoteDetailResponse,
    status_code=201,
)
def create_quote(
    event_id: int,
    payload: QuoteCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    try:
        quote = quote_service.create_quote(
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
            expires_at=payload.expires_at,
            terms=payload.terms,
            footer=payload.footer,
            public_notes=payload.public_notes,
            private_notes=payload.private_notes,
            po_number=payload.po_number,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.created",
            extra={
                "user_id": user.id,
                "event_id": event_id,
                "quote_id": quote.id,
                "from_status": None,
                "to_status": quote.status,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote.id)
    return _detail_to_response(detail)


@event_quotes_router.get("/{event_id}/quotes", response_model=QuoteListResponse)
def list_quotes_for_event(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    status: _QuoteStatus | None = Query(default=None),
    include_deleted: bool = Query(default=False),
) -> QuoteListResponse:
    summaries = quote_service.list_quotes_for_event(
        db,
        event_id=event_id,
        include_deleted=include_deleted,
        status=status,
    )
    return QuoteListResponse(
        quotes=[_summary_to_response(s) for s in summaries]
    )


@quotes_router.get("", response_model=QuoteListResponse)
def search_quotes(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    q: str | None = Query(default=None, max_length=200),
    status: _QuoteStatus | None = Query(default=None),
    event_id: int | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> QuoteListResponse:
    summaries = quote_service.search_quotes(
        db,
        q=q,
        status=status,
        event_id=event_id,
        date_from=date_from,
        date_to=date_to,
        include_deleted=include_deleted,
        limit=limit,
    )
    return QuoteListResponse(
        quotes=[_summary_to_response(s) for s in summaries]
    )


@quotes_router.get("/{quote_id}", response_model=QuoteDetailResponse)
def get_quote(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> QuoteDetailResponse:
    try:
        detail = quote_service.get_quote_detail(db, quote_id)
    except QuoteServiceError as exc:
        _raise_for(exc)
    return _detail_to_response(detail)


@quotes_router.patch("/{quote_id}", response_model=QuoteDetailResponse)
def patch_quote(
    quote_id: int,
    payload: QuoteUpdate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    raw_patch = payload.model_dump(exclude_unset=True)
    if "line_items" in raw_patch and raw_patch["line_items"] is not None:
        raw_patch["line_items"] = [
            _to_line_input(QuoteLineItemPayload(**li))
            for li in raw_patch["line_items"]
        ]
    if "installments" in raw_patch and raw_patch["installments"] is not None:
        raw_patch["installments"] = [
            _to_installment_input(QuoteInstallmentPayload(**i))
            for i in raw_patch["installments"]
        ]
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        quote = quote_service.update_quote(
            db,
            quote_id=quote_id,
            patch=raw_patch,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.updated",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "revision": quote.revision,
                "fields": sorted(raw_patch.keys()),
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post("/{quote_id}/send", response_model=QuoteDetailResponse)
def send_quote(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        quote = quote_service.mark_sent(
            db, quote_id=quote_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "quote.sent",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "quote_number": quote.quote_number,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    try:
        sent_count = portal_email.send_quote_invitations(db, quote=quote)
        log.info(
            "quote.sent.email",
            extra={"quote_id": quote_id, "emails_sent": sent_count},
        )
    except PortalEmailError as exc:
        log.warning(
            "quote.sent.email_failed",
            extra={"quote_id": quote_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "email_send_failed",
                "message": (
                    "Quote was marked sent but the email failed to deliver. "
                    "Use Resend to retry."
                ),
            },
        )

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post("/{quote_id}/resend", response_model=QuoteDetailResponse)
def resend_quote(
    quote_id: int,
    payload: ResendPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    try:
        quote = quote_service.resend_quote(
            db,
            quote_id=quote_id,
            contact_ids=payload.contact_ids,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.resent",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": quote.status,
                "to_status": quote.status,
                "contact_ids": payload.contact_ids,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    invitation_ids = quote_service.invitation_ids_for_contacts(
        db, quote_id=quote_id, contact_ids=payload.contact_ids
    )
    try:
        sent_count = portal_email.send_quote_invitations(
            db, quote=quote, invitation_ids=invitation_ids
        )
        log.info(
            "quote.resent.email",
            extra={
                "quote_id": quote_id,
                "contact_ids": payload.contact_ids,
                "emails_sent": sent_count,
            },
        )
    except PortalEmailError as exc:
        log.warning(
            "quote.resent.email_failed",
            extra={"quote_id": quote_id, "error": str(exc)},
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

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post("/{quote_id}/approve", response_model=QuoteDetailResponse)
def approve_quote(
    quote_id: int,
    payload: ApprovePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    """Staff-side approve. The customer-portal approve route lives under
    a separate prefix in Phase 7 and reuses the same service call."""
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        quote = quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64=payload.signature_base64,
            signature_name=payload.signature_name,
            signature_ip=payload.signature_ip,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.approved",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "signature_name": payload.signature_name,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


def _client_ip(request: Request) -> str | None:
    """Return the request IP if it parses as a valid v4/v6 address.

    Mirrors ``portal._client_ip`` so the in-store approve route stores
    the same shape as the customer-portal approve. TestClient uses the
    literal ``"testclient"`` string, which the Postgres ``inet`` type
    rejects — ``None`` keeps the insert clean without leaking the test
    detail into production logic.
    """
    if not request.client or not request.client.host:
        return None
    host = request.client.host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


@quotes_router.post(
    "/{quote_id}/approve-in-store",
    response_model=QuoteDetailResponse,
)
def approve_quote_in_store(
    quote_id: int,
    payload: ApprovePayload,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    """Staff-witnessed in-store approval. Accepts draft or sent and
    skips the customer-email round-trip; the customer signs on a staff
    device at the counter."""
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        # User-Agent is captured opportunistically for the evidentiary
        # trail (Phase 5 of the sales portal). Truncated server-side at
        # 255 chars in the service to match the column width.
        user_agent_header = request.headers.get("user-agent")
        quote = quote_service.approve_in_store(
            db,
            quote_id=quote_id,
            signature_base64=payload.signature_base64,
            signature_name=payload.signature_name,
            signature_ip=payload.signature_ip or _client_ip(request),
            actor_user_id=user.id,
            signature_user_agent=user_agent_header,
        )
        db.commit()
        log.info(
            "quote.approved_in_store",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "signature_name": payload.signature_name,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post("/{quote_id}/reject", response_model=QuoteDetailResponse)
def reject_quote(
    quote_id: int,
    payload: RejectPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        quote = quote_service.reject_quote(
            db,
            quote_id=quote_id,
            reason=payload.reason,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.rejected",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "reason": payload.reason,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post("/{quote_id}/cancel", response_model=QuoteDetailResponse)
def cancel_quote(
    quote_id: int,
    payload: CancelPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteDetailResponse:
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        quote = quote_service.cancel_quote(
            db,
            quote_id=quote_id,
            reason=payload.reason,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "quote.cancelled",
            extra={
                "user_id": user.id,
                "event_id": quote.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": quote.status,
                "reason": payload.reason,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


@quotes_router.post(
    "/{quote_id}/convert",
    response_model=InvoiceDetailResponse,
    status_code=201,
)
def convert_quote_to_invoice(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> InvoiceDetailResponse:
    """Approved quote → draft invoice. Returns the new invoice's detail
    so the frontend can route directly to its editor without a second
    round-trip."""
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    try:
        invoice = quote_service.convert_to_invoice(
            db, quote_id=quote_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "quote.converted",
            extra={
                "user_id": user.id,
                "event_id": invoice.event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": "converted",
                "invoice_id": invoice.id,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)

    invoice_detail = get_invoice_detail(db, invoice.id)
    return _invoice_detail_to_response(invoice_detail)


@quotes_router.delete(
    "/{quote_id}", status_code=204, response_class=Response
)
def delete_quote(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    pre = _peek_quote(db, quote_id)
    pre_status = pre.status if pre else None
    pre_event_id = pre.event_id if pre else None
    try:
        quote_service.soft_delete_quote(
            db, quote_id=quote_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "quote.deleted",
            extra={
                "user_id": user.id,
                "event_id": pre_event_id,
                "quote_id": quote_id,
                "from_status": pre_status,
                "to_status": pre_status,
            },
        )
    except QuoteServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# PDF download (Phase 8)
# ---------------------------------------------------------------------------


@quotes_router.get("/{quote_id}/pdf")
def get_quote_pdf(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> FileResponse:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "quote_not_found"})
    try:
        path = invoice_pdf.ensure_quote_pdf(db, quote_id=quote_id)
        db.commit()
    except PdfRenderError as exc:
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    filename = invoice_pdf.quote_pdf_filename(quote)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=filename,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


@quotes_router.post("/{quote_id}/pdf/retry", response_model=QuoteDetailResponse)
def retry_quote_pdf(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> QuoteDetailResponse:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "quote_not_found"})
    try:
        invoice_pdf.render_quote_pdf(db, quote_id=quote_id)
        db.commit()
        log.info(
            "quote.pdf_retried",
            extra={"user_id": user.id, "quote_id": quote_id},
        )
    except PdfRenderError as exc:
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    detail = quote_service.get_quote_detail(db, quote_id)
    return _detail_to_response(detail)


# ---------------------------------------------------------------------------
# Phase 10.3b: participant tagging
# ---------------------------------------------------------------------------


class QuoteParticipantTagPatch(BaseModel):
    # Nullable: explicit ``None`` clears the tag.
    event_participant_id: int | None = None


class QuoteParticipantTagResponse(BaseModel):
    quote_id: int
    event_participant_id: int | None


_QUOTE_PARTICIPANT_ERROR_STATUS = {
    "quote_not_found": 404,
    "participant_not_found": 404,
    "participant_event_mismatch": 400,
}


@quotes_router.patch(
    "/{quote_id}/participant",
    response_model=QuoteParticipantTagResponse,
)
def tag_quote_participant(
    quote_id: int,
    payload: QuoteParticipantTagPatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> QuoteParticipantTagResponse:
    """Tag a quote with a specific event_participant (or clear).

    Shared admin+sales surface (same as the rest of the quote mutation
    routes). The buyer journey lives UNDER the event — the validator
    rejects participants from a different event.
    """
    try:
        quote = buyer_journey.attach_quote_to_participant(
            db,
            quote_id=quote_id,
            event_participant_id=payload.event_participant_id,
            actor_user_id=user.id,
        )
    except BuyerJourneyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=_QUOTE_PARTICIPANT_ERROR_STATUS.get(exc.code, 400),
            detail=exc.code,
        ) from exc

    db.commit()
    db.refresh(quote)
    return QuoteParticipantTagResponse(
        quote_id=quote.id,
        event_participant_id=quote.event_participant_id,
    )
