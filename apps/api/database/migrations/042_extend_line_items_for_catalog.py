from sqlalchemy import text


def upgrade(connection) -> None:
    # Catalog SKU obfuscation Phase 2: extend invoice_line_items and
    # quote_line_items with catalog-backed columns. The two tables stay
    # parallel because this repo has separate write paths for invoices and
    # quotes (services/invoice_service.py and services/quote_service.py),
    # each with its own line-item table and editor. Phase 2 keeps that
    # parallelism instead of collapsing the schemas.
    #
    # New columns on both tables:
    #
    #   catalog_item_id    Optional FK into catalog_items. When set, the
    #                      line is "catalog-backed": customer-facing copy
    #                      is derived from the catalog row at render time
    #                      (Phase 4) and staff-typed free text on the row
    #                      is ignored on customer surfaces.
    #
    #   size_label         Per-line size for catalog rows that are
    #                      style+color (the v1 catalog granularity).
    #                      Plain text so values like "08", "L", or
    #                      "extended-12" all fit without a vocabulary.
    #
    #   public_description Customer-safe one-liner for non-catalog lines
    #                      (alterations, rush fees, deposits, discounts).
    #                      For catalog-backed lines this column must be
    #                      NULL — the catalog row owns the customer copy.
    #                      Enforced at the DB level with the mutual-
    #                      exclusion CHECK below.
    #
    #   internal_notes     Staff-only context (vendor PO, fitting notes).
    #                      Never returned from any public endpoint, never
    #                      rendered on any customer surface.
    #
    # Legacy `description` is now nullable. New lines (catalog-backed and
    # non-catalog) do not write to it; existing rows keep their text and
    # continue rendering on customer surfaces because that text is already
    # on issued PDFs and portal pages in customers' hands. The Phase 4
    # render swap stops legacy `notes` rendering on every line.
    #
    # The CHECK constraint enforces the catalog-backed contract at the DB
    # level so a future migration script or admin SQL session cannot
    # quietly write `public_description` onto a catalog-backed line and
    # leak the staff text to customers when the Phase 4 renderers ship.
    # Existing legacy rows have catalog_item_id IS NULL and
    # public_description IS NULL, so the constraint is satisfied.
    for table_name, prefix in (
        ("invoice_line_items", "invoice"),
        ("quote_line_items", "quote"),
    ):
        connection.execute(
            text(
                f"ALTER TABLE {table_name} "
                f"ALTER COLUMN description DROP NOT NULL"
            )
        )
        connection.execute(
            text(
                f"""
                ALTER TABLE {table_name}
                    ADD COLUMN catalog_item_id INTEGER
                        REFERENCES catalog_items(id) ON DELETE RESTRICT,
                    ADD COLUMN size_label VARCHAR(40),
                    ADD COLUMN public_description TEXT,
                    ADD COLUMN internal_notes TEXT
                """
            )
        )
        connection.execute(
            text(
                f"ALTER TABLE {table_name} "
                f"ADD CONSTRAINT chk_{prefix}_line_catalog_public_desc_exclusive "
                "CHECK (catalog_item_id IS NULL OR public_description IS NULL)"
            )
        )
        # Partial index: catalog-backed lines are the minority but every
        # catalog-aware lookup ("show me every line that uses catalog row
        # 42") filters on this column. Partial keeps the index small for
        # legacy-heavy invoice tables.
        connection.execute(
            text(
                f"CREATE INDEX idx_{table_name}_catalog_item "
                f"ON {table_name}(catalog_item_id) "
                "WHERE catalog_item_id IS NOT NULL"
            )
        )
