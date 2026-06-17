"""Script smoke test for the Q by DaVinci scraper.

Runs as:

    venv/bin/python tests/test_q_by_davinci_scraper_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.seed_catalog.q_by_davinci import (  # noqa: E402
    DEFAULT_VENDOR_CODE,
    _catalog_rows,
    _extract_detail_image_urls,
    _extract_size_range,
    _fixed_image_url,
)


def check_image_url_rewrite() -> None:
    assert (
        _fixed_image_url("http://localhost:8080/uploads/products/quinceanera/80690-A.jpg")
        == "https://davincibridal.com/uploads/products/quinceanera/80690-A.jpg"
    )
    assert (
        _fixed_image_url("/uploads/products/quinceanera/80690-B.jpg")
        == "https://davincibridal.com/uploads/products/quinceanera/80690-B.jpg"
    )


def check_detail_extractors() -> None:
    html = """
    <img src="https://davincibridal.com/uploads/products/quinceanera/80690-A.jpg">
    <a data-image="https://davincibridal.com/uploads/products/quinceanera/80690-C.jpg"></a>
    <a data-image="https://davincibridal.com/uploads/products/quinceanera/80690-A.jpg"></a>
    <img src="https://davincibridal.com/uploads/products/quinceanera/80431-A.jpg">
    <h5><span>Size: </span>0 - 30</h5>
    """
    assert _extract_detail_image_urls(html, "80690") == [
        "https://davincibridal.com/uploads/products/quinceanera/80690-A.jpg",
        "https://davincibridal.com/uploads/products/quinceanera/80690-C.jpg",
    ]
    assert _extract_size_range(html) == "0 - 30"


def check_catalog_rows_shape() -> None:
    item = {
        "id": "2092",
        "num": "80690",
        "description": "",
        "images": [
            "http://localhost:8080/uploads/products/quinceanera/80690-A.jpg",
            "http://localhost:8080/uploads/products/quinceanera/80690-C.jpg",
        ],
        "wholesale_price": "599",
        "status": "active",
        "sort": "8540",
        "category": {"title": "Quinceanera"},
        "attributes": {
            "color": [{"value": "Black"}, {"value": "Lilac"}],
            "fabric": [{"value": "Embroidery"}, {"value": "Glitter Tulle"}],
            "silhouette": [{"value": "Ball Gown"}],
        },
    }
    detail = {
        "product_url": "https://qbydavinci.com/style/80690",
        "image_urls": [
            "https://davincibridal.com/uploads/products/quinceanera/80690-A.jpg",
            "https://davincibridal.com/uploads/products/quinceanera/80690-B.jpg",
        ],
        "size_range": "0 - 30",
        "description_text": "",
    }
    rows = _catalog_rows(item, detail, "https://qbydavinci.com/collection")
    assert len(rows) == 2, rows
    assert rows[0]["internal_sku"] == f"{DEFAULT_VENDOR_CODE}-80690-BLACK", rows
    assert rows[1]["internal_sku"] == f"{DEFAULT_VENDOR_CODE}-80690-LILAC", rows
    assert rows[0]["designer"] == "Q by DaVinci", rows
    assert rows[0]["category"] == "quince_gown", rows
    assert rows[0]["image_urls"] == detail["image_urls"], rows
    assert rows[0]["size_range"] == "0 - 30", rows
    assert rows[0]["attributes"]["fabric"] == ["Embroidery", "Glitter Tulle"], rows
    assert rows[0]["source"]["platform"] == "davinci_async_loadmore", rows


def main() -> int:
    check_image_url_rewrite()
    print("image URL rewrite ok")
    check_detail_extractors()
    print("detail extractors ok")
    check_catalog_rows_shape()
    print("catalog rows shape ok")
    print()
    print("q by davinci scraper smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
