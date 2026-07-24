"""Storefront analytics, attribution, activity log.

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



class StorefrontVisitor(Base):
    """Anonymous storefront browser (migration 090). Keyed by the first-party
    ``ka_vid`` cookie value. No raw PII — attribution is source/UTM only."""

    __tablename__ = "storefront_visitors"

    id = Column(Integer, primary_key=True)
    visitor_key = Column(String(64), unique=True, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    first_touch_attribution = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_touch_attribution = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class StorefrontSession(Base):
    """A single storefront visit (migration 090). Keyed by the first-party
    ``ka_sid`` cookie. IP is stored HASHED only, never raw."""

    __tablename__ = "storefront_sessions"

    id = Column(Integer, primary_key=True)
    visitor_id = Column(
        Integer, ForeignKey("storefront_visitors.id", ondelete="CASCADE"), nullable=False
    )
    session_key = Column(String(64), unique=True, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    landing_page = Column(Text)
    initial_referrer = Column(Text)
    initial_utm = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    user_agent = Column(Text)
    ip_hash = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class StorefrontEvent(Base):
    """Behavioral storefront event stream (migration 090): page_view,
    vehicle_view, lead_form_opened/started, lead_submitted. ``event_id`` is the
    CAPI dedup id (unique when present)."""

    __tablename__ = "storefront_events"

    id = Column(BigInteger, primary_key=True)
    visitor_id = Column(Integer, ForeignKey("storefront_visitors.id", ondelete="SET NULL"))
    session_id = Column(Integer, ForeignKey("storefront_sessions.id", ondelete="SET NULL"))
    event_name = Column(String(50), nullable=False)
    event_id = Column(String(64))  # CAPI dedup id; UNIQUE via partial index
    path = Column(Text)
    referrer = Column(Text)
    utm = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    listing_code = Column(String(40))
    vehicle_catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="SET NULL")
    )
    # Normalized channel (migration 096), derived at write time via the
    # UTM → click-id → referrer priority ladder. NULL = honestly unknown.
    source = Column(String(120))
    medium = Column(String(120))
    click_id = Column(String(255))
    # `metadata` is reserved on the declarative Base, so the attribute is
    # `event_metadata` while the column stays `metadata`.
    event_metadata = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class LeadAttribution(Base):
    """Bridge from anonymous browsing to a CRM deal (migration 090). 1:1 with an
    ``events`` row. Carries source/UTM/landing + the ``_fbp``/``_fbc`` Meta
    cookies for later CAPI matching. NEVER carries BHPH application PII."""

    __tablename__ = "lead_attribution"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    visitor_id = Column(Integer, ForeignKey("storefront_visitors.id", ondelete="SET NULL"))
    session_id = Column(Integer, ForeignKey("storefront_sessions.id", ondelete="SET NULL"))
    conversion_storefront_event_id = Column(
        BigInteger, ForeignKey("storefront_events.id", ondelete="SET NULL")
    )
    landing_page = Column(Text)
    source_page = Column(Text)
    utm = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    referrer = Column(Text)
    fbp = Column(String(255))
    fbc = Column(String(255))
    # Normalized channel (migration 096) — the deal's first-touch source.
    source = Column(String(120))
    medium = Column(String(120))
    click_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class AdConversionEvent(Base):
    """Provider-neutral OUTBOUND ad-conversion queue (migration 090, Phase 3).

    The table exists so the CAPI data model is ready, but nothing enqueues or
    sends until ``META_CAPI_ENABLED`` is flipped and the sender is built.
    ``user_data`` holds SERVER-HASHED identifiers only — never raw PII."""

    __tablename__ = "ad_conversion_events"

    id = Column(BigInteger, primary_key=True)
    provider = Column(String(20), nullable=False, server_default=text("'meta'"))
    event_name = Column(String(50), nullable=False)
    event_id = Column(String(64))
    event_time = Column(DateTime(timezone=True), nullable=False)
    source_url = Column(Text)
    action_source = Column(String(20), nullable=False, server_default=text("'website'"))
    user_data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    custom_data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text)
    lead_event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"))
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


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


class SalesActivityEvent(Base):
    """Commission-mode sales activity monitoring (Phase 14, migration 091).

    Append-only stream of the meaningful *reads* a sales rep performs
    (lead/event opened, appointment opened, contact opened, search run),
    recorded server-side inside the sales read endpoints. Separate from
    ``activity_log`` because contact/search views have no ``event_id`` to
    anchor to. Privacy: no note bodies, no financial fields, no raw search
    text — only normalized ``metadata`` (query length, result count).
    """

    __tablename__ = "sales_activity_events"

    id = Column(BigInteger, primary_key=True)
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_type = Column(String(40), nullable=False)
    # Subject pair invariant (CHECK in migration 091): both set or both NULL.
    subject_kind = Column(String(20))
    subject_id = Column(Integer)
    route = Column(Text)
    source = Column(String(40))
    activity_metadata = Column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


