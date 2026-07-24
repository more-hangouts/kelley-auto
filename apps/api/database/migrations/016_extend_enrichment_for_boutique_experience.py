from sqlalchemy import text


def upgrade(connection) -> None:
    # The Boutique Experience profile is a superset of the existing survey:
    # measurements, computed sizing range, calculator-style preferences, and
    # a rendered staff-display summary. One row per appointment still holds.
    # Pre-booking submissions need to live without an appointment for a moment,
    # so appointment_id becomes nullable. Postgres treats NULLs as distinct
    # under UNIQUE, so multiple unlinked profiles can coexist on the same
    # index without further changes.
    connection.execute(
        text(
            """
            ALTER TABLE appointment_enrichment_responses
                ALTER COLUMN appointment_id DROP NOT NULL
            """
        )
    )

    connection.execute(
        text(
            """
            ALTER TABLE appointment_enrichment_responses
                ADD COLUMN visitor_id              UUID,
                ADD COLUMN session_id              VARCHAR(64),
                ADD COLUMN source                  VARCHAR(32),

                ADD COLUMN bust_inches             NUMERIC(4,1),
                ADD COLUMN waist_inches            NUMERIC(4,1),
                ADD COLUMN hips_inches             NUMERIC(4,1),
                ADD COLUMN height_ft               SMALLINT,
                ADD COLUMN height_in               SMALLINT,

                ADD COLUMN estimated_size_low      SMALLINT,
                ADD COLUMN estimated_size_high     SMALLINT,
                ADD COLUMN size_by_bust            SMALLINT,
                ADD COLUMN size_by_waist           SMALLINT,
                ADD COLUMN size_by_hips            SMALLINT,
                ADD COLUMN chart_source            VARCHAR(120),
                ADD COLUMN off_chart               BOOLEAN,

                ADD COLUMN style_preference        VARCHAR(40),
                ADD COLUMN back_preference         VARCHAR(40),
                ADD COLUMN budget_preference       VARCHAR(40),
                ADD COLUMN color_preferences_text  TEXT,
                ADD COLUMN likes                   TEXT,
                ADD COLUMN avoids                  TEXT,

                ADD COLUMN summary                 TEXT
            """
        )
    )

    # Source guardrail. NULL is allowed for legacy rows that predate the
    # extended schema; new writes are required to set one of these values
    # at the application layer.
    connection.execute(
        text(
            """
            ALTER TABLE appointment_enrichment_responses
            ADD CONSTRAINT chk_aer_source
            CHECK (source IS NULL OR source IN (
                'pre_booking',
                'post_booking_email',
                'manual_attach',
                'enrichment_survey',
                'legacy_note'
            ))
            """
        )
    )

    # Visitor lookup so we can find a visitor's pre-booking profile when they
    # come back to book without re-entering measurements.
    connection.execute(
        text(
            "CREATE INDEX idx_aer_visitor_id "
            "ON appointment_enrichment_responses(visitor_id) "
            "WHERE visitor_id IS NOT NULL"
        )
    )
