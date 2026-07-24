"""Native-dialer call-attempt tracking (Phase 7).

When a signed-in salesperson taps a customer's phone number in the dashboard
on their phone, the client logs the attempt HERE *before* handing off to the
device dialer (``tel:``). This is NOT Twilio Voice — there is no telephony
routing, no recording, no VoIP. The row records who called whom, when, from
which screen, and (later, salesperson-reported) how the call went.

Why a dedicated table rather than ``activity_log`` / ``sales_activity_events``
(both audited as reuse candidates first):

  * Both are strictly append-only audit streams; a call attempt's OUTCOME
    transitions in place (call_initiated -> connected/voicemail/no_answer/…),
    which those stores cannot represent without N rows per call.
  * ``activity_log.event_id`` is NOT NULL; a call attempt is contact-anchored
    with an OPTIONAL deal — the inverse. (Migration 091 split out
    ``sales_activity_events`` for this same reason.)
  * Neither has an idempotency key; native-dialer double-taps need a UNIQUE
    guard.
  * Both deliberately forbid note bodies / PII in metadata; this table needs
    free-text notes and the E.164 number.

Deletion resilience mirrors ``activity_log``: the salesperson FK is
ON DELETE SET NULL with a write-time ``salesperson_display_name`` snapshot so
manager reports survive a rep leaving. ``contact_id`` is ON DELETE CASCADE as
a hard-delete backstop only — CRM archive is a soft ``deleted_at`` (migration
080), so archiving a contact PRESERVES its call history, as required.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


# Terminal + initial outcomes. call_initiated is the pre-outcome state the row
# is born in; the rest are salesperson-reported. Kept in sync with the service
# CALL_OUTCOMES allowlist and the API's OUTCOME literal.
_OUTCOMES = (
    "call_initiated",
    "connected",
    "left_voicemail",
    "no_answer",
    "busy",
    "wrong_number",
    "cancelled",
)


def upgrade(connection) -> None:
    outcomes_sql = ", ".join(f"'{o}'" for o in _OUTCOMES)
    connection.execute(
        text(
            f"""
            CREATE TABLE contact_call_attempts (
                id                       BIGSERIAL PRIMARY KEY,
                contact_id               INTEGER NOT NULL
                                           REFERENCES contacts(id) ON DELETE CASCADE,
                salesperson_user_id      INTEGER
                                           REFERENCES users(id) ON DELETE SET NULL,
                -- Write-time snapshot so manager reports stay attributable
                -- even after the rep's user row is deleted (FK nulled).
                salesperson_display_name VARCHAR(200),
                event_id                 INTEGER
                                           REFERENCES events(id) ON DELETE SET NULL,
                phone_e164               VARCHAR(20) NOT NULL,
                outcome                  VARCHAR(20) NOT NULL DEFAULT 'call_initiated',
                -- true while the row sits at call_initiated awaiting a
                -- salesperson-reported outcome (sheet dismissed / not yet
                -- filled). Flipped false once any real outcome is recorded.
                outcome_pending          BOOLEAN NOT NULL DEFAULT TRUE,
                notes                    TEXT,
                -- The screen the call was launched from (e.g.
                -- 'contact_detail', 'event_quick_view').
                source                   VARCHAR(40),
                -- Client-supplied dedup key for double-tap protection.
                idempotency_key          VARCHAR(64),
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_call_attempt_outcome CHECK (
                    outcome IN ({outcomes_sql})
                ),
                -- call_initiated <=> pending; a real outcome clears the flag.
                CONSTRAINT ck_call_attempt_pending_consistency CHECK (
                    (outcome = 'call_initiated') = outcome_pending
                )
            )
            """
        )
    )

    # Contact timeline: "show this contact's call attempts, newest first."
    connection.execute(
        text(
            "CREATE INDEX ix_call_attempts_contact_time "
            "ON contact_call_attempts (contact_id, created_at DESC)"
        )
    )
    # Manager reporting: "what did rep N call today?" — the dominant rollup.
    connection.execute(
        text(
            "CREATE INDEX ix_call_attempts_rep_time "
            "ON contact_call_attempts (salesperson_user_id, created_at DESC)"
        )
    )
    # Double-tap idempotency. Partial: only rows that carry a key participate,
    # so legacy/keyless inserts are never blocked.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_call_attempts_idempotency_key "
            "ON contact_call_attempts (idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )
    )
