from sqlalchemy import text


def upgrade(connection) -> None:
    # Canonical payments table. Money state is split four ways on every
    # row: amount_cents (gross received) = applied_cents + refunded_cents
    # + unapplied_cents. The invariant CHECK below enforces this; the
    # service layer is responsible for writing all four columns in lock-
    # step on every mutation. No triggers, by spec — recomputation lives
    # in `services/payment_service._recompute_payment_totals`.
    #
    # Refunds claw back from this same row by bumping refunded_cents and
    # the corresponding payment_allocations.refunded_cents. There is no
    # negative-amount payment row anywhere in the schema; chk_payment_
    # amount_pos forbids it absolutely.
    connection.execute(
        text(
            """
            CREATE TABLE payments (
                id                       SERIAL PRIMARY KEY,
                contact_id               INTEGER NOT NULL
                                         REFERENCES contacts(id) ON DELETE RESTRICT,
                payment_number           VARCHAR(32) UNIQUE,
                amount_cents             BIGINT NOT NULL,
                applied_cents            BIGINT NOT NULL DEFAULT 0,
                unapplied_cents          BIGINT NOT NULL DEFAULT 0,
                refunded_cents           BIGINT NOT NULL DEFAULT 0,
                payment_date             DATE NOT NULL DEFAULT CURRENT_DATE,
                method                   VARCHAR(20) NOT NULL,
                transaction_reference    VARCHAR(120),
                status                   VARCHAR(16) NOT NULL DEFAULT 'completed',
                notes                    TEXT,
                created_by_user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
                deleted_at               TIMESTAMPTZ,
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_payment_method CHECK (
                    method IN ('cash', 'check', 'card', 'transfer', 'zelle', 'other')
                ),
                CONSTRAINT chk_payment_status CHECK (
                    status IN ('pending', 'completed', 'partially_refunded',
                               'refunded', 'failed', 'cancelled')
                ),
                -- Money floors. amount > 0 absolutely; the rest are
                -- nonneg-or-zero. The service maintains the invariant
                -- amount = applied + refunded + unapplied at all times.
                CONSTRAINT chk_payment_amount_pos CHECK (amount_cents > 0),
                CONSTRAINT chk_payment_applied_nonneg CHECK (applied_cents >= 0),
                CONSTRAINT chk_payment_refunded_nonneg CHECK (refunded_cents >= 0),
                CONSTRAINT chk_payment_unapplied_nonneg CHECK (unapplied_cents >= 0),
                CONSTRAINT chk_payment_refunded_le_amount CHECK (
                    refunded_cents <= amount_cents
                ),
                -- THE invariant. Every INSERT / UPDATE leaves this true.
                -- Defense in depth against a service bug that forgets one
                -- of the three derived columns.
                CONSTRAINT chk_payment_amount_consistent CHECK (
                    amount_cents = applied_cents + refunded_cents + unapplied_cents
                ),
                CONSTRAINT chk_payment_number_when_not_pending CHECK (
                    status = 'pending' OR payment_number IS NOT NULL
                )
            )
            """
        )
    )

    connection.execute(
        text(
            "CREATE INDEX idx_payments_contact_date "
            "ON payments(contact_id, payment_date DESC) "
            "WHERE deleted_at IS NULL"
        )
    )
    # Powers the AR rollup variant: outstanding-by-contact reads the
    # payment side of the ledger without scanning every row.
    connection.execute(
        text(
            "CREATE INDEX idx_payments_status "
            "ON payments(status) "
            "WHERE deleted_at IS NULL"
        )
    )
