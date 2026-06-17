"""Sales-side shift-request router (Scheduling Phase 1).

Stylist-facing endpoints under `/api/sales/schedule/shift-requests`:

  GET  /                  — requests the caller is involved in, newest first
  GET  /{id}              — one request (404 if not involved)
  POST /                  — file a cover/drop/swap request on own shift
  POST /{id}/cancel       — cancel own non-terminal request

Phase 1 is read-only with respect to the schedule: creating or
cancelling a request never moves a shift. Acceptance and approval (which
do move shifts) arrive in Phase 2. Cancel uses POST (not DELETE) so the
row and its audit trail are preserved.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import staff_shift_requests
from services.staff_shift_requests import StaffShiftRequestError

router = APIRouter()


def _raise(exc: StaffShiftRequestError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(
        status_code=exc.http_status, detail=detail
    ) from exc


class ShiftRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: Literal["cover", "drop", "swap"]
    source_entry_id: int
    target_entry_id: int | None = None
    candidate_user_id: int | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.get("")
def list_my_shift_requests(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    rows = staff_shift_requests.list_for_user(db, user_id=sales_user.id)
    return {"requests": rows}


@router.get("/{request_id}")
def get_my_shift_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    row = staff_shift_requests.get_for_user(
        db, user_id=sales_user.id, request_id=request_id
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "request_not_found"}
        )
    return row


@router.post("")
def create_my_shift_request(
    payload: ShiftRequestCreate,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = staff_shift_requests.create_request(
            db,
            requester=sales_user,
            request_type=payload.request_type,
            source_entry_id=payload.source_entry_id,
            target_entry_id=payload.target_entry_id,
            candidate_user_id=payload.candidate_user_id,
            reason=payload.reason,
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{request_id}/cancel")
def cancel_my_shift_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    try:
        result = staff_shift_requests.cancel_request(
            db, user=sales_user, request_id=request_id
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{request_id}/accept")
def accept_shift_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """Candidate accepts a cover request (required before admin approval)."""
    try:
        result = staff_shift_requests.accept_request(
            db, user=sales_user, request_id=request_id
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/{request_id}/decline")
def decline_shift_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """Candidate declines a cover request (cancels it)."""
    try:
        result = staff_shift_requests.decline_request(
            db, user=sales_user, request_id=request_id
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
