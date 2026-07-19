"""Admin holiday calendar router (Phase 8 Slice C).

Owner-side CRUD over `staff_holidays` under `/api/admin/holidays`.
The schema's `UNIQUE NULLS NOT DISTINCT (holiday_date, location_id,
name)` (migration 059 + Slice A smoke probe) catches duplicate
"global" holidays; the service translates the IntegrityError to a
stable `holiday_already_exists` 409 so the frontend can render
specific copy.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import staff_holidays_admin
from services.staff_holidays_admin import StaffHolidayAdminError

router = APIRouter()


def _raise(exc: StaffHolidayAdminError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


class HolidayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    holiday_date: date
    location_id: int | None = None
    is_paid: bool = False
    multiplier: float | None = Field(default=None, gt=0, le=10)
    notes: str | None = Field(default=None, max_length=500)


class HolidayPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    holiday_date: date | None = None
    location_id: int | None = None
    is_paid: bool | None = None
    multiplier: float | None = Field(default=None, gt=0, le=10)
    notes: str | None = Field(default=None, max_length=500)


@router.get("")
def list_holidays(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
) -> dict:
    try:
        rows = staff_holidays_admin.list_holidays(
            db, from_date=from_date, to_date=to_date
        )
    except StaffHolidayAdminError as exc:
        _raise(exc)
    return {"holidays": rows}


@router.post("", status_code=201)
def create_holiday(
    payload: HolidayCreate,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_holidays_admin.create_holiday(
            db,
            name=payload.name,
            holiday_date=payload.holiday_date,
            location_id=payload.location_id,
            is_paid=payload.is_paid,
            multiplier=payload.multiplier,
            notes=payload.notes,
        )
    except StaffHolidayAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.patch("/{holiday_id}")
def patch_holiday(
    holiday_id: int,
    payload: HolidayPatch,
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
        result = staff_holidays_admin.update_holiday(
            db, holiday_id=holiday_id, fields=fields
        )
    except StaffHolidayAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.delete(
    "/{holiday_id}", status_code=204, response_class=Response
)
def delete_holiday(
    holiday_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        staff_holidays_admin.delete_holiday(db, holiday_id=holiday_id)
    except StaffHolidayAdminError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=204)
