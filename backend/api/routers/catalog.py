"""Staff catalog API.

Phase 1 admin surface for catalog creation and lookup. Customer-facing
documents must use the helpers in `services.catalog_service`; this router
is staff-only and may expose internal SKU/search fields.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from database.connection import get_db
from database.models import CatalogItem, User
from services.pricing import price_breakdown
from services.catalog_service import (
    CATEGORY_GROUPS,
    CatalogItemInput,
    CatalogServiceError,
    create_catalog_item,
    find_catalog_items,
    get_by_internal_sku,
    get_by_public_code,
    list_catalog_designers,
    search_catalog,
    update_catalog_item,
)

router = APIRouter()

# Catalog reads (GET) are dual-scope: sales staff are still staff and
# search by the same fields admins do (designer, style number, public
# code, color). The SKU-obfuscation policy applies to customer-facing
# surfaces and to activity-log payloads, NOT to staff reads. Writes
# (POST / PATCH) stay `require_admin_scope` — sales never authors
# catalog rows.


class CatalogItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_sku: str = Field(min_length=1, max_length=160)
    color: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    designer: str | None = Field(default=None, max_length=120)
    style_number: str | None = Field(default=None, max_length=80)
    house_name: str | None = Field(default=None, max_length=120)
    product_title: str | None = Field(default=None, max_length=200)
    description_text: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    source_platform: str | None = Field(default=None, max_length=40)
    source_product_id: str | None = Field(default=None, max_length=80)
    source_product_handle: str | None = Field(default=None, max_length=160)
    source_product_url: str | None = None
    source_collection_url: str | None = None
    source_product_type: str | None = Field(default=None, max_length=120)
    is_sample: bool = False
    active: bool = True
    unit_price_cents: int | None = Field(default=None, ge=0)

    def to_input(self) -> CatalogItemInput:
        return CatalogItemInput(**self.model_dump())


class CatalogItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    internal_sku: str
    public_code: str
    designer: str | None
    style_number: str | None
    color: str
    house_name: str | None
    product_title: str | None
    category: str
    description_text: str | None
    image_urls: list[str]
    source_platform: str | None
    source_product_id: str | None
    source_product_handle: str | None
    source_product_url: str | None
    source_collection_url: str | None
    source_product_type: str | None
    is_sample: bool
    active: bool
    unit_price_cents: int | None


class PriceBreakdownItem(BaseModel):
    key: str
    label: str
    removable: bool
    deduct_cents: int


class PriceBreakdownResponse(BaseModel):
    """Customer-facing price decomposition for the catalog detail view.

    Carries only derived prices and the package contents — never the
    wholesale cost or the multiplier (those stay backend-only).
    """

    catalog_item_id: int
    package_price_cents: int | None
    dress_only_price_cents: int | None
    items: list[PriceBreakdownItem]
    discretionary_discount_max_percent: float


def _item_or_404(item: CatalogItem | None) -> CatalogItem:
    if item is None:
        raise HTTPException(status_code=404, detail="catalog_item_not_found")
    return item


@router.post("", response_model=CatalogItemResponse, status_code=201)
def create_catalog_item_route(
    payload: CatalogItemCreate,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> CatalogItem:
    try:
        item = create_catalog_item(db, payload.to_input())
        db.commit()
        db.refresh(item)
        return item
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="catalog_item_conflict") from exc


@router.get("", response_model=list[CatalogItemResponse])
def list_catalog_items_route(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    q: str | None = Query(default=None, max_length=200),
    designer: str | None = None,
    active_only: bool | None = None,
    include_inactive: bool = False,
    is_sample: bool | None = Query(
        default=None,
        description=(
            "Phase 6 sample filter. True = floor samples only, False = "
            "non-samples only, omitted = both. v1 keeps samples as a "
            "single boolean; reservations and stock counts are out of "
            "scope."
        ),
    ),
    group: str | None = Query(
        default=None,
        description=(
            "UI category bucket: 'dress', 'accessory', or 'addon'. "
            "'dress' expands to the three gown enum values, 'accessory' "
            "to the accessory enum value, 'addon' to 'service'. Unknown "
            "values return an empty list."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[CatalogItem]:
    """Phase 3 picker entry point + Phase 6 sample filter.

    Two query modes:

    - ``q=<term>``: ranked multi-column search across
      ``internal_sku``, ``public_code``, ``designer``, ``style_number``,
      ``color``, ``house_name``, and ``product_title``. Active-only by
      default; pass ``include_inactive=true`` to surface retired rows.
    - No ``q``: simple listing. ``designer`` filter and the legacy
      ``active_only`` flag still work for admin browsing.

    ``is_sample`` filters orthogonally to either mode: pass ``true`` to
    show only the rows flagged as floor samples (admin "samples" view
    or staff picker "we have it on the floor" filter), ``false`` to
    hide samples, omit to include both.

    ``active_only`` is preserved for back-compat with the Phase 1
    listing API; new callers should use ``include_inactive`` instead.
    When both are supplied, ``include_inactive=true`` wins so a single
    flag can flip the picker open without the caller having to clear
    ``active_only``.
    """
    if active_only is None:
        active_only_resolved = not include_inactive
    else:
        active_only_resolved = active_only and not include_inactive

    categories: tuple[str, ...] | None = None
    if group:
        if group not in CATEGORY_GROUPS:
            # Unknown bucket — return [] rather than fall through to
            # an unfiltered listing. Empty tuples are falsy and would
            # be ignored by the service guard.
            return []
        categories = CATEGORY_GROUPS[group]

    if q and q.strip():
        return search_catalog(
            db,
            q=q,
            include_inactive=not active_only_resolved,
            is_sample=is_sample,
            categories=categories,
            designer=designer,
            limit=limit,
        )
    return find_catalog_items(
        db,
        designer=designer,
        active_only=active_only_resolved,
        is_sample=is_sample,
        categories=categories,
        limit=limit,
    )


class CatalogDesignerResponse(BaseModel):
    designer: str
    count: int


@router.get("/designers", response_model=list[CatalogDesignerResponse])
def list_catalog_designers_route(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> list[dict[str, object]]:
    """Distinct designers + counts for the admin Products vendor filter.

    Declared ahead of the ``/{catalog_item_id}`` catch-all so the literal
    ``/designers`` path is never shadowed by the int-id route.
    """
    return [
        {"designer": designer, "count": count}
        for designer, count in list_catalog_designers(db)
    ]


class CatalogItemPatch(BaseModel):
    """Admin-only partial update. ``internal_sku`` and ``public_code``
    are intentionally absent: the service rejects them with
    ``internal_sku_immutable`` / ``public_code_immutable`` to keep
    references on existing invoice/quote/special-order rows stable
    and to honor the "public code is immutable once issued" rule.
    """

    model_config = ConfigDict(extra="forbid")

    designer: str | None = Field(default=None, max_length=120)
    style_number: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, min_length=1, max_length=80)
    house_name: str | None = Field(default=None, max_length=120)
    product_title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=40)
    description_text: str | None = None
    image_urls: list[str] | None = None
    source_platform: str | None = Field(default=None, max_length=40)
    source_product_id: str | None = Field(default=None, max_length=80)
    source_product_handle: str | None = Field(default=None, max_length=160)
    source_product_url: str | None = None
    source_collection_url: str | None = None
    source_product_type: str | None = Field(default=None, max_length=120)
    is_sample: bool | None = None
    active: bool | None = None
    unit_price_cents: int | None = Field(default=None, ge=0)


_PATCH_ERROR_STATUS: dict[str, int] = {
    "catalog_item_not_found": 404,
    "internal_sku_immutable": 422,
    "public_code_immutable": 422,
    "unknown_fields": 422,
    "catalog_field_required": 422,
    "catalog_category_invalid": 422,
    "image_urls_invalid": 422,
    "unit_price_cents_invalid": 422,
    "unit_price_cents_negative": 422,
}


@router.patch("/{catalog_item_id}", response_model=CatalogItemResponse)
def patch_catalog_item_route(
    catalog_item_id: int,
    payload: CatalogItemPatch,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> CatalogItem:
    """Admin patch. Phase 6 ships this so the staff UI can flip
    ``is_sample`` / ``active`` (and fix typos in display fields)
    without raw SQL. ``internal_sku`` and ``public_code`` are
    rejected by the service."""
    raw = payload.model_dump(exclude_unset=True)
    if not raw:
        # Empty PATCH is a no-op — avoid bumping updated_at for nothing.
        item = db.get(CatalogItem, catalog_item_id)
        if item is None:
            raise HTTPException(
                status_code=404, detail="catalog_item_not_found"
            )
        return item
    try:
        item = update_catalog_item(
            db, catalog_item_id=catalog_item_id, patch=raw
        )
        db.commit()
        db.refresh(item)
        return item
    except CatalogServiceError as exc:
        db.rollback()
        status = _PATCH_ERROR_STATUS.get(exc.code, 400)
        detail: dict[str, object] = {"code": exc.code}
        if exc.extra:
            detail.update(exc.extra)
        raise HTTPException(status_code=status, detail=detail) from exc


@router.get("/by-internal-sku/{internal_sku}", response_model=CatalogItemResponse)
def get_catalog_item_by_internal_sku_route(
    internal_sku: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> CatalogItem:
    return _item_or_404(get_by_internal_sku(db, internal_sku))


@router.get("/by-public-code/{public_code}", response_model=CatalogItemResponse)
def get_catalog_item_by_public_code_route(
    public_code: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> CatalogItem:
    return _item_or_404(get_by_public_code(db, public_code))


@router.get("/{catalog_item_id}", response_model=CatalogItemResponse)
def get_catalog_item_route(
    catalog_item_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> CatalogItem:
    return _item_or_404(db.get(CatalogItem, catalog_item_id))


@router.get(
    "/{catalog_item_id}/price-breakdown",
    response_model=PriceBreakdownResponse,
)
def get_catalog_item_price_breakdown_route(
    catalog_item_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> PriceBreakdownResponse:
    """Price decomposition for the detail view: package vs dress-only and
    what each removable package item is worth. Computed from wholesale via
    services.pricing but exposes only the derived prices."""
    item = _item_or_404(db.get(CatalogItem, catalog_item_id))
    breakdown = price_breakdown(
        item.wholesale_cents, package_price_cents=item.unit_price_cents
    )
    return PriceBreakdownResponse(catalog_item_id=item.id, **breakdown)
