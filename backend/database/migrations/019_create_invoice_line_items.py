from sqlalchemy import text


def upgrade(connection) -> None:
    # Invoice line items. Separate table (not JSON) so per-line history is
    # queryable. Single tax slot per line in v1; Texas sales tax is uniform.
    # All money math the service does in Decimal/ROUND_HALF_EVEN lands in
    # *_cents columns rounded once.
    connection.execute(
        text(
            """
            CREATE TABLE invoice_line_items (
                id                  SERIAL PRIMARY KEY,
                invoice_id          INTEGER NOT NULL
                                    REFERENCES invoices(id) ON DELETE CASCADE,
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

                CONSTRAINT chk_line_kind CHECK (
                    kind IN ('product', 'service', 'alteration', 'fee')
                ),
                CONSTRAINT chk_line_quantity_pos CHECK (quantity > 0),
                CONSTRAINT chk_line_unit_price_nonneg CHECK (unit_price_cents >= 0),
                CONSTRAINT chk_line_discount_le_subtotal CHECK (
                    -- gross_line_cents = quantity * unit_price, expressed in cents
                    -- via the NUMERIC(10,2) quantity * BIGINT unit_price_cents.
                    -- The ::BIGINT cast keeps the comparison integer-clean.
                    discount_cents <= (quantity * unit_price_cents)::BIGINT
                ),
                CONSTRAINT chk_line_tax_rate_range CHECK (
                    tax_rate >= 0 AND tax_rate < 1
                )
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoice_line_items_invoice_sort "
            "ON invoice_line_items(invoice_id, sort_order)"
        )
    )
