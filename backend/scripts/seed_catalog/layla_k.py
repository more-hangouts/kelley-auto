"""Export Layla K (May Queen USA) quinceanera products into catalog seed JSON.

mayqueenusa.com is a Magento store. The Layla K products are *simple*
products (no configurable swatches), so color lives in an unexpected but
reliable place: every image in the Magento media gallery carries a
``caption`` equal to its color (e.g. ``HUNTERGREEN``). The scraper:

  1. Pages the category listing (``?p=N``) to collect product URLs.
  2. Fetches each product page and reads the ``mage/gallery/gallery``
     JSON, grouping the gallery images by their ``caption`` to recover
     one image set per color.

Color captions are uppercase and run-together (``ROSEGOLD``,
``DUSTYBLUE``); a small word splitter restores spaces so the catalog's
color-family filter can classify them.

One catalog row per (style, color), same as the other vendors.

Usage:

    venv/bin/python scripts/seed_catalog/layla_k.py
    venv/bin/python scripts/seed_catalog/layla_k.py \\
        --url https://www.mayqueenusa.com/brands/layla-k/quince-dresses.html
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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://www.mayqueenusa.com/brands/layla-k/quince-dresses.html"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/layla_k_quince.json"
DEFAULT_VENDOR_CODE = "LAYLA"
DEFAULT_DESIGNER = "Layla K"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
ORIGIN = "https://www.mayqueenusa.com"
DETAIL_FETCH_SLEEP_SECONDS = 0.4
MAX_PAGES = 20


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


# Color caption splitting. Captions arrive uppercase and run-together
# ("ROSEGOLD", "DUSTYBLUE", "HUNTERGREEN"). Insert a space before any
# known trailing color word that follows another letter so the result is
# human- and classifier-friendly. Longest words first so "rosegold" picks
# "gold" not a shorter accidental match.
_COLOR_SECOND_WORDS = [
    "champagne", "silver", "purple", "yellow", "orange", "maroon",
    "green", "white", "black", "nude", "rose", "gold", "blue", "pink",
    "gray", "grey", "teal", "aqua", "plum", "wine", "mint", "sage",
    "multi", "ombre",
]
_COLOR_SPLIT_RE = re.compile(
    r"(?<=[a-z])(" + "|".join(sorted(_COLOR_SECOND_WORDS, key=len, reverse=True)) + r")",
    re.I,
)


def _prettify_color(caption: str) -> str:
    raw = _collapse_space(caption)
    if not raw:
        return raw
    # Keep existing separators, then split run-together compounds.
    spaced = re.sub(r"[/_-]+", " ", raw.lower())
    spaced = _COLOR_SPLIT_RE.sub(r" \1", spaced)
    spaced = _collapse_space(spaced)
    return " ".join(part.capitalize() for part in spaced.split(" "))


# ---------------------------------------------------------------------------
# Category page parsing.
# ---------------------------------------------------------------------------


_PRODUCT_URL_RE = re.compile(
    r'href="(https://www\.mayqueenusa\.com/lk-?\d+\.html)"', re.I
)


def parse_category_links(category_html: str) -> list[str]:
    """Ordered, de-duped product URLs on one category page."""
    seen: set[str] = set()
    out: list[str] = []
    for url in _PRODUCT_URL_RE.findall(category_html):
        normalized = url.split("?")[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _category_page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page > 1:
        query["p"] = str(page)
    else:
        query.pop("p", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


# ---------------------------------------------------------------------------
# Product detail page parsing.
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
    colors: list[ProductColor]
    size_range: str | None = None


# The authoritative variant source is Magento's grouped-product table:
# one <tr class="grouped-product-row"> per ORDERABLE color, each carrying
# its color label and its own image set (data-gallery-items-json). This
# is what's actually sold, unlike the media-gallery captions which also
# include photographed-but-unlisted colors. The size column headers give
# the size run.
_GROUPED_TABLE_RE = re.compile(
    r'<table[^>]*id="super-product-table"[^>]*>(.*?)</table>', re.S
)
_GROUPED_ROW_RE = re.compile(r'<tr class="grouped-product-row"([^>]*)>')
_SIZE_TH_RE = re.compile(r'<th class="col item" scope="col">(\d+[A-Za-z]?)</th>')
# Fallback only: the flat media gallery, grouped by image caption, for the
# rare simple (non-grouped) product with no orderable-variant table.
_GALLERY_RE = re.compile(
    r'"mage/gallery/gallery"\s*:\s*\{.*?"data"\s*:\s*(\[.*?\])\s*,', re.S
)
_DESCRIPTION_RE = re.compile(
    r'class="product attribute description">.*?class="value"[^>]*>(.*?)</div>',
    re.S,
)


def _style_from_url(product_url: str) -> str:
    slug = urlparse(product_url).path.rsplit("/", 1)[-1]
    slug = re.sub(r"\.html$", "", slug, flags=re.I)
    return re.sub(r"[^A-Za-z0-9]+", "", slug).upper()


def _row_attr(attrs: str, name: str) -> str | None:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', attrs)
    return match.group(1) if match else None


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _parse_grouped_table(table_html: str) -> tuple[list[ProductColor], str | None]:
    sizes = _SIZE_TH_RE.findall(table_html)
    size_range = None
    if sizes:
        size_range = sizes[0] if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}"

    colors: list[ProductColor] = []
    for attrs in _GROUPED_ROW_RE.findall(table_html):
        label = _row_attr(attrs, "data-gallery-color-label")
        if not label:
            continue
        images: list[str] = []
        items_json = _row_attr(attrs, "data-gallery-items-json")
        if items_json:
            try:
                items = json.loads(html.unescape(items_json))
                images = [
                    str(i.get("full"))
                    for i in items
                    if isinstance(i, dict) and i.get("full")
                ]
            except json.JSONDecodeError:
                images = []
        if not images:
            full = _row_attr(attrs, "data-gallery-full-url")
            if full:
                images = [full]
        # The color label attribute is HTML-entity encoded ("Champagne&#x2f;
        # gold"); decode before prettifying.
        name = _prettify_color(html.unescape(label))
        colors.append(
            ProductColor(name=name, slug=_slug(name), images=_dedupe(images))
        )
    return colors, size_range


def _parse_gallery_captions(detail_html: str) -> list[ProductColor]:
    """Fallback color source for simple (non-grouped) products: group the
    media-gallery images by their caption."""
    gallery_match = _GALLERY_RE.search(detail_html)
    if not gallery_match:
        return []
    try:
        gallery = json.loads(gallery_match.group(1))
    except json.JSONDecodeError:
        return []
    colors: list[ProductColor] = []
    by_name: dict[str, ProductColor] = {}
    for image in gallery:
        full = (image.get("full") or image.get("img") or "").strip()
        if not full:
            continue
        caption = _prettify_color(str(image.get("caption") or "")) or "Assorted"
        color = by_name.get(caption)
        if color is None:
            color = ProductColor(name=caption, slug=_slug(caption))
            by_name[caption] = color
            colors.append(color)
        if full not in color.images:
            color.images.append(full)
    return colors


def parse_product_detail(detail_html: str, product_url: str) -> ProductDetail:
    style_number = _style_from_url(product_url)

    title_match = re.search(r"<title>([^<]+)</title>", detail_html, re.I)
    product_title = (
        re.sub(r"-+", "-", title_match.group(1).strip()) if title_match else style_number
    )

    description_text = ""
    desc_match = _DESCRIPTION_RE.search(detail_html)
    if desc_match:
        description_text = _html_to_text(desc_match.group(1))

    colors: list[ProductColor] = []
    size_range: str | None = None
    table_match = _GROUPED_TABLE_RE.search(detail_html)
    if table_match:
        colors, size_range = _parse_grouped_table(table_match.group(1))
    if not colors:
        colors = _parse_gallery_captions(detail_html)
    if not colors:
        colors = [ProductColor(name="Assorted", slug="ASSORTED")]

    return ProductDetail(
        style_number=style_number,
        product_url=product_url,
        product_title=product_title,
        description_text=description_text,
        colors=colors,
        size_range=size_range,
    )


# ---------------------------------------------------------------------------
# Catalog row assembly.
# ---------------------------------------------------------------------------


def _catalog_rows(
    detail: ProductDetail, *, category: str, collection_url: str
) -> list[dict[str, Any]]:
    if not detail.style_number:
        raise ValueError(f"Product at {detail.product_url} has no style number")

    handle = urlparse(detail.product_url).path.rsplit("/", 1)[-1]
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
                    "platform": "magento_gallery",
                    "handle": handle,
                    "product_url": detail.product_url,
                    "source_url": collection_url,
                    "product_type": "Quinceanera Dresses",
                    "title": detail.product_title,
                },
            }
        )
    return rows


# ---------------------------------------------------------------------------
# HTTP fetchers + orchestration.
# ---------------------------------------------------------------------------


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def _category_for_url(url: str) -> str:
    lowered = url.lower()
    if "bridal" in lowered or "wedding" in lowered:
        return "bridal_gown"
    if "prom" in lowered or "pageant" in lowered or "evening" in lowered:
        return "formal_gown"
    return "quince_gown"


def _fetch_product_urls(client: httpx.Client, category_url: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        response = client.get(_category_page_url(category_url, page))
        response.raise_for_status()
        before = len(ordered)
        for url in parse_category_links(response.text):
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
        if len(ordered) == before:
            break
    return ordered


def build_seed(
    category_url: str, sleep_seconds: float = DETAIL_FETCH_SLEEP_SECONDS
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    category = _category_for_url(category_url)

    with _new_client() as client:
        product_urls = _fetch_product_urls(client, category_url)
        for index, url in enumerate(product_urls):
            try:
                detail = parse_product_detail(client.get(url).text, url)
                rows.extend(
                    _catalog_rows(detail, category=category, collection_url=category_url)
                )
            except Exception as exc:  # noqa: BLE001 — record and continue
                skipped.append({"product_url": url, "reason": str(exc)})
            if sleep_seconds and index + 1 < len(product_urls):
                time.sleep(sleep_seconds)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": category_url,
        "scope": (
            "Layla K (May Queen USA) Magento store, one catalog row per "
            "(style, color) grouped from the media-gallery captions."
        ),
        "notes": [
            "Color comes from each gallery image's caption; there is no "
            "Magento swatch/configurable data for these simple products.",
            "Run-together caption colors are space-split for the color-family "
            "filter; hex swatches and pricing are not exposed by the store.",
            "Public codes are null because the app mints them on import.",
        ],
        "product_count": len(product_urls),
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Category page URL.")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Destination JSON file."
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
