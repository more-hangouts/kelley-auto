"""Smoke for C4: append-only triggers on the five audit tables.

Covers:
  - INSERT into each audit table still works (the trigger fires on
    UPDATE/DELETE only).
  - Without bypass, UPDATE on a freshly inserted row raises
    CheckViolation with the documented message.
  - Without bypass, DELETE on a freshly inserted row raises
    CheckViolation.
  - With session-local bypass (`SET LOCAL audit_tables.allow_mutation
    = on`), UPDATE and DELETE both succeed — this is the documented
    ops correction path.
  - The connect-event listener in `database/connection.py` is the
    same opt-in mechanism the rest of the smoke suite relies on; the
    smoke runs WITH `ALLOW_AUDIT_MUTATION=1` set in os.environ, so
    SessionLocal()'d connections get the GUC for free. The
    "without bypass" scenarios above use a fresh raw psycopg2
    connection that does NOT get the listener, to prove production
    code paths (which also lack the env var) hit the trigger.

Run with: venv/bin/python tests/test_audit_append_only_smoke.py
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)
# C4 bypass: every smoke that cleans up audit rows sets this so the
# SessionLocal connect listener emits SET audit_tables.allow_mutation = on
# on each pooled connection. This smoke ALSO needs it set because it
# inserts then deletes its own probe rows.
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")

import psycopg2  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from config.settings import DATABASE_URL  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    EventStatusChangeEvent,
)


def _raw_connection_without_bypass():
    """Open a fresh psycopg2 connection bypassing the SQLAlchemy engine.

    The engine has the connect-event listener registered (because the
    env var is set above), which would emit the bypass GUC on every
    pooled connection. We need a connection that does NOT have the
    listener so we can verify the trigger really does block UPDATE/
    DELETE on a code path that hasn't opted in.
    """
    return psycopg2.connect(DATABASE_URL)


_event_ids: list[int] = []
_contact_ids: list[int] = []


def _seed_event() -> int:
    """Seed an event so we can write a status-change audit row against it."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        c = Contact(
            display_name=f"C4 Customer {suffix}",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"c4-{suffix}@example.com",
        )
        db.add(c)
        db.flush()
        _contact_ids.append(c.id)

        e = Event(
            primary_contact_id=c.id,
            event_type="quinceanera",
            event_name=f"C4 Audit {suffix}",
            event_date=date.today() + timedelta(days=200),
            status="lead",
        )
        db.add(e)
        db.flush()
        _event_ids.append(e.id)
        db.commit()
        return e.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM event_status_change_events WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        db.commit()
    finally:
        db.close()


try:
    event_id = _seed_event()

    # ---------------------------------------------------------------------
    # 1. INSERT into each of the five audit tables still works. We need
    # a "host" row for each: activity_log + event_status_change_events
    # need an event_id; staff_punch_audit_events needs an existing punch
    # (we skip that — its insert path is exercised by clock smokes);
    # time_off_decision_events needs an existing request; refund_events
    # needs an existing payment.
    #
    # To keep this smoke self-contained, we INSERT into the two tables
    # whose FK chain we already have (activity_log + status_change), and
    # rely on the unified trigger function to mean the same block/allow
    # logic applies to the other three tables (proved by their CREATE
    # TRIGGER in migration 063 using the same function).
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO activity_log "
                "(event_id, actor_kind, activity_type, subject_kind, subject_id, payload, actor_display_name) "
                "VALUES (:eid, 'system', 'c4.smoke', 'event', :eid, '{}'::jsonb, 'C4 Smoke')"
            ),
            {"eid": event_id},
        )
        db.add(
            EventStatusChangeEvent(
                event_id=event_id,
                from_status="lead",
                to_status="consulted",
                changed_at=datetime.now(timezone.utc),
                notes="C4 smoke probe",
            )
        )
        db.commit()
        activity_id = db.execute(
            sql_text(
                "SELECT id FROM activity_log "
                "WHERE event_id = :eid AND activity_type = 'c4.smoke' "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"eid": event_id},
        ).scalar()
        status_change_id = db.execute(
            sql_text(
                "SELECT id FROM event_status_change_events "
                "WHERE event_id = :eid ORDER BY id DESC LIMIT 1"
            ),
            {"eid": event_id},
        ).scalar()
        assert activity_id and status_change_id, (activity_id, status_change_id)
    finally:
        db.close()
    print("INSERT into activity_log + event_status_change_events ok")

    # ---------------------------------------------------------------------
    # 2. Without bypass (raw psycopg2 — no connect listener fired),
    # UPDATE and DELETE both raise CheckViolation with the documented
    # message. Test each of the five tables via the trigger metadata —
    # we only have probe rows in two of them, so the other three are
    # exercised via a no-op UPDATE that targets a guaranteed-non-existent
    # row (`WHERE id = -1`) — the WHERE clause matches zero rows so the
    # trigger does NOT fire, which would falsely look like a pass. So
    # we limit the per-table no-bypass test to the two we actually
    # have rows in, and rely on schema introspection for the other
    # three (verify the trigger exists on each table).
    # ---------------------------------------------------------------------
    for op, sql in [
        ("UPDATE", "UPDATE activity_log SET payload = '{}'::jsonb WHERE id = %s"),
        ("DELETE", "DELETE FROM activity_log WHERE id = %s"),
        ("UPDATE", "UPDATE event_status_change_events SET notes = 'changed' WHERE id = %s"),
        ("DELETE", "DELETE FROM event_status_change_events WHERE id = %s"),
    ]:
        target = activity_id if "activity_log" in sql else status_change_id
        conn = _raw_connection_without_bypass()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, (target,))
                    conn.commit()
                    raise AssertionError(
                        f"{op} on {sql} should have been blocked by the trigger"
                    )
                except psycopg2.errors.CheckViolation as exc:
                    conn.rollback()
                    msg = str(exc).splitlines()[0]
                    assert "audit tables are append-only" in msg, msg
                    assert op in msg, (op, msg)
        finally:
            conn.close()
    print("no-bypass UPDATE + DELETE on both probe tables raises CheckViolation ok")

    # ---------------------------------------------------------------------
    # 3. Schema introspection: confirm the trigger exists on every one of
    # the five tables. Skipping a CREATE TRIGGER for any of them would
    # be a silent gap; the migration writes all five from the same loop,
    # but this assertion guarantees future ALTER scripts cannot quietly
    # drop one without the smoke noticing.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        expected = {
            "activity_log",
            "staff_punch_audit_events",
            "time_off_decision_events",
            "refund_events",
            "event_status_change_events",
        }
        rows = db.execute(
            sql_text(
                """
                SELECT c.relname AS tablename
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE t.tgname LIKE 'trg_%_append_only'
                  AND NOT t.tgisinternal
                """
            )
        ).all()
        found = {r.tablename for r in rows}
        missing = expected - found
        assert not missing, f"trigger missing on tables: {missing}"
    finally:
        db.close()
    print(f"all 5 append-only triggers present: {sorted(expected)} ok")

    # ---------------------------------------------------------------------
    # 4. With session-local bypass, UPDATE and DELETE succeed. This is
    # both the test cleanup path and the documented ops correction
    # path. The SessionLocal connect-listener already issues
    # SET audit_tables.allow_mutation = on because we set the env var
    # at the top of this file, so the default sessions in this smoke
    # are themselves bypassing — we use that to clean up.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE activity_log SET payload = '{\"c4\":\"updated\"}'::jsonb WHERE id = :i"),
            {"i": activity_id},
        )
        db.execute(
            sql_text("DELETE FROM activity_log WHERE id = :i"),
            {"i": activity_id},
        )
        db.execute(
            sql_text("DELETE FROM event_status_change_events WHERE id = :i"),
            {"i": status_change_id},
        )
        db.commit()
        remaining = db.execute(
            sql_text("SELECT COUNT(*) FROM activity_log WHERE id = :i"),
            {"i": activity_id},
        ).scalar()
        assert remaining == 0, remaining
    finally:
        db.close()
    print("with-bypass UPDATE+DELETE succeeds ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_audit_append_only_smoke OK")
