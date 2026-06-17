from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 9 review fix.
    #
    # Migration 036 had two interacting rules that conflict on user
    # deletion:
    #
    #   - actor_user_id FK uses ON DELETE SET NULL (so deleting a user
    #     wipes the FK on every activity row).
    #   - chk_activity_staff_has_actor enforces actor_kind <> 'staff'
    #     OR actor_user_id IS NOT NULL (so a staff row with NULL FK
    #     is invalid).
    #
    # The collision: deleting a user with staff activity rows would
    # cascade NULL into actor_user_id and then immediately fail the
    # CHECK. Either the FK has to RESTRICT or the CHECK has to relax.
    #
    # We pick "relax + denormalize" so user deletion doesn't lose the
    # audit trail entirely. A new actor_display_name column captures
    # the user's name at write-time; the reader joins users for the
    # live name first and falls back to the stored snapshot when the
    # FK has been nulled by a deletion.
    connection.execute(
        text(
            "ALTER TABLE activity_log "
            "DROP CONSTRAINT IF EXISTS chk_activity_staff_has_actor"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE activity_log "
            "ADD COLUMN actor_display_name VARCHAR(200)"
        )
    )
    # Backfill any existing staff rows so the new column has data
    # immediately. New writes will populate it directly.
    connection.execute(
        text(
            """
            UPDATE activity_log a
               SET actor_display_name = COALESCE(u.full_name, u.username)
              FROM users u
             WHERE a.actor_user_id = u.id
               AND a.actor_display_name IS NULL
            """
        )
    )
