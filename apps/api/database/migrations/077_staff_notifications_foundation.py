"""Staff notifications foundation (B1 of the wiring pass).

Schema-only slice. Creates the durable plumbing the dispatcher in
``services/notification_routing`` (also added in this slice) needs to
support staff fan-out, per-user subscription overrides, and digest
event-history queries. No event-surface call sites change — those land
in B2.

What lands here:

  - ``notification_jobs`` widening: ``subject_kind`` + ``subject_id``
    (polymorphic subject pair) and ``recipient_user_id`` (FK to
    ``users``). The existing ``appointment_id`` column stays and gets
    backfilled into the new pair so legacy customer-booking flows
    keep dispatching while staff flows start writing the new shape.

  - ``staff_notification_events``: append-only event log. Real-time
    hooks (the customer-facing booking flows already in flight + the
    A-slice direct hooks) write here in the same transaction as the
    `notification_jobs` row so the digest worker has a complete
    timeline to summarise from. ``daily_digest_consumed_at`` and
    ``weekly_digest_consumed_at`` mark which rows each digest cadence
    has already covered — partial indexes make the "what's pending"
    query trivial.

  - ``notification_preferences``: per-user subscription overrides
    keyed by ``(user_id, event_kind)``. Existence of a row means the
    user has explicitly chosen; absence means the role default
    applies. Role defaults stay hardcoded in
    ``services/notification_routing.ROLE_DEFAULTS`` until ops needs
    to edit them from a UI.

  - ``uq_one_digest_per_user_per_window``: partial unique index that
    prevents the daily/weekly digest worker from double-sending if
    its tick fires twice on the same day. Scoped to digest-kind
    rows so it doesn't constrain the customer/staff transactional
    queue.

DML probes at the bottom exercise every new shape against the dev
DB before the migration commits, per the project's
validate-schema-with-real-inserts policy.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ===== notification_jobs widening =====
    connection.execute(
        text(
            "ALTER TABLE notification_jobs "
            "ADD COLUMN subject_kind TEXT, "
            "ADD COLUMN subject_id BIGINT, "
            "ADD COLUMN recipient_user_id INTEGER "
            "    REFERENCES users(id) ON DELETE SET NULL"
        )
    )
    # Backfill legacy customer-flow rows into the polymorphic pair so
    # the dispatcher can treat (subject_kind, subject_id) as canonical
    # going forward without losing context on jobs queued before B1.
    connection.execute(
        text(
            "UPDATE notification_jobs "
            "SET subject_kind = 'appointment', subject_id = appointment_id "
            "WHERE appointment_id IS NOT NULL AND subject_kind IS NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_notif_jobs_subject "
            "ON notification_jobs (subject_kind, subject_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_notif_jobs_recipient "
            "ON notification_jobs (recipient_user_id)"
        )
    )

    # ===== staff_notification_events =====
    connection.execute(
        text(
            """
            CREATE TABLE staff_notification_events (
                id            BIGSERIAL PRIMARY KEY,
                kind          TEXT NOT NULL,
                subject_kind  TEXT,
                subject_id    BIGINT,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                daily_digest_consumed_at  TIMESTAMPTZ,
                weekly_digest_consumed_at TIMESTAMPTZ
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_sne_kind_occurred "
            "ON staff_notification_events (kind, occurred_at DESC)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_sne_subject "
            "ON staff_notification_events (subject_kind, subject_id)"
        )
    )
    # Partial indexes for the digest workers' "what's unsummarised" scan.
    # The WHERE clauses match the exact predicate the runners will use so
    # the planner stays on the partial index.
    connection.execute(
        text(
            "CREATE INDEX ix_sne_daily_pending "
            "ON staff_notification_events (occurred_at) "
            "WHERE daily_digest_consumed_at IS NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_sne_weekly_pending "
            "ON staff_notification_events (occurred_at) "
            "WHERE weekly_digest_consumed_at IS NULL"
        )
    )

    # ===== notification_preferences =====
    connection.execute(
        text(
            """
            CREATE TABLE notification_preferences (
                user_id    INTEGER NOT NULL
                           REFERENCES users(id) ON DELETE CASCADE,
                event_kind TEXT NOT NULL,
                enabled    BOOLEAN NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, event_kind)
            )
            """
        )
    )

    # ===== Digest dedup index =====
    # Scoped to digest jobs so the customer transactional queue (where
    # the same kind+recipient can repeat across multiple appointments)
    # is untouched. payload->>'digest_window' is the date string the
    # runner stamps ('2026-05-18'), giving us a natural per-day key.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_one_digest_per_user_per_window "
            "ON notification_jobs ("
            "    recipient_user_id, kind, (payload ->> 'digest_window')"
            ") "
            "WHERE subject_kind = 'digest' AND status IN ('pending','sent')"
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        # Need a real user for FK probes; skip user-touching cases on an
        # empty users table (the migration is still valid, just less
        # exercised).
        user_row = connection.execute(
            text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).first()
        uid = int(user_row[0]) if user_row is not None else None

        # --- staff_notification_events: round-trip + payload + consumed marks
        event_id = connection.execute(
            text(
                "INSERT INTO staff_notification_events "
                "(kind, subject_kind, subject_id, actor_user_id, payload) "
                "VALUES (:k, :sk, :sid, :aid, CAST(:p AS jsonb)) "
                "RETURNING id"
            ),
            {
                "k": "test.event",
                "sk": "schedule_week",
                "sid": 999_999,
                "aid": uid,
                "p": '{"hint":"probe"}',
            },
        ).scalar()
        row = connection.execute(
            text(
                "SELECT kind, subject_kind, subject_id, payload, "
                "       daily_digest_consumed_at, weekly_digest_consumed_at "
                "FROM staff_notification_events WHERE id = :id"
            ),
            {"id": event_id},
        ).first()
        assert row[0] == "test.event"
        assert row[1] == "schedule_week"
        assert row[2] == 999_999
        assert row[3] == {"hint": "probe"}
        assert row[4] is None
        assert row[5] is None

        # Partial-index targeting: pending-daily query should see this row.
        pending = connection.execute(
            text(
                "SELECT COUNT(*) FROM staff_notification_events "
                "WHERE daily_digest_consumed_at IS NULL "
                "  AND id = :id"
            ),
            {"id": event_id},
        ).scalar()
        assert pending == 1

        # Mark consumed; row should drop out of pending.
        connection.execute(
            text(
                "UPDATE staff_notification_events "
                "SET daily_digest_consumed_at = NOW() WHERE id = :id"
            ),
            {"id": event_id},
        )
        pending_after = connection.execute(
            text(
                "SELECT COUNT(*) FROM staff_notification_events "
                "WHERE daily_digest_consumed_at IS NULL "
                "  AND id = :id"
            ),
            {"id": event_id},
        ).scalar()
        assert pending_after == 0

        # --- notification_preferences: round-trip + PK collision
        if uid is not None:
            connection.execute(
                text(
                    "INSERT INTO notification_preferences "
                    "(user_id, event_kind, enabled) "
                    "VALUES (:uid, :k, TRUE)"
                ),
                {"uid": uid, "k": "schedule.shift_edited"},
            )
            got = connection.execute(
                text(
                    "SELECT enabled FROM notification_preferences "
                    "WHERE user_id = :uid AND event_kind = :k"
                ),
                {"uid": uid, "k": "schedule.shift_edited"},
            ).scalar()
            assert got is True

            # PK collision should reject.
            sp_pk = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO notification_preferences "
                            "(user_id, event_kind, enabled) "
                            "VALUES (:uid, :k, FALSE)"
                        ),
                        {"uid": uid, "k": "schedule.shift_edited"},
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "notification_preferences PK accepted a duplicate"
                    )
            finally:
                sp_pk.rollback()

        # --- notification_jobs widening: new fields populate + backfill
        if uid is not None:
            # New-shape insert: subject_kind/subject_id + recipient_user_id,
            # appointment_id stays NULL since this is a staff event.
            job_id = connection.execute(
                text(
                    "INSERT INTO notification_jobs "
                    "(kind, channel, recipient, payload, "
                    " subject_kind, subject_id, recipient_user_id) "
                    "VALUES (:k, 'email', :to, '{}'::jsonb, "
                    "        'schedule_week', 999999, :uid) "
                    "RETURNING id"
                ),
                {"k": "test.staff", "to": "probe@example.com", "uid": uid},
            ).scalar()
            row = connection.execute(
                text(
                    "SELECT subject_kind, subject_id, recipient_user_id, "
                    "       appointment_id "
                    "FROM notification_jobs WHERE id = :id"
                ),
                {"id": job_id},
            ).first()
            assert row[0] == "schedule_week"
            assert row[1] == 999_999
            assert row[2] == uid
            assert row[3] is None

        # --- Digest dedup: same (recipient, kind, window) twice fails
        if uid is not None:
            digest_payload = '{"digest_window":"2026-05-18"}'
            connection.execute(
                text(
                    "INSERT INTO notification_jobs "
                    "(kind, channel, recipient, payload, "
                    " subject_kind, recipient_user_id, status) "
                    "VALUES (:k, 'email', :to, CAST(:p AS jsonb), "
                    "        'digest', :uid, 'pending')"
                ),
                {
                    "k": "digest.staff_daily",
                    "to": "probe@example.com",
                    "p": digest_payload,
                    "uid": uid,
                },
            )
            sp_dup = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO notification_jobs "
                            "(kind, channel, recipient, payload, "
                            " subject_kind, recipient_user_id, status) "
                            "VALUES (:k, 'email', :to, CAST(:p AS jsonb), "
                            "        'digest', :uid, 'pending')"
                        ),
                        {
                            "k": "digest.staff_daily",
                            "to": "probe@example.com",
                            "p": digest_payload,
                            "uid": uid,
                        },
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "uq_one_digest_per_user_per_window allowed a duplicate"
                    )
            finally:
                sp_dup.rollback()

            # Same (recipient, kind) on a DIFFERENT window is allowed.
            connection.execute(
                text(
                    "INSERT INTO notification_jobs "
                    "(kind, channel, recipient, payload, "
                    " subject_kind, recipient_user_id, status) "
                    "VALUES (:k, 'email', :to, CAST(:p AS jsonb), "
                    "        'digest', :uid, 'pending')"
                ),
                {
                    "k": "digest.staff_daily",
                    "to": "probe@example.com",
                    "p": '{"digest_window":"2026-05-19"}',
                    "uid": uid,
                },
            )

        # --- Backfill check: legacy appointment-bound rows should have
        # subject_kind / subject_id populated from appointment_id.
        legacy = connection.execute(
            text(
                "SELECT COUNT(*) FROM notification_jobs "
                "WHERE appointment_id IS NOT NULL "
                "  AND (subject_kind IS NULL OR subject_id IS NULL)"
            )
        ).scalar()
        assert legacy == 0, (
            f"backfill missed {legacy} appointment-bound row(s)"
        )
    finally:
        sp.rollback()
