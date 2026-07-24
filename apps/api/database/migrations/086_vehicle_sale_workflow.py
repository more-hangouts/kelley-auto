"""Vehicle-sale deal pipeline on the events table (Day 3 / Phase 2).

Kelley Autoplex reuses the existing CRM `events` table for car deals — the
same kanban the quinceañera boutique used, with a second workflow. This
migration teaches the table about `vehicle_sale` WITHOUT touching any
existing quinceañera row or behavior. Everything here is additive:

  - `chk_events_event_type` widens from ('quinceanera') to add
    'vehicle_sale'. Postgres has no "add value to a CHECK", so we DROP and
    re-ADD — every existing value stays valid; we only ADD one.
  - `chk_events_status` widens to the UNION of the quinceañera statuses
    and the nine vehicle-sale statuses (`new_lead`, `contacted`,
    `appointment`, `test_drive`, `negotiation`, `financing`, `sold`,
    `delivered`, `lost`). `sold` is shared between the two workflows and
    appears once. The single shared CHECK is the established pattern (one
    constraint for every event_type); the per-workflow status set is
    enforced in services/event_workflow.py + the status-patch service,
    which gates each event on the statuses of ITS OWN workflow.
  - `events.vehicle_catalog_item_id` — nullable FK to catalog_items(id),
    ON DELETE SET NULL. Links a deal to the car being sold. NULL for
    general leads and every quinceañera row. SET NULL (not CASCADE/RESTRICT)
    so removing a vehicle never blocks or deletes its deal history.

Terminal semantics (`delivered`, `lost` terminal; `sold` non-terminal so
the team can finish paperwork/delivery) live in event_workflow.py, not the
DB — the CHECK only governs the allowed value set.

`is_vehicle` stays the hard inventory boundary: this migration adds a deal
link but the status propagation that marks a car sold/delivered (in the
service) only ever touches a linked row when `is_vehicle = true`.

DML probes at the end (savepoint, always rolled back, mirroring migration
085) round-trip a vehicle_sale deal and assert the widened CHECKs accept
the new values, still accept the old ones, reject bogus values, and that
the FK's ON DELETE SET NULL fires.
"""

from sqlalchemy import text


# UNION of quinceañera (015) and vehicle-sale (Day 3) statuses. `sold` is
# common to both and listed once.
_STATUS_VALUES = (
    # quinceañera (existing)
    "lead",
    "consulted",
    "sold",
    "on_order",
    "arrived",
    "in_alterations",
    "ready_for_pickup",
    "picked_up",
    "cancelled",
    # vehicle_sale (new) — 'sold' already above
    "new_lead",
    "contacted",
    "appointment",
    "test_drive",
    "negotiation",
    "financing",
    "delivered",
    "lost",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade(connection) -> None:
    # --- 1. Widen event_type CHECK to admit 'vehicle_sale' ---------------
    connection.execute(
        text("ALTER TABLE events DROP CONSTRAINT chk_events_event_type")
    )
    connection.execute(
        text(
            """
            ALTER TABLE events
                ADD CONSTRAINT chk_events_event_type
                CHECK (event_type IN ('quinceanera', 'vehicle_sale'))
            """
        )
    )

    # --- 2. Widen status CHECK to the union of both workflows ------------
    connection.execute(
        text("ALTER TABLE events DROP CONSTRAINT chk_events_status")
    )
    connection.execute(
        text(
            f"""
            ALTER TABLE events
                ADD CONSTRAINT chk_events_status
                CHECK (status IN ({_in_list(_STATUS_VALUES)}))
            """
        )
    )

    # --- 3. Vehicle link column + lookup index --------------------------
    connection.execute(
        text(
            """
            ALTER TABLE events
                ADD COLUMN vehicle_catalog_item_id INTEGER NULL
                    REFERENCES catalog_items(id) ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_events_vehicle_catalog_item_id "
            "ON events(vehicle_catalog_item_id) "
            "WHERE vehicle_catalog_item_id IS NOT NULL"
        )
    )

    # --- 4. DML probes (savepoint, always rolled back) ------------------
    sp = connection.begin_nested()
    try:
        contact_id = connection.execute(
            text(
                "INSERT INTO contacts (display_name) "
                "VALUES ('Deal Probe Buyer') RETURNING id"
            )
        ).scalar()

        veh_id = connection.execute(
            text(
                """
                INSERT INTO catalog_items
                    (internal_sku, public_code, color, category, image_urls,
                     is_vehicle, stock_number, make, model, vehicle_status)
                VALUES
                    ('VEHSALE-PROBE-SKU', 'BVX-99950', 'White', 'vehicle',
                     '[]'::jsonb, true, 'STK-DEALPROBE-1', 'Toyota', 'Camry',
                     'available')
                RETURNING id
                """
            )
        ).scalar()

        # A valid vehicle_sale deal in its initial column, linked to the car.
        event_id = connection.execute(
            text(
                """
                INSERT INTO events
                    (primary_contact_id, event_type, event_name, status,
                     vehicle_catalog_item_id)
                VALUES
                    (:cid, 'vehicle_sale', '2019 Toyota Camry — Deal Probe',
                     'new_lead', :vid)
                RETURNING id
                """
            ),
            {"cid": contact_id, "vid": veh_id},
        ).scalar()

        row = connection.execute(
            text(
                "SELECT event_type, status, vehicle_catalog_item_id "
                "FROM events WHERE id = :id"
            ),
            {"id": event_id},
        ).first()
        assert row[0] == "vehicle_sale", "event_type round-trip"
        assert row[1] == "new_lead", "vehicle_sale initial status round-trip"
        assert row[2] == veh_id, "vehicle link round-trip"

        # Existing quinceañera shape still inserts — back-compat intact.
        connection.execute(
            text(
                "INSERT INTO events "
                "(primary_contact_id, event_type, event_name, status) "
                "VALUES (:cid, 'quinceanera', 'Quince Probe', 'lead')"
            ),
            {"cid": contact_id},
        )

        # A terminal vehicle-sale status is an accepted value.
        connection.execute(
            text(
                "INSERT INTO events "
                "(primary_contact_id, event_type, event_name, status) "
                "VALUES (:cid, 'vehicle_sale', 'Delivered Probe', 'delivered')"
            ),
            {"cid": contact_id},
        )

        def _rejects(sql: str, params: dict, label: str) -> None:
            ok = False
            sp2 = connection.begin_nested()
            try:
                connection.execute(text(sql), params)
            except Exception:
                ok = True
                sp2.rollback()
            assert ok, f"{label} must be rejected"

        # Unknown event_type violates the widened CHECK.
        _rejects(
            "INSERT INTO events "
            "(primary_contact_id, event_type, event_name, status) "
            "VALUES (:cid, 'wedding', 'Bad Type', 'new_lead')",
            {"cid": contact_id},
            "unknown event_type",
        )
        # Unknown status violates the widened CHECK.
        _rejects(
            "INSERT INTO events "
            "(primary_contact_id, event_type, event_name, status) "
            "VALUES (:cid, 'vehicle_sale', 'Bad Status', 'for_sale')",
            {"cid": contact_id},
            "unknown status",
        )

        # ON DELETE SET NULL: removing the car nulls the deal's link rather
        # than blocking the delete or cascading the deal away.
        connection.execute(
            text("DELETE FROM catalog_items WHERE id = :id"), {"id": veh_id}
        )
        linked = connection.execute(
            text("SELECT vehicle_catalog_item_id FROM events WHERE id = :id"),
            {"id": event_id},
        ).scalar()
        assert linked is None, "vehicle delete must SET NULL the deal link"
    finally:
        sp.rollback()
