"""Notification subscribers — the "who gets what" registry (Omnichannel
Inbox Plan, Part 1).

Today every notification recipient must be a ``users`` row: routing resolves
intrinsic targeting → role defaults → per-user preference overrides, all keyed
on ``users.id``. The owner wants to add people who should get *email alerts*
without giving them a CRM login at all — a front-desk address, the dealership
principal, an accountant. This migration adds the fourth resolution layer:

  - ``notification_subscribers`` — a person who can receive notifications.
    ``user_id`` links a login account (one subscriber row per user, enforced
    by a partial unique index); ``user_id IS NULL`` is an external,
    email-only person. A CHECK guarantees an external row always carries an
    email to deliver to.

  - ``notification_subscriptions`` — which event kinds each subscriber wants,
    per channel. PK ``(subscriber_id, kind, channel)`` makes an upsert the
    natural write. Phase 1 only routes ``channel='email'``; ``in_app`` /
    ``sms`` are reserved for when the inbox and Twilio land.

``services.notification_routing.recipients_for`` reads these as a union layer
on top of the existing three, deduped by email so a role default plus a
subscription can't double-send. Nothing here fires on its own — the existing
event surfaces (``admin.new_booking`` etc.) gain external recipients the
moment a row is added.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE notification_subscribers (
                id             SERIAL PRIMARY KEY,
                user_id        INTEGER REFERENCES users(id) ON DELETE CASCADE,
                display_name   VARCHAR(200) NOT NULL,
                email          VARCHAR(320),
                phone_e164     VARCHAR(20),
                is_active      BOOLEAN NOT NULL DEFAULT TRUE,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                -- An external (login-less) subscriber must have somewhere to
                -- deliver. A linked subscriber can omit email and inherit the
                -- user's authoritative address at resolve time. NULLIF(BTRIM…)
                -- rejects a blank/whitespace-only email from a direct DB insert
                -- so the database contract matches the service contract (which
                -- trims blanks to NULL).
                CONSTRAINT chk_notification_subscribers_deliverable
                    CHECK (
                        user_id IS NOT NULL
                        OR NULLIF(BTRIM(email), '') IS NOT NULL
                    )
            )
            """
        )
    )

    # One subscriber row per login user (NULLs are distinct in Postgres, so
    # this leaves external rows unconstrained — many may share NULL user_id).
    connection.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_notification_subscribers_user
                ON notification_subscribers (user_id)
                WHERE user_id IS NOT NULL
            """
        )
    )

    # No two external subscribers on the same email (case-insensitive). Linked
    # subscribers are excluded — their identity is the user, not the email.
    connection.execute(
        text(
            """
            CREATE UNIQUE INDEX uq_notification_subscribers_external_email
                ON notification_subscribers (LOWER(email))
                WHERE user_id IS NULL AND email IS NOT NULL
            """
        )
    )

    connection.execute(
        text(
            """
            CREATE TABLE notification_subscriptions (
                subscriber_id  INTEGER NOT NULL
                                   REFERENCES notification_subscribers(id)
                                   ON DELETE CASCADE,
                kind           TEXT NOT NULL,
                channel        VARCHAR(16) NOT NULL DEFAULT 'email',
                enabled        BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (subscriber_id, kind, channel),
                CONSTRAINT chk_notification_subscriptions_channel
                    CHECK (channel IN ('email', 'in_app', 'sms'))
            )
            """
        )
    )

    # recipients_for() fans out per kind: index the lookup path.
    connection.execute(
        text(
            """
            CREATE INDEX idx_notification_subscriptions_kind
                ON notification_subscriptions (kind, channel)
                WHERE enabled
            """
        )
    )
