"""Business-local time helpers.

The VPS runs UTC, but boutique attendance rules (when "today" starts,
when a shift cutoff fires, when DST flips) are local. Centralizing the
conversion here keeps every attendance cron and shift comparison from
re-implementing `datetime.now()` math against `APP_TIMEZONE`.

Use these in:

  - `services/clock_in.py` for the today-bounds query in
    `current_status` / status reads.
  - The Phase 7 Slice 2 / Phase 9 attendance crons (auto-close,
    pre-close reminders, retention sweep).
  - Anywhere a comparison would otherwise be `datetime.now()` vs a
    business-local cutoff — that comparison is wrong on a UTC host.

`booking_service.shop_tz` already exists and remains for back-compat;
new code should import from this module.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import APP_TIMEZONE


def shop_tz() -> ZoneInfo:
    """Boutique's local timezone, derived from `APP_TIMEZONE`."""
    return ZoneInfo(APP_TIMEZONE)


def business_now() -> datetime:
    """Wall-clock time in the boutique's local timezone."""
    return datetime.now(shop_tz())


def to_business_local(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime into the boutique's local
    timezone. Naive datetimes are treated as UTC — the VPS is the only
    place naive datetimes legitimately come from in this codebase, and
    the VPS clock is UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(shop_tz())


def business_date(dt: datetime | None = None) -> date:
    """Calendar date as the boutique sees it. `None` means right now.

    Used by the today's-punches query and by the auto-close cron's
    "this shift's cutoff has passed" comparison. Both must use this
    helper instead of `dt.date()` so a punch at 23:30 local time on a
    Saturday does not get attributed to Sunday by a UTC `.date()` call.
    """
    target = dt if dt is not None else business_now()
    return to_business_local(target).date()
