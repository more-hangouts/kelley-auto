"""Public site endpoints — Day 4.

Unauthenticated, CORS-allowed reads for the customer-facing marketing/sales
site. Mounted at ``/api/public``. This slice ships the vehicle inventory
contract (list + detail); the leads / posts / business-profile endpoints in
the Day 4 plan are separate follow-ups.

Every vehicle projection is the camelCase ``public_vehicle_dto`` allowlist —
no internal_sku / stock_number / wholesale / source / compat fields ever
reach the wire. Visibility gating (is_vehicle + active + status whitelist)
lives in services.public_inventory_service so the router stays thin.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from services import public_inventory_service as inventory
from services.public_inventory_service import InventoryFilters

router = APIRouter()


class InventoryListResponse(BaseModel):
    # `items` is a list of public_vehicle_dto dicts. We deliberately do NOT
    # pin a per-item model here: the DTO in catalog_service is the single
    # source of the public contract (and asserts no forbidden keys), so a
    # second schema would just be a copy to drift out of sync.
    items: list[dict[str, Any]]
    total: int
    page: int
    limit: int


@router.get("/inventory", response_model=InventoryListResponse)
def list_inventory(
    db: Annotated[Session, Depends(get_db)],
    make: str | None = None,
    model: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    drivetrain: str | None = None,
    min_price: int | None = Query(default=None, ge=0, description="Whole USD."),
    max_price: int | None = Query(default=None, ge=0, description="Whole USD."),
    min_year: int | None = Query(default=None, ge=1980),
    max_year: int | None = Query(default=None, ge=1980),
    max_mileage: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=120),
    status: str | None = Query(
        default=None,
        description="Public list status filter: 'available' (default) or "
        "'pending'. Other values fall back to the default.",
    ),
    sort: str = Query(default=inventory.DEFAULT_SORT),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=inventory.DEFAULT_LIMIT, ge=1, le=inventory.MAX_LIMIT),
) -> InventoryListResponse:
    """Public, paginated vehicle list. Defaults to in-stock (``available``)
    cars sorted newest-first; hides sold/delivered/hidden/wholesale and any
    non-vehicle or inactive row."""
    if sort not in inventory.SORT_KEYS:
        sort = inventory.DEFAULT_SORT
    # Whole-dollar price params -> cents (the DTO/storage unit).
    filters = InventoryFilters(
        make=make,
        model=model,
        body_type=body_type,
        fuel_type=fuel_type,
        transmission=transmission,
        drivetrain=drivetrain,
        min_price_cents=min_price * 100 if min_price is not None else None,
        max_price_cents=max_price * 100 if max_price is not None else None,
        min_year=min_year,
        max_year=max_year,
        max_mileage=max_mileage,
        q=q,
        status=status,
        sort=sort,
        page=page,
        limit=limit,
    )
    items, total = inventory.list_public_inventory(db, filters)
    return InventoryListResponse(items=items, total=total, page=page, limit=limit)


@router.get("/inventory/{id_or_listing_code}")
def get_inventory_item(
    id_or_listing_code: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Public vehicle detail by numeric id or listingCode (public_code).

    Serves available/pending/sold/delivered; 404 for hidden/wholesale/
    inactive/non-vehicle/unknown."""
    dto = inventory.get_public_vehicle(db, id_or_listing_code)
    if dto is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")
    return dto
