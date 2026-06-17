from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE contacts (
                id                  SERIAL PRIMARY KEY,
                first_name          VARCHAR(100),
                last_name           VARCHAR(100),
                display_name        VARCHAR(200) NOT NULL,
                email               VARCHAR(255),
                phone               VARCHAR(32),
                phone_e164          VARCHAR(20),
                address             JSONB NOT NULL DEFAULT '{}'::jsonb,
                tags                JSONB NOT NULL DEFAULT '[]'::jsonb,
                notes               TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # Identity lookup: phone_e164 is the canonical identity when present.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_contacts_phone_e164 "
            "ON contacts(phone_e164) WHERE phone_e164 IS NOT NULL"
        )
    )
    # Email is informational, not unique — parents and quinces often share inboxes.
    connection.execute(
        text(
            "CREATE INDEX idx_contacts_email_lower "
            "ON contacts(lower(email)) WHERE email IS NOT NULL"
        )
    )
    connection.execute(
        text("CREATE INDEX idx_contacts_created_at ON contacts(created_at DESC)")
    )

    # Link appointments at customer identity. Nullable because legacy rows
    # need backfill and the booking widget hasn't been updated yet to populate it.
    connection.execute(
        text(
            "ALTER TABLE appointments "
            "ADD COLUMN contact_id INTEGER "
            "REFERENCES contacts(id) ON DELETE SET NULL"
        )
    )
    connection.execute(
        text("CREATE INDEX idx_appointments_contact_id ON appointments(contact_id)")
    )

    # Backfill pass 1: dedup by phone_e164 (oldest appointment wins for name/email).
    connection.execute(
        text(
            """
            INSERT INTO contacts (
                first_name, last_name, display_name,
                email, phone, phone_e164, created_at, updated_at
            )
            SELECT DISTINCT ON (phone_e164)
                celebrant_first_name,
                celebrant_last_name,
                TRIM(celebrant_first_name || ' ' || COALESCE(celebrant_last_name, '')),
                email,
                phone,
                phone_e164,
                created_at,
                NOW()
            FROM appointments
            WHERE phone_e164 IS NOT NULL
            ORDER BY phone_e164, created_at ASC
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE appointments a
            SET contact_id = c.id
            FROM contacts c
            WHERE c.phone_e164 = a.phone_e164
              AND a.contact_id IS NULL
            """
        )
    )

    # Backfill pass 2: appointments without phone_e164 — dedup by lower(email),
    # but only if no existing contact already covers that email (avoid duplicates
    # for parent/quince who share the inbox but already got a phone-based contact).
    connection.execute(
        text(
            """
            INSERT INTO contacts (
                first_name, last_name, display_name,
                email, phone, phone_e164, created_at, updated_at
            )
            SELECT DISTINCT ON (lower(a.email))
                a.celebrant_first_name,
                a.celebrant_last_name,
                TRIM(a.celebrant_first_name || ' ' || COALESCE(a.celebrant_last_name, '')),
                a.email,
                a.phone,
                NULL,
                a.created_at,
                NOW()
            FROM appointments a
            WHERE a.phone_e164 IS NULL
              AND a.email IS NOT NULL
              AND a.contact_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM contacts c WHERE lower(c.email) = lower(a.email)
              )
            ORDER BY lower(a.email), a.created_at ASC
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE appointments a
            SET contact_id = c.id
            FROM contacts c
            WHERE a.contact_id IS NULL
              AND a.phone_e164 IS NULL
              AND a.email IS NOT NULL
              AND lower(c.email) = lower(a.email)
            """
        )
    )

    # Soft check: any appointment left without a contact_id?
    # We don't fail the migration — booking widget code will be updated next
    # to populate contact_id on insert; legacy orphans can be reconciled by hand.
    connection.execute(
        text(
            """
            DO $$
            DECLARE
                orphan_count integer;
            BEGIN
                SELECT COUNT(*) INTO orphan_count
                FROM appointments WHERE contact_id IS NULL;
                IF orphan_count > 0 THEN
                    RAISE WARNING
                        'After contact backfill, % appointments have no contact_id',
                        orphan_count;
                END IF;
            END $$
            """
        )
    )
