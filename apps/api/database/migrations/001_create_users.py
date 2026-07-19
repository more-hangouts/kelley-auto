from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE users (
                id              SERIAL PRIMARY KEY,
                username        VARCHAR(100) UNIQUE NOT NULL,
                email           VARCHAR(255) UNIQUE NOT NULL,
                hashed_password VARCHAR(255) NOT NULL,
                full_name       VARCHAR(200),
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                role            VARCHAR(20) NOT NULL DEFAULT 'user',
                permissions     JSONB NOT NULL DEFAULT '[]'::jsonb,
                token_version   INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_login      TIMESTAMPTZ
            )
            """
        )
    )
    connection.execute(text("CREATE INDEX idx_users_email ON users(email)"))
    connection.execute(text("CREATE INDEX idx_users_username ON users(username)"))
