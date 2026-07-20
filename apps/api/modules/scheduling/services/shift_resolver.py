"""Shift resolver (Phase 8 Slice B + Phase 10 Slice 1).

The single source of truth the user's guardrail #1 locked in,
extended by Phase 10 to put published per-day entries at the top:

    published schedule entry → override → assigned/base shift
                              → location/default policy

Every consumer (clock-in earliest-check-in window, late/early-out
classification on punch insert, the auto-close + pre-close crons,
the Slice C `/api/sales/schedule` read) calls
`resolve_active_shift(db, user_id, as_of_local=...)`. Returning the
same `ResolvedShift` shape from every path keeps the precedence in
one place.

Design notes:

  - **`starts_at` / `ends_at` on `staff_shifts` are TIMESTAMPTZ
    template anchors.** The local time-of-day component repeats on
    each ISO weekday in `working_days`. The resolver carries
    `duration = ends_at - starts_at` and produces concrete
    `starts_at_local` / `ends_at_local` on a requested date —
    overnight shifts emerge naturally because the duration crosses
    local midnight rather than the time-of-day "wrapping."
  - **Published entry semantics** (Phase 10 Slice 1): a row in
    `staff_schedule_entries` with `status='published'` covering the
    requested business date wins outright. Its `starts_at_local` /
    `ends_at_local` are the resolved interval; policy fields fall
    back to the entry's `source_shift_id` when present, otherwise
    sensible defaults (manual entries have no template to lean on).
    `shift_id` on the returned `ResolvedShift` reflects the source
    template if any (else None) so `clock_in` keeps writing a
    valid `staff_punches.shift_id` FK for template-cloned entries.
  - **Override semantics** (per the doc lock-in): an override row
    points to a specific `staff_shifts.id`. When the override's date
    range covers the requested date, the override's shift template
    is applied even if its `working_days` would not normally include
    that weekday. The override IS the schedule; weekday containment
    is for base-shift expansion only.
  - **Multi-shift-per-day disambiguation**: for a base shift only
    (overrides take precedence regardless), if a stylist has more
    than one shift on the same weekday, we pick the one whose
    instance brackets `as_of_local` first, then the closest upcoming,
    then the closest past. v1 boutiques almost always have one
    shift per stylist per day; this rule is here so the resolver
    behaves deterministically once that ever changes.
  - **Time-off suppression** is a read-side concern of `expand_shifts`,
    not the cron-facing `resolve_active_shift`. Reason: a stylist who
    has approved time-off but somehow punches in still needs the cron
    to auto-close their session. The schedule UI uses
    `expand_shifts(suppress_time_off=True)` to omit the day from the
    stylist's calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from database.models import (
    RecurringUnavailability,
    StaffHoliday,
    StaffScheduleEntry,
    StaffShift,
    StaffShiftOverride,
    TimeOffRequest,
)
from services.business_time import business_now, shop_tz, to_business_local


@dataclass(frozen=True)
class ResolvedShift:
    """Concrete shift instance for one business-local date.

    `starts_at_local` and `ends_at_local` carry tz-aware datetimes in
    the boutique's local timezone. For an overnight shift,
    `ends_at_local.date() != starts_at_local.date()`.

    `shift_id` is the source template's id when the resolution came
    from a base shift, an override, or a template-cloned schedule
    entry. It is None for a manual published schedule entry — in that
    case the punch row's FK to `staff_shifts` simply stays NULL, the
    same way an `unscheduled` punch does today.

    `schedule_entry_id` is set when this resolution came from
    `staff_schedule_entries`; consumers downstream (Slice 2) read it
    to know which entry to stamp `actual_clock_in_punch_id` on.

    `manager_notes` carries the published entry's note (when the
    resolution came from `staff_schedule_entries`). It is private to the
    owning stylist: it surfaces on a stylist's OWN schedule read and
    never on the coworker-visible team view, which goes through a
    separate sanitized path.
    """

    shift_id: int | None
    user_id: int
    business_date: date
    starts_at_local: datetime
    ends_at_local: datetime
    late_grace_period_minutes: int
    earliest_check_in_minutes: int
    early_out_grace_minutes: int
    auto_session_close_time: time | None
    max_session_hours: float | None
    location_id: int | None
    is_override: bool
    schedule_entry_id: int | None = None
    manager_notes: str | None = None


@dataclass(frozen=True)
class _Instance:
    starts_at_local: datetime
    ends_at_local: datetime


def _instance_for_date(
    shift: StaffShift, biz_date: date, *, force: bool = False
) -> _Instance | None:
    """Expand a shift template's instance for `biz_date`.

    `force=True` skips the `working_days` check and is used when the
    caller has already established that this shift applies (override
    case). For base-shift expansion `force=False` filters by ISO
    weekday containment.
    """
    iso_weekday = biz_date.isoweekday()
    if not force:
        working_days = list(shift.working_days or [])
        if iso_weekday not in working_days:
            return None

    tz = shop_tz()
    template_start_local = shift.starts_at.astimezone(tz)
    duration = shift.ends_at - shift.starts_at

    # Combine the requested business date with the template's local
    # time-of-day. Using `datetime.combine(..., tzinfo=tz)` is DST-safe
    # for the start; the duration's `timedelta` arithmetic computes
    # absolute time, so an "8-hour shift" across a DST boundary still
    # pays exactly 8 hours.
    starts_at_local = datetime.combine(
        biz_date, template_start_local.time(), tzinfo=tz
    )
    ends_at_local = starts_at_local + duration
    return _Instance(
        starts_at_local=starts_at_local, ends_at_local=ends_at_local
    )


def _to_resolved(
    shift: StaffShift,
    instance: _Instance,
    *,
    business_date_: date,
    is_override: bool,
) -> ResolvedShift:
    return ResolvedShift(
        shift_id=shift.id,
        user_id=shift.user_id,
        business_date=business_date_,
        starts_at_local=instance.starts_at_local,
        ends_at_local=instance.ends_at_local,
        late_grace_period_minutes=int(shift.late_grace_period_minutes),
        earliest_check_in_minutes=int(shift.earliest_check_in_minutes),
        early_out_grace_minutes=int(shift.early_out_grace_minutes),
        auto_session_close_time=shift.auto_session_close_time,
        max_session_hours=(
            float(shift.max_session_hours)
            if shift.max_session_hours is not None
            else None
        ),
        location_id=shift.location_id,
        is_override=is_override,
    )


def _active_override(
    db: Session, *, user_id: int, biz_date: date
) -> StaffShiftOverride | None:
    """Most-recent override whose date range covers `biz_date`.

    `created_at DESC` so a freshly-added override beats an older one
    if both happen to cover the same date — operationally the owner's
    last edit wins."""
    return (
        db.query(StaffShiftOverride)
        .filter(StaffShiftOverride.user_id == user_id)
        .filter(StaffShiftOverride.starts_on <= biz_date)
        .filter(StaffShiftOverride.ends_on >= biz_date)
        .order_by(StaffShiftOverride.created_at.desc())
        .first()
    )


def _published_entries_for_date(
    db: Session, *, user_id: int, biz_date: date
) -> list[StaffScheduleEntry]:
    """All published schedule entries for `(user_id, biz_date)`.

    Multiple entries on the same day are legal (split shifts, coverage
    handoffs); the caller picks the right one via
    `_pick_best_entry`. Ordered by `starts_at_local` so deterministic
    tie-breaks fall out of the iteration order.
    """
    return list(
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.user_id == user_id)
        .filter(StaffScheduleEntry.business_date == biz_date)
        .filter(StaffScheduleEntry.status == "published")
        .order_by(StaffScheduleEntry.starts_at_local)
        .all()
    )


def _pick_best_entry(
    entries: list[StaffScheduleEntry], *, as_of_local: datetime
) -> StaffScheduleEntry | None:
    """Same disambiguation rule as `_pick_best_candidate`: prefer the
    instance bracketing `as_of_local`; else the closest upcoming;
    else the closest past."""
    if not entries:
        return None
    bracketing = [
        e
        for e in entries
        if e.starts_at_local <= as_of_local <= e.ends_at_local
    ]
    if bracketing:
        return bracketing[0]
    upcoming = sorted(
        [e for e in entries if e.starts_at_local > as_of_local],
        key=lambda e: e.starts_at_local,
    )
    if upcoming:
        return upcoming[0]
    past = sorted(
        [e for e in entries if e.ends_at_local < as_of_local],
        key=lambda e: e.ends_at_local,
        reverse=True,
    )
    return past[0] if past else None


def _resolved_from_entry(
    db: Session, entry: StaffScheduleEntry
) -> ResolvedShift:
    """Build a `ResolvedShift` from a published schedule entry.

    Policy fields (earliest_check_in_minutes, early_out_grace_minutes,
    auto_session_close_time, max_session_hours, location_id) fall back
    to the entry's `source_shift_id` template when present. For manual
    entries with no source shift, we use the same conservative defaults
    the schema picks for `staff_shifts`: 120-min earliest check-in,
    0-min early-out grace, no auto-close, no max hours, no location.
    `late_grace_period_minutes` always comes from the entry's own
    `late_grace_minutes` — the user's directive said the entry's grace
    is authoritative once the row is published.
    """
    tz = shop_tz()
    starts_local = entry.starts_at_local.astimezone(tz)
    ends_local = entry.ends_at_local.astimezone(tz)
    source = (
        db.get(StaffShift, entry.source_shift_id)
        if entry.source_shift_id is not None
        else None
    )
    return ResolvedShift(
        shift_id=source.id if source is not None else None,
        user_id=entry.user_id,
        business_date=entry.business_date,
        starts_at_local=starts_local,
        ends_at_local=ends_local,
        late_grace_period_minutes=int(entry.late_grace_minutes),
        earliest_check_in_minutes=(
            int(source.earliest_check_in_minutes)
            if source is not None
            else 120
        ),
        early_out_grace_minutes=(
            int(source.early_out_grace_minutes)
            if source is not None
            else 0
        ),
        auto_session_close_time=(
            source.auto_session_close_time if source is not None else None
        ),
        max_session_hours=(
            float(source.max_session_hours)
            if source is not None and source.max_session_hours is not None
            else None
        ),
        location_id=(source.location_id if source is not None else None),
        is_override=False,
        schedule_entry_id=entry.id,
        manager_notes=entry.manager_notes,
    )


def _base_shift_candidates(
    db: Session, *, user_id: int, biz_date: date
) -> list[tuple[StaffShift, _Instance]]:
    """All base shifts whose `working_days` covers the ISO weekday of
    `biz_date`, paired with their concrete instances."""
    weekday = biz_date.isoweekday()
    rows = (
        db.query(StaffShift)
        .filter(StaffShift.user_id == user_id)
        .filter(StaffShift.working_days.contains([weekday]))
        .all()
    )
    out: list[tuple[StaffShift, _Instance]] = []
    for s in rows:
        inst = _instance_for_date(s, biz_date, force=False)
        if inst is not None:
            out.append((s, inst))
    return out


def _pick_best_candidate(
    candidates: list[tuple[StaffShift, _Instance]], *, as_of_local: datetime
) -> tuple[StaffShift, _Instance] | None:
    """Disambiguation rule for multi-shift-per-day: prefer the
    instance bracketing `as_of_local`; else the closest upcoming;
    else the closest past."""
    if not candidates:
        return None

    bracketing = [
        c
        for c in candidates
        if c[1].starts_at_local <= as_of_local <= c[1].ends_at_local
    ]
    if bracketing:
        return bracketing[0]

    upcoming = sorted(
        [c for c in candidates if c[1].starts_at_local > as_of_local],
        key=lambda c: c[1].starts_at_local,
    )
    if upcoming:
        return upcoming[0]

    past = sorted(
        [c for c in candidates if c[1].ends_at_local < as_of_local],
        key=lambda c: c[1].ends_at_local,
        reverse=True,
    )
    if past:
        return past[0]
    return None


def resolve_active_shift(
    db: Session,
    *,
    user_id: int,
    as_of_local: datetime | None = None,
) -> ResolvedShift | None:
    """Return the shift the resolver picks for `(user_id, as_of_local)`.

    Precedence (Phase 10 Slice 1):

      1. **Published `staff_schedule_entries` row** covering
         `as_of_local.date()`. Multi-entry days are disambiguated
         identically to base shifts (`_pick_best_entry`).
      2. Override row whose `[starts_on, ends_on]` covers
         `as_of_local.date()`. The override's `shift_id` is applied
         even when the shift's `working_days` would not normally
         match this weekday — overrides are intentional schedule
         changes.
      3. Otherwise the base shift whose `working_days` covers the ISO
         weekday of `as_of_local.date()`, disambiguated by
         `_pick_best_candidate`.
      4. Otherwise None.

    Caller maps None to "use the location/default policy" — that
    fallback is intentionally the cron's responsibility, not the
    resolver's.
    """
    if as_of_local is None:
        as_of_local = business_now()
    elif as_of_local.tzinfo is None:
        as_of_local = as_of_local.replace(tzinfo=shop_tz())
    else:
        as_of_local = as_of_local.astimezone(shop_tz())

    biz_date = as_of_local.date()

    entries = _published_entries_for_date(
        db, user_id=user_id, biz_date=biz_date
    )
    chosen = _pick_best_entry(entries, as_of_local=as_of_local)
    if chosen is not None:
        return _resolved_from_entry(db, chosen)

    override = _active_override(db, user_id=user_id, biz_date=biz_date)
    if override is not None:
        shift = db.get(StaffShift, override.shift_id)
        if shift is not None:
            instance = _instance_for_date(shift, biz_date, force=True)
            if instance is not None:
                return _to_resolved(
                    shift,
                    instance,
                    business_date_=biz_date,
                    is_override=True,
                )

    candidates = _base_shift_candidates(
        db, user_id=user_id, biz_date=biz_date
    )
    pick = _pick_best_candidate(candidates, as_of_local=as_of_local)
    if pick is None:
        return None
    shift, instance = pick
    return _to_resolved(
        shift, instance, business_date_=biz_date, is_override=False
    )


# ---------------------------------------------------------------------------
# Holiday lookup (called from clock_in on punch insert)
# ---------------------------------------------------------------------------


def find_holiday_id(
    db: Session,
    *,
    biz_date: date,
    location_id: int | None,
) -> int | None:
    """Return a `staff_holidays.id` matching the punch's date + location,
    or None.

    Per-location holiday wins over a same-day global (location_id IS
    NULL) so a boutique-specific holiday name takes precedence in
    reporting. Holidays are advisory: `clock_in.punch_in` stamps the
    FK without ever blocking the punch.
    """
    rows = (
        db.query(StaffHoliday)
        .filter(StaffHoliday.holiday_date == biz_date)
        .all()
    )
    if not rows:
        return None
    if location_id is not None:
        for h in rows:
            if h.location_id == location_id:
                return h.id
    for h in rows:
        if h.location_id is None:
            return h.id
    return None


# ---------------------------------------------------------------------------
# Schedule expansion (used by Slice C's /api/sales/schedule)
# ---------------------------------------------------------------------------


def _approved_time_off_intervals(
    db: Session, *, user_id: int, from_date: date, to_date: date
) -> list[tuple[date, date]]:
    """Return `(local_start_date, local_end_date)` pairs for every
    approved time-off request that intersects the requested range."""
    tz = shop_tz()
    range_start = datetime.combine(from_date, time(0, 0), tzinfo=tz)
    range_end = datetime.combine(
        to_date + timedelta(days=1), time(0, 0), tzinfo=tz
    )
    rows = (
        db.query(TimeOffRequest)
        .filter(TimeOffRequest.user_id == user_id)
        .filter(TimeOffRequest.status == "approved")
        .filter(TimeOffRequest.starts_at < range_end)
        .filter(TimeOffRequest.ends_at > range_start)
        .all()
    )
    out: list[tuple[date, date]] = []
    for r in rows:
        local_start = r.starts_at.astimezone(tz).date()
        # An ends_at at midnight should not include that midnight day
        # itself — a request covering "Jul 1 00:00 → Jul 4 00:00" means
        # off for Jul 1, 2, 3.
        end_local = r.ends_at.astimezone(tz)
        if end_local.time() == time(0, 0):
            local_end = end_local.date() - timedelta(days=1)
        else:
            local_end = end_local.date()
        out.append((local_start, local_end))
    return out


def _date_in_intervals(
    biz_date: date, intervals: Iterable[tuple[date, date]]
) -> bool:
    return any(s <= biz_date <= e for s, e in intervals)


def _recurring_unavailable_for_range(
    db: Session, *, user_id: int, from_date: date, to_date: date
) -> dict[date, list[dict]]:
    """Materialize active `recurring_unavailability` rules into a
    `{biz_date: [{starts_at_local, ends_at_local, reason}]}` map for
    the requested range.

    Used by `expand_shifts` to attach a `recurring_unavailable_blocks`
    array to each day's payload — informational only, never suppresses
    the resolved shift. The stylist still needs to see "scheduled 9-5"
    on a day they marked themselves unavailable, so they can flag the
    conflict to the manager.
    """
    tz = shop_tz()
    rows = list(
        db.query(RecurringUnavailability)
        .filter(RecurringUnavailability.user_id == user_id)
        .filter(
            (RecurringUnavailability.effective_until.is_(None))
            | (RecurringUnavailability.effective_until >= from_date)
        )
        .filter(RecurringUnavailability.effective_from <= to_date)
        .all()
    )
    out: dict[date, list[dict]] = {}
    cur = from_date
    while cur <= to_date:
        weekday = cur.isoweekday()
        for r in rows:
            if int(r.weekday) != weekday:
                continue
            if r.effective_from > cur:
                continue
            if r.effective_until is not None and r.effective_until < cur:
                continue
            starts = datetime.combine(cur, r.start_time_local, tzinfo=tz)
            ends = datetime.combine(cur, r.end_time_local, tzinfo=tz)
            out.setdefault(cur, []).append(
                {
                    "block_id": r.id,
                    "starts_at_local": starts.isoformat(),
                    "ends_at_local": ends.isoformat(),
                    "reason": r.reason,
                }
            )
        cur += timedelta(days=1)
    return out


def expand_shifts(
    db: Session,
    *,
    user_id: int,
    from_date: date,
    to_date: date,
    suppress_time_off: bool = True,
) -> list[dict]:
    """Per-day expansion for the stylist's `/schedule` view.

    Returns one entry per business-local date in `[from_date, to_date]`
    with the resolved shift (if any), the time-off suppression flag,
    and an explicit `business_date` so the frontend doesn't have to
    re-derive dates from datetimes. Slice C wires this to
    `GET /api/sales/schedule`.

    `suppress_time_off=True` (default) omits the shift on dates that
    fall inside an approved time-off request and instead tags the day
    with `time_off_suppressed: True`. The cron-facing
    `resolve_active_shift` deliberately does NOT respect this — a
    stylist who somehow punches in on an approved-off day still needs
    the cron to auto-close them.
    """
    if to_date < from_date:
        raise ValueError("to_date must be >= from_date")

    intervals = (
        _approved_time_off_intervals(
            db, user_id=user_id, from_date=from_date, to_date=to_date
        )
        if suppress_time_off
        else []
    )
    recurring_blocks_by_date = _recurring_unavailable_for_range(
        db, user_id=user_id, from_date=from_date, to_date=to_date
    )

    out: list[dict] = []
    cur = from_date
    while cur <= to_date:
        suppressed = (
            suppress_time_off and _date_in_intervals(cur, intervals)
        )
        recurring_blocks = recurring_blocks_by_date.get(cur, [])
        if suppressed:
            out.append(
                {
                    "business_date": cur.isoformat(),
                    "shift": None,
                    "time_off_suppressed": True,
                    "recurring_unavailable_blocks": recurring_blocks,
                }
            )
        else:
            tz = shop_tz()
            anchor = datetime.combine(cur, time(0, 0), tzinfo=tz)
            resolved = resolve_active_shift(
                db, user_id=user_id, as_of_local=anchor
            )
            if resolved is None:
                out.append(
                    {
                        "business_date": cur.isoformat(),
                        "shift": None,
                        "time_off_suppressed": False,
                        "recurring_unavailable_blocks": recurring_blocks,
                    }
                )
            else:
                out.append(
                    {
                        "business_date": cur.isoformat(),
                        "shift": {
                            "shift_id": resolved.shift_id,
                            "schedule_entry_id": resolved.schedule_entry_id,
                            "manager_notes": resolved.manager_notes,
                            "starts_at_local": resolved.starts_at_local.isoformat(),
                            "ends_at_local": resolved.ends_at_local.isoformat(),
                            "is_override": resolved.is_override,
                            "location_id": resolved.location_id,
                            "late_grace_period_minutes": resolved.late_grace_period_minutes,
                            "earliest_check_in_minutes": resolved.earliest_check_in_minutes,
                            "early_out_grace_minutes": resolved.early_out_grace_minutes,
                            "auto_session_close_time": (
                                resolved.auto_session_close_time.isoformat()
                                if resolved.auto_session_close_time
                                else None
                            ),
                            "max_session_hours": resolved.max_session_hours,
                        },
                        "time_off_suppressed": False,
                        "recurring_unavailable_blocks": recurring_blocks,
                    }
                )
        cur += timedelta(days=1)
    return out


__all__ = [
    "ResolvedShift",
    "expand_shifts",
    "find_holiday_id",
    "resolve_active_shift",
]
