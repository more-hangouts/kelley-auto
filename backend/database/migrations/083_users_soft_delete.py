"""Staff archive (soft delete) for the staff roster.

Adds a `deleted_at` soft-delete to `users`, mirroring the CRM recycle-bin
pattern from migration 080. Archiving a staffer hides them from the admin
roster and (via `is_active=False`, set in the same service call) blocks
login/PIN and drops them from scheduling/notifications — while preserving
all history (schedules, attendance, payroll attribution), which the
project's no-hard-delete-of-history guardrail requires.

`deleted_by_user_id` / `deleted_reason` capture who archived and why for
the audit trail. Username/email stay globally unique (no partial-unique
change) so the auth lookups don't need a deleted_at filter — an archived
account simply keeps its username reserved until restored.

DML probes round-trip the soft-delete and the self-referential
deleted_by SET NULL.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD COLUMN deleted_at TIMESTAMPTZ NULL,
                ADD COLUMN deleted_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                ADD COLUMN deleted_reason TEXT NULL
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_users_deleted_at "
            "ON users(deleted_at) WHERE deleted_at IS NOT NULL"
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        actor_id = connection.execute(
            text(
                "INSERT INTO users (username, email, hashed_password, "
                "is_active, role) VALUES "
                "('softdel-probe-actor', 'softdel-probe-actor@example.com', "
                "'x', TRUE, 'admin') RETURNING id"
            )
        ).scalar()
        target_id = connection.execute(
            text(
                "INSERT INTO users (username, email, hashed_password, "
                "is_active, role, deleted_at, deleted_by_user_id, "
                "deleted_reason) VALUES "
                "('softdel-probe-target', 'softdel-probe-target@example.com', "
                "'x', FALSE, 'sales', NOW(), :actor, 'left the shop') "
                "RETURNING id"
            ),
            {"actor": actor_id},
        ).scalar()

        row = connection.execute(
            text(
                "SELECT deleted_at, deleted_by_user_id, deleted_reason "
                "FROM users WHERE id = :id"
            ),
            {"id": target_id},
        ).first()
        assert row[0] is not None, "deleted_at should be set"
        assert row[1] == actor_id
        assert row[2] == "left the shop"

        # Deleting the archiver nulls deleted_by_user_id, keeps the row.
        connection.execute(
            text("DELETE FROM users WHERE id = :id"), {"id": actor_id}
        )
        survivor = connection.execute(
            text("SELECT deleted_by_user_id FROM users WHERE id = :id"),
            {"id": target_id},
        ).scalar()
        assert survivor is None, (
            "deleted_by_user_id should SET NULL when the archiver is removed"
        )
    finally:
        sp.rollback()
