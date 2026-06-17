"""Export Morilee Shopify products into catalog seed JSON.

The source endpoint is Shopify's public products.json feed. The scraper uses
that feed as the discovery pass, then fetches each product page to capture the
visible storefront details that Shopify omits from products.json: collection,
short style copy, and the "Additional Information" attribute block.

It assumes Morilee's current shape where all variants on a product share the
same style-number SKU and color is the only meaningful public variant axis.

Usage:
    python scripts/seed_catalog/morilee.py
    python scripts/seed_catalog/morilee.py --url https://www.morilee.com/collections/vizcaya/products.json
    python scripts/seed_catalog/morilee.py --skip-detail-enrichment
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://www.morilee.com/collections/vizcaya/products.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/morilee_vizcaya.json"
DEFAULT_VENDOR_CODE = "MORI"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
SHOPIFY_PAGE_LIMIT = 250
DETAIL_FETCH_SLEEP_SECONDS = 0.25


_SHORT_DESCRIPTION_RE = re.compile(
    r'<div class="short-description">(?P<html>.*?)</div><span class="badges">',
    re.S,
)
_COLLECTION_RE = re.compile(
    r'<p class="product--text subheading"[^>]*>\s*Collection:\s*(?P<value>.*?)\s*</p>',
    re.S,
)
_ATTRIBUTES_RE = re.compile(
    r'<div class="product-metafields">(?P<html>.*?)</div>\s*</div>\s*</details>',
    re.S,
)
_ATTRIBUTE_BLOCK_RE = re.compile(
    r'<div class="metafield-block">\s*<h6>(?P<label>.*?)</h6>(?P<body>.*?)</div>',
    re.S,
)
_SWATCH_RE = re.compile(
    r'<input[^>]+name="Color"[^>]+value="(?P<color>[^"]+)"[^>]*>\s*'
    r'<label[^>]+style="(?P<style>[^"]*)"',
    re.S,
)


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


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _absolute_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    return urljoin(base_url, html.unescape(value.strip()))


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-")
    return normalized or "UNKNOWN"


def _category(product_type: str | None) -> str:
    value = (product_type or "").lower()
    if "quince" in value:
        return "quince_gown"
    if "bridal" in value or "wedding" in value:
        return "bridal_gown"
    if "dress" in value or "gown" in value:
        return "formal_gown"
    return "accessory"


def _first_sku(product: dict[str, Any]) -> str:
    for variant in product.get("variants") or []:
        sku = str(variant.get("sku") or "").strip()
        if sku:
            return sku
    raise ValueError(f"Product {product.get('id')} has no variant SKU")


def _colors(product: dict[str, Any]) -> list[str]:
    options = product.get("options") or []
    if options:
        first = options[0] or {}
        values = [str(value).strip() for value in first.get("values") or []]
        colors = [value for value in values if value]
        if colors:
            return colors

    colors: list[str] = []
    seen: set[str] = set()
    for variant in product.get("variants") or []:
        color = str(variant.get("option1") or "").strip()
        key = color.lower()
        if color and key not in seen:
            colors.append(color)
            seen.add(key)
    return colors or ["Unspecified"]


def _image_urls(product: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for image in product.get("images") or []:
        src = str(image.get("src") or "").strip()
        if src:
            urls.append(src)
    return urls


def _image_meta(product: dict[str, Any]) -> list[dict[str, Any]]:
    meta: list[dict[str, Any]] = []
    for image in product.get("images") or []:
        src = str(image.get("src") or "").strip()
        if not src:
            continue
        meta.append(
            {
                "url": src,
                "image_id": image.get("id"),
                "position": image.get("position"),
                "width": image.get("width"),
                "height": image.get("height"),
                "variant_ids": image.get("variant_ids") or [],
            }
        )
    return meta


def _product_url(source_url: str, product: dict[str, Any]) -> str | None:
    handle = str(product.get("handle") or "").strip()
    if not handle:
        return None
    parsed = urlparse(source_url)
    return urlunparse((parsed.scheme, parsed.netloc, f"/products/{handle}", "", "", ""))


def _variant_by_color(product: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for variant in product.get("variants") or []:
        color = str(variant.get("option1") or variant.get("title") or "").strip()
        if color:
            out[color.lower()] = variant
    return out


def _swatch_meta(style: str, base_url: str) -> dict[str, str | None]:
    color_match = re.search(r"--option-color:\s*([^;]+)", style)
    image_match = re.search(r"--option-color-image:\s*url\('([^']*)'\)", style)
    raw_color = _collapse_space(color_match.group(1)) if color_match else ""
    swatch_color = raw_color if raw_color.startswith(("#", "rgb", "hsl")) else None
    return {
        "swatch_color": swatch_color,
        "swatch_image_url": _absolute_url(image_match.group(1), base_url)
        if image_match
        else None,
    }


def _parse_detail_html(page_html: str, product_url: str) -> dict[str, Any]:
    detail: dict[str, Any] = {}

    short_match = _SHORT_DESCRIPTION_RE.search(page_html)
    if short_match:
        short_html = short_match.group("html").strip()
        detail["short_description_html"] = html.unescape(short_html)
        detail["short_description_text"] = _html_to_text(short_html)

    collection_match = _COLLECTION_RE.search(page_html)
    if collection_match:
        detail["collection"] = _html_to_text(collection_match.group("value")) or None

    swatches: dict[str, dict[str, str | None]] = {}
    for match in _SWATCH_RE.finditer(page_html):
        color = html.unescape(match.group("color")).strip()
        if color:
            swatches[color.lower()] = _swatch_meta(match.group("style"), product_url)
    if swatches:
        detail["color_swatches"] = swatches

    attributes_match = _ATTRIBUTES_RE.search(page_html)
    if attributes_match:
        attributes: dict[str, list[str]] = {}
        for block in _ATTRIBUTE_BLOCK_RE.finditer(attributes_match.group("html")):
            key = _normalize_key(_html_to_text(block.group("label")))
            values = [
                _collapse_space(value.rstrip(","))
                for value in re.findall(r"<span[^>]*>(.*?)</span>", block.group("body"), re.S)
            ]
            clean_values = [value for value in values if value]
            if key and clean_values:
                attributes[key] = clean_values
        if attributes:
            detail["attributes"] = attributes

    return detail


def _fetch_detail_map(products: list[dict[str, Any]], source_url: str) -> dict[int, dict[str, Any]]:
    details: dict[int, dict[str, Any]] = {}
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for idx, product in enumerate(products):
            product_id = product.get("id")
            product_url = _product_url(source_url, product)
            if product_id is None or not product_url:
                continue
            if idx:
                time.sleep(DETAIL_FETCH_SLEEP_SECONDS)
            response = client.get(product_url)
            response.raise_for_status()
            details[int(product_id)] = _parse_detail_html(response.text, product_url)
    return details


def _with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["limit"] = str(SHOPIFY_PAGE_LIMIT)
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _fetch_products(url: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        page = 1
        while True:
            page_url = _with_page(url, page)
            response = client.get(page_url)
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("products") or []
            if not batch:
                break
            products.extend(batch)
            if len(batch) < SHOPIFY_PAGE_LIMIT:
                break
            page += 1
    return products


def _catalog_rows(
    source_url: str,
    product: dict[str, Any],
    detail: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    detail = detail or {}
    vendor = str(product.get("vendor") or "Morilee").strip()
    style_number = _first_sku(product)
    description = _html_to_text(product.get("body_html"))
    colors = _colors(product)
    product_url = _product_url(source_url, product)
    variants_by_color = _variant_by_color(product)
    all_image_meta = _image_meta(product)
    color_swatches = detail.get("color_swatches") or {}
    rows: list[dict[str, Any]] = []

    for color in colors:
        variant = variants_by_color.get(color.lower()) or {}
        featured_image = variant.get("featured_image") or {}
        swatch = color_swatches.get(color.lower()) or {}
        rows.append(
            {
                "internal_sku": f"{DEFAULT_VENDOR_CODE}-{style_number}-{_slug(color)}",
                "designer": vendor,
                "style_number": style_number,
                "color": color,
                "collection": detail.get("collection"),
                "category": _category(product.get("product_type")),
                "product_title": product.get("title"),
                "short_description_html": detail.get("short_description_html") or "",
                "short_description_text": detail.get("short_description_text") or "",
                "attributes": detail.get("attributes") or {},
                "image_meta": all_image_meta,
                "color_swatch": {
                    "color": swatch.get("swatch_color"),
                    "image_url": swatch.get("swatch_image_url"),
                },
                "source": {
                    "platform": "shopify_products_json",
                    "product_id": product.get("id"),
                    "variant_id": variant.get("id"),
                    "variant_available": variant.get("available"),
                    "variant_featured_image_url": featured_image.get("src"),
                    "handle": product.get("handle"),
                    "product_url": product_url,
                    "source_url": source_url,
                    "product_type": product.get("product_type"),
                    "title": product.get("title"),
                    "published_at": product.get("published_at"),
                    "created_at": product.get("created_at"),
                    "updated_at": product.get("updated_at"),
                },
                "description_html": product.get("body_html") or "",
                "description_text": description,
                "image_urls": _image_urls(product),
                "raw_tags": product.get("tags") or [],
                "public_code": None,
                "house_name": None,
                "active": True,
                "is_sample": False,
            }
        )
    return rows


def build_seed(
    url: str,
    *,
    enrich_details: bool = True,
    max_products: int | None = None,
) -> dict[str, Any]:
    products = _fetch_products(url)
    if max_products is not None:
        products = products[:max_products]
    details = _fetch_detail_map(products, url) if enrich_details else {}
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for product in products:
        try:
            product_id = product.get("id")
            detail = details.get(int(product_id)) if product_id is not None else None
            rows.extend(_catalog_rows(url, product, detail))
        except ValueError as exc:
            skipped.append(
                {
                    "product_id": product.get("id"),
                    "title": product.get("title"),
                    "reason": str(exc),
                }
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "scope": (
            "Morilee Vizcaya Shopify products.json, one catalog row per color, "
            "with product-page detail enrichment"
            if enrich_details
            else "Morilee Vizcaya Shopify products.json, one catalog row per color"
        ),
        "notes": [
            "Variant sku is treated as style_number.",
            "Sizes are intentionally omitted; Bellas owns size lists per style.",
            "Public codes are null because the app will mint them when catalog rows are created.",
            "Shopify price is intentionally ignored because Morilee exposes placeholder pricing.",
            "Visible product-page attributes are captured in attributes but not imported yet.",
            "Product-page collection, short description, swatch metadata, and image metadata are captured for future staff workflows.",
        ],
        "product_count": len(products),
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Shopify products.json URL.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file.",
    )
    parser.add_argument(
        "--skip-detail-enrichment",
        action="store_true",
        help="Only use Shopify products.json; skip product-page HTML fetches.",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="Limit products processed; useful for scraper smoke checks.",
    )
    args = parser.parse_args()

    seed = build_seed(
        args.url,
        enrich_details=not args.skip_detail_enrichment,
        max_products=args.max_products,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
