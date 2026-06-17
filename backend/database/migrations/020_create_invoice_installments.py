from sqlalchemy import text


def upgrade(connection) -> None:
    # Payment schedule. The deposit IS the first row, balance IS the second.
    # Phase 12 lets staff add more rows to support installment plans without
    # changing this schema. SUM(amount_cents) per invoice MUST equal the
    # invoice's total_cents once the invoice is sent — enforced by the
    # service, not the DB, since it spans rows.
    connection.execute(
        text(
            """
            CREATE TABLE invoice_installments (
                id           SERIAL PRIMARY KEY,
                invoice_id   INTEGER NOT NULL
                             REFERENCES invoices(id) ON DELETE CASCADE,
                sort_order   INTEGER NOT NULL DEFAULT 0,
                label        VARCHAR(60) NOT NULL,
                amount_cents BIGINT NOT NULL,
                due_date     DATE NOT NULL,
                paid_at      TIMESTAMPTZ,
                staff_notes  TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_installment_amount_pos CHECK (amount_cents > 0)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoice_installments_invoice_sort "
            "ON invoice_installments(invoice_id, sort_order)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoice_installments_due_date "
            "ON invoice_installments(due_date)"
        )
    )
