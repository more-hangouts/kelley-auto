"""Admin open-shift management (Scheduling Phase 3).

Endpoints under `/api/admin/schedule/open-shifts`:

  GET  /                  — posts in a date range, optional status filter
  POST /                  — post an open shift
  POST /{post_id}/cancel  — cancel an open post

Pickup approval is not here — a pickup is a `staff_shift_requests` row, so
it's approved through `/api/admin/schedule/shift-requests/{id}/decide`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import open_shifts
from services.open_shifts import OpenShiftPostError

router = APIRouter()

MAX_RANGE_DAYS = 62


def _raise(exc: OpenShiftPostError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(
        status_code=exc.http_status, detail=detail
    ) from exc


class OpenShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date
    starts_at_local: datetime
    ends_at_local: datetime
    late_grace_minutes: int | None = Field(default=None, ge=0, le=120)
    manager_notes: str | None = Field(default=None, max_length=500)


@router.get("")
def list_open_shifts(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    from_date: date = Query(...),
    to_date: date = Query(...),
    status_in: list[str] | None = Query(default=None, alias="status"),
) -> dict:
    if to_date < from_date:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_date_range"}
        )
    if (to_date - from_date) > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail={"code": "date_range_too_wide", "max_days": MAX_RANGE_DAYS},
        )
    try:
        posts = open_shifts.list_admin(
            db, from_date=from_date, to_date=to_date, statuses=status_in
        )
    except OpenShiftPostError as exc:
        _raise(exc)
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "posts": posts,
    }


@router.post("", status_code=201)
def create_open_shift(
    payload: OpenShiftCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = open_shifts.create_post(
            db,
            actor_user_id=admin.id,
            business_date_=payload.business_date,
            starts_at_local=payload.starts_at_local,
            ends_at_local=payload.ends_at_local,
            late_grace_minutes=payload.late_grace_minutes,
            manager_notes=payload.manager_notes,
        )
    except OpenShiftPostError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{post_id}/cancel")
def cancel_open_shift(
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = open_shifts.cancel_post(db, post_id=post_id)
    except OpenShiftPostError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
