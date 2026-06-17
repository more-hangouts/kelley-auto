"""D2 of the CRM record deletion plan: reclassify Tier 2 CRM-core
tables (``contacts``, ``events``, ``event_participants``,
``special_orders``) from append-only to single-state soft-delete.

Adds a nullable ``deleted_at TIMESTAMPTZ`` to each. ``NULL`` means
active; ``NOT NULL`` means in the Recycle Bin (D3 surfaces it).
Counterpart Recycle-Bin partial indexes target the deleted set so
``WHERE deleted_at IS NOT NULL`` queries stay cheap.

The two existing partial unique indexes that overlap the deletion
state get rewritten to add ``AND deleted_at IS NULL``:

  - ``uq_contacts_phone_e164``: previously
    ``WHERE phone_e164 IS NOT NULL``. Without ``deleted_at IS NULL``,
    a soft-deleted contact would block a returning customer from
    being recreated with the same phone.
  - ``uq_event_participants_quinceanera_per_event``: previously
    ``WHERE role='quinceanera' AND status='active'``. Adding the
    ``deleted_at IS NULL`` predicate is belt-and-suspenders — D3
    will also flip ``status='removed'`` on archive — but keeps the
    invariant honest if any code path forgets the status flip.

No other unique indexes on the four target tables overlap deletion
(verified during the D2 schema audit in
``docs/CRM_RECORD_DELETION_PLAN.md``).

No backfill: every existing row stays active (``deleted_at IS NULL``)
because nothing has been archived yet. The dependency service
(``services/record_dependencies.py``) flips its
``_TARGET_TABLES_WITH_DELETED_AT`` flags in the same commit so deleted
counts start reflecting reality.

Concurrent-rebuild note: contacts is a small table in this single-
tenant deployment (hundreds of rows). The DROP + CREATE UNIQUE INDEX
holds a brief table-level lock that the admin-facing surface tolerates
without disruption. If this migration is ever ported to a larger
deployment, swap to ``CREATE UNIQUE INDEX CONCURRENTLY`` + ``DROP INDEX
CONCURRENTLY`` and remove the wrapping transaction.

DML probes verify:

  1. Partial unique on contacts allows reusing a phone_e164 once the
     prior row is soft-deleted.
  2. Partial unique on event_participants allows a new active
     quinceanera once the prior one is soft-deleted.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    for table in ("contacts", "events", "event_participants", "special_orders"):
        connection.execute(
            text(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMPTZ")
        )

    # ------------------------------------------------------------------
    # Partial indexes on the deleted set (Recycle Bin reads).
    # ------------------------------------------------------------------
    for table in ("contacts", "events", "event_participants", "special_orders"):
        connection.execute(
            text(
                f"CREATE INDEX idx_{table}_deleted_at "
                f"ON {table}(deleted_at DESC) "
                "WHERE deleted_at IS NOT NULL"
            )
        )

    # ------------------------------------------------------------------
    # Rewrite the two overlapping partial unique indexes.
    # ------------------------------------------------------------------
    connection.execute(text("DROP INDEX uq_contacts_phone_e164"))
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_contacts_phone_e164 "
            "ON contacts(phone_e164) "
            "WHERE phone_e164 IS NOT NULL AND deleted_at IS NULL"
        )
    )

    connection.execute(
        text("DROP INDEX uq_event_participants_quinceanera_per_event")
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_event_participants_quinceanera_per_event "
            "ON event_participants(event_id) "
            "WHERE role = 'quinceanera' AND status = 'active' "
            "  AND deleted_at IS NULL"
        )
    )

    # ------------------------------------------------------------------
    # DML probes (project rule: validate schema with real INSERTs).
    # Each runs in a savepoint that rolls back so the migration leaves
    # no test data behind.
    # ------------------------------------------------------------------

    # Probe 1: contacts phone_e164 partial unique allows reuse after
    # soft-delete. The "expected to fail" INSERT lives inside its own
    # nested savepoint so its failure does not abort the outer
    # transaction.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                INSERT INTO contacts (display_name, phone_e164)
                VALUES ('__080_probe_contact_a__', '+15555550190')
                """
            )
        )
        inner = connection.begin_nested()
        duplicate_blocked = False
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO contacts (display_name, phone_e164)
                    VALUES ('__080_probe_contact_b__', '+15555550190')
                    """
                )
            )
        except Exception:
            duplicate_blocked = True
            inner.rollback()
        else:
            inner.rollback()
        if not duplicate_blocked:
            raise AssertionError(
                "uq_contacts_phone_e164 did not block a duplicate phone "
                "while the original row was live"
            )

        # Soft-delete the first row; the partial unique should now
        # allow the second insert.
        connection.execute(
            text(
                "UPDATE contacts SET deleted_at = NOW() "
                "WHERE display_name = '__080_probe_contact_a__'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO contacts (display_name, phone_e164)
                VALUES ('__080_probe_contact_b__', '+15555550190')
                """
            )
        )
    finally:
        sp.rollback()

    # Probe 2: event_participants quinceanera-per-event partial unique
    # allows replacement after soft-delete. Needs a parent event row.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                INSERT INTO contacts (display_name)
                VALUES ('__080_probe_event_contact__')
                """
            )
        )
        contact_id = connection.execute(
            text(
                "SELECT id FROM contacts "
                "WHERE display_name = '__080_probe_event_contact__'"
            )
        ).scalar()
        connection.execute(
            text(
                """
                INSERT INTO events
                    (primary_contact_id, event_type, event_name, status)
                VALUES (:cid, 'quinceanera', '__080_probe_event__', 'lead')
                """
            ),
            {"cid": contact_id},
        )
        event_id = connection.execute(
            text(
                "SELECT id FROM events "
                "WHERE event_name = '__080_probe_event__'"
            )
        ).scalar()
        connection.execute(
            text(
                """
                INSERT INTO event_participants
                    (event_id, contact_id, role, display_name, status)
                VALUES (:eid, :cid, 'quinceanera',
                        '__080_probe_quince_a__', 'active')
                """
            ),
            {"eid": event_id, "cid": contact_id},
        )
        # A second active quince must FAIL — inner savepoint so the
        # outer transaction stays usable.
        inner = connection.begin_nested()
        duplicate_blocked = False
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO event_participants
                        (event_id, contact_id, role, display_name, status)
                    VALUES (:eid, :cid, 'quinceanera',
                            '__080_probe_quince_b__', 'active')
                    """
                ),
                {"eid": event_id, "cid": contact_id},
            )
        except Exception:
            duplicate_blocked = True
            inner.rollback()
        else:
            inner.rollback()
        if not duplicate_blocked:
            raise AssertionError(
                "uq_event_participants_quinceanera_per_event did not block "
                "a second active quinceanera while the original row was live"
            )

        # Soft-delete the first quince; insert should now succeed.
        connection.execute(
            text(
                "UPDATE event_participants SET deleted_at = NOW() "
                "WHERE display_name = '__080_probe_quince_a__'"
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO event_participants
                    (event_id, contact_id, role, display_name, status)
                VALUES (:eid, :cid, 'quinceanera',
                        '__080_probe_quince_b__', 'active')
                """
            ),
            {"eid": event_id, "cid": contact_id},
        )
    finally:
        sp.rollback()
