from sqlalchemy import text


def upgrade(connection) -> None:
    # Append-only audit table: every refund operation emits one row. The
    # service uses `from_unapplied_cents` to track how much of a payment's
    # accumulated `refunded_cents` came out of the unapplied pool versus
    # out of allocations — that split matters for the activity timeline
    # (Phase 9) and for the recomputation invariant
    # (`unapplied_cents = amount - applied - refunded`).
    #
    # `from_allocations_json` is the audit detail of which allocations
    # got clawed back and by how much. JSONB rather than a child table
    # because allocation refunds are immutable history, not relational.
    connection.execute(
        text(
            """
            CREATE TABLE refund_events (
                id                       SERIAL PRIMARY KEY,
                payment_id               INTEGER NOT NULL
                                         REFERENCES payments(id) ON DELETE RESTRICT,
                amount_cents             BIGINT NOT NULL,
                from_unapplied_cents     BIGINT NOT NULL DEFAULT 0,
                from_allocations_json    JSONB NOT NULL DEFAULT '[]',
                refund_method            VARCHAR(20) NOT NULL,
                refund_reference         VARCHAR(120),
                notes                    TEXT,
                actor_user_id            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_refund_amount_pos CHECK (amount_cents > 0),
                CONSTRAINT chk_refund_from_unapplied_nonneg CHECK (
                    from_unapplied_cents >= 0
                ),
                CONSTRAINT chk_refund_from_unapplied_le_amount CHECK (
                    from_unapplied_cents <= amount_cents
                ),
                CONSTRAINT chk_refund_method CHECK (
                    refund_method IN ('cash', 'check', 'card', 'transfer', 'zelle', 'other')
                )
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_refund_events_payment "
            "ON refund_events(payment_id, created_at DESC)"
        )
    )
