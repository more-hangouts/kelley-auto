from sqlalchemy import text


def upgrade(connection) -> None:
    # Payment allocations: one row per (payment, invoice) link. The
    # applied_cents column is the slice of the payment applied to the
    # invoice. refunded_cents tracks the per-allocation refund slice; the
    # invoice's effective payment from this allocation is
    # `applied - refunded`.
    #
    # ON DELETE RESTRICT on invoice_id: invoices outlive payments for AR
    # audit. A canonical invoice cannot be deleted while any payment
    # touches it; staff must cancel/reverse the invoice instead.
    #
    # ON DELETE CASCADE on payment_id: a soft-deleted payment sweeps its
    # allocations with it. The service blocks hard-delete of completed
    # payments, but CASCADE is the right safety net for the unlikely
    # admin-script direct DELETE case.
    connection.execute(
        text(
            """
            CREATE TABLE payment_allocations (
                id              SERIAL PRIMARY KEY,
                payment_id      INTEGER NOT NULL
                                REFERENCES payments(id) ON DELETE CASCADE,
                invoice_id      INTEGER NOT NULL
                                REFERENCES invoices(id) ON DELETE RESTRICT,
                applied_cents   BIGINT NOT NULL,
                refunded_cents  BIGINT NOT NULL DEFAULT 0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_alloc_applied_pos CHECK (applied_cents > 0),
                CONSTRAINT chk_alloc_refunded_nonneg CHECK (refunded_cents >= 0),
                CONSTRAINT chk_alloc_refunded_le_applied CHECK (
                    refunded_cents <= applied_cents
                ),
                CONSTRAINT uq_payment_alloc_payment_invoice UNIQUE (payment_id, invoice_id)
            )
            """
        )
    )
    # Powers the invoice → its-payments lookup that the editor's
    # "Payments" sub-section runs on every render.
    connection.execute(
        text(
            "CREATE INDEX idx_payment_allocations_invoice "
            "ON payment_allocations(invoice_id)"
        )
    )
