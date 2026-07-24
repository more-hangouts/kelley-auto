"""SMS consent capture on contacts (Omnichannel Inbox Plan Part 7).

A2P 10DLC / TCPA: express written consent must be RECORDED, not just asked
for. The storefront forms show an optional (never pre-checked, never
required) consent checkbox; when checked, the public lead endpoint stamps
these columns on the contact. Outbound SMS (Phase 3) must check
``sms_consent_at IS NOT NULL AND sms_opted_out_at IS NULL`` before sending.

Kept separate from ``marketing_consent_at`` (email marketing) and from the
opt-out pair added in migration 094 — consent and revocation are distinct
events with distinct sources.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE contacts
                ADD COLUMN IF NOT EXISTS sms_consent_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS sms_consent_source TEXT
            """
        )
    )
