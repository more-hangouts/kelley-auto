"""Dated notes on a deal, with optional follow-up reminders.

Until now a deal's notes were a single free-text column (``events.notes``)
that the admin Overview rendered as lines. There was no author, no
timestamp, no history — a rep who wrote "called again, call back Thursday"
overwrote whatever the last rep wrote, and nothing could remind anyone on
Thursday.

This adds ``event_notes``: one row per note, authored and timestamped, so
the detail page can show the running series reps actually work from.

Reminder fields live ON THE NOTE rather than in a separate table. A
follow-up is always *about* something a rep just wrote down, so splitting
them would mean a join and a lifecycle (edit the note, orphan the
reminder) with nothing to gain:

  * ``remind_at``      — when to nudge (NULL = plain note, the common case)
  * ``remind_user_id`` — who gets nudged; CHECK-enforced present whenever
    ``remind_at`` is set, so a reminder can never be undeliverable
  * ``remind_channel`` — 'email' or 'sms'. Both are accepted by the CHECK
    so adding SMS later needs no migration; the service currently rejects
    'sms' at write time because ``users`` has no phone column yet.
  * ``reminder_sent_at`` — delivery stamp, and the idempotency guard: the
    pass only claims rows where it IS NULL, so a second run never
    re-sends.
  * ``resolved_at`` — the rep saying "handled", which retires a reminder
    whether or not it already fired.

``author_display_name`` snapshots the writer's name (the pattern from
contact_call_attempts, migration 098): the FK is ON DELETE SET NULL so a
departed rep's notes keep their byline.

The partial index ``idx_event_notes_due`` is shaped for the one hot query
the reminder pass runs — due, unsent, unresolved, undeleted — so the pass
stays an index scan as the notes table grows.

BACKFILL: every event with a non-empty ``events.notes`` gets one seeded
note carrying that text, stamped with the event's own ``created_at`` so
the timeline starts where the deal did. The legacy column is NOT dropped
— the Overview tab and any other reader keep working, and dropping a
column with live readers is a separate, riskier change.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # --- 1. Table -------------------------------------------------------
    connection.execute(
        text(
            """
            CREATE TABLE event_notes (
                id                  BIGSERIAL PRIMARY KEY,
                event_id            INTEGER NOT NULL
                                        REFERENCES events(id) ON DELETE CASCADE,
                body                TEXT NOT NULL,
                author_user_id      INTEGER
                                        REFERENCES users(id) ON DELETE SET NULL,
                author_display_name VARCHAR(200),
                remind_at           TIMESTAMPTZ,
                remind_user_id      INTEGER
                                        REFERENCES users(id) ON DELETE SET NULL,
                remind_channel      VARCHAR(16) NOT NULL DEFAULT 'email',
                reminder_sent_at    TIMESTAMPTZ,
                resolved_at         TIMESTAMPTZ,
                resolved_by_user_id INTEGER
                                        REFERENCES users(id) ON DELETE SET NULL,
                edited_at           TIMESTAMPTZ,
                deleted_at          TIMESTAMPTZ,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_event_notes_body_not_blank
                    CHECK (btrim(body) <> ''),
                CONSTRAINT chk_event_notes_remind_channel
                    CHECK (remind_channel IN ('email', 'sms')),
                -- A reminder with nobody to remind is a silent dead end.
                CONSTRAINT chk_event_notes_reminder_has_target
                    CHECK (remind_at IS NULL OR remind_user_id IS NOT NULL)
            )
            """
        )
    )

    # --- 2. Indexes -----------------------------------------------------
    # Timeline read: newest note first for one deal.
    connection.execute(
        text(
            "CREATE INDEX idx_event_notes_event_created "
            "ON event_notes(event_id, created_at DESC) "
            "WHERE deleted_at IS NULL"
        )
    )
    # The reminder pass's claim query, exactly.
    connection.execute(
        text(
            "CREATE INDEX idx_event_notes_due "
            "ON event_notes(remind_at) "
            "WHERE remind_at IS NOT NULL "
            "  AND reminder_sent_at IS NULL "
            "  AND resolved_at IS NULL "
            "  AND deleted_at IS NULL"
        )
    )
    # "My open follow-ups" for a rep.
    connection.execute(
        text(
            "CREATE INDEX idx_event_notes_remind_user "
            "ON event_notes(remind_user_id, remind_at) "
            "WHERE remind_at IS NOT NULL "
            "  AND resolved_at IS NULL "
            "  AND deleted_at IS NULL"
        )
    )

    # --- 3. Backfill the legacy blob into a first note ------------------
    result = connection.execute(
        text(
            """
            INSERT INTO event_notes (event_id, body, created_at, updated_at)
            SELECT id, btrim(notes), created_at, NOW()
              FROM events
             WHERE notes IS NOT NULL
               AND btrim(notes) <> ''
               AND deleted_at IS NULL
            """
        )
    )
    print(f"    backfilled {result.rowcount} legacy event note(s)")

    # --- 4. DML probes (savepoint, always rolled back) ------------------
    sp = connection.begin_nested()
    try:
        contact_id = connection.execute(
            text(
                "INSERT INTO contacts (display_name) "
                "VALUES ('Note Probe Buyer') RETURNING id"
            )
        ).scalar()
        event_id = connection.execute(
            text(
                """
                INSERT INTO events
                    (primary_contact_id, event_type, event_name, status)
                VALUES (:cid, 'vehicle_sale', 'Note Probe Deal', 'contacted')
                RETURNING id
                """
            ),
            {"cid": contact_id},
        ).scalar()
        user_id = connection.execute(
            text(
                """
                INSERT INTO users (username, email, hashed_password, role)
                VALUES ('note_probe_user', 'note.probe@example.com', 'x', 'sales')
                RETURNING id
                """
            )
        ).scalar()

        # A plain note round-trips.
        note_id = connection.execute(
            text(
                """
                INSERT INTO event_notes
                    (event_id, body, author_user_id, author_display_name)
                VALUES (:eid, 'Called again, no answer.', :uid, 'Probe Rep')
                RETURNING id
                """
            ),
            {"eid": event_id, "uid": user_id},
        ).scalar()
        row = connection.execute(
            text(
                "SELECT body, remind_at, remind_channel, deleted_at "
                "FROM event_notes WHERE id = :id"
            ),
            {"id": note_id},
        ).first()
        assert row[0] == "Called again, no answer.", "note body round-trip"
        assert row[1] is None, "plain note has no reminder"
        assert row[2] == "email", "remind_channel defaults to email"
        assert row[3] is None, "note starts undeleted"

        # A note carrying a reminder round-trips, both channels allowed.
        for channel in ("email", "sms"):
            connection.execute(
                text(
                    """
                    INSERT INTO event_notes
                        (event_id, body, remind_at, remind_user_id, remind_channel)
                    VALUES (:eid, 'Call back Thursday.',
                            NOW() + INTERVAL '2 days', :uid, :ch)
                    """
                ),
                {"eid": event_id, "uid": user_id, "ch": channel},
            )

        # Deleting the deal takes its notes with it (CASCADE).
        sp_cascade = connection.begin_nested()
        connection.execute(
            text("DELETE FROM events WHERE id = :id"), {"id": event_id}
        )
        left = connection.execute(
            text("SELECT COUNT(*) FROM event_notes WHERE event_id = :id"),
            {"id": event_id},
        ).scalar()
        assert left == 0, "notes cascade with their event"
        sp_cascade.rollback()

        def _rejects(sql: str, params: dict, label: str) -> None:
            ok = False
            sp2 = connection.begin_nested()
            try:
                connection.execute(text(sql), params)
            except Exception:
                ok = True
                sp2.rollback()
            assert ok, f"{label} must be rejected"

        _rejects(
            "INSERT INTO event_notes (event_id, body) VALUES (:eid, '   ')",
            {"eid": event_id},
            "blank note body",
        )
        _rejects(
            "INSERT INTO event_notes (event_id, body, remind_at, remind_user_id) "
            "VALUES (:eid, 'orphan reminder', NOW(), NULL)",
            {"eid": event_id},
            "reminder with no recipient",
        )
        _rejects(
            "INSERT INTO event_notes (event_id, body, remind_channel) "
            "VALUES (:eid, 'bad channel', 'carrier_pigeon')",
            {"eid": event_id},
            "unknown remind_channel",
        )
    finally:
        sp.rollback()
