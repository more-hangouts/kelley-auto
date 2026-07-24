"""Commission-mode sales activity monitoring (Phase 14).

Product pivot 2026-07-07: sales reps are 100% commission, so clock-in is an
"active in app" signal, not GPS/payroll attendance. The owner's real question
is "are my reps actually reviewing leads and contacts?" — so we record a
lightweight, read-only activity stream server-side at the endpoint boundary
(a rep can't fake it by suppressing a client beacon).

One additive table, no change to any existing row/behavior:

  - ``sales_activity_events`` — append-only. One row per meaningful read a
    sales rep performs: opening a lead/event, an appointment, a contact, or
    running a search. Recorded inside the existing sales read endpoints.

Deliberately a DEDICATED table rather than overloading event-scoped
``activity_log``: contact views and search events have no ``crm_event_id`` to
anchor to, and ``activity_log`` requires a non-null ``event_id``. Keeping this
stream separate also means monitoring writes can never collide with the
staff-facing timeline vocabulary.

Privacy invariants enforced by shape and by the writer (``services/
sales_activity.py``): NO note bodies, NO invoice/balance/financial fields, NO
document keys or portal tokens, and NO raw search text — only a normalized
query length and result count live in ``metadata`` for search rows.

Subject-pair invariant mirrors ``activity_log``: a row has BOTH
``subject_kind`` and ``subject_id`` or NEITHER (search rows carry neither).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE sales_activity_events (
                id             BIGSERIAL PRIMARY KEY,
                actor_user_id  INTEGER NOT NULL
                                 REFERENCES users(id) ON DELETE CASCADE,
                activity_type  VARCHAR(40) NOT NULL,
                subject_kind   VARCHAR(20),
                subject_id     INTEGER,
                route          TEXT,
                source         VARCHAR(40),
                metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_sales_activity_subject_pair CHECK (
                    (subject_kind IS NULL) = (subject_id IS NULL)
                )
            )
            """
        )
    )

    # Admin reporting reads: "what did rep N do today?" — the dominant query.
    connection.execute(
        text(
            "CREATE INDEX ix_sales_activity_actor_time "
            "ON sales_activity_events (actor_user_id, created_at DESC)"
        )
    )
    # Cross-rep rollups by type ("leads viewed today across the floor").
    connection.execute(
        text(
            "CREATE INDEX ix_sales_activity_type_time "
            "ON sales_activity_events (activity_type, created_at DESC)"
        )
    )
    # Per-subject drilldown ("who looked at this lead?") + backs the
    # writer's throttle lookup. Partial: search rows have no subject.
    connection.execute(
        text(
            "CREATE INDEX ix_sales_activity_subject "
            "ON sales_activity_events (subject_kind, subject_id, created_at DESC) "
            "WHERE subject_kind IS NOT NULL"
        )
    )
