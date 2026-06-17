from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE booking_widget_theme_settings (
                id           SMALLINT PRIMARY KEY DEFAULT 1,
                theme        JSONB NOT NULL DEFAULT '{}'::jsonb,
                copy         JSONB NOT NULL DEFAULT '{}'::jsonb,
                flow         JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_theme_singleton CHECK (id = 1)
            )
            """
        )
    )

    # Singleton row with the brand defaults.
    connection.execute(
        text(
            """
            INSERT INTO booking_widget_theme_settings (id, theme, copy, flow)
            VALUES (
                1,
                '{
                    "color_bg": "#FBF5EF",
                    "color_surface": "#FFFFFF",
                    "color_accent": "#A7616F",
                    "color_accent_dark": "#7E4451",
                    "color_text": "#2A1B1F",
                    "color_text_muted": "#7A6A6F",
                    "font_heading": "Playfair Display, serif",
                    "font_body": "Inter, system-ui, sans-serif",
                    "radius": "16px"
                }'::jsonb,
                '{
                    "header_brand": "Bellas XV",
                    "header_title": "Initial consultation",
                    "header_subtitle": "Meet with our stylists to discuss your vision and explore options for your special day.",
                    "step1_heading": "Pick a date and time",
                    "step2_heading": "Who is this for?",
                    "step2_celebrant_label": "Quinceanera''s name",
                    "step2_event_date_label": "Event date (if known)",
                    "step2_party_size_label": "Who''s coming with you?",
                    "step2_party_solo": "Just me",
                    "step2_party_2_3": "2-3 people",
                    "step2_party_4_plus": "4 or more",
                    "step3_heading": "How do we reach you?",
                    "step3_phone_label": "Phone number",
                    "step3_email_label": "Email",
                    "step3_note_label": "Anything you''d like us to know? (optional)",
                    "submit_label": "Confirm appointment",
                    "success_heading": "You''re booked.",
                    "success_subtitle": "We just emailed your confirmation. We can''t wait to meet you.",
                    "boutique_label": "Bella''s XV boutique",
                    "timezone_label": "America/Chicago"
                }'::jsonb,
                '{
                    "duration_options_minutes": [15, 30, 45, 60],
                    "default_duration_minutes": 45,
                    "max_days_ahead": 60,
                    "min_lead_time_minutes": 120
                }'::jsonb
            )
            """
        )
    )
