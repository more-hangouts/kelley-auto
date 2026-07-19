"""Structured, encrypted BHPH lead-application PII (privacy remediation).

Kelley's buy-here-pay-here storefront form collects date of birth, driver's
license, and home address. Until now those landed as free text inside
``events.notes`` — plaintext, fetched/rendered/emailed with the deal, and
visible to every admin. This migration introduces a dedicated home for that
data so it can be isolated, encrypted at rest, permission-gated, and audited
independently of the deal record.

Everything here is additive — a brand-new table, no change to ``events`` or
any existing row/behavior:

  - ``lead_applications`` is 1:1 with a vehicle-sale ``events`` row
    (``event_id`` UNIQUE, ON DELETE CASCADE — the application dies with its
    deal). ``contact_id`` ON DELETE RESTRICT mirrors ``events`` (never
    orphan/erase applicant identity silently).
  - High-sensitivity fields are ``BYTEA`` ciphertext columns, written via
    ``services/lead_pii_crypto.py`` (Fernet/MultiFernet, key ``LEAD_PII_KEYS``):
    ``date_of_birth``, ``driver_license_number``, ``ssn`` (nullable, reserved
    for future underwriting), and ``address`` (encrypted JSON blob of
    street/city/state/zip).
  - Low-sensitivity workflow fields stay plaintext for filtering/display:
    ``driver_license_state`` (2-char), ``has_driver_license`` (bool).

No CHECK/DML probe block (unlike 086) — there is no constraint surface to
round-trip; the encrypted columns are opaque BYTEA.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE lead_applications (
                id                        SERIAL PRIMARY KEY,
                event_id                  INTEGER NOT NULL UNIQUE
                                            REFERENCES events(id) ON DELETE CASCADE,
                contact_id                INTEGER NOT NULL
                                            REFERENCES contacts(id) ON DELETE RESTRICT,
                -- Fernet ciphertext (services/lead_pii_crypto.py):
                date_of_birth_ciphertext          BYTEA,
                driver_license_number_ciphertext  BYTEA,
                ssn_ciphertext                    BYTEA,
                address_ciphertext                BYTEA,
                -- Low-sensitivity workflow fields, plaintext:
                driver_license_state      VARCHAR(2),
                has_driver_license        BOOLEAN,
                created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_lead_applications_contact_id "
            "ON lead_applications (contact_id)"
        )
    )
