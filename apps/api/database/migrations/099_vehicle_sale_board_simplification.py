"""Trim the vehicle_sale board to the columns Kelley actually works.

The Day 3 pipeline (migration 086) modeled a full franchise-store funnel:
new_lead -> contacted -> appointment -> test_drive -> negotiation ->
financing -> sold -> delivered -> lost. In practice Kelley's floor never
used the middle of it — financing runs on its own system, and the board
only needs to answer three questions: did we talk to them, do we owe them
a follow-up, and did we win or lose it.

So the vehicle-sale status set becomes:

    new_lead -> contacted -> follow_up -> sold | lost

  - ADDS `follow_up` — the working list of customers owed a callback.
  - DROPS `appointment`, `test_drive`, `negotiation`, `financing`, and
    `delivered` from the allowed values.

Dropping values from a CHECK is only safe when nothing uses them, so
`upgrade` COUNTS the rows in each dropped status first and raises if any
exist rather than letting the ALTER fail halfway. At authoring time the
production table held exactly three vehicle-sale statuses — contacted
(43), lost (39), new_lead (7) — and zero rows in any dropped value, so
this is a pure constraint narrowing with no data rewrite.

`delivered` is dropped as a DEAL status only. It remains a valid
catalog_items.vehicle_status (migration 085) — an inventory fact staff set
on the car, no longer driven by a deal column. The service-side map in
booking/services/event_service.py drops its `delivered` entry to match.

Quinceañera statuses are untouched: the shared CHECK keeps all nine legacy
values, and `sold` (common to both workflows) stays listed once. Per-
workflow gating still lives in booking/services/event_workflow.py — keep
this value set and VEHICLE_SALE_STATUSES in sync.

DML probes at the end (savepoint, always rolled back, mirroring 086)
assert the narrowed CHECK accepts the new column, still accepts every
legacy quinceañera value, and now rejects each dropped status.
"""

from sqlalchemy import text


# Vehicle-sale statuses removed from the allowed set by this migration.
_DROPPED_STATUSES = (
    "appointment",
    "test_drive",
    "negotiation",
    "financing",
    "delivered",
)

# The narrowed union: quinceañera (015) + the trimmed vehicle-sale board.
# `sold` is common to both workflows and is listed once.
_STATUS_VALUES = (
    # quinceañera (unchanged)
    "lead",
    "consulted",
    "sold",
    "on_order",
    "arrived",
    "in_alterations",
    "ready_for_pickup",
    "picked_up",
    "cancelled",
    # vehicle_sale (trimmed) — 'sold' already above
    "new_lead",
    "contacted",
    "follow_up",
    "lost",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade(connection) -> None:
    # --- 1. Refuse to narrow the CHECK out from under live rows ---------
    counts = connection.execute(
        text(
            "SELECT status, COUNT(*) FROM events "
            f"WHERE status IN ({_in_list(_DROPPED_STATUSES)}) "
            "GROUP BY status"
        )
    ).all()
    if counts:
        detail = ", ".join(f"{status}={n}" for status, n in counts)
        raise RuntimeError(
            "cannot narrow chk_events_status: events still sit in dropped "
            f"vehicle_sale statuses ({detail}). Move them to new_lead/"
            "contacted/follow_up/sold/lost first, then re-run."
        )

    # --- 2. Swap the status CHECK for the narrowed union -----------------
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

    # --- 3. DML probes (savepoint, always rolled back) ------------------
    sp = connection.begin_nested()
    try:
        contact_id = connection.execute(
            text(
                "INSERT INTO contacts (display_name) "
                "VALUES ('Board Trim Probe') RETURNING id"
            )
        ).scalar()

        # The new column accepts a deal.
        event_id = connection.execute(
            text(
                """
                INSERT INTO events
                    (primary_contact_id, event_type, event_name, status)
                VALUES
                    (:cid, 'vehicle_sale', 'Follow Up Probe', 'follow_up')
                RETURNING id
                """
            ),
            {"cid": contact_id},
        ).scalar()
        status = connection.execute(
            text("SELECT status FROM events WHERE id = :id"), {"id": event_id}
        ).scalar()
        assert status == "follow_up", "follow_up round-trip"

        # Every surviving vehicle-sale column still inserts.
        for keep in ("new_lead", "contacted", "sold", "lost"):
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(primary_contact_id, event_type, event_name, status) "
                    "VALUES (:cid, 'vehicle_sale', 'Keep Probe', :st)"
                ),
                {"cid": contact_id, "st": keep},
            )

        # Legacy quinceañera values are untouched by the narrowing.
        for legacy in (
            "lead",
            "consulted",
            "on_order",
            "arrived",
            "in_alterations",
            "ready_for_pickup",
            "picked_up",
            "cancelled",
        ):
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(primary_contact_id, event_type, event_name, status) "
                    "VALUES (:cid, 'quinceanera', 'Legacy Probe', :st)"
                ),
                {"cid": contact_id, "st": legacy},
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

        # Each dropped status is now an illegal value.
        for gone in _DROPPED_STATUSES:
            _rejects(
                "INSERT INTO events "
                "(primary_contact_id, event_type, event_name, status) "
                "VALUES (:cid, 'vehicle_sale', 'Dropped Probe', :st)",
                {"cid": contact_id, "st": gone},
                f"dropped status {gone}",
            )

        # Garbage is still rejected.
        _rejects(
            "INSERT INTO events "
            "(primary_contact_id, event_type, event_name, status) "
            "VALUES (:cid, 'vehicle_sale', 'Garbage Probe', 'bogus')",
            {"cid": contact_id},
            "unknown status",
        )
    finally:
        sp.rollback()
