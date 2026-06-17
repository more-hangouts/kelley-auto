"""Auto-scheduler: generate manager-reviewable DRAFT shifts for a week.

This is groundwork for Phase 11 (intuitive auto-scheduling). The goal
is to take the manager from a blank weekly grid to a fully-populated
*draft* in one click, without ever publishing on their behalf. Drafts
land in `staff_schedule_entries` with `status='draft'` and the manager
edits/publishes them through the normal grid flow.

Design notes
------------
- Rules live in `AutoScheduleRules` (defaults below). The dataclass is
  shaped so it can be hydrated from a future `auto_schedule_rules`
  table without rewriting the call sites — for now, the service
  constants are the source of truth and the endpoint accepts shallow
  per-call overrides.
- Generation never touches existing rows. If a stylist already has any
  schedule entry (draft OR published) on a given business_date, that
  (stylist, day) pair is skipped. Approved time-off blocks the day for
  that stylist; active recurring unavailability blocks the day's shift
  window. Each count surfaces separately in the result summary.
- Stylist selection is fair across the week: we count entries already
  allocated for the run plus pre-existing entries on the same week,
  and pick the least-loaded stylists first, breaking ties by id for
  determinism. Any stylist with an appointment assignment on the day
  is auto-included (they're already on the hook for the client).
- Closed days (weekdays not in `open_days`) are skipped silently — the
  manager can override `open_days` per-call if they want to schedule a
  closed day exceptionally.

Boundary with `staff_schedule.create_entry`
-------------------------------------------
We call `create_entry(publish=False)` for the actual insert. That
gives us duplicate-check, business-date validation, and late-grace
defaulting for free, and keeps the audit story identical to a
manual draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    StaffScheduleEntry,
    TimeOffRequest,
    User,
)
from services import recurring_availability, staff_schedule
from services.business_time import shop_tz
from services.staff_schedule import StaffScheduleError


# Weekday tokens accepted in `open_days`. ISO weekday: Mon=1 … Sun=7.
_WEEKDAY_TOKENS: dict[str, int] = {
    "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4,
    "Fri": 5, "Sat": 6, "Sun": 7,
}


@dataclass(frozen=True)
class AutoScheduleRules:
    """Configurable rule set. Defaults match the Bella's XV operating
    rhythm (closed Mon/Tue, 12-7 hours, no-appointment fill 2-7)."""

    open_days: tuple[str, ...] = ("Wed", "Thu", "Fri", "Sat", "Sun")
    business_open_time: time = time(12, 0)
    business_close_time: time = time(19, 0)
    no_appointment_shift_start: time = time(14, 0)
    no_appointment_shift_end: time = time(19, 0)
    appointment_buffer_minutes: int = 60
    min_stylists_when_appointments: int = 1
    min_stylists_when_quiet: int = 1
    rotate_fairly: bool = True

    def open_weekdays(self) -> set[int]:
        out: set[int] = set()
        for tok in self.open_days:
            iso = _WEEKDAY_TOKENS.get(tok)
            if iso is None:
                raise StaffScheduleError(
                    "invalid_open_day",
                    http_status=422,
                    extra={"value": tok},
                )
            out.add(iso)
        return out


DEFAULT_RULES = AutoScheduleRules()

# Buffer values the manager dialog exposes. Kept narrow on purpose:
# 30/60/90/120 covers every realistic prep window without letting a
# stray value silently produce 7am shifts.
ALLOWED_APPOINTMENT_BUFFERS: frozenset[int] = frozenset({30, 60, 90, 120})


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    # Accept "HH:MM" and "HH:MM:SS"
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise StaffScheduleError(
            "invalid_time", http_status=422, extra={"value": value}
        )
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise StaffScheduleError(
            "invalid_time", http_status=422, extra={"value": value}
        ) from exc
    return time(h, m, s)


def apply_overrides(base: AutoScheduleRules, overrides: dict | None) -> AutoScheduleRules:
    """Merge per-call overrides onto the base rule set.

    Unknown keys are rejected so a typo can't silently no-op a request.
    Empty / missing dict is a no-op — the manager will usually just
    click Generate and accept the defaults.
    """
    if not overrides:
        return base
    allowed = {
        "open_days",
        "business_open_time",
        "business_close_time",
        "no_appointment_shift_start",
        "no_appointment_shift_end",
        "appointment_buffer_minutes",
        "min_stylists_when_appointments",
        "min_stylists_when_quiet",
        "rotate_fairly",
    }
    unknown = set(overrides) - allowed
    if unknown:
        raise StaffScheduleError(
            "unknown_override",
            http_status=422,
            extra={"fields": sorted(unknown)},
        )
    patch: dict = {}
    if "open_days" in overrides:
        v = overrides["open_days"]
        if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
            raise StaffScheduleError("invalid_open_day", http_status=422)
        patch["open_days"] = tuple(v)
    for key in (
        "business_open_time",
        "business_close_time",
        "no_appointment_shift_start",
        "no_appointment_shift_end",
    ):
        if key in overrides:
            patch[key] = _parse_time(overrides[key])
    if "appointment_buffer_minutes" in overrides:
        try:
            buf = int(overrides["appointment_buffer_minutes"])
        except (TypeError, ValueError) as exc:
            raise StaffScheduleError(
                "invalid_appointment_buffer",
                http_status=422,
                extra={"value": overrides["appointment_buffer_minutes"]},
            ) from exc
        if buf not in ALLOWED_APPOINTMENT_BUFFERS:
            raise StaffScheduleError(
                "invalid_appointment_buffer",
                http_status=422,
                extra={
                    "value": buf,
                    "allowed": sorted(ALLOWED_APPOINTMENT_BUFFERS),
                },
            )
        patch["appointment_buffer_minutes"] = buf
    for key in (
        "min_stylists_when_appointments",
        "min_stylists_when_quiet",
    ):
        if key in overrides:
            try:
                v = int(overrides[key])
            except (TypeError, ValueError) as exc:
                raise StaffScheduleError(
                    "invalid_min_stylists",
                    http_status=422,
                    extra={"field": key, "value": overrides[key]},
                ) from exc
            if v < 1:
                raise StaffScheduleError(
                    "invalid_min_stylists",
                    http_status=422,
                    extra={"field": key, "value": v},
                )
            patch[key] = v
    if "rotate_fairly" in overrides:
        patch["rotate_fairly"] = bool(overrides["rotate_fairly"])

    merged = replace(base, **patch)

    # Cross-field invariants: the manager UX prevents most of these,
    # but the service is the backstop. A bad combination here would
    # silently generate zero shifts or shifts ending before they
    # start, so reject loudly with a stable code.
    if merged.no_appointment_shift_end <= merged.no_appointment_shift_start:
        raise StaffScheduleError(
            "invalid_no_appointment_window",
            http_status=422,
            extra={
                "start": merged.no_appointment_shift_start.strftime("%H:%M"),
                "end": merged.no_appointment_shift_end.strftime("%H:%M"),
            },
        )
    if merged.business_close_time <= merged.business_open_time:
        raise StaffScheduleError(
            "invalid_business_hours",
            http_status=422,
            extra={
                "open": merged.business_open_time.strftime("%H:%M"),
                "close": merged.business_close_time.strftime("%H:%M"),
            },
        )
    return merged


def rules_to_dict(rules: AutoScheduleRules) -> dict:
    """Serialize for the frontend dialog and the result summary."""
    return {
        "open_days": list(rules.open_days),
        "business_open_time": rules.business_open_time.strftime("%H:%M"),
        "business_close_time": rules.business_close_time.strftime("%H:%M"),
        "no_appointment_shift_start": rules.no_appointment_shift_start.strftime(
            "%H:%M"
        ),
        "no_appointment_shift_end": rules.no_appointment_shift_end.strftime(
            "%H:%M"
        ),
        "appointment_buffer_minutes": rules.appointment_buffer_minutes,
        "min_stylists_when_appointments": rules.min_stylists_when_appointments,
        "min_stylists_when_quiet": rules.min_stylists_when_quiet,
        "rotate_fairly": rules.rotate_fairly,
    }


def _week_bounds_utc(week_start: date) -> tuple[datetime, datetime]:
    tz = shop_tz()
    start_local = datetime.combine(week_start, time.min, tzinfo=tz)
    end_local = datetime.combine(
        week_start + timedelta(days=7), time.min, tzinfo=tz
    )
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _active_sales_staff(
    db: Session, *, user_ids: Iterable[int] | None = None
) -> list[User]:
    stmt = (
        select(User)
        .where(User.is_active.is_(True))
        .where(User.role == "sales")
        .order_by(User.id)
    )
    if user_ids is not None:
        ids = list(user_ids)
        if not ids:
            return []
        stmt = stmt.where(User.id.in_(ids))
    return list(db.execute(stmt).scalars().all())


def _existing_entry_keys(
    db: Session, *, week_start: date, user_ids: Iterable[int]
) -> set[tuple[int, date]]:
    """Set of (user_id, business_date) pairs that already have ANY
    schedule entry in the week — draft or published. We skip these so
    generation never overwrites manager intent."""
    ids = list(user_ids)
    if not ids:
        return set()
    stmt = (
        select(
            StaffScheduleEntry.user_id, StaffScheduleEntry.business_date
        )
        .where(StaffScheduleEntry.user_id.in_(ids))
        .where(StaffScheduleEntry.business_date >= week_start)
        .where(
            StaffScheduleEntry.business_date < week_start + timedelta(days=7)
        )
    )
    return {(uid, bd) for uid, bd in db.execute(stmt).all()}


def _approved_time_off_blocks(
    db: Session, *, week_start: date, user_ids: Iterable[int]
) -> list[tuple[int, datetime, datetime]]:
    """Approved time-off intersecting the week. Used to compute
    per-day blocked (user_id, date) pairs."""
    ids = list(user_ids)
    if not ids:
        return []
    week_start_utc, week_end_utc = _week_bounds_utc(week_start)
    stmt = (
        select(
            TimeOffRequest.user_id,
            TimeOffRequest.starts_at,
            TimeOffRequest.ends_at,
        )
        .where(TimeOffRequest.user_id.in_(ids))
        .where(TimeOffRequest.status == "approved")
        .where(TimeOffRequest.starts_at < week_end_utc)
        .where(TimeOffRequest.ends_at > week_start_utc)
    )
    return [(uid, s, e) for uid, s, e in db.execute(stmt).all()]


def _time_off_blocked_dates(
    blocks: list[tuple[int, datetime, datetime]], *, week_start: date
) -> set[tuple[int, date]]:
    """Project approved time-off intervals onto the per-day grid.

    A time-off interval blocks every business_date whose [00:00, next
    day 00:00) (local) window intersects the interval. We compare in
    UTC, anchored on the shop tz, the same way `list_week` does so the
    grid and the auto-scheduler agree about which days are blocked.
    """
    tz = shop_tz()
    blocked: set[tuple[int, date]] = set()
    for i in range(7):
        d = week_start + timedelta(days=i)
        day_start_utc = datetime.combine(d, time.min, tzinfo=tz).astimezone(
            timezone.utc
        )
        day_end_utc = datetime.combine(
            d + timedelta(days=1), time.min, tzinfo=tz
        ).astimezone(timezone.utc)
        for uid, s, e in blocks:
            if s < day_end_utc and e > day_start_utc:
                blocked.add((uid, d))
    return blocked


def _appointments_by_day(
    db: Session, *, week_start: date
) -> dict[date, list[Appointment]]:
    """Active appointments grouped by their local business date.

    'Active' = anything not cancelled. A no-show still counted as a
    booked slot for scheduling purposes — the stylist had to be there.
    """
    week_start_utc, week_end_utc = _week_bounds_utc(week_start)
    stmt = (
        select(Appointment)
        .where(Appointment.slot_start_at >= week_start_utc)
        .where(Appointment.slot_start_at < week_end_utc)
        .where(Appointment.status != "cancelled")
        .order_by(Appointment.slot_start_at)
    )
    tz = shop_tz()
    out: dict[date, list[Appointment]] = {}
    for appt in db.execute(stmt).scalars().all():
        local_date = appt.slot_start_at.astimezone(tz).date()
        out.setdefault(local_date, []).append(appt)
    return out


def _shift_window_for_day(
    rules: AutoScheduleRules,
    *,
    day: date,
    appointments: list[Appointment],
) -> tuple[time, time]:
    """Pick (start_time, end_time) for a given day.

    - With appointments: end = business_close_time; start = first
      appointment's local time minus `appointment_buffer_minutes`,
      floored at business_open_time.
    - Without appointments: the configured no-appointment window.
    """
    if not appointments:
        return (
            rules.no_appointment_shift_start,
            rules.no_appointment_shift_end,
        )
    tz = shop_tz()
    first_local = min(a.slot_start_at for a in appointments).astimezone(tz)
    buffered = first_local - timedelta(
        minutes=rules.appointment_buffer_minutes
    )
    buffered_t = buffered.time().replace(second=0, microsecond=0)
    start_t = max(buffered_t, rules.business_open_time)
    return (start_t, rules.business_close_time)


def _select_stylists(
    *,
    day: date,
    appointments: list[Appointment],
    available: list[User],
    blocked_pairs: set[tuple[int, date]],
    existing_pairs: set[tuple[int, date]],
    load_so_far: dict[int, int],
    rules: AutoScheduleRules,
) -> list[User]:
    """Pick which stylists to draft for a single day.

    Rule of thumb: anyone with an appointment assignment that day MUST
    be on the schedule (so they can attend their client). After that,
    fill up to the minimum coverage threshold from the least-loaded
    available stylists. Time-off and existing-entry conflicts are
    filtered out, and pre-existing entries on this day count toward
    the coverage target — re-running the generator must be idempotent.
    """
    by_id: dict[int, User] = {u.id: u for u in available}

    def eligible(uid: int) -> bool:
        if (uid, day) in blocked_pairs:
            return False
        if (uid, day) in existing_pairs:
            return False
        return uid in by_id

    chosen: dict[int, User] = {}
    for appt in appointments:
        uid = appt.assigned_user_id
        if uid is not None and eligible(uid) and uid not in chosen:
            chosen[uid] = by_id[uid]

    base_target = (
        rules.min_stylists_when_appointments
        if appointments
        else rules.min_stylists_when_quiet
    )
    existing_day_count = sum(
        1 for u in available if (u.id, day) in existing_pairs
    )
    remaining = base_target - existing_day_count - len(chosen)
    if remaining > 0:
        pool = [
            u
            for u in available
            if eligible(u.id) and u.id not in chosen
        ]
        if rules.rotate_fairly:
            pool.sort(key=lambda u: (load_so_far.get(u.id, 0), u.id))
        else:
            pool.sort(key=lambda u: u.id)
        for u in pool[:remaining]:
            chosen[u.id] = u

    return list(chosen.values())


def generate_draft_week(
    db: Session,
    *,
    actor_user_id: int,
    week_start: date,
    overrides: dict | None = None,
    user_ids: Iterable[int] | None = None,
) -> dict:
    """Populate DRAFT shifts for an empty (or partially empty) week.

    Returns a summary the UI shows the manager so they understand
    *exactly* what changed — never an opaque "Done."

    `user_ids` is an optional scope: when provided, only those active
    sales users are eligible (used by smoke tests, and reserved for a
    future "regenerate for these stylists" verb).
    """
    if week_start.isoweekday() != 1:
        raise StaffScheduleError(
            "week_start_not_monday", http_status=422
        )

    rules = apply_overrides(DEFAULT_RULES, overrides)
    open_isos = rules.open_weekdays()
    tz = shop_tz()

    staff = _active_sales_staff(db, user_ids=user_ids)
    staff_ids = [u.id for u in staff]
    existing_pairs = _existing_entry_keys(
        db, week_start=week_start, user_ids=staff_ids
    )
    time_off_blocks = _approved_time_off_blocks(
        db, week_start=week_start, user_ids=staff_ids
    )
    blocked_pairs = _time_off_blocked_dates(
        time_off_blocks, week_start=week_start
    )
    # Recurring unavailability is a self-serve weekly blocker (Epic 3.4).
    # Publish later hard-skips any draft that overlaps an active rule, so
    # generating those drafts just creates work the manager can't publish.
    # Treat it as an eligibility blocker here too, scoped to the day's
    # computed shift window (a 9–11am block shouldn't bar a 2–6pm shift).
    unavail_by_user_date: dict[
        tuple[int, date], list[tuple[datetime, datetime]]
    ] = {}
    for blk in recurring_availability.expand_blocks_for_week(
        db, week_start=week_start, user_ids=staff_ids or None
    ):
        bd = date.fromisoformat(blk["business_date"])
        unavail_by_user_date.setdefault((blk["user_id"], bd), []).append(
            (
                datetime.fromisoformat(blk["starts_at_local"]),
                datetime.fromisoformat(blk["ends_at_local"]),
            )
        )
    appts_by_day = _appointments_by_day(db, week_start=week_start)

    def _unavailable(
        uid: int, day: date, starts_at: datetime, ends_at: datetime
    ) -> bool:
        for s, e in unavail_by_user_date.get((uid, day), ()):
            if s < ends_at and e > starts_at:
                return True
        return False

    created: list[dict] = []
    warnings: list[str] = []
    skipped_existing = 0
    skipped_time_off = 0
    skipped_unavailable = 0
    skipped_closed_days = 0
    load: dict[int, int] = {u.id: 0 for u in staff}
    for uid, _bd in existing_pairs:
        if uid in load:
            load[uid] += 1

    if not staff:
        warnings.append("No active sales staff to schedule.")

    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if day.isoweekday() not in open_isos:
            skipped_closed_days += 1
            continue

        day_appts = appts_by_day.get(day, [])
        start_t, end_t = _shift_window_for_day(
            rules, day=day, appointments=day_appts
        )
        starts_at = datetime.combine(day, start_t, tzinfo=tz)
        ends_at = datetime.combine(day, end_t, tzinfo=tz)

        # Stylists whose recurring unavailability overlaps THIS day's
        # computed shift window. Merged into the blocker set so selection
        # never drafts a shift the publish step would reject.
        unavailable_pairs = {
            (u.id, day)
            for u in staff
            if _unavailable(u.id, day, starts_at, ends_at)
        }

        # Audit: count would-be-eligible stylists who are blocked, so
        # the manager sees "skipped 3 because of time off" instead of
        # silently shrinking coverage. Pre-existing entries win (the
        # manager already has those rows), then time off, then recurring
        # unavailability — one reason per stylist per day.
        for u in staff:
            pair = (u.id, day)
            if pair in existing_pairs:
                skipped_existing += 1
            elif pair in blocked_pairs:
                skipped_time_off += 1
            elif pair in unavailable_pairs:
                skipped_unavailable += 1

        if not staff:
            continue

        picked = _select_stylists(
            day=day,
            appointments=day_appts,
            available=staff,
            blocked_pairs=blocked_pairs | unavailable_pairs,
            existing_pairs=existing_pairs,
            load_so_far=load,
            rules=rules,
        )
        if not picked:
            if day_appts:
                warnings.append(
                    f"{day.isoformat()}: appointments exist but no "
                    "stylist was available (all on time off, "
                    "unavailable, or already scheduled)."
                )
            continue

        if ends_at <= starts_at:
            warnings.append(
                f"{day.isoformat()}: computed shift end is not after "
                "start; skipping."
            )
            continue

        for u in picked:
            try:
                row = staff_schedule.create_entry(
                    db,
                    actor_user_id=actor_user_id,
                    user_id=u.id,
                    business_date_=day,
                    starts_at_local=starts_at,
                    ends_at_local=ends_at,
                    source="manual",
                    publish=False,
                )
            except StaffScheduleError as exc:
                # Duplicate or validation race — treat as "existing"
                # so the summary stays honest without aborting the
                # rest of the week.
                if exc.code == "duplicate_entry":
                    skipped_existing += 1
                    continue
                raise
            created.append(row)
            load[u.id] = load.get(u.id, 0) + 1
            existing_pairs.add((u.id, day))

    return {
        "week_start": week_start.isoformat(),
        "created_count": len(created),
        "skipped_existing_count": skipped_existing,
        "skipped_time_off_count": skipped_time_off,
        "skipped_unavailable_count": skipped_unavailable,
        "skipped_closed_days_count": skipped_closed_days,
        "warnings": warnings,
        "rules": rules_to_dict(rules),
        "created": created,
    }
