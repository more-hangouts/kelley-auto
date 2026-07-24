"""Add ``event_participant_id`` FK to appointments, quotes, invoices.

Phase 10.2 foundation (per docs/SALES_REP_DASHBOARD_PHASES.md). Today
event participants are stored in ``event_participants`` (one row per
court member, parent, or other person attached to a quince event), but
appointments, quotes, and invoices only attach to the shared event +
the participant's contact — nothing ties an appointment/quote/invoice
to the specific participant row. The pipeline cannot render a
participant's buyer journey because the underlying rows can't be
filtered by participant.

This migration adds a nullable FK ``event_participant_id`` to each of
appointments, quotes, and invoices. ``ON DELETE SET NULL`` matches the
existing pattern for soft-link FKs (``assigned_user_id``,
``owner_user_id``): deleting an event_participant row preserves the
appointment/quote/invoice without orphaning the FK.

NULL ``event_participant_id`` means "celebrant's purchase or
unspecified" — backward-compatible with every existing row.

Partial indexes mirror the partial-index pattern from migrations 078
and 015: the hot read is "rows for THIS participant," not "any null vs
not null check." NULL is the dominant value pre-Phase-10.

No backfill. Existing rows keep NULL until the service layer or admin
explicitly attaches them to a participant journey. The Phase 10.3
shared service is responsible for writing this column going forward;
this slice only adds the schema.
"""

from sqlalchemy import text


_TABLES = ("appointments", "quotes", "invoices")


def upgrade(connection) -> None:
    # ===== Columns =====
    for table in _TABLES:
        connection.execute(
            text(
                f"ALTER TABLE {table} "
                f"ADD COLUMN IF NOT EXISTS event_participant_id INTEGER "
                f"REFERENCES event_participants(id) ON DELETE SET NULL"
            )
        )

    # ===== Partial indexes =====
    for table in _TABLES:
        index_name = f"idx_{table}_event_participant_id"
        connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {table}(event_participant_id) "
                f"WHERE event_participant_id IS NOT NULL"
            )
        )

    # ===== Schema probes =====
    # Verify each column exists with the right type + nullability + FK,
    # and each partial index exists with the predicate we asked for.
    # Probes run inside the migration's transaction so any drift aborts
    # the apply before commit (per feedback_validate_schema_with_real_inserts).
    for table in _TABLES:
        col = connection.execute(
            text(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t "
                "  AND column_name='event_participant_id'"
            ),
            {"t": table},
        ).first()
        assert col is not None, f"{table}.event_participant_id was not added"
        assert col[0] == "integer", f"{table}.event_participant_id type={col[0]}"
        assert col[1] == "YES", (
            f"{table}.event_participant_id nullability={col[1]} (expected YES)"
        )

        fk = connection.execute(
            text(
                "SELECT rc.delete_rule, ccu.table_name, ccu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.referential_constraints rc "
                "  ON tc.constraint_name = rc.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.table_schema='public' "
                "  AND tc.table_name = :t "
                "  AND tc.constraint_type = 'FOREIGN KEY' "
                "  AND kcu.column_name = 'event_participant_id'"
            ),
            {"t": table},
        ).first()
        assert fk is not None, (
            f"{table}.event_participant_id FK was not created"
        )
        assert fk[0] == "SET NULL", (
            f"{table}.event_participant_id ON DELETE rule={fk[0]}, expected SET NULL"
        )
        assert fk[1] == "event_participants", (
            f"{table}.event_participant_id references {fk[1]}, expected event_participants"
        )
        assert fk[2] == "id", (
            f"{table}.event_participant_id references column {fk[2]}, expected id"
        )

        index_name = f"idx_{table}_event_participant_id"
        idx = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' "
                "  AND tablename=:t "
                "  AND indexname=:n"
            ),
            {"t": table, "n": index_name},
        ).first()
        assert idx is not None, f"{index_name} was not created"
        assert "event_participant_id" in idx[0], idx[0]
        assert "where (event_participant_id is not null)" in idx[0].lower(), idx[0]
