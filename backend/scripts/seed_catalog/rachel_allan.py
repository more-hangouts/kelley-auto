"""Export Rachel Allan quinceanera products into catalog seed JSON.

Rachel Allan's site is a server-rendered storefront. The quince listing
page exposes paginated product/color detail URLs like:

    /alianna-quinceneara-gowns/style-RQ6020/14439/14558

The final path segment is color-specific, so this scraper treats each
detail URL as one catalog row. Product pages carry the active color,
gallery image URLs, size labels, description, materials, season, and
collection metadata.

Usage:

    venv/bin/python scripts/seed_catalog/rachel_allan.py
    venv/bin/python scripts/seed_catalog/rachel_allan.py --max-products 10
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://www.rachelallan.com/quince"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/rachel_allan_quince.json"
DEFAULT_VENDOR_CODE = "RACH"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
DETAIL_FETCH_SLEEP_SECONDS = 0.2
MAX_CATEGORY_PAGES = 30
ORIGIN = "https://www.rachelallan.com"


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


def _clean_collection(slug: str) -> str | None:
    value = slug.strip("/").split("/")[0].replace("-", " ")
    value = re.sub(r"\bquinceneara\b", "", value, flags=re.I)
    value = re.sub(r"\bquinceanera\b", "", value, flags=re.I)
    value = re.sub(r"\bgowns\b", "", value, flags=re.I)
    value = _collapse_space(value)
    return value.title() if value else None


def _meta_content(page_html: str, name: str) -> str | None:
    patterns = [
        rf'<meta[^>]+name="{re.escape(name)}"[^>]+content="([^"]*)"',
        rf'<meta[^>]+property="{re.escape(name)}"[^>]+content="([^"]*)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return None


def _canonical_url(page_html: str, fallback_url: str) -> str:
    match = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', page_html, re.I)
    if match:
        return html.unescape(match.group(1)).strip()
    return fallback_url


def _category_page_url(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["pg"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


_PRODUCT_HREF_RE = re.compile(
    r'href="(?P<href>https://www\.rachelallan\.com/[^"]+/style-(?P<style>[A-Z0-9]+)/(?P<product_id>\d+)/(?P<color_id>\d+))"',
    re.I,
)


def parse_category_links(category_html: str, base_url: str) -> list[dict[str, str]]:
    """Return ordered detail links from one listing page."""

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for match in _PRODUCT_HREF_RE.finditer(category_html):
        url = urljoin(base_url, html.unescape(match.group("href")))
        if url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "style_number": match.group("style").upper(),
                "product_id": match.group("product_id"),
                "color_id": match.group("color_id"),
                "product_url": url,
            }
        )
    return out


def _extract_section(page_html: str, start_marker: str, end_marker: str) -> str:
    start = page_html.find(start_marker)
    if start < 0:
        return ""
    end = page_html.find(end_marker, start)
    return page_html[start:] if end < 0 else page_html[start:end]


def _extract_gallery_image_urls(page_html: str) -> list[str]:
    gallery = _extract_section(page_html, '<div class="gallery-images">', '<div class="gallery-content')
    urls: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r'(?:srcset|src)="([^"]+)"', gallery):
        url = html.unescape(raw).strip()
        if not url.startswith("https://media.rachelallan.com/products/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _extract_color(page_html: str) -> str:
    match = re.search(r"<p>\s*Color:\s*<span>\s*&nbsp;\s*([^<]+)</span>\s*</p>", page_html, re.I)
    if not match:
        raise ValueError("active color not found")
    return _collapse_space(html.unescape(match.group(1))).title()


def _extract_style_number(page_html: str, product_url: str) -> str:
    h1_match = re.search(r'<h1>\s*([A-Z]{1,6}\d{2,8})\s*</h1>', page_html, re.I)
    if h1_match:
        return h1_match.group(1).upper()
    url_match = re.search(r"/style-([A-Z0-9]+)/", product_url, re.I)
    if url_match:
        return url_match.group(1).upper()
    raise ValueError("style number not found")


def _extract_content_blocks(page_html: str) -> list[str]:
    section = _extract_section(page_html, '<div class="content-description">', "<!-- <div class=\"content\">")
    return [
        block.strip()
        for block in re.findall(r'<div class="content">\s*(.*?)\s*</div>', section, re.S)
    ]


def _extract_size_labels(page_html: str) -> list[str]:
    section = _extract_section(page_html, '<div class="size-list">', '<ul class="content-list">')
    labels: list[str] = []
    for match in re.finditer(r"<span[^>]*>([^<]+)</span>", section, re.S):
        label = _collapse_space(html.unescape(match.group(1)))
        if label:
            labels.append(label)
    return labels


def _product_title(page_html: str, style_number: str) -> str | None:
    title = _meta_content(page_html, "og_title") or _meta_content(page_html, "twitter:title")
    if not title:
        title_match = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
        title = _html_to_text(title_match.group(1)) if title_match else None
    if not title:
        return None
    title = re.sub(rf"\s*\|\s*Style\s*-\s*{re.escape(style_number)}\s*$", "", title, flags=re.I)
    title = re.sub(r"\s+in\s+[^|]*?\s+Color\s*$", "", title, flags=re.I)
    return _collapse_space(title)


def _attributes(page_html: str) -> dict[str, list[str]]:
    keywords = _meta_content(page_html, "keywords") or ""
    parts = [_collapse_space(p) for p in keywords.split(",") if _collapse_space(p)]
    attrs: dict[str, list[str]] = {}
    if len(parts) >= 4:
        attrs["neckline"] = [parts[3].title()]
    if len(parts) >= 5:
        attrs["fabric"] = [p.strip().title() for p in parts[4].split("/") if p.strip()]
    if len(parts) >= 6:
        attrs["silhouette"] = [parts[5].title()]

    description = _meta_content(page_html, "description") or ""
    season_match = re.search(r"Season\s+([A-Za-z]+\s+\d{4})", description)
    if season_match:
        attrs["season"] = [_collapse_space(season_match.group(1)).title()]

    blocks = _extract_content_blocks(page_html)
    if len(blocks) >= 2:
        materials = _html_to_text(blocks[1])
        if materials:
            attrs["materials"] = [
                _collapse_space(p).title()
                for p in materials.split(",")
                if _collapse_space(p)
            ]
    return attrs


@dataclass
class ProductDetail:
    style_number: str
    product_url: str
    product_id: str | None
    color_id: str | None
    color: str
    collection: str | None
    product_title: str | None
    description_html: str
    description_text: str
    image_urls: list[str]
    size_range: str | None
    size_labels: list[str]
    attributes: dict[str, list[str]]


def parse_product_detail(page_html: str, product_url: str) -> ProductDetail:
    canonical = _canonical_url(page_html, product_url)
    parsed = urlparse(canonical)
    path_parts = [p for p in parsed.path.split("/") if p]
    product_id = path_parts[-2] if len(path_parts) >= 2 and path_parts[-2].isdigit() else None
    color_id = path_parts[-1] if path_parts and path_parts[-1].isdigit() else None
    collection = _clean_collection(path_parts[0]) if path_parts else None
    style_number = _extract_style_number(page_html, canonical)
    color = _extract_color(page_html)
    blocks = _extract_content_blocks(page_html)
    description_html = html.unescape(blocks[0]) if blocks else ""
    description_text = _html_to_text(description_html)
    size_labels = _extract_size_labels(page_html)
    size_range = f"{size_labels[0]} - {size_labels[-1]}" if size_labels else None

    return ProductDetail(
        style_number=style_number,
        product_url=canonical,
        product_id=product_id,
        color_id=color_id,
        color=color,
        collection=collection,
        product_title=_product_title(page_html, style_number),
        description_html=description_html,
        description_text=description_text,
        image_urls=_extract_gallery_image_urls(page_html),
        size_range=size_range,
        size_labels=size_labels,
        attributes=_attributes(page_html),
    )


def _catalog_row(detail: ProductDetail, category_url: str) -> dict[str, Any]:
    return {
        "internal_sku": f"{DEFAULT_VENDOR_CODE}-{detail.style_number}-{_slug(detail.color)}",
        "designer": "Rachel Allan",
        "house_name": detail.collection,
        "style_number": detail.style_number,
        "color": detail.color,
        "category": "quince_gown",
        "product_title": detail.product_title,
        "description_text": detail.description_text,
        "description_html": detail.description_html,
        "image_urls": detail.image_urls,
        "image_meta": [
            {"url": url, "sequence": index + 1}
            for index, url in enumerate(detail.image_urls)
        ],
        "size_range": detail.size_range,
        "size_labels": detail.size_labels,
        "attributes": detail.attributes,
        "raw_tags": [],
        "public_code": None,
        "active": True,
        "is_sample": False,
        "source": {
            "platform": "rachel_allan_html",
            "product_id": detail.product_id,
            "color_id": detail.color_id,
            "handle": detail.style_number.lower(),
            "product_url": detail.product_url,
            "source_url": category_url,
            "product_type": detail.collection,
            "title": detail.product_title,
        },
    }


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _fetch_product_urls(client: httpx.Client, category_url: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for page in range(1, MAX_CATEGORY_PAGES + 1):
        page_url = _category_page_url(category_url, page)
        page_response = client.get(page_url)
        page_response.raise_for_status()
        html_text = page_response.text
        links = parse_category_links(html_text, page_url)
        if not links:
            break
        before = len(ordered)
        for link in links:
            url = link["product_url"]
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
        if len(ordered) == before:
            break
    return ordered


def _fetch_product_detail_html(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    return response.text


def build_seed(
    category_url: str,
    *,
    sleep_seconds: float = DETAIL_FETCH_SLEEP_SECONDS,
    max_products: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with _new_client() as client:
        product_urls = _fetch_product_urls(client, category_url)
        if max_products is not None:
            product_urls = product_urls[:max_products]

        for index, url in enumerate(product_urls):
            try:
                detail_html = _fetch_product_detail_html(client, url)
                detail = parse_product_detail(detail_html, url)
                rows.append(_catalog_row(detail, category_url))
            except Exception as exc:
                skipped.append({"product_url": url, "reason": str(exc)})
            if sleep_seconds and index + 1 < len(product_urls):
                time.sleep(sleep_seconds)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": category_url,
        "scope": "Rachel Allan quince HTML storefront, one catalog row per color detail URL.",
        "notes": [
            "Listing pages expose color-specific product URLs; each detail URL is one catalog row.",
            "Description, materials, size labels, season, and gallery image URLs are captured from the detail page.",
            "Public codes are null because the app mints them on import.",
            "Pricing and per-size availability are intentionally skipped.",
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
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="Optional cap for quick parser checks.",
    )
    args = parser.parse_args()

    seed = build_seed(args.url, sleep_seconds=args.sleep, max_products=args.max_products)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote {seed['catalog_item_count']} catalog row(s)"
        f" from {seed['product_count']} product URL(s) to {args.output}"
    )
    if seed["skipped"]:
        print(f"Skipped {len(seed['skipped'])} product URL(s).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
