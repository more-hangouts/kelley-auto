"""Admin shift-request router (Scheduling Phase 1).

Owner-side read-only queue under `/api/admin/schedule/shift-requests`:

  GET  /          — list requests, newest first, optional status/staff
                    filters
  GET  /{id}      — one request

Phase 1 is read-only: the owner can inspect the queue but cannot yet
accept/approve/deny. Those decision verbs (which transfer shifts) land in
Phase 2 with the conflict checks and notifications they require.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
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


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "denied"]
    decision_notes: str | None = Field(default=None, max_length=500)


@router.get("")
def list_shift_requests(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    status_in: list[str] | None = Query(default=None, alias="status"),
    requester_user_id: int | None = Query(default=None),
) -> dict:
    try:
        rows = staff_shift_requests.list_admin(
            db,
            statuses=status_in,
            requester_user_id=requester_user_id,
        )
    except StaffShiftRequestError as exc:
        _raise(exc)
    return {"requests": rows}


@router.get("/{request_id}")
def get_shift_request(
    request_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    row = staff_shift_requests.get_admin(db, request_id=request_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "request_not_found"}
        )
    return row


@router.post("/{request_id}/decide")
def decide_shift_request(
    request_id: int,
    payload: DecideRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Approve or deny a request. Approving a cover transfers the shift to
    the accepted candidate; approving a drop retracts it to draft."""
    try:
        result = staff_shift_requests.decide_request(
            db,
            actor_user_id=admin.id,
            request_id=request_id,
            decision=payload.status,
            decision_notes=payload.decision_notes,
        )
    except StaffShiftRequestError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
