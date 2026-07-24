from sqlalchemy import text


def upgrade(connection) -> None:
    # Events: the CRM "deal" record. One per quinceañera (or wedding/prom later).
    # Status sequence is duplicated as a CHECK constraint here AND in
    # services/event_workflow.py — keep them in sync when adding statuses.
    connection.execute(
        text(
            """
            CREATE TABLE events (
                id                  SERIAL PRIMARY KEY,
                primary_contact_id  INTEGER NOT NULL
                                    REFERENCES contacts(id) ON DELETE RESTRICT,
                event_type          VARCHAR(32) NOT NULL,
                event_name          VARCHAR(200) NOT NULL,
                event_date          DATE,
                court_size          INTEGER,
                quince_theme        VARCHAR(200),
                quince_theme_colors JSONB NOT NULL DEFAULT '[]'::jsonb,
                budget_range        VARCHAR(50),
                status              VARCHAR(32) NOT NULL DEFAULT 'lead',
                status_changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                owner_user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
                notes               TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_events_event_type CHECK (
                    event_type IN ('quinceanera')
                ),
                CONSTRAINT chk_events_status CHECK (
                    status IN (
                        'lead', 'consulted', 'sold', 'on_order', 'arrived',
                        'in_alterations', 'ready_for_pickup',
                        'picked_up', 'cancelled'
                    )
                )
            )
            """
        )
    )
    connection.execute(
        text("CREATE INDEX idx_events_status ON events(status)")
    )
    connection.execute(
        text(
            "CREATE INDEX idx_events_status_changed_at "
            "ON events(status_changed_at DESC)"
        )
    )
    connection.execute(
        text("CREATE INDEX idx_events_event_date ON events(event_date)")
    )
    connection.execute(
        text(
            "CREATE INDEX idx_events_primary_contact "
            "ON events(primary_contact_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_events_owner_user_id "
            "ON events(owner_user_id) WHERE owner_user_id IS NOT NULL"
        )
    )

    # Event participants: the quinceañera plus optional court members and
    # parents. contact_id is nullable because most chambelanes/damas never
    # become real contacts in their own right.
    connection.execute(
        text(
            """
            CREATE TABLE event_participants (
                id              SERIAL PRIMARY KEY,
                event_id        INTEGER NOT NULL
                                REFERENCES events(id) ON DELETE CASCADE,
                contact_id      INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                role            VARCHAR(32) NOT NULL,
                display_name    VARCHAR(200) NOT NULL,
                phone           VARCHAR(32),
                email           VARCHAR(255),
                measurements    JSONB NOT NULL DEFAULT '{}'::jsonb,
                status          VARCHAR(20) NOT NULL DEFAULT 'active',
                notes           TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_event_participants_role CHECK (role IN (
                    'quinceanera', 'dama', 'chambelan', 'parent', 'other'
                )),
                CONSTRAINT chk_event_participants_status CHECK (status IN (
                    'active', 'removed'
                ))
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_participants_event "
            "ON event_participants(event_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_participants_contact "
            "ON event_participants(contact_id) WHERE contact_id IS NOT NULL"
        )
    )
    # Invariant: exactly one quinceañera per event (allows historical
    # 'removed' rows so we don't lose data when re-pointing).
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_event_participants_quinceanera_per_event "
            "ON event_participants(event_id) "
            "WHERE role = 'quinceanera' AND status = 'active'"
        )
    )

    # Status change audit. Powers timeline UI and at-risk reporting.
    connection.execute(
        text(
            """
            CREATE TABLE event_status_change_events (
                id                  BIGSERIAL PRIMARY KEY,
                event_id            INTEGER NOT NULL
                                    REFERENCES events(id) ON DELETE CASCADE,
                from_status         VARCHAR(32),
                to_status           VARCHAR(32) NOT NULL,
                changed_by_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
                changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                notes               TEXT
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_status_changes_event "
            "ON event_status_change_events(event_id, changed_at DESC)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_status_changes_changed_at "
            "ON event_status_change_events(changed_at DESC)"
        )
    )

    # Link an appointment to its CRM event. Named crm_event_id because
    # appointments.event_id already exists as a booking-analytics dedup key
    # (VARCHAR string from the widget) — different concept.
    connection.execute(
        text(
            "ALTER TABLE appointments "
            "ADD COLUMN crm_event_id INTEGER "
            "REFERENCES events(id) ON DELETE SET NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_appointments_crm_event_id "
            "ON appointments(crm_event_id) WHERE crm_event_id IS NOT NULL"
        )
    )
