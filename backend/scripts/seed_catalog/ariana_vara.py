"""Export Ariana Vara (Princesa) products into catalog seed JSON.

Unlike Morilee's Shopify products.json feed, arianavara.com is a Syvo
storefront with no public JSON product API, so this scraper does a
two-stage HTML pass:

  1. Walk the category page (paginated via ?page=N) to collect product
     detail URLs.
  2. Fetch each product detail page and extract style number, product
     name, color list (with hex swatches), grouped image URLs, size
     range, and the attribute block (fabric, neckline, silhouette, ...).

The seed JSON keeps every visible field so we never have to re-scrape.
The current catalog importer only consumes a narrow subset of these
columns; the extra fields (color_hex, size_range, attributes,
image_meta) ride along in the seed for the next time staff workflows
need them.

One catalog row per (style, color), same as Morilee.

Usage:

    venv/bin/python scripts/seed_catalog/ariana_vara.py
    venv/bin/python scripts/seed_catalog/ariana_vara.py \\
        --url https://www.arianavara.com/categories/latest-collection
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
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, parse_qsl

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://www.arianavara.com/categories/latest-collection"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/ariana_vara_latest_collection.json"
DEFAULT_VENDOR_CODE = "ARIA"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
DETAIL_FETCH_SLEEP_SECONDS = 0.5
MAX_CATEGORY_PAGES = 25
ORIGIN = "https://www.arianavara.com"


# ---------------------------------------------------------------------------
# Small shared helpers (mirrors of Morilee scraper helpers).
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


# ---------------------------------------------------------------------------
# Category page parsing.
# ---------------------------------------------------------------------------


# Every product card anchor carries an aria-label of the form
# "Go to PR30191 product details page". As of 2026, the href itself is a
# per-color deeplink (/.../pr30191/magenta-gold), and a card has one such
# anchor per color, so we match on the aria-label (reliable style number),
# then strip the trailing /color slug to recover the canonical product URL
# and dedupe by it.
_PRODUCT_LINK_RE = re.compile(
    r'<a\s+href="(/[^"]+)"[^>]*'
    r'aria-label="Go to ([A-Za-z]+\d+) product details page"',
    re.S,
)


def _canonical_product_url(href: str, style_number: str) -> str:
    """Trim any trailing /color-slug deeplink off a card href.

    "/princesa-quinceanera-dresses/spring-2025/pr30191/magenta-gold"
    -> "/princesa-quinceanera-dresses/spring-2025/pr30191"
    """
    match = re.search(
        r"(.*?/" + re.escape(style_number.lower()) + r")(?:/|$)",
        href,
        re.I,
    )
    return match.group(1) if match else href


def parse_category_links(category_html: str, base_url: str) -> list[dict[str, str]]:
    """Return a list of {style_number, product_url} dicts for one category page.

    Matches every card anchor by aria-label, collapses the per-color
    deeplink siblings to one canonical URL per style, and dedupes.
    """

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href, style_number in _PRODUCT_LINK_RE.findall(category_html):
        style = style_number.upper()
        canonical = _canonical_product_url(href, style)
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(
            {
                "style_number": style,
                "product_url": urljoin(base_url, canonical),
            }
        )
    return out


def _category_page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page > 1:
        query["page"] = str(page)
    else:
        query.pop("page", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


# ---------------------------------------------------------------------------
# Product detail page parsing.
# ---------------------------------------------------------------------------


@dataclass
class ProductImage:
    url: str
    sequence: int
    face: str  # "front" | "back" | "unknown"
    crop: str  # "full" | "cropped"
    is_default: bool


@dataclass
class ProductColor:
    value_id: str
    name: str
    hex_value: str | None
    slug: str
    images: list[ProductImage] = field(default_factory=list)


@dataclass
class ProductDetail:
    style_number: str
    product_url: str
    product_title: str | None
    description_text: str
    description_html: str
    breadcrumb_names: list[str]
    syvo_product_id: str | None
    syvo_vendor_account: str | None
    handle: str
    colors: list[ProductColor]
    size_range: str | None
    attributes: dict[str, list[str]]


_LDJSON_RE = re.compile(
    r'<script[^>]*type="application/ld(?:\+|&#x2B;)json"[^>]*>(.*?)</script>',
    re.S | re.I,
)
_OG_RE = re.compile(
    r'<meta[^>]*property="og:([a-z]+)"[^>]*content="([^"]*)"',
    re.I,
)
_TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.I)
_STYLE_HEADER_RE = re.compile(
    r'<div class="option-style">.*?<h1>\s*Style\s+([A-Z0-9]+)\s*</h1>',
    re.S,
)
_PRODUCT_NAME_RE = re.compile(
    r'<div class="option name">.*?<h2>\s*(.*?)\s*</h2>',
    re.S,
)
_OPTION_DISPLAY_RE = re.compile(
    r'<h5 class="option-title">\s*<span>\s*([^<:]+?)\s*:?\s*</span>\s*</h5>'
    r'\s*<h5 class="option-display">\s*(?:<span>)?\s*([^<]*?)\s*(?:</span>)?\s*</h5>',
    re.S,
)
_SWATCH_RE = re.compile(
    r'<div class="product-color product-option"[^>]*'
    r'data-value-id="(\d+)"[^>]*'
    r'data-value="([^"]+)"[^>]*'
    r'data-hex="([^"]*)"',
)
_PREVIEW_IMG_RE = re.compile(
    r'class="preview[^"]*"[^>]*data-value-id="(\d+)"[^>]*>'
    r'\s*<img[^>]*src="([^"]+)"[^>]*alt="([^"]+)"',
    re.S,
)
_OVERVIEW_ZOOM_RE = re.compile(
    r'<div class="overview"[^>]*data-value-id="(\d*)"[^>]*>'
    r'.*?<a href="([^"]+\.webp)"[^>]*class="MagicZoom overview-media"',
    re.S,
)
_SECTION_PRODUCT_RE = re.compile(
    r'<section class="section-product"[^>]*data-product-id="(\d+)"',
)
_VENDOR_FROM_IMG_RE = re.compile(
    r"cloudfront\.net/products/(\d+)/[A-Za-z0-9]+/",
)
_ATTR_UL_RE = re.compile(r'<ul class="attr-ul">(.*?)</ul>', re.S)
_ATTR_LI_RE = re.compile(
    r'<li>\s*<div>\s*([^<:]+?)\s*:\s*</div>\s*<div>\s*(.*?)\s*</div>\s*</li>',
    re.S,
)
_ALT_SEQUENCE_RE = re.compile(r"\$(\d+)")


def _parse_breadcrumb(ld_blocks: list[str]) -> list[str]:
    for blob in ld_blocks:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict):
                continue
            crumb = node.get("breadcrumb") if "breadcrumb" in node else None
            if not isinstance(crumb, dict):
                continue
            items = crumb.get("itemListElement") or []
            names: list[str] = []
            for entry in items:
                item = entry.get("item") if isinstance(entry, dict) else None
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item["name"]))
            if names:
                return names
    return []


def _ldjson_description(ld_blocks: list[str]) -> str | None:
    for blob in ld_blocks:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and node.get("description"):
                return str(node["description"])
    return None


def _classify_image_alt(alt: str) -> tuple[int, str, str, bool]:
    decoded = html.unescape(alt)
    seq_match = _ALT_SEQUENCE_RE.search(decoded)
    sequence = int(seq_match.group(1)) if seq_match else -1
    lowered = decoded.lower()
    if "backface" in lowered:
        face = "back"
    elif "frontface" in lowered:
        face = "front"
    else:
        face = "unknown"
    crop = "cropped" if "cropped" in lowered else "full"
    is_default = re.search(r"\bdefault\b", lowered) is not None
    return sequence, face, crop, is_default


def _zoom_url_from_thumb(thumb_url: str) -> str:
    absolute_url = urljoin(ORIGIN + "/", html.unescape(thumb_url).strip())
    if absolute_url.endswith(".340.webp"):
        return absolute_url[: -len(".340.webp")] + ".2000.webp"
    return re.sub(r"\.\d+\.webp$", ".2000.webp", absolute_url)


def _handle_from_url(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1].lower()


def _category_from_breadcrumb(names: list[str]) -> str:
    haystack = " ".join(names).lower()
    if "quince" in haystack:
        return "quince_gown"
    if "bridal" in haystack or "wedding" in haystack:
        return "bridal_gown"
    if "dress" in haystack or "gown" in haystack:
        return "formal_gown"
    return "accessory"


def _split_attribute_value(value_html: str) -> list[str]:
    text = _html_to_text(value_html)
    if not text:
        return []
    parts = [_collapse_space(p) for p in text.split(",")]
    return [p for p in parts if p]


def parse_product_detail(detail_html: str, product_url: str) -> ProductDetail:
    ld_blocks = [m.group(1).strip() for m in _LDJSON_RE.finditer(detail_html)]
    breadcrumb_names = _parse_breadcrumb(ld_blocks)
    ldjson_description = _ldjson_description(ld_blocks)

    og: dict[str, str] = {}
    for key, value in _OG_RE.findall(detail_html):
        og.setdefault(key.lower(), html.unescape(value))

    title_match = _TITLE_RE.search(detail_html)
    page_title = html.unescape(title_match.group(1).strip()) if title_match else None

    style_match = _STYLE_HEADER_RE.search(detail_html)
    if style_match:
        style_number = style_match.group(1).upper()
    elif breadcrumb_names:
        style_number = breadcrumb_names[-1].strip().upper()
    else:
        style_number = _handle_from_url(product_url).upper()

    name_match = _PRODUCT_NAME_RE.search(detail_html)
    product_title = _html_to_text(name_match.group(1)) if name_match else None

    description_html = ldjson_description or og.get("description") or ""
    description_text = _html_to_text(description_html)

    option_displays: dict[str, str] = {}
    for label, value in _OPTION_DISPLAY_RE.findall(detail_html):
        option_displays[_collapse_space(label).lower()] = _html_to_text(value)

    color_list_text = option_displays.get("color", "")
    size_range = option_displays.get("size") or None

    swatch_order: list[str] = []
    swatches: dict[str, ProductColor] = {}
    for value_id, raw_name, raw_hex in _SWATCH_RE.findall(detail_html):
        name = html.unescape(raw_name).strip()
        hex_value = html.unescape(raw_hex).strip() or None
        if value_id in swatches:
            continue
        swatches[value_id] = ProductColor(
            value_id=value_id,
            name=name,
            hex_value=hex_value,
            slug=_slug(name),
        )
        swatch_order.append(value_id)

    if not swatches and color_list_text:
        # Fallback: derive colors from the comma-separated option-display
        # text when the swatch markup is missing (defensive for layout
        # changes).
        for idx, raw_name in enumerate(color_list_text.split(",")):
            name = raw_name.strip()
            if not name:
                continue
            value_id = f"fallback-{idx}"
            swatches[value_id] = ProductColor(
                value_id=value_id,
                name=name,
                hex_value=None,
                slug=_slug(name),
            )
            swatch_order.append(value_id)

    for value_id, src, alt in _PREVIEW_IMG_RE.findall(detail_html):
        color = swatches.get(value_id)
        if color is None:
            continue
        sequence, face, crop, is_default = _classify_image_alt(alt)
        color.images.append(
            ProductImage(
                url=_zoom_url_from_thumb(src),
                sequence=sequence,
                face=face,
                crop=crop,
                is_default=is_default,
            )
        )

    if not any(c.images for c in swatches.values()):
        # Fallback to overview hrefs when previews are missing. Empty
        # data-value-id images (color-agnostic shots) are skipped because
        # they cannot be assigned to a per-color row.
        for value_id, href in _OVERVIEW_ZOOM_RE.findall(detail_html):
            color = swatches.get(value_id)
            if color is None:
                continue
            color.images.append(
                ProductImage(
                    url=href,
                    sequence=len(color.images),
                    face="unknown",
                    crop="full",
                    is_default=False,
                )
            )

    for color in swatches.values():
        color.images.sort(key=lambda img: (img.sequence, img.url))

    section_match = _SECTION_PRODUCT_RE.search(detail_html)
    syvo_product_id = section_match.group(1) if section_match else None

    vendor_match = _VENDOR_FROM_IMG_RE.search(detail_html)
    syvo_vendor_account = vendor_match.group(1) if vendor_match else None

    attributes: dict[str, list[str]] = {}
    attr_block_match = _ATTR_UL_RE.search(detail_html)
    if attr_block_match:
        for label, value_html in _ATTR_LI_RE.findall(attr_block_match.group(1)):
            key = _slug(label).lower().replace("-", "_")
            attributes[key] = _split_attribute_value(value_html)

    handle = _handle_from_url(product_url)

    return ProductDetail(
        style_number=style_number,
        product_url=product_url,
        product_title=product_title or page_title,
        description_text=description_text,
        description_html=description_html,
        breadcrumb_names=breadcrumb_names,
        syvo_product_id=syvo_product_id,
        syvo_vendor_account=syvo_vendor_account,
        handle=handle,
        colors=[swatches[vid] for vid in swatch_order],
        size_range=size_range,
        attributes=attributes,
    )


# ---------------------------------------------------------------------------
# Catalog row assembly.
# ---------------------------------------------------------------------------


def _product_type(detail: ProductDetail) -> str | None:
    # Drop the last breadcrumb (the product itself); join the rest with " › ".
    interior = detail.breadcrumb_names[1:-1]
    if not interior:
        return None
    return " › ".join(interior)


def _catalog_rows(
    detail: ProductDetail,
    category_url: str,
) -> list[dict[str, Any]]:
    if not detail.colors:
        raise ValueError(f"Product {detail.style_number} has no colors")
    if not detail.style_number:
        raise ValueError(f"Product at {detail.product_url} has no style number")

    rows: list[dict[str, Any]] = []
    product_type = _product_type(detail)
    category = _category_from_breadcrumb(detail.breadcrumb_names)

    for color in detail.colors:
        if not color.images:
            # We still emit the row so staff can see the color exists; the
            # importer just gets an empty image_urls array.
            pass
        rows.append(
            {
                "internal_sku": (
                    f"{DEFAULT_VENDOR_CODE}-{detail.style_number}-{color.slug}"
                ),
                "designer": "Ariana Vara",
                "house_name": "Princesa",
                "style_number": detail.style_number,
                "color": color.name,
                "color_hex": color.hex_value,
                "category": category,
                "product_title": detail.product_title,
                "description_text": detail.description_text,
                "description_html": detail.description_html,
                "image_urls": [img.url for img in color.images],
                "image_meta": [
                    {
                        "url": img.url,
                        "sequence": img.sequence,
                        "face": img.face,
                        "crop": img.crop,
                        "is_default": img.is_default,
                    }
                    for img in color.images
                ],
                "size_range": detail.size_range,
                "attributes": detail.attributes,
                "raw_tags": [],
                "public_code": None,
                "active": True,
                "is_sample": False,
                "source": {
                    "platform": "syvo_storefront",
                    "vendor_account": detail.syvo_vendor_account,
                    "product_id": detail.syvo_product_id,
                    "color_value_id": color.value_id,
                    "handle": detail.handle,
                    "product_url": detail.product_url,
                    "source_url": category_url,
                    "product_type": product_type,
                    "title": detail.product_title,
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------
# HTTP fetchers.
# ---------------------------------------------------------------------------


def _new_client() -> httpx.Client:
    headers = {"User-Agent": USER_AGENT}
    return httpx.Client(timeout=30.0, follow_redirects=True, headers=headers)


def _fetch_product_urls(client: httpx.Client, category_url: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for page in range(1, MAX_CATEGORY_PAGES + 1):
        page_url = _category_page_url(category_url, page)
        response = client.get(page_url)
        response.raise_for_status()
        links = parse_category_links(response.text, page_url)
        if not links:
            break
        before = len(ordered)
        for link in links:
            if link["product_url"] in seen:
                continue
            seen.add(link["product_url"])
            ordered.append(link["product_url"])
        if len(ordered) == before:
            # Page returned only duplicates; pagination is exhausted.
            break
    return ordered


def _fetch_product_detail_html(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    return response.text


# ---------------------------------------------------------------------------
# Top-level orchestration.
# ---------------------------------------------------------------------------


def build_seed(category_url: str, sleep_seconds: float = DETAIL_FETCH_SLEEP_SECONDS) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    product_count = 0

    with _new_client() as client:
        product_urls = _fetch_product_urls(client, category_url)

        for index, url in enumerate(product_urls):
            product_count += 1
            try:
                detail_html = _fetch_product_detail_html(client, url)
                detail = parse_product_detail(detail_html, url)
                rows.extend(_catalog_rows(detail, category_url))
            except Exception as exc:
                skipped.append({"product_url": url, "reason": str(exc)})
            if sleep_seconds and index + 1 < len(product_urls):
                time.sleep(sleep_seconds)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": category_url,
        "scope": (
            "Ariana Vara (Princesa) Syvo storefront, one catalog row per "
            "(style, color)."
        ),
        "notes": [
            "Detail-page scrape; no public JSON API.",
            (
                "image_urls stores the .2000.webp zoom URL per image. "
                "image_meta carries face/crop/sequence/is_default labels "
                "lifted from <img> alt text on the previews strip."
            ),
            (
                "color_hex, size_range, and attributes are captured but not "
                "consumed by the current importer; they wait for a future "
                "catalog_items schema change."
            ),
            "Public codes are null because the app mints them on import.",
            "Pricing and per-size availability are intentionally skipped.",
        ],
        "product_count": product_count,
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Category page URL.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DETAIL_FETCH_SLEEP_SECONDS,
        help="Seconds to sleep between detail-page fetches.",
    )
    args = parser.parse_args()

    seed = build_seed(args.url, sleep_seconds=args.sleep)
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
