from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 9. The "what happened" timeline rendered on every event detail
    # page. Mirrors Invoice Ninja's `activities` table shape but stays a
    # plain log — no projections, no derived state, no triggers. The
    # service layer writes one row per state change; reads are a single
    # query scoped by event_id.
    #
    # The existing `event_status_change_events` table is left intact;
    # the kanban depends on it. Phase 9 emits a parallel
    # `event.status_changed` activity_log row so the timeline UI gets
    # one source of truth without breaking anything.
    connection.execute(
        text(
            """
            CREATE TABLE activity_log (
                id                BIGSERIAL PRIMARY KEY,
                event_id          INTEGER NOT NULL
                                  REFERENCES events(id) ON DELETE CASCADE,
                actor_user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
                actor_kind        VARCHAR(16) NOT NULL,
                activity_type     VARCHAR(40) NOT NULL,
                subject_kind      VARCHAR(20),
                subject_id        INTEGER,
                payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_activity_actor_kind
                  CHECK (actor_kind IN ('staff', 'customer', 'system')),
                -- staff actions need an actor; customer/system can be NULL
                CONSTRAINT chk_activity_staff_has_actor
                  CHECK (actor_kind <> 'staff' OR actor_user_id IS NOT NULL),
                CONSTRAINT chk_activity_subject_pair
                  CHECK ((subject_kind IS NULL) = (subject_id IS NULL))
            )
            """
        )
    )
    # The hot read path is "list this event's activities, newest first";
    # an index on (event_id, id DESC) gives keyset pagination for free.
    connection.execute(
        text(
            "CREATE INDEX idx_activity_log_event_id_desc "
            "ON activity_log (event_id, id DESC)"
        )
    )
    # Subject lookups (e.g. "everything that ever touched invoice 123")
    # are rare but cheap to support and keep the audit story complete.
    connection.execute(
        text(
            "CREATE INDEX idx_activity_log_subject "
            "ON activity_log (subject_kind, subject_id) "
            "WHERE subject_kind IS NOT NULL"
        )
    )
