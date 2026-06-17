from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_session_events (
                id              BIGSERIAL PRIMARY KEY,
                visitor_id      UUID,
                session_id      VARCHAR(64),
                event_id        VARCHAR(64),
                event_name      VARCHAR(50) NOT NULL,
                step            VARCHAR(50),
                appointment_id  INTEGER REFERENCES appointments(id) ON DELETE SET NULL,
                payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
                page_url        TEXT,
                referrer_url    TEXT,
                user_agent      TEXT,
                ip_hash         VARCHAR(64),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_session_events_visitor_created "
            "ON appointment_session_events(visitor_id, created_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_session_events_session_created "
            "ON appointment_session_events(session_id, created_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_session_events_event_name_created "
            "ON appointment_session_events(event_name, created_at DESC)"
        )
    )
    # Idempotency for the abandon beacon and any other dedup-worthy event.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX idx_session_events_event_id "
            "ON appointment_session_events(event_id) "
            "WHERE event_id IS NOT NULL"
        )
    )
