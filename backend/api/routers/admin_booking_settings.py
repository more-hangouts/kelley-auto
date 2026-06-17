"""Admin endpoints for booking-widget configuration: availability rules,
blackout dates, and the singleton theme/copy/flow settings.

Auth-gated. The theme settings ``PUT`` is a partial update — pass any
combination of ``theme``, ``copy``, ``flow`` and only the keys present are
replaced. Missing keys are left intact (so a "tweak the accent color"
flow doesn't have to re-send the whole copy bundle).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import (
    AppointmentAvailabilityRule,
    AppointmentBlackout,
    BookingWidgetThemeSettings,
    User,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Availability rules
# ---------------------------------------------------------------------------


class AvailabilityRuleIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_minutes: int = Field(default=45, ge=5, le=480)
    capacity: int = Field(default=1, ge=1, le=20)
    effective_from: date | None = None
    effective_to: date | None = None
    active: bool = True
    label: str | None = Field(default=None, max_length=100)

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, v: time, info) -> time:
        start = info.data.get("start_time")
        if start is not None and v <= start:
            raise ValueError("end_time must be after start_time")
        return v


class AvailabilityRuleOut(AvailabilityRuleIn):
    id: int
    created_at: datetime
    updated_at: datetime


def _rule_out(r: AppointmentAvailabilityRule) -> AvailabilityRuleOut:
    return AvailabilityRuleOut(
        id=r.id,
        weekday=r.weekday,
        start_time=r.start_time,
        end_time=r.end_time,
        slot_duration_minutes=r.slot_duration_minutes,
        capacity=r.capacity,
        effective_from=r.effective_from,
        effective_to=r.effective_to,
        active=r.active,
        label=r.label,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("/availability/rules", response_model=list[AvailabilityRuleOut])
def list_rules(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> list[AvailabilityRuleOut]:
    rows = (
        db.query(AppointmentAvailabilityRule)
        .order_by(
            AppointmentAvailabilityRule.weekday.asc(),
            AppointmentAvailabilityRule.start_time.asc(),
        )
        .all()
    )
    return [_rule_out(r) for r in rows]


@router.post("/availability/rules", response_model=AvailabilityRuleOut, status_code=201)
def create_rule(
    payload: AvailabilityRuleIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> AvailabilityRuleOut:
    row = AppointmentAvailabilityRule(
        weekday=payload.weekday,
        start_time=payload.start_time,
        end_time=payload.end_time,
        slot_duration_minutes=payload.slot_duration_minutes,
        capacity=payload.capacity,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        active=payload.active,
        label=payload.label,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _rule_out(row)


@router.patch("/availability/rules/{rule_id}", response_model=AvailabilityRuleOut)
def update_rule(
    rule_id: int,
    payload: AvailabilityRuleIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> AvailabilityRuleOut:
    row = (
        db.query(AppointmentAvailabilityRule)
        .filter(AppointmentAvailabilityRule.id == rule_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="rule not found")
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _rule_out(row)


@router.delete("/availability/rules/{rule_id}", status_code=204, response_model=None)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
):
    row = (
        db.query(AppointmentAvailabilityRule)
        .filter(AppointmentAvailabilityRule.id == rule_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="rule not found")
    db.delete(row)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Blackouts
# ---------------------------------------------------------------------------


class BlackoutIn(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("end_at")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
        start = info.data.get("start_at")
        if start is not None and v <= start:
            raise ValueError("end_at must be after start_at")
        return v


class BlackoutOut(BlackoutIn):
    id: int
    created_by: int | None
    created_at: datetime


def _blackout_out(b: AppointmentBlackout) -> BlackoutOut:
    return BlackoutOut(
        id=b.id,
        start_at=b.start_at,
        end_at=b.end_at,
        reason=b.reason,
        created_by=b.created_by,
        created_at=b.created_at,
    )


@router.get("/blackouts", response_model=list[BlackoutOut])
def list_blackouts(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> list[BlackoutOut]:
    rows = (
        db.query(AppointmentBlackout)
        .order_by(AppointmentBlackout.start_at.desc())
        .all()
    )
    return [_blackout_out(b) for b in rows]


@router.post("/blackouts", response_model=BlackoutOut, status_code=201)
def create_blackout(
    payload: BlackoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_scope),
) -> BlackoutOut:
    row = AppointmentBlackout(
        start_at=payload.start_at,
        end_at=payload.end_at,
        reason=payload.reason,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _blackout_out(row)


@router.delete("/blackouts/{blackout_id}", status_code=204, response_model=None)
def delete_blackout(
    blackout_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
):
    row = (
        db.query(AppointmentBlackout)
        .filter(AppointmentBlackout.id == blackout_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="blackout not found")
    db.delete(row)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Theme + copy + flow
# ---------------------------------------------------------------------------


class ThemeSettingsOut(BaseModel):
    theme: dict[str, Any]
    copy_text: dict[str, Any]
    flow: dict[str, Any]
    updated_at: datetime


class ThemeSettingsPatch(BaseModel):
    theme: dict[str, Any] | None = None
    copy_text: dict[str, Any] | None = None
    flow: dict[str, Any] | None = None


def _theme_out(s: BookingWidgetThemeSettings) -> ThemeSettingsOut:
    return ThemeSettingsOut(
        theme=s.theme or {},
        copy_text=s.copy or {},
        flow=s.flow or {},
        updated_at=s.updated_at,
    )


def _get_singleton(db: Session) -> BookingWidgetThemeSettings:
    s = db.query(BookingWidgetThemeSettings).first()
    if s is None:
        raise HTTPException(status_code=500, detail="theme singleton missing")
    return s


@router.get("/settings", response_model=ThemeSettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin_scope),
) -> ThemeSettingsOut:
    return _theme_out(_get_singleton(db))


@router.put("/settings", response_model=ThemeSettingsOut)
def put_settings(
    payload: ThemeSettingsPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_scope),
) -> ThemeSettingsOut:
    s = _get_singleton(db)
    changes = payload.model_dump(exclude_unset=True)
    if "theme" in changes:
        s.theme = changes["theme"] or {}
    if "copy_text" in changes:
        s.copy = changes["copy_text"] or {}
    if "flow" in changes:
        s.flow = changes["flow"] or {}
    s.updated_by = user.id
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    return _theme_out(s)
