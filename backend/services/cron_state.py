"""Cron health/status helpers.

Phase 7 Slice 2B-3 of the Sales Portal. Every attendance cron records
a `cron_run_state` row at the start of a tick and updates it at the
finish so admin can read "last run, scanned/changed, error" without
parsing logs.

The `record_run` context manager is the only writer. Crons wrap their
body with::

    with cron_state.record_run("attendance.auto_close") as run:
        run.scanned = ...
        run.changed = ...

Successful exits stamp `last_finished_at`, reset `last_error` and
`consecutive_failures`. Exceptions stamp `last_error`, increment
`consecutive_failures`, and re-raise so the outer worker logs the
trace and the next tick still fires.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import CronRunState


# Stable cron names. Crons must reference these constants, not raw
# strings, so the admin status surface and the smokes share one
# authoritative list.
AUTO_CLOSE = "attendance.auto_close"
PRE_CLOSE_REMINDER = "attendance.pre_close_reminder"
SELFIE_RETENTION = "attendance.selfie_retention"
WEBHOOK_RETENTION = "webhooks.retention"
ATTENDANCE_GEO_RETENTION = "attendance.geo_retention"
SCHEDULE_NO_SHOW = "schedule.no_show"
SCHEDULE_MISSING_OUT_PUNCH = "schedule.missing_out_punch"
SCHEDULE_REQUEST_EXPIRY = "schedule.shift_request_expiry"

ALL_CRON_NAMES = (
    AUTO_CLOSE,
    PRE_CLOSE_REMINDER,
    SELFIE_RETENTION,
    WEBHOOK_RETENTION,
    ATTENDANCE_GEO_RETENTION,
    SCHEDULE_NO_SHOW,
    SCHEDULE_MISSING_OUT_PUNCH,
    SCHEDULE_REQUEST_EXPIRY,
)


@dataclass
class _CronRunCtx:
    """Mutable counters the cron body fills in. After the context
    exits these land in the matching `cron_run_state` row."""

    name: str
    started_at: datetime
    scanned: int = 0
    changed: int = 0
    extra: dict = field(default_factory=dict)


def _upsert_state(db: Session, name: str) -> CronRunState:
    row = (
        db.query(CronRunState).filter(CronRunState.name == name).first()
    )
    if row is None:
        row = CronRunState(name=name)
        db.add(row)
        db.flush()
    return row


@contextmanager
def record_run(name: str):
    """Open a transient cron-run context. The caller mutates `scanned`
    and `changed` on the yielded object; the wrapper handles the row
    write on both the happy path and the exception path.

    Each context uses its own SessionLocal so the cron body's own
    SQLAlchemy session can commit/rollback independently — a cron
    that errors out and rolls back its main session must NOT also
    erase the failure stamp it just wrote.
    """
    started_at = datetime.now(timezone.utc)
    ctx = _CronRunCtx(name=name, started_at=started_at)
    state_db = SessionLocal()
    try:
        row = _upsert_state(state_db, name)
        row.last_started_at = started_at
        state_db.commit()
    finally:
        state_db.close()

    try:
        yield ctx
    except Exception as exc:
        state_db = SessionLocal()
        try:
            row = _upsert_state(state_db, name)
            row.last_finished_at = datetime.now(timezone.utc)
            row.last_scanned_count = ctx.scanned
            row.last_changed_count = ctx.changed
            row.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            state_db.commit()
        finally:
            state_db.close()
        raise
    else:
        state_db = SessionLocal()
        try:
            row = _upsert_state(state_db, name)
            row.last_finished_at = datetime.now(timezone.utc)
            row.last_scanned_count = ctx.scanned
            row.last_changed_count = ctx.changed
            row.last_error = None
            row.consecutive_failures = 0
            state_db.commit()
        finally:
            state_db.close()


def all_states(db: Session) -> list[dict]:
    """Return one entry per known cron name, joined with the
    `cron_run_state` row when present. Crons that have never run yet
    still appear in the response so the admin warning surface lists
    them as "never run" instead of silently omitting them."""

    rows = {
        r.name: r
        for r in db.query(CronRunState).all()
    }
    out = []
    for name in ALL_CRON_NAMES:
        row = rows.get(name)
        out.append(
            {
                "name": name,
                "last_started_at": (
                    row.last_started_at.astimezone(timezone.utc).isoformat()
                    if row and row.last_started_at is not None
                    else None
                ),
                "last_finished_at": (
                    row.last_finished_at.astimezone(timezone.utc).isoformat()
                    if row and row.last_finished_at is not None
                    else None
                ),
                "last_scanned_count": row.last_scanned_count if row else 0,
                "last_changed_count": row.last_changed_count if row else 0,
                "last_error": row.last_error if row else None,
                "consecutive_failures": (
                    row.consecutive_failures if row else 0
                ),
            }
        )
    return out
