"""Smoke test for ``scripts/catalog_cleanup_report.py``.

Inserts a small fixture set into Postgres, runs the report, and
asserts that:

  - FK reference discovery picks up the live ``catalog_items`` FK
    landscape (at minimum ``invoice_line_items.catalog_item_id``).
  - A row whose ``designer`` matches the hardcoded suspicious
    allowlist with no referencing rows lands in
    ``suspicious_test_rows`` AND ``safe_delete_candidates``.
  - A suspicious-designer row WITH an invoice_line_item reference
    lands in ``suspicious_test_rows`` AND ``referenced_rows`` AND
    ``deactivate_candidates``, NOT in ``safe_delete_candidates``.
  - A row in the seed JSON for its designer is not flagged.
  - A row missing from the seed for its designer lands in
    ``source_missing_active_rows`` AND ``safe_delete_candidates``,
    NOT in ``suspicious_test_rows``.

The test makes no claims about *exact counts* in any category, only
about *membership*: pre-existing suspicious rows in the DB (e.g. the
ones the report itself was written to flag) coexist with the new
fixtures and are not the test's concern.

Runs as a script:

    venv/bin/python tests/test_catalog_cleanup_report_smoke.py

Internal helpers are named ``check_*`` so a broad ``pytest tests/``
sweep does not collect them as parameterless tests.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event  # noqa: E402
from services import invoice_service  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
)
from services.invoice_service import LineItemInput  # noqa: E402

from scripts.catalog_cleanup_report import build_report  # noqa: E402


_PREFIX = f"TEST-CLEANUP-{uuid.uuid4().hex[:8].upper()}-"
_SCOPE_DESIGNER = f"Test Brand {_PREFIX[-9:-1]}"

SKU_SUSP_SAFE = _PREFIX + "VENDOR-SUSP-SAFE"
SKU_SUSP_REF = _PREFIX + "VENDOR-SUSP-REF"
SKU_INSEED = _PREFIX + "VENDOR-INSEED"
SKU_MISSING = _PREFIX + "VENDOR-MISSING"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _get_catalog_seq() -> int:
    db = SessionLocal()
    try:
        return int(db.execute(sql_text(
            "SELECT catalog_public_code_seq FROM numbering_state WHERE id = 1"
        )).scalar())
    finally:
        db.close()


def _reset_catalog_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text(
            "UPDATE numbering_state SET catalog_public_code_seq = :s "
            "WHERE id = 1"
        ), {"s": value})
        db.commit()
    finally:
        db.close()


def _seed_fixtures() -> dict[str, int]:
    """Create contact, event, four catalog rows, and one invoice with a
    line item referencing the catalog row that should land in
    ``deactivate_candidates``. Returns a dict of ids for assertions."""

    ids: dict[str, int] = {}
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=_PREFIX + "Customer", phone="(210) 555-7777"
        )
        db.add(contact)
        db.flush()
        ids["contact_id"] = contact.id

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=_PREFIX + "Quince",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        ids["event_id"] = event.id

        # Four catalog rows covering each report category.
        susp_safe = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=SKU_SUSP_SAFE,
                color="Red",
                category="quince_gown",
                designer="Some Vendor",
                style_number="0001",
            ),
        )
        susp_ref = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=SKU_SUSP_REF,
                color="Ivory",
                category="quince_gown",
                designer="Mori Lee",
                style_number="89216",
            ),
        )
        in_seed = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=SKU_INSEED,
                color="Blush",
                category="quince_gown",
                designer=_SCOPE_DESIGNER,
                style_number="X-001",
            ),
        )
        missing = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=SKU_MISSING,
                color="Lilac",
                category="quince_gown",
                designer=_SCOPE_DESIGNER,
                style_number="X-002",
            ),
        )
        db.commit()
        ids["susp_safe_id"] = susp_safe.id
        ids["susp_ref_id"] = susp_ref.id
        ids["in_seed_id"] = in_seed.id
        ids["missing_id"] = missing.id

        invoice = invoice_service.create_invoice(
            db,
            event_id=ids["event_id"],
            contact_id=ids["contact_id"],
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=ids["susp_ref_id"],
                    size_label="08",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
        )
        db.commit()
        ids["invoice_id"] = invoice.id
    finally:
        db.close()

    return ids


def _cleanup() -> None:
    """Wipe every test row by ``_PREFIX`` in dependency order."""

    p = _PREFIX + "%"
    db = SessionLocal()
    try:
        events_subq = "(SELECT id FROM events WHERE event_name LIKE :p)"
        # Quote children, then quotes (none expected, but safe to run).
        db.execute(sql_text(
            f"DELETE FROM quote_invitations WHERE quote_id IN "
            f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
        ), {"p": p})
        db.execute(sql_text(
            f"DELETE FROM quote_line_items WHERE quote_id IN "
            f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
        ), {"p": p})
        db.execute(sql_text(
            f"DELETE FROM quotes WHERE event_id IN {events_subq}"
        ), {"p": p})
        # Invoice children, then invoices.
        db.execute(sql_text(
            f"DELETE FROM invoice_invitations WHERE invoice_id IN "
            f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
        ), {"p": p})
        db.execute(sql_text(
            f"DELETE FROM invoice_installments WHERE invoice_id IN "
            f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
        ), {"p": p})
        db.execute(sql_text(
            f"DELETE FROM invoice_line_items WHERE invoice_id IN "
            f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
        ), {"p": p})
        db.execute(sql_text(
            f"DELETE FROM invoices WHERE event_id IN {events_subq}"
        ), {"p": p})
        # Then events, contacts, and the catalog rows themselves.
        db.execute(sql_text(
            "DELETE FROM events WHERE event_name LIKE :p"
        ), {"p": p})
        db.execute(sql_text(
            "DELETE FROM contacts WHERE display_name LIKE :p"
        ), {"p": p})
        db.execute(sql_text(
            "DELETE FROM catalog_items WHERE internal_sku LIKE :p"
        ), {"p": p})
        db.commit()
    finally:
        db.close()


def _write_temp_seed(tmpdir: Path, included_skus: list[str]) -> Path:
    payload = {
        "items": [
            {
                "internal_sku": sku,
                "designer": _SCOPE_DESIGNER,
                "style_number": "X-001",
                "color": "Blush",
                "category": "quince_gown",
            }
            for sku in included_skus
        ]
    }
    path = tmpdir / "scope.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _skus_in(rows: list[dict[str, Any]]) -> set[str]:
    return {r["internal_sku"] for r in rows}


def check_fk_discovery(report: dict[str, Any]) -> None:
    discovered = {t["column"] for t in report["fk_targets_checked"]}
    assert "invoice_line_items.catalog_item_id" in discovered, discovered
    # Both other production FKs should be picked up too — proves the
    # discovery is metadata-driven, not hand-listed.
    assert "quote_line_items.catalog_item_id" in discovered, discovered
    assert "special_orders.catalog_item_id" in discovered, discovered


def check_suspicious_safe_row(report: dict[str, Any]) -> None:
    suspicious = _skus_in(report["suspicious_test_rows"])
    safe_delete = _skus_in(report["safe_delete_candidates"])
    referenced = _skus_in(report["referenced_rows"])
    deactivate = _skus_in(report["deactivate_candidates"])
    assert SKU_SUSP_SAFE in suspicious
    assert SKU_SUSP_SAFE in safe_delete
    assert SKU_SUSP_SAFE not in referenced
    assert SKU_SUSP_SAFE not in deactivate
    # FK ref totals must read zero on the suspicious-safe row.
    row = next(r for r in report["suspicious_test_rows"] if r["internal_sku"] == SKU_SUSP_SAFE)
    assert row["fk_ref_total"] == 0, row["fk_refs"]


def check_suspicious_referenced_row(report: dict[str, Any]) -> None:
    suspicious = _skus_in(report["suspicious_test_rows"])
    safe_delete = _skus_in(report["safe_delete_candidates"])
    referenced = _skus_in(report["referenced_rows"])
    deactivate = _skus_in(report["deactivate_candidates"])
    assert SKU_SUSP_REF in suspicious
    assert SKU_SUSP_REF in referenced
    assert SKU_SUSP_REF in deactivate
    assert SKU_SUSP_REF not in safe_delete
    row = next(r for r in report["suspicious_test_rows"] if r["internal_sku"] == SKU_SUSP_REF)
    assert row["fk_ref_total"] >= 1, row["fk_refs"]
    assert (
        row["fk_refs"]["invoice_line_items.catalog_item_id"] >= 1
    ), row["fk_refs"]


def check_in_seed_row_not_flagged(report: dict[str, Any]) -> None:
    suspicious = _skus_in(report["suspicious_test_rows"])
    missing = _skus_in(report["source_missing_active_rows"])
    safe_delete = _skus_in(report["safe_delete_candidates"])
    referenced = _skus_in(report["referenced_rows"])
    deactivate = _skus_in(report["deactivate_candidates"])
    assert SKU_INSEED not in suspicious
    assert SKU_INSEED not in missing
    assert SKU_INSEED not in safe_delete
    assert SKU_INSEED not in referenced
    assert SKU_INSEED not in deactivate


def check_source_missing_row(report: dict[str, Any]) -> None:
    suspicious = _skus_in(report["suspicious_test_rows"])
    missing = _skus_in(report["source_missing_active_rows"])
    safe_delete = _skus_in(report["safe_delete_candidates"])
    referenced = _skus_in(report["referenced_rows"])
    assert SKU_MISSING in missing
    assert SKU_MISSING in safe_delete
    assert SKU_MISSING not in suspicious
    assert SKU_MISSING not in referenced


def check_seed_scope_summary(report: dict[str, Any], seed_path: Path) -> None:
    summary = report["seed_scope_summary"][str(seed_path)]
    assert summary["designer"] == _SCOPE_DESIGNER, summary
    assert summary["seed_skus"] == 1, summary
    # Two active rows for that designer in the DB; one missing from seed.
    assert summary["active_db_skus"] == 2, summary
    assert summary["missing_count"] == 1, summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    baseline = _get_catalog_seq()

    try:
        ids = _seed_fixtures()
        print(
            f"seeded: contact={ids['contact_id']} event={ids['event_id']} "
            f"susp_safe={ids['susp_safe_id']} susp_ref={ids['susp_ref_id']} "
            f"in_seed={ids['in_seed_id']} missing={ids['missing_id']} "
            f"invoice={ids['invoice_id']}"
        )

        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            seed_path = _write_temp_seed(tmpdir, [SKU_INSEED])
            report = build_report([seed_path])

            check_fk_discovery(report)
            print("fk discovery ok")
            check_suspicious_safe_row(report)
            print("suspicious + no refs -> safe_delete ok")
            check_suspicious_referenced_row(report)
            print("suspicious + refs -> referenced + deactivate ok")
            check_in_seed_row_not_flagged(report)
            print("in-seed row not flagged ok")
            check_source_missing_row(report)
            print("source-missing row ok")
            check_seed_scope_summary(report, seed_path)
            print("seed scope summary ok")

        print()
        print("catalog cleanup report smoke ok")
        return 0
    finally:
        _cleanup()
        _reset_catalog_seq(baseline)
        print(f"cleanup: rows wiped, seq reset to {baseline}")


if __name__ == "__main__":
    sys.exit(main())
