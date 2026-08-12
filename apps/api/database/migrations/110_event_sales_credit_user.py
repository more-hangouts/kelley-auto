"""Separate sales commission credit from lead ownership.

``events.owner_user_id`` answers "whose queue is this in?" and is expected
to change as admins rebalance the pipeline. Commission credit answers a
different question: "who brought this customer in?" Reusing ownership for
that would erase the credit trail during routine reassignment, so this
migration adds a second nullable FK.

NULL is intentional: some customers walk in on their own, and inventing a
credited rep would be worse than leaving the answer blank.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE events
              ADD COLUMN IF NOT EXISTS sales_credit_user_id
              INTEGER REFERENCES users(id) ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_sales_credit_user_id
              ON events(sales_credit_user_id)
              WHERE sales_credit_user_id IS NOT NULL
            """
        )
    )

    # DML probe: the FK and partial index must exist after the migration.
    fk = connection.execute(
        text(
            """
            SELECT 1
              FROM pg_constraint
             WHERE conname = 'events_sales_credit_user_id_fkey'
               AND conrelid = 'events'::regclass
            """
        )
    ).first()
    assert fk is not None, "events.sales_credit_user_id FK was not created"

    idx = connection.execute(
        text(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND tablename = 'events'
               AND indexname = 'idx_events_sales_credit_user_id'
            """
        )
    ).scalar()
    assert idx is not None, "idx_events_sales_credit_user_id was not created"
    assert "sales_credit_user_id" in idx, idx
    assert "where (sales_credit_user_id is not null)" in idx.lower(), idx
