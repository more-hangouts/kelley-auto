"""Fixture-based smoke test for the Ariana Vara scraper.

Unlike the catalog smoke tests this exercises pure parsing — no DB, no
network. The fixture at `tests/fixtures/arianavara_pr30248.html` was
captured from the live site and is checked in so layout changes show up
as deterministic test failures instead of a surprise empty seed.

The scraper is more parser-sensitive than Morilee's JSON feed, so this
test asserts the high-value invariants:

  - one product detail page yields the expected number of color rows
  - color_hex is captured for each swatch (slash-separated combos kept
    as-is)
  - size_range and the attribute block both round-trip
  - images are grouped by color via data-value-id, not naive substring
    matches against image filenames
  - face/crop labels parsed from <img> alt text are populated
  - image URLs are upgraded from .340.webp thumbnails to .2000.webp
    zoom URLs
  - the resulting catalog rows carry the importer-required keys
    (internal_sku, color, category, product_title, image_urls)

Runs as a script:

    venv/bin/python tests/test_ariana_vara_scraper_smoke.py

Internal helpers are named `check_*` rather than `test_*` so a broad
`pytest tests/` sweep does not collect them as parameterless tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.seed_catalog.ariana_vara import (  # noqa: E402
    DEFAULT_VENDOR_CODE,
    _catalog_rows,
    parse_category_links,
    parse_product_detail,
)


_FIXTURE_PATH = _REPO_ROOT / "tests/fixtures/arianavara_pr30248.html"
_PRODUCT_URL = (
    "https://www.arianavara.com/princesa-quinceanera-dresses/fall-2026/pr30248"
)
_CATEGORY_URL = "https://www.arianavara.com/categories/latest-collection"


def _load_detail():
    detail_html = _FIXTURE_PATH.read_text(encoding="utf-8")
    return parse_product_detail(detail_html, _PRODUCT_URL)


def check_style_and_title() -> None:
    detail = _load_detail()
    assert detail.style_number == "PR30248", detail.style_number
    assert detail.product_title == (
        "Oversized Bow Drama Gown w/ Glitter Tulle"
    ), detail.product_title
    assert detail.handle == "pr30248", detail.handle
    assert "glitter tulle" in detail.description_text.lower(), detail.description_text


def check_breadcrumb_and_source_meta() -> None:
    detail = _load_detail()
    # Breadcrumb on this fixture is Home / Princesa Quinceanera Dresses /
    # Fall 2026 / PR30248
    names = detail.breadcrumb_names
    assert len(names) >= 4, names
    assert names[1] == "Princesa Quinceanera Dresses", names
    assert names[2] == "Fall 2026", names
    assert names[-1].upper() == "PR30248", names
    assert detail.syvo_product_id is not None
    assert detail.syvo_vendor_account == "13735", detail.syvo_vendor_account


def check_colors_and_hex() -> None:
    detail = _load_detail()
    by_name = {c.name: c for c in detail.colors}
    assert "Navy Blue/Rose Gold" in by_name, sorted(by_name)
    assert "Rose Gold" in by_name, sorted(by_name)
    assert by_name["Navy Blue/Rose Gold"].hex_value == "#2f3c71/#bfac9e"
    assert by_name["Rose Gold"].hex_value == "#ddc3b4"
    assert by_name["Navy Blue/Rose Gold"].slug == "NAVY-BLUE-ROSE-GOLD"
    assert by_name["Rose Gold"].slug == "ROSE-GOLD"


def check_image_grouping_and_zoom_urls() -> None:
    detail = _load_detail()
    by_name = {c.name: c for c in detail.colors}
    nbr = by_name["Navy Blue/Rose Gold"]
    rg = by_name["Rose Gold"]

    # Every image URL belongs to that color's filename slug.
    for img in nbr.images:
        assert "navyblue-rosegold" in img.url, img.url
        assert img.url.endswith(".2000.webp"), img.url
    for img in rg.images:
        assert "rosegold" in img.url and "navyblue" not in img.url, img.url
        assert img.url.endswith(".2000.webp"), img.url

    assert nbr.images, "Navy Blue/Rose Gold should have at least one image"
    assert rg.images, "Rose Gold should have at least one image"

    # First-by-sequence image of the swatch with `default` in alt text
    # marks the swatch's primary thumbnail.
    primary = next((img for img in nbr.images if img.is_default), None)
    assert primary is not None, "Navy Blue/Rose Gold should have a default image"
    assert primary.face == "front", primary.face

    faces = {img.face for color in detail.colors for img in color.images}
    crops = {img.crop for color in detail.colors for img in color.images}
    assert "front" in faces, faces
    # Both full and cropped variants are present in this fixture.
    assert {"full", "cropped"}.issubset(crops), crops


def check_size_and_attributes() -> None:
    detail = _load_detail()
    assert detail.size_range == "00 - 26", detail.size_range
    attrs = detail.attributes
    assert attrs.get("length") == ["Long"], attrs.get("length")
    assert attrs.get("neckline") == ["Sweetheart"], attrs.get("neckline")
    assert attrs.get("silhouette") == ["Ball Gown"], attrs.get("silhouette")
    assert attrs.get("waistline") == ["Basque"], attrs.get("waistline")
    assert "Tulle" in (attrs.get("fabric") or []), attrs.get("fabric")
    assert "Sequin" in (attrs.get("fabric") or []), attrs.get("fabric")
    assert "Capelet Included" in (attrs.get("special_features") or []), (
        attrs.get("special_features")
    )


def check_catalog_rows_shape() -> None:
    detail = _load_detail()
    rows = _catalog_rows(detail, _CATEGORY_URL)
    assert len(rows) == len(detail.colors), (len(rows), len(detail.colors))

    required_keys = {
        "internal_sku",
        "designer",
        "house_name",
        "style_number",
        "color",
        "color_hex",
        "category",
        "product_title",
        "description_text",
        "image_urls",
        "image_meta",
        "size_range",
        "attributes",
        "source",
    }
    for row in rows:
        missing = required_keys - row.keys()
        assert not missing, f"row missing keys: {missing}"
        assert row["designer"] == "Ariana Vara"
        assert row["house_name"] == "Princesa"
        assert row["category"] == "quince_gown", row["category"]
        assert row["internal_sku"].startswith(f"{DEFAULT_VENDOR_CODE}-PR30248-")
        src = row["source"]
        assert src["platform"] == "syvo_storefront"
        assert src["product_url"] == _PRODUCT_URL
        assert src["source_url"] == _CATEGORY_URL
        assert src["product_type"] == "Princesa Quinceanera Dresses › Fall 2026"
        assert len(row["image_urls"]) == len(row["image_meta"])

    skus = [row["internal_sku"] for row in rows]
    assert len(set(skus)) == len(skus), f"duplicate internal_sku: {skus}"


def check_category_link_parser() -> None:
    # Exercise the category-page parser on a synthetic fragment so we
    # don't ship a multi-MB category fixture. Color-deeplink siblings
    # (e.g. /pr30254/petal) should NOT be picked up — only the canonical
    # one-product-per-style link wrapped by the aria-label.
    fragment = """
    <div class="product mc-item" data-product-id="455">
      <a href="/princesa-quinceanera-dresses/fall-2026/pr30254"
         class="product-image"
         aria-label="Go to PR30254 product details page">img</a>
      <a href="/princesa-quinceanera-dresses/fall-2026/pr30254/petal"
         aria-label="Petal swatch">color</a>
    </div>
    <div class="product mc-item" data-product-id="442">
      <a href="/princesa-quinceanera-dresses/fall-2026/pr30241"
         aria-label="Go to PR30241 product details page">img</a>
    </div>
    """
    links = parse_category_links(fragment, _CATEGORY_URL)
    urls = [l["product_url"] for l in links]
    styles = [l["style_number"] for l in links]
    assert urls == [
        "https://www.arianavara.com/princesa-quinceanera-dresses/fall-2026/pr30254",
        "https://www.arianavara.com/princesa-quinceanera-dresses/fall-2026/pr30241",
    ], urls
    assert styles == ["PR30254", "PR30241"], styles


def main() -> int:
    if not _FIXTURE_PATH.exists():
        print(f"fixture missing: {_FIXTURE_PATH}", file=sys.stderr)
        return 1

    check_style_and_title()
    print("style + title ok")
    check_breadcrumb_and_source_meta()
    print("breadcrumb + source meta ok")
    check_colors_and_hex()
    print("colors + hex ok")
    check_image_grouping_and_zoom_urls()
    print("image grouping + zoom URLs ok")
    check_size_and_attributes()
    print("size + attributes ok")
    check_catalog_rows_shape()
    print("catalog rows shape ok")
    check_category_link_parser()
    print("category link parser ok")
    print()
    print("ariana vara scraper smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
