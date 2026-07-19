"""Sales-side time-off router (Phase 8 Slice C).

Stylist-facing endpoints under `/api/sales/time-off`:

  GET  /                  — own requests, newest first
  POST /                  — file a new request (status starts pending)
  POST /{id}/cancel       — cancel own pending request

Per the user's Slice C enforcement points: cancel uses POST (not
DELETE) so the row is preserved with `status='cancelled'` and an
audit row, sales users can only read/cancel their own, and every
write goes through `services.time_off` which records a
`time_off_decision_events` row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
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


class TimeOffSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=500)


@router.get("")
def list_my_time_off(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    rows = time_off.list_for_user(db, user_id=sales_user.id)
    return {"requests": rows}


@router.post("")
def submit_my_time_off(
    payload: TimeOffSubmitRequest,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = time_off.submit_request(
            db,
            user=sales_user,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            reason=payload.reason,
        )
    except TimeOffServiceError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{request_id}/cancel")
def cancel_my_time_off(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """POST verb (not DELETE) so the row is preserved with status
    'cancelled' and the audit row stays on the timeline. Per the
    user's Slice C enforcement #1."""
    try:
        result = time_off.cancel_request(
            db, user=sales_user, request_id=request_id
        )
    except TimeOffServiceError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
