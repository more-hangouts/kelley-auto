"""Cron health/status admin surface (Phase 7 Slice 2B-3).

Exposes the most recent run state for every attendance cron under
`/api/admin/cron-health`. Owner sees this in the admin UI alongside
the attendance review tools so a missing cron tick (auto-close failed
last night, retention hasn't run in a week) is visible without log
spelunking.

Read-only by design. The crons themselves write to `cron_run_state`
through `services.cron_state.record_run`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import cron_state

router = APIRouter()


# Each cron is expected to fire roughly once a day (the daily worker
# loop). A longer gap than this gets a "stale" flag in the response so
# the admin UI can highlight it. Two days is generous enough to cover
# a single missed tick without crying wolf.
STALE_AFTER = timedelta(days=2)


@router.get("")
def get_cron_health(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    states = cron_state.all_states(db)
    now = datetime.now(timezone.utc)
    out = []
    for s in states:
        finished = s["last_finished_at"]
        stale = True
        if finished is not None:
            try:
                finished_dt = datetime.fromisoformat(finished)
            except ValueError:
                finished_dt = None
            if finished_dt is not None:
                stale = (now - finished_dt) > STALE_AFTER
        out.append(
            {
                **s,
                "is_stale": stale,
                "ok": (
                    s["last_error"] is None
                    and not stale
                    and s["consecutive_failures"] == 0
                ),
            }
        )
    return {"crons": out, "stale_after_seconds": int(STALE_AFTER.total_seconds())}
