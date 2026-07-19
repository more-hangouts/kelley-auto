"""Admin attendance review router (Phase 7 Slice 2B-2).

Mounted at `/api/admin/attendance` and gated on `require_admin_scope`.
Owner read paths are date-bounded from day one — no unbounded "all
punches" list endpoint exists. The user explicitly called this out
when greenlighting Slice 2B-2.

Routes:

  GET  /punches            — bounded punch list, optional staff filter,
                             optional review-queue filter
  GET  /totals             — per-staff hours totals for the same window
  POST /punches/{id}/confirm
  POST /punches/{id}/adjust   — manual adjustment, append-only audit
  POST /punches/{id}/void     — flag the punch as void, never delete
  GET  /correction-requests   — pending queue by default
  POST /correction-requests/{id}/decide   — approve or deny

Adjustments and decisions write append-only `staff_punch_audit_events`
rows so the timeline survives further edits. There is intentionally no
DELETE route on a punch.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import attendance_review
from services.attendance_review import AttendanceReviewError

router = APIRouter()


def _raise(exc: AttendanceReviewError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdjustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_punched_at: datetime
    reason: str = Field(min_length=1, max_length=500)


class VoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class DecideCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "denied"]
    decision_notes: str | None = Field(default=None, max_length=500)


class ClockOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


@router.get("/punches")
def list_punches(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    range_key: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    staff_user_id: int | None = Query(default=None),
    review_queue_only: bool = Query(default=False),
) -> dict:
    try:
        return attendance_review.list_punches(
            db,
            range_key=range_key,
            from_date=from_date,
            to_date=to_date,
            staff_user_id=staff_user_id,
            review_queue_only=review_queue_only,
        )
    except AttendanceReviewError as exc:
        _raise(exc)


@router.get("/totals")
def staff_totals(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    range_key: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    bucket: str = Query(default="day"),
) -> dict:
    try:
        return attendance_review.staff_totals(
            db,
            range_key=range_key,
            from_date=from_date,
            to_date=to_date,
            bucket=bucket,
        )
    except AttendanceReviewError as exc:
        _raise(exc)


@router.get("/totals/export.csv")
def staff_totals_csv(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    range_key: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    bucket: str = Query(default="day"),
) -> StreamingResponse:
    """CSV export of per-staff totals for the same window/bucket as the
    JSON `/totals` endpoint. Streams `text/csv` with one row per
    (staff, bucket_key) pair plus a trailing total row per stylist.

    Owner-only — sales tokens hit `require_admin_scope` first and get 403.
    """
    try:
        payload = attendance_review.staff_totals(
            db,
            range_key=range_key,
            from_date=from_date,
            to_date=to_date,
            bucket=bucket,
        )
    except AttendanceReviewError as exc:
        _raise(exc)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "staff_user_id",
            "username",
            "full_name",
            "bucket",
            "bucket_key",
            "hours",
        ]
    )
    bucket_label = payload["bucket"]
    for row in payload["totals"]:
        for entry in row["by_bucket"]:
            writer.writerow(
                [
                    row["user_id"],
                    row["username"] or "",
                    row["full_name"] or "",
                    bucket_label,
                    entry["bucket_key"],
                    entry["hours"],
                ]
            )
        writer.writerow(
            [
                row["user_id"],
                row["username"] or "",
                row["full_name"] or "",
                bucket_label,
                "TOTAL",
                row["total_hours"],
            ]
        )

    buf.seek(0)
    filename = (
        f"attendance-{payload['from_date']}-{payload['to_date']}-"
        f"{bucket_label}.csv"
    )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/punches/{punch_id}/confirm")
def confirm_punch(
    punch_id: int,
    _payload: ConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = attendance_review.confirm_hours(
            db,
            punch_id=punch_id,
            actor_user_id=admin.id,
            actor_kind="owner",
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/punches/{punch_id}/adjust")
def adjust_punch(
    punch_id: int,
    payload: AdjustRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = attendance_review.manual_adjust(
            db,
            punch_id=punch_id,
            new_punched_at=payload.new_punched_at,
            reason=payload.reason,
            actor_user_id=admin.id,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/punches/{punch_id}/void")
def void_punch(
    punch_id: int,
    payload: VoidRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = attendance_review.void_punch(
            db,
            punch_id=punch_id,
            reason=payload.reason,
            actor_user_id=admin.id,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.get("/open-sessions")
def open_sessions(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Everyone currently clocked in, regardless of date. Backs the
    'On the clock now' panel and its clock-out actions."""
    return attendance_review.list_open_sessions(db)


@router.post("/punches/{punch_id}/clock-out")
def clock_out_punch(
    punch_id: int,
    payload: ClockOutRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Owner clocks one staffer out. `punch_id` is their open in-punch."""
    try:
        result = attendance_review.admin_clock_out(
            db,
            in_punch_id=punch_id,
            actor_user_id=admin.id,
            reason=payload.reason,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/clock-everyone-out")
def clock_everyone_out(
    payload: ClockOutRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Clock out every currently-open session in one pass."""
    try:
        result = attendance_review.admin_clock_out_all(
            db,
            actor_user_id=admin.id,
            reason=payload.reason,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.get("/correction-requests")
def list_correction_requests(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    status_in: list[str] | None = Query(default=None, alias="status"),
    user_id: int | None = Query(default=None),
) -> dict:
    rows = attendance_review.list_correction_requests(
        db,
        statuses=status_in or ("pending",),
        user_id=user_id,
    )
    return {"correction_requests": rows}


@router.post("/correction-requests/{request_id}/decide")
def decide_correction_request(
    request_id: int,
    payload: DecideCorrectionRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = attendance_review.decide_correction_request(
            db,
            request_id=request_id,
            status_decision=payload.status,
            decision_notes=payload.decision_notes,
            actor_user_id=admin.id,
        )
    except AttendanceReviewError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result
