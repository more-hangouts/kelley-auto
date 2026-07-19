"""Admin-side staff-locations management.

Phase 7 Slice 1 surface for the owner to seed and edit the boutique's
geofence. Without at least one active row here, every clock-in
returns 403 `outside_geofence`. Mounted at `/api/admin/staff-locations`
and gated on `require_admin_scope`.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import StaffLocation, User
from services.clock_in import haversine_m

router = APIRouter()


class StaffLocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=25, le=1000)
    grace_minutes: int = Field(default=0, ge=0, le=120)
    default_auto_session_close_time: time | None = None


class StaffLocationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int | None = Field(default=None, ge=25, le=1000)
    grace_minutes: int | None = Field(default=None, ge=0, le=120)
    # Nullable on purpose — patching with explicit null clears the
    # cutoff so the auto-close cron falls back to MAX_SESSION_HOURS.
    default_auto_session_close_time: time | None = None
    active: bool | None = None


class StaffLocationResponse(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float
    radius_m: int
    grace_minutes: int
    default_auto_session_close_time: time | None
    active: bool

    @classmethod
    def from_row(cls, row: StaffLocation) -> "StaffLocationResponse":
        return cls(
            id=row.id,
            name=row.name,
            latitude=float(row.latitude),
            longitude=float(row.longitude),
            radius_m=row.radius_m,
            grace_minutes=row.grace_minutes,
            default_auto_session_close_time=row.default_auto_session_close_time,
            active=row.active,
        )


@router.get("", response_model=list[StaffLocationResponse])
def list_locations(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> list[StaffLocationResponse]:
    rows = (
        db.execute(select(StaffLocation).order_by(StaffLocation.id))
        .scalars()
        .all()
    )
    return [StaffLocationResponse.from_row(r) for r in rows]


@router.post("", response_model=StaffLocationResponse, status_code=201)
def create_location(
    payload: StaffLocationCreate,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> StaffLocationResponse:
    row = StaffLocation(
        name=payload.name.strip(),
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_m=payload.radius_m,
        grace_minutes=payload.grace_minutes,
        default_auto_session_close_time=payload.default_auto_session_close_time,
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return StaffLocationResponse.from_row(row)


@router.patch("/{location_id}", response_model=StaffLocationResponse)
def patch_location(
    location_id: int,
    payload: StaffLocationPatch,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> StaffLocationResponse:
    row = db.get(StaffLocation, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="staff_location_not_found")
    fields_set = payload.model_fields_set
    if "name" in fields_set and payload.name is not None:
        row.name = payload.name.strip()
    if "latitude" in fields_set and payload.latitude is not None:
        row.latitude = payload.latitude
    if "longitude" in fields_set and payload.longitude is not None:
        row.longitude = payload.longitude
    if "radius_m" in fields_set and payload.radius_m is not None:
        row.radius_m = payload.radius_m
    if "grace_minutes" in fields_set and payload.grace_minutes is not None:
        row.grace_minutes = payload.grace_minutes
    if "default_auto_session_close_time" in fields_set:
        # Membership-only check (no `is not None` guard) so an explicit
        # null clears the cutoff. Auto-close falls back to
        # MAX_SESSION_HOURS when the column is NULL.
        row.default_auto_session_close_time = payload.default_auto_session_close_time
    if "active" in fields_set and payload.active is not None:
        row.active = payload.active
    db.commit()
    db.refresh(row)
    return StaffLocationResponse.from_row(row)


@router.delete(
    "/{location_id}",
    status_code=204,
    response_class=Response,
)
def deactivate_location(
    location_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    """Soft-delete by flipping `active=False`. We never hard-delete a
    location row because every historical punch's `location_id` FK
    points at it; SET NULL on delete would lose the audit attribution.
    """
    row = db.get(StaffLocation, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="staff_location_not_found")
    row.active = False
    db.commit()
    return Response(status_code=204)


class GeofenceTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GeofenceTestResponse(BaseModel):
    inside: bool
    distance_m: float
    radius_m: int


@router.post(
    "/{location_id}/test-geofence",
    response_model=GeofenceTestResponse,
)
def test_geofence(
    location_id: int,
    payload: GeofenceTestRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> GeofenceTestResponse:
    """Read-only geofence probe. Calls the same haversine helper the
    punch gate uses ([services/clock_in.py:haversine_m]) so a passing
    test guarantees a real punch from the same coords also passes.
    Inactive locations remain testable so the owner can validate
    coordinates before reactivating.
    """
    row = db.get(StaffLocation, location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="staff_location_not_found")
    distance = haversine_m(
        float(row.latitude),
        float(row.longitude),
        payload.latitude,
        payload.longitude,
    )
    return GeofenceTestResponse(
        inside=distance <= row.radius_m,
        distance_m=distance,
        radius_m=row.radius_m,
    )
