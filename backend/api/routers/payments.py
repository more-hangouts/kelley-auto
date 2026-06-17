"""Payments router.

Three logical groupings sharing one file because they all dispatch to
`services/payment_service.py`:

- `/api/payments/*`     — record / read / refund / void / soft-delete.
- `/api/invoices/{id}/payments` — list payments allocated to one invoice
  (in-editor sub-section).
- `/api/events/{id}/payments` — list every payment for the event's
  primary contact (Payments tab).

Auth on every staff route via `get_current_user`. There is no public
portal payment-recording route in v1 — customers pay via off-system
methods (Zelle, check, card-in-person) and staff record the receipt.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.rate_limit import staff_money_rate_limit
from database.auth import require_admin_scope
from database.connection import get_db
from database.models import Payment, User
from services import invoice_pdf, payment_service
from services.invoice_pdf import PdfRenderError
from services.payment_service import (
    AllocationInput,
    AllocationRefundInput,
    PaymentServiceError,
)

log = logging.getLogger(__name__)

payments_router = APIRouter()
invoice_payments_router = APIRouter()
event_payments_router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_PaymentMethod = Literal["cash", "check", "card", "transfer", "zelle", "other"]


class AllocationPayload(BaseModel):
    invoice_id: int
    applied_cents: int = Field(gt=0)


class AllocationRefundPayload(BaseModel):
    allocation_id: int
    refund_cents: int = Field(gt=0)


class RecordPaymentPayload(BaseModel):
    contact_id: int
    amount_cents: int = Field(gt=0)
    method: _PaymentMethod
    payment_date: date | None = None
    transaction_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    allocations: list[AllocationPayload] = Field(default_factory=list)


class ApplyUnappliedPayload(BaseModel):
    invoice_id: int
    applied_cents: int = Field(gt=0)


class RecordRefundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_cents: int = Field(gt=0)
    refund_method: _PaymentMethod
    refund_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    from_unapplied_cents: int = Field(default=0, ge=0)
    allocation_refunds: list[AllocationRefundPayload] = Field(default_factory=list)


class VoidPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class AllocationResponse(BaseModel):
    id: int
    invoice_id: int
    invoice_number: str | None
    applied_cents: int
    refunded_cents: int


class RefundEventResponse(BaseModel):
    id: int
    amount_cents: int
    from_unapplied_cents: int
    from_allocations: list[dict]
    refund_method: str
    refund_reference: str | None
    notes: str | None
    actor_user_id: int | None
    created_at: datetime


class PaymentDetailResponse(BaseModel):
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
    allocations: list[AllocationResponse]
    refund_events: list[RefundEventResponse]


class PaymentSummaryResponse(BaseModel):
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


class PaymentListResponse(BaseModel):
    payments: list[PaymentSummaryResponse]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


_ERROR_STATUS_MAP: dict[str, int] = {
    "contact_not_found": 404,
    "invoice_not_found": 404,
    "payment_not_found": 404,
    "allocation_not_found": 404,
    "allocation_not_on_payment": 422,
    "allocation_partially_refunded": 422,
    "duplicate_allocation": 422,
    "duplicate_allocation_refund": 422,
    "exceeds_unapplied": 422,
    "invalid_allocation": 422,
    "invalid_allocation_target": 422,
    "invalid_amount": 422,
    "invalid_method": 422,
    "invalid_payment_state": 422,
    "invoice_overallocation": 422,
    "over_allocation": 422,
    "payment_not_deletable": 422,
    "refund_exceeds_allocation_remaining": 422,
    "refund_exceeds_remaining": 422,
    "refund_split_mismatch": 422,
    "refund_unapplied_exceeds_pool": 422,
}


def _raise_for(exc: PaymentServiceError) -> None:
    status = _ERROR_STATUS_MAP.get(exc.code, 400)
    detail: dict[str, Any] = {"code": exc.code}
    if exc.extra:
        detail.update(exc.extra)
    raise HTTPException(status_code=status, detail=detail) from exc


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _detail_to_response(detail) -> PaymentDetailResponse:
    return PaymentDetailResponse(
        id=detail.id,
        contact_id=detail.contact_id,
        payment_number=detail.payment_number,
        amount_cents=detail.amount_cents,
        applied_cents=detail.applied_cents,
        unapplied_cents=detail.unapplied_cents,
        refunded_cents=detail.refunded_cents,
        payment_date=detail.payment_date,
        method=detail.method,
        transaction_reference=detail.transaction_reference,
        status=detail.status,
        notes=detail.notes,
        created_by_user_id=detail.created_by_user_id,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        deleted_at=detail.deleted_at,
        allocations=[
            AllocationResponse(
                id=a.id,
                invoice_id=a.invoice_id,
                invoice_number=a.invoice_number,
                applied_cents=a.applied_cents,
                refunded_cents=a.refunded_cents,
            )
            for a in detail.allocations
        ],
        refund_events=[
            RefundEventResponse(
                id=r.id,
                amount_cents=r.amount_cents,
                from_unapplied_cents=r.from_unapplied_cents,
                from_allocations=r.from_allocations,
                refund_method=r.refund_method,
                refund_reference=r.refund_reference,
                notes=r.notes,
                actor_user_id=r.actor_user_id,
                created_at=r.created_at,
            )
            for r in detail.refund_events
        ],
    )


def _summary_to_response(s) -> PaymentSummaryResponse:
    return PaymentSummaryResponse(
        id=s.id,
        contact_id=s.contact_id,
        contact_name=s.contact_name,
        payment_number=s.payment_number,
        amount_cents=s.amount_cents,
        applied_cents=s.applied_cents,
        unapplied_cents=s.unapplied_cents,
        refunded_cents=s.refunded_cents,
        payment_date=s.payment_date,
        method=s.method,
        status=s.status,
        created_at=s.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@payments_router.post(
    "", response_model=PaymentDetailResponse, status_code=201
)
def create_payment(
    payload: RecordPaymentPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(staff_money_rate_limit)],
) -> PaymentDetailResponse:
    try:
        payment = payment_service.record_payment(
            db,
            contact_id=payload.contact_id,
            amount_cents=payload.amount_cents,
            method=payload.method,
            payment_date=payload.payment_date,
            transaction_reference=payload.transaction_reference,
            notes=payload.notes,
            allocations=[
                AllocationInput(
                    invoice_id=a.invoice_id, applied_cents=a.applied_cents
                )
                for a in payload.allocations
            ],
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "payment.recorded",
            extra={
                "user_id": user.id,
                "contact_id": payload.contact_id,
                "payment_id": payment.id,
                "payment_number": payment.payment_number,
                "amount_cents": payment.amount_cents,
                "applied_cents": payment.applied_cents,
                "unapplied_cents": payment.unapplied_cents,
            },
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = payment_service.get_payment_detail(db, payment.id)
    return _detail_to_response(detail)


@payments_router.get(
    "/{payment_id}", response_model=PaymentDetailResponse
)
def get_payment(
    payment_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentDetailResponse:
    try:
        detail = payment_service.get_payment_detail(db, payment_id)
    except PaymentServiceError as exc:
        _raise_for(exc)
    return _detail_to_response(detail)


@payments_router.post(
    "/{payment_id}/apply", response_model=PaymentDetailResponse
)
def apply_unapplied(
    payment_id: int,
    payload: ApplyUnappliedPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentDetailResponse:
    try:
        payment_service.apply_unapplied(
            db,
            payment_id=payment_id,
            invoice_id=payload.invoice_id,
            applied_cents=payload.applied_cents,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "payment.applied_unapplied",
            extra={
                "user_id": user.id,
                "payment_id": payment_id,
                "invoice_id": payload.invoice_id,
                "applied_cents": payload.applied_cents,
            },
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = payment_service.get_payment_detail(db, payment_id)
    return _detail_to_response(detail)


@payments_router.delete(
    "/allocations/{allocation_id}",
    response_model=PaymentDetailResponse,
)
def unapply_allocation(
    allocation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentDetailResponse:
    """Remove a non-refunded allocation; funds return to the unapplied
    pool. Returns the parent payment's updated detail so the editor can
    refresh in one round-trip."""
    try:
        payment = payment_service.unapply_allocation(
            db, allocation_id=allocation_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "payment.allocation_unapplied",
            extra={
                "user_id": user.id,
                "allocation_id": allocation_id,
                "payment_id": payment.id,
            },
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = payment_service.get_payment_detail(db, payment.id)
    return _detail_to_response(detail)


@payments_router.post(
    "/{payment_id}/refunds", response_model=PaymentDetailResponse
)
def record_refund(
    payment_id: int,
    payload: RecordRefundPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentDetailResponse:
    try:
        refund_event = payment_service.record_refund(
            db,
            payment_id=payment_id,
            amount_cents=payload.amount_cents,
            refund_method=payload.refund_method,
            refund_reference=payload.refund_reference,
            notes=payload.notes,
            from_unapplied_cents=payload.from_unapplied_cents,
            allocation_refunds=[
                AllocationRefundInput(
                    allocation_id=ar.allocation_id, refund_cents=ar.refund_cents
                )
                for ar in payload.allocation_refunds
            ],
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "payment.refunded",
            extra={
                "user_id": user.id,
                "payment_id": payment_id,
                "refund_event_id": refund_event.id,
                "amount_cents": refund_event.amount_cents,
                "from_unapplied_cents": refund_event.from_unapplied_cents,
            },
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = payment_service.get_payment_detail(db, payment_id)
    return _detail_to_response(detail)


@payments_router.post(
    "/{payment_id}/void", response_model=PaymentDetailResponse
)
def void_payment(
    payment_id: int,
    payload: VoidPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentDetailResponse:
    try:
        payment_service.void_payment(
            db,
            payment_id=payment_id,
            reason=payload.reason,
            actor_user_id=user.id,
        )
        db.commit()
        log.info(
            "payment.voided",
            extra={
                "user_id": user.id,
                "payment_id": payment_id,
                "reason": payload.reason,
            },
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)

    detail = payment_service.get_payment_detail(db, payment_id)
    return _detail_to_response(detail)


@payments_router.delete(
    "/{payment_id}", status_code=204, response_class=Response
)
def delete_payment(
    payment_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        payment_service.soft_delete_payment(
            db, payment_id=payment_id, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "payment.deleted",
            extra={"user_id": user.id, "payment_id": payment_id},
        )
    except PaymentServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return Response(status_code=204)


@invoice_payments_router.get(
    "/{invoice_id}/payments", response_model=PaymentListResponse
)
def list_payments_for_invoice(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentListResponse:
    summaries = payment_service.list_payments_for_invoice(
        db, invoice_id=invoice_id
    )
    return PaymentListResponse(
        payments=[_summary_to_response(s) for s in summaries]
    )


@event_payments_router.get(
    "/{event_id}/payments", response_model=PaymentListResponse
)
def list_payments_for_event(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> PaymentListResponse:
    summaries = payment_service.list_payments_for_event(db, event_id=event_id)
    return PaymentListResponse(
        payments=[_summary_to_response(s) for s in summaries]
    )


# ---------------------------------------------------------------------------
# Receipt PDF (Phase 8). Receipts are immutable so the cache key has no
# revision; the first download renders, subsequent downloads serve.
# ---------------------------------------------------------------------------


@payments_router.get("/{payment_id}/receipt.pdf")
def get_payment_receipt_pdf(
    payment_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> FileResponse:
    payment = db.get(Payment, payment_id)
    if payment is None or payment.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "payment_not_found"})
    try:
        path = invoice_pdf.ensure_payment_receipt_pdf(db, payment_id=payment_id)
        db.commit()
    except PdfRenderError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    filename = invoice_pdf.receipt_pdf_filename(payment)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=filename,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )
