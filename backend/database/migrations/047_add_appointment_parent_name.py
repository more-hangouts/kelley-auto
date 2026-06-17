from sqlalchemy import text


def upgrade(connection) -> None:
    # The booking widget now captures the booking parent's first and last
    # name as the contact identity, while the celebrant field carries only
    # the quinceañera's first name. Historical rows pre-date the parent
    # capture, so the new columns are nullable; the API contract enforces
    # them on new submissions.
    connection.execute(
        text("ALTER TABLE appointments ADD COLUMN parent_first_name VARCHAR(100)")
    )
    connection.execute(
        text("ALTER TABLE appointments ADD COLUMN parent_last_name VARCHAR(100)")
    )

    # Party size buckets shifted with the new flow: a party of 2 is now its
    # own bucket ("Me and my quinceañera") rather than rolling up into
    # "2-3 people", and the upper buckets shifted accordingly. Old values
    # remain valid so historical rows render and the reschedule path can
    # carry their bucket forward unchanged.
    connection.execute(
        text(
            "ALTER TABLE appointments DROP CONSTRAINT chk_appointments_party_size"
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT chk_appointments_party_size
            CHECK (party_size_bucket IN (
                'solo', '2_3', '4_plus',
                'pair', '3_4', '5_plus'
            ))
            """
        )
    )
