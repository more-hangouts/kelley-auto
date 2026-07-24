"""G1: explicit revocation column for booking self-service tokens.

Adds `appointments.tokens_invalidated_at` (TIMESTAMPTZ, NULL = never
invalidated). The G1 token verifier compares the token's `iat` claim
against this column; any token minted before the row's
`tokens_invalidated_at` is rejected as if expired.

Cancellation and reschedule (the two operations that should obsolete
prior self-service links) bump this column to `NOW()` so the old
emailed reschedule/cancel/enrichment links no longer work. New tokens
minted after the bump pass because their `iat` is later.

The column is NULL on every existing row at migration time — that's
the correct initial state, meaning "no revocation event yet, every
unexpired token is still valid." No backfill needed.

Pair this migration with `services/booking_tokens.py` changes that
shorten the default TTLs (60d → 30d for reschedule/cancel, 30d → 14d
for enrichment) and cap by the appointment's `slot_start_at` so a
token can never outlive the appointment it points at.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE appointments
                ADD COLUMN IF NOT EXISTS tokens_invalidated_at TIMESTAMPTZ
            """
        )
    )

    # Comment is for the DBA; query plans don't care, but a future
    # operator reading \d+ appointments will see why it exists.
    connection.execute(
        text(
            """
            COMMENT ON COLUMN appointments.tokens_invalidated_at IS
            'G1: timestamp of the last token-invalidation event for this row '
            '(cancel/reschedule). NULL means no invalidation yet. The booking '
            'token verifier rejects any token with iat < this value.'
            """
        )
    )


def downgrade(connection) -> None:
    connection.execute(
        text("ALTER TABLE appointments DROP COLUMN IF EXISTS tokens_invalidated_at")
    )
