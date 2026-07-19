"""Wholesale cost + price-list provenance on catalog_items.

Bella's retail price is computed from wholesale cost (see
services/pricing.py), not from Morilee's MSRP. To make monthly price-list
imports a controlled, auditable refresh, the catalog row records:

  - `wholesale_cents`: the wholesale/base cost the retail price was
    derived from. The only Morilee-controlled number; the input to the
    multiplier bands. CHECK keeps it a non-negative integer (mirrors the
    `unit_price_cents` rule from migration 067).
  - `wholesale_as_of`: the date the wholesale figure is current as of,
    parsed from the price-list WORKSHEET TAB name (e.g. "Quince as of
    6126" -> 2026-06-01), not the workbook filename. Lets staff see, per
    category, how fresh the pricing is.
  - `wholesale_source`: free-text provenance (e.g.
    "Morilee Consolidated / Quince as of 6126") so a row's price can be
    traced back to the exact list it came from.

Computed retail still lands in the existing `unit_price_cents`; these
columns hold the inputs/provenance behind it. Existing quotes/invoices
snapshot their own `unit_price_cents` on their line rows, so a reimport
that updates catalog pricing never mutates historical documents.

All three columns are nullable: non-Morilee rows and rows not yet covered
by a price list simply leave them NULL.

DML probes round-trip a populated row and assert the CHECK rejects a
negative wholesale.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
                ADD COLUMN wholesale_cents INTEGER NULL
                    CHECK (wholesale_cents IS NULL OR wholesale_cents >= 0),
                ADD COLUMN wholesale_as_of DATE NULL,
                ADD COLUMN wholesale_source TEXT NULL
            """
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        # Minimal valid catalog row (public_code must match ^BVX-[0-9]{5}$,
        # category must be in the whitelist, image_urls a JSONB array).
        item_id = connection.execute(
            text(
                """
                INSERT INTO catalog_items
                    (internal_sku, public_code, color, category, image_urls,
                     wholesale_cents, wholesale_as_of, wholesale_source,
                     unit_price_cents)
                VALUES
                    ('WHL-PROBE-SKU', 'BVX-99901', 'Black/Gold',
                     'quince_gown', '[]'::jsonb,
                     89900, DATE '2026-06-01',
                     'Morilee Consolidated / Quince as of 6126', 269700)
                RETURNING id
                """
            )
        ).scalar()

        row = connection.execute(
            text(
                "SELECT wholesale_cents, wholesale_as_of, wholesale_source, "
                "unit_price_cents FROM catalog_items WHERE id = :id"
            ),
            {"id": item_id},
        ).first()
        assert row[0] == 89900, "wholesale_cents round-trip"
        assert str(row[1]) == "2026-06-01", "wholesale_as_of round-trip"
        assert row[2].endswith("Quince as of 6126"), "wholesale_source round-trip"
        assert row[3] == 269700, "unit_price_cents unaffected"

        # CHECK rejects a negative wholesale.
        rejected = False
        sp2 = connection.begin_nested()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog_items
                        (internal_sku, public_code, color, category,
                         image_urls, wholesale_cents)
                    VALUES
                        ('WHL-PROBE-NEG', 'BVX-99902', 'Navy', 'quince_gown',
                         '[]'::jsonb, -1)
                    """
                )
            )
        except Exception:
            rejected = True
            sp2.rollback()
        assert rejected, "negative wholesale_cents must violate the CHECK"
    finally:
        sp.rollback()
