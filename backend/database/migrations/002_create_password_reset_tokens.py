from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE password_reset_tokens (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash  VARCHAR(64) UNIQUE NOT NULL,
                expires_at  TIMESTAMPTZ NOT NULL,
                used_at     TIMESTAMPTZ,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text("CREATE INDEX idx_password_reset_tokens_user_id ON password_reset_tokens(user_id)")
    )
    connection.execute(
        text(
            "CREATE INDEX idx_password_reset_tokens_expires_at ON password_reset_tokens(expires_at)"
        )
    )
