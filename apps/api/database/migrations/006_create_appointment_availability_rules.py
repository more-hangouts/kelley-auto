from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_availability_rules (
                id                      SERIAL PRIMARY KEY,
                weekday                 SMALLINT NOT NULL,
                start_time              TIME NOT NULL,
                end_time                TIME NOT NULL,
                slot_duration_minutes   INTEGER NOT NULL DEFAULT 45,
                capacity                INTEGER NOT NULL DEFAULT 1,
                effective_from          DATE,
                effective_to            DATE,
                active                  BOOLEAN NOT NULL DEFAULT TRUE,
                label                   VARCHAR(100),
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_availability_weekday CHECK (weekday BETWEEN 0 AND 6),
                CONSTRAINT chk_availability_time_range CHECK (end_time > start_time),
                CONSTRAINT chk_availability_slot_duration CHECK (slot_duration_minutes > 0),
                CONSTRAINT chk_availability_capacity CHECK (capacity > 0)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_availability_rules_weekday_active "
            "ON appointment_availability_rules(weekday, active)"
        )
    )

    # Seed the public hours from the marketing site.
    # weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    seed_rows = [
        (2, "12:00", "19:00", "Wednesday public hours"),
        (3, "12:00", "19:00", "Thursday public hours"),
        (4, "12:00", "17:00", "Friday public hours"),
        (5, "11:00", "17:00", "Saturday public hours"),
        (6, "12:00", "17:00", "Sunday public hours"),
    ]
    for weekday, start, end, label in seed_rows:
        connection.execute(
            text(
                """
                INSERT INTO appointment_availability_rules
                    (weekday, start_time, end_time, slot_duration_minutes, capacity, label)
                VALUES (:weekday, :start, :end, 45, 1, :label)
                """
            ),
            {"weekday": weekday, "start": start, "end": end, "label": label},
        )
