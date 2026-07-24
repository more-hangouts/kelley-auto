"""Notification jobs, subscriptions, conversations, web-chat.

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


class NotificationSubscriber(Base):
    """A person who can receive staff notifications (migration 093).

    ``user_id`` links a login account (one subscriber row per user, enforced
    by the partial unique index ``uq_notification_subscribers_user``);
    ``user_id IS NULL`` is an external, email-only recipient with no CRM
    login. The ``chk_notification_subscribers_deliverable`` CHECK guarantees
    an external row always carries an email. This is the fourth recipient
    layer read by ``services.notification_routing.recipients_for``.
    """

    __tablename__ = "notification_subscribers"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    display_name = Column(String(200), nullable=False)
    email = Column(String(320))
    phone_e164 = Column(String(20))
    is_active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class NotificationSubscription(Base):
    """Which event kinds a subscriber wants, per channel (migration 093).

    PK ``(subscriber_id, kind, channel)`` makes an upsert the natural write.
    Phase 1 only routes ``channel='email'``; ``in_app`` / ``sms`` are reserved
    for the inbox and Twilio phases.
    """

    __tablename__ = "notification_subscriptions"

    subscriber_id = Column(
        Integer,
        ForeignKey("notification_subscribers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind = Column(Text, primary_key=True)
    channel = Column(
        String(16), primary_key=True, server_default=text("'email'")
    )
    enabled = Column(Boolean, nullable=False, server_default=text("TRUE"))
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class Conversation(Base):
    """One omnichannel inbox thread per (provider, channel, external party)
    — migration 094. ``external_id`` is the customer's E.164 for SMS. The
    ``uq_conversations_identity`` unique index is the upsert target that
    stops racing inbound webhooks from forking a thread. ``contact_id`` /
    ``event_id`` stay NULL until a text is matched/linked to the CRM.
    """

    __tablename__ = "conversations"

    id = Column(BigInteger, primary_key=True)
    channel = Column(String(16), nullable=False)
    provider = Column(String(16), nullable=False)
    external_id = Column(Text, nullable=False)
    business_ref = Column(Text)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"))
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"))
    status = Column(String(16), nullable=False, server_default=text("'open'"))
    assigned_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    last_message_at = Column(DateTime(timezone=True))
    last_inbound_at = Column(DateTime(timezone=True))
    last_outbound_at = Column(DateTime(timezone=True))
    last_inbound_preview = Column(Text)
    # Web chat only (migration 097): presence stamp from the widget's poll
    # (≤ every 30s), the storefront page being viewed, and SMS consent from
    # the chat contact step. NULL on every other channel.
    visitor_last_seen_at = Column(DateTime(timezone=True))
    visitor_page_url = Column(Text)
    visitor_sms_opt_in = Column(Boolean)
    conversation_metadata = Column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class ConversationMessage(Base):
    """One inbound/outbound message in a conversation (migration 094).
    Channel-neutral ``sender_ref`` / ``recipient_ref``. The partial unique
    ``(provider, provider_message_id)`` dedups webhook retries and echoes of
    our own sends; ``status`` moves monotonically in the service layer.
    """

    __tablename__ = "conversation_messages"

    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction = Column(String(8), nullable=False)
    channel = Column(String(16), nullable=False)
    provider = Column(String(16), nullable=False)
    sender_ref = Column(Text, nullable=False)
    recipient_ref = Column(Text, nullable=False)
    body = Column(Text)
    media = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status = Column(String(16), nullable=False)
    provider_message_id = Column(Text)
    provider_payload = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    provider_error_code = Column(Text)
    provider_error_message = Column(Text)
    sent_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    consent_snapshot = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_echo = Column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    sent_at = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    failed_at = Column(DateTime(timezone=True))


class ConversationRead(Base):
    """Per-user read state for a conversation (migration 094). Unread is
    derived: ``conversation.last_inbound_at > last_read_at``."""

    __tablename__ = "conversation_reads"

    conversation_id = Column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_read_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class WebChatScript(Base):
    """Versioned guided-intake question tree for the storefront web chat
    (migration 097). Append-only: saving an edit inserts ``version = max+1``;
    old conversations stay readable because each intake block is stamped with
    the script version it ran. The service falls back to a seeded constant
    when this table is empty."""

    __tablename__ = "web_chat_scripts"

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, unique=True)
    script = Column(JSONB, nullable=False)
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


