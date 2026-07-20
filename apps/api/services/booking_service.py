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


from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    Appointment,
    AppointmentAvailabilityRule,
    AppointmentBlackout,
    BookingWidgetThemeSettings,
)

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


# normalize_phone_e164 is a core primitive (services/phone.py); re-exported
# here so booking_service.normalize_phone_e164 stays a valid call site.
from services.phone import normalize_phone_e164  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Confirmation codes
# ---------------------------------------------------------------------------


def _generate_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{_CODE_PREFIX}{body}"


# Confirmation-code canonicalization/display are core primitives
# (services/confirmation_codes.py) — code *generation* below stays booking.
# Re-exported so booking_service.{normalize,format}_confirmation_code call
# sites keep working.
from services.confirmation_codes import (  # noqa: E402,F401
    format_confirmation_code,
    normalize_confirmation_code,
)


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
