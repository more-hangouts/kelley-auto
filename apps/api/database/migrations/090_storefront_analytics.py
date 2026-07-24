"""First-party storefront analytics + CAPI-ready conversion model.

Kelley wants to see each shopper's path to a lead ("viewed these vehicles,
came from this source, converted after N minutes") using tables Kelley owns —
no third-party analytics dependency — and to reuse that same clean event
stream for Meta Conversions API attribution when ads begin.

Everything here is additive (five brand-new tables, no change to any existing
row/behavior). The design mirrors the proven ``appointment_visitors`` /
``appointment_session_events`` machinery built for the booking widget, but
pointed at the vehicle storefront and joined to a ``vehicle_sale`` deal at
conversion:

  - ``storefront_visitors``  — one row per anonymous browser (first-party
    ``ka_vid`` cookie). Opaque ``visitor_key``; no raw PII. First/last-touch
    attribution kept as JSONB for source reporting.
  - ``storefront_sessions``  — one row per visit (first-party ``ka_sid``
    cookie). Landing page, initial referrer/UTM, user agent, and a *hashed*
    IP (never the raw IP) for the visit.
  - ``storefront_events``    — the behavioral stream: page_view, vehicle_view,
    lead_form_opened/started, lead_submitted. ``event_id`` is the CAPI dedup
    id (unique when present) so a browser Pixel event and its server-side CAPI
    twin collapse to one conversion.
  - ``lead_attribution``     — 1:1 with a ``events`` (deal) row. The bridge
    from anonymous browsing to the CRM deal: which visitor/session converted,
    the landing page/source/UTM, and the ``_fbp``/``_fbc`` Meta cookies needed
    later for CAPI matching. NO BHPH application PII ever lands here.
  - ``ad_conversion_events`` — provider-neutral OUTBOUND queue (Phase 3). The
    table exists now so the data model is ready; the Meta sender and the
    enqueue-on-conversion wiring stay OFF until a Pixel ID, access token, test
    event code, and consent language are in place. ``status`` gates delivery
    (pending/sent/failed/suppressed).

Privacy invariants enforced by shape: analytics tables carry no DOB/DL/SSN/
application address, no raw IP (only ``ip_hash``), and the outbound queue's
``user_data`` is reserved for SERVER-HASHED identifiers only.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # --- storefront_visitors -------------------------------------------------
    connection.execute(
        text(
            """
            CREATE TABLE storefront_visitors (
                id                       SERIAL PRIMARY KEY,
                visitor_key              VARCHAR(64) NOT NULL UNIQUE,
                first_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                first_touch_attribution  JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_touch_attribution   JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # --- storefront_sessions -------------------------------------------------
    connection.execute(
        text(
            """
            CREATE TABLE storefront_sessions (
                id                SERIAL PRIMARY KEY,
                visitor_id        INTEGER NOT NULL
                                    REFERENCES storefront_visitors(id) ON DELETE CASCADE,
                session_key       VARCHAR(64) NOT NULL UNIQUE,
                started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                landing_page      TEXT,
                initial_referrer  TEXT,
                initial_utm       JSONB NOT NULL DEFAULT '{}'::jsonb,
                user_agent        TEXT,
                ip_hash           VARCHAR(64),
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_storefront_sessions_visitor_id "
            "ON storefront_sessions (visitor_id)"
        )
    )

    # --- storefront_events ---------------------------------------------------
    # `event_id` is the CAPI dedup id: UNIQUE when present, but many events
    # (e.g. page_view) carry none, so the partial unique index skips NULLs.
    connection.execute(
        text(
            """
            CREATE TABLE storefront_events (
                id                       BIGSERIAL PRIMARY KEY,
                visitor_id               INTEGER
                                           REFERENCES storefront_visitors(id) ON DELETE SET NULL,
                session_id               INTEGER
                                           REFERENCES storefront_sessions(id) ON DELETE SET NULL,
                event_name               VARCHAR(50) NOT NULL,
                event_id                 VARCHAR(64),
                path                     TEXT,
                referrer                 TEXT,
                utm                      JSONB NOT NULL DEFAULT '{}'::jsonb,
                listing_code             VARCHAR(40),
                vehicle_catalog_item_id  INTEGER
                                           REFERENCES catalog_items(id) ON DELETE SET NULL,
                metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX ux_storefront_events_event_id "
            "ON storefront_events (event_id) WHERE event_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_storefront_events_visitor_id "
            "ON storefront_events (visitor_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_storefront_events_session_id "
            "ON storefront_events (session_id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_storefront_events_vehicle "
            "ON storefront_events (vehicle_catalog_item_id) "
            "WHERE vehicle_catalog_item_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_storefront_events_name_time "
            "ON storefront_events (event_name, occurred_at)"
        )
    )

    # --- lead_attribution ----------------------------------------------------
    # 1:1 with a deal (event_id UNIQUE). On a repeat submission the row is
    # updated in place — one attribution per deal, first-touch preserved.
    connection.execute(
        text(
            """
            CREATE TABLE lead_attribution (
                id                              SERIAL PRIMARY KEY,
                event_id                        INTEGER NOT NULL UNIQUE
                                                  REFERENCES events(id) ON DELETE CASCADE,
                visitor_id                      INTEGER
                                                  REFERENCES storefront_visitors(id) ON DELETE SET NULL,
                session_id                      INTEGER
                                                  REFERENCES storefront_sessions(id) ON DELETE SET NULL,
                conversion_storefront_event_id  BIGINT
                                                  REFERENCES storefront_events(id) ON DELETE SET NULL,
                landing_page                    TEXT,
                source_page                     TEXT,
                utm                             JSONB NOT NULL DEFAULT '{}'::jsonb,
                referrer                        TEXT,
                -- Meta browser cookies, captured for later CAPI matching. These
                -- are pseudonymous ad identifiers, NOT application PII.
                fbp                             VARCHAR(255),
                fbc                             VARCHAR(255),
                created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_lead_attribution_visitor_id "
            "ON lead_attribution (visitor_id)"
        )
    )

    # --- ad_conversion_events (Phase 3 outbound queue — sender OFF) -----------
    # Provider-neutral. Created now so the data model is CAPI-ready; nothing
    # enqueues or sends until META_CAPI_ENABLED is flipped and the sender is
    # built. `user_data` holds SERVER-HASHED identifiers only — never raw PII.
    connection.execute(
        text(
            """
            CREATE TABLE ad_conversion_events (
                id             BIGSERIAL PRIMARY KEY,
                provider       VARCHAR(20) NOT NULL DEFAULT 'meta',
                event_name     VARCHAR(50) NOT NULL,
                event_id       VARCHAR(64),
                event_time     TIMESTAMPTZ NOT NULL,
                source_url     TEXT,
                action_source  VARCHAR(20) NOT NULL DEFAULT 'website',
                user_data      JSONB NOT NULL DEFAULT '{}'::jsonb,
                custom_data    JSONB NOT NULL DEFAULT '{}'::jsonb,
                status         VARCHAR(20) NOT NULL DEFAULT 'pending',
                attempt_count  INTEGER NOT NULL DEFAULT 0,
                last_error     TEXT,
                lead_event_id  INTEGER REFERENCES events(id) ON DELETE SET NULL,
                sent_at        TIMESTAMPTZ,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX ix_ad_conversion_events_status "
            "ON ad_conversion_events (status, created_at)"
        )
    )
