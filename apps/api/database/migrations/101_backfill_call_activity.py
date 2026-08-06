"""Backfill deal-linked call attempts onto the Activity timeline.

Calls have always been tracked (``contact_call_attempts``, migration 098)
but never appeared on a deal's Activity tab, which reads only
``activity_log``. A rep looking at Tareka White's deal saw eleven calls in
the Recent Calls widget and an Activity tab that never mentioned the
phone ringing.

``modules/analytics/services/call_attempts.py`` now mirrors both
milestones (``call.initiated``, ``call.outcome_recorded``) as calls
happen. This migration gives the same treatment to the calls already on
record so existing deals aren't left with a hole in their timeline.

Scope and shape:

  * Only attempts with ``event_id IS NOT NULL`` — ``activity_log.event_id``
    is NOT NULL, and a contact-only call has no deal timeline to land on.
  * Only attempts whose deal still exists (the FK is ON DELETE CASCADE, so
    a stale reference would fail the insert).
  * ``created_at`` is copied from the call, not stamped NOW(), so the
    backfilled rows sort into the timeline where the calls actually
    happened.
  * One ``call.initiated`` per attempt; plus one ``call.outcome_recorded``
    for attempts a rep has since reported an outcome on (``outcome <>
    'call_initiated'``), timestamped ``updated_at`` — when the outcome was
    reported.
  * Payload carries {attempt_id, outcome, source} and deliberately NO
    phone number or call notes, matching the live write path — activity_log
    forbids PII in metadata.

Idempotent: rows are skipped when an activity row already exists for that
(subject_id, activity_type) pair, so a re-run after a partial failure — or
a replay onto a DB where the service already mirrored some calls — adds
nothing twice.

``activity_log`` carries an append-only trigger; it blocks UPDATE/DELETE,
not INSERT, so this backfill is permitted.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # --- 1. call.initiated, one per deal-linked attempt -----------------
    initiated = connection.execute(
        text(
            """
            INSERT INTO activity_log
                (event_id, actor_kind, actor_user_id, actor_display_name,
                 activity_type, subject_kind, subject_id, payload, created_at)
            SELECT
                a.event_id,
                CASE WHEN a.salesperson_user_id IS NULL THEN 'system'
                     ELSE 'staff' END,
                a.salesperson_user_id,
                a.salesperson_display_name,
                'call.initiated',
                'contact_call_attempt',
                a.id,
                jsonb_build_object(
                    'attempt_id', a.id,
                    'outcome', a.outcome,
                    'source', a.source,
                    'backfilled', true
                ),
                a.created_at
              FROM contact_call_attempts a
              JOIN events e ON e.id = a.event_id
             WHERE a.event_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM activity_log l
                    WHERE l.subject_kind = 'contact_call_attempt'
                      AND l.subject_id = a.id
                      AND l.activity_type = 'call.initiated'
               )
            """
        )
    )
    print(f"    backfilled {initiated.rowcount} call.initiated row(s)")

    # --- 2. call.outcome_recorded for attempts a rep has reported on ----
    outcomes = connection.execute(
        text(
            """
            INSERT INTO activity_log
                (event_id, actor_kind, actor_user_id, actor_display_name,
                 activity_type, subject_kind, subject_id, payload, created_at)
            SELECT
                a.event_id,
                CASE WHEN a.salesperson_user_id IS NULL THEN 'system'
                     ELSE 'staff' END,
                a.salesperson_user_id,
                a.salesperson_display_name,
                'call.outcome_recorded',
                'contact_call_attempt',
                a.id,
                jsonb_build_object(
                    'attempt_id', a.id,
                    'outcome', a.outcome,
                    'source', a.source,
                    'backfilled', true
                ),
                a.updated_at
              FROM contact_call_attempts a
              JOIN events e ON e.id = a.event_id
             WHERE a.event_id IS NOT NULL
               AND a.outcome <> 'call_initiated'
               AND NOT EXISTS (
                   SELECT 1 FROM activity_log l
                    WHERE l.subject_kind = 'contact_call_attempt'
                      AND l.subject_id = a.id
                      AND l.activity_type = 'call.outcome_recorded'
               )
            """
        )
    )
    print(f"    backfilled {outcomes.rowcount} call.outcome_recorded row(s)")

    # --- 3. Assertions (read-only; the inserts above are the payload) ---
    orphaned = connection.execute(
        text(
            """
            SELECT COUNT(*)
              FROM activity_log l
             WHERE l.subject_kind = 'contact_call_attempt'
               AND NOT EXISTS (
                   SELECT 1 FROM contact_call_attempts a WHERE a.id = l.subject_id
               )
            """
        )
    ).scalar()
    assert orphaned == 0, "every mirrored row points at a live call attempt"

    missing = connection.execute(
        text(
            """
            SELECT COUNT(*)
              FROM contact_call_attempts a
              JOIN events e ON e.id = a.event_id
             WHERE a.event_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM activity_log l
                    WHERE l.subject_kind = 'contact_call_attempt'
                      AND l.subject_id = a.id
                      AND l.activity_type = 'call.initiated'
               )
            """
        )
    ).scalar()
    assert missing == 0, "every deal-linked call attempt has a timeline row"
