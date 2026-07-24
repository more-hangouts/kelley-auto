from sqlalchemy import text


def upgrade(connection) -> None:
    # Global Search Phase 4: document-number indexes.
    #
    # Invoice and quote numbers, vendor order numbers, and catalog public
    # codes are case-insensitive but accent-free by construction, so these
    # branches use plain lower(...) instead of the f_unaccent(lower(...))
    # expression required for Spanish names in Phase 1.
    #
    # Special-order UI is not shipped yet, but these indexes are cheap and
    # prepare the database for the future special_order search branch.
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    connection.execute(
        text(
            "CREATE INDEX invoices_number_trgm "
            "ON invoices USING gin (lower(invoice_number) gin_trgm_ops) "
            "WHERE invoice_number IS NOT NULL AND deleted_at IS NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX quotes_number_trgm "
            "ON quotes USING gin (lower(quote_number) gin_trgm_ops) "
            "WHERE quote_number IS NOT NULL AND deleted_at IS NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX special_orders_vendor_order_number_trgm "
            "ON special_orders USING gin "
            "(lower(vendor_order_number) gin_trgm_ops) "
            "WHERE vendor_order_number IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX catalog_items_public_code_trgm "
            "ON catalog_items USING gin (lower(public_code) gin_trgm_ops)"
        )
    )
