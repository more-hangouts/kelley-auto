"""Catalog cleanup apply.

Companion to ``scripts/catalog_cleanup_report.py``. Acts on the
categorization the report produces, but rebuilds it fresh inside this
process so an apply can never act on a stale snapshot.

Modes (every destructive action is gated by ``--confirm``; without
``--confirm`` the script prints the plan and exits 0):

  - **default**: delete every row in ``safe_delete_candidates``.
  - **--deactivate-referenced**: also set ``active=False`` on every
    row in ``deactivate_candidates``.
  - **--purge-test-fixtures**: also delete the catalog row in
    ``deactivate_candidates`` together with its referencing parent
    invoices, parent quotes, line items (via CASCADE), and the
    placeholder contact, IFF the cluster matches a hardcoded
    fingerprint (see ``_evaluate_purge_cluster``). Multiple matching
    clusters are allowed; any cluster that fails the fingerprint
    aborts the entire apply.

Everything runs inside one transaction. A single failed evidence
check or DB error rolls the whole transaction back; the apply is
all-or-nothing.

Usage:

    venv/bin/python scripts/catalog_cleanup_apply.py \\
        --seed data/seeds/morilee_vizcaya.json
    venv/bin/python scripts/catalog_cleanup_apply.py \\
        --seed data/seeds/morilee_vizcaya.json \\
        --deactivate-referenced --purge-test-fixtures --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from sqlalchemy import text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402

from scripts.catalog_cleanup_report import (  # noqa: E402
    SUSPICIOUS_DESIGNERS,
    build_report,
)


# Hardcoded fingerprint of the known smoke-test contact. Anchoring on
# the literal phone string keeps the purge mode narrowly scoped: a
# different contact (real or test) with a different phone will fail the
# evidence check and abort the apply.
TEST_FIXTURE_PHONE = "(210) 555-7777"

# Maximum allowed clock-distance between the catalog row's created_at
# and any of its referencing rows' created_at, when evaluating the
# "all created in one burst" evidence. Generous enough to absorb a slow
# smoke test, tight enough to reject a real customer thread.
FIXTURE_BURST_SECONDS = 60


# ---------------------------------------------------------------------------
# Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PurgeCluster:
    catalog_item_id: int
    catalog_label: str
    invoice_ids: list[int] = field(default_factory=list)
    quote_ids: list[int] = field(default_factory=list)
    contact_ids: list[int] = field(default_factory=list)
    invoice_line_item_count: int = 0
    quote_line_item_count: int = 0
    refusal_reasons: list[str] = field(default_factory=list)


@dataclass
class Plan:
    delete_catalog_ids: list[int] = field(default_factory=list)
    delete_catalog_labels: list[str] = field(default_factory=list)
    deactivate_catalog_ids: list[int] = field(default_factory=list)
    deactivate_catalog_labels: list[str] = field(default_factory=list)
    purge_clusters: list[PurgeCluster] = field(default_factory=list)
    skipped_purge_clusters: list[PurgeCluster] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def _row_label(row: dict[str, Any]) -> str:
    bits = [row["public_code"], row["designer"] or "?"]
    if row["color"]:
        bits.append(row["color"])
    if row["style_number"]:
        bits.append(f"({row['style_number']})")
    return " / ".join(bits)


def _evaluate_purge_cluster(
    db, catalog_row: dict[str, Any]
) -> PurgeCluster:
    """Build a PurgeCluster for one deactivate_candidate and run every
    evidence check. ``refusal_reasons`` is empty on a clean match;
    populated otherwise (the apply refuses if any cluster has reasons)."""

    catalog_id = catalog_row["id"]
    cluster = PurgeCluster(
        catalog_item_id=catalog_id,
        catalog_label=_row_label(catalog_row),
    )

    if catalog_row["designer"] not in SUSPICIOUS_DESIGNERS:
        cluster.refusal_reasons.append(
            f"designer {catalog_row['designer']!r} not in suspicious allowlist"
        )
        return cluster

    catalog_meta = db.execute(text("""
        select created_at, internal_sku, designer
        from catalog_items where id = :id
    """), {"id": catalog_id}).mappings().first()
    if catalog_meta is None:
        cluster.refusal_reasons.append("catalog row vanished between report and apply")
        return cluster
    catalog_created_at = catalog_meta["created_at"]
    burst_lo = catalog_created_at - timedelta(seconds=FIXTURE_BURST_SECONDS)
    burst_hi = catalog_created_at + timedelta(seconds=FIXTURE_BURST_SECONDS)

    inv_rows = db.execute(text("""
        select id, contact_id, status, sent_at, viewed_at, paid_at,
               deleted_at, paid_to_date_cents, created_at
        from invoices
        where id in (
            select distinct invoice_id from invoice_line_items
            where catalog_item_id = :cid
        )
    """), {"cid": catalog_id}).mappings().all()
    cluster.invoice_ids = [r["id"] for r in inv_rows]

    quote_rows = db.execute(text("""
        select id, contact_id, status, sent_at, viewed_at, approved_at,
               rejected_at, converted_at, cancelled_at,
               signature_signed_at, deleted_at, created_at
        from quotes
        where id in (
            select distinct quote_id from quote_line_items
            where catalog_item_id = :cid
        )
    """), {"cid": catalog_id}).mappings().all()
    cluster.quote_ids = [r["id"] for r in quote_rows]

    cluster.invoice_line_item_count = db.execute(text("""
        select count(*) from invoice_line_items where catalog_item_id = :cid
    """), {"cid": catalog_id}).scalar() or 0
    cluster.quote_line_item_count = db.execute(text("""
        select count(*) from quote_line_items where catalog_item_id = :cid
    """), {"cid": catalog_id}).scalar() or 0

    contact_ids = sorted({r["contact_id"] for r in inv_rows} | {
        r["contact_id"] for r in quote_rows
    })
    cluster.contact_ids = contact_ids

    # Invoices: must all be untouched drafts inside the burst window.
    for inv in inv_rows:
        if inv["status"] != "draft":
            cluster.refusal_reasons.append(
                f"invoice id={inv['id']} status={inv['status']!r}; expected 'draft'"
            )
        for ts_col in ("sent_at", "viewed_at", "paid_at", "deleted_at"):
            if inv[ts_col] is not None:
                cluster.refusal_reasons.append(
                    f"invoice id={inv['id']} has non-null {ts_col}"
                )
        if (inv["paid_to_date_cents"] or 0) != 0:
            cluster.refusal_reasons.append(
                f"invoice id={inv['id']} has paid_to_date_cents="
                f"{inv['paid_to_date_cents']}"
            )
        if not (burst_lo <= inv["created_at"] <= burst_hi):
            cluster.refusal_reasons.append(
                f"invoice id={inv['id']} created_at={inv['created_at']} "
                f"outside fixture burst window of catalog row"
            )

    # Quotes: every lifecycle timestamp must be null inside the window.
    quote_ts_cols = (
        "sent_at", "viewed_at", "approved_at", "rejected_at",
        "converted_at", "cancelled_at", "signature_signed_at", "deleted_at",
    )
    for q in quote_rows:
        for ts_col in quote_ts_cols:
            if q[ts_col] is not None:
                cluster.refusal_reasons.append(
                    f"quote id={q['id']} has non-null {ts_col}"
                )
        if not (burst_lo <= q["created_at"] <= burst_hi):
            cluster.refusal_reasons.append(
                f"quote id={q['id']} created_at={q['created_at']} "
                f"outside fixture burst window"
            )

    # Contacts: each must be a bare placeholder with the expected
    # fingerprint AND have no footprint outside this cluster.
    for cid in contact_ids:
        c = db.execute(text("""
            select id, first_name, last_name, email, phone, created_at
            from contacts where id = :cid
        """), {"cid": cid}).mappings().first()
        if c is None:
            cluster.refusal_reasons.append(f"contact id={cid} missing")
            continue
        if any(c[col] for col in ("first_name", "last_name", "email")):
            cluster.refusal_reasons.append(
                f"contact id={cid} has non-null identity fields"
            )
        if c["phone"] != TEST_FIXTURE_PHONE:
            cluster.refusal_reasons.append(
                f"contact id={cid} phone={c['phone']!r}; "
                f"expected {TEST_FIXTURE_PHONE!r}"
            )
        if c["created_at"] is not None and not (
            burst_lo <= c["created_at"] <= burst_hi
        ):
            cluster.refusal_reasons.append(
                f"contact id={cid} created_at={c['created_at']} "
                f"outside fixture burst window"
            )

        # Footprint outside the cluster.
        outside = {
            "appointments": db.execute(text(
                "select count(*) from appointments where contact_id = :c"
            ), {"c": cid}).scalar() or 0,
            "events.primary_contact_id": db.execute(text(
                "select count(*) from events where primary_contact_id = :c"
            ), {"c": cid}).scalar() or 0,
            "event_participants": db.execute(text(
                "select count(*) from event_participants where contact_id = :c"
            ), {"c": cid}).scalar() or 0,
            "payments": db.execute(text(
                "select count(*) from payments where contact_id = :c"
            ), {"c": cid}).scalar() or 0,
            "invoices_outside_cluster": db.execute(text(
                "select count(*) from invoices "
                "where contact_id = :c and id not in :ids"
            ).bindparams(__import__("sqlalchemy").bindparam(
                "ids", expanding=True
            )), {"c": cid, "ids": cluster.invoice_ids or [-1]}).scalar() or 0,
            "quotes_outside_cluster": db.execute(text(
                "select count(*) from quotes "
                "where contact_id = :c and id not in :ids"
            ).bindparams(__import__("sqlalchemy").bindparam(
                "ids", expanding=True
            )), {"c": cid, "ids": cluster.quote_ids or [-1]}).scalar() or 0,
        }
        for label, n in outside.items():
            if n:
                cluster.refusal_reasons.append(
                    f"contact id={cid} has {n} ref(s) in {label}"
                )

    return cluster


def build_plan(db, report: dict[str, Any], args: argparse.Namespace) -> Plan:
    plan = Plan()

    for row in report["safe_delete_candidates"]:
        plan.delete_catalog_ids.append(row["id"])
        plan.delete_catalog_labels.append(_row_label(row))

    if args.deactivate_referenced:
        for row in report["deactivate_candidates"]:
            plan.deactivate_catalog_ids.append(row["id"])
            plan.deactivate_catalog_labels.append(_row_label(row))

    if args.purge_test_fixtures:
        for row in report["deactivate_candidates"]:
            cluster = _evaluate_purge_cluster(db, row)
            if cluster.refusal_reasons:
                plan.skipped_purge_clusters.append(cluster)
            else:
                plan.purge_clusters.append(cluster)

    return plan


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------


def apply_plan(db, plan: Plan) -> dict[str, int]:
    counts = {
        "catalog_items_deleted": 0,
        "catalog_items_deactivated": 0,
        "purged_invoices": 0,
        "purged_quotes": 0,
        "purged_contacts": 0,
        "purged_catalog_items": 0,
    }

    if plan.delete_catalog_ids:
        result = db.execute(text(
            "delete from catalog_items where id = any(:ids)"
        ), {"ids": plan.delete_catalog_ids})
        counts["catalog_items_deleted"] = result.rowcount or 0

    if plan.deactivate_catalog_ids:
        result = db.execute(text(
            "update catalog_items set active = false, updated_at = now() "
            "where id = any(:ids) and active = true"
        ), {"ids": plan.deactivate_catalog_ids})
        counts["catalog_items_deactivated"] = result.rowcount or 0

    for cluster in plan.purge_clusters:
        # Delete parent invoices/quotes first; their line_items go via
        # ON DELETE CASCADE, which also frees the catalog_items
        # ON DELETE RESTRICT FK on this row.
        if cluster.invoice_ids:
            result = db.execute(text(
                "delete from invoices where id = any(:ids)"
            ), {"ids": cluster.invoice_ids})
            counts["purged_invoices"] += result.rowcount or 0
        if cluster.quote_ids:
            result = db.execute(text(
                "delete from quotes where id = any(:ids)"
            ), {"ids": cluster.quote_ids})
            counts["purged_quotes"] += result.rowcount or 0
        # Now the catalog row has no remaining FK refs.
        result = db.execute(text(
            "delete from catalog_items where id = :id"
        ), {"id": cluster.catalog_item_id})
        counts["purged_catalog_items"] += result.rowcount or 0
        # Contacts last, after invoices/quotes are gone (RESTRICT).
        if cluster.contact_ids:
            result = db.execute(text(
                "delete from contacts where id = any(:ids)"
            ), {"ids": cluster.contact_ids})
            counts["purged_contacts"] += result.rowcount or 0

    return counts


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------


def print_plan(plan: Plan, *, applied: dict[str, int] | None = None) -> None:
    print("plan:")
    print(f"  delete catalog rows: {len(plan.delete_catalog_ids)}")
    for label in plan.delete_catalog_labels:
        print(f"    - {label}")

    print(f"  deactivate catalog rows: {len(plan.deactivate_catalog_ids)}")
    for label in plan.deactivate_catalog_labels:
        print(f"    - {label}")

    print(f"  purge fixture clusters: {len(plan.purge_clusters)}")
    for cluster in plan.purge_clusters:
        print(f"    - {cluster.catalog_label}")
        print(f"        invoices: {cluster.invoice_ids}")
        print(f"        quotes:   {cluster.quote_ids}")
        print(f"        contacts: {cluster.contact_ids}")
        print(f"        invoice_line_items: {cluster.invoice_line_item_count}")
        print(f"        quote_line_items:   {cluster.quote_line_item_count}")

    if plan.skipped_purge_clusters:
        print(f"  REFUSED purge clusters: {len(plan.skipped_purge_clusters)}")
        for cluster in plan.skipped_purge_clusters:
            print(f"    - {cluster.catalog_label}")
            for reason in cluster.refusal_reasons:
                print(f"        × {reason}")

    if applied is not None:
        print()
        print("applied:")
        for k, v in applied.items():
            print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help="Same shape as catalog_cleanup_report.py; pass once per vendor.",
    )
    parser.add_argument(
        "--deactivate-referenced",
        action="store_true",
        help="Set active=false on every deactivate_candidate.",
    )
    parser.add_argument(
        "--purge-test-fixtures",
        action="store_true",
        help=(
            "Delete the deactivate_candidate(s) along with their parent "
            "invoices, quotes, and placeholder contacts, IFF every cluster "
            "matches the hardcoded test-fixture fingerprint."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for any DB writes; without it the plan prints and exits.",
    )
    args = parser.parse_args()

    for seed_path in args.seed:
        if not seed_path.is_file():
            print(f"seed not found: {seed_path}", file=sys.stderr)
            return 2

    report = build_report(args.seed)

    db = SessionLocal()
    try:
        plan = build_plan(db, report, args)

        if args.purge_test_fixtures and plan.skipped_purge_clusters:
            print_plan(plan)
            print()
            print(
                "abort: --purge-test-fixtures was passed but at least one "
                "cluster failed evidence checks. Resolve refusals above or "
                "drop --purge-test-fixtures.",
                file=sys.stderr,
            )
            return 3

        if not args.confirm:
            print_plan(plan)
            print()
            print("(dry run; pass --confirm to apply)")
            return 0

        try:
            with db.begin():
                applied = apply_plan(db, plan)
        except Exception:
            print("apply FAILED, transaction rolled back.", file=sys.stderr)
            raise

        print_plan(plan, applied=applied)
        print()
        print("apply complete.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
