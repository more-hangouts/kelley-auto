from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 4 of the discount/payment-term refactor.
    #
    # Quotes carry a payment schedule the same shape invoices already
    # have, minus payment-state columns. Quote schedules let staff lock
    # in 1/2/3-payment plans on the quote so conversion to invoice
    # carries the customer's chosen plan forward instead of minting a
    # default 50/50.
    #
    # Differences from `invoice_installments`:
    # - `label` is nullable here. Quote rows are mostly draft scratchpad
    #   data; the conversion path fills in a default ("Installment N")
    #   when copying into invoice_installments where label is NOT NULL.
    # - No `paid_at` or `staff_notes`. Quotes never carry payment state;
    #   nothing has been paid yet.
    #
    # Sort order index mirrors invoice_installments so the editor can
    # ORDER BY (quote_id, sort_order) in line with the invoice surface.
    connection.execute(
        text(
            """
            CREATE TABLE quote_installments (
                id           BIGSERIAL PRIMARY KEY,
                quote_id     INTEGER NOT NULL
                             REFERENCES quotes(id) ON DELETE CASCADE,
                sort_order   INTEGER NOT NULL DEFAULT 0,
                label        TEXT,
                amount_cents BIGINT NOT NULL,
                due_date     DATE NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_quote_installment_amount_pos
                  CHECK (amount_cents > 0)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_quote_installments_quote_sort "
            "ON quote_installments(quote_id, sort_order)"
        )
    )

    # Project rule: validate with real DML against the new table before
    # declaring the phase done. Borrow an existing quote.id (the table
    # is empty in fresh installs; that's OK — we just exit early).
    quote_row = connection.execute(
        text("SELECT id FROM quotes ORDER BY id LIMIT 1")
    ).first()
    if quote_row is None:
        return

    quote_id = int(quote_row[0])

    # Happy path: two probe rows, including a NULL-label row to confirm
    # the nullable column accepts it.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                INSERT INTO quote_installments (
                  quote_id, sort_order, label, amount_cents, due_date
                ) VALUES
                  (:qid, 0, 'Probe Deposit', 100, '2099-01-01'),
                  (:qid, 1, NULL,            100, '2099-02-01')
                """
            ),
            {"qid": quote_id},
        )
        rows = connection.execute(
            text(
                "SELECT sort_order, label, amount_cents, due_date "
                "FROM quote_installments WHERE quote_id = :qid "
                "ORDER BY sort_order"
            ),
            {"qid": quote_id},
        ).all()
        assert len(rows) == 2, rows
        assert rows[0][1] == "Probe Deposit", rows
        assert rows[1][1] is None, rows
    finally:
        sp.rollback()

    # Reject path: amount_cents = 0 violates the CHECK. Run in its own
    # savepoint so the constraint failure can't poison the outer
    # transaction.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    "INSERT INTO quote_installments "
                    "(quote_id, sort_order, label, amount_cents, due_date) "
                    "VALUES (:qid, 99, 'bad', 0, '2099-01-01')"
                ),
                {"qid": quote_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "chk_quote_installment_amount_pos did not reject zero amount"
            )
    finally:
        sp.rollback()
