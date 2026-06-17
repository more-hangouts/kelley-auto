"""Admin per-day schedule service (Phase 10 Slice 1).

CRUD + week read + publish for `staff_schedule_entries`, the table
backing the manager's weekly grid. Published rows take precedence
over `staff_shift_overrides` and `staff_shifts` in
`services/shift_resolver`.

Slice-1 surface (Slice 2 will add the clock-in writeback + no-show
cron + 'excuse' transitions tied to those):

  - `create_entry`, `update_entry`, `delete_entry`
  - `list_week` — one-shot grid payload (staff + entries + approved
    time-off blocks for the requested week)
  - `publish_week` — flips matching draft rows to published, validates
    each one against approved time-off, stamps `published_at` and
    `published_by_user_id`
  - `set_manager_notes`
  - `mark_excused` — flips a `no_show` row to `excused`

Validation rules locked in this slice:

  1. `ends_at_local > starts_at_local` (schema CHECK + service guard
     for clearer errors).
  2. `business_date` must match `starts_at_local.date()` in the
     boutique tz — drift here would corrupt every grid query.
  3. Publishing an entry whose `[starts_at_local, ends_at_local]`
     interval overlaps an approved time-off request is rejected
     with `time_off_conflict`. (The grid UI prevents this at compose
     time too; the service is the backstop.)
  4. Duplicate `(user_id, starts_at_local, ends_at_local)` rejected —
     covers a double-click on the grid creating two identical entries.
     Split shifts and same-day coverage handoffs are fine because they
     differ in their start or end.
  5. Late-grace defaults: copied from the source shift's
     `late_grace_period_minutes` when `source='template_clone'`,
     defaulting to 30 for manual entries.

`mark_excused` is the only path that mutates `attendance_status` in
Slice 1. Other transitions (`scheduled→present`, `scheduled→late`,
`scheduled→no_show`) are owned by Slice 2's clock-in hook and cron.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    BusinessProfile,
    Invoice,
    OpenShiftPost,
    StaffPunch,
    StaffPunchAuditEvent,
    StaffScheduleEntry,
    StaffShift,
    StaffShiftRequest,
    TimeOffRequest,
    User,
)
from services import recurring_availability
from services.business_time import business_now, shop_tz
from services.email_transport import send_rendered_safely


class StaffScheduleError(Exception):
    """Stable error codes the router maps to HTTP statuses."""

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


DEFAULT_LATE_GRACE_MINUTES = 30


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _entry_to_shift_dict(e: StaffScheduleEntry) -> dict:
    """Adapt a schedule entry to the shift-dict shape that
    ``services/notification_templates`` renderers expect — datetime
    objects for starts/ends plus title/location/notes strings.
    Kept separate from ``_entry_to_dict`` so the API serialization
    (ISO strings) and the renderer interface (datetimes) can each
    evolve without dragging the other along.
    """
    return {
        "starts_at": e.starts_at_local,
        "ends_at": e.ends_at_local,
        "title": "Boutique shift",
        "location": "Bella's XV boutique",
        "notes": (e.manager_notes or "").strip() or None,
    }


def _send_schedule_published_emails(
    db: Session, *, week_start: date, published_ids: list[int]
) -> None:
    """Fan out one ``staff.schedule_published`` email per affected user.

    Groups freshly-published entries by ``user_id`` so each stylist
    receives one summary covering all of their shifts in the week,
    matching the renderer's "your week" framing.
    """
    if not published_ids:
        return
    from services import notification_templates  # local to avoid cycles

    entries = (
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.id.in_(published_ids))
        .all()
    )
    by_user: dict[int, list[StaffScheduleEntry]] = {}
    for entry in entries:
        by_user.setdefault(entry.user_id, []).append(entry)
    for user_id, user_entries in by_user.items():
        user = db.get(User, user_id)
        if user is None or not user.email or not user.is_active:
            continue
        user_entries.sort(key=lambda e: e.starts_at_local)
        rendered = notification_templates.render_schedule_published(
            staff_user=user,
            week_start=week_start,
            shifts=[_entry_to_shift_dict(e) for e in user_entries],
        )
        send_rendered_safely(
            to=user.email, rendered=rendered, scope="staff_schedule.publish_week"
        )


def _admin_recipient_emails(db: Session) -> list[str]:
    """Resolve admin notification recipients. Preference is the configured
    ``business_profile.email``; fallback is every active admin user.
    Mirrors the equivalent helper in ``services/time_off.py`` (duplicated
    on purpose to avoid an import cycle and to let the two recipient-
    policy decisions diverge later if needed)."""
    profile = db.query(BusinessProfile).first()
    if profile is not None and profile.email:
        return [profile.email]
    rows = (
        db.query(User)
        .filter(User.role == "admin")
        .filter(User.is_active.is_(True))
        .all()
    )
    return [u.email for u in rows if u.email]


def _send_missing_clock_out_emails(
    db: Session, *, flipped_entry_ids: list[int]
) -> None:
    """For each entry the cron flipped to ``missing_out_punch``, fire one
    email to the staffer (so they know to follow up) and one to each admin
    recipient (so attendance review knows to act). Called from
    ``services/missing_out_punch_cron`` after the commit."""
    if not flipped_entry_ids:
        return
    from services import notification_templates  # local to avoid cycles

    admin_emails = _admin_recipient_emails(db)
    entries = (
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.id.in_(flipped_entry_ids))
        .all()
    )
    for entry in entries:
        if entry.actual_clock_in_punch_id is None:
            # Cron should never flip rows without an in-punch, but stay
            # defensive — without a clocked_in_at timestamp the email has
            # no useful content to render.
            continue
        in_punch = db.get(StaffPunch, entry.actual_clock_in_punch_id)
        if in_punch is None:
            continue
        user = db.get(User, entry.user_id)
        if user is None:
            continue
        shift = _entry_to_shift_dict(entry)

        if user.email and user.is_active:
            rendered = notification_templates.render_staff_missing_clock_out(
                staff_user=user,
                shift=shift,
                clocked_in_at=in_punch.punched_at,
            )
            send_rendered_safely(
                to=user.email,
                rendered=rendered,
                scope="missing_out_punch.staff",
            )

        admin_rendered = notification_templates.render_admin_missing_clock_out(
            staff_user=user,
            shift=shift,
            clocked_in_at=in_punch.punched_at,
        )
        for admin_email in admin_emails:
            send_rendered_safely(
                to=admin_email,
                rendered=admin_rendered,
                scope="missing_out_punch.admin",
            )


def _send_shift_added_event(
    db: Session, *, entry: StaffScheduleEntry, actor_user_id: int | None
) -> None:
    """Single-entry publish → ``staff.shift_added`` via the event bus.

    Used by ``publish_entry`` (per-cell publish) and
    ``create_entry(publish=True)`` (the grid's "create AND publish
    immediately" action). The bulk ``publish_week`` path emits
    ``staff.schedule_published`` (#17) instead so a manager publishing
    20 shifts doesn't trigger 20 separate emails.

    Routed through ``services.staff_schedule_notifications`` →
    ``record_event`` rather than ``send_rendered_safely`` so the
    dispatcher owns recipient resolution + transactional retry +
    digest plumbing. Recipient resolution requires the entry row to
    exist at record time; this is called AFTER ``db.flush()`` so the
    insert is visible to the intrinsic-targeting lookup.
    """
    from services import staff_schedule_notifications

    staff_schedule_notifications.notify_shift_added(
        db,
        entry=entry,
        shift=_entry_to_shift_dict(entry),
        actor_user_id=actor_user_id,
    )


def _entry_to_dict(e: StaffScheduleEntry) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "business_date": e.business_date.isoformat(),
        "starts_at_local": e.starts_at_local.astimezone(shop_tz()).isoformat(),
        "ends_at_local": e.ends_at_local.astimezone(shop_tz()).isoformat(),
        "status": e.status,
        "attendance_status": e.attendance_status,
        "late_grace_minutes": int(e.late_grace_minutes),
        "source": e.source,
        "source_shift_id": e.source_shift_id,
        "manager_notes": e.manager_notes,
        "actual_clock_in_punch_id": e.actual_clock_in_punch_id,
        "actual_clock_out_punch_id": e.actual_clock_out_punch_id,
        "published_at": (
            e.published_at.astimezone(timezone.utc).isoformat()
            if e.published_at
            else None
        ),
        "published_by_user_id": e.published_by_user_id,
        "created_by_user_id": e.created_by_user_id,
        "created_at": e.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": e.updated_at.astimezone(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Labor cost (Phase 10 Slice 6 — Epic 6.1)
# ---------------------------------------------------------------------------


_CENTS_PER_HOUR = Decimal(100)
_SECONDS_PER_HOUR = Decimal(3600)


def compute_labor_cost(
    entries: Iterable[StaffScheduleEntry],
    wage_by_user_id: dict[int, Decimal | None],
) -> dict:
    """Cost-out a set of schedule entries against each stylist's
    `users.hourly_wage`.

    Both draft and published entries are summed so the manager sees
    the projected cost of an unpublished week, matching the overtime
    chip's "include drafts" convention. The breakdown lets the UI
    annotate "$X (of which $Y still draft)" if it wants.

    A user with a NULL `hourly_wage` contributes 0 cents and is named
    in `unknown_wage_user_ids` so the chip can flag missing data
    instead of silently understating cost.
    """
    total_cents = 0
    draft_cents = 0
    published_cents = 0
    unknown_user_ids: set[int] = set()

    for entry in entries:
        wage = wage_by_user_id.get(entry.user_id)
        if wage is None:
            unknown_user_ids.add(entry.user_id)
            continue
        seconds = (
            entry.ends_at_local - entry.starts_at_local
        ).total_seconds()
        if seconds <= 0:
            continue
        hours = Decimal(str(seconds)) / _SECONDS_PER_HOUR
        cost_cents = int(
            (Decimal(wage) * hours * _CENTS_PER_HOUR).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        total_cents += cost_cents
        if entry.status == "published":
            published_cents += cost_cents
        else:
            draft_cents += cost_cents

    return {
        "total_cents": total_cents,
        "draft_cents": draft_cents,
        "published_cents": published_cents,
        "unknown_wage_user_ids": sorted(unknown_user_ids),
    }


def compute_labor_target(
    db: Session,
    *,
    week_start: date,
    labor_cost_cents: int,
) -> dict:
    """Compute the labor-cost-vs-revenue target for the visible week
    (Phase 10 Slice 6 — Epic 6.2).

    Pulls `business_profile.target_labor_pct` (Numeric percent points,
    e.g. 20.00 = 20%). When set:
      * `target_sales_cents = round(labor_cost_cents * 100 / target_pct)`
        — revenue the boutique needs to hit to keep labor at the target
        share of sales.
      * `actual_sales_cents` mirrors Phase E's SPLH revenue convention
        for consistency: `SUM(invoices.paid_to_date_cents)` where the
        invoice's `issue_date` lands in `[week_start, week_start + 7)`,
        excluding draft/cancelled/reversed and rows that have collected
        nothing yet. Cash-basis, attribution-agnostic.
      * `gap_cents = target_sales_cents - actual_sales_cents` — positive
        means you're short, negative means you're past goal.

    Returns the same block when the profile setting is NULL but with
    `target_pct=None`, `target_sales_cents=None`, `gap_cents=None` so
    the UI can show actual revenue alone without a goal compare.
    """
    profile = db.get(BusinessProfile, 1)
    target_pct = (
        Decimal(str(profile.target_labor_pct))
        if profile is not None and profile.target_labor_pct is not None
        else None
    )

    week_end_exclusive = week_start + timedelta(days=7)
    actual_sales_cents = int(
        db.execute(
            select(
                func.coalesce(func.sum(Invoice.paid_to_date_cents), 0)
            )
            .where(Invoice.deleted_at.is_(None))
            .where(Invoice.issue_date >= week_start)
            .where(Invoice.issue_date < week_end_exclusive)
            .where(
                Invoice.status.notin_(("draft", "cancelled", "reversed"))
            )
            .where(Invoice.paid_to_date_cents > 0)
        ).scalar()
        or 0
    )

    target_sales_cents: int | None = None
    gap_cents: int | None = None
    if target_pct is not None and target_pct > 0:
        target_sales_cents = int(
            (
                Decimal(labor_cost_cents)
                * Decimal(100)
                / target_pct
            ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
        gap_cents = target_sales_cents - actual_sales_cents

    return {
        "target_pct": (
            str(target_pct.quantize(Decimal("0.01")))
            if target_pct is not None
            else None
        ),
        "target_sales_cents": target_sales_cents,
        "actual_sales_cents": actual_sales_cents,
        "gap_cents": gap_cents,
    }


def _intervals_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    return start_a < end_b and end_a > start_b


def _parse_iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def compute_appointment_density_warnings(
    db: Session,
    *,
    week_start: date,
    entries: Iterable[StaffScheduleEntry],
    time_off_blocks: Iterable[dict],
    recurring_unavailable_blocks: Iterable[dict],
    min_appointments: int = 3,
    required_stylists: int = 2,
) -> list[dict]:
    """Warn when appointment demand outruns scheduled available staff.

    Buckets are one boutique-local hour. An hour with at least
    `min_appointments` active appointment starts needs
    `required_stylists` scheduled stylists who are not blocked by
    approved time off or recurring unavailability during that hour.
    """
    tz = shop_tz()
    week_start_utc, week_end_utc = _week_bounds_utc(week_start)
    active_statuses = ("pending", "confirmed", "attended")

    appts = list(
        db.execute(
            select(Appointment)
            .where(Appointment.slot_start_at >= week_start_utc)
            .where(Appointment.slot_start_at < week_end_utc)
            .where(Appointment.status.in_(active_statuses))
            .order_by(Appointment.slot_start_at, Appointment.id)
        )
        .scalars()
        .all()
    )
    if not appts:
        return []

    buckets: dict[datetime, list[Appointment]] = {}
    for appt in appts:
        local_start = appt.slot_start_at.astimezone(tz)
        bucket_start = local_start.replace(
            minute=0, second=0, microsecond=0
        )
        buckets.setdefault(bucket_start, []).append(appt)

    time_off_by_user: dict[int, list[tuple[datetime, datetime]]] = {}
    for block in time_off_blocks:
        time_off_by_user.setdefault(int(block["user_id"]), []).append(
            (
                _parse_iso_dt(block["starts_at_local"]),
                _parse_iso_dt(block["ends_at_local"]),
            )
        )
    recurring_by_user: dict[int, list[tuple[datetime, datetime]]] = {}
    for block in recurring_unavailable_blocks:
        recurring_by_user.setdefault(int(block["user_id"]), []).append(
            (
                _parse_iso_dt(block["starts_at_local"]),
                _parse_iso_dt(block["ends_at_local"]),
            )
        )

    warnings: list[dict] = []
    for bucket_start, bucket_appts in sorted(buckets.items()):
        if len(bucket_appts) < min_appointments:
            continue
        bucket_end = bucket_start + timedelta(hours=1)
        available_user_ids: set[int] = set()
        for entry in entries:
            if not _intervals_overlap(
                entry.starts_at_local,
                entry.ends_at_local,
                bucket_start,
                bucket_end,
            ):
                continue
            user_id = int(entry.user_id)
            blocked = any(
                _intervals_overlap(start, end, bucket_start, bucket_end)
                for start, end in time_off_by_user.get(user_id, [])
            ) or any(
                _intervals_overlap(start, end, bucket_start, bucket_end)
                for start, end in recurring_by_user.get(user_id, [])
            )
            if not blocked:
                available_user_ids.add(user_id)

        scheduled_count = len(available_user_ids)
        if scheduled_count >= required_stylists:
            continue
        warnings.append(
            {
                "business_date": bucket_start.date().isoformat(),
                "bucket_start_local": bucket_start.isoformat(),
                "bucket_end_local": bucket_end.isoformat(),
                "appointment_count": len(bucket_appts),
                "appointment_ids": [int(a.id) for a in bucket_appts],
                "scheduled_stylist_count": scheduled_count,
                "required_stylist_count": required_stylists,
                "shortage": required_stylists - scheduled_count,
            }
        )
    return warnings


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _ensure_aware(dt: datetime, *, field: str) -> datetime:
    if dt.tzinfo is None:
        raise StaffScheduleError(
            "naive_datetime", http_status=422, extra={"field": field}
        )
    return dt


def _validate_range(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise StaffScheduleError("invalid_date_range", http_status=422)


def _validate_business_date(
    business_date_: date, starts_at_local: datetime
) -> None:
    """The business_date column must match the local calendar date of
    `starts_at_local`. Drift here would break every grid query (which
    filters on `business_date`)."""
    expected = starts_at_local.astimezone(shop_tz()).date()
    if business_date_ != expected:
        raise StaffScheduleError(
            "business_date_mismatch",
            http_status=422,
            extra={"expected": expected.isoformat()},
        )


def _validate_late_grace(value: int) -> None:
    if not (0 <= value <= 120):
        raise StaffScheduleError(
            "late_grace_out_of_range", http_status=422
        )


def _check_duplicate(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
    exclude_id: int | None = None,
) -> None:
    stmt = select(StaffScheduleEntry.id).where(
        and_(
            StaffScheduleEntry.user_id == user_id,
            StaffScheduleEntry.starts_at_local == starts_at_local,
            StaffScheduleEntry.ends_at_local == ends_at_local,
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(StaffScheduleEntry.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise StaffScheduleError("duplicate_entry", http_status=409)


def _check_time_off_conflict(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
) -> None:
    """Reject when the entry's interval intersects an approved time-off
    request for the same stylist. Pending/denied/cancelled don't block.
    """
    starts_utc = starts_at_local.astimezone(timezone.utc)
    ends_utc = ends_at_local.astimezone(timezone.utc)
    conflict = (
        db.execute(
            select(TimeOffRequest.id)
            .where(TimeOffRequest.user_id == user_id)
            .where(TimeOffRequest.status == "approved")
            .where(TimeOffRequest.starts_at < ends_utc)
            .where(TimeOffRequest.ends_at > starts_utc)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if conflict is not None:
        raise StaffScheduleError(
            "time_off_conflict",
            http_status=409,
            extra={"time_off_request_id": conflict},
        )


def _check_recurring_unavailable_conflict(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
) -> None:
    """Reject when the entry's interval overlaps an active recurring
    unavailability rule (Phase 10 Slice 6 — Epic 3.4).

    Unlike time-off there's no pending→approved race to lock against —
    the stylist's rule is set unilaterally, so a simple synchronous
    read is enough. Surfaces the conflicting block id in `extra` so
    the UI can name the rule.
    """
    block_id = recurring_availability.find_conflict(
        db,
        user_id=user_id,
        starts_at_local=starts_at_local,
        ends_at_local=ends_at_local,
    )
    if block_id is not None:
        raise StaffScheduleError(
            "recurring_unavailable_conflict",
            http_status=409,
            extra={"recurring_unavailable_block_id": block_id},
        )


def entry_has_started(entry: StaffScheduleEntry) -> bool:
    """A published shift is off-limits to request mutation once it has
    begun or carries any attendance. Conservative (the locked v1 rule in
    the scheduling plan): any non-'scheduled' attendance, either punch fk,
    or a start time at/before now counts as started."""
    if entry.actual_clock_in_punch_id is not None:
        return True
    if entry.actual_clock_out_punch_id is not None:
        return True
    if entry.attendance_status != "scheduled":
        return True
    return entry.starts_at_local.astimezone(timezone.utc) <= datetime.now(
        timezone.utc
    )


def validate_staff_can_work_interval(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
    exclude_entry_ids: set[int] | None = None,
) -> list[dict]:
    """Return the structured conflicts that would stop ``user_id`` from
    working ``[starts_at_local, ends_at_local)``. Empty list means clear.

    Conflict ``type`` values: ``inactive_user``, ``published_overlap``,
    ``approved_time_off``, ``recurring_unavailability``. Request approval
    turns any non-empty result into a 409; admin manual scheduling can
    show these as warnings and still proceed (see the plan's service
    design notes)."""
    conflicts: list[dict] = []
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        conflicts.append({"type": "inactive_user", "user_id": user_id})
        # No point checking intervals for a user who can't be scheduled.
        return conflicts

    tz = shop_tz()
    exclude = set(exclude_entry_ids or ())
    overlap_stmt = (
        select(StaffScheduleEntry)
        .where(StaffScheduleEntry.user_id == user_id)
        .where(StaffScheduleEntry.status == "published")
        .where(StaffScheduleEntry.starts_at_local < ends_at_local)
        .where(StaffScheduleEntry.ends_at_local > starts_at_local)
    )
    for e in db.execute(overlap_stmt).scalars().all():
        if e.id in exclude:
            continue
        conflicts.append(
            {
                "type": "published_overlap",
                "entry_id": e.id,
                "business_date": e.business_date.isoformat(),
                "starts_at_local": e.starts_at_local.astimezone(tz).isoformat(),
                "ends_at_local": e.ends_at_local.astimezone(tz).isoformat(),
            }
        )

    starts_utc = starts_at_local.astimezone(timezone.utc)
    ends_utc = ends_at_local.astimezone(timezone.utc)
    tor_id = (
        db.execute(
            select(TimeOffRequest.id)
            .where(TimeOffRequest.user_id == user_id)
            .where(TimeOffRequest.status == "approved")
            .where(TimeOffRequest.starts_at < ends_utc)
            .where(TimeOffRequest.ends_at > starts_utc)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if tor_id is not None:
        conflicts.append(
            {"type": "approved_time_off", "time_off_request_id": tor_id}
        )

    block_id = recurring_availability.find_conflict(
        db,
        user_id=user_id,
        starts_at_local=starts_at_local,
        ends_at_local=ends_at_local,
    )
    if block_id is not None:
        conflicts.append(
            {
                "type": "recurring_unavailability",
                "recurring_unavailable_block_id": block_id,
            }
        )
    return conflicts


def transfer_published_entry(
    db: Session,
    *,
    entry_id: int,
    from_user_id: int,
    to_user_id: int,
    actor_user_id: int,
    reason: str,
    request_id: int | None = None,
) -> StaffScheduleEntry:
    """Move one published schedule entry from ``from_user_id`` to
    ``to_user_id`` under a row lock, after re-running the destination
    user's conflict checks. The entry id is preserved so attendance and
    notifications stay tied to the concrete shift.

    Raises ``StaffScheduleError`` (mapped to the request flow's codes by
    the caller) on: missing/non-published entry, owner mismatch, a
    started shift, or any destination conflict (``candidate_conflict``,
    409, with the structured conflicts in ``extra``). The caller owns the
    request-event + notification writes — they need request-specific copy
    and recipients the generic schedule events can't express."""
    entry = (
        db.execute(
            select(StaffScheduleEntry)
            .where(StaffScheduleEntry.id == entry_id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status != "published":
        raise StaffScheduleError("entry_not_published", http_status=409)
    if entry.user_id != from_user_id:
        raise StaffScheduleError("entry_owner_mismatch", http_status=409)
    if entry_has_started(entry):
        raise StaffScheduleError("entry_started", http_status=409)

    conflicts = validate_staff_can_work_interval(
        db,
        user_id=to_user_id,
        starts_at_local=entry.starts_at_local,
        ends_at_local=entry.ends_at_local,
        exclude_entry_ids={entry.id},
    )
    if conflicts:
        raise StaffScheduleError(
            "candidate_conflict",
            http_status=409,
            extra={"conflicts": conflicts},
        )

    entry.user_id = to_user_id
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return entry


def swap_published_entries(
    db: Session,
    *,
    entry_a_id: int,
    entry_b_id: int,
    user_a_id: int,
    user_b_id: int,
    actor_user_id: int,
    request_id: int | None = None,
) -> tuple[StaffScheduleEntry, StaffScheduleEntry]:
    """Trade ownership of two published entries (Scheduling Phase 4).

    `entry_a` (owned by `user_a`) and `entry_b` (owned by `user_b`) swap
    owners. Both rows are locked (ordered by id to avoid deadlock), both
    must still be published, owned as expected, and unstarted, and each
    user must be able to work the OTHER's interval (the entries being
    traded are excluded from the overlap check). Entry ids are preserved.

    Raises `StaffScheduleError` (remapped by the caller): missing/
    non-published entry, owner mismatch, started shift, or
    `candidate_conflict` (409) with the structured conflicts tagged by
    `for_user_id`."""
    first_id, second_id = sorted((entry_a_id, entry_b_id))
    locked = {
        e.id: e
        for e in db.execute(
            select(StaffScheduleEntry)
            .where(StaffScheduleEntry.id.in_([first_id, second_id]))
            .with_for_update()
        )
        .scalars()
        .all()
    }
    entry_a = locked.get(entry_a_id)
    entry_b = locked.get(entry_b_id)
    if entry_a is None or entry_b is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    for entry in (entry_a, entry_b):
        if entry.status != "published":
            raise StaffScheduleError("entry_not_published", http_status=409)
        if entry_has_started(entry):
            raise StaffScheduleError("entry_started", http_status=409)
    if entry_a.user_id != user_a_id or entry_b.user_id != user_b_id:
        raise StaffScheduleError("entry_owner_mismatch", http_status=409)

    exclude = {entry_a_id, entry_b_id}
    conflicts: list[dict] = []
    for uid, dest in ((user_a_id, entry_b), (user_b_id, entry_a)):
        for c in validate_staff_can_work_interval(
            db,
            user_id=uid,
            starts_at_local=dest.starts_at_local,
            ends_at_local=dest.ends_at_local,
            exclude_entry_ids=exclude,
        ):
            conflicts.append({**c, "for_user_id": uid})
    if conflicts:
        raise StaffScheduleError(
            "candidate_conflict",
            http_status=409,
            extra={"conflicts": conflicts},
        )

    now = datetime.now(timezone.utc)
    entry_a.user_id = user_b_id
    entry_b.user_id = user_a_id
    entry_a.updated_at = now
    entry_b.updated_at = now
    db.flush()
    return entry_a, entry_b


def retract_published_entry_to_draft(
    db: Session,
    *,
    entry_id: int,
    expected_user_id: int,
    actor_user_id: int,
) -> StaffScheduleEntry:
    """Drop coverage: pull a published entry back to draft under a row
    lock (the conservative v1 drop-approval behavior; converting to an
    open post is deferred to Phase 3). Clears the publish stamp so the
    chk_sse_publish_stamp invariant holds. Refuses a started shift."""
    entry = (
        db.execute(
            select(StaffScheduleEntry)
            .where(StaffScheduleEntry.id == entry_id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status != "published":
        raise StaffScheduleError("entry_not_published", http_status=409)
    if entry.user_id != expected_user_id:
        raise StaffScheduleError("entry_owner_mismatch", http_status=409)
    if entry_has_started(entry):
        raise StaffScheduleError("entry_started", http_status=409)

    entry.status = "draft"
    entry.published_at = None
    entry.published_by_user_id = None
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return entry


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_entry(
    db: Session,
    *,
    actor_user_id: int,
    user_id: int,
    business_date_: date,
    starts_at_local: datetime,
    ends_at_local: datetime,
    source: str = "manual",
    source_shift_id: int | None = None,
    late_grace_minutes: int | None = None,
    manager_notes: str | None = None,
    publish: bool = False,
) -> dict:
    """Create a draft (default) or published entry.

    `publish=True` is the path used by the grid's per-cell "Publish
    immediately" action; the bulk path is `publish_week`. Either way,
    publishing runs the time-off-conflict check.
    """
    starts_at_local = _ensure_aware(starts_at_local, field="starts_at_local")
    ends_at_local = _ensure_aware(ends_at_local, field="ends_at_local")
    _validate_range(starts_at_local, ends_at_local)
    _validate_business_date(business_date_, starts_at_local)

    if source not in ("manual", "template_clone", "override_clone"):
        raise StaffScheduleError("invalid_source", http_status=422)

    if db.get(User, user_id) is None:
        raise StaffScheduleError("user_not_found", http_status=404)

    resolved_grace = late_grace_minutes
    if source_shift_id is not None:
        shift = db.get(StaffShift, source_shift_id)
        if shift is None:
            raise StaffScheduleError("shift_not_found", http_status=404)
        if resolved_grace is None:
            resolved_grace = int(shift.late_grace_period_minutes)
    if resolved_grace is None:
        resolved_grace = DEFAULT_LATE_GRACE_MINUTES
    _validate_late_grace(resolved_grace)

    _check_duplicate(
        db,
        user_id=user_id,
        starts_at_local=starts_at_local,
        ends_at_local=ends_at_local,
    )

    if publish:
        _check_time_off_conflict(
            db,
            user_id=user_id,
            starts_at_local=starts_at_local,
            ends_at_local=ends_at_local,
        )
        _check_recurring_unavailable_conflict(
            db,
            user_id=user_id,
            starts_at_local=starts_at_local,
            ends_at_local=ends_at_local,
        )

    now_utc = datetime.now(timezone.utc)
    entry = StaffScheduleEntry(
        user_id=user_id,
        business_date=business_date_,
        starts_at_local=starts_at_local,
        ends_at_local=ends_at_local,
        status="published" if publish else "draft",
        attendance_status="scheduled",
        late_grace_minutes=resolved_grace,
        source=source,
        source_shift_id=source_shift_id,
        manager_notes=(manager_notes or "").strip() or None,
        published_at=now_utc if publish else None,
        published_by_user_id=actor_user_id if publish else None,
        created_by_user_id=actor_user_id,
    )
    db.add(entry)
    db.flush()
    if publish:
        _send_shift_added_event(
            db, entry=entry, actor_user_id=actor_user_id
        )
    return _entry_to_dict(entry)


def update_entry(
    db: Session,
    *,
    entry_id: int,
    fields: dict,
) -> dict:
    """Partial update for draft entries.

    Published rows are immutable through this path — once a row is
    published, changes happen through targeted endpoints
    (`set_manager_notes`, `mark_excused`) or by unpublish-then-edit
    in a future slice. Locking down published rows here keeps the
    audit story tractable: a published row's start/end never silently
    shifts.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status == "published":
        raise StaffScheduleError(
            "entry_already_published", http_status=409
        )

    allowed = {
        "starts_at_local",
        "ends_at_local",
        "business_date",
        "late_grace_minutes",
        "manager_notes",
        "source_shift_id",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise StaffScheduleError(
            "unknown_field",
            http_status=422,
            extra={"fields": sorted(unknown)},
        )
    if not fields:
        raise StaffScheduleError("nothing_to_update", http_status=422)

    starts_at = fields.get("starts_at_local", entry.starts_at_local)
    ends_at = fields.get("ends_at_local", entry.ends_at_local)
    starts_at = _ensure_aware(starts_at, field="starts_at_local")
    ends_at = _ensure_aware(ends_at, field="ends_at_local")
    _validate_range(starts_at, ends_at)

    business_date_ = fields.get("business_date", entry.business_date)
    _validate_business_date(business_date_, starts_at)

    late_grace = int(
        fields.get("late_grace_minutes", entry.late_grace_minutes)
    )
    _validate_late_grace(late_grace)

    if (
        starts_at != entry.starts_at_local
        or ends_at != entry.ends_at_local
    ):
        _check_duplicate(
            db,
            user_id=entry.user_id,
            starts_at_local=starts_at,
            ends_at_local=ends_at,
            exclude_id=entry.id,
        )

    if "source_shift_id" in fields and fields["source_shift_id"] is not None:
        if db.get(StaffShift, fields["source_shift_id"]) is None:
            raise StaffScheduleError("shift_not_found", http_status=404)

    if "manager_notes" in fields:
        v = fields["manager_notes"]
        fields["manager_notes"] = (v or "").strip() or None

    for k, v in fields.items():
        setattr(entry, k, v)
    entry.late_grace_minutes = late_grace
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _entry_to_dict(entry)


def delete_entry(db: Session, *, entry_id: int) -> None:
    """Delete a draft entry. Published rows can't be deleted through
    this path — ``retract_published_entry`` is the explicit verb for
    moving a published row back to draft. Hard-deleting a published
    row is intentionally not supported (the row is the only audit
    trail of what the staffer was originally notified about)."""
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status == "published":
        raise StaffScheduleError(
            "entry_already_published", http_status=409
        )
    db.delete(entry)
    db.flush()


def update_published_entry(
    db: Session,
    *,
    actor_user_id: int,
    entry_id: int,
    fields: dict,
) -> dict:
    """Mutate a published entry's start/end/business_date/notes/grace.

    Counterpart to ``update_entry``; the draft path rejects published
    rows because draft edits stay silent (the staffer hasn't been
    notified of an unpublished shift). This path is for the after-
    publish "I need to nudge the time by 15 minutes" workflow, and
    fires ``staff.shift_edited`` with an old/new snapshot in payload.

    Errors:
      * ``entry_not_found`` (404) — id doesn't exist.
      * ``entry_not_published`` (409) — row is in draft; use
        ``update_entry`` instead.
      * ``time_off_conflict`` (409) — the new interval overlaps an
        approved time-off request.
      * ``recurring_unavailable_conflict`` (409) — overlaps an active
        recurring unavailability rule.
      * ``duplicate_entry`` (409) — the new interval collides with
        another row for the same user (via ``_check_duplicate``).
      * ``unknown_field`` / ``nothing_to_update`` (422) — same as
        ``update_entry``.

    The old/new snapshots used in the event payload are taken from
    ``_entry_to_shift_dict`` before and after the mutation. They're
    intentionally NOT computed from the diff alone — a renderer that
    wants to show the unchanged title/location alongside the changed
    window needs the full shift on both sides.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status != "published":
        raise StaffScheduleError(
            "entry_not_published",
            http_status=409,
            extra={"status": entry.status},
        )

    allowed = {
        "starts_at_local",
        "ends_at_local",
        "business_date",
        "late_grace_minutes",
        "manager_notes",
        "source_shift_id",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise StaffScheduleError(
            "unknown_field",
            http_status=422,
            extra={"fields": sorted(unknown)},
        )
    if not fields:
        raise StaffScheduleError("nothing_to_update", http_status=422)

    starts_at = fields.get("starts_at_local", entry.starts_at_local)
    ends_at = fields.get("ends_at_local", entry.ends_at_local)
    starts_at = _ensure_aware(starts_at, field="starts_at_local")
    ends_at = _ensure_aware(ends_at, field="ends_at_local")
    _validate_range(starts_at, ends_at)

    business_date_ = fields.get("business_date", entry.business_date)
    _validate_business_date(business_date_, starts_at)

    late_grace = int(
        fields.get("late_grace_minutes", entry.late_grace_minutes)
    )
    _validate_late_grace(late_grace)

    if (
        starts_at != entry.starts_at_local
        or ends_at != entry.ends_at_local
    ):
        _check_duplicate(
            db,
            user_id=entry.user_id,
            starts_at_local=starts_at,
            ends_at_local=ends_at,
            exclude_id=entry.id,
        )
        # Re-run the same publish-time conflict checks the row originally
        # passed, since the interval moved. Draft edits skip these because
        # publish_week re-validates at publish time; published edits have
        # no later checkpoint.
        _check_time_off_conflict(
            db,
            user_id=entry.user_id,
            starts_at_local=starts_at,
            ends_at_local=ends_at,
        )
        _check_recurring_unavailable_conflict(
            db,
            user_id=entry.user_id,
            starts_at_local=starts_at,
            ends_at_local=ends_at,
        )

    if "source_shift_id" in fields and fields["source_shift_id"] is not None:
        if db.get(StaffShift, fields["source_shift_id"]) is None:
            raise StaffScheduleError("shift_not_found", http_status=404)

    if "manager_notes" in fields:
        v = fields["manager_notes"]
        fields["manager_notes"] = (v or "").strip() or None

    old_shift = _entry_to_shift_dict(entry)

    for k, v in fields.items():
        setattr(entry, k, v)
    entry.late_grace_minutes = late_grace
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()

    new_shift = _entry_to_shift_dict(entry)
    if old_shift != new_shift:
        from services import staff_schedule_notifications

        staff_schedule_notifications.notify_shift_edited(
            db,
            entry=entry,
            old_shift=old_shift,
            new_shift=new_shift,
            actor_user_id=actor_user_id,
        )
    return _entry_to_dict(entry)


def retract_published_entry(
    db: Session,
    *,
    actor_user_id: int,
    entry_id: int,
) -> dict:
    """Move a published entry back to draft.

    The "delete a published shift" UX, modelled as retract-to-draft
    rather than a hard delete so the row survives as audit of what
    the staffer was originally notified about. Fires
    ``staff.shift_deleted`` with the published-shift snapshot in
    payload — the renderer reads from payload, not from the (now
    draft) row.

    Order of operations: snapshot → ``record_event`` → flip status.
    ``record_event`` resolves recipients via intrinsic targeting on
    the entry row, so the row must still exist (and still belong to
    the affected staffer) when we record. After the flip the row is
    a draft for the same user, which the dispatcher will continue to
    treat as the correct subject.

    Errors:
      * ``entry_not_found`` (404)
      * ``entry_not_published`` (409) — already a draft.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status != "published":
        raise StaffScheduleError(
            "entry_not_published",
            http_status=409,
            extra={"status": entry.status},
        )

    shift_snapshot = _entry_to_shift_dict(entry)

    from services import staff_schedule_notifications

    staff_schedule_notifications.notify_shift_deleted(
        db,
        entry=entry,
        shift=shift_snapshot,
        actor_user_id=actor_user_id,
    )

    entry.status = "draft"
    entry.published_at = None
    entry.published_by_user_id = None
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _entry_to_dict(entry)


# ---------------------------------------------------------------------------
# Week read (grid payload)
# ---------------------------------------------------------------------------


def _week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=i) for i in range(7)]


def _week_bounds_utc(week_start: date) -> tuple[datetime, datetime]:
    tz = shop_tz()
    start_local = datetime.combine(week_start, time.min, tzinfo=tz)
    end_local = datetime.combine(
        week_start + timedelta(days=7), time.min, tzinfo=tz
    )
    return start_local.astimezone(timezone.utc), end_local.astimezone(
        timezone.utc
    )


def _compute_overlap_warnings(
    entry_rows: list[StaffScheduleEntry],
) -> list[dict]:
    """Advisory per-stylist overlapping-interval detection for the grid.

    Manual scheduling intentionally allows split shifts (back-to-back
    intervals that share an edge) and handoffs, so overlaps are
    warning-first here — the grid surfaces them, it does not block.
    Two entries warn when they belong to the same stylist and their
    [start, end) intervals strictly overlap: exact duplicates overlap,
    edge-sharing split shifts (a.ends == b.starts) do not.

    Each warning carries both entry ids and the business_date so the
    frontend can flag the affected cell(s). Request-approval flows
    (later phases) turn the same overlap into a hard 409.
    """
    warnings: list[dict] = []
    by_user: dict[int, list[StaffScheduleEntry]] = {}
    for entry in entry_rows:
        by_user.setdefault(entry.user_id, []).append(entry)
    for user_id, rows in by_user.items():
        ordered = sorted(rows, key=lambda r: r.starts_at_local)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if b.starts_at_local >= a.ends_at_local:
                    # Sorted by start: no later entry can overlap `a`.
                    break
                warnings.append(
                    {
                        "user_id": user_id,
                        "entry_ids": [a.id, b.id],
                        "business_date": a.business_date.isoformat(),
                    }
                )
    return warnings


def _zero_exception_counts() -> dict:
    return {"pending_requests": 0, "open_shifts": 0, "conflicts": 0}


def _bump_exception_count(
    bucket: dict[str, dict[str, int]], key: str, field: str
) -> None:
    row = bucket.setdefault(key, _zero_exception_counts().copy())
    row[field] = int(row.get(field, 0)) + 1


def _schedule_exception_counts(
    db: Session,
    *,
    week_start: date,
    staff_id_set: set[int],
    overlap_warnings: list[dict],
) -> dict:
    """Compact exception counts for the admin grid.

    ``by_date`` drives day-header badges. ``by_cell`` drives per-stylist
    cell badges and uses the frontend's ``user_id|YYYY-MM-DD`` key.
    """
    week_end = week_start + timedelta(days=7)
    by_date: dict[str, dict[str, int]] = {}
    by_cell: dict[str, dict[str, int]] = {}

    def add_date(day: date | str, field: str) -> None:
        day_s = day if isinstance(day, str) else day.isoformat()
        _bump_exception_count(by_date, day_s, field)

    def add_cell(user_id: int | None, day: date | str, field: str) -> None:
        if user_id is None:
            return
        if staff_id_set and user_id not in staff_id_set:
            return
        day_s = day if isinstance(day, str) else day.isoformat()
        _bump_exception_count(by_cell, f"{user_id}|{day_s}", field)
        add_date(day_s, field)

    for warning in overlap_warnings:
        add_cell(
            int(warning["user_id"]),
            warning["business_date"],
            "conflicts",
        )

    requests = (
        db.execute(
            select(StaffShiftRequest).where(
                StaffShiftRequest.status.in_(
                    ("pending", "accepted_by_staff")
                )
            )
        )
        .scalars()
        .all()
    )
    for request in requests:
        touched: list[tuple[int | None, date]] = []
        if request.source_entry_id is not None:
            source = db.get(StaffScheduleEntry, request.source_entry_id)
            if source is not None:
                touched.append((source.user_id, source.business_date))
        if request.target_entry_id is not None:
            target = db.get(StaffScheduleEntry, request.target_entry_id)
            if target is not None:
                touched.append((target.user_id, target.business_date))
        if request.open_shift_post_id is not None:
            post = db.get(OpenShiftPost, request.open_shift_post_id)
            if post is not None:
                touched.append((None, post.business_date))

        seen: set[tuple[int | None, date]] = set()
        for user_id, business_date_ in touched:
            if not (week_start <= business_date_ < week_end):
                continue
            marker = (user_id, business_date_)
            if marker in seen:
                continue
            seen.add(marker)
            if user_id is None:
                add_date(business_date_, "pending_requests")
            else:
                add_cell(user_id, business_date_, "pending_requests")

    open_posts = (
        db.execute(
            select(OpenShiftPost)
            .where(OpenShiftPost.status == "open")
            .where(OpenShiftPost.business_date >= week_start)
            .where(OpenShiftPost.business_date < week_end)
        )
        .scalars()
        .all()
    )
    for post in open_posts:
        add_date(post.business_date, "open_shifts")

    return {"by_date": by_date, "by_cell": by_cell}


def list_week(
    db: Session,
    *,
    week_start: date,
    user_ids: Iterable[int] | None = None,
) -> dict:
    """One-shot payload for the manager's weekly grid.

    Returns:

        {
          "week_start": "YYYY-MM-DD",
          "days": ["YYYY-MM-DD", ...7],
          "staff": [{id, full_name, username}, ...],
          "entries": [<_entry_to_dict>, ...],
          "time_off_blocks": [
            {user_id, request_id, starts_at_local, ends_at_local}, ...
          ],
          "overlap_warnings": [
            {user_id, entry_ids: [a, b], business_date}, ...
          ],
        }

    Overlap warnings are advisory (see `_compute_overlap_warnings`):
    they flag same-stylist entries whose intervals overlap so the grid
    can warn without blocking manual split shifts.

    Time-off blocks are restricted to APPROVED requests intersecting
    the week — those are what the grid grays out. Pending/denied do
    not block the cell.

    `labor_cost` aggregates `users.hourly_wage` against entry hours
    for the visible scope; see `compute_labor_cost`.
    """
    if week_start.isoweekday() != 1:
        # Mon-anchored grid; reject Tue-anchored window so the frontend
        # can't get out of sync by walking days instead of weeks.
        raise StaffScheduleError(
            "week_start_not_monday", http_status=422
        )

    days = [d.isoformat() for d in _week_dates(week_start)]
    week_start_utc, week_end_utc = _week_bounds_utc(week_start)

    staff_stmt = (
        select(
            User.id,
            User.username,
            User.full_name,
            User.hourly_wage,
        )
        .where(User.is_active.is_(True))
        .order_by(User.full_name.nullsfirst(), User.username)
    )
    if user_ids is not None:
        ids = list(user_ids)
        if not ids:
            empty_target = compute_labor_target(
                db, week_start=week_start, labor_cost_cents=0
            )
            return {
                "week_start": week_start.isoformat(),
                "days": days,
                "staff": [],
                "entries": [],
                "time_off_blocks": [],
                "recurring_unavailable_blocks": [],
                "overlap_warnings": [],
                "labor_cost": {
                    "total_cents": 0,
                    "draft_cents": 0,
                    "published_cents": 0,
                    "unknown_wage_user_ids": [],
                },
                "labor_target": empty_target,
                "appointment_density_warnings": [],
                "schedule_exception_counts": {"by_date": {}, "by_cell": {}},
            }
        staff_stmt = staff_stmt.where(User.id.in_(ids))
    staff_rows = db.execute(staff_stmt).all()
    staff = [
        {
            "id": row.id,
            "username": row.username,
            "full_name": row.full_name,
        }
        for row in staff_rows
    ]
    staff_id_set = {s["id"] for s in staff}
    wage_by_user_id: dict[int, Decimal | None] = {
        row.id: row.hourly_wage for row in staff_rows
    }

    entries_stmt = (
        select(StaffScheduleEntry)
        .where(StaffScheduleEntry.business_date >= week_start)
        .where(
            StaffScheduleEntry.business_date < week_start + timedelta(days=7)
        )
        .order_by(
            StaffScheduleEntry.user_id,
            StaffScheduleEntry.starts_at_local,
        )
    )
    if user_ids is not None:
        entries_stmt = entries_stmt.where(
            StaffScheduleEntry.user_id.in_(staff_id_set)
        )
    entry_rows = list(db.execute(entries_stmt).scalars().all())
    entries = [_entry_to_dict(e) for e in entry_rows]
    overlap_warnings = _compute_overlap_warnings(entry_rows)
    labor_cost = compute_labor_cost(entry_rows, wage_by_user_id)

    tor_stmt = (
        select(TimeOffRequest)
        .where(TimeOffRequest.status == "approved")
        .where(TimeOffRequest.starts_at < week_end_utc)
        .where(TimeOffRequest.ends_at > week_start_utc)
    )
    if user_ids is not None:
        tor_stmt = tor_stmt.where(
            TimeOffRequest.user_id.in_(staff_id_set)
        )
    tz = shop_tz()
    time_off_blocks = [
        {
            "user_id": r.user_id,
            "request_id": r.id,
            "starts_at_local": r.starts_at.astimezone(tz).isoformat(),
            "ends_at_local": r.ends_at.astimezone(tz).isoformat(),
        }
        for r in db.execute(tor_stmt).scalars().all()
    ]

    recurring_unavailable_blocks = (
        recurring_availability.expand_blocks_for_week(
            db, week_start=week_start, user_ids=staff_id_set or None
        )
    )

    labor_target = compute_labor_target(
        db,
        week_start=week_start,
        labor_cost_cents=int(labor_cost["total_cents"]),
    )
    appointment_density_warnings = compute_appointment_density_warnings(
        db,
        week_start=week_start,
        entries=entry_rows,
        time_off_blocks=time_off_blocks,
        recurring_unavailable_blocks=recurring_unavailable_blocks,
    )

    return {
        "week_start": week_start.isoformat(),
        "days": days,
        "staff": staff,
        "entries": entries,
        "time_off_blocks": time_off_blocks,
        "recurring_unavailable_blocks": recurring_unavailable_blocks,
        "overlap_warnings": overlap_warnings,
        "labor_cost": labor_cost,
        "labor_target": labor_target,
        "appointment_density_warnings": appointment_density_warnings,
        "schedule_exception_counts": _schedule_exception_counts(
            db,
            week_start=week_start,
            staff_id_set=staff_id_set,
            overlap_warnings=overlap_warnings,
        ),
    }


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def _conflicting_time_off_locked(
    db: Session,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
) -> int | None:
    """Slice-4 hardened variant of `_check_time_off_conflict`: returns
    the conflicting time_off_request id (or None) and uses
    SELECT ... FOR UPDATE to hold a row lock for the rest of the
    transaction.

    The lock covers BOTH `'approved'` AND `'pending'` rows in the
    overlap window. The approved-only lock the earlier slice shipped
    only serialized against rows already terminal — a parallel
    `time_off.decide_request` flipping a pending row to approved
    could still slip past. Including 'pending' in the FOR UPDATE
    makes the publish transaction wait for any in-flight approval
    of an overlapping request: once `decide_request` commits, we
    re-read and see `status='approved'` (still treated as conflict)
    OR `status='denied'` (no conflict, publish proceeds).

    Returns the conflicting request id only when it's currently
    APPROVED — pending rows that survive the lock window without
    being approved aren't blocking. We always lock both, only block
    on approved.
    """
    starts_utc = starts_at_local.astimezone(timezone.utc)
    ends_utc = ends_at_local.astimezone(timezone.utc)

    # Step 1: take the FOR UPDATE lock on any pending/approved
    # overlapping rows. This serializes us against decide_request.
    locked = (
        db.execute(
            select(TimeOffRequest.id, TimeOffRequest.status)
            .where(TimeOffRequest.user_id == user_id)
            .where(TimeOffRequest.status.in_(("pending", "approved")))
            .where(TimeOffRequest.starts_at < ends_utc)
            .where(TimeOffRequest.ends_at > starts_utc)
            .with_for_update()
        )
        .all()
    )
    # Step 2: among the locked rows, return the first one that's
    # currently approved (post-lock — if a pending row got flipped
    # while we were waiting, our re-read sees the new status).
    for row in locked:
        if row.status == "approved":
            return row.id
    return None


def publish_week(
    db: Session,
    *,
    actor_user_id: int,
    week_start: date,
    user_ids: Iterable[int] | None = None,
) -> dict:
    """Per-shift partial publish: flip every draft entry in the week
    to 'published' EXCEPT the ones that overlap an approved time-off
    request for the same stylist.

    The time-off check uses `SELECT ... FOR UPDATE` so a parallel
    `time_off.decide_request` that approves a request for the same
    stylist during this transaction is serialized — the race window
    where a draft is published and then the time-off is approved
    seconds later collapses to a wait-and-recheck.

    Returns
    -------
    dict
        ``{week_start, published_count, entry_ids, skipped}`` where
        each ``skipped`` row carries the entry id, user id, business
        date, local start/end, and the conflicting
        ``time_off_request_id`` so the UI can name the specific
        shift that was blocked.

    Slice-4 changes the semantics from Slice 1's wholesale-abort to
    per-shift partial publish. The frontend warns when ``skipped`` is
    non-empty; the smoke that previously expected a wholesale 409 was
    updated alongside.
    """
    if week_start.isoweekday() != 1:
        raise StaffScheduleError(
            "week_start_not_monday", http_status=422
        )

    stmt = (
        select(StaffScheduleEntry)
        .where(StaffScheduleEntry.business_date >= week_start)
        .where(
            StaffScheduleEntry.business_date < week_start + timedelta(days=7)
        )
        .where(StaffScheduleEntry.status == "draft")
    )
    if user_ids is not None:
        ids = list(user_ids)
        if not ids:
            return {
                "week_start": week_start.isoformat(),
                "published_count": 0,
                "entry_ids": [],
                "skipped": [],
            }
        stmt = stmt.where(StaffScheduleEntry.user_id.in_(ids))

    candidates = list(db.execute(stmt).scalars().all())

    now_utc = datetime.now(timezone.utc)
    published_ids: list[int] = []
    skipped: list[dict] = []

    for entry in candidates:
        conflict_id = _conflicting_time_off_locked(
            db,
            user_id=entry.user_id,
            starts_at_local=entry.starts_at_local,
            ends_at_local=entry.ends_at_local,
        )
        if conflict_id is not None:
            skipped.append(
                {
                    "entry_id": entry.id,
                    "user_id": entry.user_id,
                    "business_date": entry.business_date.isoformat(),
                    "starts_at_local": entry.starts_at_local.astimezone(
                        shop_tz()
                    ).isoformat(),
                    "ends_at_local": entry.ends_at_local.astimezone(
                        shop_tz()
                    ).isoformat(),
                    "time_off_request_id": conflict_id,
                    "reason": "time_off_conflict",
                }
            )
            continue
        recurring_block_id = recurring_availability.find_conflict(
            db,
            user_id=entry.user_id,
            starts_at_local=entry.starts_at_local,
            ends_at_local=entry.ends_at_local,
        )
        if recurring_block_id is not None:
            skipped.append(
                {
                    "entry_id": entry.id,
                    "user_id": entry.user_id,
                    "business_date": entry.business_date.isoformat(),
                    "starts_at_local": entry.starts_at_local.astimezone(
                        shop_tz()
                    ).isoformat(),
                    "ends_at_local": entry.ends_at_local.astimezone(
                        shop_tz()
                    ).isoformat(),
                    "recurring_unavailable_block_id": recurring_block_id,
                    "reason": "recurring_unavailable_conflict",
                }
            )
            continue
        entry.status = "published"
        entry.published_at = now_utc
        entry.published_by_user_id = actor_user_id
        entry.updated_at = now_utc
        published_ids.append(entry.id)
    db.flush()

    _send_schedule_published_emails(
        db, week_start=week_start, published_ids=published_ids
    )

    return {
        "week_start": week_start.isoformat(),
        "published_count": len(published_ids),
        "entry_ids": published_ids,
        "skipped": skipped,
    }


def resend_published_week(
    db: Session,
    *,
    week_start: date,
    actor_user_id: int,
    user_ids: Iterable[int] | None = None,
) -> dict:
    """Re-send the staff.schedule_published email for every recipient who
    has at least one published shift in the given week.

    Bypasses ``record_event`` because the broadcast doesn't fit
    intrinsic+role-default recipient resolution — the admin is the one
    explicitly picking who gets re-notified (default: every affected
    staffer). Each enqueued job carries ``payload.manual_resend = true``
    so an audit query against notification_jobs can tell originals from
    resends.

    Returns
    -------
    dict
        ``{week_start, recipients, jobs_enqueued, skipped_users}``.
        ``skipped_users`` lists user_ids the admin requested who had no
        published shifts in the week — the caller can surface that in
        the UI so a stale selection doesn't silently no-op.
    """
    if week_start.isoweekday() != 1:
        raise StaffScheduleError(
            "week_start_not_monday", http_status=422
        )

    week_end = week_start + timedelta(days=7)
    stmt = (
        select(StaffScheduleEntry)
        .where(StaffScheduleEntry.business_date >= week_start)
        .where(StaffScheduleEntry.business_date < week_end)
        .where(StaffScheduleEntry.status == "published")
    )
    requested_ids: set[int] | None = None
    if user_ids is not None:
        requested_ids = {int(uid) for uid in user_ids}
        if not requested_ids:
            return {
                "week_start": week_start.isoformat(),
                "recipients": [],
                "jobs_enqueued": 0,
                "skipped_users": [],
            }
        stmt = stmt.where(StaffScheduleEntry.user_id.in_(requested_ids))

    entries = list(db.execute(stmt).scalars().all())
    by_user: dict[int, list[StaffScheduleEntry]] = {}
    for entry in entries:
        by_user.setdefault(entry.user_id, []).append(entry)

    from services.notification_service import enqueue_staff_job

    recipients: list[int] = []
    jobs_enqueued = 0
    for user_id, user_entries in by_user.items():
        user = db.get(User, user_id)
        if user is None or not user.email or not user.is_active:
            continue
        user_entries.sort(key=lambda e: e.starts_at_local)
        payload = {
            "week_start": week_start.isoformat(),
            "shifts": [
                {
                    "starts_at": e.starts_at_local.isoformat(),
                    "ends_at": e.ends_at_local.isoformat(),
                    "title": "Boutique shift",
                    "location": "Bella's XV boutique",
                    "notes": (e.manager_notes or "").strip() or None,
                }
                for e in user_entries
            ],
            "manual_resend": True,
            "resent_by_user_id": actor_user_id,
        }
        enqueue_staff_job(
            db,
            kind="staff.schedule_published",
            recipient_user_id=user.id,
            recipient=user.email,
            subject_kind="schedule_week",
            subject_id=user.id,
            payload=payload,
        )
        recipients.append(user.id)
        jobs_enqueued += 1
    db.flush()

    skipped_users: list[int] = []
    if requested_ids is not None:
        skipped_users = sorted(requested_ids - set(by_user.keys()))

    return {
        "week_start": week_start.isoformat(),
        "recipients": recipients,
        "jobs_enqueued": jobs_enqueued,
        "skipped_users": skipped_users,
    }


def publish_entry(
    db: Session,
    *,
    actor_user_id: int,
    entry_id: int,
) -> dict:
    """Publish a single draft entry.

    Reuses the same `_conflicting_time_off_locked` check as
    `publish_week` so the publish-vs-approve race protections apply
    identically — a parallel `time_off.decide_request` that's mid-
    approval for an overlapping request will serialize against our
    SELECT FOR UPDATE.

    Errors:
      * `entry_not_found` (404) — id doesn't exist
      * `entry_already_published` (409) — non-draft status. Idempotent
        in the sense that re-publishing is a no-op, but loud rather
        than silent so a UI race (two managers clicking simultaneously)
        surfaces the second click instead of pretending it succeeded.
      * `time_off_conflict` (409) — overlapping approved time-off;
        `extra.time_off_request_id` names the conflicting request.

    Returns the freshly-serialized entry dict (same shape as
    `create_entry`).
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.status != "draft":
        raise StaffScheduleError(
            "entry_already_published",
            http_status=409,
            extra={"status": entry.status},
        )

    conflict_id = _conflicting_time_off_locked(
        db,
        user_id=entry.user_id,
        starts_at_local=entry.starts_at_local,
        ends_at_local=entry.ends_at_local,
    )
    if conflict_id is not None:
        raise StaffScheduleError(
            "time_off_conflict",
            http_status=409,
            extra={"time_off_request_id": conflict_id},
        )

    _check_recurring_unavailable_conflict(
        db,
        user_id=entry.user_id,
        starts_at_local=entry.starts_at_local,
        ends_at_local=entry.ends_at_local,
    )

    now_utc = datetime.now(timezone.utc)
    entry.status = "published"
    entry.published_at = now_utc
    entry.published_by_user_id = actor_user_id
    entry.updated_at = now_utc
    db.flush()
    _send_shift_added_event(db, entry=entry, actor_user_id=actor_user_id)
    return _entry_to_dict(entry)


# ---------------------------------------------------------------------------
# Targeted post-publish writes
# ---------------------------------------------------------------------------


def set_manager_notes(
    db: Session,
    *,
    entry_id: int,
    notes: str | None,
) -> dict:
    """Set or clear `manager_notes` on any entry regardless of status.

    Notes are the inline field the manager fills next to a missed
    shift ("called out sick" / "no call no show"). The endpoint exists
    on its own verb instead of routing through `update_entry` so that
    published rows can be annotated without unlocking the time-shift
    path.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    entry.manager_notes = (notes or "").strip() or None
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _entry_to_dict(entry)


def mark_excused(
    db: Session,
    *,
    actor_user_id: int,
    entry_id: int,
    notes: str | None = None,
) -> dict:
    """Flip a `no_show` entry to `excused`. Optional notes are merged
    into `manager_notes` (appended, not replaced — the original "no
    call no show" note is worth keeping).

    Slice 1 ships this excuse path so the validation logic is in place
    even though `no_show` rows can only be created by Slice 2's cron.
    Smoke seeds a no-show row by direct INSERT to exercise the flip.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.attendance_status != "no_show":
        raise StaffScheduleError(
            "entry_not_no_show",
            http_status=409,
            extra={"attendance_status": entry.attendance_status},
        )

    entry.attendance_status = "excused"
    if notes:
        addition = notes.strip()
        if addition:
            entry.manager_notes = (
                f"{entry.manager_notes}\n{addition}"
                if entry.manager_notes
                else addition
            )
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _entry_to_dict(entry)


# ---------------------------------------------------------------------------
# Clock-in / clock-out write-through (Phase 10 Slice 2)
# ---------------------------------------------------------------------------


def stamp_clock_in(
    db: Session,
    *,
    schedule_entry_id: int,
    punch_id: int,
    punched_at_local: datetime,
) -> StaffScheduleEntry | None:
    """Stamp `actual_clock_in_punch_id` on the entry and re-derive
    `attendance_status`.

    Returns the updated entry, or None if it can't be stamped (entry
    missing, not published, or already stamped with a different punch).
    Caller never raises on a no-op — clock-in must not 500 just because
    the schedule layer is in an unexpected state.

    Status flip rules:

      - `scheduled` → `present` if punched_at_local <= grace boundary,
        else `late`.
      - `no_show` → same as above. (A late arrival recovers a row the
        cron pre-emptively flipped.)
      - `missing_out_punch` → same. (A stylist who left without
        clocking out yesterday and the manager hasn't resolved it yet
        comes back today; clocking back in shouldn't be blocked. The
        Slice-4 cron flips a clocked-in-but-not-out entry to
        missing_out_punch only after business_date passes, so the
        re-clock case is rare but real.)
      - Anything else (`present`, `late`, `excused`) → leave alone
        (idempotent — punching twice doesn't ratchet the status).

    The entry's own `late_grace_minutes` is the grace value. The Slice
    1 contract copied it onto the entry at publish time so the cron
    and this hook read the same number.
    """
    entry = db.get(StaffScheduleEntry, schedule_entry_id)
    if entry is None or entry.status != "published":
        return None
    if (
        entry.actual_clock_in_punch_id is not None
        and entry.actual_clock_in_punch_id != punch_id
    ):
        # Already stamped with a different punch — do not overwrite
        # the historical link.
        return None

    entry.actual_clock_in_punch_id = punch_id
    if entry.attendance_status in (
        "scheduled",
        "no_show",
        "missing_out_punch",
    ):
        threshold = entry.starts_at_local.astimezone(
            shop_tz()
        ) + timedelta(minutes=int(entry.late_grace_minutes))
        local = punched_at_local.astimezone(shop_tz())
        entry.attendance_status = "late" if local > threshold else "present"
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return entry


def stamp_clock_out(
    db: Session,
    *,
    in_punch_id: int,
    out_punch_id: int,
) -> StaffScheduleEntry | None:
    """Stamp `actual_clock_out_punch_id` on the entry whose
    `actual_clock_in_punch_id` matches `in_punch_id`.

    Returns the updated entry or None if no schedule entry was
    associated with the in-punch. Like `stamp_clock_in`, never
    raises — clock-out shouldn't fail because of schedule layer drift.
    """
    entry = (
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.actual_clock_in_punch_id == in_punch_id)
        .first()
    )
    if entry is None or entry.status != "published":
        return None
    if (
        entry.actual_clock_out_punch_id is not None
        and entry.actual_clock_out_punch_id != out_punch_id
    ):
        return None

    entry.actual_clock_out_punch_id = out_punch_id
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return entry


# ---------------------------------------------------------------------------
# No-show cron (Phase 10 Slice 2)
# ---------------------------------------------------------------------------


def find_missing_out_candidates(
    db: Session, *, as_of_utc: datetime
) -> list[StaffScheduleEntry]:
    """Slice-4 "36-hour shift" detector. Returns published entries
    where the stylist clocked in, did NOT clock out, and the
    entry's business_date is strictly before today (boutique-local).

    The "strictly before today" guard is what makes this safe to run
    repeatedly through the day without flagging legitimate ongoing
    shifts. The cron is scheduled in the daily worker (02:30 local)
    so by the time it fires, "yesterday" has truly closed.

    We additionally exclude entries already in a terminal state
    (`missing_out_punch`, `excused`) to keep re-runs idempotent.
    """
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    today_local = as_of_utc.astimezone(shop_tz()).date()
    return list(
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.status == "published")
        .filter(StaffScheduleEntry.actual_clock_in_punch_id.isnot(None))
        .filter(StaffScheduleEntry.actual_clock_out_punch_id.is_(None))
        .filter(StaffScheduleEntry.business_date < today_local)
        .filter(
            StaffScheduleEntry.attendance_status.notin_(
                ("missing_out_punch", "excused")
            )
        )
        .all()
    )


def mark_missing_out_punches(
    db: Session, *, as_of_utc: datetime | None = None
) -> list[int]:
    """Flip every overdue clocked-in-but-not-out entry to
    `missing_out_punch`. Returns the flipped entry ids; caller commits.
    """
    if as_of_utc is None:
        as_of_utc = datetime.now(timezone.utc)
    flipped: list[int] = []
    for entry in find_missing_out_candidates(db, as_of_utc=as_of_utc):
        entry.attendance_status = "missing_out_punch"
        entry.updated_at = datetime.now(timezone.utc)
        flipped.append(entry.id)
    db.flush()
    return flipped


def resolve_missing_out_punch(
    db: Session,
    *,
    actor_user_id: int,
    entry_id: int,
    out_at_local: datetime,
    notes: str | None = None,
) -> dict:
    """Manager-driven recovery from `missing_out_punch`. Inserts a
    paired out-punch at the manager-supplied time, stamps the
    entry's actual_clock_out_punch_id, re-derives attendance_status
    (`present` if the original in landed inside the grace window,
    else `late`), and writes a `staff_punch_audit_events` row so the
    fix is on the timeline.

    Reuses the in-punch's location and shift_id so the recovered
    out-punch looks like a normal closing punch to downstream
    reporting; status is `'manual_adjusted'` and
    `hours_confirmation_status='confirmed'` since the manager is
    asserting these hours by hand.
    """
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffScheduleError("entry_not_found", http_status=404)
    if entry.attendance_status != "missing_out_punch":
        raise StaffScheduleError(
            "entry_not_missing_out_punch",
            http_status=409,
            extra={"attendance_status": entry.attendance_status},
        )
    if entry.actual_clock_in_punch_id is None:
        # Shouldn't be reachable — missing_out_punch implies an in
        # was stamped. Defensive code path so a manual DB nudge can't
        # walk us into a NoneType deref.
        raise StaffScheduleError(
            "entry_missing_in_punch", http_status=409
        )

    out_at_local = _ensure_aware(out_at_local, field="out_at_local")
    in_punch = db.get(StaffPunch, entry.actual_clock_in_punch_id)
    if in_punch is None:
        raise StaffScheduleError(
            "in_punch_not_found", http_status=409
        )

    out_utc = out_at_local.astimezone(timezone.utc)
    if out_utc <= in_punch.punched_at:
        raise StaffScheduleError(
            "invalid_date_range",
            http_status=422,
            extra={"reason": "out must be after the in-punch"},
        )

    out_punch = StaffPunch(
        user_id=entry.user_id,
        direction="out",
        punched_at=out_utc,
        status="manual_adjusted",
        location_id=in_punch.location_id,
        shift_id=in_punch.shift_id,
        holiday_id=in_punch.holiday_id,
        auto_closed=False,
        hours_confirmation_status="confirmed",
        hours_confirmed_by_user_id=actor_user_id,
        hours_confirmed_at=datetime.now(timezone.utc),
        notes=(notes or "").strip() or None,
    )
    db.add(out_punch)
    db.flush()

    db.add(
        StaffPunchAuditEvent(
            punch_id=out_punch.id,
            actor_kind="owner",
            actor_user_id=actor_user_id,
            action="punch.missing_out_resolved",
            reason_code="missing_out_punch_resolved",
            old_values={
                "in_punch_id": in_punch.id,
                "schedule_entry_id": entry.id,
                "attendance_status": "missing_out_punch",
            },
            new_values={
                "out_punch_id": out_punch.id,
                "punched_at": out_utc.isoformat(),
            },
            notes=(notes or "").strip() or None,
        )
    )

    entry.actual_clock_out_punch_id = out_punch.id
    # Re-derive attendance_status from the original in-punch against
    # the entry's late-grace window.
    in_local = in_punch.punched_at.astimezone(shop_tz())
    threshold = entry.starts_at_local.astimezone(
        shop_tz()
    ) + timedelta(minutes=int(entry.late_grace_minutes))
    entry.attendance_status = "late" if in_local > threshold else "present"
    if notes:
        addition = notes.strip()
        if addition:
            entry.manager_notes = (
                f"{entry.manager_notes}\n{addition}"
                if entry.manager_notes
                else addition
            )
    entry.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _entry_to_dict(entry)


def find_no_show_candidates(
    db: Session, *, as_of_utc: datetime
) -> list[StaffScheduleEntry]:
    """Return published entries whose late-grace window has elapsed
    with no clock-in. The query mirrors the partial index seeded by
    migration 068 so it stays cheap as historical rows pile up.
    """
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    # We compare in the column's tz-aware semantics. Postgres normalizes
    # both sides to UTC for the comparison, so the timedelta math here
    # is correct even when the boutique is mid-DST.
    candidates = (
        db.query(StaffScheduleEntry)
        .filter(StaffScheduleEntry.status == "published")
        .filter(StaffScheduleEntry.attendance_status == "scheduled")
        .filter(StaffScheduleEntry.actual_clock_in_punch_id.is_(None))
        .all()
    )
    out: list[StaffScheduleEntry] = []
    for e in candidates:
        threshold = e.starts_at_local + timedelta(
            minutes=int(e.late_grace_minutes)
        )
        if threshold.astimezone(timezone.utc) < as_of_utc:
            out.append(e)
    return out


def mark_no_shows(
    db: Session, *, as_of_utc: datetime | None = None
) -> list[int]:
    """Flip every overdue 'scheduled' entry to 'no_show'.

    Returns the list of flipped entry ids. Caller commits the
    transaction — this keeps the cron's commit/rollback discipline in
    one place rather than splitting it across the service and the
    worker.
    """
    if as_of_utc is None:
        as_of_utc = datetime.now(timezone.utc)
    flipped: list[int] = []
    for entry in find_no_show_candidates(db, as_of_utc=as_of_utc):
        entry.attendance_status = "no_show"
        entry.updated_at = datetime.now(timezone.utc)
        flipped.append(entry.id)
    db.flush()
    return flipped


# ---------------------------------------------------------------------------
# Read paths for the Attendance Review additions
# ---------------------------------------------------------------------------


def list_flagged_exceptions(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    user_id: int | None = None,
) -> list[dict]:
    """Return published entries with `attendance_status` in
    (`no_show`, `missing_out_punch`) for the date range, with the
    stylist's display name attached.

    The 'Flagged Exceptions' card on Attendance Review reads this:
    both flavors are manager-actionable. `no_show` exposes a "Mark
    excused" CTA, `missing_out_punch` exposes a "Resolve" CTA that
    captures the actual out-time.
    """
    if to_date < from_date:
        raise StaffScheduleError("invalid_date_range", http_status=422)

    stmt = (
        select(
            StaffScheduleEntry,
            User.username,
            User.full_name,
        )
        .join(User, User.id == StaffScheduleEntry.user_id)
        .where(StaffScheduleEntry.business_date >= from_date)
        .where(StaffScheduleEntry.business_date <= to_date)
        .where(StaffScheduleEntry.status == "published")
        .where(
            StaffScheduleEntry.attendance_status.in_(
                ("no_show", "missing_out_punch")
            )
        )
        .order_by(
            StaffScheduleEntry.business_date.desc(),
            StaffScheduleEntry.starts_at_local,
        )
    )
    if user_id is not None:
        stmt = stmt.where(StaffScheduleEntry.user_id == user_id)

    rows: list[dict] = []
    for entry, username, full_name in db.execute(stmt).all():
        d = _entry_to_dict(entry)
        d["user_username"] = username
        d["user_full_name"] = full_name
        rows.append(d)
    return rows


def hours_variance(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    user_id: int | None = None,
) -> list[dict]:
    """Per-staff scheduled-vs-actual hours over published entries.

    Scheduled hours = sum of (`ends_at_local - starts_at_local`) for
    every published entry in range. Actual hours = sum of (out punch
    `punched_at` - in punch `punched_at`) when both punches are
    stamped. Variance = actual - scheduled (negative when staff
    worked less than scheduled).

    Used by the "Hours variance" card on Attendance Review so payroll
    can see "Maria was scheduled 40h, worked 38.5h" without joining
    the tables by hand. Returns rows sorted by abs(variance) descending
    so the biggest deltas float to the top.
    """
    from database.models import StaffPunch  # local import to dodge a
    # circular: clock_in -> staff_schedule -> models is fine, but the
    # service file is imported during model bootstrap in some test
    # harnesses, so keeping this local guards against import order.

    if to_date < from_date:
        raise StaffScheduleError("invalid_date_range", http_status=422)

    stmt = (
        select(StaffScheduleEntry, User.username, User.full_name)
        .join(User, User.id == StaffScheduleEntry.user_id)
        .where(StaffScheduleEntry.business_date >= from_date)
        .where(StaffScheduleEntry.business_date <= to_date)
        .where(StaffScheduleEntry.status == "published")
    )
    if user_id is not None:
        stmt = stmt.where(StaffScheduleEntry.user_id == user_id)

    per_user: dict[int, dict] = {}
    in_punch_ids: set[int] = set()
    out_punch_ids: set[int] = set()
    user_entries: dict[int, list[StaffScheduleEntry]] = {}

    for entry, username, full_name in db.execute(stmt).all():
        per_user.setdefault(
            entry.user_id,
            {
                "user_id": entry.user_id,
                "username": username,
                "full_name": full_name,
                "scheduled_hours": 0.0,
                "actual_hours": 0.0,
                "entry_count": 0,
                "stamped_pairs": 0,
            },
        )
        per_user[entry.user_id]["scheduled_hours"] += (
            (entry.ends_at_local - entry.starts_at_local).total_seconds()
            / 3600.0
        )
        per_user[entry.user_id]["entry_count"] += 1
        if entry.actual_clock_in_punch_id is not None:
            in_punch_ids.add(entry.actual_clock_in_punch_id)
        if entry.actual_clock_out_punch_id is not None:
            out_punch_ids.add(entry.actual_clock_out_punch_id)
        user_entries.setdefault(entry.user_id, []).append(entry)

    # Batch-load every referenced punch in a single round trip rather
    # than N+1 lookups per entry.
    punch_rows = (
        db.execute(
            select(StaffPunch).where(
                StaffPunch.id.in_(in_punch_ids | out_punch_ids)
            )
        )
        .scalars()
        .all()
        if in_punch_ids or out_punch_ids
        else []
    )
    punches_by_id = {p.id: p for p in punch_rows}

    for user_id_, entries in user_entries.items():
        for entry in entries:
            in_id = entry.actual_clock_in_punch_id
            out_id = entry.actual_clock_out_punch_id
            if in_id is None or out_id is None:
                continue
            in_p = punches_by_id.get(in_id)
            out_p = punches_by_id.get(out_id)
            if in_p is None or out_p is None:
                continue
            delta = (out_p.punched_at - in_p.punched_at).total_seconds() / 3600.0
            if delta > 0:
                per_user[user_id_]["actual_hours"] += delta
                per_user[user_id_]["stamped_pairs"] += 1

    rows = []
    for r in per_user.values():
        scheduled = round(r["scheduled_hours"], 2)
        actual = round(r["actual_hours"], 2)
        rows.append(
            {
                "user_id": r["user_id"],
                "username": r["username"],
                "full_name": r["full_name"],
                "scheduled_hours": scheduled,
                "actual_hours": actual,
                "variance_hours": round(actual - scheduled, 2),
                "entry_count": r["entry_count"],
                "stamped_pairs": r["stamped_pairs"],
            }
        )
    rows.sort(key=lambda r: abs(r["variance_hours"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Sales-scoped team-schedule read (Phase 10 Slice 5)
# ---------------------------------------------------------------------------


def list_team_published_schedule(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Return PUBLISHED schedule entries for ALL active staff inside
    the requested date range, with just enough display info for the
    stylist-facing team-schedule view.

    This is the sales surface — what shows up at
    `sales.shopbellasxv.com/schedule` under the "Team" tab. The
    returned dicts deliberately carry **only** the columns coworkers
    are allowed to see:

      * `user_id`, `username`, `full_name` — display attribution
      * `entry_id`, `business_date`,
        `starts_at_local`, `ends_at_local` — the shift itself

    Excluded for privacy: `manager_notes`,
    `attendance_status`, `actual_clock_in_punch_id`,
    `actual_clock_out_punch_id`, `late_grace_minutes`, `published_by`,
    timestamps. The endpoint that wraps this serializes only the
    fields below; the dict shape IS the privacy contract.

    Drafts are excluded outright (`status='published'`), so a
    coworker can never accidentally see a shift the manager hasn't
    released yet.

    Sorted by `business_date, starts_at_local, full_name` so the
    UI can `groupBy(business_date)` and the same day's stylists
    appear in stable order.
    """
    if to_date < from_date:
        raise StaffScheduleError("invalid_date_range", http_status=422)

    # role='sales' is the stylist set per the existing
    # admin_sales_staff convention and the sales_auth scope check.
    # Without this filter, an active admin/owner user with a
    # published schedule entry (rare, but possible via the admin
    # grid) would leak into the coworker view.
    stmt = (
        select(
            StaffScheduleEntry.id,
            StaffScheduleEntry.user_id,
            StaffScheduleEntry.business_date,
            StaffScheduleEntry.starts_at_local,
            StaffScheduleEntry.ends_at_local,
            User.username,
            User.full_name,
        )
        .join(User, User.id == StaffScheduleEntry.user_id)
        .where(StaffScheduleEntry.status == "published")
        .where(StaffScheduleEntry.business_date >= from_date)
        .where(StaffScheduleEntry.business_date <= to_date)
        .where(User.is_active.is_(True))
        .where(User.role == "sales")
        .order_by(
            StaffScheduleEntry.business_date,
            StaffScheduleEntry.starts_at_local,
            User.full_name.nullsfirst(),
            User.username,
        )
    )

    tz = shop_tz()
    rows: list[dict] = []
    for (
        entry_id,
        user_id,
        biz_date,
        starts_at,
        ends_at,
        username,
        full_name,
    ) in db.execute(stmt).all():
        rows.append(
            {
                "entry_id": entry_id,
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "business_date": biz_date.isoformat(),
                "starts_at_local": starts_at.astimezone(tz).isoformat(),
                "ends_at_local": ends_at.astimezone(tz).isoformat(),
            }
        )
    return rows


__all__ = [
    "DEFAULT_LATE_GRACE_MINUTES",
    "StaffScheduleError",
    "compute_appointment_density_warnings",
    "compute_labor_cost",
    "compute_labor_target",
    "create_entry",
    "delete_entry",
    "find_missing_out_candidates",
    "find_no_show_candidates",
    "hours_variance",
    "list_flagged_exceptions",
    "list_team_published_schedule",
    "list_week",
    "mark_excused",
    "mark_missing_out_punches",
    "mark_no_shows",
    "publish_entry",
    "publish_week",
    "resend_published_week",
    "resolve_missing_out_punch",
    "set_manager_notes",
    "stamp_clock_in",
    "stamp_clock_out",
    "update_entry",
]
