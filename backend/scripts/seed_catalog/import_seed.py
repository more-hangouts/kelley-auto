"""Import committed catalog seed data through the catalog service.

Three modes, controlled by ``--update-existing`` and ``--dry-run``:

  - **default**: create missing rows; existing rows with no diff are
    skipped silently; existing rows with diff are surfaced as
    ``skipped_with_diff`` (operator can re-run with
    ``--update-existing`` to apply).
  - **--update-existing**: same as default plus apply any diff via
    ``services.catalog_service.refresh_catalog_item`` (allowlisted
    fields only). Bumps ``updated_at``. No activity-log entry per
    SKU; the run-level summary JSON is the audit artifact.
  - **--dry-run**: compute everything but write nothing. Combines
    with ``--update-existing`` to preview what an update would do.

A summary JSON is written every run, regardless of mode. Stdout
prints a short human summary of the same data.

Usage:

    venv/bin/python scripts/seed_catalog/import_seed.py
    venv/bin/python scripts/seed_catalog/import_seed.py \\
        --seed data/seeds/ariana_vara_latest_collection.json
    venv/bin/python scripts/seed_catalog/import_seed.py \\
        --seed data/seeds/morilee_vizcaya.json \\
        --update-existing --dry-run
    venv/bin/python scripts/seed_catalog/import_seed.py \\
        --seed data/seeds/morilee_vizcaya.json \\
        --update-existing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from services.catalog_service import (  # noqa: E402
    REFRESH_ALLOWLIST,
    CatalogItemInput,
    create_catalog_item,
    get_by_internal_sku,
    refresh_catalog_item,
)

DEFAULT_SEED = _REPO_ROOT / "data/seeds/morilee_vizcaya.json"
DEFAULT_SUMMARY_OUTPUT = _REPO_ROOT / "data/reports/catalog_import_summary.json"


# ---------------------------------------------------------------------------
# Seed -> CatalogItemInput mapping (used by both create and refresh paths)
# ---------------------------------------------------------------------------


def _seed_product_title(raw: dict) -> str | None:
    return raw.get("product_title") or (raw.get("source") or {}).get("title")


def _seed_source_product_id(raw: dict) -> str | None:
    src = raw.get("source") or {}
    if src.get("product_id") is None:
        return None
    return str(src["product_id"])


def _input_from_seed_item(raw: dict) -> CatalogItemInput:
    source = raw.get("source") or {}
    return CatalogItemInput(
        internal_sku=raw["internal_sku"],
        designer=raw.get("designer"),
        style_number=raw.get("style_number"),
        color=raw["color"],
        house_name=raw.get("house_name"),
        product_title=_seed_product_title(raw),
        category=raw["category"],
        description_text=raw.get("description_text"),
        image_urls=list(raw.get("image_urls") or []),
        source_platform=source.get("platform"),
        source_product_id=_seed_source_product_id(raw),
        source_product_handle=source.get("handle"),
        source_product_url=source.get("product_url"),
        source_collection_url=source.get("source_url"),
        source_product_type=source.get("product_type"),
        is_sample=bool(raw.get("is_sample", False)),
        active=bool(raw.get("active", True)),
    )


# ---------------------------------------------------------------------------
# Diff computation (refresh allowlist only)
# ---------------------------------------------------------------------------


def _seed_value_for_field(raw: dict, field_name: str) -> Any:
    """Pull the seed-side value for one allowlist field, mapped to the
    same shape that the create path would write."""

    source = raw.get("source") or {}
    if field_name == "product_title":
        return _seed_product_title(raw)
    if field_name == "description_text":
        return raw.get("description_text")
    if field_name == "image_urls":
        return list(raw.get("image_urls") or [])
    if field_name == "house_name":
        return raw.get("house_name")
    if field_name == "source_platform":
        return source.get("platform")
    if field_name == "source_product_id":
        return _seed_source_product_id(raw)
    if field_name == "source_product_handle":
        return source.get("handle")
    if field_name == "source_product_url":
        return source.get("product_url")
    if field_name == "source_collection_url":
        return source.get("source_url")
    if field_name == "source_product_type":
        return source.get("product_type")
    raise KeyError(f"no seed mapping for field {field_name!r}")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def compute_refresh_diff(item: CatalogItem, raw: dict) -> dict[str, Any]:
    """Return the dict of allowlist fields that should be refreshed.

    Conservative rules:
      - Fields outside ``REFRESH_ALLOWLIST`` are never proposed.
      - A None/empty seed value never overwrites a non-empty existing
        value (refusing to clear data on a dropped scrape signal).
      - ``house_name`` is refreshed only when the existing value is
        currently None — preserves staff-curated brand lines.
      - All other equal-value cases are skipped.
    """
    diff: dict[str, Any] = {}
    for field_name in REFRESH_ALLOWLIST:
        seed_value = _seed_value_for_field(raw, field_name)
        existing = getattr(item, field_name)
        if _is_empty(seed_value):
            continue
        if seed_value == existing:
            continue
        if field_name == "house_name" and existing is not None:
            continue
        diff[field_name] = seed_value
    return diff


# ---------------------------------------------------------------------------
# Run-level result container
# ---------------------------------------------------------------------------


def _value_summary(value: Any) -> str:
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, str) and len(value) > 80:
        return f"text[{len(value)}]"
    return repr(value)


def _change_payload(field_name: str, old: Any, new: Any) -> dict[str, Any]:
    return {
        "field": field_name,
        "old_summary": _value_summary(old),
        "new_summary": _value_summary(new),
        "old_value": old,
        "new_value": new,
    }


def import_seed(
    path: Path,
    *,
    update_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the importer and return a structured summary.

    The summary is the source of truth for both stdout output and the
    JSON sidecar. Caller decides where to print/write it.
    """
    payload = json.loads(path.read_text())
    items = payload.get("items") or []

    created_skus: list[str] = []
    updated_skus: list[dict[str, Any]] = []
    skipped_no_diff: list[str] = []
    skipped_with_diff_skus: list[dict[str, Any]] = []

    db = SessionLocal()
    try:
        for raw in items:
            internal_sku = raw["internal_sku"]
            existing = get_by_internal_sku(db, internal_sku)
            if existing is None:
                if not dry_run:
                    create_catalog_item(db, _input_from_seed_item(raw))
                created_skus.append(internal_sku)
                continue

            diff = compute_refresh_diff(existing, raw)
            if not diff:
                skipped_no_diff.append(internal_sku)
                continue

            changes_payload = [
                _change_payload(field, getattr(existing, field), new_value)
                for field, new_value in sorted(diff.items())
            ]

            if not update_existing:
                skipped_with_diff_skus.append(
                    {"internal_sku": internal_sku, "changes": changes_payload}
                )
                continue

            if not dry_run:
                refresh_catalog_item(db, existing, diff)
            updated_skus.append(
                {"internal_sku": internal_sku, "changes": changes_payload}
            )

        if not dry_run:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if dry_run and update_existing:
        mode = "update-existing-dry-run"
    elif dry_run:
        mode = "dry-run"
    elif update_existing:
        mode = "update-existing"
    else:
        mode = "default"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed_path": str(path),
        "mode": mode,
        "refresh_allowlist": sorted(REFRESH_ALLOWLIST),
        "counts": {
            "total_seed_items": len(items),
            "created": len(created_skus),
            "updated": len(updated_skus),
            "skipped_no_diff": len(skipped_no_diff),
            "skipped_with_diff": len(skipped_with_diff_skus),
        },
        "created_skus": created_skus,
        "updated_skus": updated_skus,
        "skipped_with_diff_skus": skipped_with_diff_skus,
    }


# ---------------------------------------------------------------------------
# Stdout rendering
# ---------------------------------------------------------------------------


def _print_change_block(label: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    print(f"\n{label}:")
    for entry in rows:
        print(f"  {entry['internal_sku']}")
        for change in entry["changes"]:
            print(
                f"    {change['field']}: "
                f"{change['old_summary']} -> {change['new_summary']}"
            )


def print_summary(summary: dict[str, Any]) -> None:
    seed = Path(summary["seed_path"]).name
    print(f"catalog seed import: seed={seed} mode={summary['mode']}")
    counts = summary["counts"]
    print(f"  total seed items:      {counts['total_seed_items']}")
    print(f"  created:               {counts['created']}")
    print(f"  updated:               {counts['updated']}")
    print(f"  skipped (no diff):     {counts['skipped_no_diff']}")
    print(f"  skipped (with diff):   {counts['skipped_with_diff']}")

    _print_change_block("updated", summary["updated_skus"])
    if summary["skipped_with_diff_skus"]:
        hint = (
            "drift detected (re-run with --update-existing to apply, "
            "or --dry-run for full diffs)"
        )
        _print_change_block(hint, summary["skipped_with_diff_skus"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        type=Path,
        default=DEFAULT_SEED,
        help="Path to a catalog seed JSON file produced by a vendor scraper.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "For existing rows with allowlisted-field diffs, apply the "
            "refresh through services.catalog_service.refresh_catalog_item. "
            "Without this flag, the importer is insert-only."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but commit nothing.",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=DEFAULT_SUMMARY_OUTPUT,
        help="Destination JSON sidecar path for the run summary.",
    )
    args = parser.parse_args()

    summary = import_seed(
        args.seed,
        update_existing=args.update_existing,
        dry_run=args.dry_run,
    )

    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print_summary(summary)
    print(f"\nsummary written: {args.summary_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
