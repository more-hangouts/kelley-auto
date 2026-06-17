from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_enrichment_responses (
                id                       SERIAL PRIMARY KEY,
                appointment_id           INTEGER NOT NULL UNIQUE
                                          REFERENCES appointments(id) ON DELETE CASCADE,
                dress_styles             JSONB NOT NULL DEFAULT '[]'::jsonb,
                colors                   JSONB NOT NULL DEFAULT '[]'::jsonb,
                budget_range             VARCHAR(50),
                quince_theme             VARCHAR(200),
                quince_theme_colors      JSONB NOT NULL DEFAULT '[]'::jsonb,
                court_size               INTEGER,
                inspiration_photos       JSONB NOT NULL DEFAULT '[]'::jsonb,
                free_text                TEXT,
                opened_at                TIMESTAMPTZ,
                submitted_at             TIMESTAMPTZ,
                raw_payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
