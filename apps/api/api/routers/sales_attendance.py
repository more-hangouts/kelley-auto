"""Sales-side attendance routes (Phase 7 Slice 2B-2).

Stylist-facing surface mounted at `/api/sales/attendance`. Two
workflows:

  - Confirm an auto-closed punch ("System closed at 9pm, that's
    accurate") so it leaves the owner's review queue.
  - Submit + cancel a missed-punch correction request ("I forgot to
    clock out, I actually left at 6:15"). Owner approves or denies
    from the admin attendance review queue.

All routes require a sales-scope token. Stylists cannot decide their
own correction requests — `decide` is admin-only by design so the
review trail has an explicit owner/staff handoff.

The user explicitly directed Slice 2B-2 to keep correction approval
*separate* from manual punch adjustment so the timeline stays
understandable; this router exposes only the stylist halves of those
two flows.
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
from services import attendance_review
from services.attendance_review import AttendanceReviewError

router = APIRouter()


def _raise(exc: AttendanceReviewError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


class ConfirmHoursRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectionRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    punch_id: int | None = None
    requested_check_in_at: datetime | None = None
    requested_check_out_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=500)


@router.post("/punches/{punch_id}/confirm")
def confirm_my_punch(
    punch_id: int,
    _payload: ConfirmHoursRequest,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """Stylist confirms hours on one of their own auto-closed punches.

    Refuses to confirm someone else's punch — the surface is
    intentionally per-user.
    """
    from database.models import StaffPunch  # local import keeps the dep graph tight

    punch = db.get(StaffPunch, punch_id)
    if punch is None:
        raise HTTPException(
            status_code=404, detail={"code": "punch_not_found"}
        )
    if punch.user_id != sales_user.id:
        raise HTTPException(
            status_code=403, detail={"code": "punch_not_yours"}
        )

    try:
        result = attendance_review.confirm_hours(
            db,
            punch_id=punch_id,
            actor_user_id=sales_user.id,
            actor_kind="staff",
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.get("/correction-requests")
def list_my_correction_requests(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    rows = attendance_review.list_correction_requests(
        db,
        user_id=sales_user.id,
    )
    return {"correction_requests": rows}


@router.post("/correction-requests")
def submit_my_correction_request(
    payload: CorrectionRequestCreate,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = attendance_review.submit_correction_request(
            db,
            user=sales_user,
            punch_id=payload.punch_id,
            requested_check_in_at=payload.requested_check_in_at,
            requested_check_out_at=payload.requested_check_out_at,
            reason=payload.reason,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/correction-requests/{request_id}/cancel")
def cancel_my_correction_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = attendance_review.cancel_correction_request(
            db,
            request_id=request_id,
            user=sales_user,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
