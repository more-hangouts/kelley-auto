from sqlalchemy import text


def upgrade(connection) -> None:
    # Quote line items. Identical shape to invoice_line_items so a converted
    # quote's lines copy 1:1 into the resulting invoice's lines without a
    # transformation step. The four nonneg-money checks added to invoice
    # lines in migration 024 are baked into this table from creation so
    # quotes never need a follow-up tightening migration.
    connection.execute(
        text(
            """
            CREATE TABLE quote_line_items (
                id                  SERIAL PRIMARY KEY,
                quote_id            INTEGER NOT NULL
                                    REFERENCES quotes(id) ON DELETE CASCADE,
                sort_order          INTEGER NOT NULL DEFAULT 0,
                kind                VARCHAR(16) NOT NULL DEFAULT 'product',
                product_key         VARCHAR(120),
                description         TEXT NOT NULL,
                quantity            NUMERIC(10, 2) NOT NULL DEFAULT 1,
                unit_price_cents    BIGINT NOT NULL,
                discount_cents      BIGINT NOT NULL DEFAULT 0,
                tax_rate            NUMERIC(7, 5) NOT NULL DEFAULT 0,
                tax_name            VARCHAR(40),
                line_subtotal_cents BIGINT NOT NULL,
                line_tax_cents      BIGINT NOT NULL,
                line_total_cents    BIGINT NOT NULL,
                notes               TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_quote_line_kind CHECK (
                    kind IN ('product', 'service', 'alteration', 'fee')
                ),
                CONSTRAINT chk_quote_line_quantity_pos CHECK (quantity > 0),
                CONSTRAINT chk_quote_line_unit_price_nonneg CHECK (unit_price_cents >= 0),
                CONSTRAINT chk_quote_line_discount_le_subtotal CHECK (
                    discount_cents <= (quantity * unit_price_cents)::BIGINT
                ),
                CONSTRAINT chk_quote_line_tax_rate_range CHECK (
                    tax_rate >= 0 AND tax_rate < 1
                ),
                CONSTRAINT chk_quote_line_discount_nonneg CHECK (discount_cents >= 0),
                CONSTRAINT chk_quote_line_subtotal_nonneg CHECK (line_subtotal_cents >= 0),
                CONSTRAINT chk_quote_line_tax_nonneg CHECK (line_tax_cents >= 0),
                CONSTRAINT chk_quote_line_total_nonneg CHECK (line_total_cents >= 0)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_quote_line_items_quote_sort "
            "ON quote_line_items(quote_id, sort_order)"
        )
    )
