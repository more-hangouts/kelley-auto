"""Export Q by DaVinci quinceanera products into catalog seed JSON.

Q's public collection page is mostly a shell. Product cards are loaded
from DaVinci's async endpoint:

    https://davincibridal.com/async/loadmore?cat=30&page=0&filters=&detail=true

That endpoint returns style data, colors, fabrics, and two image URLs.
The image URLs are currently serialized with a bogus
``http://localhost:8080`` origin; the real files live under
``https://davincibridal.com/uploads/...``. The style detail pages expose
the fuller image set, so this scraper uses the JSON endpoint for
discovery and detail pages for image/size enrichment.

Usage:

    venv/bin/python scripts/seed_catalog/q_by_davinci.py
    venv/bin/python scripts/seed_catalog/q_by_davinci.py --max-products 10
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
from urllib.parse import urlencode, urlparse, urlunparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://qbydavinci.com/collection"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/q_by_davinci_quince.json"
DEFAULT_VENDOR_CODE = "QDV"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
ASYNC_URL = "https://davincibridal.com/async/loadmore"
DETAIL_BASE_URL = "https://qbydavinci.com/style"
IMAGE_ORIGIN = "https://davincibridal.com"
CATEGORY_ID = "30"
DETAIL_FETCH_SLEEP_SECONDS = 0.15
MAX_ASYNC_PAGES = 50


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


def _fixed_image_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    value = html.unescape(str(raw_url)).strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.netloc == "localhost:8080":
        return urlunparse(("https", "davincibridal.com", parsed.path, "", parsed.query, ""))
    if parsed.scheme in {"http", "https"}:
        return value
    if value.startswith("/uploads/"):
        return IMAGE_ORIGIN + value
    return value


def _attribute_values(item: dict[str, Any], key: str) -> list[str]:
    attrs = item.get("attributes") or {}
    out: list[str] = []
    for raw in attrs.get(key) or []:
        if not isinstance(raw, dict):
            continue
        value = _collapse_space(str(raw.get("value") or ""))
        if value:
            out.append(value)
    return out


def _attributes(item: dict[str, Any]) -> dict[str, list[str]]:
    attrs: dict[str, list[str]] = {}
    for key in ("fabric", "details", "neckline", "silhouette"):
        values = _attribute_values(item, key)
        if values:
            attrs[key] = values
    return attrs


def _async_page_url(page: int) -> str:
    return ASYNC_URL + "?" + urlencode(
        {
            "cat": CATEGORY_ID,
            "page": str(page),
            "filters": "",
            "detail": "true",
        }
    )


def _fetch_products(client: httpx.Client) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(MAX_ASYNC_PAGES):
        response = client.get(_async_page_url(page))
        response.raise_for_status()
        if not response.text.strip():
            break
        payload = response.json()
        batch = payload.get("result") or []
        if not batch:
            break
        for item in batch:
            style = str(item.get("num") or "").strip()
            if not style or style in seen:
                continue
            seen.add(style)
            products.append(item)
        total = int(payload.get("total") or 0)
        if total and len(products) >= total:
            break
    return products


def _detail_url(style_number: str) -> str:
    return f"{DETAIL_BASE_URL}/{style_number}"


def _extract_detail_image_urls(detail_html: str, style_number: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    pattern = (
        r"https://davincibridal\.com/uploads/products/quinceanera/"
        + re.escape(style_number)
        + r"-[^\"']+"
    )
    for raw in re.findall(pattern, detail_html):
        url = html.unescape(raw).strip()
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _extract_size_range(detail_html: str) -> str | None:
    match = re.search(r"<span>\s*Size:\s*</span>\s*([^<]+)</h5>", detail_html, re.I)
    if not match:
        return None
    value = _html_to_text(match.group(1))
    return value or None


def _extract_description(detail_html: str) -> str:
    meta_match = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', detail_html, re.I)
    if meta_match:
        return _html_to_text(meta_match.group(1))
    return ""


def _reachable_image_urls(client: httpx.Client, urls: list[str]) -> list[str]:
    reachable: list[str] = []
    for url in urls:
        try:
            response = client.head(url)
            if response.status_code < 400:
                reachable.append(url)
        except httpx.HTTPError:
            continue
    return reachable


def _fetch_detail(
    client: httpx.Client,
    style_number: str,
    *,
    validate_images: bool,
) -> dict[str, Any]:
    url = _detail_url(style_number)
    response = client.get(url)
    response.raise_for_status()
    detail_html = response.text
    image_urls = _extract_detail_image_urls(detail_html, style_number)
    if validate_images:
        image_urls = _reachable_image_urls(client, image_urls)
    return {
        "product_url": url,
        "image_urls": image_urls,
        "size_range": _extract_size_range(detail_html),
        "description_text": _extract_description(detail_html),
    }


def _image_urls_from_item(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in item.get("images") or []:
        url = _fixed_image_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _catalog_rows(
    item: dict[str, Any],
    detail: dict[str, Any],
    source_url: str,
) -> list[dict[str, Any]]:
    style_number = str(item.get("num") or "").strip()
    if not style_number:
        raise ValueError(f"product {item.get('id')} has no style number")

    colors = _attribute_values(item, "color") or ["Unspecified"]
    attributes = _attributes(item)
    image_urls = detail.get("image_urls") or _image_urls_from_item(item)
    description_text = detail.get("description_text") or _collapse_space(str(item.get("description") or ""))
    product_url = detail.get("product_url") or _detail_url(style_number)
    wholesale_price = str(item.get("wholesale_price") or "").strip()

    rows: list[dict[str, Any]] = []
    for color in colors:
        rows.append(
            {
                "internal_sku": f"{DEFAULT_VENDOR_CODE}-{style_number}-{_slug(color)}",
                "designer": "Q by DaVinci",
                "house_name": None,
                "style_number": style_number,
                "color": color,
                "category": "quince_gown",
                "product_title": f"Style {style_number}",
                "description_text": description_text,
                "image_urls": image_urls,
                "image_meta": [
                    {"url": url, "sequence": index + 1}
                    for index, url in enumerate(image_urls)
                ],
                "size_range": detail.get("size_range"),
                "attributes": attributes,
                "raw_tags": [],
                "public_code": None,
                "active": str(item.get("status") or "").lower() != "inactive",
                "is_sample": False,
                "wholesale_price": wholesale_price or None,
                "source": {
                    "platform": "davinci_async_loadmore",
                    "product_id": item.get("id"),
                    "handle": style_number,
                    "product_url": product_url,
                    "source_url": source_url,
                    "product_type": (item.get("category") or {}).get("title"),
                    "title": f"Style {style_number}",
                    "sort": item.get("sort"),
                    "wholesale_price": wholesale_price or None,
                },
            }
        )
    return rows


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def build_seed(
    source_url: str,
    *,
    enrich_details: bool = True,
    validate_images: bool = True,
    sleep_seconds: float = DETAIL_FETCH_SLEEP_SECONDS,
    max_products: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with _new_client() as client:
        products = _fetch_products(client)
        if max_products is not None:
            products = products[:max_products]
        for index, item in enumerate(products):
            style_number = str(item.get("num") or "").strip()
            try:
                detail = (
                    _fetch_detail(client, style_number, validate_images=validate_images)
                    if enrich_details
                    else {}
                )
                rows.extend(_catalog_rows(item, detail, source_url))
            except Exception as exc:
                skipped.append(
                    {
                        "product_id": item.get("id"),
                        "style_number": style_number,
                        "reason": str(exc),
                    }
                )
            if enrich_details and sleep_seconds and index + 1 < len(products):
                time.sleep(sleep_seconds)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "scope": (
            "Q by DaVinci collection via DaVinci async loadmore endpoint, "
            "one catalog row per color."
        ),
        "notes": [
            "The public collection page loads products from davincibridal.com/async/loadmore.",
            "Async image URLs currently use a bad localhost origin; scraper rewrites them to davincibridal.com.",
            "Detail-page enrichment captures the fuller image set and size range.",
            "Image URLs are HEAD-checked by default because some detail pages reference missing files.",
            "Public codes are null because the app mints them on import.",
            "Wholesale price is captured in seed source metadata but not imported into catalog pricing.",
        ],
        "product_count": len(products),
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Visible collection page URL.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file.",
    )
    parser.add_argument(
        "--skip-detail-enrichment",
        action="store_true",
        help="Use async endpoint only; skip per-style detail page fetches.",
    )
    parser.add_argument(
        "--skip-image-validation",
        action="store_true",
        help="Keep detail-page image URLs without HEAD-checking them.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DETAIL_FETCH_SLEEP_SECONDS,
        help="Seconds to sleep between detail-page fetches.",
    )
    parser.add_argument("--max-products", type=int, default=None)
    args = parser.parse_args()

    seed = build_seed(
        args.url,
        enrich_details=not args.skip_detail_enrichment,
        validate_images=not args.skip_image_validation,
        sleep_seconds=args.sleep,
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
