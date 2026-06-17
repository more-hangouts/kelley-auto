from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE notification_jobs (
                id              BIGSERIAL PRIMARY KEY,
                kind            VARCHAR(64) NOT NULL,
                channel         VARCHAR(16) NOT NULL,
                appointment_id  INTEGER REFERENCES appointments(id) ON DELETE CASCADE,
                recipient       VARCHAR(320) NOT NULL,
                payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
                due_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                status          VARCHAR(16) NOT NULL DEFAULT 'pending',
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT,
                sent_at         TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_notification_jobs_channel
                  CHECK (channel IN ('email', 'sms')),
                CONSTRAINT chk_notification_jobs_status
                  CHECK (status IN ('pending', 'sent', 'failed', 'cancelled'))
            )
            """
        )
    )
    # Worker poll: due pending jobs ordered by due_at.
    connection.execute(
        text(
            "CREATE INDEX idx_notification_jobs_pending_due "
            "ON notification_jobs(status, due_at) "
            "WHERE status = 'pending'"
        )
    )
    # Lookup by appointment for reschedule/cancel cascades and admin inspection.
    connection.execute(
        text(
            "CREATE INDEX idx_notification_jobs_appointment "
            "ON notification_jobs(appointment_id, kind, status)"
        )
    )
