"""Phase C1: at-rest encryption for `integration_tokens` token columns.

Adds `access_token_ciphertext BYTEA` and `refresh_token_ciphertext BYTEA`
alongside the existing plaintext columns. New writes go to ciphertext;
the dual-read service layer in `services.integration_tokens` keeps the
plaintext columns readable as a transitional fallback. A follow-up
slice will null and drop the plaintext columns after one production
deploy cycle confirms the new write path is exercising cleanly.

Backfill: if any rows have plaintext but no ciphertext, encrypt them in
place using the configured `INTEGRATION_TOKEN_KEYS`. On the current
production database `integration_tokens` is empty, so the backfill is a
no-op — but the code is here so dev environments with seeded rows are
migrated atomically with the schema change.

Skipping the in-migration round-trip probe deliberately: the C1 smoke
test exercises a full encrypt → read → at-rest-bytes-check → rotation
flow in a properly isolated test row, which is a stronger acceptance
signal than a savepoint INSERT could be inside the migration.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE integration_tokens
                ADD COLUMN access_token_ciphertext  BYTEA NULL,
                ADD COLUMN refresh_token_ciphertext BYTEA NULL
            """
        )
    )

    needs_backfill = connection.execute(
        text(
            """
            SELECT COUNT(*) FROM integration_tokens
            WHERE (access_token  IS NOT NULL AND access_token_ciphertext  IS NULL)
               OR (refresh_token IS NOT NULL AND refresh_token_ciphertext IS NULL)
            """
        )
    ).scalar()

    if not needs_backfill:
        return

    # Importing the service module lazily keeps the migration runnable on
    # empty-table installs that haven't generated a Fernet key yet. If we
    # do have rows to encrypt, the import will surface
    # IntegrationTokenCryptoUnconfigured if INTEGRATION_TOKEN_KEYS is
    # missing — which is the right safety: do not let the schema advance
    # past plaintext without a key committed to .env.
    from services.integration_tokens import encrypt  # noqa: PLC0415

    rows = connection.execute(
        text(
            """
            SELECT id, access_token, refresh_token
            FROM integration_tokens
            WHERE (access_token  IS NOT NULL AND access_token_ciphertext  IS NULL)
               OR (refresh_token IS NOT NULL AND refresh_token_ciphertext IS NULL)
            """
        )
    ).all()

    for row in rows:
        access_ct = encrypt(row.access_token) if row.access_token else None
        refresh_ct = encrypt(row.refresh_token) if row.refresh_token else None
        connection.execute(
            text(
                """
                UPDATE integration_tokens
                   SET access_token_ciphertext  = COALESCE(:access_ct,  access_token_ciphertext),
                       refresh_token_ciphertext = COALESCE(:refresh_ct, refresh_token_ciphertext)
                 WHERE id = :id
                """
            ),
            {"id": row.id, "access_ct": access_ct, "refresh_ct": refresh_ct},
        )
