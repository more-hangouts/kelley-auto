"""Pure-logic smoke test for services/pricing.py.

No database. Exercises the multiplier bands, the $800+/over-$999 ceiling
rule, the $299 manual-pricing floor, Dress Only and package-removal
quote-time adjustments, the 5% discretionary / >5% manager-auth discount
rule, and the worksheet-tab as-of-date parser.

Run: venv/bin/python tests/test_dress_pricing_smoke.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.pricing import (  # noqa: E402
    PACKAGE_ITEMS,
    base_multiplier,
    calculate_dress_price,
    parse_tab_as_of,
    price_breakdown,
    shelf_price_cents,
)

D = 100  # cents per dollar


def check_multiplier_bands() -> None:
    # band edges (dollars -> expected multiplier)
    cases = {
        299: 4.0,
        399: 4.0,
        400: 3.5,
        599: 3.5,
        600: 3.25,
        799: 3.25,
        800: 3.0,
        999: 3.0,
        1199: 3.0,  # over $999 stays x3.0, no ceiling
        1299: 3.0,
    }
    for dollars, expected in cases.items():
        got = base_multiplier(dollars * D)
        assert got == expected, f"{dollars} -> {got}, expected {expected}"
    # below the floor: no rule
    assert base_multiplier(298 * D) is None
    assert base_multiplier(0) is None
    print("multiplier bands ok (incl. over-$999 = x3.0, <$299 = manual)")


def check_shelf_price() -> None:
    # Real Morilee quince numbers from the Nov price list.
    assert shelf_price_cents(859 * D) == 859 * 3.0 * D == 257700
    assert shelf_price_cents(899 * D) == 269700
    assert shelf_price_cents(1199 * D) == 1199 * 3.0 * D == 359700
    # below floor -> manual pricing, no auto price
    assert shelf_price_cents(199 * D) is None
    print("shelf price (full package) ok")


def check_manual_pricing_floor() -> None:
    r = calculate_dress_price(150 * D)
    assert r.final_price_cents is None
    assert r.requires_manual_pricing is True
    assert r.base_multiplier is None
    assert r.notes, "should explain why it needs manual pricing"
    print("manual-pricing floor ok")


def check_dress_only() -> None:
    # $800 wholesale: package x3.0 = $2400; dress only x2.75 = $2200.
    pkg = calculate_dress_price(800 * D, is_package=True)
    dress_only = calculate_dress_price(800 * D, is_package=False)
    assert pkg.final_price_cents == 2400 * D
    assert dress_only.effective_multiplier == 2.75
    assert dress_only.final_price_cents == 2200 * D
    print("dress-only -0.25 multiplier ok")


def check_package_removals() -> None:
    # $600 wholesale x3.25 = $1950; remove crown(-100)+petticoat(-100)+steam(-50)
    r = calculate_dress_price(
        600 * D,
        is_package=True,
        removed_items=["crown", "petticoat", "steam"],
    )
    assert r.package_deductions_cents == 250 * D
    assert r.final_price_cents == (1950 - 250) * D
    # synonyms: tiara == crown, pedicoat == petticoat
    r2 = calculate_dress_price(
        600 * D, is_package=True, removed_items=["tiara", "pedicoat"]
    )
    assert r2.package_deductions_cents == 200 * D
    # removals are ignored for a Dress Only sale (no package to strip)
    r3 = calculate_dress_price(
        600 * D, is_package=False, removed_items=["crown"]
    )
    assert r3.package_deductions_cents == 0
    print("package removals (+synonyms, dress-only ignores) ok")


def check_discount_authorization() -> None:
    base = calculate_dress_price(600 * D).final_price_cents  # $1950
    # 5% is rep discretion, no auth
    five = calculate_dress_price(600 * D, discount_percent=5)
    assert five.requires_authorization is False
    assert five.final_price_cents == int(round(base * 0.95))
    # >5% needs manager auth, but the price is still computed
    ten = calculate_dress_price(600 * D, discount_percent=10)
    assert ten.requires_authorization is True
    assert ten.final_price_cents == int(round(base * 0.90))
    print("discount 5% discretion / >5% manager auth ok")


def check_as_of_parser() -> None:
    cases = {
        "Quince as of 6126": date(2026, 6, 1),
        "Quince Accessories as of 8325": date(2025, 8, 3),
        "MLNY as of 51526": date(2026, 5, 15),
        "Grace as of 111925": date(2025, 11, 19),
        "Morilee Bridal as of 6126": date(2026, 6, 1),
    }
    for tab, expected in cases.items():
        got = parse_tab_as_of(tab)
        assert got == expected, f"{tab!r} -> {got}, expected {expected}"
    assert parse_tab_as_of("Sheet1") is None
    print("worksheet as-of-date parser ok")


def check_price_breakdown() -> None:
    # $899 wholesale: package x3.0 = $2,697; dress only x2.75 = $2,472.25.
    bd = price_breakdown(899 * D, package_price_cents=269700)
    assert bd["package_price_cents"] == 269700
    assert bd["dress_only_price_cents"] == 247225
    assert bd["discretionary_discount_max_percent"] == 5.0
    labels = {i["key"]: i for i in bd["items"]}
    assert labels["crown"]["removable"] and labels["crown"]["deduct_cents"] == 100 * D
    assert labels["steam"]["deduct_cents"] == 50 * D
    assert labels["garment_bag"]["removable"] is False
    # No wholesale -> no dress-only line, but package list still present.
    none_bd = price_breakdown(None)
    assert none_bd["dress_only_price_cents"] is None
    assert len(none_bd["items"]) == len(PACKAGE_ITEMS)
    # Breakdown must never leak the wholesale cost.
    assert "wholesale" not in json_keys(bd)
    print("price breakdown (package/dress-only/items, no wholesale leak) ok")


def json_keys(d: dict) -> set:
    keys = set(d)
    for v in d.values():
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    keys |= set(item)
    return keys


def main() -> int:
    check_multiplier_bands()
    check_shelf_price()
    check_manual_pricing_floor()
    check_dress_only()
    check_package_removals()
    check_discount_authorization()
    check_price_breakdown()
    check_as_of_parser()
    print("\ndress pricing smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
