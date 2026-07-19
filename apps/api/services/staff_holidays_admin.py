"""Admin holiday calendar service (Phase 8 Slice C).

CRUD over `staff_holidays`. Uniqueness is enforced at the DB layer
via `UNIQUE NULLS NOT DISTINCT (holiday_date, location_id, name)`
(migration 059 + the Slice A smoke probe). The service surfaces
collisions as a stable `holiday_already_exists` 409 instead of a raw
IntegrityError so the frontend can render specific copy.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import StaffHoliday, StaffLocation


class StaffHolidayAdminError(Exception):
    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _to_dict(h: StaffHoliday) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "holiday_date": h.holiday_date.isoformat(),
        "location_id": h.location_id,
        "is_paid": bool(h.is_paid),
        "multiplier": (
            float(h.multiplier) if h.multiplier is not None else None
        ),
        "notes": h.notes,
        "created_at": h.created_at.astimezone(timezone.utc).isoformat(),
    }


def list_holidays(
    db: Session,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[dict]:
    """Optional date filter so the admin UI can scope to a window."""
    if from_date is not None and to_date is not None and to_date < from_date:
        raise StaffHolidayAdminError("invalid_date_range", http_status=422)
    stmt = select(StaffHoliday).order_by(StaffHoliday.holiday_date)
    if from_date is not None:
        stmt = stmt.where(StaffHoliday.holiday_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(StaffHoliday.holiday_date <= to_date)
    rows = db.execute(stmt).scalars().all()
    return [_to_dict(h) for h in rows]


def create_holiday(
    db: Session,
    *,
    name: str,
    holiday_date: date,
    location_id: int | None = None,
    is_paid: bool = False,
    multiplier: float | None = None,
    notes: str | None = None,
) -> dict:
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise StaffHolidayAdminError("name_required", http_status=422)
    if multiplier is not None and multiplier <= 0:
        raise StaffHolidayAdminError(
            "multiplier_out_of_range", http_status=422
        )
    if location_id is not None and db.get(StaffLocation, location_id) is None:
        raise StaffHolidayAdminError(
            "location_not_found", http_status=404
        )

    h = StaffHoliday(
        name=cleaned_name,
        holiday_date=holiday_date,
        location_id=location_id,
        is_paid=bool(is_paid),
        multiplier=multiplier,
        notes=(notes or "").strip() or None,
    )
    db.add(h)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Either the NULLS NOT DISTINCT UNIQUE caught a duplicate or
        # an FK violation we can surface stably. Caller's begin_nested
        # handles the rollback; here we just translate the error.
        raise StaffHolidayAdminError(
            "holiday_already_exists", http_status=409
        ) from exc
    return _to_dict(h)


def update_holiday(
    db: Session,
    *,
    holiday_id: int,
    fields: dict,
) -> dict:
    h = db.get(StaffHoliday, holiday_id)
    if h is None:
        raise StaffHolidayAdminError(
            "holiday_not_found", http_status=404
        )
    if "name" in fields:
        cleaned = (fields["name"] or "").strip()
        if not cleaned:
            raise StaffHolidayAdminError("name_required", http_status=422)
        h.name = cleaned
    if "holiday_date" in fields:
        h.holiday_date = fields["holiday_date"]
    if "location_id" in fields:
        loc = fields["location_id"]
        if loc is not None and db.get(StaffLocation, loc) is None:
            raise StaffHolidayAdminError(
                "location_not_found", http_status=404
            )
        h.location_id = loc
    if "is_paid" in fields:
        h.is_paid = bool(fields["is_paid"])
    if "multiplier" in fields:
        m = fields["multiplier"]
        if m is not None and m <= 0:
            raise StaffHolidayAdminError(
                "multiplier_out_of_range", http_status=422
            )
        h.multiplier = m
    if "notes" in fields:
        h.notes = (fields["notes"] or "").strip() or None

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise StaffHolidayAdminError(
            "holiday_already_exists", http_status=409
        ) from exc
    return _to_dict(h)


def delete_holiday(db: Session, *, holiday_id: int) -> None:
    h = db.get(StaffHoliday, holiday_id)
    if h is None:
        raise StaffHolidayAdminError(
            "holiday_not_found", http_status=404
        )
    db.delete(h)
    db.flush()
