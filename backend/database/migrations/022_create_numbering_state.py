from sqlalchemy import text


def upgrade(connection) -> None:
    # Single-row table that hands out invoice and quote numbers atomically on
    # first send. Phase 2's invoice_service grabs SELECT ... FOR UPDATE on
    # this row, increments the appropriate counter (resetting to 1 on year
    # rollover), and returns the formatted number. Drafts never call this;
    # only mark_sent does.
    connection.execute(
        text(
            """
            CREATE TABLE numbering_state (
                id           SMALLINT PRIMARY KEY DEFAULT 1,
                invoice_year SMALLINT NOT NULL,
                invoice_seq  INTEGER NOT NULL DEFAULT 0,
                quote_year   SMALLINT NOT NULL,
                quote_seq    INTEGER NOT NULL DEFAULT 0,
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_numbering_singleton CHECK (id = 1),
                CONSTRAINT chk_numbering_seq_nonneg CHECK (
                    invoice_seq >= 0 AND quote_seq >= 0
                )
            )
            """
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO numbering_state (id, invoice_year, quote_year)
            VALUES (
                1,
                EXTRACT(YEAR FROM NOW())::SMALLINT,
                EXTRACT(YEAR FROM NOW())::SMALLINT
            )
            """
        )
    )
