"""Phase 6 of the Sales Portal: tighten event_participants.contact_id.

Phase 6 enforces the rule "no participant exists without a contact."
The Phase 0 audit confirmed the column was already populated for every
existing row (0 orphans), so this migration is a clean tightening:

  - SET NOT NULL on event_participants.contact_id.
  - Switch the FK from ON DELETE SET NULL to ON DELETE RESTRICT, so
    a hard-delete attempt against a contact that's still tied to an
    event blocks at the FK rather than tripping the new NOT NULL via
    a multi-step SET NULL → NOT NULL violation.

A defensive pre-flight assertion fails loudly if any orphan rows
sneaked in between the Phase 0 audit and this deploy. That keeps the
ALTER COLUMN from blowing up mid-migration on a row count we did not
expect.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- Pre-flight: refuse to run if any orphan rows exist. ----
    orphan_count = connection.execute(
        text(
            "SELECT COUNT(*) FROM event_participants WHERE contact_id IS NULL"
        )
    ).scalar()
    assert orphan_count == 0, (
        f"event_participants has {orphan_count} rows with NULL contact_id. "
        "Backfill or delete them before this migration runs; the Phase 0 "
        "audit reported zero orphans."
    )

    connection.execute(
        text(
            "ALTER TABLE event_participants "
            "ALTER COLUMN contact_id SET NOT NULL"
        )
    )

    # Find the existing FK name so we can drop and re-add with the
    # tighter ON DELETE behavior. Don't hard-code the constraint name —
    # SQLAlchemy auto-named it during the original CREATE TABLE and the
    # name varies by environment.
    fk_row = connection.execute(
        text(
            """
            SELECT conname FROM pg_constraint
             WHERE conrelid = 'event_participants'::regclass
               AND contype = 'f'
               AND conname LIKE '%contact_id%'
            """
        )
    ).first()
    if fk_row is not None:
        fk_name = fk_row[0]
        connection.execute(
            text(
                f"ALTER TABLE event_participants DROP CONSTRAINT {fk_name}"
            )
        )
    connection.execute(
        text(
            """
            ALTER TABLE event_participants
              ADD CONSTRAINT fk_event_participants_contact
              FOREIGN KEY (contact_id)
              REFERENCES contacts(id)
              ON DELETE RESTRICT
            """
        )
    )

    # ---- DML probes per the project rule ----
    # NULL contact_id is rejected by the new NOT NULL constraint.
    sp = connection.begin_nested()
    try:
        # We need a real event_id for the FK to a parent event. Pick any.
        event_row = connection.execute(
            text("SELECT id FROM events ORDER BY id LIMIT 1")
        ).first()
        if event_row is not None:
            event_id = int(event_row[0])
            try:
                connection.execute(
                    text(
                        """
                        INSERT INTO event_participants
                            (event_id, contact_id, role, display_name)
                        VALUES (:eid, NULL, 'other', '__055_probe_null__')
                        """
                    ),
                    {"eid": event_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "event_participants accepted a NULL contact_id after "
                    "the migration"
                )
    finally:
        sp.rollback()

    # ON DELETE RESTRICT blocks contact removal while participants
    # reference the contact.
    sp = connection.begin_nested()
    try:
        # Find a contact that has at least one participant row.
        contact_row = connection.execute(
            text(
                "SELECT contact_id FROM event_participants "
                "WHERE contact_id IS NOT NULL "
                "ORDER BY id LIMIT 1"
            )
        ).first()
        if contact_row is not None:
            contact_id = int(contact_row[0])
            try:
                connection.execute(
                    text("DELETE FROM contacts WHERE id = :cid"),
                    {"cid": contact_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "ON DELETE RESTRICT did not block contact deletion "
                    "while event_participants rows referenced it"
                )
    finally:
        sp.rollback()
