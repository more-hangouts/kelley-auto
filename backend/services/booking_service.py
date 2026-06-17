"""Booking domain logic: availability, normalization, confirmation codes.

Kept separate from the FastAPI router so the slot algorithm and the helpers
can be unit-tested without spinning up the HTTP layer.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    Appointment,
    AppointmentAvailabilityRule,
    AppointmentBlackout,
    AppointmentEnrichmentResponse,
    BookingWidgetThemeSettings,
)
from services.booking_contracts import BoutiqueExperienceSubmission

# Confirmation code alphabet — no 0/O/I/1 to avoid customer transcription errors.
# 31 chars (Crockford-ish without 0/1/I/L/O); log2(31) ≈ 4.954 bits per char.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# Body length 20 → log2(31^20) ≈ 99.1 bits.
# Phase D1 of SECURITY_REMEDIATION_PLAN.md — raised from 6 chars (~30 bits)
# to close the brute-force gap that B3's per-email rate limit had been
# carrying alone. With B3 still in place, entropy + limiter are a layered
# defense: even if Redis fails-open during an incident, the code space
# stays out of practical reach.
_CODE_LENGTH = 20
_CODE_PREFIX = "BX"  # No hyphen in stored canonical form; display layer adds it.
_DISPLAY_GROUP_SIZE = 5  # `BX-XXXXX-XXXXX-XXXXX-XXXXX` for human reading.

_LIVE_STATUSES = ("pending", "confirmed")


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------


def shop_tz() -> ZoneInfo:
    return ZoneInfo(APP_TIMEZONE)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=shop_tz())
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Phone normalization (US-first; international callers pass through raw)
# ---------------------------------------------------------------------------


def normalize_phone_e164(raw: str) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.lstrip().startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    return None


# ---------------------------------------------------------------------------
# Confirmation codes
# ---------------------------------------------------------------------------


def _generate_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{_CODE_PREFIX}{body}"


def normalize_confirmation_code(raw: str | None) -> str:
    """Canonicalize a confirmation code for storage and lookup.

    Strips every non-alphanumeric character and uppercases. This makes
    `BX-ABCDE-FGHJK-MNPQR-STUVW`, `bx abcde fghjk mnpqr stuvw`, and
    `bxabcdefghjkmnpqrstuvw` all match a single canonical stored form.
    Also handles legacy `BX-ABCDEF` codes from before D1 (their hyphen
    is stripped to `BXABCDEF`, matching the post-D1 canonical column).

    Returns an empty string for None or whitespace-only input — the
    caller should treat that the same as a not-found lookup.
    """
    if not raw:
        return ""
    return _NON_ALPHANUMERIC_RE.sub("", str(raw)).upper()


def format_confirmation_code(stored: str | None) -> str:
    """Render a stored canonical code for human display.

    For D1-era bodies (≥ 2 groups' worth of characters) inserts hyphens
    every `_DISPLAY_GROUP_SIZE` chars: `BX-ABCDE-FGHJK-MNPQR-STUVW`.
    For legacy short bodies (≤ one full group, e.g. backfilled
    pre-D1 codes) the body is rendered as a single segment so the
    display matches what the original customer email showed:
    `BXABCDEF` → `BX-ABCDEF`, not `BX-ABCDE-F`.
    Storage stays hyphen-free.
    """
    if not stored:
        return ""
    canon = normalize_confirmation_code(stored)
    if not canon.startswith(_CODE_PREFIX):
        return stored  # Unrecognized shape — return as-is rather than mangle.
    body = canon[len(_CODE_PREFIX):]
    if not body:
        return canon
    if len(body) <= _DISPLAY_GROUP_SIZE + 2:
        # Pre-D1 codes (6-7 char body). Single-group display is friendlier
        # than `BX-ABCDE-F` for the trailing-one-char case.
        return f"{_CODE_PREFIX}-{body}"
    groups = [
        body[i : i + _DISPLAY_GROUP_SIZE]
        for i in range(0, len(body), _DISPLAY_GROUP_SIZE)
    ]
    return f"{_CODE_PREFIX}-{'-'.join(groups)}"


def generate_unique_confirmation_code(db: Session, *, max_attempts: int = 8) -> str:
    """Generate a canonical confirmation code that does not already exist.

    At 99 bits of entropy the retry loop is essentially never going to
    fire, but it stays as defense-in-depth against a regression in the
    random source.
    """
    for _ in range(max_attempts):
        code = _generate_code()
        exists = db.execute(
            select(Appointment.id).where(Appointment.confirmation_code == code)
        ).first()
        if exists is None:
            return code
    raise RuntimeError("could not generate unique confirmation code")


_NON_ALPHANUMERIC_RE = re.compile(r"[^A-Za-z0-9]+")


# ---------------------------------------------------------------------------
# Theme accessor
# ---------------------------------------------------------------------------


def get_theme_settings(db: Session) -> BookingWidgetThemeSettings:
    settings = db.query(BookingWidgetThemeSettings).first()
    if settings is None:
        # Schema migration 011 inserts the singleton; defending against a
        # truncated table being more annoying than the explicit error.
        raise RuntimeError("booking_widget_theme_settings singleton missing")
    return settings


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotCandidate:
    start: datetime  # tz-aware in shop timezone
    end: datetime
    capacity: int
    duration_minutes: int


def _generate_rule_slots(
    rule: AppointmentAvailabilityRule, on_date: date, tz: ZoneInfo
) -> list[SlotCandidate]:
    duration = timedelta(minutes=rule.slot_duration_minutes)
    cursor = datetime.combine(on_date, rule.start_time, tzinfo=tz)
    closing = datetime.combine(on_date, rule.end_time, tzinfo=tz)
    out: list[SlotCandidate] = []
    while cursor + duration <= closing:
        out.append(
            SlotCandidate(
                start=cursor,
                end=cursor + duration,
                capacity=rule.capacity,
                duration_minutes=rule.slot_duration_minutes,
            )
        )
        cursor += duration
    return out


def _rules_for_date(
    rules: Iterable[AppointmentAvailabilityRule], on_date: date
) -> list[AppointmentAvailabilityRule]:
    weekday = on_date.weekday()
    out: list[AppointmentAvailabilityRule] = []
    for rule in rules:
        if not rule.active or rule.weekday != weekday:
            continue
        if rule.effective_from and on_date < rule.effective_from:
            continue
        if rule.effective_to and on_date > rule.effective_to:
            continue
        out.append(rule)
    return out


def _booked_count(
    appointments: list[Appointment], slot_start: datetime, slot_end: datetime
) -> int:
    n = 0
    for appt in appointments:
        if appt.slot_start_at < slot_end and appt.slot_end_at > slot_start:
            n += 1
    return n


def _in_blackout(
    blackouts: list[AppointmentBlackout], slot_start: datetime, slot_end: datetime
) -> bool:
    for b in blackouts:
        if b.start_at < slot_end and b.end_at > slot_start:
            return True
    return False


def compute_availability(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    now: datetime | None = None,
    min_lead_minutes: int = 0,
) -> list[dict]:
    """Generate available slots in the [from_date, to_date] inclusive range.

    Slots come from active rules, minus blackouts, minus existing live
    appointments (per-slot capacity), minus anything starting before
    ``now + min_lead_minutes``.
    """
    if to_date < from_date:
        return []

    tz = shop_tz()
    now = now or datetime.now(timezone.utc)
    earliest_start = now + timedelta(minutes=min_lead_minutes)

    range_start = datetime.combine(from_date, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    range_end = datetime.combine(
        to_date + timedelta(days=1), time(0, 0), tzinfo=tz
    ).astimezone(timezone.utc)

    rules = db.query(AppointmentAvailabilityRule).filter(
        AppointmentAvailabilityRule.active.is_(True)
    ).all()
    blackouts = (
        db.query(AppointmentBlackout)
        .filter(
            AppointmentBlackout.end_at > range_start,
            AppointmentBlackout.start_at < range_end,
        )
        .all()
    )
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.status.in_(_LIVE_STATUSES),
            Appointment.slot_end_at > range_start,
            Appointment.slot_start_at < range_end,
        )
        .all()
    )

    days: list[dict] = []
    cursor_date = from_date
    while cursor_date <= to_date:
        day_rules = _rules_for_date(rules, cursor_date)
        slots: list[dict] = []
        for rule in day_rules:
            for cand in _generate_rule_slots(rule, cursor_date, tz):
                if cand.start < earliest_start:
                    continue
                if _in_blackout(blackouts, cand.start, cand.end):
                    continue
                booked = _booked_count(appointments, cand.start, cand.end)
                remaining = cand.capacity - booked
                if remaining <= 0:
                    continue
                slots.append(
                    {
                        "start": cand.start,
                        "end": cand.end,
                        "duration_minutes": cand.duration_minutes,
                        "remaining": remaining,
                    }
                )
        slots.sort(key=lambda s: (s["start"], s["duration_minutes"]))
        days.append(
            {"date": cursor_date, "weekday": cursor_date.weekday(), "slots": slots}
        )
        cursor_date += timedelta(days=1)

    return days


# ---------------------------------------------------------------------------
# Slot validation at booking time (server-side guard against tampering)
# ---------------------------------------------------------------------------


def slot_is_bookable(
    db: Session,
    *,
    slot_start: datetime,
    slot_duration_minutes: int,
    min_lead_minutes: int,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Re-validate a customer-claimed slot. Returns (ok, reason_if_not)."""
    if slot_start.tzinfo is None:
        return False, "slot_start must be timezone-aware"

    now = now or datetime.now(timezone.utc)
    if slot_start < now + timedelta(minutes=min_lead_minutes):
        return False, "slot is in the past or inside the lead-time window"

    slot_end = slot_start + timedelta(minutes=slot_duration_minutes)
    tz = shop_tz()
    on_date = slot_start.astimezone(tz).date()

    rules = (
        db.query(AppointmentAvailabilityRule)
        .filter(AppointmentAvailabilityRule.active.is_(True))
        .all()
    )
    matching_rule = None
    for rule in _rules_for_date(rules, on_date):
        if rule.slot_duration_minutes != slot_duration_minutes:
            continue
        for cand in _generate_rule_slots(rule, on_date, tz):
            if cand.start == slot_start.astimezone(tz):
                matching_rule = rule
                break
        if matching_rule:
            break
    if matching_rule is None:
        return False, "slot does not match any active availability rule"

    blackouts = (
        db.query(AppointmentBlackout)
        .filter(
            AppointmentBlackout.end_at > slot_start,
            AppointmentBlackout.start_at < slot_end,
        )
        .all()
    )
    if blackouts:
        return False, "slot is inside a blackout"

    booked = (
        db.query(Appointment)
        .filter(
            Appointment.status.in_(_LIVE_STATUSES),
            Appointment.slot_end_at > slot_start,
            Appointment.slot_start_at < slot_end,
        )
        .count()
    )
    if booked >= matching_rule.capacity:
        return False, "slot is full"

    return True, None


# ---------------------------------------------------------------------------
# Bot heuristics
# ---------------------------------------------------------------------------


def looks_like_bot(*, time_on_widget_ms: int | None, interaction_count: int | None,
                   steps_completed: int | None, user_agent: str | None) -> bool:
    """Weak-signal bot detection. Honeypot is checked separately at the router."""
    if not user_agent:
        return True
    if (time_on_widget_ms or 0) < 2000:
        return True
    if (interaction_count or 0) < 3:
        return True
    if (steps_completed or 0) < 2:
        return True
    return False


def hash_ip(raw_ip: str | None) -> str | None:
    if not raw_ip:
        return None
    return hashlib.sha256(raw_ip.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Boutique Experience profile
# ---------------------------------------------------------------------------


def _parse_visitor_uuid(raw: str | None) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, TypeError):
        return None


def _apply_profile_payload(
    profile: AppointmentEnrichmentResponse,
    payload: BoutiqueExperienceSubmission,
    *,
    source: str,
) -> None:
    """Copy a Boutique Experience submission onto an existing profile row.

    Stamps `submitted_at`, sets `source` if not already set (the original
    source wins on upsert so we can distinguish "first arrived via email"
    from a later edit), and stashes the request payload so we can recover
    if a column is added later.
    """
    m = payload.measurements
    s = payload.sizing
    p = payload.preferences

    profile.bust_inches = m.bust_inches
    profile.waist_inches = m.waist_inches
    profile.hips_inches = m.hips_inches
    profile.height_ft = m.height_ft
    profile.height_in = m.height_in

    profile.estimated_size_low = s.estimated_size_low
    profile.estimated_size_high = s.estimated_size_high
    profile.size_by_bust = s.size_by_bust
    profile.size_by_waist = s.size_by_waist
    profile.size_by_hips = s.size_by_hips
    profile.chart_source = s.chart_source
    profile.off_chart = s.off_chart

    profile.style_preference = p.style
    profile.back_preference = p.back
    profile.budget_preference = p.budget
    profile.color_preferences_text = p.colors
    profile.likes = p.likes
    profile.avoids = p.avoids

    profile.summary = payload.summary
    profile.session_id = payload.session_id or profile.session_id
    visitor_uuid = _parse_visitor_uuid(payload.visitor_id)
    if visitor_uuid is not None:
        profile.visitor_id = visitor_uuid

    if profile.source is None:
        profile.source = source
    profile.submitted_at = datetime.now(timezone.utc)
    profile.raw_payload = payload.model_dump(mode="json")


def create_pre_booking_profile(
    db: Session, *, payload: BoutiqueExperienceSubmission
) -> AppointmentEnrichmentResponse:
    """Insert an unlinked profile for the calculator-first path."""
    profile = AppointmentEnrichmentResponse(appointment_id=None)
    _apply_profile_payload(profile, payload, source="pre_booking")
    db.add(profile)
    db.flush()
    return profile


def upsert_profile_for_appointment(
    db: Session,
    *,
    appointment_id: int,
    payload: BoutiqueExperienceSubmission,
    source: str,
) -> AppointmentEnrichmentResponse:
    """Create-or-update the Boutique Experience profile for one appointment.

    Used by the email-token path. If a profile already exists for this
    appointment (e.g. promoted from a pre-booking row, or a re-submit), update
    it in place rather than rejecting the duplicate.
    """
    existing = (
        db.query(AppointmentEnrichmentResponse)
        .filter(AppointmentEnrichmentResponse.appointment_id == appointment_id)
        .first()
    )
    if existing is None:
        existing = AppointmentEnrichmentResponse(appointment_id=appointment_id)
        db.add(existing)
    _apply_profile_payload(existing, payload, source=source)
    db.flush()
    return existing


def link_profile_to_appointment(
    db: Session, *, profile_id: int, appointment_id: int
) -> bool:
    """Best-effort attach of a pre-booking profile to a freshly created appointment.

    Returns True on success. Returns False (and does not raise) if:
      - the profile id is unknown,
      - the profile is already linked to a different appointment,
      - another profile already exists for the target appointment.

    Callers should swallow False: the appointment is the source of truth, and
    a missed link is recoverable by support re-attaching after the fact.
    """
    profile = db.get(AppointmentEnrichmentResponse, profile_id)
    if profile is None:
        return False
    if profile.appointment_id == appointment_id:
        return True
    if profile.appointment_id is not None:
        return False
    other = (
        db.query(AppointmentEnrichmentResponse)
        .filter(AppointmentEnrichmentResponse.appointment_id == appointment_id)
        .first()
    )
    if other is not None:
        return False
    profile.appointment_id = appointment_id
    return True
