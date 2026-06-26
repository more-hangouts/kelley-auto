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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200))
    is_active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    role = Column(String(20), nullable=False, server_default=text("'user'"))
    permissions = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    token_version = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_login = Column(DateTime(timezone=True))
    # Sales-portal PIN auth (migration 052). NULL `pin_hash` means the
    # user cannot PIN-login; admin users keep `pin_hash = NULL`.
    pin_hash = Column(String(255))
    pin_failed_count = Column(Integer, nullable=False, server_default=text("0"))
    pin_locked_until = Column(DateTime(timezone=True))
    last_pin_used_at = Column(DateTime(timezone=True))
    force_pin_change = Column(Boolean, nullable=False, server_default=text("FALSE"))
    # Compensation (migration 071). Admin-only on the wire — never
    # serialized by sales / portal / public surfaces. `commission_rate`
    # is stored as a decimal fraction (0.0750 = 7.5%); the staff
    # profile drawer converts to/from a percent for display.
    hourly_wage = Column(Numeric(10, 2))
    commission_rate = Column(Numeric(5, 4))
    # Staff archive / soft delete (migration 083). NULL = active roster
    # member; NOT NULL = archived (hidden from the roster, login/PIN and
    # scheduling blocked via is_active=False, history preserved).
    deleted_at = Column(DateTime(timezone=True))
    deleted_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_reason = Column(Text)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class IntegrationToken(Base):
    __tablename__ = "integration_tokens"

    id = Column(Integer, primary_key=True)
    provider = Column(String(50), unique=True, nullable=False)
    # Legacy plaintext columns. Reads fall back to these via
    # services.integration_tokens during the C1 transition window; a
    # follow-up slice will null and drop them.
    access_token = Column(Text)
    refresh_token = Column(Text)
    # C1 encrypted-at-rest columns. Writes always go through
    # services.integration_tokens.set_token which encrypts with the
    # newest INTEGRATION_TOKEN_KEYS entry.
    access_token_ciphertext = Column(LargeBinary)
    refresh_token_ciphertext = Column(LargeBinary)
    token_type = Column(String(20), server_default=text("'Bearer'"))
    expires_at = Column(DateTime(timezone=True))
    owner_uri = Column(String(500))
    organization_uri = Column(String(500))
    extra_metadata = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    source = Column(String(50), nullable=False)
    event_type = Column(String(100), nullable=False)
    external_id = Column(String(200))
    payload = Column(JSONB, nullable=False)
    headers = Column(JSONB)
    processed = Column(Boolean, nullable=False, server_default=text("FALSE"))
    processed_at = Column(DateTime(timezone=True))
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, server_default=text("0"))
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


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


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    display_name = Column(String(200), nullable=False)
    email = Column(String(255))
    phone = Column(String(32))
    phone_e164 = Column(String(20))
    address = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    notes = Column(Text)
    marketing_consent_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    primary_contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    event_type = Column(String(32), nullable=False)
    event_name = Column(String(200), nullable=False)
    event_date = Column(Date)
    court_size = Column(Integer)
    quince_theme = Column(String(200))
    quince_theme_colors = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    budget_range = Column(String(50))
    status = Column(String(32), nullable=False, server_default=text("'lead'"))
    status_changed_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    notes = Column(Text)
    # Day 3: optional link from a vehicle_sale deal to the catalog_items row
    # of the car being sold. Nullable — general leads and quinceañera events
    # leave it NULL. ON DELETE SET NULL so removing a vehicle never blocks
    # or cascades into its deal history.
    vehicle_catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="SET NULL")
    )
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class EventParticipant(Base):
    __tablename__ = "event_participants"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role = Column(String(32), nullable=False)
    display_name = Column(String(200), nullable=False)
    phone = Column(String(32))
    email = Column(String(255))
    measurements = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    notes = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class EventStatusChangeEvent(Base):
    __tablename__ = "event_status_change_events"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    from_status = Column(String(32))
    to_status = Column(String(32), nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    changed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    notes = Column(Text)


class EventDocument(Base):
    __tablename__ = "event_documents"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind = Column(String(16), nullable=False)
    filename = Column(String(500), nullable=False)
    content_type = Column(String(150), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    storage_key = Column(String(500), nullable=False)
    label = Column(String(200))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    invoice_amount_cents = Column(BigInteger)
    invoice_status = Column(String(16))
    invoice_issued_at = Column(DateTime(timezone=True))
    invoice_paid_at = Column(DateTime(timezone=True))

    # Phase 4a: optional pointer back to a canonical invoices.id row.
    # Populated only on kind='external_invoice' (enforced by CHECK).
    # Phase 4b's data migration backfills this for retagged legacy rows.
    linked_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL")
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    # Phase 10.2: which event participant's buyer journey this invoice
    # belongs to. NULL = celebrant's invoice or unspecified.
    event_participant_id = Column(
        Integer, ForeignKey("event_participants.id", ondelete="SET NULL")
    )
    invoice_number = Column(String(32), unique=True)
    status = Column(String(16), nullable=False, server_default=text("'draft'"))
    issue_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    due_date = Column(Date)

    subtotal_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    total_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    paid_to_date_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    balance_cents = Column(BigInteger, nullable=False, server_default=text("0"))

    # Phase 7: order-level discounts moved to a 1:N child table
    # (`invoice_order_discounts`). When the child table has at least
    # one row, `discount_cents` becomes a derived display value (sum
    # of per-discount savings) and the totals service uses pre-tax
    # math. With zero rows and `discount_cents > 0`, the legacy
    # post-tax flat-amount math still applies for already-sent records.

    terms = Column(Text)
    footer = Column(Text)
    public_notes = Column(Text)
    private_notes = Column(Text)
    po_number = Column(String(64))

    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    sold_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    sent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    paid_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)

    revision = Column(Integer, nullable=False, server_default=text("1"))
    last_pdf_rendered_revision = Column(Integer)
    last_pdf_rendered_at = Column(DateTime(timezone=True))
    last_pdf_render_error = Column(Text)

    legacy_migration_run_id = Column(UUID(as_uuid=True))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceOrderDiscount(Base):
    __tablename__ = "invoice_order_discounts"

    id = Column(BigInteger, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    # `preset_id` references `business_profile.discount_presets[].id` —
    # not a real foreign key because presets live inside a JSONB blob.
    # NULL marks a "Custom %" entry.
    preset_id = Column(Text)
    label = Column(Text, nullable=False)
    percent = Column(Numeric(5, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    kind = Column(String(16), nullable=False, server_default=text("'product'"))
    product_key = Column(String(120))
    # Legacy column. New lines (both catalog-backed and non-catalog) write
    # NULL here; the customer-facing copy comes from `public_description` or
    # the joined `catalog_items` row. Existing rows keep their staff-typed
    # text and still render to customers because that text is on issued
    # PDFs already.
    description = Column(Text)
    quantity = Column(Numeric(10, 2), nullable=False, server_default=text("1"))
    unit_price_cents = Column(BigInteger, nullable=False)
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_rate = Column(Numeric(7, 5), nullable=False, server_default=text("0"))
    tax_name = Column(String(40))
    line_subtotal_cents = Column(BigInteger, nullable=False)
    line_tax_cents = Column(BigInteger, nullable=False)
    line_total_cents = Column(BigInteger, nullable=False)
    # Legacy column. Stops rendering to customers at the Phase 4 render
    # swap; staff-readable historic context stays.
    notes = Column(Text)
    catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="RESTRICT")
    )
    size_label = Column(String(40))
    public_description = Column(Text)
    internal_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceInstallment(Base):
    __tablename__ = "invoice_installments"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    label = Column(String(60), nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    paid_at = Column(DateTime(timezone=True))
    staff_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceInvitation(Base):
    __tablename__ = "invoice_invitations"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    public_key = Column(String(64), unique=True, nullable=False)
    sent_at = Column(DateTime(timezone=True))
    last_resent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    last_viewed_at = Column(DateTime(timezone=True))
    view_count = Column(Integer, nullable=False, server_default=text("0"))
    email_opened_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    # Phase 10.2: which event participant's buyer journey this quote
    # belongs to. NULL = celebrant's quote or unspecified.
    event_participant_id = Column(
        Integer, ForeignKey("event_participants.id", ondelete="SET NULL")
    )
    quote_number = Column(String(32), unique=True)
    status = Column(String(16), nullable=False, server_default=text("'draft'"))
    issue_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    expires_at = Column(Date)

    subtotal_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    total_cents = Column(BigInteger, nullable=False, server_default=text("0"))

    # Phase 7: order-level discounts moved to a 1:N child table
    # (`quote_order_discounts`). See `Invoice` for the same semantics.

    terms = Column(Text)
    footer = Column(Text)
    public_notes = Column(Text)
    private_notes = Column(Text)
    po_number = Column(String(64))
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    sent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    rejected_at = Column(DateTime(timezone=True))
    rejection_reason = Column(Text)
    converted_at = Column(DateTime(timezone=True))
    converted_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL")
    )
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)

    signature_base64 = Column(Text)
    signature_signed_at = Column(DateTime(timezone=True))
    signature_ip = Column(INET)
    signature_name = Column(String(120))
    # Phase 5 of the sales portal — captured opportunistically from the
    # `User-Agent` request header during in-store signing for the
    # evidentiary trail. Nullable so older rows and tests that don't
    # provide a header continue to work.
    signature_user_agent = Column(String(255))
    # C3: HMAC-SHA256 hex over the canonical signed payload, stamped
    # by services.quote_signature_hmac at sign time. Schema CHECK
    # requires this once signature_signed_at is set; trigger
    # `trg_quote_signature_immutable` blocks any UPDATE that would
    # change it (or any other signature column) after the row is
    # signed.
    signature_hmac = Column(String(64))

    revision = Column(Integer, nullable=False, server_default=text("1"))
    last_pdf_rendered_revision = Column(Integer)
    last_pdf_rendered_at = Column(DateTime(timezone=True))
    last_pdf_render_error = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteOrderDiscount(Base):
    __tablename__ = "quote_order_discounts"

    id = Column(BigInteger, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    preset_id = Column(Text)
    label = Column(Text, nullable=False)
    percent = Column(Numeric(5, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id = Column(Integer, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    kind = Column(String(16), nullable=False, server_default=text("'product'"))
    product_key = Column(String(120))
    # See InvoiceLineItem.description: nullable for new lines, populated
    # on legacy rows.
    description = Column(Text)
    quantity = Column(Numeric(10, 2), nullable=False, server_default=text("1"))
    unit_price_cents = Column(BigInteger, nullable=False)
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_rate = Column(Numeric(7, 5), nullable=False, server_default=text("0"))
    tax_name = Column(String(40))
    line_subtotal_cents = Column(BigInteger, nullable=False)
    line_tax_cents = Column(BigInteger, nullable=False)
    line_total_cents = Column(BigInteger, nullable=False)
    notes = Column(Text)
    catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="RESTRICT")
    )
    size_label = Column(String(40))
    public_description = Column(Text)
    internal_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteInstallment(Base):
    """Phase 4 of the discount/payment-term refactor.

    Mirrors `InvoiceInstallment` minus the payment-state columns
    (`paid_at`, `staff_notes`). Quote schedules carry the customer's
    chosen plan from quote sign-off into the converted invoice; nothing
    on a quote has been paid yet, so the payment-state columns are
    deliberately absent.
    """

    __tablename__ = "quote_installments"

    id = Column(BigInteger, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    label = Column(Text)
    amount_cents = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class QuoteInvitation(Base):
    __tablename__ = "quote_invitations"

    id = Column(Integer, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    public_key = Column(String(64), unique=True, nullable=False)
    sent_at = Column(DateTime(timezone=True))
    last_resent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    last_viewed_at = Column(DateTime(timezone=True))
    view_count = Column(Integer, nullable=False, server_default=text("0"))
    email_opened_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    payment_number = Column(String(32), unique=True)
    amount_cents = Column(BigInteger, nullable=False)
    applied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    unapplied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    refunded_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    payment_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    method = Column(String(20), nullable=False)
    transaction_reference = Column(String(120))
    status = Column(String(24), nullable=False, server_default=text("'completed'"))
    notes = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id = Column(Integer, primary_key=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    applied_cents = Column(BigInteger, nullable=False)
    refunded_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class RefundEvent(Base):
    __tablename__ = "refund_events"

    id = Column(Integer, primary_key=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    amount_cents = Column(BigInteger, nullable=False)
    from_unapplied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    from_allocations_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    refund_method = Column(String(20), nullable=False)
    refund_reference = Column(String(120))
    notes = Column(Text)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class NumberingState(Base):
    __tablename__ = "numbering_state"

    id = Column(SmallInteger, primary_key=True, server_default=text("1"))
    invoice_year = Column(SmallInteger, nullable=False)
    invoice_seq = Column(Integer, nullable=False, server_default=text("0"))
    quote_year = Column(SmallInteger, nullable=False)
    quote_seq = Column(Integer, nullable=False, server_default=text("0"))
    # Phase 6: payment numbering shares the singleton row.
    payment_year = Column(SmallInteger, nullable=False)
    payment_seq = Column(Integer, nullable=False, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class BusinessProfile(Base):
    __tablename__ = "business_profile"

    id = Column(SmallInteger, primary_key=True, server_default=text("1"))
    legal_name = Column(String(200), nullable=False)
    display_name = Column(String(200))
    address_line1 = Column(String(200))
    address_line2 = Column(String(200))
    city = Column(String(120))
    state = Column(String(40))
    postal_code = Column(String(20))
    country = Column(String(2), nullable=False, server_default=text("'US'"))
    phone = Column(String(40))
    email = Column(String(255))
    website = Column(String(255))
    logo_storage_key = Column(String(500))

    # Public-facing opening hours (migration 087). Nullable JSONB; NULL means
    # "not set" and the storefront falls back to a generic hours line. Shape:
    # {"timezone": str, "days": [{"day": str, "closed": bool} |
    #  {"day": str, "open": str, "close": str}]}. Owner-editable via PATCH;
    # part of the public NAP DTO.
    business_hours = Column(JSONB)
    default_tax_rate = Column(Numeric(7, 5), nullable=False, server_default=text("0"))
    default_tax_name = Column(String(40))
    default_invoice_terms = Column(Text)
    default_invoice_footer = Column(Text)
    default_payment_instructions = Column(Text)

    # Phase 11: reminder cadence. Three slots, each with an enabled
    # flag, a day offset, and an offset basis ('before_due',
    # 'after_due', 'after_sent'). Late fee fires on reminder3 only.
    reminder1_enabled = Column(Boolean, nullable=False, server_default=text("FALSE"))
    reminder1_days_offset = Column(Integer, nullable=False, server_default=text("0"))
    reminder1_offset_basis = Column(
        String(16), nullable=False, server_default=text("'before_due'")
    )
    reminder2_enabled = Column(Boolean, nullable=False, server_default=text("FALSE"))
    reminder2_days_offset = Column(Integer, nullable=False, server_default=text("0"))
    reminder2_offset_basis = Column(
        String(16), nullable=False, server_default=text("'before_due'")
    )
    reminder3_enabled = Column(Boolean, nullable=False, server_default=text("FALSE"))
    reminder3_days_offset = Column(Integer, nullable=False, server_default=text("0"))
    reminder3_offset_basis = Column(
        String(16), nullable=False, server_default=text("'before_due'")
    )
    reminder_late_fee_cents = Column(
        BigInteger, nullable=False, server_default=text("0")
    )
    reminder_late_fee_pct = Column(
        Numeric(5, 3), nullable=False, server_default=text("0")
    )

    # Discount presets for the quote/invoice editor dropdown. Shape:
    # [{"id": str, "label": str, "percent": Decimal, "active": bool}].
    # Service-layer normalization caps at 12 entries and percent at 0-50.
    discount_presets = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    default_payment_plan_count = Column(SmallInteger)
    default_deposit_percent = Column(Numeric(5, 2))

    # Phase 7 Slice 2 of the Sales Portal — attendance settings.
    # `attendance_gate_enabled=True` (default) blocks sales-scope
    # appointment mutations when the stylist is punched out.
    # `selfie_policy` is `required | optional | disabled`; a
    # `disabled` policy makes /api/sales/clock/in reject any selfie.
    # `selfie_retention_days` drives the Slice 2 retention cron;
    # NULL means "keep forever".
    attendance_gate_enabled = Column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    selfie_policy = Column(
        String(16), nullable=False, server_default=text("'optional'")
    )
    selfie_retention_days = Column(Integer, server_default=text("365"))

    # Phase 9 sub-slice 1, Priority 2: biweekly pay-period anchor for
    # attendance reporting. When set, the `bucket=biweek` aggregation
    # aligns 14-day windows to this date; NULL means biweek bucketing
    # is unavailable (`bucket=biweek` returns 422 until set) and the
    # legacy `pay_period` range key falls back to "today minus 13 days".
    biweekly_anchor_date = Column(Date)

    # Phase 10 Slice 6 (Epic 6.2): target labor cost as a percent of
    # weekly revenue. When set, the admin schedule grid shows a
    # "Sales goal: $X" chip computed as labor_cost / target_labor_pct
    # * 100, alongside actual revenue for the visible week. NULL means
    # the chip is hidden; the CHECK keeps 0 out (would divide by zero).
    target_labor_pct = Column(Numeric(5, 2))

    # Clock-in reliability slice A: owner-tunable cap on how much
    # accuracy slack the geofence is willing to grant a single punch.
    # Effective buffer = min(client_accuracy_m, this cap). Default 50,
    # CHECK 0-200 (0 disables the buffer entirely).
    gps_accuracy_buffer_max_m = Column(
        Integer, nullable=False, server_default=text("50")
    )

    # Clock-in reliability slice C: trusted-network fallback. The
    # `_enabled` flag stays FALSE during the log-only ramp; detection
    # still runs and stamps `staff_punches.trusted_network_detected` so
    # the owner can verify the shop's public IP is stable before
    # flipping the toggle. `trusted_clock_in_ips` is a JSONB array of
    # IP-or-CIDR strings; see services.clock_in.is_ip_in_trusted_list.
    trusted_network_enabled = Column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    trusted_clock_in_ips = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    updated_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InstallmentReminderState(Base):
    __tablename__ = "installment_reminder_state"

    installment_id = Column(
        Integer,
        ForeignKey("invoice_installments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reminder1_sent_at = Column(DateTime(timezone=True))
    reminder2_sent_at = Column(DateTime(timezone=True))
    reminder3_sent_at = Column(DateTime(timezone=True))
    late_fee_applied_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class NotificationJob(Base):
    __tablename__ = "notification_jobs"

    id = Column(BigInteger, primary_key=True)
    kind = Column(String(64), nullable=False)
    channel = Column(String(16), nullable=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id", ondelete="CASCADE"))
    recipient = Column(String(320), nullable=False)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    due_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    status = Column(String(16), nullable=False, server_default=text("'pending'"))
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text)
    sent_at = Column(DateTime(timezone=True))
    # B1: polymorphic subject pair so the queue can carry staff/digest
    # jobs that have no Appointment to anchor against. Legacy customer-
    # booking rows are backfilled by migration 077 to
    # (subject_kind='appointment', subject_id=appointment_id) so
    # downstream consumers can treat the new pair as canonical.
    subject_kind = Column(Text)
    subject_id = Column(BigInteger)
    # B1: recipient resolved at enqueue time so the dispatcher can
    # re-check is_active / email-on-file before sending, and the admin
    # debug view can filter jobs by staff user without parsing the
    # recipient email string. NULL is allowed for legacy customer-flow
    # rows where the recipient was a raw email with no user account.
    recipient_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class StaffNotificationEvent(Base):
    """Append-only event log feeding the staff notification fan-out and
    digest summaries. Real-time event surfaces write here in the same
    transaction as any synchronous emails they fire so the digest
    runners (B2) have a complete activity timeline regardless of which
    hook path delivered each notification.

    ``daily_digest_consumed_at`` / ``weekly_digest_consumed_at`` track
    which rows each cadence's runner has already summarised; the
    partial indexes ``ix_sne_daily_pending`` and ``ix_sne_weekly_pending``
    make the "what's unsummarised" scan cheap as the table grows.
    """

    __tablename__ = "staff_notification_events"

    id = Column(BigInteger, primary_key=True)
    kind = Column(Text, nullable=False)
    subject_kind = Column(Text)
    subject_id = Column(BigInteger)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurred_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    daily_digest_consumed_at = Column(DateTime(timezone=True))
    weekly_digest_consumed_at = Column(DateTime(timezone=True))


class NotificationPreference(Base):
    """Per-user override for a single event kind. Existence of a row
    means the user has explicitly chosen (on or off); absence means the
    role default from ``services.notification_routing.ROLE_DEFAULTS``
    applies. PK is ``(user_id, event_kind)`` so a second write for the
    same pair must use ON CONFLICT or UPSERT — the dispatcher tooling
    never writes twice without intent.
    """

    __tablename__ = "notification_preferences"

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_kind = Column(Text, primary_key=True)
    enabled = Column(Boolean, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


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


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    # Captured at write-time so the audit row stays useful even after
    # the user is deleted (FK nulled). Reader prefers the live join,
    # falls back to this snapshot.
    actor_display_name = Column(String(200))
    actor_kind = Column(String(16), nullable=False)
    activity_type = Column(String(40), nullable=False)
    subject_kind = Column(String(20))
    subject_id = Column(Integer)
    payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class CatalogItem(Base):
    """One row per orderable style + color combination.

    Two identifier semantics:
      - `internal_sku`: real designer SKU staff types and searches by.
        Never returned from public/customer-facing endpoints.
      - `public_code`: opaque Bellas-only code (BVX-NNNNN) minted by
        services/catalog_service.py under a row-level lock on
        numbering_state. Once assigned, never rewritten by service code;
        Phase 7 will add a DB trigger as belt-and-suspenders.

    The category whitelist, image_urls array shape, and public_code
    format (^BVX-[0-9]{5}$) are enforced by CHECK constraints in
    migration 041; if you change those rules, change the migration.
    """

    __tablename__ = "catalog_items"

    id = Column(Integer, primary_key=True)
    internal_sku = Column(String(160), unique=True, nullable=False)
    public_code = Column(String(32), unique=True, nullable=False)
    designer = Column(String(120))
    style_number = Column(String(80))
    color = Column(String(80), nullable=False)
    house_name = Column(String(120))
    product_title = Column(String(200))
    category = Column(String(40), nullable=False)
    description_text = Column(Text)
    image_urls = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_platform = Column(String(40))
    source_product_id = Column(String(80))
    source_product_handle = Column(String(160))
    source_product_url = Column(Text)
    source_collection_url = Column(Text)
    source_product_type = Column(String(120))
    is_sample = Column(Boolean, nullable=False, server_default=text("FALSE"))
    active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    unit_price_cents = Column(Integer)
    # Vehicle inventory overlay (migration 085). `is_vehicle` is the
    # discriminator; legacy gown rows may have mirrored vehicle fields from
    # the compatibility backfill but keep is_vehicle=false.
    is_vehicle = Column(Boolean, nullable=False, server_default=text("FALSE"))
    vin = Column(String(17))
    stock_number = Column(String(64))
    year = Column(SmallInteger)
    make = Column(String(80))
    model = Column(String(80))
    trim = Column(String(80))
    mileage = Column(Integer)
    transmission = Column(String(40))
    fuel_type = Column(String(40))
    exterior_color = Column(String(60))
    interior_color = Column(String(60))
    body_type = Column(String(40))
    drivetrain = Column(String(20))
    condition = Column(String(20))
    vehicle_status = Column(String(20))
    carfax_url = Column(Text)
    video_url = Column(Text)
    features_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Wholesale inputs/provenance behind the computed unit_price_cents.
    # See services/pricing.py and migration 084.
    wholesale_cents = Column(Integer)
    wholesale_as_of = Column(Date)
    wholesale_source = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class SpecialOrder(Base):
    """One row per "where is my dress?" lifecycle entry.

    Phase 5 of the catalog SKU obfuscation plan. Tracks the
    needed → ordered → received → picked_up flow against a
    catalog-backed line without modeling stock counts, vendor
    integrations, or warehouse locations.

    Status vocabulary, the picked-up-requires-received invariant, and
    the ``status='ordered'/'received'/'picked_up'`` ↔ corresponding
    timestamp checks are enforced by CHECK constraints in migration
    043. The service layer enforces the same rules in Python so the
    error messages are friendlier than the DB violation, but the
    constraints are the back-stop.

    ``invoice_line_item_id`` is ON DELETE SET NULL because staff edit
    invoices freely; ``catalog_item_id`` and ``event_id`` are ON
    DELETE RESTRICT because losing either would orphan the lifecycle
    log without a way to reconstruct what was on order.

    ``vendor_order_number`` and ``internal_notes`` are staff-only;
    they are NEVER returned from public endpoints, embedded in
    activity payloads that customers can read, or rendered on any
    customer surface. Phase 7's lint will assert that.
    """

    __tablename__ = "special_orders"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_line_item_id = Column(
        Integer,
        ForeignKey("invoice_line_items.id", ondelete="SET NULL"),
    )
    catalog_item_id = Column(
        Integer,
        ForeignKey("catalog_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    size_label = Column(String(40), nullable=False)
    status = Column(String(24), nullable=False, server_default=text("'needed'"))
    ordered_at = Column(DateTime(timezone=True))
    eta_date = Column(Date)
    received_at = Column(DateTime(timezone=True))
    picked_up_at = Column(DateTime(timezone=True))
    vendor_order_number = Column(String(120))
    internal_notes = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


# ---------------------------------------------------------------------------
# Phase 7 Slice 1 of the Sales Portal — clock-in foundation. Selfie
# storage, owner attendance review UI, and the punched-out gate on
# existing sales endpoints arrive in Slice 2.
# ---------------------------------------------------------------------------


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


class CronRunState(Base):
    """One row per cron name. Updated in place at the start and end of
    every tick so admin can read "last run, scanned/changed, error"
    for the auto-close, pre-close reminder, and selfie retention crons
    without parsing logs."""

    __tablename__ = "cron_run_state"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    last_started_at = Column(DateTime(timezone=True))
    last_finished_at = Column(DateTime(timezone=True))
    last_scanned_count = Column(
        Integer, nullable=False, server_default=text("0")
    )
    last_changed_count = Column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error = Column(Text)
    consecutive_failures = Column(
        Integer, nullable=False, server_default=text("0")
    )
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
