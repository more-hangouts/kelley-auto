"""Cash car vs buy-here-pay-here, as a property of the vehicle.

Kelley sells two ways. Most of the lot is buy-here-pay-here: the dealer
carries the note, and the number that matters to the shopper is the down
payment (the storefront already leads with "As low as $2,000 down"). A
smaller set are **cash cars** — sold outright, no financing offered. They
are the same rows in the same table, but they are shopped for differently
and the staff talk about them as a separate list, so the site needs to be
able to show them as one.

**A stored flag, not a derived one.** The tempting shortcut is to call
anything under some price a cash car. That gets the common case right and
then quietly lies in both directions: a cheap car the dealer would happily
finance shows up as cash-only, and a pricier one they'll only take cash on
does not. Whether a note is carried is a business decision per car, not a
function of its sticker, so it is stored per car.

Values are constrained to the two the business actually has. 'bhph' is
the default because that is what the lot mostly is, and because it makes
this migration a no-op for all 31 existing rows — nothing changes on the
site until someone deliberately flags a car.

Why a VARCHAR + CHECK rather than a boolean `is_cash_car`: a boolean bakes
in "there are exactly two, and one is the negation of the other". Adding a
third arrangement later (outside lender / consignment) to a boolean means
a new column and a data migration; adding it here means widening the CHECK.
The column also reads correctly at a glance in psql, which `is_cash_car =
false` does not.

Indexed because it is a list filter: the storefront's Cash Cars tab is a
WHERE on this column combined with the existing status gate.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text

SALE_TYPES = ("bhph", "cash")


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
              ADD COLUMN IF NOT EXISTS sale_type VARCHAR(16) NOT NULL
                DEFAULT 'bhph'
            """
        )
    )

    exists = connection.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'chk_catalog_items_sale_type'"
        )
    ).first()
    if not exists:
        values = ", ".join(f"'{v}'" for v in SALE_TYPES)
        connection.execute(
            text(
                f"""
                ALTER TABLE catalog_items
                  ADD CONSTRAINT chk_catalog_items_sale_type
                  CHECK (sale_type IN ({values}))
                """
            )
        )

    # Partial index: the filter only ever runs against vehicles, and the
    # non-vehicle catalog rows carrying the default would just bloat it.
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_catalog_items_sale_type
              ON catalog_items (sale_type)
              WHERE is_vehicle = TRUE
            """
        )
    )
