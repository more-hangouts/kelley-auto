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

    # Optional pre-booking Boutique Experience profile to link to the new
    # appointment. Linking is best-effort; a stale or already-linked id will
    # not prevent the booking from succeeding.
    boutique_experience_profile_id: int | None = None

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
    # Tokenized URL the customer hits to complete (or update) their
    # Boutique Experience profile. Always populated, even when a profile
    # is already attached, so the booking widget can render a CTA the
    # customer can revisit.
    boutique_experience_url: str
    # True if a profile row is already linked to this appointment, so the
    # success screen can show "profile added" instead of asking the
    # customer to fill it out again.
    boutique_experience_attached: bool


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


# ---------------------------------------------------------------------------
# Boutique Experience profile (sizing calculator + style preferences)
# ---------------------------------------------------------------------------


class BoutiqueExperienceMeasurements(BaseModel):
    bust_inches: float | None = Field(default=None, ge=20, le=80)
    waist_inches: float | None = Field(default=None, ge=15, le=70)
    hips_inches: float | None = Field(default=None, ge=20, le=85)
    height_ft: int | None = Field(default=None, ge=3, le=8)
    height_in: int | None = Field(default=None, ge=0, le=11)


class BoutiqueExperienceSizing(BaseModel):
    estimated_size_low: int | None = Field(default=None, ge=0, le=40)
    estimated_size_high: int | None = Field(default=None, ge=0, le=40)
    size_by_bust: int | None = Field(default=None, ge=0, le=40)
    size_by_waist: int | None = Field(default=None, ge=0, le=40)
    size_by_hips: int | None = Field(default=None, ge=0, le=40)
    chart_source: str | None = Field(default=None, max_length=120)
    off_chart: bool | None = None


class BoutiqueExperiencePreferences(BaseModel):
    style: str | None = Field(default=None, max_length=40)
    back: str | None = Field(default=None, max_length=40)
    budget: str | None = Field(default=None, max_length=40)
    colors: str | None = Field(default=None, max_length=500)
    likes: str | None = Field(default=None, max_length=2000)
    avoids: str | None = Field(default=None, max_length=2000)


class BoutiqueExperienceSubmission(BaseModel):
    measurements: BoutiqueExperienceMeasurements = Field(
        default_factory=BoutiqueExperienceMeasurements
    )
    sizing: BoutiqueExperienceSizing = Field(default_factory=BoutiqueExperienceSizing)
    preferences: BoutiqueExperiencePreferences = Field(
        default_factory=BoutiqueExperiencePreferences
    )
    summary: str | None = Field(default=None, max_length=4000)
    visitor_id: str | None = None
    session_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _require_meaningful_data(self) -> "BoutiqueExperienceSubmission":
        # Without this, `{}` validates because every nested object has a
        # default factory and every field is nullable. The write path then
        # stamps `submitted_at` and the lead shows "complete" with no real
        # data behind it. Reject empty payloads at the API edge so the
        # read-side completion check can stay simple.
        m = self.measurements
        s = self.sizing
        p = self.preferences

        has_measurements = any(
            v is not None
            for v in (m.bust_inches, m.waist_inches, m.hips_inches,
                      m.height_ft, m.height_in)
        )
        has_sizing = any(
            v is not None
            for v in (s.estimated_size_low, s.estimated_size_high,
                      s.size_by_bust, s.size_by_waist, s.size_by_hips,
                      s.chart_source, s.off_chart)
        )
        has_preferences = any(
            v is not None and (not isinstance(v, str) or v.strip())
            for v in (p.style, p.back, p.budget, p.colors, p.likes, p.avoids)
        )
        has_summary = bool(self.summary and self.summary.strip())

        if not (has_measurements or has_sizing or has_preferences or has_summary):
            raise ValueError(
                "boutique experience submission must include at least one "
                "of measurements, sizing, preferences, or summary"
            )
        return self


class BoutiqueExperienceCreatedResponse(BaseModel):
    profile_id: int
    source: Literal["pre_booking"] = "pre_booking"


class BoutiqueExperienceConfirmRequest(BaseModel):
    # max_length 32 fits the D1 canonical form (`BX` + 20 chars = 22) with
    # room for legacy variants and minor input slack. The validator
    # canonicalises so the stored-vs-input comparison uses one shape.
    confirmation_code: str = Field(min_length=4, max_length=64)
    email: EmailStr
    profile: BoutiqueExperienceSubmission

    @field_validator("confirmation_code")
    @classmethod
    def _normalize_confirmation_code(cls, v: str) -> str:
        # Lazy import: booking_contracts is a leaf in the dep graph and
        # booking_service imports from it; importing at module-load time
        # would create a circular chain.
        from services.booking_service import normalize_confirmation_code

        return normalize_confirmation_code(v)


class BoutiqueExperienceTokenResponse(BaseModel):
    profile_id: int
    source: Literal[
        "post_booking_email",
        "post_booking_confirmation",
    ] = "post_booking_email"
    slot_start: datetime
    timezone: str
    confirmation_code: str
