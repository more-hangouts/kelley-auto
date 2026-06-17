from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 1 of the invoice/quote discount + payment-term refactor.
    #
    # `discount_presets` is the source of truth for the dropdown that
    # appears on the quote and invoice editors. Stored as JSONB so the
    # business can add/remove presets without a schema migration. Each
    # entry is `{id, label, percent, active}`. Service layer caps the
    # list at 12 entries and percent at 0-50.
    #
    # `default_payment_plan_count` and `default_deposit_percent` are the
    # defaults the plan selector seeds itself from when staff create a
    # new quote or invoice. Both nullable so the UI can fall back to a
    # baked-in 2-payment / 50% default when the business has not
    # configured anything.
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD COLUMN discount_presets             JSONB NOT NULL DEFAULT '[]'::jsonb,
                ADD COLUMN default_payment_plan_count   SMALLINT,
                ADD COLUMN default_deposit_percent      NUMERIC(5,2),

                ADD CONSTRAINT chk_default_payment_plan_count CHECK (
                    default_payment_plan_count IS NULL
                    OR default_payment_plan_count IN (1, 2, 3)
                ),
                ADD CONSTRAINT chk_default_deposit_percent CHECK (
                    default_deposit_percent IS NULL
                    OR (default_deposit_percent >= 50 AND default_deposit_percent <= 100)
                )
            """
        )
    )

    # Seed the singleton row with the three locked presets. The IDs are
    # stable strings (not generated UUIDs) so they read well in the DB
    # and snapshot cleanly onto records.
    connection.execute(
        text(
            """
            UPDATE business_profile
            SET discount_presets = :presets
            WHERE id = 1
            """
        ),
        {
            "presets": (
                '[{"id":"moonlight","label":"Moonlight Ballroom","percent":10,"active":true},'
                '{"id":"military","label":"Military","percent":5,"active":true},'
                '{"id":"same_day","label":"Same-day","percent":2,"active":true}]'
            ),
        },
    )

    # Validation: the business_profile table has a singleton CHECK
    # constraint, so the project rule about real INSERTs does not apply
    # here (a second INSERT cannot succeed). Round-trip the seed via
    # UPDATE to confirm the JSONB column accepts the shape we expect.
    row = connection.execute(
        text(
            "SELECT discount_presets FROM business_profile WHERE id = 1"
        )
    ).one()
    presets = row[0]
    assert isinstance(presets, list), "discount_presets must round-trip as a list"
    assert len(presets) == 3, "expected three seeded presets"
    ids = sorted(p["id"] for p in presets)
    assert ids == ["military", "moonlight", "same_day"], (
        f"unexpected preset ids: {ids}"
    )
