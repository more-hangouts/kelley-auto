"""Catalog cleanup report.

Identifies rows in ``catalog_items`` that are likely cleanup
candidates and which of those have referencing rows in catalog-aware
tables. The report is read-only: it never mutates the database.

Two row populations are flagged:

  - **suspicious_test_rows**: rows whose ``designer`` matches a
    hard-coded allowlist of test-designer names. The allowlist is
    deliberately fixed (not a CLI flag) so the report's vocabulary
    stays auditable across runs and operators.
  - **source_missing_active_rows**: rows still present and active in
    ``catalog_items`` whose ``internal_sku`` no longer appears in the
    refreshed seed JSON for their designer scope. Requires one or
    more ``--seed FILE`` arguments; without seeds, this section is
    omitted from the report rather than silently empty.

For every flagged row, the report counts referencing rows in each
table whose foreign key targets ``catalog_items.id``. The set of
target tables is discovered from live SQLAlchemy metadata, not
hand-listed, so a future migration that adds a new FK will be picked
up automatically.

Two derived recommendation lists fall out of the FK counts:

  - **safe_delete_candidates**: flagged rows with zero FK refs.
    Deletion is physically safe; it does not violate any
    ``ON DELETE RESTRICT`` constraint.
  - **deactivate_candidates**: flagged rows that have at least one
    FK ref AND are still ``active=True``. Deletion would be blocked;
    setting ``active=False`` is the next safest step.

A third descriptive list, **referenced_rows**, captures every flagged
row that has at least one FK ref regardless of current ``active``
state. ``deactivate_candidates`` is the actionable subset of that.

Usage:

    venv/bin/python scripts/catalog_cleanup_report.py
    venv/bin/python scripts/catalog_cleanup_report.py \\
        --seed data/seeds/morilee_vizcaya.json \\
        --seed data/seeds/ariana_vara_latest_collection.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from sqlalchemy import MetaData, func, select  # noqa: E402

from database.connection import SessionLocal, engine  # noqa: E402
from database.models import CatalogItem  # noqa: E402


# Hard-coded; do not promote to a CLI flag without changing this comment.
# The point of pinning the list here is so the report's vocabulary stays
# stable and auditable. Adding a vendor here is a deliberate code change,
# not a per-invocation knob.
SUSPICIOUS_DESIGNERS: tuple[str, ...] = ("Some Vendor", "Mori Lee")

DEFAULT_OUTPUT = _REPO_ROOT / "data/reports/catalog_cleanup_report.json"


# ---------------------------------------------------------------------------
# FK discovery
# ---------------------------------------------------------------------------


def discover_catalog_fks(metadata: MetaData) -> list[tuple[str, str, str | None]]:
    """Return ``(table_name, column_name, on_delete)`` triples for every
    FK that targets ``catalog_items.id`` in the live schema."""

    targets: list[tuple[str, str, str | None]] = []
    for tbl in metadata.tables.values():
        for col in tbl.columns:
            for fk in col.foreign_keys:
                if fk.column.table.name == "catalog_items":
                    targets.append((tbl.name, col.name, fk.ondelete))
    targets.sort()
    return targets


def count_refs(
    db,
    metadata: MetaData,
    fk_targets: list[tuple[str, str, str | None]],
    catalog_item_id: int,
) -> dict[str, int]:
    """For one catalog row, count referencing rows per FK target."""

    refs: dict[str, int] = {}
    for table_name, col_name, _ondelete in fk_targets:
        tbl = metadata.tables[table_name]
        col = tbl.c[col_name]
        n = db.execute(
            select(func.count()).select_from(tbl).where(col == catalog_item_id)
        ).scalar() or 0
        refs[f"{table_name}.{col_name}"] = int(n)
    return refs


# ---------------------------------------------------------------------------
# Flagged-row collection
# ---------------------------------------------------------------------------


def find_suspicious_test_rows(db) -> list[CatalogItem]:
    return list(
        db.execute(
            select(CatalogItem)
            .where(CatalogItem.designer.in_(SUSPICIOUS_DESIGNERS))
            .order_by(CatalogItem.id)
        ).scalars()
    )


def _seed_designer_skus(seed_path: Path) -> tuple[str | None, set[str]]:
    """Return ``(designer, internal_skus)`` for one seed file.

    Designer is taken from the first item that carries one; the seed is
    expected to be single-vendor.
    """

    payload = json.loads(seed_path.read_text())
    items = payload.get("items") or []
    designer: str | None = None
    skus: set[str] = set()
    for item in items:
        sku = item.get("internal_sku")
        if not sku:
            continue
        skus.add(sku)
        if designer is None:
            designer_value = item.get("designer")
            if designer_value:
                designer = str(designer_value).strip() or None
    return designer, skus


def find_source_missing_rows(
    db,
    seed_paths: list[Path],
) -> tuple[list[CatalogItem], dict[str, dict[str, Any]]]:
    """Return active rows whose ``internal_sku`` is not in the seed for
    their designer scope, plus a per-seed scope summary."""

    rows: list[CatalogItem] = []
    seen_ids: set[int] = set()
    summaries: dict[str, dict[str, Any]] = {}

    for seed_path in seed_paths:
        designer, seed_skus = _seed_designer_skus(seed_path)
        if not designer:
            summaries[str(seed_path)] = {
                "designer": None,
                "seed_skus": len(seed_skus),
                "active_db_skus": 0,
                "missing_count": 0,
                "skipped_reason": "seed has no designer field",
            }
            continue

        active_rows = list(
            db.execute(
                select(CatalogItem)
                .where(
                    CatalogItem.designer == designer,
                    CatalogItem.active.is_(True),
                )
                .order_by(CatalogItem.id)
            ).scalars()
        )
        missing = [row for row in active_rows if row.internal_sku not in seed_skus]
        for row in missing:
            if row.id in seen_ids:
                continue
            seen_ids.add(row.id)
            rows.append(row)
        summaries[str(seed_path)] = {
            "designer": designer,
            "seed_skus": len(seed_skus),
            "active_db_skus": len(active_rows),
            "missing_count": len(missing),
        }

    return rows, summaries


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _row_payload(row: CatalogItem, fk_refs: dict[str, int]) -> dict[str, Any]:
    return {
        "id": row.id,
        "internal_sku": row.internal_sku,
        "public_code": row.public_code,
        "designer": row.designer,
        "style_number": row.style_number,
        "color": row.color,
        "active": bool(row.active),
        "is_sample": bool(row.is_sample),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "fk_refs": fk_refs,
        "fk_ref_total": sum(fk_refs.values()),
    }


def _short_label(row: dict[str, Any]) -> str:
    parts = [row["public_code"], row["designer"] or "?"]
    if row["color"]:
        parts.append(row["color"])
    if row["style_number"]:
        parts.append(f"({row['style_number']})")
    return " / ".join(parts)


def _print_summary(report: dict[str, Any]) -> None:
    print(f"catalog cleanup report  generated_at={report['generated_at']}")
    print()
    print("scope:")
    print(f"  suspicious designers: {', '.join(report['suspicious_designers'])}")
    seeds = report["seeds_used"]
    if seeds:
        print(f"  seeds: {', '.join(seeds)}")
        for seed_path, summary in report["seed_scope_summary"].items():
            note = summary.get("skipped_reason") or (
                f"designer={summary['designer']} "
                f"seed_skus={summary['seed_skus']} "
                f"active_db_skus={summary['active_db_skus']} "
                f"missing={summary['missing_count']}"
            )
            print(f"    {seed_path}  {note}")
    else:
        print("  seeds: (none — source-missing checks skipped)")
    fk_targets = report["fk_targets_checked"]
    if fk_targets:
        print("  fk targets checked:")
        for target in fk_targets:
            print(f"    {target['column']} ON DELETE {target['on_delete']}")
    else:
        print("  fk targets checked: (none discovered)")

    print()
    counts = report["counts"]
    print("counts:")
    for key in (
        "suspicious_test_rows",
        "source_missing_active_rows",
        "referenced_rows",
        "safe_delete_candidates",
        "deactivate_candidates",
    ):
        print(f"  {key:32s} {counts[key]}")

    for category in (
        "suspicious_test_rows",
        "source_missing_active_rows",
        "safe_delete_candidates",
        "deactivate_candidates",
    ):
        rows = report[category]
        if not rows:
            continue
        print()
        print(f"{category}:")
        for row in rows:
            ref_summary = (
                ", ".join(f"{k}={v}" for k, v in row["fk_refs"].items() if v)
                or "no refs"
            )
            active_marker = "" if row["active"] else " (already inactive)"
            print(f"  - {_short_label(row)}{active_marker}  [{ref_summary}]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_report(seed_paths: list[Path]) -> dict[str, Any]:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    fk_targets = discover_catalog_fks(metadata)

    db = SessionLocal()
    try:
        suspicious_rows = find_suspicious_test_rows(db)
        if seed_paths:
            source_missing_rows, seed_scope_summary = find_source_missing_rows(
                db, seed_paths
            )
        else:
            source_missing_rows, seed_scope_summary = [], {}

        # Build the union of flagged rows, deduped by id, recording why
        # each row was flagged.
        flagged_by_id: dict[int, CatalogItem] = {}
        flag_reasons: dict[int, set[str]] = defaultdict(set)
        for row in suspicious_rows:
            flagged_by_id[row.id] = row
            flag_reasons[row.id].add("suspicious_test")
        for row in source_missing_rows:
            flagged_by_id[row.id] = row
            flag_reasons[row.id].add("source_missing")

        # Count FK refs per flagged row.
        ref_counts_by_id: dict[int, dict[str, int]] = {}
        for row_id in flagged_by_id:
            ref_counts_by_id[row_id] = count_refs(
                db, metadata, fk_targets, row_id
            )
    finally:
        db.close()

    suspicious_payload = [
        _row_payload(row, ref_counts_by_id[row.id]) for row in suspicious_rows
    ]
    source_missing_payload = [
        _row_payload(row, ref_counts_by_id[row.id]) for row in source_missing_rows
    ]
    referenced_payload = [
        _row_payload(flagged_by_id[rid], ref_counts_by_id[rid])
        for rid in sorted(flagged_by_id)
        if any(ref_counts_by_id[rid].values())
    ]
    safe_delete_payload = [
        _row_payload(flagged_by_id[rid], ref_counts_by_id[rid])
        for rid in sorted(flagged_by_id)
        if not any(ref_counts_by_id[rid].values())
    ]
    deactivate_payload = [
        _row_payload(flagged_by_id[rid], ref_counts_by_id[rid])
        for rid in sorted(flagged_by_id)
        if any(ref_counts_by_id[rid].values()) and bool(flagged_by_id[rid].active)
    ]

    fk_target_payload = [
        {
            "column": f"{table}.{col}",
            "on_delete": on_delete or "(default NO ACTION)",
        }
        for table, col, on_delete in fk_targets
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suspicious_designers": list(SUSPICIOUS_DESIGNERS),
        "seeds_used": [str(p) for p in seed_paths],
        "seed_scope_summary": seed_scope_summary,
        "fk_targets_checked": fk_target_payload,
        "counts": {
            "suspicious_test_rows": len(suspicious_payload),
            "source_missing_active_rows": len(source_missing_payload),
            "referenced_rows": len(referenced_payload),
            "safe_delete_candidates": len(safe_delete_payload),
            "deactivate_candidates": len(deactivate_payload),
        },
        "suspicious_test_rows": suspicious_payload,
        "source_missing_active_rows": source_missing_payload,
        "referenced_rows": referenced_payload,
        "safe_delete_candidates": safe_delete_payload,
        "deactivate_candidates": deactivate_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help=(
            "Path to a vendor seed JSON. Pass once per vendor. Required "
            "for the source_missing_active_rows section; omit to skip "
            "that check."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON sidecar path.",
    )
    args = parser.parse_args()

    for seed_path in args.seed:
        if not seed_path.is_file():
            print(f"seed not found: {seed_path}", file=sys.stderr)
            return 2

    report = build_report(args.seed)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _print_summary(report)
    print()
    print(f"json written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
