from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 6 follow-up: `partially_refunded` is 18 characters but the
    # original migration 030 declared `status VARCHAR(16)`. The
    # chk_payment_status CHECK accepts the literal but the column itself
    # truncates. Widen it.
    connection.execute(
        text(
            "ALTER TABLE payments "
            "ALTER COLUMN status TYPE VARCHAR(24)"
        )
    )
