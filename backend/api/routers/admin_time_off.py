"""Admin time-off router (Phase 8 Slice C).

Owner-side surface under `/api/admin/time-off`:

  GET  /                      — date-bounded list with optional staff +
                                status filters
  POST /{id}/decide           — approve or deny a pending request
  POST /{id}/amend            — owner edits proposed times before
                                approval (status stays 'pending')

Per the user's Slice C enforcements: all reads are date-bounded
(both `from_date` and `to_date` required), every state transition
writes a `time_off_decision_events` row, and any action on a
terminal-status request returns 409.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import time_off
from services.time_off import TimeOffServiceError

router = APIRouter()


def _raise(exc: TimeOffServiceError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(
        status_code=exc.http_status, detail=detail
    ) from exc


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "denied"]
    decision_notes: str | None = Field(default=None, max_length=500)


class AmendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    decision_notes: str | None = Field(default=None, max_length=500)


def _local_day_to_utc(d: date, *, end_of_day: bool) -> datetime:
    tz = ZoneInfo(APP_TIMEZONE)
    if end_of_day:
        local = datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
    else:
        local = datetime.combine(d, time.min, tzinfo=tz)
    return local.astimezone(timezone.utc)


@router.get("")
def list_time_off(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    from_date: date = Query(...),
    to_date: date = Query(...),
    user_id: int | None = Query(default=None),
    status_in: list[str] | None = Query(default=None, alias="status"),
) -> dict:
    """Required date range — no unbounded list endpoint per the user's
    enforcement #4. The service intersects on `[starts_at < to,
    ends_at > from]` so requests spanning the window are included."""
    if to_date < from_date:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_date_range"}
        )
    from_utc = _local_day_to_utc(from_date, end_of_day=False)
    to_utc = _local_day_to_utc(to_date, end_of_day=True)
    try:
        rows = time_off.list_admin(
            db,
            from_date=from_utc,
            to_date=to_utc,
            user_id=user_id,
            statuses=status_in,
        )
    except TimeOffServiceError as exc:
        _raise(exc)
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "requests": rows,
    }


@router.post("/{request_id}/decide")
def decide(
    request_id: int,
    payload: DecideRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = time_off.decide_request(
            db,
            actor_user_id=admin.id,
            request_id=request_id,
            decision=payload.status,
            decision_notes=payload.decision_notes,
        )
    except TimeOffServiceError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{request_id}/amend")
def amend(
    request_id: int,
    payload: AmendRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = time_off.amend_request(
            db,
            actor_user_id=admin.id,
            request_id=request_id,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            decision_notes=payload.decision_notes,
        )
    except TimeOffServiceError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
