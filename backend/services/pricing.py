"""Bella's dress pricing logic — the single source of truth for turning a
wholesale cost into a retail price.

Two callers share this module so the math lives in exactly one place:

  - **Import time** (``shelf_price_cents``): when a Morilee price list is
    imported, the full-package shelf price is computed from wholesale and
    stored on ``catalog_items.unit_price_cents``. That is the number a
    sales rep sees on the dress card. No multiplier math is ever shown.

  - **Quote time** (``calculate_dress_price``): when a rep builds a quote
    line, the same rules run with the rep's selections (dress-only,
    removed package items, a discretionary discount). Package removals and
    discounts live ONLY on the quote line, never on the catalog row.

Business rules (authoritative; see the project memory note):

  Shelf price = wholesale * multiplier, where multiplier is set by the
  wholesale dollar band:
      $299-399 -> x4.0
      $400-599 -> x3.5
      $600-799 -> x3.25
      $800+    -> x3.0   (no ceiling; anything over $999 is still x3.0)
      below $299 -> no rule; flag for manual pricing.

  The full package is: standard crown/tiara, standard petticoat, steam,
  and a garment bag.

  Quote-time adjustments:
      - Dress Only (no package): multiplier -0.25.
      - Remove crown/tiara: -$100. Remove petticoat: -$100. Remove
        steam: -$50. (Garment bag is not separately removable.)
      - Discount up to 5% is normal rep discretion. Anything over 5%
        requires manager authorization (no exceptions) — surfaced as a
        flag; this module computes the discounted price either way and
        leaves enforcement to the caller.

All money is handled in integer cents. Multipliers are applied to integer
cents and rounded to the nearest cent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# --- Multiplier bands (thresholds in cents) -------------------------------
# Contiguous by hundreds so there is no gap at fractional-dollar boundaries.
_BAND_FLOOR_CENTS = 299_00  # below this: no rule, manual pricing.
_BAND_800_CENTS = 800_00

# --- The package + quote-time deductions ----------------------------------
DRESS_ONLY_MULTIPLIER_DELTA = -0.25


@dataclass(frozen=True)
class PackageItem:
    """One item bundled into the full-package price.

    ``deduct_cents`` is what a customer saves by opting out at quote time
    (only meaningful when ``removable``). ``synonyms`` are alternate keys
    callers may pass for the same item (e.g. the pricing sheet spells
    "petticoat" as "pedicoat").
    """

    key: str
    label: str
    removable: bool
    deduct_cents: int
    synonyms: tuple[str, ...] = ()


# Single source of truth for what the full package contains and what each
# removable item is worth. Drives both the quote-time math and the
# customer-facing price breakdown shown in the catalog detail view.
PACKAGE_ITEMS: tuple[PackageItem, ...] = (
    PackageItem("crown", "Crown / tiara", True, 100_00, synonyms=("tiara",)),
    PackageItem("petticoat", "Petticoat", True, 100_00, synonyms=("pedicoat",)),
    PackageItem("steam", "Steam", True, 50_00),
    PackageItem("garment_bag", "Garment bag", False, 0),
)

# Flattened {key/synonym -> deduction} for removable items, derived from
# PACKAGE_ITEMS so the deduction amounts live in exactly one place.
_REMOVAL_DEDUCTIONS_CENTS: dict[str, int] = {}
for _pi in PACKAGE_ITEMS:
    if _pi.removable:
        _REMOVAL_DEDUCTIONS_CENTS[_pi.key] = _pi.deduct_cents
        for _syn in _pi.synonyms:
            _REMOVAL_DEDUCTIONS_CENTS[_syn] = _pi.deduct_cents

# Discounts at or below this percent are rep discretion; above needs auth.
DISCRETIONARY_DISCOUNT_MAX_PERCENT = 5.0


def base_multiplier(base_cost_cents: int) -> float | None:
    """Return the band multiplier for a wholesale cost, or ``None`` if the
    cost is below the $299 floor (no rule -> manual pricing)."""
    if base_cost_cents < _BAND_FLOOR_CENTS:
        return None
    if base_cost_cents < 400_00:
        return 4.0
    if base_cost_cents < 600_00:
        return 3.5
    if base_cost_cents < 800_00:
        return 3.25
    return 3.0  # $800 and up, no ceiling


def _normalize_removed(removed_items: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in removed_items:
        key = (raw or "").strip().lower()
        if key in _REMOVAL_DEDUCTIONS_CENTS:
            out.append(key)
    return out


@dataclass
class PriceResult:
    """Outcome of a price calculation.

    ``final_price_cents`` is ``None`` only when the wholesale cost is below
    the $299 floor, in which case ``requires_manual_pricing`` is True and a
    human must set the price.
    """

    final_price_cents: int | None
    base_multiplier: float | None
    effective_multiplier: float | None
    package_deductions_cents: int
    discount_percent: float
    requires_authorization: bool
    requires_manual_pricing: bool
    notes: list[str] = field(default_factory=list)


def calculate_dress_price(
    base_cost_cents: int,
    *,
    is_package: bool = True,
    removed_items: list[str] | tuple[str, ...] = (),
    discount_percent: float = 0.0,
) -> PriceResult:
    """Compute a dress price in cents from wholesale cost and selections.

    The defaults (full package, nothing removed, no discount) yield the
    shelf price shown on the catalog card. Pass rep selections to get the
    quote-line price.
    """
    if not isinstance(base_cost_cents, int) or isinstance(base_cost_cents, bool):
        raise TypeError("base_cost_cents must be an int (cents)")
    if base_cost_cents < 0:
        raise ValueError("base_cost_cents must be non-negative")
    if discount_percent < 0:
        raise ValueError("discount_percent must be non-negative")

    notes: list[str] = []
    mult = base_multiplier(base_cost_cents)
    if mult is None:
        notes.append(
            f"wholesale ${base_cost_cents / 100:.2f} is below the $299 "
            "pricing floor; set the price manually"
        )
        return PriceResult(
            final_price_cents=None,
            base_multiplier=None,
            effective_multiplier=None,
            package_deductions_cents=0,
            discount_percent=discount_percent,
            requires_authorization=discount_percent
            > DISCRETIONARY_DISCOUNT_MAX_PERCENT,
            requires_manual_pricing=True,
            notes=notes,
        )

    effective_mult = mult
    if not is_package:
        effective_mult += DRESS_ONLY_MULTIPLIER_DELTA

    price = base_cost_cents * effective_mult

    deductions = 0
    if is_package:
        # Removals only apply to a package; a Dress Only sale already
        # excludes the package items via the multiplier reduction.
        for key in _normalize_removed(removed_items):
            deductions += _REMOVAL_DEDUCTIONS_CENTS[key]
        price -= deductions

    requires_auth = discount_percent > DISCRETIONARY_DISCOUNT_MAX_PERCENT
    if discount_percent:
        price *= 1 - (discount_percent / 100.0)

    final_cents = int(round(price))
    if final_cents < 0:
        final_cents = 0
        notes.append("deductions/discount drove the price below $0; clamped")

    return PriceResult(
        final_price_cents=final_cents,
        base_multiplier=mult,
        effective_multiplier=effective_mult,
        package_deductions_cents=deductions,
        discount_percent=discount_percent,
        requires_authorization=requires_auth,
        requires_manual_pricing=False,
        notes=notes,
    )


def shelf_price_cents(base_cost_cents: int) -> int | None:
    """The full-package shelf price for the catalog card, or ``None`` when
    the wholesale cost is below the $299 floor (manual pricing needed)."""
    return calculate_dress_price(base_cost_cents).final_price_cents


def price_breakdown(
    base_cost_cents: int | None,
    *,
    package_price_cents: int | None = None,
) -> dict:
    """Customer-facing decomposition of a catalog price for the detail view.

    Returns ONLY derived prices and the package contents — never the
    wholesale cost or the multiplier, so this is safe to send to the
    frontend. ``package_price_cents`` is the stored shelf price (what the
    rep already sees); when omitted it is computed from wholesale.

    ``dress_only_price_cents`` is ``None`` when there is no wholesale to
    derive it from (e.g. a non-Morilee row not yet on a price list, or a
    row priced manually) — the caller should then hide the Dress Only line.
    """
    package = package_price_cents
    if package is None and base_cost_cents is not None:
        package = shelf_price_cents(base_cost_cents)

    dress_only = None
    if base_cost_cents is not None:
        dress_only = calculate_dress_price(
            base_cost_cents, is_package=False
        ).final_price_cents

    return {
        "package_price_cents": package,
        "dress_only_price_cents": dress_only,
        "items": [
            {
                "key": item.key,
                "label": item.label,
                "removable": item.removable,
                "deduct_cents": item.deduct_cents,
            }
            for item in PACKAGE_ITEMS
        ],
        "discretionary_discount_max_percent": DISCRETIONARY_DISCOUNT_MAX_PERCENT,
    }


# --- Price-list freshness --------------------------------------------------
# Morilee updates individual workbook tabs in place; each tab name carries
# its own as-of date (e.g. "Quince as of 6126" -> 2026-06-01). The filename
# is NOT reliable for currency, so we parse the tab name instead.
_AS_OF_RE = re.compile(r"as of\s*(\d{4,6})\b", re.IGNORECASE)


def parse_tab_as_of(tab_name: str) -> date | None:
    """Parse the as-of date encoded in a price-list worksheet tab name.

    The trailing two digits are the year (20YY); the leading digits are the
    month and day, with the first valid month/day split chosen:
        "6126"   -> 2026-06-01
        "8325"   -> 2025-08-03
        "51526"  -> 2026-05-15
        "111925" -> 2025-11-19
    Returns ``None`` if no parseable date is present.
    """
    m = _AS_OF_RE.search(tab_name or "")
    if not m:
        return None
    digits = m.group(1)
    year = 2000 + int(digits[-2:])
    rem = digits[:-2]
    for month_len in (1, 2):
        if month_len >= len(rem):
            continue
        month = int(rem[:month_len])
        day = int(rem[month_len:])
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return None
