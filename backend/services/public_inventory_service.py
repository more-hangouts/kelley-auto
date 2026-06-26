"""Public (unauthenticated) vehicle inventory reads — Day 4.

The customer-facing site reads cars through here. Every projection goes
through :func:`services.catalog_service.public_vehicle_dto`, an allowlist
that excludes `internal_sku`, `stock_number`, `wholesale_*`, the compat
columns (`designer`/`style_number`/`color`), and all `source_*` scrape
metadata — so an internal field can never leak even if a query selected it.

Visibility gating (three layers, all required):
  * ``is_vehicle = true`` — the hard inventory boundary; a dress/catalog row
    can never surface here.
  * ``active = true`` — `catalog_items` has no `deleted_at`; `active=false`
    is its soft-delete / "pull from the site" switch.
  * a status whitelist — different for list vs detail (see below).

List shows for-sale cars only (default ``available``; ``pending`` allowed on
request). Detail also serves ``sold``/``delivered`` so a shared/indexed link
to a just-sold car still resolves, but ``hidden``/``wholesale`` (and anything
failing the first two gates) 404.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import CatalogItem
from services.catalog_service import public_vehicle_dto

# Statuses a car may appear in on the public LIST. 'available' is the
# default; 'pending' (deal in progress, still shown) is allowed when asked
# for explicitly. sold/delivered/hidden/wholesale never list.
PUBLIC_LIST_STATUSES: tuple[str, ...] = ("available", "pending")
DEFAULT_LIST_STATUS = "available"

# Statuses whose DETAIL page is public. A sold/delivered car keeps a live
# detail page (shared links, search indexing); hidden/wholesale do not.
PUBLIC_DETAIL_STATUSES: frozenset[str] = frozenset(
    {"available", "pending", "sold", "delivered"}
)

# Allowed sort keys -> SQLAlchemy order_by, NULLs always last so missing
# prices/mileage never crowd the top of a sorted page.
_SORTS = {
    "newest": lambda: CatalogItem.created_at.desc(),
    "oldest": lambda: CatalogItem.created_at.asc(),
    "price_asc": lambda: CatalogItem.unit_price_cents.asc().nulls_last(),
    "price_desc": lambda: CatalogItem.unit_price_cents.desc().nulls_last(),
    "year_desc": lambda: CatalogItem.year.desc().nulls_last(),
    "year_asc": lambda: CatalogItem.year.asc().nulls_last(),
    "mileage_asc": lambda: CatalogItem.mileage.asc().nulls_last(),
}
DEFAULT_SORT = "newest"
SORT_KEYS: frozenset[str] = frozenset(_SORTS)

MAX_LIMIT = 60
DEFAULT_LIMIT = 24


@dataclass
class InventoryFilters:
    make: str | None = None
    model: str | None = None
    body_type: str | None = None
    fuel_type: str | None = None
    transmission: str | None = None
    drivetrain: str | None = None
    min_price_cents: int | None = None
    max_price_cents: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    max_mileage: int | None = None
    q: str | None = None
    status: str | None = None  # one of PUBLIC_LIST_STATUSES; default available
    sort: str = DEFAULT_SORT
    page: int = 1
    limit: int = DEFAULT_LIMIT
    # Internal: which statuses to include. Defaults to the requested single
    # status (or the default). Kept as a field so a future staff caller could
    # widen it without going through the public param parsing.
    _statuses: tuple[str, ...] = field(default_factory=tuple)


def _public_base():
    """Vehicles that clear the two non-status gates: real car + active."""
    return select(CatalogItem).where(
        CatalogItem.is_vehicle.is_(True),
        CatalogItem.active.is_(True),
    )


def _ci_equals(column, value: str):
    # Case-insensitive exact match for facet filters (make=honda finds
    # 'Honda'). Trimmed so a trailing space from a query string still hits.
    return func.lower(column) == value.strip().lower()


def _apply_filters(stmt, f: InventoryFilters):
    if f.make:
        stmt = stmt.where(_ci_equals(CatalogItem.make, f.make))
    if f.model:
        stmt = stmt.where(_ci_equals(CatalogItem.model, f.model))
    if f.body_type:
        stmt = stmt.where(_ci_equals(CatalogItem.body_type, f.body_type))
    if f.fuel_type:
        stmt = stmt.where(_ci_equals(CatalogItem.fuel_type, f.fuel_type))
    if f.transmission:
        stmt = stmt.where(_ci_equals(CatalogItem.transmission, f.transmission))
    if f.drivetrain:
        stmt = stmt.where(_ci_equals(CatalogItem.drivetrain, f.drivetrain))
    if f.min_price_cents is not None:
        stmt = stmt.where(CatalogItem.unit_price_cents >= f.min_price_cents)
    if f.max_price_cents is not None:
        stmt = stmt.where(CatalogItem.unit_price_cents <= f.max_price_cents)
    if f.min_year is not None:
        stmt = stmt.where(CatalogItem.year >= f.min_year)
    if f.max_year is not None:
        stmt = stmt.where(CatalogItem.year <= f.max_year)
    if f.max_mileage is not None:
        stmt = stmt.where(CatalogItem.mileage <= f.max_mileage)
    if f.q and f.q.strip():
        like = f"%{f.q.strip()}%"
        stmt = stmt.where(
            or_(
                CatalogItem.make.ilike(like),
                CatalogItem.model.ilike(like),
                CatalogItem.trim.ilike(like),
                CatalogItem.product_title.ilike(like),
                CatalogItem.body_type.ilike(like),
                CatalogItem.exterior_color.ilike(like),
            )
        )
    return stmt


def _resolve_list_statuses(f: InventoryFilters) -> tuple[str, ...]:
    if f._statuses:
        return f._statuses
    if f.status and f.status in PUBLIC_LIST_STATUSES:
        return (f.status,)
    return (DEFAULT_LIST_STATUS,)


def list_public_inventory(
    db: Session, filters: InventoryFilters
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(items, total)`` — a page of public vehicle DTOs plus the
    unpaginated count for the caller's pager. ``total`` reflects every
    filter EXCEPT page/limit.
    """
    statuses = _resolve_list_statuses(filters)
    page = max(1, filters.page)
    limit = max(1, min(MAX_LIMIT, filters.limit))

    base = _public_base().where(CatalogItem.vehicle_status.in_(statuses))
    base = _apply_filters(base, filters)

    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    order = _SORTS.get(filters.sort, _SORTS[DEFAULT_SORT])()
    # Stable tiebreak on id so equal sort keys page deterministically.
    rows = db.execute(
        base.order_by(order, CatalogItem.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    ).scalars().all()

    return [public_vehicle_dto(item) for item in rows], int(total)


def get_public_vehicle(
    db: Session, id_or_listing_code: str
) -> dict[str, Any] | None:
    """Resolve one car for a public detail page, or ``None`` (-> 404).

    ``id_or_listing_code`` is the numeric id (all digits) or the
    ``listingCode`` (public_code, e.g. ``BVX-00042``, matched case-
    insensitively). Returns the DTO only if the row is a vehicle, active,
    and in a publicly viewable status; otherwise ``None``.
    """
    token = (id_or_listing_code or "").strip()
    if not token:
        return None

    stmt = _public_base().where(
        CatalogItem.vehicle_status.in_(PUBLIC_DETAIL_STATUSES)
    )
    if token.isdigit():
        stmt = stmt.where(CatalogItem.id == int(token))
    else:
        stmt = stmt.where(func.upper(CatalogItem.public_code) == token.upper())

    item = db.execute(stmt).scalars().first()
    return public_vehicle_dto(item) if item is not None else None
