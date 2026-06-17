"""Clock-in service (Phase 7 Slice 1 of the Sales Portal).

Pure backend logic: geofence haversine, punch state machine, today's
punches read. No selfie writes — that's Slice 2. No punched-out gate
on existing sales endpoints — that's Slice 2 too. No shift resolution
— that's Phase 8.

Phase 7's design notes the doc locked in:

  - Geofence is server-side. Client-supplied coordinates are inputs,
    never authority. We compute haversine against every active
    `staff_locations` row and reject the punch when none cover the
    client's coords. The closest location's distance is echoed back
    so the UI can render "you're 230m too far north" instead of a
    generic 403.
  - Punch-out does NOT enforce the geofence. A stylist may already
    be walking out the door when they tap clock-out; blocking that is
    user-hostile. We still record the closest location and distance
    for the audit trail.
  - Without shift data, every punch lands as `status='unscheduled'`.
    Phase 8 fills `shift_id` and re-derives `status` (`recorded` /
    `late` / `early_out`).
  - "Currently punched in?" is a left-fold over the user's most
    recent non-void punch: `direction='in'` means in, anything else
    means out. We never trust a wall-clock interval.
"""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import StaffLocation, StaffPunch
from services import shift_resolver, staff_schedule
from services.business_time import business_date, business_now, shop_tz, to_business_local
from services.shift_resolver import ResolvedShift


def _coerce_ip(raw: str | None) -> str | None:
    """Return `raw` if it parses as an IPv4 or IPv6 address; else None.

    Postgres `INET` rejects anything that isn't a real address — and
    FastAPI's TestClient sets `request.client.host` to the literal
    string "testclient", so we cannot blindly persist whatever the
    framework hands us. The audit field is opportunistic; a missing
    IP is preferable to a 500 on punch-in.
    """
    if not raw:
        return None
    try:
        ipaddress.ip_address(raw)
    except (TypeError, ValueError):
        return None
    return raw


def is_ip_in_trusted_list(
    client_ip: str | None, trusted: list[str] | None
) -> bool:
    """Return True when `client_ip` matches any entry in `trusted`.

    Entries may be single IPs (`203.0.113.5`) or CIDR networks
    (`198.51.100.0/24`); both v4 and v6 are supported uniformly via
    the stdlib `ipaddress` module. Malformed entries are silently
    skipped so a typo in the owner's IP list cannot 500 a clock-in;
    it just fails to match. An empty list or a None/non-IP client
    address returns False.
    """
    if not client_ip or not trusted:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except (TypeError, ValueError):
        return False
    for entry in trusted:
        if not isinstance(entry, str) or not entry.strip():
            continue
        token = entry.strip()
        try:
            if "/" in token:
                net = ipaddress.ip_network(token, strict=False)
                if addr in net:
                    return True
            else:
                if addr == ipaddress.ip_address(token):
                    return True
        except (TypeError, ValueError):
            continue
    return False

# WGS-84 mean radius. Good to ~0.5% over short distances; the geofence
# tolerance (radius_m, bounded 25-1000) makes Earth-shape errors
# irrelevant at boutique scale.
EARTH_RADIUS_M = 6_371_000

# Slice-4 idempotency window for "shared iPad double-tap." A second
# same-direction punch from the same user within this many seconds
# returns the existing punch rather than inserting a new row. Short
# enough to never elide a legitimate clock-out-then-back-in, long
# enough to absorb retries from a flaky network, multi-finger taps,
# or a stylist double-clicking the button.
IDEMPOTENCY_WINDOW_SECONDS = 60


class ClockInError(Exception):
    """Stable error codes the router maps to HTTP statuses.

    `extra` is merged into the response detail object so the UI can
    render context (e.g. distance to closest location) without parsing
    free text.
    """

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


@dataclass(frozen=True)
class GeofenceMatch:
    location: StaffLocation
    # `None` only on a trusted-network punch that carried no GPS fix at
    # all — there is no client coordinate to measure a distance from.
    # Every GPS-derived match sets a real number.
    distance_m: float | None
    # Slice A: when >0, the gate accepted this punch only after widening
    # the radius by this many meters. The router records the value on
    # the punch row so the audit reader can tell a strict pass from a
    # buffer-assisted one without re-running the math.
    buffer_applied_m: float = 0.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters between two lat/lng points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _closest_active_location(
    db: Session, client_lat: float, client_lng: float
) -> tuple[StaffLocation | None, float | None]:
    """Return (location, distance_m) for the closest active location,
    even if the client is outside its radius. Used for both the
    geofence accept/reject decision and the audit metadata when the
    client is out of range."""
    rows = (
        db.execute(select(StaffLocation).where(StaffLocation.active.is_(True)))
        .scalars()
        .all()
    )
    closest: StaffLocation | None = None
    closest_distance: float | None = None
    for loc in rows:
        d = haversine_m(
            float(loc.latitude), float(loc.longitude), client_lat, client_lng
        )
        if closest_distance is None or d < closest_distance:
            closest = loc
            closest_distance = d
    return closest, closest_distance


def _first_active_location(db: Session) -> StaffLocation | None:
    """Return one active location for a trusted-network punch that
    carries no GPS fix. With a single boutique this is unambiguous.
    With several active locations the network match cannot disambiguate
    which one the staffer is standing in, so we attribute the punch to
    the lowest-id active location and lean on `accepted_by` /
    `trusted_network_detected` to mark it as network-, not GPS-,
    accepted."""
    return (
        db.execute(
            select(StaffLocation)
            .where(StaffLocation.active.is_(True))
            .order_by(StaffLocation.id)
        )
        .scalars()
        .first()
    )


def find_active_location_within_radius(
    db: Session,
    client_lat: float,
    client_lng: float,
    *,
    accuracy_buffer_m: float = 0.0,
) -> GeofenceMatch | None:
    """Return the closest `staff_location` whose `radius_m` covers
    (client_lat, client_lng). None if every active location is too
    far. Caller raises 403 with the closest distance for debugging.

    Slice A: `accuracy_buffer_m` widens the acceptance threshold by up
    to that many meters when the strict radius does not cover the
    client. The two-step check (strict first, then buffer) keeps the
    `accepted_by` audit value accurate — a punch dead-center on the
    boutique still records `'gps'`, not `'gps_with_accuracy_buffer'`.
    The caller is responsible for capping `accuracy_buffer_m` to the
    owner-configured max; this helper does no clamping.
    """
    closest, distance = _closest_active_location(db, client_lat, client_lng)
    if closest is None or distance is None:
        return None
    if distance <= closest.radius_m:
        return GeofenceMatch(
            location=closest, distance_m=distance, buffer_applied_m=0.0
        )
    if (
        accuracy_buffer_m > 0
        and distance <= closest.radius_m + accuracy_buffer_m
    ):
        return GeofenceMatch(
            location=closest,
            distance_m=distance,
            buffer_applied_m=float(accuracy_buffer_m),
        )
    return None


def _recent_same_direction_punch(
    db: Session,
    *,
    user_id: int,
    direction: str,
    now_utc: datetime,
    window_seconds: int = IDEMPOTENCY_WINDOW_SECONDS,
) -> StaffPunch | None:
    """Return the latest non-void punch for `user_id` when it is the
    same direction and falls inside the idempotency window, or None.

    The window is anchored at `now_utc` (passed in by the caller so
    test-mode `now_override` flows through cleanly). Voided punches
    are excluded so an admin who manually voided a duplicate doesn't
    inadvertently block the next legitimate tap inside the window.
    Opposite-direction punches break the debounce chain: a quick
    in→out→in sequence is a legitimate state transition, not a
    duplicate of the first tap.
    """
    cutoff = now_utc - timedelta(seconds=window_seconds)
    latest = (
        db.execute(
            select(StaffPunch)
            .where(StaffPunch.user_id == user_id)
            .where(StaffPunch.status != "void")
            .order_by(StaffPunch.punched_at.desc(), StaffPunch.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if latest is None:
        return None
    if latest.direction != direction:
        return None
    if latest.punched_at < cutoff:
        return None
    return latest


def current_status(
    db: Session, user_id: int
) -> tuple[str, StaffPunch | None]:
    """Return (`'in' | 'out'`, last_punch).

    The last non-void punch determines state. If the most recent punch
    is a `direction='in'` row, the user is in. Anything else (or no
    punches) is out.
    """
    last = (
        db.execute(
            select(StaffPunch)
            .where(StaffPunch.user_id == user_id)
            .where(StaffPunch.status != "void")
            .order_by(StaffPunch.punched_at.desc(), StaffPunch.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if last is None or last.direction == "out":
        return "out", last
    return "in", last


def today_punches(db: Session, user_id: int) -> list[StaffPunch]:
    """Return the user's punches whose `punched_at` falls inside the
    boutique's local "today". Used by GET /api/sales/clock/status so
    the UI can render the day's history right after a punch."""
    tz = shop_tz()
    today_local = business_date()
    start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    return list(
        db.execute(
            select(StaffPunch)
            .where(StaffPunch.user_id == user_id)
            .where(StaffPunch.status != "void")
            .where(StaffPunch.punched_at >= start_utc)
            .where(StaffPunch.punched_at < end_utc)
            .order_by(StaffPunch.punched_at, StaffPunch.id)
        )
        .scalars()
        .all()
    )


def _classify_in_status(
    *, now_local: datetime, resolved: ResolvedShift | None
) -> str:
    """Status to stamp on a `direction='in'` punch.

    Phase 7 left every punch as `'unscheduled'` because no shift data
    existed. Slice B re-derives:

      - No shift covers the punch time → `'unscheduled'` (legacy behavior).
      - Shift exists, `now <= shift.starts_at + late_grace_period` →
        `'recorded'`.
      - Shift exists, `now > shift.starts_at + late_grace_period` →
        `'late'`.

    The existing `idx_staff_punches_review_queue` partial index already
    covers the `'late'` and `'early_out'` statuses, so no DB change is
    needed for the new vocabulary to flow into the owner's review
    queue. Slice 2B-2's queue UI surfaces them automatically.
    """
    if resolved is None:
        return "unscheduled"
    grace = timedelta(minutes=resolved.late_grace_period_minutes)
    threshold = resolved.starts_at_local + grace
    if now_local > threshold:
        return "late"
    return "recorded"


def _classify_out_status(
    *,
    out_local: datetime,
    in_punch: StaffPunch,
    resolved: ResolvedShift | None,
) -> str:
    """Status to stamp on a `direction='out'` punch.

    `early_out` fires when the clock-out lands before
    `shift.ends_at - early_out_grace_minutes`. The shift used is the
    one that was active at the in-punch's business date — a stylist
    who pulled an overnight shift gets graded against yesterday's
    template, not today's.
    """
    if resolved is None:
        return "unscheduled"
    threshold = resolved.ends_at_local - timedelta(
        minutes=resolved.early_out_grace_minutes
    )
    if out_local < threshold:
        return "early_out"
    return "recorded"


def punch_in(
    db: Session,
    *,
    user,
    client_lat: float | None,
    client_lng: float | None,
    client_accuracy_m: float | None = None,
    accuracy_buffer_max_m: int = 0,
    trusted_network_match: bool = False,
    trusted_network_enabled: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
    now_override: datetime | None = None,
) -> StaffPunch:
    """Record a clock-in for `user`.

    Slice B added shift-aware behavior on top of Phase 7:

      - **Earliest-check-in window**: if a shift covers the punch's
        business date and `now < shift.starts_at -
        earliest_check_in_minutes`, raise `ClockInError(
        'too_early_for_shift', 403)` with the shift's `starts_at` and
        the `earliest_allowed_at` boundary in `extra`. Owner manual
        punches via the Slice C admin endpoint will bypass this.
      - **Status classification** on insert via `_classify_in_status`:
        `'recorded'` if inside the late grace, `'late'` past it,
        `'unscheduled'` when no shift exists.
      - **Holiday tagging** via `find_holiday_id`: a per-location
        holiday wins over a same-day global holiday. Holidays are
        advisory, never blocking.

    Raises ClockInError(`already_punched_in`, 409) if the user's last
    non-void punch is direction='in'. Raises ClockInError(
    `outside_geofence`, 403) with `distance_m` and `closest_location_name`
    when no active location's radius covers the client coords.
    """
    now_utc = (
        now_override.astimezone(timezone.utc)
        if now_override is not None
        else datetime.now(timezone.utc)
    )
    now_local = to_business_local(now_utc)
    biz_date = now_local.date()
    resolved = shift_resolver.resolve_active_shift(
        db, user_id=user.id, as_of_local=now_local
    )

    if resolved is not None:
        earliest_allowed_local = resolved.starts_at_local - timedelta(
            minutes=resolved.earliest_check_in_minutes
        )
        if now_local < earliest_allowed_local:
            raise ClockInError(
                "too_early_for_shift",
                http_status=403,
                extra={
                    "shift_starts_at": resolved.starts_at_local.isoformat(),
                    "earliest_allowed_at": earliest_allowed_local.isoformat(),
                },
            )

    # WiFi fast-path: a staffer on the boutique network may clock in
    # with no GPS fix at all — that's the whole point of the trusted
    # network, so the girls aren't held hostage to a 30s lock. When
    # coords ARE present we still run the full geofence; when they're
    # absent the trusted-network match is the only way through.
    has_coords = client_lat is not None and client_lng is not None

    # Slice A: the effective buffer is whichever is smaller — what the
    # phone reported, or what the owner is willing to grant. A phone
    # claiming ±500m can not single-handedly widen the gate to 500m.
    # A 0/None accuracy reading falls through as a strict check.
    effective_buffer = 0.0
    if (
        has_coords
        and client_accuracy_m is not None
        and accuracy_buffer_max_m > 0
    ):
        effective_buffer = min(
            float(client_accuracy_m), float(accuracy_buffer_max_m)
        )

    # Slice C: trusted-network fallback. The toggle is the gate — if the
    # owner has not flipped `trusted_network_enabled` yet, a matching
    # request IP only flows into the audit flag, not into acceptance.
    # When the toggle is on, the network match grants a pass IF GPS
    # rejected OR was never captured; an already-passing GPS stays
    # `'gps'` and only picks up `trusted_network_detected=TRUE`.
    if has_coords:
        match = find_active_location_within_radius(
            db, client_lat, client_lng, accuracy_buffer_m=effective_buffer
        )
        closest, closest_distance = _closest_active_location(
            db, client_lat, client_lng
        )
    else:
        match = None
        closest, closest_distance = None, None

    if match is None:
        if trusted_network_match and trusted_network_enabled:
            # Attribute against the closest active location when a GPS
            # fix exists to measure from; otherwise fall back to any
            # active location so the row keeps a usable `location_id`
            # (and the punch_out / shift-resolution flows downstream do
            # not need a special null-location branch). When no active
            # location exists at all, the trusted network cannot rescue
            # a punch with nowhere to attribute it.
            loc = closest if closest is not None else _first_active_location(db)
            if loc is None:
                raise ClockInError(
                    "outside_geofence",
                    http_status=403,
                    extra={
                        "distance_m": None,
                        "closest_location_name": None,
                        "closest_location_radius_m": None,
                        "accuracy_buffer_m": None,
                    },
                )
            match = GeofenceMatch(
                location=loc,
                distance_m=closest_distance,
                buffer_applied_m=0.0,
            )
            accepted_by = "trusted_network"
        else:
            raise ClockInError(
                "outside_geofence",
                http_status=403,
                extra={
                    "distance_m": (
                        round(closest_distance, 1)
                        if closest_distance is not None
                        else None
                    ),
                    "closest_location_name": (
                        closest.name if closest else None
                    ),
                    "closest_location_radius_m": (
                        closest.radius_m if closest else None
                    ),
                    # Surfacing the buffer in the rejection payload lets
                    # the frontend say "we already widened the gate by
                    # X m; the closest boutique is still Y m past that"
                    # instead of a generic "outside" message.
                    "accuracy_buffer_m": (
                        round(effective_buffer, 1)
                        if effective_buffer > 0
                        else None
                    ),
                },
            )
    else:
        accepted_by = (
            "gps_with_accuracy_buffer" if match.buffer_applied_m > 0 else "gps"
        )

    # Slice-4 idempotency guard: a same-direction non-void punch
    # inside the IDEMPOTENCY_WINDOW means an iPad double-tap or a
    # network-retried request. Return the existing punch instead of
    # inserting a duplicate. Runs AFTER the geofence + earliest-
    # check-in checks so a tap-from-far-away still 403s cleanly —
    # debounce only short-circuits when everything else would have
    # passed too. Runs BEFORE the state-machine guard so the second
    # tap doesn't hit `already_punched_in`.
    dup = _recent_same_direction_punch(
        db, user_id=user.id, direction="in", now_utc=now_utc
    )
    if dup is not None:
        return dup

    state, _last = current_status(db, user.id)
    if state == "in":
        raise ClockInError("already_punched_in", http_status=409)

    holiday_id = shift_resolver.find_holiday_id(
        db, biz_date=biz_date, location_id=match.location.id
    )

    punch = StaffPunch(
        user_id=user.id,
        direction="in",
        # Stamp `punched_at` from `now_utc` (so test overrides land on
        # the row) when one is provided; otherwise let the DB default
        # `NOW()` fire so production stays exact-same as before Slice B.
        punched_at=now_utc if now_override is not None else None,
        status=_classify_in_status(now_local=now_local, resolved=resolved),
        location_id=match.location.id,
        shift_id=resolved.shift_id if resolved else None,
        holiday_id=holiday_id,
        client_latitude=client_lat,
        client_longitude=client_lng,
        client_accuracy_m=client_accuracy_m,
        distance_to_location_m=match.distance_m,
        ip=_coerce_ip(ip),
        user_agent=(user_agent or "")[:255] or None,
        accepted_by=accepted_by,
        accepted_buffer_m=(
            match.buffer_applied_m if match.buffer_applied_m > 0 else None
        ),
        # Log-only audit: a punch coming from the boutique Wi-Fi gets
        # this flag set even on a GPS-accepted pass, so the owner can
        # validate the trusted IP list before flipping the bypass on.
        trusted_network_detected=bool(trusted_network_match),
    )
    db.add(punch)
    db.flush()

    # Phase 10 Slice 2: if this punch resolved against a published
    # schedule entry, stamp the entry's actual_clock_in_punch_id and
    # flip its attendance_status. The stamp helper is defensive — it
    # returns None on missing/stale entries rather than raising, so a
    # schedule-layer hiccup never breaks the clock-in path.
    if resolved is not None and resolved.schedule_entry_id is not None:
        staff_schedule.stamp_clock_in(
            db,
            schedule_entry_id=resolved.schedule_entry_id,
            punch_id=punch.id,
            punched_at_local=now_local,
        )

    return punch


def punch_out(
    db: Session,
    *,
    user,
    client_lat: float | None,
    client_lng: float | None,
    client_accuracy_m: float | None = None,
    trusted_network_match: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
    now_override: datetime | None = None,
) -> StaffPunch:
    """Record a clock-out for `user`.

    Slice B re-derives status from the in-punch's resolved shift:
    `'early_out'` when `out < ends_at - early_out_grace_minutes`,
    `'recorded'` otherwise, `'unscheduled'` when no shift covered the
    session. The shift is resolved against the **in-punch's** business
    date so an overnight session is graded against the right
    template (the user pulled a Saturday-night shift, not Sunday's
    nothing).

    Raises ClockInError(`not_punched_in`, 409) if the user's last
    non-void punch is anything other than direction='in'. Punch-out
    deliberately does NOT enforce the geofence — a stylist may
    already be walking out — but we still attribute the closest
    active location and record the distance for the audit trail.
    """
    now_utc = (
        now_override.astimezone(timezone.utc)
        if now_override is not None
        else datetime.now(timezone.utc)
    )
    now_local = to_business_local(now_utc)
    # Punch-out never enforces the geofence, so a missing GPS fix (the
    # WiFi fast-path) just means no closest-location attribution — the
    # row still records direction, time, and the trusted-network flag.
    if client_lat is not None and client_lng is not None:
        closest, distance = _closest_active_location(db, client_lat, client_lng)
    else:
        closest, distance = None, None

    # Slice-4 idempotency guard, same shape as punch_in. Closest-
    # location attribution above is non-blocking (punch_out never
    # rejects on geofence) so we can run it pre-debounce without
    # changing observable behavior. State-machine check stays AFTER
    # the debounce so a rapid second tap returns the existing out
    # rather than hitting `not_punched_in`.
    dup = _recent_same_direction_punch(
        db, user_id=user.id, direction="out", now_utc=now_utc
    )
    if dup is not None:
        return dup

    state, last_in = current_status(db, user.id)
    if state == "out":
        raise ClockInError("not_punched_in", http_status=409)

    # Resolve against the IN punch's business date so an overnight
    # session is classified against the shift it actually started in.
    resolved = None
    if last_in is not None:
        in_local = to_business_local(last_in.punched_at)
        resolved = shift_resolver.resolve_active_shift(
            db, user_id=user.id, as_of_local=in_local
        )

    holiday_id = (
        shift_resolver.find_holiday_id(
            db,
            biz_date=now_local.date(),
            location_id=closest.id if closest else None,
        )
        if closest is not None
        else None
    )

    punch = StaffPunch(
        user_id=user.id,
        direction="out",
        punched_at=now_utc if now_override is not None else None,
        status=_classify_out_status(
            out_local=now_local, in_punch=last_in, resolved=resolved
        ),
        location_id=closest.id if closest else None,
        shift_id=resolved.shift_id if resolved else None,
        holiday_id=holiday_id,
        client_latitude=client_lat,
        client_longitude=client_lng,
        client_accuracy_m=client_accuracy_m,
        distance_to_location_m=distance,
        ip=_coerce_ip(ip),
        user_agent=(user_agent or "")[:255] or None,
        trusted_network_detected=bool(trusted_network_match),
    )
    db.add(punch)
    db.flush()

    # Phase 10 Slice 2: stamp the schedule entry's actual_clock_out_punch_id
    # by joining through the in-punch we already located. Defensive: a
    # missing entry (manual punch with no schedule row, drift, etc.)
    # is a silent no-op.
    if last_in is not None:
        staff_schedule.stamp_clock_out(
            db, in_punch_id=last_in.id, out_punch_id=punch.id
        )

    return punch
