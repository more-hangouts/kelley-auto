"""Cross-cutting kernel models: auth, users, business profile, webhooks, cron state.

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
    # Phase 14: 'payroll' (strict geofence/selfie enforcement) or
    # 'commission' (clock-in is an active-app signal; no GPS/geofence
    # block, selfie never required). Migration 092.
    attendance_mode = Column(
        String(20), nullable=False, server_default=text("'payroll'")
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


