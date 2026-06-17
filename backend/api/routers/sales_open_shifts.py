"""Sales-side open-shift board (Scheduling Phase 3).

Endpoints under `/api/sales/schedule/open-shifts`:

  GET  /                  — open posts in a date range (sanitized board)
  POST /{post_id}/claim   — claim a post (creates a pending pickup request)

Open posts are visible to all active sales staff (Gate 3). The response
is allowlisted in the service — no manager/audit fields leak.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import open_shifts, staff_shift_requests
from services.open_shifts import OpenShiftPostError
from services.staff_shift_requests import StaffShiftRequestError

router = APIRouter()

MAX_RANGE_DAYS = 31


def _validate_range(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_date_range"}
        )
    if (to_date - from_date) > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail={"code": "date_range_too_wide", "max_days": MAX_RANGE_DAYS},
        )


@router.get("")
def list_open_shifts(
    db: Annotated[Session, Depends(get_db)],
    _sales_user: Annotated[User, Depends(require_sales_scope)],
    from_date: date = Query(...),
    to_date: date = Query(...),
) -> dict:
    _validate_range(from_date, to_date)
    try:
        posts = open_shifts.list_open_for_sales(
            db, from_date=from_date, to_date=to_date
        )
    except OpenShiftPostError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code}
        ) from exc
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "posts": posts,
    }


@router.post("/{post_id}/claim")
def claim_open_shift(
    post_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = staff_shift_requests.claim_open_shift(
            db, user=sales_user, post_id=post_id
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        detail: dict[str, object] = {"code": exc.code}
        detail.update(exc.extra)
        raise HTTPException(
            status_code=exc.http_status, detail=detail
        ) from exc
    db.commit()
    return result
