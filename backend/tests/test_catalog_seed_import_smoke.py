"""Smoke test for ``scripts/seed_catalog/import_seed.py`` refresh path.

Inserts catalog rows in three different drift states, builds a tmp
seed JSON, and exercises every import mode against the live DB:

  - default mode: existing rows with diffs become ``skipped_with_diff``.
  - ``--dry-run``: no DB writes, but diffs are still computed.
  - ``--update-existing``: applies the diff through
    ``refresh_catalog_item``; bumps ``updated_at``; respects the
    allowlist (designer/style/color/category never change); honors
    the conservative "no nulling" rule and the
    "house_name only when existing is null" rule.

Exits 1 on any failure. Cleans up by ``_PREFIX`` at the end and resets
``catalog_public_code_seq`` to its baseline so reruns are idempotent.

Runs as a script:

    venv/bin/python tests/test_catalog_seed_import_smoke.py

Internal helpers are named ``check_*`` so a broad ``pytest tests/``
sweep does not collect them as parameterless tests.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
    get_by_internal_sku,
)

from scripts.seed_catalog.import_seed import import_seed  # noqa: E402


_PREFIX = f"TEST-IMPORT-{uuid.uuid4().hex[:8].upper()}-"

SKU_NEW = _PREFIX + "NEW"
SKU_NO_DIFF = _PREFIX + "NODIFF"
SKU_IMG_DRIFT = _PREFIX + "IMGDRIFT"
SKU_HOUSE_PRESERVE = _PREFIX + "HOUSEPRESERVE"
SKU_HOUSE_FILL = _PREFIX + "HOUSEFILL"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(db.execute(sql_text(
            "SELECT catalog_public_code_seq FROM numbering_state WHERE id = 1"
        )).scalar())
    finally:
        db.close()


def _reset_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text(
            "UPDATE numbering_state SET catalog_public_code_seq = :s WHERE id = 1"
        ), {"s": value})
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    p = _PREFIX + "%"
    db = SessionLocal()
    try:
        db.execute(sql_text(
            "DELETE FROM catalog_items WHERE internal_sku LIKE :p"
        ), {"p": p})
        db.commit()
    finally:
        db.close()


def _seed_initial_rows() -> None:
    """Insert four pre-existing rows. SKU_NEW is intentionally not
    inserted — that's the create path."""

    db = SessionLocal()
    try:
        create_catalog_item(db, CatalogItemInput(
            internal_sku=SKU_NO_DIFF,
            color="Sky", category="quince_gown",
            designer="Vendor Z", style_number="Z1",
            product_title="Stable Title",
            description_text="stable description",
            image_urls=["https://cdn.example.com/z1_a.webp"],
            source_platform="syvo_storefront",
            source_product_id="100",
            source_product_handle="z1",
            source_product_url="https://vendor-z.example.com/products/z1",
            source_collection_url="https://vendor-z.example.com/categories/main",
            source_product_type="Quince",
        ))
        create_catalog_item(db, CatalogItemInput(
            internal_sku=SKU_IMG_DRIFT,
            color="Coral", category="quince_gown",
            designer="Vendor Z", style_number="Z2",
            product_title="Stale Title",
            description_text="stale description",
            image_urls=["https://cdn.example.com/z2_old1.webp"],
            source_platform="syvo_storefront",
            source_product_id="200",
            source_product_handle="z2",
            source_product_url="https://vendor-z.example.com/products/z2",
            source_collection_url="https://vendor-z.example.com/categories/main",
            source_product_type="Quince",
        ))
        create_catalog_item(db, CatalogItemInput(
            internal_sku=SKU_HOUSE_PRESERVE,
            color="Mint", category="quince_gown",
            designer="Vendor Z", style_number="Z3",
            house_name="Staff-Curated",  # must NOT be overwritten
            product_title="Title",
            description_text="desc",
            image_urls=["https://cdn.example.com/z3_a.webp"],
        ))
        create_catalog_item(db, CatalogItemInput(
            internal_sku=SKU_HOUSE_FILL,
            color="Rose", category="quince_gown",
            designer="Vendor Z", style_number="Z4",
            house_name=None,  # null — refresh CAN fill it
            product_title="Title",
            description_text="desc",
            image_urls=["https://cdn.example.com/z4_a.webp"],
        ))
        db.commit()
    finally:
        db.close()


def _write_seed(tmpdir: Path) -> Path:
    items = [
        # New row: doesn't exist in DB → create.
        {
            "internal_sku": SKU_NEW,
            "designer": "Vendor Z",
            "style_number": "Z0",
            "color": "Pearl",
            "category": "quince_gown",
            "product_title": "Brand-New Row",
            "description_text": "fresh from vendor",
            "image_urls": ["https://cdn.example.com/z0.webp"],
            "house_name": None,
            "source": {
                "platform": "syvo_storefront",
                "product_id": 9000,
                "handle": "z0",
                "product_url": "https://vendor-z.example.com/products/z0",
                "source_url": "https://vendor-z.example.com/categories/main",
                "product_type": "Quince",
                "title": "Brand-New Row",
            },
            "is_sample": False,
            "active": True,
        },
        # Identical to DB → no diff.
        {
            "internal_sku": SKU_NO_DIFF,
            "designer": "Vendor Z",
            "style_number": "Z1",
            "color": "Sky",
            "category": "quince_gown",
            "product_title": "Stable Title",
            "description_text": "stable description",
            "image_urls": ["https://cdn.example.com/z1_a.webp"],
            "house_name": None,
            "source": {
                "platform": "syvo_storefront",
                "product_id": 100,
                "handle": "z1",
                "product_url": "https://vendor-z.example.com/products/z1",
                "source_url": "https://vendor-z.example.com/categories/main",
                "product_type": "Quince",
                "title": "Stable Title",
            },
        },
        # Drift on multiple allowlist fields. Note we deliberately
        # change designer/style_number/color in seed too, to verify
        # the importer's refresh path does NOT propagate ID-ish
        # fields even when seed disagrees.
        {
            "internal_sku": SKU_IMG_DRIFT,
            "designer": "Vendor RENAMED",   # must NOT propagate
            "style_number": "Z2-NEW",       # must NOT propagate
            "color": "Coral",
            "category": "quince_gown",
            "product_title": "Refreshed Title",
            "description_text": "refreshed description",
            "image_urls": [
                "https://cdn.example.com/z2_new1.webp",
                "https://cdn.example.com/z2_new2.webp",
            ],
            "house_name": None,
            "source": {
                "platform": "syvo_storefront",
                "product_id": 200,
                "handle": "z2",
                "product_url": "https://vendor-z.example.com/products/z2",
                "source_url": "https://vendor-z.example.com/categories/main",
                "product_type": "Quince",
                "title": "Refreshed Title",
            },
        },
        # Existing has staff-curated house_name; seed proposes a
        # different one — the importer must NOT overwrite.
        {
            "internal_sku": SKU_HOUSE_PRESERVE,
            "designer": "Vendor Z",
            "style_number": "Z3",
            "color": "Mint",
            "category": "quince_gown",
            "house_name": "Vendor-Default",   # must NOT overwrite "Staff-Curated"
            "product_title": "Title",
            "description_text": "desc",
            "image_urls": ["https://cdn.example.com/z3_a.webp"],
        },
        # Existing has null house_name; seed has one → refresh must
        # fill it in.
        {
            "internal_sku": SKU_HOUSE_FILL,
            "designer": "Vendor Z",
            "style_number": "Z4",
            "color": "Rose",
            "category": "quince_gown",
            "house_name": "Vendor-Default",
            "product_title": "Title",
            "description_text": "desc",
            "image_urls": ["https://cdn.example.com/z4_a.webp"],
        },
    ]
    payload = {"items": items}
    path = tmpdir / "vendor_z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_default_mode_surfaces_drift_without_writing(seed_path: Path) -> None:
    summary = import_seed(seed_path, update_existing=False, dry_run=False)
    assert summary["mode"] == "default", summary["mode"]
    counts = summary["counts"]
    # Created: SKU_NEW. No diff: SKU_NO_DIFF and SKU_HOUSE_PRESERVE
    # (house_name preservation rule means seed's "Vendor-Default"
    # never gets compared against the existing "Staff-Curated"; every
    # other field matches). Drift: SKU_IMG_DRIFT and SKU_HOUSE_FILL.
    assert counts["created"] == 1, counts
    assert counts["skipped_no_diff"] == 2, counts
    assert counts["skipped_with_diff"] == 2, counts
    assert counts["updated"] == 0, counts

    # SKU_HOUSE_PRESERVE has no diff (house_name preserved, all other
    # fields match). SKU_HOUSE_FILL has a diff (house_name None -> set).
    drift_skus = {
        e["internal_sku"] for e in summary["skipped_with_diff_skus"]
    }
    assert SKU_IMG_DRIFT in drift_skus, drift_skus
    assert SKU_HOUSE_FILL in drift_skus, drift_skus
    assert SKU_HOUSE_PRESERVE not in drift_skus, drift_skus

    db = SessionLocal()
    try:
        # The new SKU was created (default mode is create-on-missing).
        assert get_by_internal_sku(db, SKU_NEW) is not None
        # Drift row was NOT updated.
        drifted = get_by_internal_sku(db, SKU_IMG_DRIFT)
        assert drifted.product_title == "Stale Title", drifted.product_title
        assert drifted.image_urls == [
            "https://cdn.example.com/z2_old1.webp"
        ], drifted.image_urls
    finally:
        db.close()


def check_dry_run_writes_nothing(seed_path: Path) -> None:
    db = SessionLocal()
    try:
        before = get_by_internal_sku(db, SKU_IMG_DRIFT)
        before_title = before.product_title
        before_urls = list(before.image_urls)
        before_updated_at = before.updated_at
    finally:
        db.close()

    summary = import_seed(seed_path, update_existing=True, dry_run=True)
    assert summary["mode"] == "update-existing-dry-run"
    counts = summary["counts"]
    # In dry-run+update-existing, drift rows are reported as "updated"
    # but no DB write actually happens.
    assert counts["updated"] == 2, counts
    assert counts["skipped_with_diff"] == 0, counts

    db = SessionLocal()
    try:
        after = get_by_internal_sku(db, SKU_IMG_DRIFT)
        assert after.product_title == before_title
        assert list(after.image_urls) == before_urls
        assert after.updated_at == before_updated_at, (
            "updated_at must not bump in dry-run mode"
        )
    finally:
        db.close()


def check_update_existing_applies_only_allowlist(seed_path: Path) -> None:
    db = SessionLocal()
    try:
        before = get_by_internal_sku(db, SKU_IMG_DRIFT)
        before_designer = before.designer
        before_style = before.style_number
        before_color = before.color
        before_category = before.category
        before_updated_at = before.updated_at
    finally:
        db.close()

    # Sleep just over a second so the updated_at bump is observable
    # against typical TIMESTAMPTZ resolution.
    time.sleep(1.1)

    summary = import_seed(seed_path, update_existing=True, dry_run=False)
    assert summary["mode"] == "update-existing"
    counts = summary["counts"]
    assert counts["created"] == 0, counts  # SKU_NEW already exists from earlier check
    assert counts["updated"] == 2, counts
    assert counts["skipped_no_diff"] == 3, counts  # NEW, NO_DIFF, HOUSE_PRESERVE
    assert counts["skipped_with_diff"] == 0, counts

    updated_skus = {e["internal_sku"] for e in summary["updated_skus"]}
    assert SKU_IMG_DRIFT in updated_skus
    assert SKU_HOUSE_FILL in updated_skus

    db = SessionLocal()
    try:
        after = get_by_internal_sku(db, SKU_IMG_DRIFT)
        # Allowlist fields were refreshed.
        assert after.product_title == "Refreshed Title", after.product_title
        assert after.description_text == "refreshed description"
        assert after.image_urls == [
            "https://cdn.example.com/z2_new1.webp",
            "https://cdn.example.com/z2_new2.webp",
        ], after.image_urls
        # Identity-defining fields stayed put even though seed disagreed.
        assert after.designer == before_designer == "Vendor Z"
        assert after.style_number == before_style == "Z2"
        assert after.color == before_color == "Coral"
        assert after.category == before_category == "quince_gown"
        # updated_at must move forward.
        assert after.updated_at > before_updated_at, (
            f"updated_at did not bump: {before_updated_at} -> {after.updated_at}"
        )

        # House_name preservation: staff-curated value stayed.
        preserve = get_by_internal_sku(db, SKU_HOUSE_PRESERVE)
        assert preserve.house_name == "Staff-Curated", preserve.house_name

        # House_name fill: null became the seed value.
        fill = get_by_internal_sku(db, SKU_HOUSE_FILL)
        assert fill.house_name == "Vendor-Default", fill.house_name
    finally:
        db.close()


def check_repeat_run_is_no_op(seed_path: Path) -> None:
    """After a successful update-existing pass, re-running should
    show zero diffs."""

    summary = import_seed(seed_path, update_existing=True, dry_run=False)
    counts = summary["counts"]
    assert counts["updated"] == 0, counts
    assert counts["skipped_with_diff"] == 0, counts
    assert counts["skipped_no_diff"] == counts["total_seed_items"], counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    baseline = _get_seq()
    try:
        _seed_initial_rows()
        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            seed_path = _write_seed(tmpdir)

            check_default_mode_surfaces_drift_without_writing(seed_path)
            print("default mode surfaces drift, doesn't write ok")

            check_dry_run_writes_nothing(seed_path)
            print("dry-run writes nothing ok")

            check_update_existing_applies_only_allowlist(seed_path)
            print("update-existing applies allowlist + bumps updated_at ok")

            check_repeat_run_is_no_op(seed_path)
            print("repeat run is no-op ok")

        print()
        print("catalog seed import smoke ok")
        return 0
    finally:
        _cleanup()
        _reset_seq(baseline)
        print(f"cleanup: rows wiped, seq reset to {baseline}")


if __name__ == "__main__":
    sys.exit(main())
