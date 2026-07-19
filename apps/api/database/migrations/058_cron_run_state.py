"""Phase 7 Slice 2B-3 of the Sales Portal: cron health/status surface.

Adds two tables that back the cron-health requirement the doc locked
in: "Every attendance cron records last-run timestamp, rows scanned,
rows changed, and errors. Admin gets a visible warning if auto-close,
reminders, or retention have not run within the expected window."

  - `cron_run_state`: one row per cron name, updated in place after
    every tick. Carries the most recent `started_at` / `finished_at`,
    the scanned/changed counters, and the last error string. Backed
    by a UNIQUE on `name` so the cron writes are pure UPSERTs.
  - `attendance_pre_close_reminders`: idempotency table for the
    pre-close reminder cron. The user explicitly called out auto-
    close idempotency in Slice 2B-3 ("a serial smoke with two cron
    invocations against the same open punch is worth adding"); the
    same guarantee applies to pre-close. Each row pairs `(punch_id,
    cutoff_business_date)` so two ticks against the same shift's
    cutoff cannot fire two reminders. The pair is UNIQUE.

Auto-close itself does not need a separate idempotency table — its
output is a new `staff_punches` row with `direction='out'` plus the
session pair, and `services.attendance_close.run_auto_close` checks
`current_status` before inserting (a session that's already closed
can't be auto-closed again). The smoke proves both ticks land the
same row count.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE cron_run_state (
                id SERIAL PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                last_started_at TIMESTAMPTZ NULL,
                last_finished_at TIMESTAMPTZ NULL,
                last_scanned_count INTEGER NOT NULL DEFAULT 0,
                last_changed_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_cron_run_state_name UNIQUE (name)
            )
            """
        )
    )

    connection.execute(
        text(
            """
            CREATE TABLE attendance_pre_close_reminders (
                id BIGSERIAL PRIMARY KEY,
                punch_id BIGINT NOT NULL
                    REFERENCES staff_punches(id) ON DELETE CASCADE,
                cutoff_business_date DATE NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_pre_close_punch_cutoff
                    UNIQUE (punch_id, cutoff_business_date)
            )
            """
        )
    )

    # Audit hook for the pre-close reminder send so the timeline
    # explains where a reminder came from. The retention + auto-close
    # crons already write into `staff_punch_audit_events` directly.
    connection.execute(
        text(
            "CREATE INDEX idx_pre_close_reminders_sent "
            "ON attendance_pre_close_reminders(sent_at DESC)"
        )
    )

    # ---- DML probes per the project rule ----

    # Round-trip cron_run_state: insert, update via UPSERT pattern,
    # confirm the unique name.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "INSERT INTO cron_run_state "
                "(name, last_scanned_count, last_changed_count) "
                "VALUES ('__058_probe_cron__', 5, 2)"
            )
        )
        # UPSERT the same name → update path.
        connection.execute(
            text(
                """
                INSERT INTO cron_run_state
                    (name, last_scanned_count, last_changed_count)
                VALUES ('__058_probe_cron__', 9, 4)
                ON CONFLICT (name) DO UPDATE
                    SET last_scanned_count = EXCLUDED.last_scanned_count,
                        last_changed_count = EXCLUDED.last_changed_count,
                        updated_at = NOW()
                """
            )
        )
        row = connection.execute(
            text(
                "SELECT last_scanned_count, last_changed_count "
                "FROM cron_run_state WHERE name = '__058_probe_cron__'"
            )
        ).first()
        assert row is not None and row[0] == 9 and row[1] == 4, row

        # Duplicate name → UNIQUE rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO cron_run_state (name) "
                        "VALUES ('__058_probe_cron__')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "cron_run_state UNIQUE accepted duplicate name"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()

    # Round-trip attendance_pre_close_reminders against a real punch.
    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    if user_row is None:
        return
    user_id = int(user_row[0])

    sp = connection.begin_nested()
    try:
        in_id = connection.execute(
            text(
                "INSERT INTO staff_punches (user_id, direction, status) "
                "VALUES (:uid, 'in', 'unscheduled') RETURNING id"
            ),
            {"uid": user_id},
        ).scalar()

        connection.execute(
            text(
                "INSERT INTO attendance_pre_close_reminders "
                "(punch_id, cutoff_business_date) "
                "VALUES (:pid, '2026-05-08')"
            ),
            {"pid": in_id},
        )

        # Same (punch_id, cutoff_business_date) → UNIQUE rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO attendance_pre_close_reminders "
                        "(punch_id, cutoff_business_date) "
                        "VALUES (:pid, '2026-05-08')"
                    ),
                    {"pid": in_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "pre_close_reminders UNIQUE accepted duplicate "
                    "(punch_id, cutoff_business_date)"
                )
        finally:
            sp_inner.rollback()

        # Different cutoff dates against the same punch are allowed
        # (a multi-day open session may need multiple reminders).
        connection.execute(
            text(
                "INSERT INTO attendance_pre_close_reminders "
                "(punch_id, cutoff_business_date) "
                "VALUES (:pid, '2026-05-09')"
            ),
            {"pid": in_id},
        )
    finally:
        sp.rollback()
