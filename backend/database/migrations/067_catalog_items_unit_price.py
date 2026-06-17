"""Default unit price on catalog items.

Adds `catalog_items.unit_price_cents` (INTEGER, NULL = no default
price). When set, the admin catalog editor and the invoice/quote
line-item editors treat it as a pre-fill: picking the item into a line
seeds `unit_price_cents` on that line. The line price stays editable
after the pre-fill so staff can apply one-off adjustments without
mutating the catalog row.

NULL is the only safe default for existing rows: the catalog was
backfilled from scraped vendor data with no price, so any non-NULL
default here would be a guess. NULL also disables the auto-fill path
at the editor level — `CatalogPicker.onChange` only seeds the line
when `unit_price_cents` is a number.

Past quotes and invoices are unaffected: their line-level
`unit_price_cents` is copied onto the line at pick time (Phase 4
behavior), so later catalog price edits do not retroactively rewrite
issued documents.

CHECK keeps prices non-negative. Negative line totals exist (discount
lines), but those go on a discount line item, not on a catalog row.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
                ADD COLUMN IF NOT EXISTS unit_price_cents INTEGER
            """
        )
    )

    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
                ADD CONSTRAINT chk_catalog_items_unit_price_nonneg
                CHECK (unit_price_cents IS NULL OR unit_price_cents >= 0)
            """
        )
    )

    connection.execute(
        text(
            """
            COMMENT ON COLUMN catalog_items.unit_price_cents IS
            'Default unit price (cents) used to pre-fill invoice/quote '
            'line items when this catalog row is picked. NULL = no '
            'default; the line price is entered manually. Editable in '
            'the admin catalog page; past documents are unaffected '
            'because line prices are copied at pick time.'
            """
        )
    )


def downgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE catalog_items "
            "DROP CONSTRAINT IF EXISTS chk_catalog_items_unit_price_nonneg"
        )
    )
    connection.execute(
        text("ALTER TABLE catalog_items DROP COLUMN IF EXISTS unit_price_cents")
    )
