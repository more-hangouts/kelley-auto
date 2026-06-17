"""Catalog SKU obfuscation Phase 1 smoke tests.

Exercises the model + service end-to-end against a real Postgres
connection:

  - `create_catalog_item` mints a fresh `BVX-NNNNN` code under the
    `numbering_state` row lock.
  - Sequential creates produce a contiguous sequence with no skips.
  - Duplicate `internal_sku` is rejected, and the failed transaction
    does NOT increment `catalog_public_code_seq`.
  - Customer-safe formatters (`customer_sku`, `customer_line_description`)
    never include designer name, style number, internal SKU, or product
    title; designer identity must not leak through these helpers.
  - N concurrent threads inserting via `create_catalog_item` produce N
    distinct codes in a contiguous range; the row lock on
    `catalog_public_code_seq` actually serializes allocation.

Why direct service calls and not HTTP: there is no router yet at this
phase, and the FastAPI TestClient single-threads through one ASGI app
which would defeat the concurrency proof. Each thread opens its own
SessionLocal so they hit Postgres on independent connections.

Runs as a script:

    venv/bin/python tests/test_catalog_smoke.py

Cleans up every catalog row created (matched by internal_sku prefix) and
resets `catalog_public_code_seq` back to its baseline so repeated runs
are idempotent.

Internal helpers are named `check_*` rather than `test_*` so a broad
`pytest tests/` sweep does not collect them as parameterless tests.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from sqlalchemy.exc import IntegrityError  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
    customer_line_description,
    customer_sku,
    staff_sku,
)


# Per-run prefix isolates this test's rows from any real catalog data
# already in the DB. The cleanup wipes by this prefix.
_SKU_PREFIX = f"TEST-{uuid.uuid4().hex[:8].upper()}-"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _wipe_test_rows() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": _SKU_PREFIX + "%"},
        )
        db.commit()
    finally:
        db.close()


def _make_input(suffix: str, **overrides) -> CatalogItemInput:
    base = dict(
        internal_sku=_SKU_PREFIX + suffix,
        color="Ivory",
        category="quince_gown",
        designer="Morilee",
        style_number="89216",
        product_title="Beatriz Quinceanera Dress",
    )
    base.update(overrides)
    return CatalogItemInput(**base)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_create_and_mint_code() -> None:
    seq_before = _get_seq()
    db = SessionLocal()
    try:
        item = create_catalog_item(db, _make_input("BASIC"))
        db.commit()
        assert item.id is not None, "id was not flushed"
        expected = f"BVX-{seq_before + 1:05d}"
        assert item.public_code == expected, (
            f"got {item.public_code!r}, expected {expected!r}"
        )
        assert item.internal_sku == _SKU_PREFIX + "BASIC"
    finally:
        db.close()
    seq_after = _get_seq()
    assert seq_after == seq_before + 1, (
        f"seq did not advance: {seq_before} -> {seq_after}"
    )


def check_sequential_codes_no_skips() -> None:
    seq_before = _get_seq()
    db = SessionLocal()
    codes: list[str] = []
    try:
        for n in range(3):
            item = create_catalog_item(db, _make_input(f"SEQ{n}"))
            codes.append(item.public_code)
        db.commit()
    finally:
        db.close()
    expected = [
        f"BVX-{seq_before + 1:05d}",
        f"BVX-{seq_before + 2:05d}",
        f"BVX-{seq_before + 3:05d}",
    ]
    assert codes == expected, f"got {codes}, expected {expected}"


def check_duplicate_internal_sku_rejected() -> None:
    seq_before = _get_seq()
    db = SessionLocal()
    try:
        create_catalog_item(db, _make_input("DUP"))
        db.commit()
    finally:
        db.close()
    seq_after_first = _get_seq()
    assert seq_after_first == seq_before + 1

    # Second insert with the same internal_sku must fail at INSERT time
    # via the unique constraint, and the entire transaction (including
    # the seq increment) must roll back.
    db = SessionLocal()
    try:
        try:
            create_catalog_item(db, _make_input("DUP"))
            db.commit()
            raise AssertionError(
                "expected duplicate internal_sku to raise IntegrityError"
            )
        except IntegrityError:
            db.rollback()
    finally:
        db.close()

    seq_after_second = _get_seq()
    assert seq_after_second == seq_after_first, (
        f"failed insert leaked a seq increment: {seq_after_first} -> "
        f"{seq_after_second}; the rollback should have undone the UPDATE "
        f"to numbering_state.catalog_public_code_seq"
    )


def check_customer_line_description_formatter() -> None:
    item = CatalogItem(
        internal_sku="MORI-89216-IVORY",
        public_code="BVX-00042",
        designer="Morilee",
        style_number="89216",
        color="Ivory",
        category="quince_gown",
        product_title="Beatriz Quinceanera Dress",
    )
    assert customer_sku(item) == "BVX-00042"
    assert staff_sku(item) == "MORI-89216-IVORY"

    desc = customer_line_description(item)
    assert desc == "Quince gown / Ivory", f"unexpected: {desc!r}"

    desc_with_size = customer_line_description(item, size_label="08")
    assert desc_with_size == "Quince gown / Ivory / Size 08", (
        f"unexpected: {desc_with_size!r}"
    )

    # house_name takes precedence over the category label.
    item.house_name = "Isabella"
    desc_house = customer_line_description(item, size_label="10")
    assert desc_house == "Isabella / Ivory / Size 10", (
        f"unexpected: {desc_house!r}"
    )

    # bridal_gown maps to the bridal label.
    bridal = CatalogItem(
        internal_sku="ALLURE-9999-IVORY",
        public_code="BVX-00099",
        designer="Allure Bridals",
        color="Ivory",
        category="bridal_gown",
    )
    assert customer_line_description(bridal) == "Bridal gown / Ivory"


def check_no_designer_leak_in_helpers() -> None:
    """Defensive: customer-facing helpers must never expose designer
    name, style number, internal SKU, or product title. This test is
    the line of defense against a future refactor that "helpfully" adds
    them back.
    """
    item = CatalogItem(
        internal_sku="MORI-89216-IVORY",
        public_code="BVX-00042",
        designer="Morilee",
        style_number="89216",
        color="Ivory",
        category="quince_gown",
        product_title="Beatriz Quinceanera Dress",
    )
    forbidden = (
        "Morilee",
        "morilee",
        "MORI",
        "89216",
        "MORI-89216-IVORY",
        "Beatriz",
    )
    for output in (
        customer_sku(item),
        customer_line_description(item),
        customer_line_description(item, size_label="08"),
    ):
        for token in forbidden:
            assert token not in output, (
                f"forbidden token {token!r} leaked into customer-facing "
                f"output {output!r}"
            )


def check_public_code_not_settable_by_service_input() -> None:
    assert "public_code" not in CatalogItemInput.__dataclass_fields__, (
        "CatalogItemInput must not expose public_code; the catalog service "
        "is the only Phase 1 public-code allocator"
    )


def check_concurrent_allocation() -> None:
    """N threads each call `create_catalog_item` simultaneously. The
    SELECT FOR UPDATE on `numbering_state.catalog_public_code_seq` must
    serialize allocation so all N codes are distinct and form a
    contiguous range.
    """
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    seq_before = _get_seq()

    def _do_insert(idx: int) -> str:
        # Each thread on its own connection so they hit Postgres in
        # parallel rather than serializing on a shared session.
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            item = create_catalog_item(db, _make_input(f"CONC{idx:02d}"))
            db.commit()
            return item.public_code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(_do_insert, i) for i in range(n_threads)]
        codes = [f.result() for f in as_completed(futures)]

    assert len(set(codes)) == n_threads, (
        f"expected {n_threads} distinct codes, got "
        f"{len(set(codes))} unique out of {len(codes)}: {codes}"
    )
    seq_values = sorted(int(c.split("-")[1]) for c in codes)
    expected_range = list(
        range(seq_before + 1, seq_before + 1 + n_threads)
    )
    assert seq_values == expected_range, (
        f"expected contiguous range {expected_range}, got {seq_values}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using internal_sku prefix {_SKU_PREFIX}")
    baseline = _get_seq()
    print(f"catalog_public_code_seq baseline = {baseline}")
    try:
        check_create_and_mint_code()
        print("create + mint ok")
        check_sequential_codes_no_skips()
        print("sequential codes ok")
        check_duplicate_internal_sku_rejected()
        print("duplicate internal_sku rejected (no seq drift) ok")
        check_customer_line_description_formatter()
        print("customer_line_description formatter ok")
        check_no_designer_leak_in_helpers()
        print("no designer/style/sku leak in customer helpers ok")
        check_public_code_not_settable_by_service_input()
        print("public_code not settable through service input ok")
        check_concurrent_allocation()
        print("concurrent allocation ok")
        print()
        print("catalog smoke ok")
        return 0
    finally:
        _wipe_test_rows()
        _reset_seq(baseline)
        print(f"cleanup: rows wiped, seq reset to {baseline}")


if __name__ == "__main__":
    sys.exit(main())
