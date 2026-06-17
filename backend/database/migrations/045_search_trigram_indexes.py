from sqlalchemy import text


def upgrade(connection) -> None:
    # Global Search Phase 1: trigram + unaccent infrastructure for the
    # /api/search endpoint.
    #
    # `pg_trgm` gives substring + fuzzy matching for the as-you-type
    # palette UI. `unaccent` makes "hernandez" reliably match "Hernández"
    # for Bellas's heavily Spanish-named customer base.
    #
    # The bundled `unaccent()` is marked STABLE, not IMMUTABLE, which
    # means Postgres rejects it inside expression indexes. The standard
    # workaround is a thin SQL wrapper marked IMMUTABLE that invokes the
    # default `unaccent` dictionary by name. Once we have an IMMUTABLE
    # wrapper, the GIN expression indexes can be built on
    # f_unaccent(lower(col)) and the runtime queries match the same
    # expression so the planner picks the index instead of seq-scanning.
    #
    # Critical contract: every query against these columns must apply
    # f_unaccent(lower(...)) on both sides. services/search_service.py
    # centralizes the call so the index expression and runtime expression
    # cannot drift.
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))

    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
              AS $$ SELECT public.unaccent('public.unaccent', $1) $$
              LANGUAGE SQL IMMUTABLE PARALLEL SAFE
            """
        )
    )

    # Trigram GIN indexes on (unaccent + lower) of each searchable column.
    # phone_e164 is digits-only by construction (see
    # booking_service.normalize_phone_e164) so it skips the unaccent
    # wrapper. Trigram is still useful there because users type partial
    # phones and the search service does substring matching after
    # stripping non-digits from the query.
    connection.execute(
        text(
            "CREATE INDEX contacts_display_name_trgm "
            "ON contacts USING gin (f_unaccent(lower(display_name)) gin_trgm_ops)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX contacts_email_trgm "
            "ON contacts USING gin (f_unaccent(lower(email)) gin_trgm_ops) "
            "WHERE email IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX contacts_phone_e164_trgm "
            "ON contacts USING gin (phone_e164 gin_trgm_ops) "
            "WHERE phone_e164 IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX events_event_name_trgm "
            "ON events USING gin (f_unaccent(lower(event_name)) gin_trgm_ops)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX events_quince_theme_trgm "
            "ON events USING gin (f_unaccent(lower(quince_theme)) gin_trgm_ops) "
            "WHERE quince_theme IS NOT NULL"
        )
    )
