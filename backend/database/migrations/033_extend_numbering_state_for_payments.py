from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 6: extend the singleton numbering_state to track payment
    # numbering alongside invoices and quotes. Payment numbers follow the
    # same `PMT-YYYY-NNNNNN` shape with the same `SELECT FOR UPDATE`
    # allocation pattern under a row-level lock. Reusing the singleton row
    # means concurrent invoice/quote/payment sends serialize on the same
    # lock — slower in extreme contention but simpler to reason about than
    # per-document-type tables, and the volume here is tiny.
    connection.execute(
        text(
            """
            ALTER TABLE numbering_state
                ADD COLUMN payment_year SMALLINT NOT NULL DEFAULT EXTRACT(YEAR FROM NOW()),
                ADD COLUMN payment_seq  INTEGER  NOT NULL DEFAULT 0
            """
        )
    )
    connection.execute(
        text(
            "ALTER TABLE numbering_state "
            "ADD CONSTRAINT chk_numbering_payment_seq_nonneg CHECK (payment_seq >= 0)"
        )
    )
