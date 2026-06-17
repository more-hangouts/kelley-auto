"""Export House of Wu (Fiesta / quinceanera) products into catalog seed JSON.

Unlike the other vendors, houseofwu.com is a WordPress site that exposes
a clean public REST API for its ``dress`` custom post type, so this
scraper needs no HTML scraping at all:

  1. Resolve the collection slug (e.g. ``fiesta``) to its
     ``dress_collection`` taxonomy term id.
  2. Page the REST list endpoint
     ``/wp-json/wp/v2/dress?dress_collection=<term>&_embed=wp:featuredmedia``
     to pull every dress with its style number (post title), description,
     taxonomy attributes (fabric/neckline/silhouette/sleeve), the
     featured (front) image, and the ACF photo fields.

Colors come from two sources, merged: the ACF ``color_images`` field
(authoritative name + hex + a representative image, present on ~85% of
dresses) and the ``dress_color`` taxonomy (covers the rest). Images from
all fields are grouped to a color by the color token embedded in each
filename (``56547_4_Blush-1.jpg`` -> Blush), mirroring how the Ariana
Vara scraper groups per-color images.

One catalog row per (style, color), same as the other vendors.

Usage:

    venv/bin/python scripts/seed_catalog/house_of_wu.py
    venv/bin/python scripts/seed_catalog/house_of_wu.py \\
        --url https://houseofwu.com/collection/mini-quince/
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
from urllib.parse import urlparse

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://houseofwu.com/collection/fiesta/"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/seeds/house_of_wu_fiesta.json"
DEFAULT_VENDOR_CODE = "WU"
DEFAULT_DESIGNER = "House of Wu"
USER_AGENT = "bellas-catalog-seed/0.1 (luis@morehangouts.com)"
ORIGIN = "https://houseofwu.com"
REST_ROOT = f"{ORIGIN}/wp-json/wp/v2"
PAGE_SIZE = 50
PAGE_FETCH_SLEEP_SECONDS = 0.5
MAX_PAGES = 25


# ---------------------------------------------------------------------------
# Small shared helpers (mirrors of the other seed scrapers).
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
    """Uppercase, hyphen-joined slug for the internal SKU (matches the
    other vendors' ``VENDOR-STYLE-COLOR`` convention)."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.upper()).strip("-")
    return normalized or "UNKNOWN"


def _match_key(value: str) -> str:
    """Lowercase alphanumeric-only key for matching a color name against
    the color token parsed from an image filename. ``"Red/Gold"`` and
    ``"RedGold"`` both collapse to ``"redgold"``."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _title_from_slug(slug: str) -> str:
    """``"sky-silver"`` -> ``"Sky Silver"`` for taxonomy-only colors that
    have no ACF display name."""
    return " ".join(part.capitalize() for part in slug.split("-") if part)


# ---------------------------------------------------------------------------
# Category mapping.
# ---------------------------------------------------------------------------


def _category_for_collection(name: str) -> str:
    lowered = name.lower()
    if "bridal" in lowered or "wedding" in lowered:
        return "bridal_gown"
    if "prom" in lowered or "pageant" in lowered or "evening" in lowered:
        return "formal_gown"
    # Fiesta, Mini Quince, and the other quinceanera lines.
    return "quince_gown"


# ---------------------------------------------------------------------------
# Per-product model.
# ---------------------------------------------------------------------------


@dataclass
class ProductColor:
    name: str
    hex_value: str | None
    slug: str
    images: list[str] = field(default_factory=list)


@dataclass
class ProductDetail:
    style_number: str
    product_url: str
    handle: str
    product_title: str | None
    description_text: str
    description_html: str
    wp_id: int | None
    colors: list[ProductColor]
    attributes: dict[str, list[str]]


_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)$", re.I)


def _image_url(node: Any) -> str | None:
    """ACF image fields are sometimes a dict, sometimes a bare URL."""
    if isinstance(node, dict):
        url = node.get("url")
        return url.strip() if isinstance(url, str) and url.strip() else None
    if isinstance(node, str) and node.strip():
        return node.strip()
    return None


def _best_color_for_filename(
    url: str, colors: list["ProductColor"]
) -> "ProductColor | None":
    """Assign an image to a color by matching the color name inside the
    filename, normalized to alphanumerics.

    Positional parsing is unreliable because House of Wu mixes two
    filename layouts (``56547_1_Sky.jpg`` and ``56530_Black_Champagne_1
    .jpg``). Substring matching handles both. The longest matching color
    name wins so a compound like ``Sky/Silver`` ("skysilver") is not
    stolen by the bare ``Sky`` ("sky") of another color on the same dress.
    """
    norm = _match_key(_IMG_EXT_RE.sub("", url.rsplit("/", 1)[-1]))
    best: ProductColor | None = None
    best_len = 0
    for color in colors:
        key = _match_key(color.name)
        if key and key in norm and len(key) > best_len:
            best, best_len = color, len(key)
    return best


def _colors_from_taxonomy(item: dict[str, Any]) -> list[tuple[str, str]]:
    """(display_name, slug) for every ``dress_color-<slug>`` in class_list."""
    out: list[tuple[str, str]] = []
    for cls in item.get("class_list") or []:
        if isinstance(cls, str) and cls.startswith("dress_color-"):
            slug = cls[len("dress_color-"):]
            out.append((_title_from_slug(slug), slug))
    return out


def _attributes_from_classlist(item: dict[str, Any]) -> dict[str, list[str]]:
    """Fabric / neckline / silhouette / sleeve, lifted from the taxonomy
    slugs WordPress stamps into ``class_list``."""
    wanted = {
        "dress_fabric": "fabric",
        "dress_silhouette": "silhouette",
        "dress_neckline": "neckline",
        "dress_sleeve": "sleeve",
    }
    out: dict[str, list[str]] = {}
    for cls in item.get("class_list") or []:
        if not isinstance(cls, str):
            continue
        for prefix, key in wanted.items():
            if cls.startswith(prefix + "-"):
                out.setdefault(key, []).append(
                    _title_from_slug(cls[len(prefix) + 1:])
                )
    return out


def parse_product(item: dict[str, Any]) -> ProductDetail:
    acf = item.get("acf") or {}
    style_number = _html_to_text((item.get("title") or {}).get("rendered")) or str(
        item.get("id")
    )
    handle = str(item.get("slug") or "")
    product_url = str(item.get("link") or f"{ORIGIN}/dress/{handle}/")
    description_html = (item.get("content") or {}).get("rendered") or ""
    description_text = _html_to_text(description_html)

    # 1. Gather every image URL across the ACF + featured fields, in
    #    priority order (featured front first) and de-duped.
    image_pool: list[str] = []
    seen_urls: set[str] = set()

    def _add_image(url: str | None) -> None:
        if url and url not in seen_urls:
            seen_urls.add(url)
            image_pool.append(url)

    embedded = (item.get("_embedded") or {}).get("wp:featuredmedia") or []
    if embedded:
        _add_image(
            embedded[0].get("source_url").strip()
            if isinstance(embedded[0].get("source_url"), str)
            else None
        )
    _add_image(_image_url(acf.get("back_photo")))
    for node in acf.get("additional_images_gallery") or []:
        _add_image(_image_url(node))

    color_image_nodes = acf.get("color_images") or []
    for entry in color_image_nodes:
        _add_image(_image_url((entry or {}).get("image")))

    # 2. Build the color list. ACF color_images is authoritative (name +
    #    hex); fall back to / augment with the dress_color taxonomy.
    colors: list[ProductColor] = []
    by_key: dict[str, ProductColor] = {}

    def _ensure_color(name: str, hex_value: str | None, slug: str) -> ProductColor:
        key = _match_key(name)
        existing = by_key.get(key)
        if existing:
            if hex_value and not existing.hex_value:
                existing.hex_value = hex_value
            return existing
        color = ProductColor(name=name, hex_value=hex_value, slug=slug)
        by_key[key] = color
        colors.append(color)
        return color

    for entry in color_image_nodes:
        name = _collapse_space(str((entry or {}).get("name") or ""))
        if not name:
            continue
        hex_value = (entry or {}).get("color") or None
        _ensure_color(name, hex_value, _slug(name))

    for name, slug in _colors_from_taxonomy(item):
        _ensure_color(name, None, _slug(name))

    if not colors:
        # No color metadata at all — emit a single "Unspecified" row so
        # the style still appears in the catalog.
        colors.append(ProductColor(name="Unspecified", hex_value=None, slug="UNSPECIFIED"))
        by_key[_match_key("Unspecified")] = colors[-1]

    # 3. Assign each image to the color named in its filename; anything
    #    unmatched rides on the first (default/front) color so no photo is
    #    dropped. Insertion order is preserved (featured image first).
    for url in image_pool:
        target = _best_color_for_filename(url, colors) or colors[0]
        target.images.append(url)

    attributes = _attributes_from_classlist(item)

    return ProductDetail(
        style_number=style_number,
        product_url=product_url,
        handle=handle,
        product_title=style_number,
        description_text=description_text,
        description_html=description_html,
        wp_id=item.get("id"),
        colors=colors,
        attributes=attributes,
    )


# ---------------------------------------------------------------------------
# Catalog row assembly.
# ---------------------------------------------------------------------------


def _catalog_rows(
    detail: ProductDetail,
    *,
    designer: str,
    house_name: str,
    category: str,
    collection_url: str,
    product_type: str,
) -> list[dict[str, Any]]:
    if not detail.style_number:
        raise ValueError(f"Product at {detail.product_url} has no style number")

    rows: list[dict[str, Any]] = []
    for color in detail.colors:
        rows.append(
            {
                "internal_sku": (
                    f"{DEFAULT_VENDOR_CODE}-{detail.style_number}-{color.slug}"
                ),
                "designer": designer,
                "house_name": house_name,
                "style_number": detail.style_number,
                "color": color.name,
                "color_hex": color.hex_value,
                "category": category,
                "product_title": detail.product_title,
                "description_text": detail.description_text,
                "description_html": detail.description_html,
                "image_urls": list(color.images),  # already list[str]
                "size_range": None,
                "attributes": detail.attributes,
                "raw_tags": [],
                "public_code": None,
                "active": True,
                "is_sample": False,
                "source": {
                    "platform": "wordpress_rest",
                    "product_id": detail.wp_id,
                    "color_hex": color.hex_value,
                    "handle": detail.handle,
                    "product_url": detail.product_url,
                    "source_url": collection_url,
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
    return httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def _collection_slug(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    # .../collection/<slug>/
    if "collection" in parts:
        idx = parts.index("collection")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1] if parts else ""


def _resolve_collection(client: httpx.Client, slug: str) -> tuple[int, str]:
    """Slug -> (term_id, display_name) via the dress_collection taxonomy."""
    response = client.get(
        f"{REST_ROOT}/dress_collection", params={"slug": slug, "per_page": 1}
    )
    response.raise_for_status()
    terms = response.json()
    if not terms:
        raise ValueError(f"No dress_collection found for slug {slug!r}")
    term = terms[0]
    return int(term["id"]), _html_to_text(term.get("name")) or slug.title()


def _fetch_collection_items(
    client: httpx.Client, term_id: int, sleep_seconds: float
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        response = client.get(
            f"{REST_ROOT}/dress",
            params={
                "dress_collection": term_id,
                "per_page": PAGE_SIZE,
                "page": page,
                "_embed": "wp:featuredmedia",
            },
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return items


# ---------------------------------------------------------------------------
# Top-level orchestration.
# ---------------------------------------------------------------------------


def build_seed(
    collection_url: str, sleep_seconds: float = PAGE_FETCH_SLEEP_SECONDS
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    slug = _collection_slug(collection_url)
    items: list[dict[str, Any]] = []
    with _new_client() as client:
        term_id, collection_name = _resolve_collection(client, slug)
        category = _category_for_collection(collection_name)
        items = _fetch_collection_items(client, term_id, sleep_seconds)

        for item in items:
            try:
                detail = parse_product(item)
                rows.extend(
                    _catalog_rows(
                        detail,
                        designer=DEFAULT_DESIGNER,
                        house_name=collection_name,
                        category=category,
                        collection_url=collection_url,
                        product_type=collection_name,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — record and continue
                skipped.append(
                    {"product_id": item.get("id"), "reason": str(exc)}
                )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_url": collection_url,
        "scope": (
            "House of Wu WordPress REST API (dress post type), one catalog "
            "row per (style, color)."
        ),
        "notes": [
            "Discovery + detail come from /wp-json/wp/v2/dress; no HTML scrape.",
            (
                "Colors merge the ACF color_images field (name + hex) with the "
                "dress_color taxonomy; images are grouped to a color by the "
                "color token in each filename."
            ),
            "color_hex, attributes, and description_html ride along unused by "
            "the current importer.",
            "Public codes are null because the app mints them on import.",
            "Pricing and per-size availability are intentionally skipped.",
        ],
        "product_count": len(items),
        "catalog_item_count": len(rows),
        "skipped": skipped,
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=DEFAULT_URL, help="Collection page URL (…/collection/<slug>/)."
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Destination JSON file."
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=PAGE_FETCH_SLEEP_SECONDS,
        help="Seconds to sleep between REST list pages.",
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
