"""Catalog domain service.

Owns CRUD over `catalog_items` and the public-code minting flow. Two
identifier semantics:

  - `internal_sku`: real designer SKU (e.g. `MORI-4080000-BLACK-RED-ROSE`)
    staff types into search/admin/reorder views. Never returned from
    public/customer-facing endpoints.

  - `public_code`: opaque Bellas-only code (`BVX-NNNNN`) minted here
    under a `SELECT ... FOR UPDATE` row lock on the `numbering_state`
    singleton, the same row that already serializes invoice/quote/payment
    numbering. Vendor identity is intentionally not encoded; one global
    sequence covers every vendor.

Phase 4 customer renderers consume only the helpers in this module
(`customer_sku`, `customer_line_description`); they never read
`internal_sku`, `designer`, or `style_number` from a catalog row.

Public-code immutability is enforced at the service layer in v1: this
module never UPDATEs `public_code`. Phase 7 adds a DB trigger as
belt-and-suspenders so a future migration script or admin SQL session
cannot quietly rewrite codes already on issued invoices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from database.models import CatalogItem


class CatalogServiceError(Exception):
    """Domain-level rejection; routers map this to 4xx."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "catalog_error",
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class CatalogItemInput:
    """Shape accepted by `create_catalog_item`. Mirrors the seed JSON
    closely so the Morilee importer can pass through, but the same shape
    serves admin UI creation.

    `public_code` is intentionally absent: the service mints it. Callers
    that try to set it are bypassing the obfuscation contract.
    """

    internal_sku: str
    color: str
    category: str
    designer: str | None = None
    style_number: str | None = None
    house_name: str | None = None
    product_title: str | None = None
    description_text: str | None = None
    image_urls: list[str] = field(default_factory=list)
    source_platform: str | None = None
    source_product_id: str | None = None
    source_product_handle: str | None = None
    source_product_url: str | None = None
    source_collection_url: str | None = None
    source_product_type: str | None = None
    is_sample: bool = False
    active: bool = True
    unit_price_cents: int | None = None


# ---------------------------------------------------------------------------
# Customer-safe helpers (Phase 4 renderers consume these only)
# ---------------------------------------------------------------------------


_CATEGORY_LABELS = {
    "quince_gown": "Quince gown",
    "bridal_gown": "Bridal gown",
    "formal_gown": "Formal gown",
    "accessory": "Accessory",
    "service": "Alteration",
}

# UI grouping for the admin catalog page and the editor picker tabs.
# Staff think in three buckets — dresses, accessories, add-ons — but
# the customer-copy renderer in `customer_line_description` still needs
# the three gown variants to label rows correctly when `house_name` is
# unset. The DB enum stays at five values; this map only widens the
# search filter when staff tap "Dress" in the picker.
CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "dress": ("quince_gown", "bridal_gown", "formal_gown"),
    "accessory": ("accessory",),
    "addon": ("service",),
}

FORBIDDEN_PUBLIC_RENDER_KEYS = frozenset(
    {
        "internal_sku",
        "designer",
        "style_number",
        "internal_notes",
        "product_key",
        "notes",
        "private_notes",
        "staff_notes",
        "transaction_reference",
        "rejection_reason",
        "cancellation_reason",
    }
)


def staff_sku(item: CatalogItem) -> str:
    """Internal SKU for staff surfaces. Do not call from customer
    templates."""
    return item.internal_sku


def customer_sku(item: CatalogItem) -> str:
    """Public BVX code for customer surfaces. Safe to render anywhere
    customer-facing."""
    return item.public_code


def assert_no_catalog_leak(
    item: CatalogItem,
    value: str | None,
    *,
    field_name: str,
) -> None:
    """Reject a public-facing value that contains the catalog row's
    ``internal_sku``, ``designer``, or ``style_number`` as a case-
    insensitive substring.

    Phase 2 ships this guard for line-item writes so a staff-typed
    ``public_description`` cannot leak the matched catalog row's vendor
    SKU even if the API layer's reject-on-catalog-backed check is ever
    bypassed. Phase 7 widens the sweep across every public-facing field
    and DTO; Phase 2 closes the obvious hole during the staff-typing
    window between schema ship and full hardening.

    The guard is intentionally noisy: it raises ``CatalogServiceError``
    rather than silently sanitizing, because silent sanitization makes
    the leak attempt invisible to operators reviewing logs.
    """
    if not value:
        return
    haystack = value.lower()
    for ident_attr in ("internal_sku", "designer", "style_number"):
        ident = getattr(item, ident_attr, None)
        if ident and ident.lower() in haystack:
            raise CatalogServiceError(
                f"{field_name} contains catalog identifier "
                f"({ident_attr}); use customer_line_description instead "
                "of staff-entered text on catalog-backed lines",
                code="catalog_leak",
                field=field_name,
                identifier_kind=ident_attr,
            )


def assert_no_public_catalog_leaks(
    db: Session,
    values: dict[str, str | None],
) -> None:
    """Reject customer-facing free text that contains any known catalog
    identifier.

    Phase 2 guarded catalog-backed line items against the matched
    catalog row. Phase 7 broadens the same rule to invoice/quote
    public notes, terms, footers, and terminal-status reasons. The
    service raises instead of sanitizing so staff move sensitive text
    into private/internal fields deliberately.
    """
    public_values = {
        field_name: value
        for field_name, value in values.items()
        if isinstance(value, str) and value.strip()
    }
    if not public_values:
        return

    rows = (
        db.query(
            CatalogItem.internal_sku,
            CatalogItem.designer,
            CatalogItem.style_number,
        )
        .all()
    )
    identifiers: list[tuple[str, str]] = []
    for row in rows:
        for ident_attr in ("internal_sku", "designer", "style_number"):
            ident = getattr(row, ident_attr, None)
            if ident is None and hasattr(row, "_mapping"):
                ident = row._mapping.get(ident_attr)
            if not ident:
                continue
            token = str(ident).strip()
            if len(token) >= 3:
                identifiers.append((ident_attr, token))

    for field_name, value in public_values.items():
        haystack = value.lower()
        for ident_attr, token in identifiers:
            if token.lower() in haystack:
                raise CatalogServiceError(
                    f"{field_name} contains catalog identifier "
                    f"({ident_attr}); move staff-only details to a "
                    "private/internal field",
                    code="catalog_leak",
                    field=field_name,
                    identifier_kind=ident_attr,
                )


def assert_public_render_keys(value: Any, *, path: str = "$") -> None:
    """Recursively assert that a customer-bound DTO/tree contains no
    forbidden public keys."""
    if is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            if f.name in FORBIDDEN_PUBLIC_RENDER_KEYS:
                raise CatalogServiceError(
                    f"forbidden public key {f.name} at {path}",
                    code="forbidden_public_key",
                    field=f.name,
                    path=path,
                )
            assert_public_render_keys(
                getattr(value, f.name), path=f"{path}.{f.name}"
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            key_str = str(key)
            if key_str in FORBIDDEN_PUBLIC_RENDER_KEYS:
                raise CatalogServiceError(
                    f"forbidden public key {key_str} at {path}",
                    code="forbidden_public_key",
                    field=key_str,
                    path=path,
                )
            assert_public_render_keys(child, path=f"{path}.{key_str}")
        return
    if isinstance(value, (list, tuple)):
        for idx, child in enumerate(value):
            assert_public_render_keys(child, path=f"{path}[{idx}]")


def public_render_dict(value: Any) -> dict[str, Any]:
    """Convert a customer-bound dataclass or dict to a dict after the
    public-key allowlist passes."""
    assert_public_render_keys(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise CatalogServiceError(
        "public_render_dict requires a dataclass or dict",
        code="public_render_invalid",
    )


@dataclass
class CustomerLineView:
    """Customer-safe projection of one invoice/quote line.

    Phase 4 routes every customer-facing surface (PDF partial, portal
    partial) through this projection so a single helper enforces the
    line-render rules in one place. Future surfaces — Stripe payloads,
    a customer-facing receipt route, etc. — pick this up instead of
    re-deriving from the row.

    Fields:
        public_code: BVX-NNNNN for catalog-backed lines; ``None`` for
            non-catalog and legacy lines so the renderer hides the
            SKU column on those rows.
        display_text: customer-safe one-liner. For catalog-backed
            lines this is ``customer_line_description``; for non-
            catalog lines it is ``public_description``; for legacy
            lines (no ``catalog_item_id`` and no
            ``public_description``) it falls back to the legacy
            ``description`` because that text is already on issued
            PDFs and portal pages in customers' hands.
        quantity / unit_price_cents / line_total_cents / kind: pass-
            throughs for the renderer math; staff-only fields are
            deliberately absent.

    The projection never includes staff-only catalog, line, or notes
    fields. Phase 7's lint asserts that customer templates only read
    fields on this dataclass.
    """

    public_code: str | None
    display_text: str
    quantity: Any
    unit_price_cents: int
    line_total_cents: int
    kind: str


def customer_line_view(line: Any, catalog: CatalogItem | None) -> CustomerLineView:
    """Project a raw ``invoice_line_items`` / ``quote_line_items`` row
    into the customer-safe view above.

    ``line`` is duck-typed: any object with ``catalog_item_id``,
    ``size_label``, customer copy text, ``quantity``,
    ``unit_price_cents``, ``line_total_cents``, and
    ``kind`` works (the SQLAlchemy ORM models, the
    ``invoice_service.LineItemView`` dataclass, and the
    ``quote_service.QuoteLineView`` dataclass all match this shape).

    ``catalog`` is the joined ``CatalogItem`` row when
    ``line.catalog_item_id`` is set. Pass ``None`` for non-catalog
    and legacy lines.

    Resolution priority for ``display_text``:

    1. Catalog-backed: ``customer_line_description(catalog, size_label)``.
    2. Non-catalog new line: ``line.public_description``.
    3. Legacy line: ``line.description`` (grandfathered).
    4. Defensive fallback: empty string.

    Legacy staff-only side text is never read on this code path.
    """
    if catalog is not None:
        return CustomerLineView(
            public_code=catalog.public_code,
            display_text=customer_line_description(
                catalog, size_label=getattr(line, "size_label", None)
            ),
            quantity=line.quantity,
            unit_price_cents=int(line.unit_price_cents),
            line_total_cents=int(line.line_total_cents),
            kind=line.kind,
        )
    public = getattr(line, "public_description", None)
    legacy = getattr(line, "description", None)
    text = public if public else (legacy or "")
    return CustomerLineView(
        public_code=None,
        display_text=text,
        quantity=line.quantity,
        unit_price_cents=int(line.unit_price_cents),
        line_total_cents=int(line.line_total_cents),
        kind=line.kind,
    )


def customer_line_description(
    item: CatalogItem,
    *,
    size_label: str | None = None,
) -> str:
    """Customer-safe one-line description.

    Format: `<label> / <color>`, plus ` / Size <N>` when a size is
    supplied. Separator is always ` / `; em dashes are forbidden in
    customer copy across this project, so the separator is part of the
    contract, not a stylistic choice.

    The label is `house_name` when the row has one, otherwise the
    category label. Designer name and style number are deliberately
    excluded from this output.
    """
    label = item.house_name or _CATEGORY_LABELS.get(item.category, "Item")
    parts = [label, item.color]
    if size_label:
        parts.append(f"Size {size_label}")
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# Public-code allocation
# ---------------------------------------------------------------------------


def _assign_catalog_public_code(db: Session) -> str:
    """Allocate the next public code under a row-level lock on
    `numbering_state.catalog_public_code_seq`. Format is
    `BVX-{seq:05d}`.

    No year reset: catalog public codes are stable identifiers and
    remain valid for the lifetime of the catalog row, even past year
    boundaries.

    Must run inside the same transaction as the catalog row INSERT so
    two concurrent callers cannot mint the same code. This is the
    contract `create_catalog_item` upholds; do not call this allocator
    standalone.
    """
    row = db.execute(
        sql_text(
            "SELECT catalog_public_code_seq FROM numbering_state "
            "WHERE id = 1 FOR UPDATE"
        )
    ).one()
    new_seq = int(row.catalog_public_code_seq) + 1
    db.execute(
        sql_text(
            "UPDATE numbering_state SET catalog_public_code_seq = :s, "
            "updated_at = NOW() WHERE id = 1"
        ),
        {"s": new_seq},
    )
    return f"BVX-{new_seq:05d}"


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create_catalog_item(db: Session, data: CatalogItemInput) -> CatalogItem:
    """Create one catalog row, minting a fresh `public_code` under the
    numbering lock.

    Caller owns the outer transaction. This function does `db.flush()`
    so the returned item has its `id` populated, but does not commit.
    The importer wraps a batch in one transaction; admin UI wraps a
    single create.

    Validation:
      - Duplicate `internal_sku` surfaces as `IntegrityError` from
        SQLAlchemy when the unique constraint fires; the router maps
        that to 409.
      - Category whitelist, public_code format, and image_urls array
        shape are enforced by the DB CHECK constraints in migration 041.

    `public_code` is set by the service, not by the caller. The
    `CatalogItemInput` dataclass intentionally omits the field.
    """
    public_code = _assign_catalog_public_code(db)
    item = CatalogItem(
        internal_sku=data.internal_sku,
        public_code=public_code,
        designer=data.designer,
        style_number=data.style_number,
        color=data.color,
        house_name=data.house_name,
        product_title=data.product_title,
        category=data.category,
        description_text=data.description_text,
        image_urls=list(data.image_urls),
        source_platform=data.source_platform,
        source_product_id=data.source_product_id,
        source_product_handle=data.source_product_handle,
        source_product_url=data.source_product_url,
        source_collection_url=data.source_collection_url,
        source_product_type=data.source_product_type,
        is_sample=data.is_sample,
        active=data.active,
        unit_price_cents=data.unit_price_cents,
    )
    db.add(item)
    db.flush()
    return item


# ---------------------------------------------------------------------------
# Reads (Phase 1 scaffolding; Phase 3 expands with the full search contract)
# ---------------------------------------------------------------------------


def find_catalog_items(
    db: Session,
    *,
    designer: str | None = None,
    active_only: bool = True,
    is_sample: bool | None = None,
    categories: tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[CatalogItem]:
    """Listing without a search term. Used by admin views that want to
    enumerate catalog items by exact attributes (designer filter,
    active toggle, sample toggle).

    ``is_sample`` semantics:
        ``None`` — no filter; both samples and non-samples returned
        ``True`` — only rows flagged as floor samples
        ``False`` — only rows NOT flagged as floor samples

    ``categories``: optional tuple of category enum values to restrict
    to (e.g. the three gown values for the Dress UI bucket). ``None``
    means no category filter. Unknown categories are silently ignored
    so a stale client cannot 500 the route; the SQL just returns no
    rows for that bucket.

    Phase 3 search (multi-column ranked) lives in
    :func:`search_catalog`; this helper is for exact-match admin
    browsing.
    """
    q = db.query(CatalogItem)
    if active_only:
        q = q.filter(CatalogItem.active.is_(True))
    if designer:
        q = q.filter(CatalogItem.designer == designer)
    if is_sample is True:
        q = q.filter(CatalogItem.is_sample.is_(True))
    elif is_sample is False:
        q = q.filter(CatalogItem.is_sample.is_(False))
    if categories:
        allowed = tuple(c for c in categories if c in _CATALOG_CATEGORIES)
        if allowed:
            q = q.filter(CatalogItem.category.in_(allowed))
        else:
            # Caller asked to filter by an unknown bucket; respond with
            # the empty set rather than fall through to "no filter".
            return []
    return q.order_by(CatalogItem.id).limit(limit).all()


def list_catalog_designers(db: Session) -> list[tuple[str, int]]:
    """Distinct designers with row counts, busiest first.

    Powers the admin Products vendor filter. Sourced from the DB (not
    a page of rows) so every vendor appears even once the catalog grows
    past the per-request limit. Null/blank designers are excluded.
    """
    rows = (
        db.query(CatalogItem.designer, func.count(CatalogItem.id))
        .filter(CatalogItem.designer.isnot(None))
        .filter(CatalogItem.designer != "")
        .group_by(CatalogItem.designer)
        .order_by(func.count(CatalogItem.id).desc(), CatalogItem.designer.asc())
        .all()
    )
    return [(designer, count) for designer, count in rows]


# ---------------------------------------------------------------------------
# Phase 3: staff line-item-picker search
# ---------------------------------------------------------------------------


def _normalize_term(term: str) -> str:
    """Lowercase, strip whitespace, and treat ``/`` and ``-`` as
    equivalent so ``"regal/royal"`` matches ``"regal-royal"`` and
    ``"MORI 4080000"`` matches ``"MORI-4080000"``.

    The same normalization runs on the catalog row's searchable
    columns inside the SQL expression, so the comparison is symmetric.
    """
    t = term.strip().lower()
    t = t.replace("/", "-")
    t = t.replace(" ", "-")
    while "--" in t:
        t = t.replace("--", "-")
    return t


def _normalize_column(col: Any) -> Any:
    """Apply the same normalization to a column expression as
    ``_normalize_term`` applies to the user term."""
    expr = func.lower(col)
    expr = func.replace(expr, "/", "-")
    expr = func.replace(expr, " ", "-")
    # Collapse runs of '-' into a single '-'. Postgres ``regexp_replace``
    # makes this one round-trip; Python loops only run on the term side.
    expr = func.regexp_replace(expr, "-+", "-", "g")
    return expr


# Match priority: exact internal_sku/public_code rank first, prefix
# matches second, substring matches third. The values are sortable
# integers so the SQL ORDER BY stays stable even when several lines
# tie.
_RANK_EXACT_ID = 0
_RANK_PREFIX_ID = 1
_RANK_EXACT_OTHER = 2
_RANK_PREFIX_OTHER = 3
_RANK_SUBSTRING = 4


# Columns the picker matches against. Order is significant for tie-
# break stability: identifier columns first, then descriptive columns.
_SEARCH_COLUMNS: tuple[Any, ...] = (
    CatalogItem.internal_sku,
    CatalogItem.public_code,
    CatalogItem.designer,
    CatalogItem.style_number,
    CatalogItem.color,
    CatalogItem.house_name,
    CatalogItem.product_title,
)
_IDENTIFIER_COLUMNS: tuple[Any, ...] = (
    CatalogItem.internal_sku,
    CatalogItem.public_code,
)


def search_catalog(
    db: Session,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    is_sample: bool | None = None,
    categories: tuple[str, ...] | None = None,
    designer: str | None = None,
    limit: int = 50,
) -> list[CatalogItem]:
    """Phase 3 line-item picker search.

    Matches ``q`` against ``internal_sku``, ``public_code``,
    ``designer``, ``style_number``, ``color``, ``house_name``, and
    ``product_title``. Ranking puts exact matches on the two identifier
    columns first (``internal_sku``, ``public_code``), prefix matches
    next, then substring matches. Normalization makes ``/`` and ``-``
    interchangeable and is case-insensitive.

    No ``q`` means "list active items" (the picker's idle state); the
    same call still honors ``include_inactive``.

    Performance: at v1 scale (low thousands of rows) ``ILIKE '%term%'``
    over the columns above is acceptable. Btree does not help leading-
    wildcard queries, so do not assume btree alone makes substring
    searches fast. If the catalog grows past ~50k rows, swap to a
    ``pg_trgm`` GIN index per the plan; the call shape stays the same.
    """
    base = db.query(CatalogItem)
    if not include_inactive:
        base = base.filter(CatalogItem.active.is_(True))
    if designer:
        base = base.filter(CatalogItem.designer == designer)
    if is_sample is True:
        base = base.filter(CatalogItem.is_sample.is_(True))
    elif is_sample is False:
        base = base.filter(CatalogItem.is_sample.is_(False))
    if categories:
        allowed = tuple(c for c in categories if c in _CATALOG_CATEGORIES)
        if not allowed:
            # Caller asked for an unknown bucket; return empty rather
            # than treat it as "no filter" and surface every row.
            return []
        base = base.filter(CatalogItem.category.in_(allowed))

    if not q or not q.strip():
        return (
            base.order_by(CatalogItem.id)
            .limit(min(int(limit), 500))
            .all()
        )

    term = _normalize_term(q)
    if not term:
        return (
            base.order_by(CatalogItem.id)
            .limit(min(int(limit), 500))
            .all()
        )

    # Build a CASE expression that ranks each row by where/how it
    # matched. Ranking lives in SQL so the LIMIT applied at the end
    # only chops the lowest-priority tail; doing it in Python after
    # over-fetching would either miss matches past the limit or read
    # the whole table.
    like_pattern = f"%{term}%"
    prefix_pattern = f"{term}%"

    rank_cases: list[tuple[Any, int]] = []
    match_predicates: list[Any] = []

    for col in _IDENTIFIER_COLUMNS:
        norm_col = _normalize_column(col)
        rank_cases.append((norm_col == term, _RANK_EXACT_ID))
        rank_cases.append((norm_col.like(prefix_pattern), _RANK_PREFIX_ID))
        match_predicates.append(norm_col.like(like_pattern))

    for col in _SEARCH_COLUMNS:
        if col in _IDENTIFIER_COLUMNS:
            continue
        norm_col = _normalize_column(col)
        rank_cases.append((norm_col == term, _RANK_EXACT_OTHER))
        rank_cases.append((norm_col.like(prefix_pattern), _RANK_PREFIX_OTHER))
        match_predicates.append(norm_col.like(like_pattern))

    rank_expr = case(*rank_cases, else_=_RANK_SUBSTRING).label("match_rank")
    match_filter = or_(*match_predicates)

    rows = (
        base.add_columns(rank_expr)
        .filter(match_filter)
        .order_by(rank_expr.asc(), CatalogItem.id.asc())
        .limit(min(int(limit), 500))
        .all()
    )
    # `add_columns` returns (CatalogItem, rank) tuples; the rank is a
    # ranking artifact and the caller wants plain catalog rows.
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Admin updates (Phase 6)
# ---------------------------------------------------------------------------


# Fields the admin PATCH route is allowed to rewrite. Excludes
# internal_sku and public_code by construction: internal_sku is the
# stable staff identifier (changing it would break invoice lines that
# already reference the row), and public_code is the immutable
# customer-facing identifier (Phase 7 adds a DB trigger for the
# belt-and-suspenders version of this rule).
_ADMIN_PATCHABLE_FIELDS = {
    "designer",
    "style_number",
    "color",
    "house_name",
    "product_title",
    "category",
    "description_text",
    "image_urls",
    "source_platform",
    "source_product_id",
    "source_product_handle",
    "source_product_url",
    "source_collection_url",
    "source_product_type",
    "is_sample",
    "active",
    "unit_price_cents",
}
_CATALOG_CATEGORIES = set(_CATEGORY_LABELS)
_ADMIN_PATCH_REQUIRED_FIELDS = {
    "color",
    "category",
    "image_urls",
    "is_sample",
    "active",
}


def update_catalog_item(
    db: Session,
    *,
    catalog_item_id: int,
    patch: dict[str, Any],
) -> CatalogItem:
    """Apply an admin patch to a catalog row.

    Phase 6 ships this primarily so admins can flip ``is_sample`` and
    ``active`` from the staff UI without raw SQL. The whitelist also
    covers the descriptive fields the importer set so admins can
    correct a bad scrape (typo in ``house_name``, wrong category) in
    place.

    Refuses to touch:
      - ``internal_sku``: changing it would silently break any
        ``invoice_line_items.catalog_item_id`` reference whose staff
        UI uses the SKU as the lookup key. Admin must create a fresh
        row instead and migrate references explicitly.
      - ``public_code``: immutable once issued. Phase 7 enforces
        this with a DB trigger; the service-level rejection here is
        the front door.

    Returns the refreshed row. Caller owns the transaction.
    """
    row = db.get(CatalogItem, catalog_item_id)
    if row is None:
        raise CatalogServiceError(
            f"catalog item {catalog_item_id} not found",
            code="catalog_item_not_found",
        )
    if "internal_sku" in patch:
        raise CatalogServiceError(
            "internal_sku is immutable; create a new catalog row "
            "instead",
            code="internal_sku_immutable",
        )
    if "public_code" in patch:
        raise CatalogServiceError(
            "public_code is immutable once issued",
            code="public_code_immutable",
        )
    unknown = set(patch) - _ADMIN_PATCHABLE_FIELDS
    if unknown:
        raise CatalogServiceError(
            f"cannot patch fields: {sorted(unknown)}",
            code="unknown_fields",
        )
    for field_name, value in patch.items():
        if field_name in _ADMIN_PATCH_REQUIRED_FIELDS and value is None:
            raise CatalogServiceError(
                f"{field_name} cannot be null",
                code="catalog_field_required",
                field=field_name,
            )
        if field_name == "category" and value not in _CATALOG_CATEGORIES:
            raise CatalogServiceError(
                "category is not allowed",
                code="catalog_category_invalid",
                field=field_name,
            )
        if field_name == "image_urls":
            # Defensive: keep the JSONB array shape the migration's
            # CHECK enforces. A None or non-list slipping in would
            # blow up the constraint at flush time with a less
            # friendly error.
            if not isinstance(value, list):
                raise CatalogServiceError(
                    "image_urls must be a list",
                    code="image_urls_invalid",
                )
            if any(not isinstance(url, str) for url in value):
                raise CatalogServiceError(
                    "image_urls must contain strings",
                    code="image_urls_invalid",
                )
        if field_name == "unit_price_cents" and value is not None:
            # Mirror migration 067's CHECK so the rejection surfaces
            # as a friendly domain error instead of a raw IntegrityError.
            if not isinstance(value, int) or isinstance(value, bool):
                raise CatalogServiceError(
                    "unit_price_cents must be an integer",
                    code="unit_price_cents_invalid",
                    field=field_name,
                )
            if value < 0:
                raise CatalogServiceError(
                    "unit_price_cents must be non-negative",
                    code="unit_price_cents_negative",
                    field=field_name,
                )
        setattr(row, field_name, value)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


# Subset of catalog columns the seed importer is allowed to refresh in
# place when its ``--update-existing`` mode is enabled. Narrower than
# ``_ADMIN_PATCHABLE_FIELDS`` on purpose: identity-defining columns
# (``designer``, ``style_number``, ``color``, ``category``) and admin
# toggles (``is_sample``, ``active``) must NOT change as a side effect
# of a vendor-source refresh — if any of those drifted, the row is a
# different SKU, not the same row with new data.
#
# ``house_name`` is in this allowlist but the importer applies a
# stricter rule: only refresh if the existing value is NULL, so a
# staff-curated brand line is never overwritten by a re-scrape.
REFRESH_ALLOWLIST: frozenset[str] = frozenset({
    "product_title",
    "description_text",
    "image_urls",
    "source_platform",
    "source_product_id",
    "source_product_handle",
    "source_product_url",
    "source_collection_url",
    "source_product_type",
    "house_name",
})


def refresh_catalog_item(
    db: Session,
    item: CatalogItem,
    updates: dict[str, Any],
) -> CatalogItem:
    """Apply a vendor-source-driven update to an allowed subset of
    catalog columns. Bumps ``updated_at`` and intentionally does NOT
    write to ``activity_log``.

    Bulk source refreshes are not staff-authored business events; an
    activity-log entry per refreshed SKU would drown the customer/
    event audit trail in importer noise. Run-level traceability lives
    in the importer's summary JSON sidecar instead.

    The ``updates`` dict is whatever the caller has already filtered
    against ``REFRESH_ALLOWLIST`` and the seed-vs-DB diff. This
    helper enforces the allowlist as a back-stop and does the
    ``image_urls`` shape check that the migration's CHECK constraint
    expects, but it does NOT interpret seed semantics (e.g. the
    "house_name only if existing is null" rule lives in the caller).

    No-op when ``updates`` is empty.
    """
    if not updates:
        return item
    unknown = set(updates) - REFRESH_ALLOWLIST
    if unknown:
        raise CatalogServiceError(
            f"refresh: fields not in allowlist: {sorted(unknown)}",
            code="refresh_field_not_allowed",
        )
    if "image_urls" in updates:
        value = updates["image_urls"]
        if not isinstance(value, list) or any(
            not isinstance(url, str) for url in value
        ):
            raise CatalogServiceError(
                "image_urls must be a list of strings",
                code="image_urls_invalid",
            )
    for field_name, value in updates.items():
        setattr(item, field_name, value)
    item.updated_at = datetime.now(timezone.utc)
    db.flush()
    return item


def get_by_internal_sku(db: Session, internal_sku: str) -> CatalogItem | None:
    """Lookup by internal SKU. The importer uses this to detect
    duplicates before attempting an INSERT (cheaper than catching the
    IntegrityError and rolling back the whole transaction).
    """
    return (
        db.query(CatalogItem)
        .filter(CatalogItem.internal_sku == internal_sku)
        .first()
    )


def get_by_public_code(db: Session, public_code: str) -> CatalogItem | None:
    """Lookup by public BVX code. Useful when staff need to answer
    "what dress is on invoice line BVX-00042?"
    """
    return (
        db.query(CatalogItem)
        .filter(CatalogItem.public_code == public_code)
        .first()
    )
