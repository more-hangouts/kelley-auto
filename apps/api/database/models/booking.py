"""Public appointment booking and appointment lifecycle.

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



class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    # D1: widened from VARCHAR(20) to VARCHAR(32) when entropy bumped from
    # 6 chars to 20. Stored canonical (no hyphens, uppercased); the
    # display layer hyphenates via `booking_service.format_confirmation_code`.
    confirmation_code = Column(String(32), unique=True, nullable=False)

    slot_start_at = Column(DateTime(timezone=True), nullable=False)
    slot_end_at = Column(DateTime(timezone=True), nullable=False)
    slot_duration_minutes = Column(Integer, nullable=False)
    timezone = Column(String(64), nullable=False)

    celebrant_first_name = Column(String(100), nullable=False)
    celebrant_last_name = Column(String(100))
    parent_first_name = Column(String(100))
    parent_last_name = Column(String(100))
    event_date = Column(Date)
    party_size_bucket = Column(String(20), nullable=False)
    phone = Column(String(32), nullable=False)
    phone_e164 = Column(String(20))
    email = Column(String(255), nullable=False)
    customer_note = Column(Text)

    status = Column(String(20), nullable=False, server_default=text("'confirmed'"))
    assigned_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    internal_notes = Column(Text)
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)
    rescheduled_from_id = Column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL")
    )

    # G1: bumped to `NOW()` whenever a self-service token surface for this
    # appointment should stop working (currently: cancel + reschedule-of-
    # original). Verifier rejects any token whose `iat` is older.
    tokens_invalidated_at = Column(DateTime(timezone=True))

    attended_at = Column(DateTime(timezone=True))
    no_show_at = Column(DateTime(timezone=True))
    purchase_at = Column(DateTime(timezone=True))
    purchase_value_cents = Column(Integer)

    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"))
    crm_event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"))
    # Phase 10.2: which event participant's buyer journey this appointment
    # belongs to. NULL = celebrant's appointment or unspecified.
    event_participant_id = Column(
        Integer, ForeignKey("event_participants.id", ondelete="SET NULL")
    )

    visitor_id = Column(UUID(as_uuid=True))
    session_id = Column(String(64))
    event_id = Column(String(64), unique=True)
    page_url = Column(Text)
    referrer_url = Column(Text)
    utm_source = Column(String(255))
    utm_medium = Column(String(255))
    utm_campaign = Column(String(255))
    utm_content = Column(String(255))
    utm_term = Column(String(255))
    utm_id = Column(String(255))
    fbclid = Column(String(500))
    gclid = Column(String(500))
    msclkid = Column(String(500))
    fbp_cookie = Column(String(255))
    fbc_cookie = Column(String(500))

    device_type = Column(String(20))
    user_agent = Column(Text)
    screen = Column(String(32))
    viewport = Column(String(32))
    browser_language = Column(String(32))
    platform = Column(String(64))
    browser_timezone = Column(String(64))

    time_on_widget_ms = Column(Integer)
    interaction_count = Column(Integer)
    steps_completed = Column(Integer)
    user_journey = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    behavior_score = Column(Integer)
    bot_suspected = Column(Boolean, nullable=False, server_default=text("FALSE"))

    meta_capi_event_id = Column(String(128))
    meta_capi_synced_at = Column(DateTime(timezone=True))
    google_enhanced_synced_at = Column(DateTime(timezone=True))
    conversion_value_synced_at = Column(DateTime(timezone=True))

    raw_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AppointmentAvailabilityRule(Base):
    __tablename__ = "appointment_availability_rules"

    id = Column(Integer, primary_key=True)
    weekday = Column(SmallInteger, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    slot_duration_minutes = Column(Integer, nullable=False, server_default=text("45"))
    capacity = Column(Integer, nullable=False, server_default=text("1"))
    effective_from = Column(Date)
    effective_to = Column(Date)
    active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    label = Column(String(100))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AppointmentBlackout(Base):
    __tablename__ = "appointment_blackouts"

    id = Column(Integer, primary_key=True)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(200))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AppointmentVisitor(Base):
    __tablename__ = "appointment_visitors"

    visitor_id = Column(UUID(as_uuid=True), primary_key=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    first_touch_attribution = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_touch_attribution = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    session_count = Column(Integer, nullable=False, server_default=text("1"))
    booked_at = Column(DateTime(timezone=True))


class AppointmentSessionEvent(Base):
    __tablename__ = "appointment_session_events"

    id = Column(BigInteger, primary_key=True)
    visitor_id = Column(UUID(as_uuid=True))
    session_id = Column(String(64))
    event_id = Column(String(64))
    event_name = Column(String(50), nullable=False)
    step = Column(String(50))
    appointment_id = Column(Integer, ForeignKey("appointments.id", ondelete="SET NULL"))
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    page_url = Column(Text)
    referrer_url = Column(Text)
    user_agent = Column(Text)
    ip_hash = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AppointmentEnrichmentResponse(Base):
    __tablename__ = "appointment_enrichment_responses"

    id = Column(Integer, primary_key=True)
    # Nullable so calculator-first profiles can exist before booking.
    # Postgres still enforces at-most-one-per-appointment via the UNIQUE
    # constraint inherited from the original migration, since it treats
    # NULLs as distinct.
    appointment_id = Column(
        Integer,
        ForeignKey("appointments.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )

    # Survey-shape preferences (pre-Boutique-Experience era). Multi-select
    # arrays the staff UI already renders.
    dress_styles = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    colors = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    budget_range = Column(String(50))
    quince_theme = Column(String(200))
    quince_theme_colors = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    court_size = Column(Integer)
    inspiration_photos = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    free_text = Column(Text)
    opened_at = Column(DateTime(timezone=True))
    submitted_at = Column(DateTime(timezone=True))
    raw_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Boutique Experience extension (calculator path).
    visitor_id = Column(UUID(as_uuid=True))
    session_id = Column(String(64))
    source = Column(String(32))

    bust_inches = Column(Numeric(4, 1))
    waist_inches = Column(Numeric(4, 1))
    hips_inches = Column(Numeric(4, 1))
    height_ft = Column(SmallInteger)
    height_in = Column(SmallInteger)

    estimated_size_low = Column(SmallInteger)
    estimated_size_high = Column(SmallInteger)
    size_by_bust = Column(SmallInteger)
    size_by_waist = Column(SmallInteger)
    size_by_hips = Column(SmallInteger)
    chart_source = Column(String(120))
    off_chart = Column(Boolean)

    style_preference = Column(String(40))
    back_preference = Column(String(40))
    budget_preference = Column(String(40))
    color_preferences_text = Column(Text)
    likes = Column(Text)
    avoids = Column(Text)

    summary = Column(Text)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class BookingWidgetThemeSettings(Base):
    __tablename__ = "booking_widget_theme_settings"

    id = Column(SmallInteger, primary_key=True, server_default=text("1"))
    theme = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    copy = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    flow = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AppointmentTriedOnItem(Base):
    """One catalog item tried on during one appointment (Phase 4 of the
    sales portal). UNIQUE (appointment_id, catalog_item_id, size_label)
    is created `NULLS NOT DISTINCT` so two NULL size rows for the same
    dress also collide; see migration 053 for the constraint."""

    __tablename__ = "appointment_tried_on_items"

    id = Column(BigInteger, primary_key=True)
    appointment_id = Column(
        Integer,
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
    )
    catalog_item_id = Column(
        Integer,
        ForeignKey("catalog_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    size_label = Column(String(50))
    liked = Column(Boolean)
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


