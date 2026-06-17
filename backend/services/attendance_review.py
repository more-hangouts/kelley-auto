"""Owner attendance review service (Phase 7 Slice 2B-2).

Read + write paths for the owner attendance review surface:

  - `list_punches(...)` — bounded by business-local date range; supports
    a "today", "current_week" (Mon-Sun), "pay_period" (placeholder
    until Phase 8 wires shifts), or explicit `from_date`/`to_date`
    window. Optional staff filter and review-queue filter.
  - `daily_totals(...)` / `weekly_totals(...)` — paired hours totals
    derived from punch-in/punch-out pairs in the same window.
  - `confirm_hours(...)` — owner or stylist marks an auto-closed or
    needs-review punch as `confirmed` after the listed hours match.
  - `manual_adjust(...)` — owner edits a punch's `punched_at` after
    the fact. Append-only: the prior values land in
    `staff_punch_audit_events`, never on the punch row itself.
  - `void_punch(...)` — owner flags a punch as `void`. The row stays
    so historical attribution is intact.
  - `submit_correction_request(...)` / `decide_correction_request(...)`
    — stylist files a "I forgot to clock out" request; owner approves
    or denies. Approval applies the proposed change in the same shape
    as `manual_adjust` and writes the same audit row, plus stamps the
    correction request row's decision fields.

User's slice 2B-2 directives baked in:

  - "Use `business_date` everywhere in the attendance filters" —
    every date column comparison is rewritten as a half-open UTC
    interval whose bounds came from `services.business_time`. The
    column itself is queried in raw UTC so the existing
    `idx_staff_punches_user_day` index stays usable.
  - "Expose both local display time and UTC timestamp in API
    responses" — every punch dict carries `punched_at` (UTC ISO),
    `punched_at_local` (boutique-local ISO), and `business_date`
    (boutique-local date the punch falls on).
  - "Owner adjustments are append-only/audited. No hard deletes." —
    `manual_adjust` and `void_punch` stamp `staff_punches.status` to
    `manual_adjusted` / `void` and write a before/after audit row.
    There is no DELETE path on a punch.
  - "Correction approval is separate from manual punch adjustment."
    — `decide_correction_request` takes a `status` of `approved` /
    `denied` and is its own service entrypoint; `manual_adjust` is
    the path the owner uses for everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Literal, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import (
    BusinessProfile,
    StaffLocation,
    StaffPunch,
    StaffPunchAuditEvent,
    StaffPunchCorrectionRequest,
    User,
)
from services import clock_in
from services.business_time import business_date, business_now, shop_tz, to_business_local


_VALID_BUCKETS: frozenset[str] = frozenset({"day", "week", "biweek", "month"})


# Codes the router maps to HTTP statuses. Kept stable for the smoke +
# the frontend toast copy.
class AttendanceReviewError(Exception):
    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


# Review queue criteria the user explicitly listed in Slice 2B-2:
# "unscheduled, late, early-out, auto-closed, correction-requested,
# needs-review." Mapped here once so the router and the smoke share
# a single source of truth.
_REVIEW_STATUSES: frozenset[str] = frozenset(
    {"late", "early_out", "unscheduled", "manual_adjusted", "void"}
)


@dataclass(frozen=True)
class _DateWindow:
    """Half-open UTC interval covering a business-local date range,
    plus the local date strings the API echoes back."""

    start_utc: datetime
    end_utc: datetime
    from_date: date
    to_date: date  # inclusive — last business-local date the window covers

    @property
    def from_iso(self) -> str:
        return self.from_date.isoformat()

    @property
    def to_iso(self) -> str:
        return self.to_date.isoformat()


def _local_day_window(from_date: date, to_date: date) -> _DateWindow:
    """Convert a closed business-local date range to a half-open UTC
    interval. `to_date` is inclusive (the user's "to today" really
    means "through the end of today"), so we add one day at the local
    boundary before converting."""
    if to_date < from_date:
        raise AttendanceReviewError("invalid_date_range", http_status=422)
    tz = shop_tz()
    start_local = datetime.combine(from_date, time.min, tzinfo=tz)
    end_local = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
    return _DateWindow(
        start_utc=start_local.astimezone(timezone.utc),
        end_utc=end_local.astimezone(timezone.utc),
        from_date=from_date,
        to_date=to_date,
    )


def _read_biweekly_anchor(db: Session) -> date | None:
    """Read `business_profile.biweekly_anchor_date`. None when the
    singleton row doesn't exist (fresh install) or the owner hasn't
    set the anchor yet."""
    profile = db.query(BusinessProfile).first()
    if profile is None:
        return None
    return profile.biweekly_anchor_date


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_window(year: int, quarter: int) -> _DateWindow:
    start_month = 1 + (quarter - 1) * 3
    start = date(year, start_month, 1)
    end_month = start_month + 2
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, end_month + 1, 1) - timedelta(days=1)
    return _local_day_window(start, end)


def _month_window(year: int, month: int) -> _DateWindow:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return _local_day_window(start, end)


def _aligned_biweek_window(today: date, anchor: date) -> _DateWindow:
    """14-day window containing `today`, aligned to `anchor` so that
    the window always starts on a date congruent to the anchor mod-14.
    Python's modulo handles negative diffs (today before anchor) for
    free."""
    offset = (today - anchor).days % 14
    start = today - timedelta(days=offset)
    end = start + timedelta(days=13)
    return _local_day_window(start, end)


def resolve_window(
    *,
    range_key: str | None,
    from_date: date | None,
    to_date: date | None,
    biweekly_anchor: date | None = None,
) -> _DateWindow:
    """Pick the date window for an attendance read.

    Precedence:
      - explicit `from_date`/`to_date` (both required when used)
      - `range_key='today'` — today only
      - `range_key='current_week'` — Mon-Sun of business-local now
      - `range_key='current_month'` / `last_month` — calendar months
      - `range_key='current_quarter'` / `last_quarter` — calendar
        quarters (Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec)
      - `range_key='pay_period'` — when `biweekly_anchor` is set,
        returns the 14-day window containing today aligned to the
        anchor. When unset, falls back to the legacy rolling window
        (today + previous 13 days) so old frontends keep working.
      - default: today

    All dates are interpreted in the boutique's local timezone so a
    Saturday-night-into-Sunday-morning shift is attributed to Saturday.
    """
    if from_date is not None and to_date is not None:
        return _local_day_window(from_date, to_date)
    if from_date is not None or to_date is not None:
        raise AttendanceReviewError(
            "incomplete_date_range", http_status=422
        )

    today = business_date()
    key = (range_key or "today").lower()
    if key == "today":
        return _local_day_window(today, today)
    if key == "current_week":
        # Monday-anchored week that contains today.
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return _local_day_window(start, end)
    if key == "current_month":
        return _month_window(today.year, today.month)
    if key == "last_month":
        if today.month == 1:
            return _month_window(today.year - 1, 12)
        return _month_window(today.year, today.month - 1)
    if key == "current_quarter":
        return _quarter_window(today.year, _quarter_of(today))
    if key == "last_quarter":
        q = _quarter_of(today)
        if q == 1:
            return _quarter_window(today.year - 1, 4)
        return _quarter_window(today.year, q - 1)
    if key == "pay_period":
        if biweekly_anchor is not None:
            return _aligned_biweek_window(today, biweekly_anchor)
        # Legacy fallback: rolling 14-day window. Kept so the existing
        # frontend's "Last 14 days" preset keeps producing the same
        # window when the owner hasn't seeded an anchor yet.
        start = today - timedelta(days=13)
        return _local_day_window(start, today)
    raise AttendanceReviewError("invalid_range_key", http_status=422)


def _week_key(d: date) -> str:
    """ISO Mon-Sun week, identified by the Monday's date string."""
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _biweek_key(d: date, anchor: date) -> str:
    """14-day window aligned to anchor, identified by its start date."""
    offset = (d - anchor).days % 14
    start = d - timedelta(days=offset)
    return start.isoformat()


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _rebucket(
    per_day: dict[str, float],
    *,
    bucket: str,
    biweekly_anchor: date | None,
) -> dict[str, float]:
    """Aggregate a daily totals map into the requested bucket.

    `bucket='day'` returns the input unchanged. The caller is
    responsible for verifying that biweekly_anchor is set before
    requesting `bucket='biweek'`.
    """
    if bucket == "day":
        return dict(per_day)
    out: dict[str, float] = {}
    for day_iso, hours in per_day.items():
        d = date.fromisoformat(day_iso)
        if bucket == "week":
            key = _week_key(d)
        elif bucket == "biweek":
            assert biweekly_anchor is not None, (
                "biweek rebucket requires an anchor; caller should validate"
            )
            key = _biweek_key(d, biweekly_anchor)
        elif bucket == "month":
            key = _month_key(d)
        else:
            raise AttendanceReviewError("invalid_bucket", http_status=422)
        out[key] = out.get(key, 0.0) + hours
    return out


def _row_to_dict(p: StaffPunch, *, location_name: str | None) -> dict:
    """Shape every API response uses for a punch.

    `punched_at` stays UTC (the canonical timestamp the database
    stores); `punched_at_local` is the boutique-local rendering the UI
    displays. `business_date` is the local calendar date the punch
    counts toward — important across DST and across the 11pm-1am
    window where UTC and local disagree on the date.
    """
    local = to_business_local(p.punched_at)
    return {
        "id": p.id,
        "user_id": p.user_id,
        "direction": p.direction,
        "status": p.status,
        "punched_at": p.punched_at.astimezone(timezone.utc).isoformat(),
        "punched_at_local": local.isoformat(),
        "business_date": local.date().isoformat(),
        "location_id": p.location_id,
        "location_name": location_name,
        "client_latitude": (
            float(p.client_latitude) if p.client_latitude is not None else None
        ),
        "client_longitude": (
            float(p.client_longitude) if p.client_longitude is not None else None
        ),
        "client_accuracy_m": (
            float(p.client_accuracy_m)
            if p.client_accuracy_m is not None
            else None
        ),
        "distance_to_location_m": (
            float(p.distance_to_location_m)
            if p.distance_to_location_m is not None
            else None
        ),
        "selfie_storage_key": p.selfie_storage_key,
        "auto_closed": bool(p.auto_closed),
        "auto_close_reason": p.auto_close_reason,
        "auto_closed_at": (
            p.auto_closed_at.astimezone(timezone.utc).isoformat()
            if p.auto_closed_at is not None
            else None
        ),
        "hours_confirmation_status": p.hours_confirmation_status,
        "hours_confirmed_by_user_id": p.hours_confirmed_by_user_id,
        "hours_confirmed_at": (
            p.hours_confirmed_at.astimezone(timezone.utc).isoformat()
            if p.hours_confirmed_at is not None
            else None
        ),
        "notes": p.notes,
    }


def _staff_filter_predicate(staff_user_id: int | None):
    if staff_user_id is None:
        return None
    return StaffPunch.user_id == staff_user_id


def _review_queue_predicate(active: bool):
    """Predicate that captures the review-queue criteria the user
    listed in Slice 2B-2: late, early_out, unscheduled, manual_adjusted,
    void, auto_closed=True, or hours_confirmation_status in
    (needs_review, adjusted)."""
    if not active:
        return None
    return or_(
        StaffPunch.status.in_(list(_REVIEW_STATUSES)),
        StaffPunch.auto_closed.is_(True),
        StaffPunch.hours_confirmation_status.in_(["needs_review", "adjusted"]),
    )


def list_punches(
    db: Session,
    *,
    range_key: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    staff_user_id: int | None = None,
    review_queue_only: bool = False,
) -> dict:
    """Date-bounded punch list. Returns the window echoed back, the
    rows in the window, and the matching review-queue subset count so
    the UI can render a "needs review" badge without a second query.

    The query is a single SELECT over `staff_punches` plus a small
    location-name backfill. Punch counts at boutique scale are bounded
    (single-digit staff × ~10 punches/day), so we don't paginate at
    this layer; the `to_date` cap is the bound."""
    window = resolve_window(
        range_key=range_key,
        from_date=from_date,
        to_date=to_date,
        biweekly_anchor=_read_biweekly_anchor(db),
    )

    stmt = (
        select(StaffPunch)
        .where(StaffPunch.punched_at >= window.start_utc)
        .where(StaffPunch.punched_at < window.end_utc)
        .order_by(StaffPunch.punched_at, StaffPunch.id)
    )
    staff_pred = _staff_filter_predicate(staff_user_id)
    if staff_pred is not None:
        stmt = stmt.where(staff_pred)
    review_pred = _review_queue_predicate(review_queue_only)
    if review_pred is not None:
        stmt = stmt.where(review_pred)

    rows: Sequence[StaffPunch] = db.execute(stmt).scalars().all()

    # Single batched location-name lookup so the response is N+1-free
    # even with many distinct locations (Bellas has one today; a
    # second-store rollout shouldn't degrade this query).
    location_ids = {p.location_id for p in rows if p.location_id is not None}
    location_map: dict[int, str] = {}
    if location_ids:
        loc_rows = (
            db.execute(
                select(StaffLocation).where(StaffLocation.id.in_(list(location_ids)))
            )
            .scalars()
            .all()
        )
        location_map = {l.id: l.name for l in loc_rows}

    # Review-queue count covers the entire window even when the caller
    # didn't filter — the "Needs review" badge in the UI should reflect
    # the whole window, not the post-filter slice.
    badge_stmt = (
        select(func.count(StaffPunch.id))
        .where(StaffPunch.punched_at >= window.start_utc)
        .where(StaffPunch.punched_at < window.end_utc)
    )
    if staff_pred is not None:
        badge_stmt = badge_stmt.where(staff_pred)
    badge_stmt = badge_stmt.where(_review_queue_predicate(True))
    review_count = int(db.execute(badge_stmt).scalar() or 0)

    return {
        "from_date": window.from_iso,
        "to_date": window.to_iso,
        "timezone": str(shop_tz()),
        "review_queue_count": review_count,
        "punches": [
            _row_to_dict(p, location_name=location_map.get(p.location_id))
            for p in rows
        ],
    }


def _pair_hours_by_day(rows: Sequence[StaffPunch]) -> dict[str, float]:
    """Walk a chronological list of punches per user and add up the
    paired in→out durations bucketed by the in-punch's local
    business_date. Unmatched (open) punches contribute zero. Punch-out
    rows that close a session land in the same bucket as their
    paired punch-in even when the out crosses midnight — the day's
    total reflects "shift started today"."""
    totals: dict[str, float] = {}
    open_in: StaffPunch | None = None
    for p in rows:
        if p.status == "void":
            continue
        if p.direction == "in":
            open_in = p
            continue
        if open_in is None:
            continue
        delta = (p.punched_at - open_in.punched_at).total_seconds()
        if delta > 0:
            local_day = to_business_local(open_in.punched_at).date().isoformat()
            totals[local_day] = totals.get(local_day, 0.0) + delta / 3600.0
        open_in = None
    return totals


def staff_totals(
    db: Session,
    *,
    range_key: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    bucket: str = "day",
) -> dict:
    """Per-staff hours totals for a window: daily breakdown + sum,
    plus an optional re-bucketing into ISO weeks, anchor-aligned
    biweeks, or calendar months.

    Used by `/reports/attendance` for the totals panel. Pairing
    happens in Python because the data set is small and we already
    have to walk every row to assign each pair to its business-local
    day. Re-bucketing is a one-pass aggregation over the daily map,
    so the existing `by_day` shape is preserved verbatim and `by_bucket`
    is added in parallel.
    """
    bucket = (bucket or "day").lower()
    if bucket not in _VALID_BUCKETS:
        raise AttendanceReviewError("invalid_bucket", http_status=422)

    biweekly_anchor = _read_biweekly_anchor(db)
    if bucket == "biweek" and biweekly_anchor is None:
        raise AttendanceReviewError(
            "pay_period_anchor_missing", http_status=422
        )

    window = resolve_window(
        range_key=range_key,
        from_date=from_date,
        to_date=to_date,
        biweekly_anchor=biweekly_anchor,
    )

    stmt = (
        select(StaffPunch)
        .where(StaffPunch.punched_at >= window.start_utc)
        .where(StaffPunch.punched_at < window.end_utc)
        .order_by(StaffPunch.user_id, StaffPunch.punched_at, StaffPunch.id)
    )
    rows = db.execute(stmt).scalars().all()

    # Group rows by user, then pair within each user's stream so a
    # punch-in by Maria never closes a punch-out by Jordan.
    grouped: dict[int, list[StaffPunch]] = {}
    for p in rows:
        grouped.setdefault(p.user_id, []).append(p)

    user_ids = list(grouped.keys())
    user_map: dict[int, User] = {}
    if user_ids:
        urows = (
            db.execute(select(User).where(User.id.in_(user_ids)))
            .scalars()
            .all()
        )
        user_map = {u.id: u for u in urows}

    out: list[dict] = []
    for uid, user_rows in grouped.items():
        per_day = _pair_hours_by_day(user_rows)
        total = sum(per_day.values())
        per_bucket = _rebucket(
            per_day, bucket=bucket, biweekly_anchor=biweekly_anchor
        )
        u = user_map.get(uid)
        out.append(
            {
                "user_id": uid,
                "username": u.username if u else None,
                "full_name": u.full_name if u else None,
                "total_hours": round(total, 2),
                "by_day": [
                    {"business_date": d, "hours": round(h, 2)}
                    for d, h in sorted(per_day.items())
                ],
                "by_bucket": [
                    {"bucket_key": k, "hours": round(h, 2)}
                    for k, h in sorted(per_bucket.items())
                ],
            }
        )

    out.sort(key=lambda r: (r["full_name"] or r["username"] or ""))
    return {
        "from_date": window.from_iso,
        "to_date": window.to_iso,
        "timezone": str(shop_tz()),
        "bucket": bucket,
        "biweekly_anchor_date": (
            biweekly_anchor.isoformat() if biweekly_anchor is not None else None
        ),
        "totals": out,
    }


# ---------------------------------------------------------------------------
# Append-only audit helpers
# ---------------------------------------------------------------------------


def _audit(
    db: Session,
    *,
    punch: StaffPunch | None,
    actor_user_id: int | None,
    actor_kind: str,
    action: str,
    reason_code: str | None,
    old_values: dict,
    new_values: dict,
    notes: str | None = None,
) -> None:
    """Write one append-only audit row. Punch state is already mutated
    on the in-memory `punch` by the caller; this helper just persists
    the before/after diff so the timeline survives further edits."""
    db.add(
        StaffPunchAuditEvent(
            punch_id=punch.id if punch is not None else None,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            action=action,
            reason_code=reason_code,
            old_values=old_values,
            new_values=new_values,
            notes=notes,
        )
    )
    db.flush()


# ---------------------------------------------------------------------------
# Confirm / adjust / void
# ---------------------------------------------------------------------------


_ConfirmActor = Literal["owner", "staff"]


def confirm_hours(
    db: Session,
    *,
    punch_id: int,
    actor_user_id: int,
    actor_kind: _ConfirmActor,
) -> dict:
    """Mark an auto-closed or `needs_review` punch as `confirmed`.

    Owner uses this from the review queue. Stylist uses it from the
    sales-side "System closed, confirm hours" prompt. Both paths write
    the same audit row so the timeline reads the same regardless of
    who confirmed.

    No-ops past the first call (idempotent) — re-tapping confirm just
    returns the row unchanged."""
    punch = db.get(StaffPunch, punch_id)
    if punch is None:
        raise AttendanceReviewError("punch_not_found", http_status=404)

    if punch.hours_confirmation_status == "confirmed":
        return _row_to_dict(punch, location_name=_location_name(db, punch))

    if punch.hours_confirmation_status not in ("needs_review", "adjusted"):
        raise AttendanceReviewError(
            "punch_not_in_review", http_status=409
        )

    old = {
        "hours_confirmation_status": punch.hours_confirmation_status,
        "hours_confirmed_by_user_id": punch.hours_confirmed_by_user_id,
    }
    punch.hours_confirmation_status = "confirmed"
    punch.hours_confirmed_by_user_id = actor_user_id
    punch.hours_confirmed_at = business_now().astimezone(timezone.utc)
    db.flush()

    _audit(
        db,
        punch=punch,
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
        action="punch.hours_confirmed",
        reason_code="hours_confirmed",
        old_values=old,
        new_values={
            "hours_confirmation_status": "confirmed",
            "hours_confirmed_by_user_id": actor_user_id,
        },
    )

    return _row_to_dict(punch, location_name=_location_name(db, punch))


def manual_adjust(
    db: Session,
    *,
    punch_id: int,
    new_punched_at: datetime,
    reason: str,
    actor_user_id: int,
) -> dict:
    """Owner edits a punch's `punched_at`. Append-only.

    The new timestamp must be timezone-aware. We never silently
    rewrite a `void` punch — the operator should un-void first if
    they want to bring an old row back into the timeline.

    Stamps `status='manual_adjusted'` so the row keeps showing up in
    the review queue until an owner explicitly confirms hours afterward.
    """
    if new_punched_at.tzinfo is None:
        raise AttendanceReviewError("naive_datetime", http_status=422)
    if not reason or not reason.strip():
        raise AttendanceReviewError("reason_required", http_status=422)

    punch = db.get(StaffPunch, punch_id)
    if punch is None:
        raise AttendanceReviewError("punch_not_found", http_status=404)
    if punch.status == "void":
        raise AttendanceReviewError("punch_void", http_status=409)

    old_at = punch.punched_at
    old_status = punch.status

    punch.punched_at = new_punched_at.astimezone(timezone.utc)
    punch.status = "manual_adjusted"
    punch.hours_confirmation_status = "adjusted"
    db.flush()

    _audit(
        db,
        punch=punch,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action="punch.manual_adjusted",
        reason_code="manual_adjustment",
        old_values={
            "punched_at": old_at.astimezone(timezone.utc).isoformat(),
            "status": old_status,
        },
        new_values={
            "punched_at": punch.punched_at.astimezone(timezone.utc).isoformat(),
            "status": "manual_adjusted",
        },
        notes=reason.strip(),
    )

    return _row_to_dict(punch, location_name=_location_name(db, punch))


def void_punch(
    db: Session,
    *,
    punch_id: int,
    reason: str,
    actor_user_id: int,
) -> dict:
    """Mark a punch as `void` without deleting it. Idempotent."""
    if not reason or not reason.strip():
        raise AttendanceReviewError("reason_required", http_status=422)

    punch = db.get(StaffPunch, punch_id)
    if punch is None:
        raise AttendanceReviewError("punch_not_found", http_status=404)
    if punch.status == "void":
        return _row_to_dict(punch, location_name=_location_name(db, punch))

    old_status = punch.status
    punch.status = "void"
    db.flush()

    _audit(
        db,
        punch=punch,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action="punch.voided",
        reason_code="void",
        old_values={"status": old_status},
        new_values={"status": "void"},
        notes=reason.strip(),
    )
    return _row_to_dict(punch, location_name=_location_name(db, punch))


# ---------------------------------------------------------------------------
# Currently-open sessions + owner clock-out
# ---------------------------------------------------------------------------


# Same roster the auto-close cron walks (services.attendance_close).
# Keep the two in sync so "who can be clocked in" has one definition.
_STAFF_ROLES: tuple[str, ...] = ("sales", "user", "admin")


def _open_in_punch_for(db: Session, user_id: int) -> StaffPunch | None:
    """The user's current open punch (their latest non-void punch when
    it's a direction='in'), or None when they're clocked out."""
    state, last = clock_in.current_status(db, user_id)
    if state == "in" and last is not None and last.direction == "in":
        return last
    return None


def list_open_sessions(db: Session) -> dict:
    """Every staffer currently clocked in, with how long each session
    has been open. Drives the admin 'On the clock now' panel plus its
    per-person and bulk clock-out actions.

    Deliberately NOT date-bounded: a forgotten session opened days ago
    still surfaces here even though its in-punch falls outside the
    review window the rest of this page uses."""
    users = (
        db.execute(select(User).where(User.role.in_(_STAFF_ROLES)))
        .scalars()
        .all()
    )
    now_utc = business_now().astimezone(timezone.utc)
    sessions: list[dict] = []
    for u in users:
        in_punch = _open_in_punch_for(db, u.id)
        if in_punch is None:
            continue
        opened = in_punch.punched_at.astimezone(timezone.utc)
        local = to_business_local(in_punch.punched_at)
        sessions.append(
            {
                "user_id": u.id,
                "full_name": u.full_name,
                "username": u.username,
                "in_punch_id": in_punch.id,
                "punched_at": opened.isoformat(),
                "punched_at_local": local.isoformat(),
                "business_date": local.date().isoformat(),
                "location_name": _location_name(db, in_punch),
                "hours_open": round(
                    (now_utc - opened).total_seconds() / 3600.0, 2
                ),
            }
        )
    # Oldest open session first — the most likely "forgot to clock out".
    sessions.sort(key=lambda s: s["punched_at"])
    return {
        "server_now": now_utc.isoformat(),
        "timezone": str(shop_tz()),
        "open_sessions": sessions,
    }


def _force_clock_out(
    db: Session,
    *,
    in_punch: StaffPunch,
    actor_user_id: int,
    reason: str | None,
) -> StaffPunch:
    """Close one open session on the owner's behalf.

    Reuses `clock_in.punch_out` for the state machine, status
    classification, and schedule-entry stamping, then flags the
    resulting pair for review and writes an owner audit row. The close
    time is "now" (when the owner acted), which is not necessarily when
    the staffer actually left — so the hours land in `needs_review` and
    the owner can `Adjust` the out time afterward. The caller has
    already verified `in_punch` is the user's current open punch."""
    user = db.get(User, in_punch.user_id)
    now = business_now()
    out_punch = clock_in.punch_out(
        db,
        user=user,
        client_lat=None,
        client_lng=None,
        user_agent="admin-clock-out",
        now_override=now,
    )
    out_punch.hours_confirmation_status = "needs_review"
    # Don't clobber an in-punch the owner already confirmed/adjusted.
    if in_punch.hours_confirmation_status in (None, "not_required"):
        in_punch.hours_confirmation_status = "needs_review"
    db.flush()

    _audit(
        db,
        punch=out_punch,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action="punch.admin_clock_out",
        reason_code="admin_clock_out",
        old_values={"in_punch_id": in_punch.id},
        new_values={
            "out_punch_id": out_punch.id,
            "punched_at": out_punch.punched_at.astimezone(
                timezone.utc
            ).isoformat(),
            "status": out_punch.status,
        },
        notes=(reason or "").strip() or None,
    )
    return out_punch


def admin_clock_out(
    db: Session,
    *,
    in_punch_id: int,
    actor_user_id: int,
    reason: str | None = None,
) -> dict:
    """Owner clocks one staffer out. `in_punch_id` is the open in-punch
    shown in the 'On the clock now' panel."""
    punch = db.get(StaffPunch, in_punch_id)
    if punch is None:
        raise AttendanceReviewError("punch_not_found", http_status=404)
    if punch.direction != "in":
        raise AttendanceReviewError("not_an_in_punch", http_status=409)
    if punch.status == "void":
        raise AttendanceReviewError("punch_void", http_status=409)
    open_in = _open_in_punch_for(db, punch.user_id)
    if open_in is None or open_in.id != punch.id:
        # Already clocked out, or a newer punch superseded this one.
        raise AttendanceReviewError("not_currently_open", http_status=409)

    out_punch = _force_clock_out(
        db, in_punch=punch, actor_user_id=actor_user_id, reason=reason
    )
    return _row_to_dict(out_punch, location_name=_location_name(db, out_punch))


def admin_clock_out_all(
    db: Session,
    *,
    actor_user_id: int,
    reason: str | None = None,
) -> dict:
    """Clock out every currently-open session in one pass. Each close
    re-checks open state so a session that closed between discovery and
    the write (a staffer tapping clock-out themselves) is skipped, not
    double-punched."""
    payload = list_open_sessions(db)
    closed: list[dict] = []
    for session in payload["open_sessions"]:
        in_punch = db.get(StaffPunch, session["in_punch_id"])
        if in_punch is None:
            continue
        open_in = _open_in_punch_for(db, in_punch.user_id)
        if open_in is None or open_in.id != in_punch.id:
            continue
        out_punch = _force_clock_out(
            db, in_punch=in_punch, actor_user_id=actor_user_id, reason=reason
        )
        closed.append(
            _row_to_dict(out_punch, location_name=_location_name(db, out_punch))
        )
    return {"closed_count": len(closed), "closed": closed}


# ---------------------------------------------------------------------------
# Correction requests
# ---------------------------------------------------------------------------


def _request_to_dict(
    req: StaffPunchCorrectionRequest,
    *,
    user: User | None = None,
    decided_by: User | None = None,
) -> dict:
    def _maybe_iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()

    def _maybe_local(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return to_business_local(dt).isoformat()

    return {
        "id": req.id,
        "user_id": req.user_id,
        "user_full_name": user.full_name if user is not None else None,
        "user_username": user.username if user is not None else None,
        "punch_id": req.punch_id,
        "requested_check_in_at": _maybe_iso(req.requested_check_in_at),
        "requested_check_in_at_local": _maybe_local(req.requested_check_in_at),
        "requested_check_out_at": _maybe_iso(req.requested_check_out_at),
        "requested_check_out_at_local": _maybe_local(
            req.requested_check_out_at
        ),
        "reason": req.reason,
        "status": req.status,
        "decided_by_user_id": req.decided_by_user_id,
        "decided_by_full_name": (
            decided_by.full_name if decided_by is not None else None
        ),
        "decided_at": _maybe_iso(req.decided_at),
        "decision_notes": req.decision_notes,
        "created_at": _maybe_iso(req.created_at),
    }


def submit_correction_request(
    db: Session,
    *,
    user: User,
    punch_id: int | None,
    requested_check_in_at: datetime | None,
    requested_check_out_at: datetime | None,
    reason: str,
) -> dict:
    """Stylist files a correction. At least one of the proposed
    timestamps must be set; both is fine for a "I forgot to clock in
    AND out yesterday" request. Reason is required so an owner has
    enough context to decide without chasing the stylist."""
    if requested_check_in_at is None and requested_check_out_at is None:
        raise AttendanceReviewError(
            "no_proposed_times", http_status=422
        )
    if requested_check_in_at is not None and requested_check_in_at.tzinfo is None:
        raise AttendanceReviewError("naive_datetime", http_status=422)
    if (
        requested_check_out_at is not None
        and requested_check_out_at.tzinfo is None
    ):
        raise AttendanceReviewError("naive_datetime", http_status=422)
    if not reason or not reason.strip():
        raise AttendanceReviewError("reason_required", http_status=422)

    if punch_id is not None:
        punch = db.get(StaffPunch, punch_id)
        if punch is None:
            raise AttendanceReviewError(
                "punch_not_found", http_status=404
            )
        if punch.user_id != user.id:
            # Stylist can only file requests against their own punch.
            # Owner manual adjustments use a different path.
            raise AttendanceReviewError(
                "punch_not_yours", http_status=403
            )

    req = StaffPunchCorrectionRequest(
        user_id=user.id,
        punch_id=punch_id,
        requested_check_in_at=(
            requested_check_in_at.astimezone(timezone.utc)
            if requested_check_in_at is not None
            else None
        ),
        requested_check_out_at=(
            requested_check_out_at.astimezone(timezone.utc)
            if requested_check_out_at is not None
            else None
        ),
        reason=reason.strip(),
        status="pending",
    )
    db.add(req)
    db.flush()

    return _request_to_dict(req, user=user)


def list_correction_requests(
    db: Session,
    *,
    statuses: Iterable[str] | None = None,
    user_id: int | None = None,
) -> list[dict]:
    """Return correction requests, newest first.

    Owner queue defaults to `statuses=('pending',)`. Stylist's own
    request list defaults to all statuses. Both go through this single
    function so the row shape stays consistent.
    """
    stmt = select(StaffPunchCorrectionRequest).order_by(
        StaffPunchCorrectionRequest.created_at.desc(),
        StaffPunchCorrectionRequest.id.desc(),
    )
    if statuses:
        stmt = stmt.where(
            StaffPunchCorrectionRequest.status.in_(list(statuses))
        )
    if user_id is not None:
        stmt = stmt.where(StaffPunchCorrectionRequest.user_id == user_id)

    rows = db.execute(stmt).scalars().all()

    user_ids = {r.user_id for r in rows} | {
        r.decided_by_user_id for r in rows if r.decided_by_user_id is not None
    }
    user_map: dict[int, User] = {}
    if user_ids:
        urows = (
            db.execute(select(User).where(User.id.in_(list(user_ids))))
            .scalars()
            .all()
        )
        user_map = {u.id: u for u in urows}
    return [
        _request_to_dict(
            r,
            user=user_map.get(r.user_id),
            decided_by=user_map.get(r.decided_by_user_id)
            if r.decided_by_user_id is not None
            else None,
        )
        for r in rows
    ]


def decide_correction_request(
    db: Session,
    *,
    request_id: int,
    status_decision: Literal["approved", "denied"],
    decision_notes: str | None,
    actor_user_id: int,
) -> dict:
    """Owner decides a pending correction request.

    Approval applies the proposed time to the linked punch (if any)
    via the same audit-row pattern `manual_adjust` uses, so the punch
    timeline reads consistently regardless of which surface drove the
    edit. If the request did not specify a target punch, approval is
    record-only — owner is expected to also `manual_adjust` whatever
    punch the request was about. Denial just stamps the decision.
    """
    if status_decision not in ("approved", "denied"):
        raise AttendanceReviewError("invalid_decision", http_status=422)

    req = db.get(StaffPunchCorrectionRequest, request_id)
    if req is None:
        raise AttendanceReviewError(
            "correction_request_not_found", http_status=404
        )
    if req.status != "pending":
        raise AttendanceReviewError(
            "correction_request_not_pending", http_status=409
        )

    req.status = status_decision
    req.decided_by_user_id = actor_user_id
    req.decided_at = business_now().astimezone(timezone.utc)
    if decision_notes is not None:
        req.decision_notes = decision_notes.strip() or None

    if status_decision == "approved" and req.punch_id is not None:
        punch = db.get(StaffPunch, req.punch_id)
        if punch is not None and punch.status != "void":
            old_at = punch.punched_at
            old_status = punch.status

            # The proposed time depends on the punch's direction —
            # an `in` punch matches `requested_check_in_at`, an `out`
            # punch matches `requested_check_out_at`.
            target = (
                req.requested_check_in_at
                if punch.direction == "in"
                else req.requested_check_out_at
            )
            if target is not None:
                punch.punched_at = target.astimezone(timezone.utc)
                punch.status = "manual_adjusted"
                punch.hours_confirmation_status = "adjusted"
                db.flush()
                _audit(
                    db,
                    punch=punch,
                    actor_user_id=actor_user_id,
                    actor_kind="owner",
                    action="punch.correction_applied",
                    reason_code="correction_approved",
                    old_values={
                        "punched_at": old_at.astimezone(timezone.utc).isoformat(),
                        "status": old_status,
                    },
                    new_values={
                        "punched_at": punch.punched_at.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "status": "manual_adjusted",
                    },
                    notes=(
                        f"Correction request #{req.id} approved: "
                        + (decision_notes or req.reason)
                    ),
                )

    db.flush()

    user = db.get(User, req.user_id)
    decided_by = (
        db.get(User, req.decided_by_user_id)
        if req.decided_by_user_id is not None
        else None
    )
    return _request_to_dict(req, user=user, decided_by=decided_by)


def cancel_correction_request(
    db: Session,
    *,
    request_id: int,
    user: User,
) -> dict:
    """Stylist cancels their own pending correction. Idempotent on a
    cancelled row; refuses on already-decided rows."""
    req = db.get(StaffPunchCorrectionRequest, request_id)
    if req is None:
        raise AttendanceReviewError(
            "correction_request_not_found", http_status=404
        )
    if req.user_id != user.id:
        raise AttendanceReviewError(
            "correction_request_not_yours", http_status=403
        )
    if req.status == "cancelled":
        return _request_to_dict(req, user=user)
    if req.status != "pending":
        raise AttendanceReviewError(
            "correction_request_not_pending", http_status=409
        )
    req.status = "cancelled"
    db.flush()
    return _request_to_dict(req, user=user)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _location_name(db: Session, punch: StaffPunch) -> str | None:
    if punch.location_id is None:
        return None
    loc = db.get(StaffLocation, punch.location_id)
    return loc.name if loc is not None else None
