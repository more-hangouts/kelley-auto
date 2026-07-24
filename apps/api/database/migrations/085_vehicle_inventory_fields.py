"""Vehicle inventory fields on catalog_items (Day 1 / Phase 1).

Kelley Autoplex reuses `catalog_items` as the single inventory table for
v1 (see MIGRATION_PLAN.md "Domain Mapping"). This migration makes the
table car-capable WITHOUT renaming the table or any existing column and
WITHOUT touching existing quote/invoice/catalog behavior. Everything here
is additive and reversible-by-design: every new column is nullable (or
defaulted), so existing dress/catalog rows keep working untouched.

New columns
-----------
Vehicle attributes, all nullable so a non-vehicle row simply leaves them
NULL:

    vin, stock_number, year, make, model, trim, mileage, transmission,
    fuel_type, exterior_color, interior_color, body_type, drivetrain,
    condition, vehicle_status, carfax_url, video_url, features_json

Plus one discriminator:

    is_vehicle BOOLEAN NOT NULL DEFAULT false

`is_vehicle` is the explicit "this row is a car, not a gown" flag. It
exists because the compatibility backfill below copies `designer -> make`,
`style_number -> model`, and `color -> exterior_color` onto EVERY active
row (gowns included), and sets `vehicle_status='available'` on every
active row. After that backfill, `make IS NOT NULL` (or `vehicle_status`
alone) can no longer mean "this is a car" — those fields are populated on
dresses too. `is_vehicle` is the unambiguous signal: the vehicle create
path sets it true, and Day 4's public/vehicle queries gate on
`is_vehicle = true` (plus `vehicle_status`), never on the overloaded
compat fields. The backfill is therefore harmless: nothing reads vehicle
semantics on rows where `is_vehicle = false`.

Constraints
-----------
  - vehicle_status: CHECK constrains to the known status set when present
    (NULL allowed so the column is purely additive).
  - vin: partial-UNIQUE index WHERE vin is a non-empty string. Empty/NULL
    VINs are allowed (manual early entry) but a duplicate non-empty VIN is
    blocked.
  - stock_number: partial-UNIQUE index WHERE present. Duplicate stock
    numbers blocked when set.
  - mileage: CHECK non-negative when present.
  - year: CHECK 1980..2100 — a deliberately LOOSE backstop. The real rule
    ("1980 through next calendar year") can't be a DB CHECK because the
    upper bound is non-immutable (it moves each January); the service
    layer enforces the strict 1980..(current_year + 1) bound, the same way
    unit_price_cents >= 0 is enforced in services, not only in the DB.
  - features_json: NOT NULL DEFAULT '[]' with an array-shape CHECK,
    mirroring the existing `image_urls` JSONB column exactly.

Compatibility backfill
----------------------
Per MIGRATION_PLAN.md Phase 1, mirror the legacy fields forward so the new
vehicle columns are populated for existing rows:

    make           <- designer
    model          <- style_number
    exterior_color <- color
    vehicle_status <- 'available'   (active rows only)

DML probes at the end (savepoint, rolled back) round-trip a vehicle row
and assert each new CHECK / partial-unique actually fires. They follow the
same begin_nested()/rollback pattern migration 084 established, so nothing
the probes write ever persists.
"""

from sqlalchemy import text


_STATUS_VALUES = (
    "available",
    "pending",
    "sold",
    "delivered",
    "wholesale",
    "hidden",
)


def upgrade(connection) -> None:
    # --- 1. Add columns (all nullable / defaulted -> additive) -----------
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
                ADD COLUMN is_vehicle      BOOLEAN     NOT NULL DEFAULT false,
                ADD COLUMN vin             VARCHAR(17) NULL,
                ADD COLUMN stock_number    VARCHAR(64) NULL,
                ADD COLUMN year            SMALLINT    NULL
                    CHECK (year IS NULL OR year BETWEEN 1980 AND 2100),
                ADD COLUMN make            VARCHAR(80) NULL,
                ADD COLUMN model           VARCHAR(80) NULL,
                ADD COLUMN trim            VARCHAR(80) NULL,
                ADD COLUMN mileage         INTEGER     NULL
                    CHECK (mileage IS NULL OR mileage >= 0),
                ADD COLUMN transmission    VARCHAR(40) NULL,
                ADD COLUMN fuel_type       VARCHAR(40) NULL,
                ADD COLUMN exterior_color  VARCHAR(60) NULL,
                ADD COLUMN interior_color  VARCHAR(60) NULL,
                ADD COLUMN body_type       VARCHAR(40) NULL,
                ADD COLUMN drivetrain      VARCHAR(20) NULL,
                ADD COLUMN condition       VARCHAR(20) NULL,
                ADD COLUMN vehicle_status  VARCHAR(20) NULL
                    CONSTRAINT chk_catalog_items_vehicle_status
                    CHECK (
                        vehicle_status IS NULL
                        OR vehicle_status IN (
                            'available', 'pending', 'sold',
                            'delivered', 'wholesale', 'hidden'
                        )
                    ),
                ADD COLUMN carfax_url      TEXT        NULL,
                ADD COLUMN video_url       TEXT        NULL,
                ADD COLUMN features_json   JSONB       NOT NULL DEFAULT '[]'::jsonb
                    CONSTRAINT chk_catalog_items_features_json_array
                    CHECK (jsonb_typeof(features_json) = 'array')
            """
        )
    )

    # --- 1b. Additively widen the category CHECK to admit 'vehicle' ------
    # 041 created chk_catalog_items_category as an allow-list of five gown/
    # accessory/service values. Cars need their own honest category value:
    # filing them under 'service' would mislabel them "Alteration" in
    # customer_line_description and inherit any service-line semantics. This
    # is the same additive move Phase 2 makes on events.event_type/status —
    # every existing value stays valid and no existing row is affected; we
    # only ADD 'vehicle'. We must DROP and re-ADD because Postgres has no
    # in-place "add value to a CHECK".
    connection.execute(
        text("ALTER TABLE catalog_items DROP CONSTRAINT chk_catalog_items_category")
    )
    connection.execute(
        text(
            """
            ALTER TABLE catalog_items
                ADD CONSTRAINT chk_catalog_items_category
                CHECK (category IN (
                    'quince_gown',
                    'bridal_gown',
                    'formal_gown',
                    'accessory',
                    'service',
                    'vehicle'
                ))
            """
        )
    )

    # --- 2. Compatibility backfill (MIGRATION_PLAN.md Phase 1) -----------
    # Mirror legacy fields forward. These touch gown rows too; that is
    # intentional and harmless because is_vehicle stays false on them, and
    # the vehicle read paths gate on is_vehicle.
    connection.execute(
        text(
            "UPDATE catalog_items SET make = designer "
            "WHERE make IS NULL AND designer IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE catalog_items SET model = style_number "
            "WHERE model IS NULL AND style_number IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE catalog_items SET exterior_color = color "
            "WHERE exterior_color IS NULL AND color IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE catalog_items SET vehicle_status = 'available' "
            "WHERE vehicle_status IS NULL AND active = true"
        )
    )

    # --- 3. Partial-unique indexes --------------------------------------
    # Empty/NULL VIN allowed (early manual entry); duplicate non-empty VIN
    # blocked. Same rule for stock_number.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_catalog_items_vin "
            "ON catalog_items (vin) "
            "WHERE vin IS NOT NULL AND vin <> ''"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_catalog_items_stock_number "
            "ON catalog_items (stock_number) "
            "WHERE stock_number IS NOT NULL AND stock_number <> ''"
        )
    )
    # List/board filter helper for Day 4. Partial: only vehicle-ish rows.
    connection.execute(
        text(
            "CREATE INDEX idx_catalog_items_vehicle_status "
            "ON catalog_items (vehicle_status) "
            "WHERE vehicle_status IS NOT NULL"
        )
    )

    # --- 4. DML probes (savepoint, always rolled back) ------------------
    sp = connection.begin_nested()
    try:
        # A valid vehicle row using the now-widened category='vehicle',
        # the same value the real create path will set — so the probe
        # exercises the actual create shape, not a stand-in.
        veh_id = connection.execute(
            text(
                """
                INSERT INTO catalog_items
                    (internal_sku, public_code, color, category, image_urls,
                     is_vehicle, vin, stock_number, year, make, model, trim,
                     mileage, transmission, fuel_type, exterior_color,
                     interior_color, body_type, drivetrain, condition,
                     vehicle_status, carfax_url, video_url, features_json,
                     unit_price_cents)
                VALUES
                    ('VEH-PROBE-SKU', 'BVX-99801', 'White', 'vehicle',
                     '[]'::jsonb, true, '1HGCM82633A004352', 'STK-PROBE-1',
                     2019, 'Toyota', 'Camry', 'LE', 82214, 'Automatic', 'Gas',
                     'White', 'Black', 'Sedan', 'FWD', 'used', 'available',
                     'https://carfax.example/x', 'https://video.example/x',
                     '["Bluetooth","Backup Camera"]'::jsonb, 1499500)
                RETURNING id
                """
            )
        ).scalar()

        row = connection.execute(
            text(
                "SELECT is_vehicle, vin, stock_number, year, make, model, "
                "mileage, vehicle_status, features_json "
                "FROM catalog_items WHERE id = :id"
            ),
            {"id": veh_id},
        ).first()
        assert row[0] is True, "is_vehicle round-trip"
        assert row[1] == "1HGCM82633A004352", "vin round-trip"
        assert row[2] == "STK-PROBE-1", "stock_number round-trip"
        assert row[3] == 2019, "year round-trip"
        assert row[4] == "Toyota", "make round-trip"
        assert row[5] == "Camry", "model round-trip"
        assert row[6] == 82214, "mileage round-trip"
        assert row[7] == "available", "vehicle_status round-trip"
        assert row[8] == ["Bluetooth", "Backup Camera"], "features_json round-trip"

        def _rejects(sql: str, label: str) -> None:
            ok = False
            sp2 = connection.begin_nested()
            try:
                connection.execute(text(sql))
            except Exception:
                ok = True
                sp2.rollback()
            assert ok, f"{label} must be rejected"

        # Bad vehicle_status violates the CHECK.
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, "
            " vehicle_status) "
            "VALUES ('VEH-PROBE-BADSTAT', 'BVX-99802', 'Navy', 'vehicle', "
            "'[]'::jsonb, 'for_sale')",
            "invalid vehicle_status",
        )
        # Negative mileage violates the CHECK.
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, mileage) "
            "VALUES ('VEH-PROBE-NEGMI', 'BVX-99803', 'Navy', 'vehicle', "
            "'[]'::jsonb, -1)",
            "negative mileage",
        )
        # Year out of the loose backstop range violates the CHECK.
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, year) "
            "VALUES ('VEH-PROBE-BADYR', 'BVX-99804', 'Navy', 'vehicle', "
            "'[]'::jsonb, 1900)",
            "year below 1980",
        )
        # features_json must be a JSON array.
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, "
            " features_json) "
            "VALUES ('VEH-PROBE-BADFEAT', 'BVX-99805', 'Navy', 'vehicle', "
            "'[]'::jsonb, '{}'::jsonb)",
            "non-array features_json",
        )
        # Duplicate NON-EMPTY vin is blocked (same VIN as the probe row).
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, vin) "
            "VALUES ('VEH-PROBE-DUPVIN', 'BVX-99806', 'Navy', 'vehicle', "
            "'[]'::jsonb, '1HGCM82633A004352')",
            "duplicate non-empty vin",
        )
        # Duplicate stock_number is blocked.
        _rejects(
            "INSERT INTO catalog_items "
            "(internal_sku, public_code, color, category, image_urls, "
            " stock_number) "
            "VALUES ('VEH-PROBE-DUPSTK', 'BVX-99807', 'Navy', 'vehicle', "
            "'[]'::jsonb, 'STK-PROBE-1')",
            "duplicate stock_number",
        )

        # Two EMPTY-string VINs must both be allowed (partial index skips
        # them). Insert two rows with vin='' and assert no violation.
        connection.execute(
            text(
                "INSERT INTO catalog_items "
                "(internal_sku, public_code, color, category, image_urls, vin) "
                "VALUES ('VEH-PROBE-EMPTYVIN-A', 'BVX-99808', 'Navy', "
                "'vehicle', '[]'::jsonb, '')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO catalog_items "
                "(internal_sku, public_code, color, category, image_urls, vin) "
                "VALUES ('VEH-PROBE-EMPTYVIN-B', 'BVX-99809', 'Navy', "
                "'vehicle', '[]'::jsonb, '')"
            )
        )
    finally:
        sp.rollback()
