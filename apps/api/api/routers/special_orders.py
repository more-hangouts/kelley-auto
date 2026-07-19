"""Staff special-order API.

Phase 5 of the catalog SKU obfuscation plan. Two routers because the
routes split across ``/api/events/{event_id}/special-orders`` (list +
create) and ``/api/special-orders/{id}/...`` (transitions, patch,
get). server.py mounts each at the matching prefix.

Auth: every route requires ``get_current_user``. Special orders are
staff-only; ``vendor_order_number`` and ``internal_notes`` never
appear on a customer surface and the entire surface lives under
``/api`` so Nginx + the auth dep gate access.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import special_order_service
from services.special_order_service import (
    CreateSpecialOrderInput,
    SpecialOrderError,
)

log = logging.getLogger(__name__)

event_special_orders_router = APIRouter()
special_orders_router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_Status = Literal[
    "needed", "ordered", "delayed", "received", "picked_up", "cancelled"
]


class SpecialOrderCreatePayload(BaseModel):
    """Direct create. The picker-driven flow uses
    :class:`SpecialOrderFromInvoiceLinePayload` instead."""

    model_config = ConfigDict(extra="forbid")

    catalog_item_id: int
    size_label: str = Field(min_length=1, max_length=40)
    invoice_line_item_id: int | None = None
    status: _Status = "needed"
    eta_date: date | None = None
    vendor_order_number: str | None = Field(default=None, max_length=120)
    internal_notes: str | None = None


class SpecialOrderFromInvoiceLinePayload(BaseModel):
    """Create a special order from an existing catalog-backed invoice
    line. The service copies catalog_item_id + size_label so the two
    surfaces cannot disagree."""

    model_config = ConfigDict(extra="forbid")

    invoice_line_item_id: int
    status: _Status = "needed"
    eta_date: date | None = None
    vendor_order_number: str | None = Field(default=None, max_length=120)
    internal_notes: str | None = None


class SpecialOrderPatchPayload(BaseModel):
    """Metadata update. Lifecycle transitions go through the
    ``mark_*`` POST routes so timestamp side-effects stay tight."""

    model_config = ConfigDict(extra="forbid")

    size_label: str | None = Field(default=None, min_length=1, max_length=40)
    eta_date: date | None = None
    vendor_order_number: str | None = Field(default=None, max_length=120)
    internal_notes: str | None = None


class TransitionPayload(BaseModel):
    """Optional metadata accepted alongside a lifecycle transition.
    All fields are optional; the service only updates what's set."""

    model_config = ConfigDict(extra="forbid")

    eta_date: date | None = None
    vendor_order_number: str | None = Field(default=None, max_length=120)
    when: datetime | None = None


class CatalogSnapshotResponse(BaseModel):
    id: int
    internal_sku: str
    public_code: str
    designer: str | None
    style_number: str | None
    color: str
    house_name: str | None
    category: str
    product_title: str | None


class SpecialOrderResponse(BaseModel):
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
    catalog: CatalogSnapshotResponse | None


class SpecialOrderListResponse(BaseModel):
    special_orders: list[SpecialOrderResponse]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


_ERROR_STATUS: dict[str, int] = {
    "event_not_found": 404,
    "catalog_item_not_found": 404,
    "invoice_line_not_found": 404,
    "special_order_not_found": 404,
    "catalog_item_inactive": 422,
    "invoice_line_not_catalog_backed": 422,
    "invoice_line_size_required": 422,
    "invoice_line_event_missing": 422,
    "invoice_line_catalog_mismatch": 422,
    "invoice_line_event_mismatch": 422,
    "invoice_line_size_mismatch": 422,
    "invalid_status": 422,
    "invalid_initial_status": 422,
    "invalid_transition": 422,
    "size_label_required": 422,
    "unknown_fields": 422,
}


def _raise_for(exc: SpecialOrderError) -> None:
    status = _ERROR_STATUS.get(exc.code, 400)
    detail: dict[str, object] = {"code": exc.code}
    if exc.extra:
        detail.update(exc.extra)
    raise HTTPException(status_code=status, detail=detail) from exc


def _to_response(view) -> SpecialOrderResponse:
    return SpecialOrderResponse(
        id=view.id,
        event_id=view.event_id,
        invoice_line_item_id=view.invoice_line_item_id,
        catalog_item_id=view.catalog_item_id,
        size_label=view.size_label,
        status=view.status,
        ordered_at=view.ordered_at,
        eta_date=view.eta_date,
        received_at=view.received_at,
        picked_up_at=view.picked_up_at,
        vendor_order_number=view.vendor_order_number,
        internal_notes=view.internal_notes,
        created_at=view.created_at,
        updated_at=view.updated_at,
        catalog=(
            CatalogSnapshotResponse(
                id=view.catalog.id,
                internal_sku=view.catalog.internal_sku,
                public_code=view.catalog.public_code,
                designer=view.catalog.designer,
                style_number=view.catalog.style_number,
                color=view.catalog.color,
                house_name=view.catalog.house_name,
                category=view.catalog.category,
                product_title=view.catalog.product_title,
            )
            if view.catalog
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Event-scoped routes
# ---------------------------------------------------------------------------


@event_special_orders_router.get(
    "/{event_id}/special-orders",
    response_model=SpecialOrderListResponse,
)
def list_event_special_orders(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    include_terminal: bool = Query(
        default=True,
        description=(
            "Include picked_up and cancelled rows in the listing. "
            "Default true so the event detail screen shows the full "
            "lifecycle history; the dashboard 'open orders' widget "
            "passes false."
        ),
    ),
) -> SpecialOrderListResponse:
    rows = special_order_service.list_for_event(
        db, event_id=event_id, include_terminal=include_terminal
    )
    return SpecialOrderListResponse(
        special_orders=[_to_response(r) for r in rows]
    )


@event_special_orders_router.post(
    "/{event_id}/special-orders",
    response_model=SpecialOrderResponse,
    status_code=201,
)
def create_event_special_order(
    event_id: int,
    payload: SpecialOrderCreatePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    try:
        row = special_order_service.create_special_order(
            db,
            CreateSpecialOrderInput(
                event_id=event_id,
                catalog_item_id=payload.catalog_item_id,
                size_label=payload.size_label,
                invoice_line_item_id=payload.invoice_line_item_id,
                status=payload.status,
                eta_date=payload.eta_date,
                vendor_order_number=payload.vendor_order_number,
                internal_notes=payload.internal_notes,
            ),
        )
        db.commit()
        log.info(
            "special_order.created",
            extra={
                "user_id": user.id,
                "event_id": event_id,
                "special_order_id": row.id,
                "catalog_item_id": payload.catalog_item_id,
                "status": row.status,
            },
        )
    except SpecialOrderError as exc:
        db.rollback()
        _raise_for(exc)

    return _to_response(special_order_service.get_special_order(db, row.id))


@event_special_orders_router.post(
    "/{event_id}/special-orders/from-invoice-line",
    response_model=SpecialOrderResponse,
    status_code=201,
)
def create_special_order_from_invoice_line(
    event_id: int,
    payload: SpecialOrderFromInvoiceLinePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    """Convenience endpoint for the invoice editor's "mark as
    order-needed" action. The service validates that the invoice
    line belongs to the event in the URL by walking through the
    catalog item; the explicit ``event_id`` in the URL also lets the
    router confirm the right event before any write happens."""
    try:
        row = special_order_service.create_from_invoice_line(
            db,
            invoice_line_item_id=payload.invoice_line_item_id,
            status=payload.status,
            eta_date=payload.eta_date,
            vendor_order_number=payload.vendor_order_number,
            internal_notes=payload.internal_notes,
        )
        if int(row.event_id) != int(event_id):
            # The URL says one event, the invoice line lives on
            # another. Refuse before commit so the row never lands
            # under the wrong event.
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "event_mismatch",
                    "url_event_id": int(event_id),
                    "line_event_id": int(row.event_id),
                },
            )
        db.commit()
        log.info(
            "special_order.from_invoice_line",
            extra={
                "user_id": user.id,
                "event_id": event_id,
                "special_order_id": row.id,
                "invoice_line_item_id": payload.invoice_line_item_id,
                "status": row.status,
            },
        )
    except SpecialOrderError as exc:
        db.rollback()
        _raise_for(exc)

    return _to_response(special_order_service.get_special_order(db, row.id))


# ---------------------------------------------------------------------------
# Per-row routes
# ---------------------------------------------------------------------------


@special_orders_router.get(
    "/{special_order_id}",
    response_model=SpecialOrderResponse,
)
def get_special_order(
    special_order_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    try:
        return _to_response(
            special_order_service.get_special_order(db, special_order_id)
        )
    except SpecialOrderError as exc:
        _raise_for(exc)


@special_orders_router.patch(
    "/{special_order_id}",
    response_model=SpecialOrderResponse,
)
def patch_special_order_route(
    special_order_id: int,
    payload: SpecialOrderPatchPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    raw = payload.model_dump(exclude_unset=True)
    try:
        special_order_service.patch_special_order(
            db, special_order_id=special_order_id, patch=raw
        )
        db.commit()
        log.info(
            "special_order.patched",
            extra={
                "user_id": user.id,
                "special_order_id": special_order_id,
                "fields": sorted(raw.keys()),
            },
        )
    except SpecialOrderError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(
        special_order_service.get_special_order(db, special_order_id)
    )


def _handle_transition(
    db: Session,
    user: User,
    fn,
    *,
    special_order_id: int,
    event_label: str,
    **kwargs,
) -> SpecialOrderResponse:
    try:
        row = fn(db, special_order_id=special_order_id, **kwargs)
        db.commit()
        log.info(
            f"special_order.{event_label}",
            extra={
                "user_id": user.id,
                "special_order_id": special_order_id,
                "to_status": row.status,
            },
        )
    except SpecialOrderError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(
        special_order_service.get_special_order(db, special_order_id)
    )


@special_orders_router.post(
    "/{special_order_id}/mark-ordered",
    response_model=SpecialOrderResponse,
)
def mark_ordered_route(
    special_order_id: int,
    payload: TransitionPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    return _handle_transition(
        db, user, special_order_service.mark_ordered,
        special_order_id=special_order_id,
        event_label="marked_ordered",
        eta_date=payload.eta_date,
        vendor_order_number=payload.vendor_order_number,
        when=payload.when,
    )


@special_orders_router.post(
    "/{special_order_id}/mark-delayed",
    response_model=SpecialOrderResponse,
)
def mark_delayed_route(
    special_order_id: int,
    payload: TransitionPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    return _handle_transition(
        db, user, special_order_service.mark_delayed,
        special_order_id=special_order_id,
        event_label="marked_delayed",
        eta_date=payload.eta_date,
    )


@special_orders_router.post(
    "/{special_order_id}/mark-received",
    response_model=SpecialOrderResponse,
)
def mark_received_route(
    special_order_id: int,
    payload: TransitionPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    return _handle_transition(
        db, user, special_order_service.mark_received,
        special_order_id=special_order_id,
        event_label="marked_received",
        when=payload.when,
    )


@special_orders_router.post(
    "/{special_order_id}/mark-picked-up",
    response_model=SpecialOrderResponse,
)
def mark_picked_up_route(
    special_order_id: int,
    payload: TransitionPayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    return _handle_transition(
        db, user, special_order_service.mark_picked_up,
        special_order_id=special_order_id,
        event_label="marked_picked_up",
        when=payload.when,
    )


@special_orders_router.post(
    "/{special_order_id}/cancel",
    response_model=SpecialOrderResponse,
)
def cancel_special_order_route(
    special_order_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> SpecialOrderResponse:
    return _handle_transition(
        db, user, special_order_service.mark_cancelled,
        special_order_id=special_order_id,
        event_label="cancelled",
    )
