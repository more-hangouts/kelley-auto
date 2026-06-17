from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE integration_tokens (
                id                SERIAL PRIMARY KEY,
                provider          VARCHAR(50) UNIQUE NOT NULL,
                access_token      TEXT,
                refresh_token     TEXT,
                token_type        VARCHAR(20) DEFAULT 'Bearer',
                expires_at        TIMESTAMPTZ,
                owner_uri         VARCHAR(500),
                organization_uri  VARCHAR(500),
                metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text("CREATE INDEX idx_integration_tokens_provider ON integration_tokens(provider)")
    )
