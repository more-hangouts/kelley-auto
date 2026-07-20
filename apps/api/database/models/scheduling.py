"""Staff scheduling, shifts, punches, time-off, attendance.

Split from the former monolithic database/models.py (Phase 3). All classes
subclass the single Base from database.connection; foreign keys are string
references, so cross-domain FKs need no import between these files.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID

from database.connection import Base



class StaffLocation(Base):
    """Per-boutique geofence center. The clock-in handler computes
    haversine distance against every `active=True` row and accepts the
    punch only if at least one is within `radius_m`. radius_m is
    bounded to 25-1000 to catch fat-finger mistakes that would
    otherwise let punches in from blocks away."""

    __tablename__ = "staff_locations"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    latitude = Column(Numeric(10, 7), nullable=False)
    longitude = Column(Numeric(10, 7), nullable=False)
    radius_m = Column(Integer, nullable=False)
    grace_minutes = Column(Integer, nullable=False, server_default=text("0"))
    default_auto_session_close_time = Column(Time)
    active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffPunch(Base):
    """One row per clock-in or clock-out event.

    Phase 7 Slice 1 only writes `direction='in' | 'out'`,
    `status='unscheduled'` (no shift data yet — that lands in Phase 8),
    `location_id`, the client-supplied coords + accuracy + computed
    `distance_to_location_m`, and the request `ip` / `user_agent`.

    `shift_id` and `holiday_id` are plain nullable columns (no FK).
    Phase 8's migration adds the FKs against `staff_shifts` and
    `staff_holidays` once those tables exist.
    """

    __tablename__ = "staff_punches"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    direction = Column(String(8), nullable=False)
    punched_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    status = Column(
        String(20), nullable=False, server_default=text("'recorded'")
    )
    location_id = Column(
        Integer, ForeignKey("staff_locations.id", ondelete="SET NULL")
    )
    shift_id = Column(
        BigInteger, ForeignKey("staff_shifts.id", ondelete="SET NULL")
    )
    holiday_id = Column(
        Integer, ForeignKey("staff_holidays.id", ondelete="SET NULL")
    )
    client_latitude = Column(Numeric(10, 7))
    client_longitude = Column(Numeric(10, 7))
    client_accuracy_m = Column(Numeric(10, 2))
    distance_to_location_m = Column(Numeric(10, 2))
    selfie_storage_key = Column(String(255))
    auto_closed = Column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    auto_close_reason = Column(String(24))
    auto_closed_at = Column(DateTime(timezone=True))
    hours_confirmation_status = Column(
        String(20),
        nullable=False,
        server_default=text("'not_required'"),
    )
    hours_confirmed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    hours_confirmed_at = Column(DateTime(timezone=True))
    user_agent = Column(String(255))
    ip = Column(INET)
    notes = Column(Text)
    # Clock-in reliability slice A: how the geofence gate accepted this
    # punch. `'gps'` = strict radius pass; `'gps_with_accuracy_buffer'`
    # = pass after widening the gate by the configured accuracy cap;
    # `'trusted_network'` is reserved for slice C. punch_out always
    # records `'gps'` because out-punches do not enforce the geofence.
    accepted_by = Column(
        String(32), nullable=False, server_default=text("'gps'")
    )
    # When the accuracy buffer was applied, the cap value (in meters)
    # used to widen the gate. NULL on every non-buffered acceptance,
    # so the row tells you both `accepted_by` AND the slack used.
    accepted_buffer_m = Column(Numeric(10, 2))
    # Slice C: TRUE when the request came from a trusted shop IP,
    # regardless of `accepted_by`. During the log-only window the GPS
    # path still gates acceptance — this flag just records evidence so
    # the owner can validate the IP list before flipping
    # `business_profile.trusted_network_enabled` to TRUE.
    trusted_network_detected = Column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffPunchAuditEvent(Base):
    """Append-only before/after audit row for any system or human
    change to a punch. Punch rows carry current state; this table
    explains how they got there."""

    __tablename__ = "staff_punch_audit_events"

    id = Column(BigInteger, primary_key=True)
    punch_id = Column(
        BigInteger, ForeignKey("staff_punches.id", ondelete="SET NULL")
    )
    actor_kind = Column(String(20), nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    action = Column(String(40), nullable=False)
    reason_code = Column(String(60))
    old_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    new_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffPunchCorrectionRequest(Base):
    """Stylist-submitted "I forgot to clock out, I actually left at X"
    request. Owner approves/denies via the attendance review queue
    that lands in Slice 2."""

    __tablename__ = "staff_punch_correction_requests"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    punch_id = Column(
        BigInteger, ForeignKey("staff_punches.id", ondelete="SET NULL")
    )
    requested_check_in_at = Column(DateTime(timezone=True))
    requested_check_out_at = Column(DateTime(timezone=True))
    reason = Column(Text, nullable=False)
    status = Column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    decided_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at = Column(DateTime(timezone=True))
    decision_notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class AttendancePreCloseReminder(Base):
    """Idempotency record for the pre-close reminder cron. UNIQUE on
    `(punch_id, cutoff_business_date)` so two cron ticks against the
    same shift cutoff cannot fire two emails."""

    __tablename__ = "attendance_pre_close_reminders"

    id = Column(BigInteger, primary_key=True)
    punch_id = Column(
        BigInteger,
        ForeignKey("staff_punches.id", ondelete="CASCADE"),
        nullable=False,
    )
    cutoff_business_date = Column(Date, nullable=False)
    sent_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


# ---------------------------------------------------------------------------
# Phase 8 of the Sales Portal — schedule + time-off + holiday calendar.
# Migration 059 lays down five tables and adds two FKs on staff_punches.
# ---------------------------------------------------------------------------


class StaffShift(Base):
    """Weekly shift template per stylist.

    `starts_at` and `ends_at` are TIMESTAMPTZ anchors; the time-of-day
    component (in the boutique's local timezone) is what repeats on
    each ISO weekday in `working_days`. Phase 8 Slice B's resolver
    carries `duration = ends_at - starts_at` and expands the template
    onto each working day in the requested range, so an overnight shift
    (the duration crosses midnight) cleanly produces a `(Sat 18:00,
    Sun 00:00)` end without the time-of-day having to wrap.

    Field semantics (locked in Phase 7 doc):

      - `late_grace_period_minutes` (0-120): late = punched_at >
        starts_at + grace.
      - `earliest_check_in_minutes` (0-720): clock-in rejected before
        starts_at - earliest. Phase 8 Slice B wires the rejection.
      - `early_out_grace_minutes` (0-120): early-out flag if
        punch-out < ends_at - grace.
      - `auto_session_close_time`: drives the auto-close cron's cutoff
        when a shift exists; falls back to the location default
        otherwise. Phase 8 Slice B wires the precedence.
      - `max_session_hours` (1-24): runaway-session guard.
      - `working_days`: ISO weekday list (1=Mon, 7=Sun) the shift
        repeats on. The CHECK constraints enforce length ≤ 7 and
        elements ⊆ {1..7}.
    """

    __tablename__ = "staff_shifts"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id = Column(
        Integer, ForeignKey("staff_locations.id", ondelete="SET NULL")
    )
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    late_grace_period_minutes = Column(
        Integer, nullable=False, server_default=text("0")
    )
    earliest_check_in_minutes = Column(
        Integer, nullable=False, server_default=text("120")
    )
    early_out_grace_minutes = Column(
        Integer, nullable=False, server_default=text("0")
    )
    auto_session_close_time = Column(Time)
    max_session_hours = Column(Numeric(5, 2))
    working_days = Column(
        ARRAY(Integer),
        nullable=False,
        server_default=text("ARRAY[1, 2, 3, 4, 5, 6]"),
    )
    notes = Column(Text)
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffShiftOverride(Base):
    """Temporary per-stylist override that wins over the assigned
    shift for a date range. The resolver checks this first (highest
    priority) before falling back to the base shift, then to the
    location/default policy."""

    __tablename__ = "staff_shift_overrides"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    shift_id = Column(
        BigInteger,
        ForeignKey("staff_shifts.id", ondelete="CASCADE"),
        nullable=False,
    )
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    reason = Column(Text)
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffHoliday(Base):
    """Advisory holiday calendar.

    `UNIQUE NULLS NOT DISTINCT (holiday_date, location_id, name)`
    means two "global" (location_id IS NULL) entries with the same
    date + name actually collide instead of slipping past Postgres's
    default distinct-NULL semantics. The migration probes this case
    explicitly per the user's Phase 8 guardrail.

    Holidays are advisory: a punch on a holiday gets `holiday_id`
    stamped (so reporting can multiply the rate later) but the punch
    is never blocked because of one.
    """

    __tablename__ = "staff_holidays"

    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    holiday_date = Column(Date, nullable=False)
    location_id = Column(
        Integer, ForeignKey("staff_locations.id", ondelete="CASCADE")
    )
    is_paid = Column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    multiplier = Column(Numeric(5, 2))
    notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class TimeOffRequest(Base):
    """Stylist-submitted time-off request.

    The latest decision lives on the row (`status`, `decided_by_user_id`,
    `decided_at`, `decision_notes`) for fast reads. The full timeline
    of requested → amended → approved → ... lives in
    `TimeOffDecisionEvent` rows. Phase 8 Slice C's `decide` endpoint
    refuses re-decision on a terminal status (409 per the doc).
    """

    __tablename__ = "time_off_requests"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(Text)
    status = Column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    decided_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at = Column(DateTime(timezone=True))
    decision_notes = Column(Text)
    manager_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class TimeOffDecisionEvent(Base):
    """Append-only audit row for time-off requests.

    Mirrors `StaffPunchAuditEvent` so the timeline reads consistently
    across attendance and time-off surfaces. The action vocabulary is
    locked at the schema level: `requested`, `approved`, `denied`,
    `cancelled`, `amended`. A future state needs a migration, not a
    code-only change.
    """

    __tablename__ = "time_off_decision_events"

    id = Column(BigInteger, primary_key=True)
    request_id = Column(
        BigInteger,
        ForeignKey("time_off_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_kind = Column(String(20), nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    action = Column(String(20), nullable=False)
    old_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    new_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffShiftRequest(Base):
    """Staff-initiated shift request (Scheduling Phase 1).

    One row per cover/swap/drop/pickup request. The latest state lives
    on the row (`status`, `accepted_*`, `decided_*`); the full timeline
    lives in `StaffShiftRequestEvent`. Phase 1 only creates and cancels
    these records — no schedule mutation happens from a request until
    Phase 2+. A per-type CHECK (migration 081) keeps the entry shape
    honest: cover/drop carry a source only, swap carries source+target,
    pickup carries neither.

    `open_shift_post_id` is reserved for pickup claims; its FK lands in
    Phase 3 with the `open_shift_posts` table.
    """

    __tablename__ = "staff_shift_requests"

    id = Column(BigInteger, primary_key=True)
    request_type = Column(String(16), nullable=False)
    status = Column(
        String(24), nullable=False, server_default=text("'pending'")
    )
    source_entry_id = Column(
        BigInteger,
        ForeignKey("staff_schedule_entries.id", ondelete="CASCADE"),
    )
    target_entry_id = Column(
        BigInteger,
        ForeignKey("staff_schedule_entries.id", ondelete="CASCADE"),
    )
    open_shift_post_id = Column(BigInteger)
    requester_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at = Column(DateTime(timezone=True))
    decided_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at = Column(DateTime(timezone=True))
    reason = Column(Text)
    decision_notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class OpenShiftPost(Base):
    """Manager-posted open shift staff can claim (Scheduling Phase 3).

    Open shifts are intentionally NOT stored as
    `staff_schedule_entries.user_id = NULL`; they live here until a
    pickup is approved, at which point a normal published entry is
    created for the claimant and the post closes as `claimed`.
    """

    __tablename__ = "open_shift_posts"

    id = Column(BigInteger, primary_key=True)
    business_date = Column(Date, nullable=False)
    starts_at_local = Column(DateTime(timezone=True), nullable=False)
    ends_at_local = Column(DateTime(timezone=True), nullable=False)
    late_grace_minutes = Column(
        Integer, nullable=False, server_default=text("30")
    )
    source = Column(
        String(16), nullable=False, server_default=text("'manual'")
    )
    manager_notes = Column(Text)
    status = Column(
        String(16), nullable=False, server_default=text("'open'")
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_request_id = Column(
        BigInteger,
        ForeignKey("staff_shift_requests.id", ondelete="SET NULL"),
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffShiftRequestEvent(Base):
    """Append-only audit row for staff shift requests (Phase 1).

    Mirrors `TimeOffDecisionEvent`. The action vocabulary is locked at
    the schema level (migration 081): `requested`, `accepted`,
    `approved`, `denied`, `cancelled`, `expired`, `amended`. Protected
    by the shared `enforce_audit_append_only()` trigger.
    """

    __tablename__ = "staff_shift_request_events"

    id = Column(BigInteger, primary_key=True)
    request_id = Column(
        BigInteger,
        ForeignKey("staff_shift_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_kind = Column(String(20), nullable=False)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    action = Column(String(20), nullable=False)
    old_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    new_values = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    notes = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class RecurringUnavailability(Base):
    """Stylist-set standing rule "I am unavailable weekday X from
    HH:MM to HH:MM" (migration 072 — Epic 3.4).

    Distinct from `TimeOffRequest` (one-off date range, needs admin
    approval) and from `StaffShift.working_days` (manager-set
    template of WHEN the stylist IS working). The stylist owns
    their own rows and can add or delete without admin involvement;
    the admin sees them on the weekly grid the same way they see
    approved time-off, and the publish path treats a published
    shift overlapping an active rule as a per-shift skip.

    `weekday` is ISO weekday 1-7 (Mon=1, Sun=7). `start_time_local`
    / `end_time_local` are boutique-local wall-clock TIME values,
    same-day only (`end > start` is enforced by CHECK in 072).

    `effective_until IS NULL` means the rule is open-ended;
    setting a date makes it stop applying after that date,
    inclusive. No `deleted_at` — removing a rule is a hard DELETE
    (no audit need surfaced yet; revisit if one does).
    """

    __tablename__ = "recurring_unavailability"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday = Column(SmallInteger, nullable=False)
    start_time_local = Column(Time, nullable=False)
    end_time_local = Column(Time, nullable=False)
    effective_from = Column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    effective_until = Column(Date, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


# ---------------------------------------------------------------------------
# Phase 10 of the Sales Portal — per-day published schedule entries.
# Migration 068 lays down `staff_schedule_entries`, which the manager's
# weekly grid writes to and the resolver consults ahead of overrides and
# templates (precedence: published entry > override > base template).
# ---------------------------------------------------------------------------


class StaffScheduleEntry(Base):
    """Concrete per-day shift instance the manager publishes through
    the weekly grid UI.

    Where `StaffShift` is a recurring template and `StaffShiftOverride`
    is a date-range exception pointing back to a template, this table
    holds materialized rows for specific (user, business_date) pairs.
    Published rows win over overrides and templates in the resolver.

    Lifecycle:

      - `status='draft'` — the manager is composing the week, not yet
        visible to staff. `published_at` must be NULL (CHECK enforced).
      - `status='published'` — visible to staff, authoritative for the
        resolver. `published_at` must be set (CHECK enforced).

    `attendance_status` lives on this row only. Slice 1 ships it as
    'scheduled' for every new row; Slice 2 wires the clock-in path to
    flip it to 'present'/'late' and a cron to flip stale rows to
    'no_show'. We are intentionally NOT mutating `StaffPunch.status`
    semantics — punches keep their late/early_out/unscheduled
    vocabulary; the schedule layer tracks "did this scheduled shift
    happen" on its own row.

    `late_grace_minutes` is copied onto the row at create/publish time
    (from the source template's `late_grace_period_minutes` for
    `template_clone` entries, defaulting to 30 for manual entries).
    Slice 2's no-show cron reads this directly so it doesn't have to
    walk back to a template whose grace value may have drifted.
    """

    __tablename__ = "staff_schedule_entries"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    business_date = Column(Date, nullable=False)
    starts_at_local = Column(DateTime(timezone=True), nullable=False)
    ends_at_local = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        String(16), nullable=False, server_default=text("'draft'")
    )
    attendance_status = Column(
        String(24), nullable=False, server_default=text("'scheduled'")
    )
    late_grace_minutes = Column(
        Integer, nullable=False, server_default=text("30")
    )
    source = Column(
        String(16), nullable=False, server_default=text("'manual'")
    )
    source_shift_id = Column(
        BigInteger, ForeignKey("staff_shifts.id", ondelete="SET NULL")
    )
    manager_notes = Column(Text)
    actual_clock_in_punch_id = Column(
        BigInteger, ForeignKey("staff_punches.id", ondelete="SET NULL")
    )
    actual_clock_out_punch_id = Column(
        BigInteger, ForeignKey("staff_punches.id", ondelete="SET NULL")
    )
    published_at = Column(DateTime(timezone=True))
    published_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class StaffSchedulePreset(Base):
    """Admin-configurable shift preset for the weekly grid's "Preset"
    dropdown.

    A preset is a time-of-day pair (`start_time`, `end_time`) plus
    grace + sort + active flag. The grid combines the picked preset
    with the cell's business date to build a concrete
    `staff_schedule_entries` row — the preset itself never carries a
    timezone. That avoids a DST trap where a "9am-5pm" stored as
    TIMESTAMPTZ silently rolls past a fall-back boundary.

    `active=FALSE` is soft-delete. A partial unique index on
    `(label) WHERE active = TRUE` (migration 069) lets an archived
    preset's label be re-used by a new active row.
    """

    __tablename__ = "staff_schedule_presets"

    id = Column(BigInteger, primary_key=True)
    label = Column(String(80), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    late_grace_minutes = Column(
        Integer, nullable=False, server_default=text("30")
    )
    sort_order = Column(
        Integer, nullable=False, server_default=text("100")
    )
    active = Column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
