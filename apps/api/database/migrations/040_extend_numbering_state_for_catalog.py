from sqlalchemy import text


def upgrade(connection) -> None:
    # Catalog SKU obfuscation Phase 1: extend the singleton numbering_state
    # to mint catalog public codes (BVX-NNNNN) under the same row-level
    # SELECT ... FOR UPDATE lock that already serializes invoice, quote,
    # and payment numbering. Reusing the singleton keeps allocation logic
    # in one place; catalog inserts are infrequent so the shared lock has
    # no practical contention cost.
    #
    # No year column unlike invoice/quote/payment numbering: catalog public
    # codes do not reset annually. Once a public_code lands on an issued
    # invoice or quote it is part of the audit trail and must remain a
    # stable identifier for the lifetime of the catalog row.
    connection.execute(
        text(
            """
            ALTER TABLE numbering_state
                ADD COLUMN catalog_public_code_seq INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    connection.execute(
        text(
            "ALTER TABLE numbering_state "
            "ADD CONSTRAINT chk_numbering_catalog_public_code_seq_nonneg "
            "CHECK (catalog_public_code_seq >= 0)"
        )
    )
