"""Phase D1: widen `appointments.confirmation_code` + canonicalise existing rows.

Two changes:

  1. ALTER COLUMN TYPE to VARCHAR(32). The pre-D1 6-char body produced
     codes ~9 chars long; the D1 generator emits 22 chars (`BX` + 20
     canonical chars in a 31-symbol Crockford-ish alphabet, ≈99 bits of
     entropy). 32 gives headroom for any future widening without
     another lockstep migration.

  2. Backfill: strip every non-alphanumeric character and uppercase
     existing rows in place. Pre-D1 codes were stored with a single
     hyphen (`BX-ABCDEF`); after this migration they are
     `BXABCDEF` — matching the canonical shape new codes are written
     in. The lookup helpers (`booking_service.normalize_confirmation_code`)
     normalise input the same way, so customer-typed codes with or
     without hyphens still resolve to the right row.

The display layer prints hyphens at render time; storage stays
hyphen-free so the unique index, admin search, and direct equality
lookup all operate on one canonical shape.

Validation: a uniqueness probe runs before the column type change so a
canonicalisation that would COLLIDE two rows (unlikely with random
generation but worth catching) aborts the migration cleanly.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # Pre-flight: confirm canonicalisation does not create duplicates.
    dup = connection.execute(
        text(
            """
            SELECT UPPER(REGEXP_REPLACE(confirmation_code, '[^A-Za-z0-9]+', '', 'g')) AS canon,
                   COUNT(*) AS n
            FROM appointments
            GROUP BY 1
            HAVING COUNT(*) > 1
            """
        )
    ).all()
    assert not dup, (
        f"canonicalisation would collide on these codes: "
        f"{[(d.canon, d.n) for d in dup]}"
    )

    connection.execute(
        text(
            """
            ALTER TABLE appointments
                ALTER COLUMN confirmation_code TYPE VARCHAR(32)
            """
        )
    )

    connection.execute(
        text(
            """
            UPDATE appointments
               SET confirmation_code = UPPER(
                       REGEXP_REPLACE(confirmation_code, '[^A-Za-z0-9]+', '', 'g')
                   )
             WHERE confirmation_code ~ '[^A-Za-z0-9]'
                OR confirmation_code <> UPPER(confirmation_code)
            """
        )
    )

    # Post-condition: no row contains a hyphen / space / other separator,
    # everything is uppercase alphanumeric.
    remaining = connection.execute(
        text(
            "SELECT COUNT(*) FROM appointments "
            "WHERE confirmation_code ~ '[^A-Z0-9]'"
        )
    ).scalar() or 0
    assert remaining == 0, (
        f"{remaining} rows still contain non-canonical characters after backfill"
    )
