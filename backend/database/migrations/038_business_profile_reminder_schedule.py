from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 11. Reminder cadence settings live on the business_profile
    # singleton. Three reminder slots — most quince payment plans run
    # deposit + balance, so two reminders is the common case and a third
    # gives staff room to add a "30 days overdue" nudge with a late fee.
    #
    # `offset_basis` lets a reminder fire relative to:
    #   - 'before_due'  (e.g. 7 days before installment due)
    #   - 'after_due'   (e.g. 3 days overdue)
    #   - 'after_sent'  (e.g. 14 days after the invoice itself was sent
    #                   — useful when the schedule's first installment
    #                   is already overdue at send time)
    #
    # Late fee fires only on reminder3 firing. Either the flat
    # `reminder_late_fee_cents` OR `reminder_late_fee_pct` (of the
    # remaining unpaid balance) is used — whichever is non-zero. Both
    # zero means no fee.
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD COLUMN reminder1_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN reminder1_days_offset   INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN reminder1_offset_basis  VARCHAR(16) NOT NULL DEFAULT 'before_due',
                ADD COLUMN reminder2_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN reminder2_days_offset   INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN reminder2_offset_basis  VARCHAR(16) NOT NULL DEFAULT 'before_due',
                ADD COLUMN reminder3_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN reminder3_days_offset   INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN reminder3_offset_basis  VARCHAR(16) NOT NULL DEFAULT 'before_due',
                ADD COLUMN reminder_late_fee_cents BIGINT NOT NULL DEFAULT 0,
                ADD COLUMN reminder_late_fee_pct   NUMERIC(5,3) NOT NULL DEFAULT 0,

                ADD CONSTRAINT chk_reminder_offset_basis CHECK (
                    reminder1_offset_basis IN ('before_due', 'after_due', 'after_sent')
                AND reminder2_offset_basis IN ('before_due', 'after_due', 'after_sent')
                AND reminder3_offset_basis IN ('before_due', 'after_due', 'after_sent')
                ),
                ADD CONSTRAINT chk_reminder_late_fee_nonneg CHECK (
                    reminder_late_fee_cents >= 0
                AND reminder_late_fee_pct >= 0
                AND reminder_late_fee_pct < 1
                )
            """
        )
    )
