"""Omnichannel inbox core (Omnichannel Inbox Plan Part 2; Phase 2).

The durable conversation store the CRM inbox reads from. Phase 2 wires the
Twilio SMS **inbound** path onto it; outbound and the Meta channels land in
later phases against the same tables.

Three tables:

  - ``conversations`` — one thread per (provider, channel, external party).
    ``external_id`` is the customer's normalized E.164 for SMS. The
    ``UNIQUE (provider, channel, external_id)`` is the hard guard against two
    racing inbound webhooks creating duplicate threads: all creation goes
    through an upsert on that key. ``contact_id`` / ``event_id`` are nullable
    — an inbound text from an unknown number lands as an unlinked
    conversation for staff to triage.

  - ``conversation_messages`` — inbound and outbound messages. Channel-neutral
    ``sender_ref`` / ``recipient_ref`` (E.164 today, PSID/IGSID later) rather
    than SMS-shaped "number" columns. Partial ``UNIQUE (provider,
    provider_message_id)`` dedups webhook retries AND echoes of our own sends.
    ``status`` transitions are monotonic in the service layer (a late Twilio
    ``sent`` callback never overwrites ``delivered``).

  - ``conversation_reads`` — per-user read state. Unread is derived
    (``last_inbound_at > last_read_at``), so two staff viewing the same
    thread don't fight over a shared counter.

The raw-webhook audit/replay buffer is NOT created here: the existing
``webhook_events`` table (migration 004, still a stub) is the sanctioned raw
store, written through ``services.webhook_ingest.record_webhook_event`` with
header redaction + retention. A raw Twilio row correlates to the message it
produced via ``webhook_events.external_id = conversation_messages.provider_message_id``.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE conversations (
                id                   BIGSERIAL PRIMARY KEY,
                channel              VARCHAR(16) NOT NULL,
                provider             VARCHAR(16) NOT NULL,
                external_id          TEXT NOT NULL,
                business_ref         TEXT,
                contact_id           INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                event_id             INTEGER REFERENCES events(id) ON DELETE SET NULL,
                status               VARCHAR(16) NOT NULL DEFAULT 'open',
                assigned_user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
                last_message_at      TIMESTAMPTZ,
                last_inbound_at      TIMESTAMPTZ,
                last_outbound_at     TIMESTAMPTZ,
                last_inbound_preview TEXT,
                metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_conversations_channel
                    CHECK (channel IN ('sms', 'facebook', 'instagram')),
                CONSTRAINT chk_conversations_provider
                    CHECK (provider IN ('twilio', 'meta')),
                CONSTRAINT chk_conversations_status
                    CHECK (status IN ('open', 'pending', 'resolved'))
            )
            """
        )
    )
    # The dedup identity: upsert target for inbound webhooks.
    connection.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_conversations_identity
                ON conversations (provider, channel, external_id)
            """
        )
    )
    # Inbox list ordering + filters.
    connection.execute(
        text(
            """
            CREATE INDEX idx_conversations_status_recent
                ON conversations (status, last_message_at DESC)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX idx_conversations_contact
                ON conversations (contact_id)
                WHERE contact_id IS NOT NULL
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX idx_conversations_assigned
                ON conversations (assigned_user_id)
                WHERE assigned_user_id IS NOT NULL
            """
        )
    )

    connection.execute(
        text(
            """
            CREATE TABLE conversation_messages (
                id                    BIGSERIAL PRIMARY KEY,
                conversation_id       BIGINT NOT NULL
                                          REFERENCES conversations(id)
                                          ON DELETE CASCADE,
                direction             VARCHAR(8) NOT NULL,
                channel               VARCHAR(16) NOT NULL,
                provider              VARCHAR(16) NOT NULL,
                sender_ref            TEXT NOT NULL,
                recipient_ref         TEXT NOT NULL,
                body                  TEXT,
                media                 JSONB NOT NULL DEFAULT '[]'::jsonb,
                status                VARCHAR(16) NOT NULL,
                provider_message_id   TEXT,
                provider_payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
                provider_error_code   TEXT,
                provider_error_message TEXT,
                sent_by_user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
                consent_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
                is_echo               BOOLEAN NOT NULL DEFAULT FALSE,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sent_at               TIMESTAMPTZ,
                delivered_at          TIMESTAMPTZ,
                failed_at             TIMESTAMPTZ,
                CONSTRAINT chk_conversation_messages_direction
                    CHECK (direction IN ('inbound', 'outbound')),
                CONSTRAINT chk_conversation_messages_status
                    CHECK (status IN (
                        'received', 'queued', 'sent',
                        'delivered', 'read', 'failed'
                    ))
            )
            """
        )
    )
    # Dedup webhook retries + our own send echoes. Partial: a locally-created
    # outbound row has no provider id until Twilio accepts it.
    connection.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_conversation_messages_provider_id
                ON conversation_messages (provider, provider_message_id)
                WHERE provider_message_id IS NOT NULL
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX idx_conversation_messages_thread
                ON conversation_messages (conversation_id, created_at)
            """
        )
    )

    connection.execute(
        text(
            """
            CREATE TABLE conversation_reads (
                conversation_id BIGINT NOT NULL
                                    REFERENCES conversations(id) ON DELETE CASCADE,
                user_id         INTEGER NOT NULL
                                    REFERENCES users(id) ON DELETE CASCADE,
                last_read_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (conversation_id, user_id)
            )
            """
        )
    )

    # SMS opt-out state on the contact. Inbound-only Phase 2 can already
    # RECORD an opt-out when a customer texts STOP; the outbound guard
    # (Phase 3) reads these before ever sending. Kept separate from
    # ``marketing_consent_at`` (email marketing) on purpose.
    connection.execute(
        text(
            """
            ALTER TABLE contacts
                ADD COLUMN IF NOT EXISTS sms_opted_out_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS sms_opt_out_source TEXT
            """
        )
    )
