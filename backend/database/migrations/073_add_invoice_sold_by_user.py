"""Phase 10 Slice 7 — explicit invoice sales attribution.

SPLH and labor-sales analytics need to know who sold an invoice, not
just who created the row. Existing invoices are backfilled from
created_by_user_id so historical reporting has a reasonable default;
future invoice creation writes both fields explicitly.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE invoices
            ADD COLUMN IF NOT EXISTS sold_by_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE invoices
            SET sold_by_user_id = created_by_user_id
            WHERE sold_by_user_id IS NULL
              AND created_by_user_id IS NOT NULL
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_invoices_sold_by_issue_date
            ON invoices(sold_by_user_id, issue_date)
            WHERE deleted_at IS NULL
            """
        )
    )

    # ===== DML probes =====

    invoice_row = connection.execute(
        text(
            """
            SELECT id, created_by_user_id
            FROM invoices
            WHERE created_by_user_id IS NOT NULL
            ORDER BY id
            LIMIT 1
            """
        )
    ).first()
    if invoice_row is not None:
        assert invoice_row[1] is not None
        sold_by = connection.execute(
            text("SELECT sold_by_user_id FROM invoices WHERE id = :id"),
            {"id": int(invoice_row[0])},
        ).scalar()
        assert int(sold_by) == int(invoice_row[1])

    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    invoice_any = connection.execute(
        text("SELECT id FROM invoices ORDER BY id LIMIT 1")
    ).first()
    if user_row is None or invoice_any is None:
        return

    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                UPDATE invoices
                SET sold_by_user_id = :uid
                WHERE id = :invoice_id
                """
            ),
            {"uid": int(user_row[0]), "invoice_id": int(invoice_any[0])},
        )
        check_value = connection.execute(
            text("SELECT sold_by_user_id FROM invoices WHERE id = :invoice_id"),
            {"invoice_id": int(invoice_any[0])},
        ).scalar()
        assert int(check_value) == int(user_row[0])
    finally:
        sp.rollback()
