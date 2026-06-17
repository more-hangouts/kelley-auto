from sqlalchemy import text


def upgrade(connection) -> None:
    # Canonical quotes table. Mirrors `invoices` but quotes do not carry money
    # state — they convert into a draft invoice that does. Signature columns
    # turn the quote into a contract: a signed quote IS the contract in this
    # shop, so we attach the signature to the quote itself rather than to the
    # invitation row (Invoice Ninja attaches it to invitations; we deviate so
    # the contract record survives invitation rotation).
    connection.execute(
        text(
            """
            CREATE TABLE quotes (
                id                          SERIAL PRIMARY KEY,
                event_id                    INTEGER NOT NULL
                                            REFERENCES events(id) ON DELETE RESTRICT,
                contact_id                  INTEGER NOT NULL
                                            REFERENCES contacts(id) ON DELETE RESTRICT,
                quote_number                VARCHAR(32) UNIQUE,
                status                      VARCHAR(16) NOT NULL DEFAULT 'draft',
                issue_date                  DATE NOT NULL DEFAULT CURRENT_DATE,
                expires_at                  DATE,
                subtotal_cents              BIGINT NOT NULL DEFAULT 0,
                discount_cents              BIGINT NOT NULL DEFAULT 0,
                tax_cents                   BIGINT NOT NULL DEFAULT 0,
                total_cents                 BIGINT NOT NULL DEFAULT 0,
                terms                       TEXT,
                footer                      TEXT,
                public_notes                TEXT,
                private_notes               TEXT,
                po_number                   VARCHAR(64),
                created_by_user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,

                sent_at                     TIMESTAMPTZ,
                viewed_at                   TIMESTAMPTZ,
                approved_at                 TIMESTAMPTZ,
                rejected_at                 TIMESTAMPTZ,
                rejection_reason            TEXT,
                converted_at                TIMESTAMPTZ,
                converted_invoice_id        INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
                cancelled_at                TIMESTAMPTZ,
                cancellation_reason         TEXT,

                -- Signature capture: when the customer signs in the portal
                -- (Phase 7) we store the base64 PNG of their pen strokes plus
                -- the moment, IP, and printed name. The quote is the contract
                -- record; PDF rendering renders the signature inline.
                signature_base64            TEXT,
                signature_signed_at         TIMESTAMPTZ,
                signature_ip                INET,
                signature_name              VARCHAR(120),

                revision                    INTEGER NOT NULL DEFAULT 1,
                last_pdf_rendered_revision  INTEGER,
                last_pdf_rendered_at        TIMESTAMPTZ,
                last_pdf_render_error       TEXT,
                deleted_at                  TIMESTAMPTZ,
                created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_quote_status CHECK (
                    status IN ('draft', 'sent', 'approved', 'rejected',
                               'converted', 'expired', 'cancelled')
                ),
                CONSTRAINT chk_quote_amounts_nonneg CHECK (
                    subtotal_cents >= 0
                    AND tax_cents >= 0
                    AND total_cents >= 0
                ),
                CONSTRAINT chk_quote_revision_pos CHECK (revision >= 1),
                CONSTRAINT chk_quote_number_when_not_draft CHECK (
                    status = 'draft' OR quote_number IS NOT NULL
                ),
                -- A signature is a paired thing: stroke data without a
                -- timestamp would be a half-finished record. Either both are
                -- present or both are NULL.
                CONSTRAINT chk_quote_signature_paired CHECK (
                    (signature_base64 IS NULL AND signature_signed_at IS NULL)
                    OR (signature_base64 IS NOT NULL AND signature_signed_at IS NOT NULL)
                ),
                -- approved status requires a captured signature; signing IS
                -- approval. Rejection / conversion / cancellation do not.
                CONSTRAINT chk_quote_approved_has_signature CHECK (
                    status <> 'approved' OR signature_signed_at IS NOT NULL
                ),
                -- converted status requires a back-pointer to the invoice it
                -- became, and vice versa. Rejecting then later converting is
                -- not a supported flow — converted is a terminal state.
                CONSTRAINT chk_quote_converted_consistent CHECK (
                    (status = 'converted') = (converted_invoice_id IS NOT NULL)
                )
            )
            """
        )
    )

    connection.execute(
        text(
            "CREATE INDEX idx_quotes_event_status_deleted "
            "ON quotes(event_id, status, deleted_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_quotes_contact_status_deleted "
            "ON quotes(contact_id, status, deleted_at)"
        )
    )
    # Powers the Phase 11 quote-expiry sweep: WHERE status='sent' AND
    # expires_at < CURRENT_DATE. A partial index keyed on the relevant
    # status keeps the sweep cheap regardless of total quote volume.
    connection.execute(
        text(
            """
            CREATE INDEX idx_quotes_expiring_sent
            ON quotes(expires_at)
            WHERE deleted_at IS NULL AND status = 'sent'
            """
        )
    )
