from decimal import Decimal

from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 7 of the discount/payment-term refactor — stacked order
    # discounts.
    #
    # Phase 2a snapshotted a single order-level discount onto invoices
    # and quotes via three columns (`discount_preset_id`,
    # `discount_label`, `discount_percent`). That model could not
    # represent two stacked discounts (e.g. Military 5% + Same-day 2%
    # combined into a single 7% off the taxable base).
    #
    # This migration replaces those three columns with two child tables:
    #
    #   invoice_order_discounts(invoice_id, sort_order, preset_id?,
    #                           label, percent)
    #   quote_order_discounts(quote_id, sort_order, preset_id?,
    #                         label, percent)
    #
    # Each row is one snapshotted discount. Multiple rows stack
    # additively — the totals service sums all percents and applies
    # `(1 - sum/100)` to each line's pre-order subtotal. Service-layer
    # validation enforces a 50% combined cap (matches the existing
    # fat-finger guard) and a per-row 0..50 range (DB CHECK).
    #
    # `discount_cents` keeps its column shape and meaning. When the
    # invoice/quote has at least one row in the order-discounts table,
    # `discount_cents` is a derived display value (sum of per-discount
    # savings). When zero rows exist and `discount_cents > 0`, the
    # legacy post-tax flat-amount math still applies — that protects
    # already-sent records from total drift.
    for parent, child in (
        ("invoices", "invoice_order_discounts"),
        ("quotes", "quote_order_discounts"),
    ):
        fk_col = f"{parent[:-1]}_id"
        connection.execute(
            text(
                f"""
                CREATE TABLE {child} (
                    id          BIGSERIAL PRIMARY KEY,
                    {fk_col}    INTEGER NOT NULL
                                REFERENCES {parent}(id) ON DELETE CASCADE,
                    sort_order  INTEGER NOT NULL DEFAULT 0,
                    preset_id   TEXT,
                    label       TEXT NOT NULL,
                    percent     NUMERIC(5,2) NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    CONSTRAINT chk_{child}_percent_range CHECK (
                        percent >= 0 AND percent <= 50
                    ),
                    CONSTRAINT chk_{child}_label_nonempty CHECK (
                        length(label) >= 1
                    )
                )
                """
            )
        )
        connection.execute(
            text(
                f"CREATE INDEX idx_{child}_parent_sort "
                f"ON {child}({fk_col}, sort_order)"
            )
        )

    # Backfill: existing records with a single Phase 2a discount
    # snapshot (`discount_percent IS NOT NULL`) become a single row in
    # the new child table. Records without a snapshot (legacy flat
    # `discount_cents` or no discount at all) skip the backfill — they
    # stay on the legacy code path.
    for parent, child in (
        ("invoices", "invoice_order_discounts"),
        ("quotes", "quote_order_discounts"),
    ):
        fk_col = f"{parent[:-1]}_id"
        # `discount_label` may have been NULL or blank on legacy
        # "Custom %" records — normalize both to "Custom" so the new
        # NOT NULL + non-empty constraints hold. preset_id stays NULL
        # for custom rows.
        connection.execute(
            text(
                f"""
                INSERT INTO {child}
                    ({fk_col}, sort_order, preset_id, label, percent)
                SELECT
                    id,
                    0,
                    discount_preset_id,
                    COALESCE(NULLIF(btrim(discount_label), ''), 'Custom'),
                    discount_percent
                FROM {parent}
                WHERE discount_percent IS NOT NULL
                """
            )
        )

    # Drop the now-redundant single-snapshot columns and their CHECK
    # constraints. The new model is canonical.
    for table in ("invoices", "quotes"):
        connection.execute(
            text(
                f"""
                ALTER TABLE {table}
                    DROP CONSTRAINT IF EXISTS chk_{table[:-1]}_discount_percent,
                    DROP COLUMN IF EXISTS discount_preset_id,
                    DROP COLUMN IF EXISTS discount_label,
                    DROP COLUMN IF EXISTS discount_percent
                """
            )
        )

    # Real DML probe per the project rule. Run on whatever parent rows
    # exist; skip cleanly on a fresh install.
    for parent, child in (
        ("invoices", "invoice_order_discounts"),
        ("quotes", "quote_order_discounts"),
    ):
        fk_col = f"{parent[:-1]}_id"
        parent_row = connection.execute(
            text(f"SELECT id FROM {parent} ORDER BY id LIMIT 1")
        ).first()
        if parent_row is None:
            continue

        parent_id = int(parent_row[0])

        # Happy path: two stacked rows, one preset and one custom.
        sp = connection.begin_nested()
        try:
            probe_label_one = f"__051_probe_{child}_one__"
            probe_label_two = f"__051_probe_{child}_two__"
            connection.execute(
                text(
                    f"""
                    INSERT INTO {child}
                        ({fk_col}, sort_order, preset_id, label, percent)
                    VALUES
                        (:pid, 0, 'military',  :label_one, 5.00),
                        (:pid, 1, NULL,        :label_two, 2.50)
                    """
                ),
                {
                    "pid": parent_id,
                    "label_one": probe_label_one,
                    "label_two": probe_label_two,
                },
            )
            rows = connection.execute(
                text(
                    f"SELECT sort_order, preset_id, label, percent "
                    f"FROM {child} WHERE {fk_col} = :pid "
                    f"AND label IN (:label_one, :label_two) "
                    f"ORDER BY sort_order"
                ),
                {
                    "pid": parent_id,
                    "label_one": probe_label_one,
                    "label_two": probe_label_two,
                },
            ).all()
            # Find only the two probe rows we just inserted. Real data
            # may already have labels like "Military" or "Custom" from
            # the backfill, so the probe labels must be migration-
            # unique.
            probe_rows = [r for r in rows if r[2] in (probe_label_one, probe_label_two)]
            assert len(probe_rows) == 2, probe_rows
            military = next(r for r in probe_rows if r[2] == probe_label_one)
            custom = next(r for r in probe_rows if r[2] == probe_label_two)
            assert military[1] == "military"
            assert military[3] == Decimal("5.00")
            assert custom[1] is None
            assert custom[3] == Decimal("2.50")
        finally:
            sp.rollback()

        # CHECK enforces 0..50 per row. Run in its own savepoint so the
        # rejection cannot poison subsequent probes.
        sp = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        f"INSERT INTO {child} "
                        f"({fk_col}, label, percent) "
                        f"VALUES (:pid, 'too_big', 60.0)"
                    ),
                    {"pid": parent_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    f"chk_{child}_percent_range did not reject 60"
                )
        finally:
            sp.rollback()

        # CHECK enforces non-empty labels.
        sp = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        f"INSERT INTO {child} "
                        f"({fk_col}, label, percent) "
                        f"VALUES (:pid, '', 5.0)"
                    ),
                    {"pid": parent_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    f"chk_{child}_label_nonempty did not reject empty label"
                )
        finally:
            sp.rollback()
