"""Normalized channel attribution for storefront analytics (Sprint 3).

Ports the catering210 ``derive_source`` design onto migration 090's tables:
adds ``source`` / ``medium`` / ``click_id`` columns to ``storefront_events``
and ``lead_attribution`` so channel reporting is a plain GROUP BY instead of
digging through UTM JSONB, then backfills existing rows with the same
priority ladder the service now applies at write time:

    explicit UTM  →  ad click-id (fbclid/gclid/msclkid)  →  referrer host  →  NULL

A NULL source is honest "(direct)/unknown" — never fabricated. The backfill
mirrors ``services/storefront_analytics_service.derive_source`` closely
enough for historical reporting; rows written after this migration get the
full Python ladder (including in-app-browser orphan recovery).
"""

from sqlalchemy import text

# Referrer-host → (source, medium) classification, applied only where no UTM
# or click-id already decided the channel. Substring match on the referrer.
_REFERRER_RULES: tuple[tuple[str, str, str], ...] = (
    ("facebook.com", "facebook", "referral"),
    ("fb.com", "facebook", "referral"),
    ("instagram.com", "instagram", "referral"),
    ("google.", "google", "organic"),
    ("bing.com", "bing", "organic"),
    ("yahoo.com", "yahoo", "organic"),
    ("duckduckgo.com", "duckduckgo", "organic"),
    ("tiktok.com", "tiktok", "referral"),
    ("youtube.com", "youtube", "referral"),
    ("yelp.com", "yelp", "referral"),
    ("nextdoor.com", "nextdoor", "referral"),
    ("craigslist.org", "craigslist", "referral"),
    ("cargurus.com", "cargurus", "referral"),
    ("autotrader.com", "autotrader", "referral"),
    ("cars.com", "cars.com", "referral"),
    ("offerup.com", "offerup", "referral"),
)

# Click-id URL param → (source, default medium). fbclid wins ties by order.
_CLICK_RULES: tuple[tuple[str, str], ...] = (
    ("fbclid", "facebook"),
    ("gclid", "google"),
    ("msclkid", "bing"),
)


def upgrade(connection) -> None:
    for table in ("storefront_events", "lead_attribution"):
        connection.execute(
            text(
                f"""
                ALTER TABLE {table}
                    ADD COLUMN source   VARCHAR(120),
                    ADD COLUMN medium   VARCHAR(120),
                    ADD COLUMN click_id VARCHAR(255)
                """
            )
        )

    connection.execute(
        text(
            "CREATE INDEX ix_storefront_events_source_time "
            "ON storefront_events (source, occurred_at)"
        )
    )

    # ---- backfill: rung 1 — explicit UTM ------------------------------------
    for table in ("storefront_events", "lead_attribution"):
        connection.execute(
            text(
                f"""
                UPDATE {table}
                   SET source = LEFT(LOWER(BTRIM(utm->>'source')), 120),
                       medium = LEFT(LOWER(BTRIM(utm->>'medium')), 120)
                 WHERE COALESCE(BTRIM(utm->>'source'), '') <> ''
                """
            )
        )

    # ---- rung 2 — click ids embedded in stored URLs -------------------------
    # storefront_events keeps the full path+query; lead_attribution keeps the
    # landing page. A paid click with no UTMs still carries its click-id there.
    for param, source in _CLICK_RULES:
        connection.execute(
            text(
                f"""
                UPDATE storefront_events
                   SET click_id = COALESCE(click_id,
                                           LEFT(SUBSTRING(path FROM '{param}=([^&#]+)'), 255)),
                       source = COALESCE(source, '{source}'),
                       medium = COALESCE(medium, 'paid')
                 WHERE path LIKE '%{param}=%'
                """
            )
        )
        connection.execute(
            text(
                f"""
                UPDATE lead_attribution
                   SET click_id = COALESCE(click_id,
                                           LEFT(SUBSTRING(landing_page FROM '{param}=([^&#]+)'), 255)),
                       source = COALESCE(source, '{source}'),
                       medium = COALESCE(medium, 'paid')
                 WHERE landing_page LIKE '%{param}=%'
                """
            )
        )

    # A stored Meta ``_fbc`` cookie is proof of a Facebook ad click even when
    # the URL was lost (in-app browser). Format: fb.1.<timestamp>.<fbclid>.
    connection.execute(
        text(
            r"""
            UPDATE lead_attribution
               SET click_id = COALESCE(click_id,
                                       LEFT(SUBSTRING(fbc FROM '^fb\.[0-9]+\.[0-9]+\.(.+)$'), 255)),
                   source = COALESCE(source, 'facebook'),
                   medium = COALESCE(medium, 'paid')
             WHERE fbc IS NOT NULL AND BTRIM(fbc) <> ''
            """
        )
    )

    # ---- rung 3 — referrer host classification ------------------------------
    for needle, source, medium in _REFERRER_RULES:
        connection.execute(
            text(
                f"""
                UPDATE storefront_events
                   SET source = '{source}', medium = COALESCE(medium, '{medium}')
                 WHERE source IS NULL AND referrer ILIKE '%{needle}%'
                """
            )
        )
        connection.execute(
            text(
                f"""
                UPDATE lead_attribution
                   SET source = '{source}', medium = COALESCE(medium, '{medium}')
                 WHERE source IS NULL AND referrer ILIKE '%{needle}%'
                """
            )
        )
