"""Phase C4: append-only triggers on the five evidentiary tables.

Tables locked down:

  - ``activity_log``
  - ``staff_punch_audit_events``
  - ``time_off_decision_events``
  - ``refund_events``
  - ``event_status_change_events``

Threat model: the application currently treats these as append-only by
convention. Any compromise of the DB role — or an inattentive ad-hoc
admin query — could silently rewrite or remove history. C4 elevates
the convention to schema enforcement: a `BEFORE UPDATE OR DELETE`
trigger on each table raises ``CheckViolation`` unless the session
explicitly opts in via the bypass GUC.

Bypass: ``SET audit_tables.allow_mutation = on`` on the session lets
that session (and only that session) perform UPDATE/DELETE. The
application never sets this. Test cleanup sessions set it via the
env-gated connect-event listener in `database/connection.py`. Ops
who need to perform a real one-off correction can:

    BEGIN;
    SET LOCAL audit_tables.allow_mutation = on;
    -- corrective DELETE/UPDATE here
    COMMIT;

`current_setting(..., true)` is used so a missing GUC returns NULL
rather than raising — that's what "block by default" looks like.

INSERT is unaffected. The trigger only fires on UPDATE and DELETE.
ON DELETE CASCADE coming from a parent (e.g., deleting an event
cascades activity_log rows) still triggers the per-row DELETE check —
because cascades are real DELETE statements at the storage layer. So
test cleanup that drops parents must also use the bypass.
"""

from sqlalchemy import text


_TABLES = (
    "activity_log",
    "staff_punch_audit_events",
    "time_off_decision_events",
    "refund_events",
    "event_status_change_events",
)


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION enforce_audit_append_only()
            RETURNS trigger AS $$
            BEGIN
                IF coalesce(current_setting('audit_tables.allow_mutation', true), '') = 'on' THEN
                    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
                END IF;
                RAISE EXCEPTION '% on table % is forbidden: audit tables are append-only',
                        TG_OP, TG_TABLE_NAME
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'audit_table_append_only',
                          HINT = 'Set audit_tables.allow_mutation = on within a transaction for a deliberate one-off correction.';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )

    for tbl in _TABLES:
        connection.execute(
            text(
                f"DROP TRIGGER IF EXISTS trg_{tbl}_append_only ON {tbl}"
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TRIGGER trg_{tbl}_append_only
                BEFORE UPDATE OR DELETE ON {tbl}
                FOR EACH ROW
                EXECUTE FUNCTION enforce_audit_append_only()
                """
            )
        )
