"""Admin shift + override router (Phase 8 Slice C).

Owner-side CRUD under `/api/admin/shifts` and
`/api/admin/shift-overrides`, plus `/api/admin/shifts/overlaps` for
the **read-only** overlap visualizer (per the user's Slice C
enforcement #6: "shift overlap endpoint is read-only/visualization,
not enforcement").

All routes require `require_admin_scope` — sales tokens get 403
even when they happen to call from the sales subdomain.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import staff_shifts_admin
from services.staff_shifts_admin import StaffShiftAdminError

router = APIRouter()


def _raise(exc: StaffShiftAdminError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


class ShiftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    location_id: int | None = None
    starts_at: datetime
    ends_at: datetime
    working_days: list[int] = Field(min_length=1, max_length=7)
    late_grace_period_minutes: int = Field(default=0, ge=0, le=120)
    earliest_check_in_minutes: int = Field(default=120, ge=0, le=720)
    early_out_grace_minutes: int = Field(default=0, ge=0, le=120)
    auto_session_close_time: time | None = None
    max_session_hours: float | None = Field(default=None, ge=1, le=24)
    notes: str | None = Field(default=None, max_length=500)


class ShiftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    working_days: list[int] | None = Field(default=None, max_length=7)
    late_grace_period_minutes: int | None = Field(default=None, ge=0, le=120)
    earliest_check_in_minutes: int | None = Field(default=None, ge=0, le=720)
    early_out_grace_minutes: int | None = Field(default=None, ge=0, le=120)
    auto_session_close_time: time | None = None
    max_session_hours: float | None = Field(default=None, ge=1, le=24)
    notes: str | None = Field(default=None, max_length=500)


class OverrideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    shift_id: int
    starts_on: date
    ends_on: date
    reason: str | None = Field(default=None, max_length=500)


@router.get("")
def list_shifts(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    user_id: int | None = Query(default=None),
) -> dict:
    return {"shifts": staff_shifts_admin.list_shifts(db, user_id=user_id)}


@router.post("", status_code=201)
def create_shift(
    payload: ShiftCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_shifts_admin.create_shift(
            db,
            actor_user_id=admin.id,
            user_id=payload.user_id,
            location_id=payload.location_id,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            working_days=payload.working_days,
            late_grace_period_minutes=payload.late_grace_period_minutes,
            earliest_check_in_minutes=payload.earliest_check_in_minutes,
            early_out_grace_minutes=payload.early_out_grace_minutes,
            auto_session_close_time=payload.auto_session_close_time,
            max_session_hours=payload.max_session_hours,
            notes=payload.notes,
        )
    except StaffShiftAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.patch("/{shift_id}")
def patch_shift(
    shift_id: int,
    payload: ShiftPatch,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    fields = {
        k: v
        for k, v in payload.model_dump().items()
        if k in payload.model_fields_set
    }
    if not fields:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )
    try:
        result = staff_shifts_admin.update_shift(
            db, shift_id=shift_id, fields=fields
        )
    except StaffShiftAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.delete("/{shift_id}", status_code=204, response_class=Response)
def delete_shift(
    shift_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        staff_shifts_admin.delete_shift(db, shift_id=shift_id)
    except StaffShiftAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Overlap visualizer (read-only)
# ---------------------------------------------------------------------------


@router.get("/overlaps")
def list_overlaps(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    user_id: int = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(...),
) -> dict:
    """Date-bounded overlap detector. Per Slice C enforcement #6 this
    is purely descriptive — it never blocks a shift create/update.
    """
    try:
        rows = staff_shifts_admin.find_overlaps(
            db,
            user_id=user_id,
            from_date=from_date,
            to_date=to_date,
        )
    except StaffShiftAdminError as exc:
        _raise(exc)
    return {
        "user_id": user_id,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "overlaps": rows,
    }


# ---------------------------------------------------------------------------
# Override CRUD (mounted on its own prefix in api/server.py)
# ---------------------------------------------------------------------------


override_router = APIRouter()


@override_router.get("")
def list_overrides(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    user_id: int | None = Query(default=None),
) -> dict:
    return {
        "overrides": staff_shifts_admin.list_overrides(db, user_id=user_id)
    }


@override_router.post("", status_code=201)
def create_override(
    payload: OverrideCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_shifts_admin.create_override(
            db,
            actor_user_id=admin.id,
            user_id=payload.user_id,
            shift_id=payload.shift_id,
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
            reason=payload.reason,
        )
    except StaffShiftAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@override_router.delete(
    "/{override_id}", status_code=204, response_class=Response
)
def delete_override(
    override_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        staff_shifts_admin.delete_override(db, override_id=override_id)
    except StaffShiftAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=204)
