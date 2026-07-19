"""Web chat — a new writer into the omnichannel inbox (ported from the
catering210 design).

The web chat is NOT a separate chat product: it writes into migration 094's
``conversations`` / ``conversation_messages`` tables with
``channel='web_chat'`` / ``provider='website'`` and the admin Inbox branches
on the channel column. This migration is purely additive/behavior-neutral:

  * widens the channel/provider CHECK constraints to admit the new pair
    (status vocabulary stays open/pending/resolved);
  * adds three nullable visitor columns to ``conversations`` — presence
    (``visitor_last_seen_at``, stamped by the widget's poll, ≤ every 30s),
    the page being viewed, and SMS consent captured in the chat contact step;
  * creates ``web_chat_scripts`` — the owner-editable guided-intake question
    tree, stored as append-only versioned JSONB. The service ships a seeded
    fallback script, so this table may stay empty forever.

``conversations.event_id`` (already present since 094) doubles as the
once-only escalation guard: the first escalation links/mints a CRM deal and
every later one reuses it.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE conversations DROP CONSTRAINT chk_conversations_channel"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE conversations ADD CONSTRAINT chk_conversations_channel "
            "CHECK (channel IN ('sms', 'facebook', 'instagram', 'web_chat'))"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE conversations DROP CONSTRAINT chk_conversations_provider"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE conversations ADD CONSTRAINT chk_conversations_provider "
            "CHECK (provider IN ('twilio', 'meta', 'website'))"
        )
    )

    connection.execute(
        text(
            """
            ALTER TABLE conversations
                ADD COLUMN visitor_last_seen_at TIMESTAMPTZ,
                ADD COLUMN visitor_page_url     TEXT,
                ADD COLUMN visitor_sms_opt_in   BOOLEAN
            """
        )
    )

    connection.execute(
        text(
            """
            CREATE TABLE web_chat_scripts (
                id                 SERIAL PRIMARY KEY,
                version            INTEGER NOT NULL UNIQUE,
                script             JSONB NOT NULL,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
