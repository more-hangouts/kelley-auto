from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 1 follow-up: tighten the money floor on invoice_line_items.
    #
    # Migration 019 only enforced `discount_cents <= quantity * unit_price_cents`
    # and the unit-price nonneg check. That left negative discount, subtotal,
    # tax, and total as accepted values, which contradicts the "money in cents,
    # never negative" rule the rest of the schema follows. The Phase 1 schema
    # smoke caught this when its coverage was expanded.
    connection.execute(
        text(
            """
            ALTER TABLE invoice_line_items
                ADD CONSTRAINT chk_line_discount_nonneg
                    CHECK (discount_cents >= 0),
                ADD CONSTRAINT chk_line_subtotal_nonneg
                    CHECK (line_subtotal_cents >= 0),
                ADD CONSTRAINT chk_line_tax_nonneg
                    CHECK (line_tax_cents >= 0),
                ADD CONSTRAINT chk_line_total_nonneg
                    CHECK (line_total_cents >= 0)
            """
        )
    )
