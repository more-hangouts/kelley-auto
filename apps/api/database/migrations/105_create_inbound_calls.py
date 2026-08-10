"""Inbound voice call log (Twilio Voice, inbound phase 1: forward to the shop).

Records every call that ARRIVES at the business Twilio number, whatever happens
to it afterwards. Phase 1 forwards to the shop's published office line; the row
is written the moment Twilio hits our webhook, so a call is logged even when the
forward leg is never answered — missed calls are exactly the ones the shop most
needs to see.

Why NOT ``contact_call_attempts`` (the outbound table from migration 098),
audited as the reuse candidate first:

  * ``contact_id`` is NOT NULL there. An inbound call from an unknown number has
    no contact at all, and a stranger calling the lot is the common case — the
    whole point of logging inbound. Relaxing that column would weaken the
    outbound table's guarantee for every existing reader.
  * ``salesperson_user_id`` means "the rep who placed the call". Inbound has no
    placer; it has (maybe) an answerer, which is a different relation.
  * Its ``outcome`` vocabulary is salesperson-REPORTED (wrong_number, cancelled
    …). Inbound status is PROVIDER-reported (no-answer, busy, failed) and
    arrives asynchronously on a status callback, not from a human.
  * Idempotency there is a client double-tap key; here it is the provider's
    CallSid, which must be unique and is also the update key for the callback.

``contact_id`` is a soft link filled in when the caller's number matches a known
contact — nullable, ON DELETE SET NULL, so the call log survives contact
deletion and an unmatched call is still a first-class row.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


# Provider-reported call statuses, mirroring Twilio's DialCallStatus /
# CallStatus vocabulary. ``received`` is the birth state written by the inbound
# webhook before any status callback has landed.
_STATUSES = (
    "received",
    "ringing",
    "in_progress",
    "completed",
    "busy",
    "no_answer",
    "failed",
    "canceled",
)

# How the call was handled. Phase 1 only ever writes 'forwarded' (or 'rejected'
# when the feature flag is off); 'browser'/'voicemail' exist so phase 2 does not
# need a migration to widen the check constraint.
_DISPOSITIONS = ("forwarded", "browser", "voicemail", "rejected")


def upgrade(connection) -> None:
    statuses_sql = ", ".join(f"'{s}'" for s in _STATUSES)
    dispositions_sql = ", ".join(f"'{d}'" for d in _DISPOSITIONS)
    connection.execute(
        text(
            f"""
            CREATE TABLE inbound_calls (
                id                  BIGSERIAL PRIMARY KEY,
                -- Twilio's CallSid. Unique: it is both the dedup key for a
                -- webhook retry and the lookup key for the status callback.
                provider_call_sid   VARCHAR(64) NOT NULL,
                -- Caller (Twilio 'From') and the business number they dialed
                -- ('To'). Stored E.164 as Twilio delivers them.
                from_number         VARCHAR(20) NOT NULL,
                to_number           VARCHAR(20) NOT NULL,
                -- Soft link to a known contact when the caller's number
                -- matches. NULL for strangers, which is the common case.
                contact_id          INTEGER
                                      REFERENCES contacts(id) ON DELETE SET NULL,
                -- The rep who ended up on the call, when we can attribute one.
                -- Unused in phase 1 (the shop line is not a CRM user); phase 2
                -- browser answering fills it.
                answered_by_user_id INTEGER
                                      REFERENCES users(id) ON DELETE SET NULL,
                status              VARCHAR(20) NOT NULL DEFAULT 'received',
                disposition         VARCHAR(20),
                -- Where phase 1 actually sent the call, snapshotted at routing
                -- time so a later config change never rewrites history.
                forwarded_to        VARCHAR(20),
                -- Talk time in seconds, from the status callback. NULL until
                -- the call completes (and for calls that never connect).
                duration_seconds    INTEGER,
                caller_city         VARCHAR(100),
                caller_state        VARCHAR(40),
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_inbound_call_status CHECK (
                    status IN ({statuses_sql})
                ),
                CONSTRAINT ck_inbound_call_disposition CHECK (
                    disposition IS NULL OR disposition IN ({dispositions_sql})
                )
            )
            """
        )
    )

    # Webhook retries deliver the same CallSid; the unique index is what makes
    # the inbound handler idempotent instead of duplicating the call.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_calls_provider_sid "
            "ON inbound_calls (provider_call_sid)"
        )
    )
    # "What came in today?" — the dominant read for the shop's call log.
    connection.execute(
        text(
            "CREATE INDEX ix_inbound_calls_created "
            "ON inbound_calls (created_at DESC)"
        )
    )
    # Contact timeline: this contact's inbound calls alongside outbound ones.
    connection.execute(
        text(
            "CREATE INDEX ix_inbound_calls_contact_time "
            "ON inbound_calls (contact_id, created_at DESC) "
            "WHERE contact_id IS NOT NULL"
        )
    )
    # Repeat-caller lookup + "did this number ever reach us?" by phone.
    connection.execute(
        text(
            "CREATE INDEX ix_inbound_calls_from_time "
            "ON inbound_calls (from_number, created_at DESC)"
        )
    )
