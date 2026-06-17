"""Sales-side schedule read (Phase 8 Slice C).

`GET /api/sales/schedule?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`
returns the stylist's resolved schedule using `expand_shifts` with
read-side time-off suppression. Approved time-off days surface as
`time_off_suppressed: true` with `shift: null`; pending and denied
requests do NOT suppress (those are still on the schedule).

Date range is required and bounded by the caller — same posture as
the Slice 2B-2 attendance review surface (the user explicitly called
out date-bounded reads when greenlighting Slice C).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import recurring_availability, shift_resolver, staff_schedule
from services.recurring_availability import RecurringAvailabilityError
from services.staff_schedule import StaffScheduleError

router = APIRouter()


def _raise_avail(exc: RecurringAvailabilityError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(
        status_code=exc.http_status, detail=detail
    ) from exc


# Cap on how wide a single read can be. Two-week schedule + a handful
# of days of slack covers the doc's "next two weeks" requirement
# without letting a stylist accidentally pull a year of data.
MAX_RANGE_DAYS = 31


def _validate_range(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_date_range"}
        )
    if (to_date - from_date) > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "date_range_too_wide",
                "max_days": MAX_RANGE_DAYS,
            },
        )


@router.get("")
def get_schedule(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
    from_date: date = Query(..., description="business-local start date"),
    to_date: date = Query(..., description="business-local end date"),
) -> dict:
    _validate_range(from_date, to_date)
    days = shift_resolver.expand_shifts(
        db,
        user_id=sales_user.id,
        from_date=from_date,
        to_date=to_date,
        suppress_time_off=True,
    )
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "days": days,
    }


@router.get("/team")
def get_team_schedule(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
    from_date: date = Query(..., description="business-local start date"),
    to_date: date = Query(..., description="business-local end date"),
) -> dict:
    """Phase 10 Slice 5: coworker-visible weekly schedule.

    Returns published shifts for every active staff member inside the
    date range so a stylist can see who they're working with that
    week — groundwork for the future cover/swap-request flow.

    Privacy contract (enforced both in the service and re-asserted
    here so a future router change can't widen the response shape by
    accident): the response carries ONLY
    `user_id`/`username`/`full_name` plus
    `entry_id`/`business_date`/`starts_at_local`/`ends_at_local`.
    Drafts are excluded outright. `manager_notes`,
    `attendance_status`, and any actual-clock-in/out FKs never appear.

    Range is bounded to `MAX_RANGE_DAYS` same as `/api/sales/schedule`.
    """
    _validate_range(from_date, to_date)
    try:
        entries = staff_schedule.list_team_published_schedule(
            db, from_date=from_date, to_date=to_date
        )
    except StaffScheduleError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code}
        ) from exc

    # Re-assert the privacy contract by re-projecting the service's
    # dict through a known-good allowlist. If a future service refactor
    # adds a field, this loop quietly drops it — coworkers never see
    # something they weren't supposed to.
    ALLOWED = (
        "entry_id",
        "user_id",
        "username",
        "full_name",
        "business_date",
        "starts_at_local",
        "ends_at_local",
    )
    sanitized = [{k: row.get(k) for k in ALLOWED} for row in entries]

    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "viewer_user_id": sales_user.id,
        "entries": sanitized,
    }


# ---------------------------------------------------------------------------
# Recurring unavailability (Phase 10 Slice 6 — Epic 3.4)
# ---------------------------------------------------------------------------


class AvailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=1, le=7)
    start_time_local: time
    end_time_local: time
    effective_from: date | None = None
    effective_until: date | None = None
    reason: str | None = Field(default=None, max_length=200)


class AvailabilityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_until: date | None = None


@router.get("/availability")
def list_my_availability(
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
    include_expired: bool = Query(default=False),
) -> dict:
    """Return the stylist's own recurring unavailability rules. Defaults
    to active rules only; pass `include_expired=true` to see history."""
    rows = recurring_availability.list_for_user(
        db, user_id=sales_user.id, include_expired=include_expired
    )
    return {"blocks": rows}


@router.post("/availability", status_code=201)
def create_my_availability(
    payload: AvailabilityCreate,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """Stylist self-serve: add a new "I'm unavailable" rule. No admin
    approval — managers see it on the grid and re-staff accordingly."""
    try:
        result = recurring_availability.create_block(
            db,
            user_id=sales_user.id,
            weekday=payload.weekday,
            start_time_local=payload.start_time_local,
            end_time_local=payload.end_time_local,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            reason=payload.reason,
        )
    except RecurringAvailabilityError as exc:
        db.rollback()
        _raise_avail(exc)
    db.commit()
    return result


@router.patch("/availability/{block_id}")
def patch_my_availability(
    block_id: int,
    payload: AvailabilityPatch,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """Bound (or re-open) an existing rule by setting `effective_until`.

    PATCH-with-null re-opens an archived rule, lifting the date cap.
    Useful when a stylist's situation changes back.
    """
    if "effective_until" not in payload.model_fields_set:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )
    try:
        result = recurring_availability.set_effective_until(
            db,
            user_id=sales_user.id,
            block_id=block_id,
            effective_until=payload.effective_until,
        )
    except RecurringAvailabilityError as exc:
        db.rollback()
        _raise_avail(exc)
    db.commit()
    return result


@router.delete(
    "/availability/{block_id}", status_code=204, response_class=Response
)
def delete_my_availability(
    block_id: int,
    db: Annotated[Session, Depends(get_db)],
    sales_user: Annotated[User, Depends(require_sales_scope)],
) -> Response:
    """Hard delete — the row is gone. Use PATCH `effective_until` if
    you want to keep history."""
    try:
        recurring_availability.delete_block(
            db, user_id=sales_user.id, block_id=block_id
        )
    except RecurringAvailabilityError as exc:
        db.rollback()
        _raise_avail(exc)
    db.commit()
    return Response(status_code=204)
