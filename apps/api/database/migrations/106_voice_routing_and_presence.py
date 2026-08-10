"""Inbound voice phase 2: ring the dashboard first, then a configurable number.

Two tables:

``voice_settings`` — a single-row config table for how inbound calls route.
Phase 1 read the destination from an env var, which meant changing where the
shop's phone rings required an ops person, a file edit, and a restart. This
makes it an admin setting, and lets the fallback be ANY number (a manager's
cell, an answering service) rather than being pinned to the office line on the
website.

Singleton is enforced by a CHECK on a fixed id, the same shape ``business_profile``
uses — one row, id = 1, no way to accidentally create a second config that
silently shadows the first.

``voice_presence`` — which reps currently have the dashboard softphone
registered. Inbound routing needs to answer "is anyone actually there?" BEFORE
it decides whether to ring browsers or go straight to the fallback number. A
heartbeat row per user is the cheapest honest answer: Twilio cannot tell us who
is online, and blindly ringing every rep's client would make every caller wait
out a 20-second timeout on an empty office.

Rows are upserted by the dashboard on an interval and treated as stale after a
short window (see ``voice_presence`` reads in the service), so a closed laptop
stops ringing on its own without needing a clean logout.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


# How inbound calls route. Stored rather than inferred so an admin can force
# the fallback (e.g. everyone's in a sales meeting) without logging out.
_MODES = (
    # Ring registered dashboard softphones; fall back to the number if nobody
    # is online or nobody answers in time. The default.
    "browser_then_fallback",
    # Skip browsers entirely; always ring the fallback number. This is phase 1
    # behaviour, kept as a one-click escape hatch.
    "fallback_only",
    # Ring browsers only; no PSTN fallback. Callers hear the unavailable
    # message if nobody picks up.
    "browser_only",
)


def upgrade(connection) -> None:
    modes_sql = ", ".join(f"'{m}'" for m in _MODES)
    connection.execute(
        text(
            f"""
            CREATE TABLE voice_settings (
                id                    INTEGER PRIMARY KEY DEFAULT 1,
                inbound_mode          VARCHAR(32) NOT NULL
                                        DEFAULT 'browser_then_fallback',
                -- Where calls go when no browser answers. NULL is legal and
                -- means "no PSTN fallback" — callers hear the unavailable
                -- message rather than being sent to a number nobody set.
                fallback_number       VARCHAR(20),
                -- Seconds to ring dashboard softphones before falling back.
                ring_timeout_seconds  INTEGER NOT NULL DEFAULT 20,
                -- Seconds the fallback number rings before giving up.
                fallback_timeout_seconds INTEGER NOT NULL DEFAULT 25,
                updated_by_user_id    INTEGER
                                        REFERENCES users(id) ON DELETE SET NULL,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_voice_settings_singleton CHECK (id = 1),
                CONSTRAINT ck_voice_settings_mode CHECK (
                    inbound_mode IN ({modes_sql})
                ),
                CONSTRAINT ck_voice_settings_ring_timeout CHECK (
                    ring_timeout_seconds BETWEEN 5 AND 120
                ),
                CONSTRAINT ck_voice_settings_fallback_timeout CHECK (
                    fallback_timeout_seconds BETWEEN 5 AND 120
                )
            )
            """
        )
    )

    # Seed the singleton. fallback_number is seeded from the phase-1 env value
    # at deploy time by the caller, not here — a migration must not depend on
    # process env, and a NULL fallback is a safe default (message, not a
    # misdirected call).
    connection.execute(text("INSERT INTO voice_settings (id) VALUES (1)"))

    connection.execute(
        text(
            """
            CREATE TABLE voice_presence (
                user_id      INTEGER PRIMARY KEY
                               REFERENCES users(id) ON DELETE CASCADE,
                -- Twilio client identity this user's browser registered as.
                identity     VARCHAR(64) NOT NULL,
                -- Heartbeat. Staleness, not an explicit logout, is what takes
                -- a rep out of the rotation, so a closed laptop self-heals.
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                -- Set false by an explicit "go offline" toggle so a rep can
                -- stop taking calls without closing the dashboard.
                available    BOOLEAN NOT NULL DEFAULT TRUE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # The routing hot path: "who is online right now?" on every inbound call.
    connection.execute(
        text(
            "CREATE INDEX ix_voice_presence_live "
            "ON voice_presence (last_seen_at DESC) WHERE available"
        )
    )

    # Phase 2 adds a conference per answered call; record which one, so hold
    # (a participant update) can find the caller's leg from the call row.
    connection.execute(
        text("ALTER TABLE inbound_calls ADD COLUMN conference_name VARCHAR(80)")
    )
    connection.execute(
        text(
            "CREATE INDEX ix_inbound_calls_conference "
            "ON inbound_calls (conference_name) WHERE conference_name IS NOT NULL"
        )
    )
