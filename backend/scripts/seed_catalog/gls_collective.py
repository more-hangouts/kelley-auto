"""Export GLS Collective (quinceanera) products into catalog seed JSON.

glscollective.com is a WooCommerce store with the public Store API
enabled, which is the richest source of any vendor: one request returns
the full product (authoritative ``Color`` attribute terms, image set,
price, sizes, description) with no HTML scraping and no per-product
detail fetch.

  GET /wp-json/wc/store/v1/products?category=<id>&per_page=100&page=N

The quinceanera products live under product category id 37
("QUINCEANERA"). Color comes from the Color attribute (the orderable
variant axis), and per-color images are recovered by matching the color
name inside each image filename (``gl3720-light-blue-1o-...webp``).

Prices ARE exposed (Woo minor units, e.g. 64900 = $649.00) and captured
into the seed's ``source.price_cents``; whether they populate the
catalog's pre-fill price is a separate import-time decision, so the
``unit_price_cents`` column is left for that step.

One catalog row per (style, color), same as the other vendors.

Usage:

    venv/bin/python scripts/seed_catalog/gls_collective.py
    venv/bin/python scripts/seed_catalog/gls_collective.py --category 37
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY = 37  # "QUINCEANERA"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/gls_collective_quince.json"
DEFAULT_VENDOR_CODE = "GLS"
DEFAULT_DESIGNER = "GLS Collective"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
STORE_API = "https://glscollective.com/wp-json/wc/store/v1"
PAGE_SIZE = 100
PAGE_FETCH_SLEEP_SECONDS = 0.4
MAX_PAGES = 10


# ---------------------------------------------------------------------------
# Shared helpers (mirrors of the other seed scrapers).
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return _collapse_space(" ".join(self._parts))


def _collapse_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(html.unescape(value))
    return parser.text()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-")
    return normalized or "UNKNOWN"


def _match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _prettify_color(name: str) -> str:
    return " ".join(part.capitalize() for part in _collapse_space(name).split(" "))


# ---------------------------------------------------------------------------
# Category mapping. GLS's quince category also carries petticoats/hoops.
# ---------------------------------------------------------------------------


def _category_for_product(name: str, slug: str) -> str:
    text = f"{name} {slug}".lower()
    if any(w in text for w in ("petticoat", "hoop", "crinoline", "slip")):
        return "accessory"
    return "quince_gown"


# ---------------------------------------------------------------------------
# Per-product model.
# ---------------------------------------------------------------------------


@dataclass
class ProductColor:
    name: str
    slug: str
    images: list[str] = field(default_factory=list)


@dataclass
class ProductDetail:
    style_number: str
    product_url: str
    product_title: str | None
    description_text: str
    wc_id: int | None
    colors: list[ProductColor]
    size_range: str | None
    price_cents: int | None


def _attr_terms(product: dict[str, Any], attr_name: str) -> list[str]:
    for attr in product.get("attributes") or []:
        if (attr.get("name") or "").strip().lower() == attr_name.lower():
            return [
                _collapse_space(str(t.get("name") or ""))
                for t in attr.get("terms") or []
                if t.get("name")
            ]
    return []


def _size_range(sizes: list[str]) -> str | None:
    if not sizes:
        return None
    return sizes[0] if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}"


def _price_cents(product: dict[str, Any]) -> int | None:
    prices = product.get("prices") or {}
    raw = prices.get("price")
    minor = prices.get("currency_minor_unit", 2)
    if raw in (None, "", "0"):
        return None
    try:
        # Woo returns the price as an integer string in minor units; a
        # 2-decimal currency already gives cents.
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if minor == 2:
        return value
    return round(value * 100 / (10 ** minor))


def _best_color_for_filename(url: str, colors: list[ProductColor]) -> ProductColor | None:
    norm = _match_key(url.rsplit("/", 1)[-1])
    best: ProductColor | None = None
    best_len = 0
    for color in colors:
        key = _match_key(color.name)
        if key and key in norm and len(key) > best_len:
            best, best_len = color, len(key)
    return best


def _title_from_permalink(permalink: str, style_number: str) -> str:
    # Woo's short_description holds long marketing copy, not a name, so the
    # readable title comes from the permalink slug ("...-ball-gown-with-
    # choker-gl3720" -> "Ball Gown With Choker"). product_title is a
    # varchar(200), so cap it.
    slug = permalink.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-gl\d+$", "", slug, flags=re.I)
    title = " ".join(word.capitalize() for word in slug.split("-") if word)
    return (title or style_number)[:200]


def parse_product(product: dict[str, Any]) -> ProductDetail:
    style_number = _collapse_space(str(product.get("sku") or product.get("name") or "")).upper()
    permalink = str(product.get("permalink") or "")
    short = _html_to_text(product.get("short_description"))
    description_text = short or _html_to_text(product.get("description"))
    product_title = _title_from_permalink(permalink, style_number)

    # Colors: the authoritative orderable Color attribute.
    color_names = _attr_terms(product, "Color")
    colors: list[ProductColor] = [
        ProductColor(name=_prettify_color(n), slug=_slug(n)) for n in color_names
    ]
    if not colors:
        colors = [ProductColor(name="Assorted", slug="ASSORTED")]

    # Assign each image to the color named in its filename; unmatched
    # images ride on the first color so none are dropped.
    seen: set[str] = set()
    for image in product.get("images") or []:
        src = str(image.get("src") or "").strip()
        if not src or src in seen:
            continue
        seen.add(src)
        target = _best_color_for_filename(src, colors) or colors[0]
        target.images.append(src)

    return ProductDetail(
        style_number=style_number,
        product_url=permalink,
        product_title=product_title,
        description_text=description_text,
        wc_id=product.get("id"),
        colors=colors,
        size_range=_size_range(_attr_terms(product, "Size")),
        price_cents=_price_cents(product),
    )


# ---------------------------------------------------------------------------
# Catalog row assembly.
# ---------------------------------------------------------------------------


def _catalog_rows(detail: ProductDetail, *, collection_url: str) -> list[dict[str, Any]]:
    if not detail.style_number:
        raise ValueError(f"Product at {detail.product_url} has no style number")

    category = _category_for_product(detail.product_title or "", detail.product_url)
    rows: list[dict[str, Any]] = []
    for color in detail.colors:
        rows.append(
            {
                "internal_sku": (
                    f"{DEFAULT_VENDOR_CODE}-{detail.style_number}-{color.slug}"
                ),
                "designer": DEFAULT_DESIGNER,
                "house_name": None,
                "style_number": detail.style_number,
                "color": color.name,
                "color_hex": None,
                "category": category,
                "product_title": detail.product_title,
                "description_text": detail.description_text,
                "description_html": "",
                "image_urls": list(color.images),
                "size_range": detail.size_range,
                "attributes": {},
                "raw_tags": [],
                "public_code": None,
                "active": True,
                "is_sample": False,
                "source": {
                    "platform": "woocommerce_store_api",
                    "product_id": detail.wc_id,
                    "price_cents": detail.price_cents,
                    "product_url": detail.product_url,
                    "source_url": collection_url,
                    "product_type": "Quinceanera Dresses",
                    "title": detail.product_title,
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------
# HTTP + orchestration.
# ---------------------------------------------------------------------------


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def _fetch_products(client: httpx.Client, category: int, sleep_seconds: float) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        response = client.get(
            f"{STORE_API}/products",
            params={"category": category, "per_page": PAGE_SIZE, "page": page},
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        products.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return products


def build_seed(
    category: int = DEFAULT_CATEGORY, sleep_seconds: float = PAGE_FETCH_SLEEP_SECONDS
) -> dict[str, Any]:
    collection_url = "https://glscollective.com/quinceanera-dresses/"
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with _new_client() as client:
        products = _fetch_products(client, category, sleep_seconds)
        for product in products:
            try:
                detail = parse_product(product)
                rows.extend(_catalog_rows(detail, collection_url=collection_url))
            except Exception as exc:  # noqa: BLE001 — record and continue
                skipped.append({"product_id": product.get("id"), "reason": str(exc)})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": collection_url,
        "scope": (
            "GLS Collective WooCommerce Store API (category QUINCEANERA), "
            "one catalog row per (style, color)."
        ),
        "notes": [
            "Single source: the public Store API returns colors, images, "
            "price, sizes, and description per product — no HTML scrape.",
            "Color comes from the Color attribute (orderable variant axis); "
            "images are grouped to a color by the color token in each filename.",
            "Retail price is captured in source.price_cents; whether it "
            "populates the catalog's pre-fill price is an import-time choice.",
            "Public codes are null because the app mints them on import.",
        ],
        "product_count": len(products),
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category", type=int, default=DEFAULT_CATEGORY, help="Woo product category id."
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Destination JSON file."
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=PAGE_FETCH_SLEEP_SECONDS,
        help="Seconds to sleep between Store API pages.",
    )
    args = parser.parse_args()

    seed = build_seed(args.category, sleep_seconds=args.sleep)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"Wrote {seed['catalog_item_count']} catalog row(s)"
        f" from {seed['product_count']} product(s) to {args.output}"
    )
    if seed["skipped"]:
        print(f"Skipped {len(seed['skipped'])} product(s).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
