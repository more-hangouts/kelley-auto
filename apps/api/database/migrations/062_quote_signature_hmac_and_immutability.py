"""Phase C3: HMAC stamp + immutability for quote signatures.

Three layered protections for already-signed evidentiary records:

  1. ``signature_hmac VARCHAR(64)`` — HMAC-SHA256 over the canonical
     signed payload (quote identity + stable business terms + image
     SHA-256), computed via ``services.quote_signature_hmac``. Stamped
     once at sign time and bound by trigger 3 below.
  2. ``CHECK chk_quotes_signature_hmac_required`` — enforces "any row
     with ``signature_signed_at`` set must also carry a non-null
     ``signature_hmac``". Added *after* backfill so the migration
     fails cleanly on any unstampable row instead of after a partial
     deploy.
  3. ``trg_quote_signature_immutable`` — BEFORE UPDATE OF on the six
     signature columns. Once any signature field is non-null, the
     trigger raises if a subsequent UPDATE would change it.

Ordering is deliberate:

  - add column nullable
  - backfill (requires ``QUOTE_SIGNATURE_KEY``)
  - add CHECK (validates the backfill)
  - add trigger (locks in the data going forward)

A bug in canonicalisation would brand all signed rows with bad HMACs
forever once the trigger is in place. The pre-flight check in
``test_quote_signature_hmac_smoke.py`` exercises the canonical
payload on a fresh quote; the C3 commit message documents that an
out-of-band DB backup precedes the migration.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- 1. Add column ----
    connection.execute(
        text(
            """
            ALTER TABLE quotes
                ADD COLUMN signature_hmac VARCHAR(64) NULL
            """
        )
    )

    # ---- 2. Backfill any already-signed rows ----
    signed_count = connection.execute(
        text("SELECT COUNT(*) FROM quotes WHERE signature_signed_at IS NOT NULL")
    ).scalar() or 0

    if signed_count > 0:
        # Lazy import: an env without QUOTE_SIGNATURE_KEY but no signed
        # rows can still apply the migration (e.g. fresh dev DB). When
        # there are signed rows the import surfaces the
        # QuoteSignatureHMACUnconfigured error — which is what we want,
        # because a CHECK constraint right below would fail anyway.
        from services.quote_signature_hmac import compute_hmac  # noqa: PLC0415

        rows = connection.execute(
            text(
                """
                SELECT id, quote_number, event_id, contact_id,
                       subtotal_cents, discount_cents, tax_cents, total_cents,
                       signature_base64, signature_signed_at, signature_name,
                       signature_ip, signature_user_agent
                FROM quotes
                WHERE signature_signed_at IS NOT NULL
                  AND signature_hmac IS NULL
                """
            )
        ).all()

        for row in rows:
            hmac_hex = compute_hmac(row)
            connection.execute(
                text(
                    "UPDATE quotes SET signature_hmac = :h WHERE id = :i"
                ),
                {"h": hmac_hex, "i": row.id},
            )

        # Confirm backfill landed on every row before locking it in.
        missing = connection.execute(
            text(
                "SELECT COUNT(*) FROM quotes "
                "WHERE signature_signed_at IS NOT NULL AND signature_hmac IS NULL"
            )
        ).scalar() or 0
        assert missing == 0, (
            f"{missing} signed quote(s) still missing signature_hmac after backfill"
        )

    # ---- 3. CHECK: signed rows must carry an HMAC ----
    connection.execute(
        text(
            """
            ALTER TABLE quotes
                ADD CONSTRAINT chk_quotes_signature_hmac_required
                CHECK (signature_signed_at IS NULL OR signature_hmac IS NOT NULL)
            """
        )
    )

    # ---- 4. Immutability trigger ----
    # Mirrors the pattern from migration 044 (catalog public_code).
    # Each guarded column has its own IS NOT NULL / IS DISTINCT FROM gate
    # so that the first transition from null → signed is allowed and any
    # subsequent change to a non-null value (including null-ing it out)
    # raises. The trigger fires only when one of the listed columns is
    # actually being updated, so unrelated UPDATEs (e.g. bumping
    # `updated_at`, cancelling the quote, recording PDF render state)
    # do not pay the trigger cost.
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION prevent_quote_signature_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.signature_base64 IS NOT NULL
                   AND NEW.signature_base64 IS DISTINCT FROM OLD.signature_base64 THEN
                    RAISE EXCEPTION 'quotes.signature_base64 is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                IF OLD.signature_signed_at IS NOT NULL
                   AND NEW.signature_signed_at IS DISTINCT FROM OLD.signature_signed_at THEN
                    RAISE EXCEPTION 'quotes.signature_signed_at is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                IF OLD.signature_ip IS NOT NULL
                   AND NEW.signature_ip IS DISTINCT FROM OLD.signature_ip THEN
                    RAISE EXCEPTION 'quotes.signature_ip is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                IF OLD.signature_name IS NOT NULL
                   AND NEW.signature_name IS DISTINCT FROM OLD.signature_name THEN
                    RAISE EXCEPTION 'quotes.signature_name is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                IF OLD.signature_user_agent IS NOT NULL
                   AND NEW.signature_user_agent IS DISTINCT FROM OLD.signature_user_agent THEN
                    RAISE EXCEPTION 'quotes.signature_user_agent is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                IF OLD.signature_hmac IS NOT NULL
                   AND NEW.signature_hmac IS DISTINCT FROM OLD.signature_hmac THEN
                    RAISE EXCEPTION 'quotes.signature_hmac is immutable once signed'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'quote_signature_immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    connection.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_quote_signature_immutable ON quotes
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TRIGGER trg_quote_signature_immutable
            BEFORE UPDATE OF
                signature_base64,
                signature_signed_at,
                signature_ip,
                signature_name,
                signature_user_agent,
                signature_hmac
            ON quotes
            FOR EACH ROW
            EXECUTE FUNCTION prevent_quote_signature_mutation()
            """
        )
    )
