"""Pydantic contracts for the public booking widget surface."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# Public booking submissions use only the current buckets. The "solo" /
# "2_3" / "4_plus" legacy values stay in the DB CHECK constraint so
# historical rows and the reschedule path (which copies the bucket
# forward, not via this contract) keep working, but new submissions
# from the widget must use one of the three current values.
PartySize = Literal["pair", "3_4", "5_plus"]


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


class ThemeResponse(BaseModel):
    theme: dict[str, Any]
    copy_text: dict[str, Any]
    flow: dict[str, Any]


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class AvailabilitySlot(BaseModel):
    start: datetime
    end: datetime
    duration_minutes: int
    remaining: int


class AvailabilityDay(BaseModel):
    date: date
    weekday: int
    slots: list[AvailabilitySlot]


class AvailabilityResponse(BaseModel):
    timezone: str
    from_date: date
    to_date: date
    days: list[AvailabilityDay]


# ---------------------------------------------------------------------------
# Attribution + behavior — shared shape
# ---------------------------------------------------------------------------


class WidgetAttribution(BaseModel):
    page_url: str | None = None
    referrer_url: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None
    fbclid: str | None = None
    gclid: str | None = None
    msclkid: str | None = None
    fbp: str | None = None
    fbc: str | None = None


class WidgetDevice(BaseModel):
    device_type: str | None = None
    user_agent: str | None = None
    screen: str | None = None
    viewport: str | None = None
    browser_language: str | None = None
    platform: str | None = None
    browser_timezone: str | None = None


class WidgetBehavior(BaseModel):
    time_on_widget_ms: int | None = None
    interaction_count: int | None = None
    steps_completed: int | None = None
    user_journey: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Booking submission
# ---------------------------------------------------------------------------


class AppointmentSubmission(BaseModel):
    # Slot — client claim, validated against rules + capacity server-side
    slot_start: datetime
    slot_duration_minutes: int = Field(ge=5, le=480)

    # Customer — the parent is the contact identity (booker) and the
    # celebrant is just a first name; the celebrant's last name is
    # implicitly the parent's and not captured separately.
    parent_first_name: str = Field(min_length=1, max_length=100)
    parent_last_name: str = Field(min_length=1, max_length=100)
    celebrant_first_name: str = Field(min_length=1, max_length=100)
    celebrant_last_name: str | None = Field(default=None, max_length=100)
    event_date: date | None = None
    party_size: PartySize
    phone: str = Field(min_length=7, max_length=32)
    email: EmailStr
    note: str | None = Field(default=None, max_length=1000)

    # Identity / dedup
    event_id: str = Field(min_length=8, max_length=64)
    visitor_id: str | None = None
    session_id: str | None = None

    # Marketing email consent. The widget renders this as an
    # unchecked-by-default checkbox; we only set the timestamp on a True.
    # A False on a return booking does NOT clear a prior opt-in.
    marketing_consent: bool = False

    # Honeypot — must be empty/missing
    company_website: str | None = None

    # Attribution / device / behavior
    attribution: WidgetAttribution = Field(default_factory=WidgetAttribution)
    device: WidgetDevice = Field(default_factory=WidgetDevice)
    behavior: WidgetBehavior = Field(default_factory=WidgetBehavior)

    @field_validator(
        "parent_first_name",
        "parent_last_name",
        "celebrant_first_name",
        "celebrant_last_name",
        "note",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: Any) -> Any:
        # Strip BEFORE pydantic checks min_length, otherwise a
        # whitespace-only "   " satisfies min_length=1 and gets stripped
        # to an empty string after the fact.
        return v.strip() if isinstance(v, str) else v


class AppointmentResponse(BaseModel):
    confirmation_code: str
    slot_start: datetime
    slot_end: datetime
    timezone: str
    status: str
    reschedule_url: str
    cancel_url: str


# ---------------------------------------------------------------------------
# Session events / abandon
# ---------------------------------------------------------------------------


class SessionEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=50)
    step: str | None = Field(default=None, max_length=50)
    event_id: str | None = Field(default=None, max_length=64)
    visitor_id: str | None = None
    session_id: str | None = Field(default=None, max_length=64)
    page_url: str | None = None
    referrer_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AbandonRequest(BaseModel):
    event_id: str | None = Field(default=None, max_length=64)
    visitor_id: str | None = None
    session_id: str | None = Field(default=None, max_length=64)
    step: str | None = Field(default=None, max_length=50)
    page_url: str | None = None
    referrer_url: str | None = None
    partial: dict[str, Any] = Field(default_factory=dict)
    attribution: WidgetAttribution = Field(default_factory=WidgetAttribution)
    device: WidgetDevice = Field(default_factory=WidgetDevice)
    behavior: WidgetBehavior = Field(default_factory=WidgetBehavior)


class AcknowledgedResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Reschedule / cancel
# ---------------------------------------------------------------------------


class RescheduleSummary(BaseModel):
    confirmation_code: str
    slot_start: datetime
    slot_end: datetime
    timezone: str
    status: str
    celebrant_first_name: str


class RescheduleRequest(BaseModel):
    slot_start: datetime
    slot_duration_minutes: int = Field(ge=5, le=480)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
