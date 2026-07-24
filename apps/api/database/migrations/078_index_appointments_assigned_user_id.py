"""Partial index on ``appointments.assigned_user_id``.

Phase 7 of the sales rep dashboard plan. The column has existed since
migration 005 but was never indexed; Phase 5 (sales walk-ins) and
Phase 6 (assignment endpoints + lead cascade) made it a hot read path:

  - "Today's appointments, mine only" filters by ``assigned_user_id``.
  - The lead reassignment cascade queries
    ``WHERE crm_event_id = :event_id AND slot_start_at >= NOW()`` —
    the cascade itself doesn't need the assigned_user_id index, but
    every read that powers a stylist's day does.

Partial form (``WHERE assigned_user_id IS NOT NULL``) matches the
mirror partial index on ``events.owner_user_id`` from migration 015.
NULL is the dominant value pre-Phase-4, and the planner does not need
the index for an "unassigned" search — there is no such UI.

No backfill — the optional consistency backfill (copying
``events.owner_user_id`` onto past appointments' ``assigned_user_id``)
is deferred per the plan's "do not run silently" guidance.
"""

from sqlalchemy import text


_INDEX_NAME = "idx_appointments_assigned_user_id"


def upgrade(connection) -> None:
    connection.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
            "ON appointments(assigned_user_id) "
            "WHERE assigned_user_id IS NOT NULL"
        )
    )

    # ===== DML probe =====
    # Verify the index exists with the partial predicate we expect.
    # `indexdef` is the round-tripped SQL Postgres uses to describe the
    # index, so any drift between the CREATE above and what the planner
    # actually has would surface here. The probe runs inside the
    # migration's transaction so a wrong predicate aborts the apply.
    row = connection.execute(
        text(
            "SELECT indexname, indexdef "
            "FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "  AND tablename = 'appointments' "
            "  AND indexname = :name"
        ),
        {"name": _INDEX_NAME},
    ).first()
    assert row is not None, f"{_INDEX_NAME} was not created"
    indexdef = row[1] or ""
    assert "assigned_user_id" in indexdef, indexdef
    # Postgres normalizes the partial predicate to lowercase + parens.
    assert "where (assigned_user_id is not null)" in indexdef.lower(), indexdef
