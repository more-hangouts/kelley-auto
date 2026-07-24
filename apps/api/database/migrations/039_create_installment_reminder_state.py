from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 11. Per-installment idempotency for the reminder cron.
    #
    # Reminders fire against installment due dates, not the
    # invoice-level due_date. A six-row payment plan needs six
    # independent reminder schedules, not one. Each row here mirrors
    # a single invoice_installments row and stamps when each of the
    # three reminders has been sent (NULL means "still eligible").
    #
    # `late_fee_applied_at` blocks the same fee from getting appended
    # twice if reminder3 fires somehow on consecutive days (clock
    # drift, manual cron rerun, etc.).
    connection.execute(
        text(
            """
            CREATE TABLE installment_reminder_state (
                installment_id      INTEGER PRIMARY KEY
                                    REFERENCES invoice_installments(id) ON DELETE CASCADE,
                reminder1_sent_at   TIMESTAMPTZ,
                reminder2_sent_at   TIMESTAMPTZ,
                reminder3_sent_at   TIMESTAMPTZ,
                late_fee_applied_at TIMESTAMPTZ,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    # Hot read path is the cron scan — give it an index that lets us
    # quickly find installments where ANY reminder slot is still NULL.
    # A composite index on the three slots isn't useful (each one is
    # checked independently in the cron), so a per-slot partial index
    # is the right shape. Skipping for v1 — the table is small (one
    # row per installment) and a full table scan stays cheap until
    # the shop has thousands of live installments.
