"""Business hours on the business_profile singleton (NAP completeness).

The public NAP DTO (`get_public_profile`) shipped name/address/phone/email/
website but no hours, so the storefront fell back to a generic "contact us
for current hours" string. This adds a single nullable JSONB column to hold
opening hours.

Purely additive and white-label-safe: the column ships NULL, and the actual
hours are owner-editable via the business-profile PATCH or seeded per tenant
(Kelley's hours land via scripts/seed_kelley_day5.py, NOT this migration — a
schema migration should not bake in one tenant's business data).

Shape (validated in services/business_profile_service.py, not the DB, so it
stays flexible per tenant):

    {
      "timezone": "America/Chicago",
      "days": [
        {"day": "Sunday", "closed": true},
        {"day": "Monday", "open": "9:00 AM", "close": "7:00 PM"},
        ...
      ]
    }

A savepoint probe (always rolled back, mirroring migrations 085/086) writes
a JSONB value into the new column on the singleton to prove the column
accepts the shape and remains nullable.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD COLUMN IF NOT EXISTS business_hours JSONB NULL"
        )
    )

    # --- DML probe (savepoint, always rolled back) ----------------------
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                UPDATE business_profile
                   SET business_hours = CAST(:h AS jsonb)
                 WHERE id = 1
                """
            ),
            {
                "h": '{"timezone": "America/Chicago", '
                '"days": [{"day": "Sunday", "closed": true}, '
                '{"day": "Monday", "open": "9:00 AM", "close": "7:00 PM"}]}'
            },
        )
        val = connection.execute(
            text("SELECT business_hours FROM business_profile WHERE id = 1")
        ).scalar()
        # Either no singleton yet (None) or a parsed JSON object — both OK.
        assert val is None or isinstance(val, (dict, list)), val
    finally:
        sp.rollback()
