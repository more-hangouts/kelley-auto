from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_visitors (
                visitor_id              UUID PRIMARY KEY,
                first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                first_touch_attribution JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_touch_attribution  JSONB NOT NULL DEFAULT '{}'::jsonb,
                session_count           INTEGER NOT NULL DEFAULT 1,
                booked_at               TIMESTAMPTZ
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_visitors_last_seen_at "
            "ON appointment_visitors(last_seen_at DESC)"
        )
    )
