from decimal import Decimal

from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 2a of the discount/payment-term refactor.
    #
    # Snapshot the order-level discount onto each invoice/quote so the
    # printed copy and the historical money math survive even if the
    # business edits or removes the source preset later. Three columns:
    #
    # - `discount_preset_id`  TEXT NULL
    #     The preset's id from `business_profile.discount_presets`.
    #     Not a foreign key because presets live inside a JSONB blob.
    #     NULL when the discount was a "Custom %" entry or absent.
    # - `discount_label`      TEXT NULL
    #     The label snapshotted at write time. Renaming a preset later
    #     does not change this. NULL means "no order discount applied".
    # - `discount_percent`    NUMERIC(5,2) NULL, CHECK 0..50
    #     The percent applied to the taxable subtotal. NULL means the
    #     legacy post-tax `discount_cents` path is in effect.
    #
    # `discount_cents` keeps its column shape. When `discount_percent IS
    # NOT NULL`, the totals service treats `discount_cents` as a derived
    # display value and recomputes it from the percent. When NULL,
    # `discount_cents` keeps its legacy meaning (absolute cents off the
    # post-tax total).
    for table in ("invoices", "quotes"):
        connection.execute(
            text(
                f"""
                ALTER TABLE {table}
                    ADD COLUMN discount_preset_id  TEXT,
                    ADD COLUMN discount_label      TEXT,
                    ADD COLUMN discount_percent    NUMERIC(5,2),
                    ADD CONSTRAINT chk_{table[:-1]}_discount_percent CHECK (
                        discount_percent IS NULL
                        OR (discount_percent >= 0 AND discount_percent <= 50)
                    )
                """
            )
        )

    # Confirm the constraints landed under the names the service layer
    # expects. Cheap belt-and-suspenders against typos in the DDL above.
    rows = connection.execute(
        text(
            """
            SELECT conname FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname IN ('invoices', 'quotes')
              AND conname IN (
                'chk_invoice_discount_percent',
                'chk_quote_discount_percent'
              )
            """
        )
    ).all()
    found = {r[0] for r in rows}
    expected = {"chk_invoice_discount_percent", "chk_quote_discount_percent"}
    assert found == expected, f"discount_percent CHECK missing: {expected - found}"

    # Real round-trip on each table: UPDATE one existing row inside a
    # savepoint, SELECT it back, then ROLLBACK so production data is not
    # touched. Skip cleanly when the table is empty (fresh installs).
    for table in ("invoices", "quotes"):
        has_row = connection.execute(
            text(f"SELECT 1 FROM {table} LIMIT 1")
        ).first()
        if not has_row:
            continue

        # Happy path: a 12.50% snapshot round-trips with full precision.
        sp = connection.begin_nested()
        try:
            connection.execute(
                text(
                    f"""
                    UPDATE {table} SET
                        discount_preset_id = 'probe',
                        discount_label     = 'Migration Probe',
                        discount_percent   = 12.50
                    WHERE id = (SELECT id FROM {table} LIMIT 1)
                    """
                )
            )
            row = connection.execute(
                text(
                    f"""
                    SELECT discount_preset_id, discount_label, discount_percent
                    FROM {table}
                    WHERE discount_preset_id = 'probe'
                    LIMIT 1
                    """
                )
            ).first()
            assert row is not None, f"{table} probe row not visible"
            assert row[0] == "probe", row
            assert row[1] == "Migration Probe", row
            assert row[2] == Decimal("12.50"), row
        finally:
            sp.rollback()

        # CHECK constraint enforces the 0..50 range.
        sp = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        f"UPDATE {table} SET discount_percent = 60 "
                        f"WHERE id = (SELECT id FROM {table} LIMIT 1)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    f"{table}.discount_percent CHECK did not reject 60"
                )
        finally:
            sp.rollback()
