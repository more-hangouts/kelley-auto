"""Admin shift + override CRUD service (Phase 8 Slice C).

Owner-side mutations on `staff_shifts` and `staff_shift_overrides`,
plus a read-only **overlap visualizer** for the schedule UI.

Per the user's Slice C enforcement #6 ("Shift overlap endpoint is
read-only/visualization, not enforcement"), the overlap detector is
a pure read — it never blocks a shift create. The doc's own
guardrail #2 already locks out DB-level overlap enforcement; this
helper is what lets the owner SEE overlaps in the calendar overlay.

Overlap definition: two shifts collide on a date if both produce a
concrete instance for that date (via `shift_resolver._instance_for_date`
respecting `working_days`) AND the instances' `[starts_at_local,
ends_at_local]` intervals intersect. Overrides are also compared
because an override's date range can overlap a base shift's recurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    StaffShift,
    StaffShiftOverride,
    User,
)
from services import shift_resolver
from services.business_time import shop_tz


class StaffShiftAdminError(Exception):
    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _shift_to_dict(s: StaffShift) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "location_id": s.location_id,
        "starts_at": s.starts_at.astimezone(timezone.utc).isoformat(),
        "ends_at": s.ends_at.astimezone(timezone.utc).isoformat(),
        "late_grace_period_minutes": s.late_grace_period_minutes,
        "earliest_check_in_minutes": s.earliest_check_in_minutes,
        "early_out_grace_minutes": s.early_out_grace_minutes,
        "auto_session_close_time": (
            s.auto_session_close_time.isoformat()
            if s.auto_session_close_time
            else None
        ),
        "max_session_hours": (
            float(s.max_session_hours)
            if s.max_session_hours is not None
            else None
        ),
        "working_days": list(s.working_days or []),
        "notes": s.notes,
        "created_by_user_id": s.created_by_user_id,
        "created_at": s.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": s.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _override_to_dict(o: StaffShiftOverride) -> dict:
    return {
        "id": o.id,
        "user_id": o.user_id,
        "shift_id": o.shift_id,
        "starts_on": o.starts_on.isoformat(),
        "ends_on": o.ends_on.isoformat(),
        "reason": o.reason,
        "created_by_user_id": o.created_by_user_id,
        "created_at": o.created_at.astimezone(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Shift CRUD
# ---------------------------------------------------------------------------


def _validate_shift_payload(
    *,
    starts_at: datetime,
    ends_at: datetime,
    working_days: list[int],
    late_grace_period_minutes: int,
    earliest_check_in_minutes: int,
    early_out_grace_minutes: int,
    max_session_hours: float | None,
) -> None:
    """Raise StaffShiftAdminError on app-level invariants the schema
    CHECK can't easily express."""
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise StaffShiftAdminError("naive_datetime", http_status=422)
    if ends_at <= starts_at:
        raise StaffShiftAdminError("invalid_date_range", http_status=422)
    if not working_days:
        raise StaffShiftAdminError("working_days_required", http_status=422)
    if len(working_days) > 7:
        raise StaffShiftAdminError("working_days_too_many", http_status=422)
    if any(d < 1 or d > 7 for d in working_days):
        raise StaffShiftAdminError("invalid_weekday", http_status=422)
    if not (0 <= late_grace_period_minutes <= 120):
        raise StaffShiftAdminError("late_grace_out_of_range", http_status=422)
    if not (0 <= earliest_check_in_minutes <= 720):
        raise StaffShiftAdminError(
            "earliest_check_in_out_of_range", http_status=422
        )
    if not (0 <= early_out_grace_minutes <= 120):
        raise StaffShiftAdminError(
            "early_out_grace_out_of_range", http_status=422
        )
    if max_session_hours is not None and not (1 <= max_session_hours <= 24):
        raise StaffShiftAdminError(
            "max_session_hours_out_of_range", http_status=422
        )


def create_shift(
    db: Session,
    *,
    actor_user_id: int,
    user_id: int,
    location_id: int | None,
    starts_at: datetime,
    ends_at: datetime,
    working_days: list[int],
    late_grace_period_minutes: int = 0,
    earliest_check_in_minutes: int = 120,
    early_out_grace_minutes: int = 0,
    auto_session_close_time: time | None = None,
    max_session_hours: float | None = None,
    notes: str | None = None,
) -> dict:
    _validate_shift_payload(
        starts_at=starts_at,
        ends_at=ends_at,
        working_days=working_days,
        late_grace_period_minutes=late_grace_period_minutes,
        earliest_check_in_minutes=earliest_check_in_minutes,
        early_out_grace_minutes=early_out_grace_minutes,
        max_session_hours=max_session_hours,
    )
    if db.get(User, user_id) is None:
        raise StaffShiftAdminError("user_not_found", http_status=404)

    s = StaffShift(
        user_id=user_id,
        location_id=location_id,
        starts_at=starts_at,
        ends_at=ends_at,
        working_days=list(working_days),
        late_grace_period_minutes=late_grace_period_minutes,
        earliest_check_in_minutes=earliest_check_in_minutes,
        early_out_grace_minutes=early_out_grace_minutes,
        auto_session_close_time=auto_session_close_time,
        max_session_hours=max_session_hours,
        notes=(notes or "").strip() or None,
        created_by_user_id=actor_user_id,
    )
    db.add(s)
    db.flush()
    return _shift_to_dict(s)


def update_shift(
    db: Session,
    *,
    shift_id: int,
    fields: dict,
) -> dict:
    """Partial update. Only the fields actually supplied are applied;
    `_validate_shift_payload` runs against the **post-update** state
    so a partial change can't sneak past invariants."""
    s = db.get(StaffShift, shift_id)
    if s is None:
        raise StaffShiftAdminError("shift_not_found", http_status=404)

    new_values = {**_shift_to_dict(s), **fields}
    # Pull the post-update view in the right types for validation.
    starts_at = (
        fields.get("starts_at") if "starts_at" in fields else s.starts_at
    )
    ends_at = fields.get("ends_at") if "ends_at" in fields else s.ends_at
    working_days = (
        fields.get("working_days")
        if "working_days" in fields
        else list(s.working_days or [])
    )
    late = fields.get(
        "late_grace_period_minutes", s.late_grace_period_minutes
    )
    earliest = fields.get(
        "earliest_check_in_minutes", s.earliest_check_in_minutes
    )
    early_out = fields.get(
        "early_out_grace_minutes", s.early_out_grace_minutes
    )
    max_hours = fields.get(
        "max_session_hours",
        float(s.max_session_hours) if s.max_session_hours is not None else None,
    )
    _validate_shift_payload(
        starts_at=starts_at,
        ends_at=ends_at,
        working_days=working_days,
        late_grace_period_minutes=late,
        earliest_check_in_minutes=earliest,
        early_out_grace_minutes=early_out,
        max_session_hours=max_hours,
    )

    for k, v in fields.items():
        setattr(s, k, v)
    db.flush()
    return _shift_to_dict(s)


def delete_shift(db: Session, *, shift_id: int) -> None:
    s = db.get(StaffShift, shift_id)
    if s is None:
        raise StaffShiftAdminError("shift_not_found", http_status=404)
    db.delete(s)
    db.flush()


def list_shifts(
    db: Session, *, user_id: int | None = None
) -> list[dict]:
    """List shifts, optionally scoped to one user. Used by the
    schedule-assignment UI; not paginated because boutique-scale shift
    counts are tiny."""
    stmt = select(StaffShift).order_by(
        StaffShift.user_id, StaffShift.starts_at
    )
    if user_id is not None:
        stmt = stmt.where(StaffShift.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    return [_shift_to_dict(s) for s in rows]


# ---------------------------------------------------------------------------
# Override CRUD
# ---------------------------------------------------------------------------


def create_override(
    db: Session,
    *,
    actor_user_id: int,
    user_id: int,
    shift_id: int,
    starts_on: date,
    ends_on: date,
    reason: str | None = None,
) -> dict:
    if ends_on < starts_on:
        raise StaffShiftAdminError("invalid_date_range", http_status=422)
    if db.get(User, user_id) is None:
        raise StaffShiftAdminError("user_not_found", http_status=404)
    if db.get(StaffShift, shift_id) is None:
        raise StaffShiftAdminError("shift_not_found", http_status=404)

    o = StaffShiftOverride(
        user_id=user_id,
        shift_id=shift_id,
        starts_on=starts_on,
        ends_on=ends_on,
        reason=(reason or "").strip() or None,
        created_by_user_id=actor_user_id,
    )
    db.add(o)
    db.flush()
    return _override_to_dict(o)


def delete_override(db: Session, *, override_id: int) -> None:
    o = db.get(StaffShiftOverride, override_id)
    if o is None:
        raise StaffShiftAdminError("override_not_found", http_status=404)
    db.delete(o)
    db.flush()


def list_overrides(
    db: Session, *, user_id: int | None = None
) -> list[dict]:
    stmt = select(StaffShiftOverride).order_by(
        StaffShiftOverride.user_id, StaffShiftOverride.starts_on
    )
    if user_id is not None:
        stmt = stmt.where(StaffShiftOverride.user_id == user_id)
    rows = db.execute(stmt).scalars().all()
    return [_override_to_dict(o) for o in rows]


# ---------------------------------------------------------------------------
# Overlap visualizer (read-only — never blocks a shift create)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConcreteInstance:
    shift_id: int
    is_override: bool
    business_date: date
    starts_at_local: datetime
    ends_at_local: datetime


def _instances_in_range(
    db: Session, *, user_id: int, from_date: date, to_date: date
) -> list[_ConcreteInstance]:
    """Walk every base shift + active override for the user in the
    range and produce concrete instances. We deliberately compute
    against the schema, not the resolver's "pick one shift per day"
    logic — for the visualizer we want EVERY potential instance so
    the owner sees collisions across base shifts AND active overrides.
    """
    out: list[_ConcreteInstance] = []
    if to_date < from_date:
        raise StaffShiftAdminError("invalid_date_range", http_status=422)

    base_shifts = (
        db.execute(
            select(StaffShift).where(StaffShift.user_id == user_id)
        )
        .scalars()
        .all()
    )
    overrides = (
        db.execute(
            select(StaffShiftOverride)
            .where(StaffShiftOverride.user_id == user_id)
            .where(StaffShiftOverride.starts_on <= to_date)
            .where(StaffShiftOverride.ends_on >= from_date)
        )
        .scalars()
        .all()
    )
    override_shifts = {
        o.shift_id: db.get(StaffShift, o.shift_id) for o in overrides
    }

    cur = from_date
    while cur <= to_date:
        # Base instances
        for s in base_shifts:
            inst = shift_resolver._instance_for_date(s, cur, force=False)
            if inst is None:
                continue
            out.append(
                _ConcreteInstance(
                    shift_id=s.id,
                    is_override=False,
                    business_date=cur,
                    starts_at_local=inst.starts_at_local,
                    ends_at_local=inst.ends_at_local,
                )
            )
        # Override instances
        for o in overrides:
            if not (o.starts_on <= cur <= o.ends_on):
                continue
            shift = override_shifts.get(o.shift_id)
            if shift is None:
                continue
            inst = shift_resolver._instance_for_date(shift, cur, force=True)
            if inst is None:
                continue
            out.append(
                _ConcreteInstance(
                    shift_id=shift.id,
                    is_override=True,
                    business_date=cur,
                    starts_at_local=inst.starts_at_local,
                    ends_at_local=inst.ends_at_local,
                )
            )
        cur += timedelta(days=1)
    return out


def find_overlaps(
    db: Session,
    *,
    user_id: int,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Read-only overlap detector for the owner's schedule UI.

    Returns one entry per overlapping pair, with the local time
    interval each side covers and the source kind (`base` or
    `override`) so the calendar overlay can paint a warning chip.
    Per Slice C enforcement #6 this NEVER blocks a shift create —
    it's purely descriptive.
    """
    instances = _instances_in_range(
        db, user_id=user_id, from_date=from_date, to_date=to_date
    )
    # Bucket by business_date so we only compare instances on the same
    # day (a Mon shift can't overlap a Tue shift).
    by_day: dict[date, list[_ConcreteInstance]] = {}
    for inst in instances:
        by_day.setdefault(inst.business_date, []).append(inst)

    overlaps: list[dict] = []
    for day, day_instances in by_day.items():
        # Pairwise comparison; n is tiny (handful of shifts/day).
        for i in range(len(day_instances)):
            for j in range(i + 1, len(day_instances)):
                a = day_instances[i]
                b = day_instances[j]
                if a.shift_id == b.shift_id and a.is_override == b.is_override:
                    # Same shift instance; nothing to compare.
                    continue
                if (
                    a.starts_at_local < b.ends_at_local
                    and b.starts_at_local < a.ends_at_local
                ):
                    overlaps.append(
                        {
                            "business_date": day.isoformat(),
                            "a": {
                                "shift_id": a.shift_id,
                                "is_override": a.is_override,
                                "starts_at_local": a.starts_at_local.isoformat(),
                                "ends_at_local": a.ends_at_local.isoformat(),
                            },
                            "b": {
                                "shift_id": b.shift_id,
                                "is_override": b.is_override,
                                "starts_at_local": b.starts_at_local.isoformat(),
                                "ends_at_local": b.ends_at_local.isoformat(),
                            },
                        }
                    )
    return overlaps
