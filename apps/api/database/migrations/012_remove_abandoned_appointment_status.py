from sqlalchemy import text


def upgrade(connection) -> None:
    # Abandon telemetry lives in appointment_session_events, not appointments.
    # The appointments table requires slot + contact fields, so a partial
    # abandoned session can't legally exist as an appointment row. Drop the
    # 'abandoned' status value to make the schema honest about that.
    connection.execute(
        text("ALTER TABLE appointments DROP CONSTRAINT chk_appointments_status")
    )
    connection.execute(
        text(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT chk_appointments_status
            CHECK (status IN (
                'pending', 'confirmed', 'attended', 'no_show',
                'cancelled', 'rescheduled'
            ))
            """
        )
    )
