"""Recurring stylist unavailability service (Phase 10 Slice 6 — Epic 3.4).

CRUD + expansion for `recurring_unavailability`. The stylist owns
their own rows; admin reads them via the weekly-grid expansion
helper (`expand_blocks_for_week`) and the schedule publish path
respects them via `find_conflict`.

The slice deliberately does NOT add an approval flow — the rule is
stylist-self-serve, mirroring "I just won't be there those hours."
Managers see the rules on the grid and use that visibility to
re-staff; if the stylist's rule is wrong, the conversation is
out-of-band.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import RecurringUnavailability
from services.business_time import shop_tz


class RecurringAvailabilityError(Exception):
    """Stable error codes mapped to HTTP statuses by the router."""

    def __init__(
        self,
        code: str,
        *,
        http_status: int = 400,
        extra: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.extra = dict(extra or {})


_ALLOWED_WEEKDAYS = frozenset(range(1, 8))


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _row_to_dict(row: RecurringUnavailability) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "weekday": int(row.weekday),
        "start_time_local": row.start_time_local.strftime("%H:%M"),
        "end_time_local": row.end_time_local.strftime("%H:%M"),
        "effective_from": row.effective_from.isoformat(),
        "effective_until": (
            row.effective_until.isoformat() if row.effective_until else None
        ),
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_for_user(
    db: Session,
    *,
    user_id: int,
    include_expired: bool = False,
    as_of: date | None = None,
) -> list[dict]:
    """All recurring unavailability rules for a user.

    With `include_expired=False` (default), rules whose
    `effective_until < as_of` are filtered out. `as_of` defaults to
    today in the boutique tz.
    """
    if as_of is None:
        as_of = datetime.now(shop_tz()).date()
    stmt = (
        select(RecurringUnavailability)
        .where(RecurringUnavailability.user_id == user_id)
        .order_by(
            RecurringUnavailability.weekday,
            RecurringUnavailability.start_time_local,
        )
    )
    rows = list(db.execute(stmt).scalars().all())
    if not include_expired:
        rows = [
            r
            for r in rows
            if r.effective_until is None or r.effective_until >= as_of
        ]
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _parse_time(value: time | str, *, field: str) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        parts = value.split(":")
        if len(parts) < 2:
            raise ValueError("missing colon")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, TypeError) as exc:
        raise RecurringAvailabilityError(
            "invalid_time", http_status=422, extra={"field": field}
        ) from exc


def create_block(
    db: Session,
    *,
    user_id: int,
    weekday: int,
    start_time_local: time | str,
    end_time_local: time | str,
    effective_from: date | None = None,
    effective_until: date | None = None,
    reason: str | None = None,
) -> dict:
    """Create a new unavailability rule for `user_id`.

    Rejects with `weekday_out_of_range` (422), `invalid_time_range`
    (422), `invalid_effective_range` (422), or `duplicate_active_rule`
    (409) when an indefinite rule with the same shape already exists.
    """
    if weekday not in _ALLOWED_WEEKDAYS:
        raise RecurringAvailabilityError(
            "weekday_out_of_range", http_status=422
        )
    start_t = _parse_time(start_time_local, field="start_time_local")
    end_t = _parse_time(end_time_local, field="end_time_local")
    if end_t <= start_t:
        raise RecurringAvailabilityError(
            "invalid_time_range", http_status=422
        )

    if effective_from is None:
        effective_from = datetime.now(shop_tz()).date()
    if effective_until is not None and effective_until < effective_from:
        raise RecurringAvailabilityError(
            "invalid_effective_range", http_status=422
        )

    if effective_until is None:
        # Partial-unique guard — surface a clean 409 instead of bubbling
        # up the database-level IntegrityError.
        existing = (
            db.execute(
                select(RecurringUnavailability.id)
                .where(RecurringUnavailability.user_id == user_id)
                .where(RecurringUnavailability.weekday == weekday)
                .where(
                    RecurringUnavailability.start_time_local == start_t
                )
                .where(RecurringUnavailability.end_time_local == end_t)
                .where(RecurringUnavailability.effective_until.is_(None))
                .limit(1)
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise RecurringAvailabilityError(
                "duplicate_active_rule",
                http_status=409,
                extra={"existing_id": existing},
            )

    row = RecurringUnavailability(
        user_id=user_id,
        weekday=weekday,
        start_time_local=start_t,
        end_time_local=end_t,
        effective_from=effective_from,
        effective_until=effective_until,
        reason=(reason or "").strip() or None,
    )
    db.add(row)
    db.flush()
    return _row_to_dict(row)


def delete_block(db: Session, *, user_id: int, block_id: int) -> None:
    """Hard-delete the rule. Enforces ownership: a stylist cannot
    delete another stylist's row even if they guess the id.
    """
    row = db.get(RecurringUnavailability, block_id)
    if row is None or row.user_id != user_id:
        raise RecurringAvailabilityError("block_not_found", http_status=404)
    db.delete(row)
    db.flush()


def set_effective_until(
    db: Session,
    *,
    user_id: int,
    block_id: int,
    effective_until: date | None,
) -> dict:
    """Bound an existing rule (or re-open it by passing None).

    If `effective_until` is set in the past, the rule is effectively
    archived immediately. Allowed — that's how a stylist "ends" a
    rule today without wiping it.
    """
    row = db.get(RecurringUnavailability, block_id)
    if row is None or row.user_id != user_id:
        raise RecurringAvailabilityError("block_not_found", http_status=404)
    if effective_until is not None and effective_until < row.effective_from:
        raise RecurringAvailabilityError(
            "invalid_effective_range", http_status=422
        )
    row.effective_until = effective_until
    row.updated_at = datetime.now(shop_tz())
    db.flush()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Expansion / conflict checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ExpandedBlock:
    user_id: int
    block_id: int
    business_date: date
    starts_at_local: datetime
    ends_at_local: datetime
    reason: str | None


def _active_for_date(
    row: RecurringUnavailability, biz_date: date
) -> bool:
    if row.effective_from > biz_date:
        return False
    if row.effective_until is not None and row.effective_until < biz_date:
        return False
    return row.weekday == biz_date.isoweekday()


def _materialize(
    row: RecurringUnavailability, biz_date: date
) -> _ExpandedBlock:
    tz = shop_tz()
    starts = datetime.combine(biz_date, row.start_time_local, tzinfo=tz)
    ends = datetime.combine(biz_date, row.end_time_local, tzinfo=tz)
    return _ExpandedBlock(
        user_id=row.user_id,
        block_id=row.id,
        business_date=biz_date,
        starts_at_local=starts,
        ends_at_local=ends,
        reason=row.reason,
    )


def expand_blocks_for_week(
    db: Session,
    *,
    week_start: date,
    user_ids: Iterable[int] | None = None,
) -> list[dict]:
    """Expand active rules across a Mon-anchored week, one entry per
    materialized (user, day) instance. Used by the admin grid to gray
    out cells the same way time-off blocks do.

    Days are bounded `[week_start, week_start + 7)`. A rule whose
    `effective_from` lands mid-week starts contributing on that day;
    a rule whose `effective_until` lands mid-week contributes through
    that day inclusive.
    """
    stmt = select(RecurringUnavailability)
    if user_ids is not None:
        ids = list(user_ids)
        if not ids:
            return []
        stmt = stmt.where(RecurringUnavailability.user_id.in_(ids))
    rows = list(db.execute(stmt).scalars().all())

    out: list[dict] = []
    for offset in range(7):
        biz_date = week_start + timedelta(days=offset)
        for row in rows:
            if not _active_for_date(row, biz_date):
                continue
            block = _materialize(row, biz_date)
            out.append(
                {
                    "user_id": block.user_id,
                    "block_id": block.block_id,
                    "business_date": block.business_date.isoformat(),
                    "starts_at_local": block.starts_at_local.isoformat(),
                    "ends_at_local": block.ends_at_local.isoformat(),
                    "reason": block.reason,
                }
            )
    return out


def find_conflict(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
) -> int | None:
    """Return the conflicting `recurring_unavailability.id` if the
    [starts_at_local, ends_at_local) interval overlaps an active rule
    for `user_id`, else None.

    Used by the schedule publish path. The interval may cross
    midnight (overnight shifts); the check is materialized per
    business-local date the interval touches.
    """
    tz = shop_tz()
    starts = starts_at_local.astimezone(tz)
    ends = ends_at_local.astimezone(tz)
    if ends <= starts:
        return None
    biz_dates: list[date] = []
    cur = starts.date()
    last = ends.date()
    while cur <= last:
        biz_dates.append(cur)
        cur += timedelta(days=1)

    weekdays = {d.isoweekday() for d in biz_dates}
    rows = list(
        db.execute(
            select(RecurringUnavailability)
            .where(RecurringUnavailability.user_id == user_id)
            .where(RecurringUnavailability.weekday.in_(weekdays))
        )
        .scalars()
        .all()
    )
    for biz_date in biz_dates:
        for row in rows:
            if not _active_for_date(row, biz_date):
                continue
            block = _materialize(row, biz_date)
            if block.starts_at_local < ends and block.ends_at_local > starts:
                return row.id
    return None


__all__ = [
    "RecurringAvailabilityError",
    "create_block",
    "delete_block",
    "expand_blocks_for_week",
    "find_conflict",
    "list_for_user",
    "set_effective_until",
]
