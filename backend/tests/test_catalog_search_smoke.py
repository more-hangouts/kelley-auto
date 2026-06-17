"""Catalog SKU obfuscation Phase 3 search smoke.

Exercises the multi-column ranked search the staff line-item picker
calls into:

  - Ranking: exact match on ``internal_sku`` or ``public_code``
    outranks a prefix/substring match in any column; prefix outranks
    substring.
  - Normalization: ``MORI 4080000`` matches ``MORI-4080000``;
    ``regal/royal`` matches ``regal-royal``.
  - Active-only default: inactive rows are hidden unless
    ``include_inactive=True``.
  - Empty term returns the idle list (used by the picker on open).

Runs as a script:

    venv/bin/python tests/test_catalog_search_smoke.py
"""

from __future__ import annotations

import os
import sys
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
    search_catalog,
)


_PREFIX = f"P3-SEARCH-{uuid.uuid4().hex[:8].upper()}-"


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state "
                    "WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _reset_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": value},
        )
        db.commit()
    finally:
        db.close()


def _seed() -> dict[str, int]:
    """Insert a fixture set the search assertions read against. Each
    row's ``internal_sku`` carries the unique prefix so cleanup wipes
    only this test's rows."""
    fixtures = [
        # 0 — exact internal_sku target
        dict(
            internal_sku=_PREFIX + "ALPHA-1234",
            color="Ivory",
            category="quince_gown",
            designer="Alpha Designer",
            style_number="1234",
            house_name="Alpha House",
            product_title="Alpha Gown",
        ),
        # 1 — prefix-match-friendly second row
        dict(
            internal_sku=_PREFIX + "ALPHA-9999",
            color="Champagne",
            category="quince_gown",
            designer="Alpha Designer",
            style_number="9999",
            house_name="Alpha House",
            product_title="Alpha Gown",
        ),
        # 2 — substring-match-only via product_title
        dict(
            internal_sku=_PREFIX + "BETA-0001",
            color="Black",
            category="quince_gown",
            designer="Beta Designer",
            style_number="0001",
            house_name="Beta House",
            product_title="Customer-favorite Alpha trim variant",
        ),
        # 3 — slash↔dash normalization pair
        dict(
            internal_sku=_PREFIX + "GAMMA-REGAL-ROYAL",
            color="Regal Royal",
            category="quince_gown",
            designer="Gamma Designer",
            style_number="2222",
        ),
        # 4 — inactive row, must not appear in default search
        dict(
            internal_sku=_PREFIX + "DELTA-0001",
            color="Ivory",
            category="quince_gown",
            designer="Delta Designer",
            style_number="0001",
            product_title="Delta retired",
            active=False,
        ),
    ]
    ids: dict[str, int] = {}
    db = SessionLocal()
    try:
        for f in fixtures:
            item = create_catalog_item(db, CatalogItemInput(**f))
            ids[f["internal_sku"]] = item.id
        db.commit()
    finally:
        db.close()
    return ids


def _wipe() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": _PREFIX + "%"},
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_empty_term_returns_idle_list(ids: dict[str, int]) -> None:
    db = SessionLocal()
    try:
        rows = search_catalog(db, q=None, limit=10)
        assert len(rows) > 0, "empty term should return active rows"
        # All defaults active. Inactive must NOT appear.
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "DELTA-0001" not in skus, (
            "inactive row leaked into idle list"
        )
    finally:
        db.close()


def check_exact_internal_sku_outranks_substring(ids: dict[str, int]) -> None:
    """Querying the full internal_sku of ALPHA-1234 must place that row
    above the BETA row whose product_title only mentions 'Alpha' as a
    substring. Otherwise, picker autocomplete shows BETA before
    ALPHA-1234 when staff type the exact SKU."""
    sku = _PREFIX + "ALPHA-1234"
    db = SessionLocal()
    try:
        rows = search_catalog(db, q=sku, limit=10)
        assert rows, f"exact internal_sku {sku!r} returned no rows"
        assert rows[0].internal_sku == sku, (
            f"first hit was {rows[0].internal_sku!r}, expected exact "
            f"match {sku!r}"
        )
    finally:
        db.close()


def check_prefix_then_substring(ids: dict[str, int]) -> None:
    """Term ``ALPHA`` (prefix of two ALPHA internal_skus, substring of
    BETA's product_title) must rank the two ALPHA rows ahead of the
    BETA row."""
    db = SessionLocal()
    try:
        rows = search_catalog(db, q=_PREFIX + "ALPHA", limit=10)
        positions = {r.internal_sku: idx for idx, r in enumerate(rows)}
        a1 = positions.get(_PREFIX + "ALPHA-1234")
        a2 = positions.get(_PREFIX + "ALPHA-9999")
        b1 = positions.get(_PREFIX + "BETA-0001")
        assert a1 is not None and a2 is not None, (
            f"missing ALPHA rows in {list(positions)}"
        )
        if b1 is not None:
            assert b1 > max(a1, a2), (
                f"BETA substring match ranked {b1} ahead of ALPHA "
                f"prefixes ({a1}, {a2})"
            )
    finally:
        db.close()


def check_public_code_exact_match(ids: dict[str, int]) -> None:
    """Pasting a BVX code (the secondary identifier on staff surfaces)
    must resolve to the matching row at rank 0."""
    db = SessionLocal()
    try:
        # Pull alpha-1234's public_code so the test doesn't hardcode a
        # value that depends on the global numbering sequence.
        target = (
            db.query(__import__("database.models", fromlist=["CatalogItem"]).CatalogItem)
            .filter_by(id=ids[_PREFIX + "ALPHA-1234"])
            .one()
        )
        rows = search_catalog(db, q=target.public_code, limit=5)
        assert rows, f"public_code {target.public_code} returned no hits"
        assert rows[0].public_code == target.public_code, (
            f"public_code search ranked {rows[0].public_code!r} ahead of "
            f"the exact match"
        )
    finally:
        db.close()


def check_normalization_space_to_dash(ids: dict[str, int]) -> None:
    """``ALPHA 1234`` (space-separated) must match
    ``ALPHA-1234`` (dash-joined) so staff can paste either spelling."""
    typed = _PREFIX.replace("-", " ") + "ALPHA 1234"
    db = SessionLocal()
    try:
        rows = search_catalog(db, q=typed, limit=5)
        skus = [r.internal_sku for r in rows]
        assert _PREFIX + "ALPHA-1234" in skus, (
            f"space-normalization missed exact match: rows={skus}"
        )
    finally:
        db.close()


def check_normalization_slash_to_dash(ids: dict[str, int]) -> None:
    """``regal/royal`` (slash-joined) must match the GAMMA row whose
    ``color`` is stored as ``Regal Royal``. The seeded Morilee data
    also has many ``REGAL-ROYAL`` rows at the same rank, so use a
    generous limit and check membership rather than position."""
    db = SessionLocal()
    try:
        rows = search_catalog(db, q="regal/royal", limit=500)
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "GAMMA-REGAL-ROYAL" in skus, (
            f"slash↔dash normalization missed GAMMA in {len(skus)} rows"
        )
    finally:
        db.close()


def check_inactive_hidden_by_default(ids: dict[str, int]) -> None:
    """Inactive DELTA must not surface on a default ``q='delta'``
    search but must appear when ``include_inactive=True``."""
    db = SessionLocal()
    try:
        active_only = search_catalog(db, q="delta retired", limit=10)
        skus_active = {r.internal_sku for r in active_only}
        assert _PREFIX + "DELTA-0001" not in skus_active, (
            "inactive row appeared in default search"
        )
        with_inactive = search_catalog(
            db, q="delta retired", include_inactive=True, limit=10
        )
        skus_all = {r.internal_sku for r in with_inactive}
        assert _PREFIX + "DELTA-0001" in skus_all, (
            "include_inactive did not surface DELTA"
        )
    finally:
        db.close()


def check_no_match_returns_empty(ids: dict[str, int]) -> None:
    db = SessionLocal()
    try:
        rows = search_catalog(
            db, q=_PREFIX + "ABSOLUTELY-NOT-A-MATCH-ZZZ", limit=10
        )
        assert rows == [], f"expected empty result, got {rows}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_seq()
    print(f"catalog_public_code_seq baseline = {seq_baseline}")
    ids = _seed()
    print(f"seeded {len(ids)} catalog rows")
    try:
        check_empty_term_returns_idle_list(ids)
        print("empty term returns idle list ok")
        check_exact_internal_sku_outranks_substring(ids)
        print("exact internal_sku outranks substring ok")
        check_prefix_then_substring(ids)
        print("prefix outranks substring ok")
        check_public_code_exact_match(ids)
        print("public_code exact match ok")
        check_normalization_space_to_dash(ids)
        print("space→dash normalization ok")
        check_normalization_slash_to_dash(ids)
        print("slash→dash normalization ok")
        check_inactive_hidden_by_default(ids)
        print("inactive hidden by default ok")
        check_no_match_returns_empty(ids)
        print("no-match returns empty ok")
        print()
        print("catalog phase 3 search smoke ok")
        return 0
    finally:
        _wipe()
        _reset_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
