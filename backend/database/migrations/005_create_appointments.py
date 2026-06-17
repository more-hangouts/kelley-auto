from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointments (
                id                          SERIAL PRIMARY KEY,
                confirmation_code           VARCHAR(20) UNIQUE NOT NULL,

                -- Slot
                slot_start_at               TIMESTAMPTZ NOT NULL,
                slot_end_at                 TIMESTAMPTZ NOT NULL,
                slot_duration_minutes       INTEGER NOT NULL,
                timezone                    VARCHAR(64) NOT NULL,

                -- Customer (six-field minimal booking)
                celebrant_first_name        VARCHAR(100) NOT NULL,
                celebrant_last_name         VARCHAR(100),
                event_date                  DATE,
                party_size_bucket           VARCHAR(20) NOT NULL,
                phone                       VARCHAR(32) NOT NULL,
                phone_e164                  VARCHAR(20),
                email                       VARCHAR(255) NOT NULL,
                customer_note               TEXT,

                -- Workflow
                status                      VARCHAR(20) NOT NULL DEFAULT 'confirmed',
                assigned_user_id            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                internal_notes              TEXT,
                cancelled_at                TIMESTAMPTZ,
                cancellation_reason         TEXT,
                rescheduled_from_id         INTEGER REFERENCES appointments(id) ON DELETE SET NULL,

                -- Conversion-quality (pushed back to ad platforms)
                attended_at                 TIMESTAMPTZ,
                no_show_at                  TIMESTAMPTZ,
                purchase_at                 TIMESTAMPTZ,
                purchase_value_cents        INTEGER,

                -- Source / attribution (the visitor came from somewhere)
                visitor_id                  UUID,
                session_id                  VARCHAR(64),
                event_id                    VARCHAR(64) UNIQUE,
                page_url                    TEXT,
                referrer_url                TEXT,
                utm_source                  VARCHAR(255),
                utm_medium                  VARCHAR(255),
                utm_campaign                VARCHAR(255),
                utm_content                 VARCHAR(255),
                utm_term                    VARCHAR(255),
                utm_id                      VARCHAR(255),
                fbclid                      VARCHAR(500),
                gclid                       VARCHAR(500),
                msclkid                     VARCHAR(500),
                fbp_cookie                  VARCHAR(255),
                fbc_cookie                  VARCHAR(500),

                -- Device
                device_type                 VARCHAR(20),
                user_agent                  TEXT,
                screen                      VARCHAR(32),
                viewport                    VARCHAR(32),
                browser_language            VARCHAR(32),
                platform                    VARCHAR(64),
                browser_timezone            VARCHAR(64),

                -- UX / behavior
                time_on_widget_ms           INTEGER,
                interaction_count           INTEGER,
                steps_completed             INTEGER,
                user_journey                JSONB NOT NULL DEFAULT '[]'::jsonb,
                behavior_score              INTEGER,
                bot_suspected               BOOLEAN NOT NULL DEFAULT FALSE,

                -- Integration sync state
                meta_capi_event_id          VARCHAR(128),
                meta_capi_synced_at         TIMESTAMPTZ,
                google_enhanced_synced_at   TIMESTAMPTZ,
                conversion_value_synced_at  TIMESTAMPTZ,

                -- Raw payload for production debugging
                raw_payload                 JSONB NOT NULL DEFAULT '{}'::jsonb,

                created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # Slot lookup for the public availability query and admin calendar.
    connection.execute(
        text("CREATE INDEX idx_appointments_slot_start_at ON appointments(slot_start_at)")
    )
    # Filter live bookings out of availability without scanning cancelled rows.
    connection.execute(
        text(
            "CREATE INDEX idx_appointments_status_slot "
            "ON appointments(status, slot_start_at)"
        )
    )
    # Admin search/filter.
    connection.execute(
        text("CREATE INDEX idx_appointments_email ON appointments(email)")
    )
    connection.execute(
        text("CREATE INDEX idx_appointments_phone_e164 ON appointments(phone_e164)")
    )
    connection.execute(
        text("CREATE INDEX idx_appointments_visitor_id ON appointments(visitor_id)")
    )
    connection.execute(
        text("CREATE INDEX idx_appointments_utm_source ON appointments(utm_source)")
    )
    connection.execute(
        text(
            "CREATE INDEX idx_appointments_utm_campaign "
            "ON appointments(utm_campaign)"
        )
    )
    # Newest-first listing.
    connection.execute(
        text("CREATE INDEX idx_appointments_created_at ON appointments(created_at DESC)")
    )

    # status guardrail
    connection.execute(
        text(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT chk_appointments_status
            CHECK (status IN (
                'pending', 'confirmed', 'attended', 'no_show',
                'cancelled', 'rescheduled', 'abandoned'
            ))
            """
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT chk_appointments_party_size
            CHECK (party_size_bucket IN ('solo', '2_3', '4_plus'))
            """
        )
    )
