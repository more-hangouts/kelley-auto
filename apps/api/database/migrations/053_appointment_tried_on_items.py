"""Phase 4 of the Sales Portal: dress try-on log.

One row per (appointment, catalog item, size) the stylist actually
brought out of the back. Owner reads "what was tried on but didn't
sell" by joining this table against quotes/invoices in reporting.

The unique constraint uses ``NULLS NOT DISTINCT`` (PG 15+) so two
rows with the same appointment + catalog item + NULL size_label also
collide. Without that flag, ``UNIQUE`` treats NULLs as distinct and
the "stylist tapped Add twice for the same dress without a size"
case would silently double-write.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE appointment_tried_on_items (
                id BIGSERIAL PRIMARY KEY,
                appointment_id INTEGER NOT NULL
                    REFERENCES appointments(id) ON DELETE CASCADE,
                catalog_item_id INTEGER NOT NULL
                    REFERENCES catalog_items(id) ON DELETE RESTRICT,
                size_label VARCHAR(50) NULL,
                liked BOOLEAN NULL,
                notes TEXT NULL,
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_atoi_appt_item_size
                    UNIQUE NULLS NOT DISTINCT (
                        appointment_id, catalog_item_id, size_label
                    )
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_atoi_appointment "
            "ON appointment_tried_on_items(appointment_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_atoi_catalog "
            "ON appointment_tried_on_items(catalog_item_id)"
        )
    )

    # ---- DML probes per the project rule ----
    # Pick any active catalog row + appointment row to probe against.
    # The probes are idempotent: each runs in its own savepoint so a
    # rejection cannot poison subsequent probes.
    catalog_row = connection.execute(
        text(
            "SELECT id FROM catalog_items "
            "WHERE active IS TRUE ORDER BY id LIMIT 1"
        )
    ).first()
    appt_row = connection.execute(
        text("SELECT id FROM appointments ORDER BY id LIMIT 1")
    ).first()

    if catalog_row is None or appt_row is None:
        # Fresh install: nothing to probe against. Schema is created;
        # behavior smokes will catch regressions.
        return

    catalog_id = int(catalog_row[0])
    appt_id = int(appt_row[0])

    # Happy path: insert size=10, then a different size for the same
    # dress, both succeed. Uses unique label markers so the probe never
    # collides with real data.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                INSERT INTO appointment_tried_on_items
                    (appointment_id, catalog_item_id, size_label, notes)
                VALUES
                    (:aid, :cid, '__053_probe_10__', 'probe row 1'),
                    (:aid, :cid, '__053_probe_12__', 'probe row 2')
                """
            ),
            {"aid": appt_id, "cid": catalog_id},
        )
        rows = connection.execute(
            text(
                "SELECT size_label FROM appointment_tried_on_items "
                "WHERE appointment_id = :aid AND catalog_item_id = :cid "
                "AND size_label IN ('__053_probe_10__', '__053_probe_12__') "
                "ORDER BY size_label"
            ),
            {"aid": appt_id, "cid": catalog_id},
        ).all()
        assert len(rows) == 2, rows
    finally:
        sp.rollback()

    # Duplicate same (appt, item, size) is rejected.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "INSERT INTO appointment_tried_on_items "
                "(appointment_id, catalog_item_id, size_label) "
                "VALUES (:aid, :cid, '__053_probe_10__')"
            ),
            {"aid": appt_id, "cid": catalog_id},
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO appointment_tried_on_items "
                    "(appointment_id, catalog_item_id, size_label) "
                    "VALUES (:aid, :cid, '__053_probe_10__')"
                ),
                {"aid": appt_id, "cid": catalog_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "uq_atoi_appt_item_size did not reject duplicate (size=10, size=10)"
            )
    finally:
        sp.rollback()

    # NULLS NOT DISTINCT: two rows with NULL size_label also collide.
    # This is the case PG default UNIQUE would miss.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "INSERT INTO appointment_tried_on_items "
                "(appointment_id, catalog_item_id, notes) "
                "VALUES (:aid, :cid, '__053_probe_null_a__')"
            ),
            {"aid": appt_id, "cid": catalog_id},
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO appointment_tried_on_items "
                    "(appointment_id, catalog_item_id, notes) "
                    "VALUES (:aid, :cid, '__053_probe_null_b__')"
                ),
                {"aid": appt_id, "cid": catalog_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "NULLS NOT DISTINCT failed: NULL size_label collision was not rejected"
            )
    finally:
        sp.rollback()

    # ON DELETE RESTRICT on catalog_items.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "INSERT INTO appointment_tried_on_items "
                "(appointment_id, catalog_item_id, size_label) "
                "VALUES (:aid, :cid, '__053_probe_restrict__')"
            ),
            {"aid": appt_id, "cid": catalog_id},
        )
        try:
            connection.execute(
                text("DELETE FROM catalog_items WHERE id = :cid"),
                {"cid": catalog_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "ON DELETE RESTRICT did not block catalog deletion with try-on rows"
            )
    finally:
        sp.rollback()
