"""Fixture-style smoke test for the Rachel Allan scraper.

Runs as a script:

    venv/bin/python tests/test_rachel_allan_scraper_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.seed_catalog.rachel_allan import (  # noqa: E402
    DEFAULT_VENDOR_CODE,
    _catalog_row,
    parse_category_links,
    parse_product_detail,
)


_CATEGORY_HTML = """
<a href="https://www.rachelallan.com/alianna-quinceneara-gowns/style-RQ6020/14439/14558">
  <h2>RQ6020</h2>
</a>
<a href="https://www.rachelallan.com/alianna-quinceneara-gowns/style-RQ6020/14439/14558">
  duplicate image link
</a>
<a href="https://www.rachelallan.com/alta-couture-quinceanera-gowns/style-RQ3180/14429/14538">
  <h2>RQ3180</h2>
</a>
"""


_DETAIL_HTML = """
<html>
  <head>
    <title>Sweetheart Ball Gown Alianna Quinceneara Gowns in Blue Color | Style - RQ6020</title>
    <meta name="keywords" content="Alianna Dresses,RQ6020,Blue,Sweetheart,ORGANZA JACQUARD,Ball Gown" />
    <meta name="description" content="Rachel Allan Sweetheart Ball Gown Alianna Quinceneara Gowns in Blue Color for Season Fall 2026 with Style Code - RQ6020 and Fabric - ORGANZA JACQUARD" />
    <meta name="og_title" property="og:title" content="Sweetheart Ball Gown Alianna Quinceneara Gowns in Blue Color | Style - RQ6020" />
    <link rel="canonical" href="https://www.rachelallan.com/alianna-quinceneara-gowns/style-RQ6020/14439/14558" />
  </head>
  <body>
    <div class="gallery-images">
      <div class="image-list">
        <picture>
          <source srcset="https://media.rachelallan.com/products/alianna-quinceneara-gowns-1.webp" type="image/webp">
          <img src="https://media.rachelallan.com/products/alianna-quinceneara-gowns-1.webp" alt="image1">
        </picture>
        <picture>
          <source srcset="https://media.rachelallan.com/products/alianna-quinceneara-gowns-2.webp" type="image/webp">
          <img src="https://media.rachelallan.com/products/alianna-quinceneara-gowns-2.webp" alt="image2">
        </picture>
      </div>
    </div>
    <div class="gallery-content pb-0">
      <h1>RQ6020</h1>
      <div class="color-list">
        <p>Color:<span>&nbsp;BLUE</span></p>
      </div>
      <div class="size-list">
        <div class="number">
          <span title="Bust 31">00</span>
          <span title="Bust 32">0</span>
          <span title="Bust 33">2</span>
        </div>
        <div class="number mt-2">
          <span title="Bust 50">22W</span>
        </div>
      </div>
      <ul class="content-list">
        <li class="active">Description</li>
        <li>Product Materials</li>
      </ul>
      <div class="content-description">
        <div class="content">
          <p>Indulge in regal glamour with this voluminous ball gown.</p>
        </div>
        <div class="content">
          <p>ORGANZA JACQUARD, TULLE, APPLIQUE</p>
        </div>
        <!-- <div class="content">
          <p>Shipping text</p>
        </div> -->
      </div>
    </div>
  </body>
</html>
"""


def check_category_link_parser() -> None:
    links = parse_category_links(_CATEGORY_HTML, "https://www.rachelallan.com/quince")
    assert [link["style_number"] for link in links] == ["RQ6020", "RQ3180"], links
    assert [link["product_id"] for link in links] == ["14439", "14429"], links
    assert [link["color_id"] for link in links] == ["14558", "14538"], links


def check_detail_parser_and_catalog_row() -> None:
    url = "https://www.rachelallan.com/alianna-quinceneara-gowns/style-RQ6020/14439/14558"
    detail = parse_product_detail(_DETAIL_HTML, url)
    assert detail.style_number == "RQ6020", detail.style_number
    assert detail.color == "Blue", detail.color
    assert detail.collection == "Alianna", detail.collection
    assert detail.product_id == "14439", detail.product_id
    assert detail.color_id == "14558", detail.color_id
    assert detail.size_range == "00 - 22W", detail.size_range
    assert detail.size_labels == ["00", "0", "2", "22W"], detail.size_labels
    assert len(detail.image_urls) == 2, detail.image_urls
    assert "regal glamour" in detail.description_text, detail.description_text
    assert detail.attributes["season"] == ["Fall 2026"], detail.attributes
    assert detail.attributes["materials"] == [
        "Organza Jacquard",
        "Tulle",
        "Applique",
    ], detail.attributes

    row = _catalog_row(detail, "https://www.rachelallan.com/quince")
    assert row["internal_sku"] == f"{DEFAULT_VENDOR_CODE}-RQ6020-BLUE", row
    assert row["designer"] == "Rachel Allan", row
    assert row["house_name"] == "Alianna", row
    assert row["category"] == "quince_gown", row
    assert row["source"]["platform"] == "rachel_allan_html", row["source"]


def main() -> int:
    check_category_link_parser()
    print("category link parser ok")
    check_detail_parser_and_catalog_row()
    print("detail parser + catalog row ok")
    print()
    print("rachel allan scraper smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
