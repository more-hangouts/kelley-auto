from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_blackouts (
                id            SERIAL PRIMARY KEY,
                start_at      TIMESTAMPTZ NOT NULL,
                end_at        TIMESTAMPTZ NOT NULL,
                reason        VARCHAR(200),
                created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_blackouts_range CHECK (end_at > start_at)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_blackouts_range "
            "ON appointment_blackouts(start_at, end_at)"
        )
    )
