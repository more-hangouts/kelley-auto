from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE webhook_events (
                id            SERIAL PRIMARY KEY,
                source        VARCHAR(50) NOT NULL,
                event_type    VARCHAR(100) NOT NULL,
                external_id   VARCHAR(200),
                payload       JSONB NOT NULL,
                headers       JSONB,
                processed     BOOLEAN NOT NULL DEFAULT FALSE,
                processed_at  TIMESTAMPTZ,
                error_message TEXT,
                retry_count   INTEGER NOT NULL DEFAULT 0,
                received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_webhook_events_source_processed "
            "ON webhook_events(source, processed)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_webhook_events_received_at "
            "ON webhook_events(received_at DESC)"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX idx_webhook_events_dedup "
            "ON webhook_events(source, external_id) "
            "WHERE external_id IS NOT NULL"
        )
    )
