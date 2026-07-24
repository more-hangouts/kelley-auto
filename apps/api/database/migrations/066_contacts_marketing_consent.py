"""Marketing-consent timestamp on contacts.

Adds `contacts.marketing_consent_at` (TIMESTAMPTZ, NULL = no consent
recorded). Set to NOW() the first time a contact opts in to
promotional email via the booking widget's consent checkbox. Never
cleared by a later booking that leaves the checkbox unchecked —
withdrawing consent goes through unsubscribe, not the booking flow.

Existing rows at migration time are NULL, meaning "never opted in."
That is the only safe default: bulk-emailing existing contacts
without explicit consent is what this whole column exists to prevent.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE contacts
                ADD COLUMN IF NOT EXISTS marketing_consent_at TIMESTAMPTZ
            """
        )
    )

    connection.execute(
        text(
            """
            COMMENT ON COLUMN contacts.marketing_consent_at IS
            'Timestamp of the first time this contact opted in to '
            'promotional email (via the booking widget consent checkbox). '
            'NULL = no consent recorded. Never cleared by a later booking '
            'that leaves the checkbox unchecked; withdrawal flows through '
            'unsubscribe, not booking.'
            """
        )
    )


def downgrade(connection) -> None:
    connection.execute(
        text("ALTER TABLE contacts DROP COLUMN IF EXISTS marketing_consent_at")
    )
