from sqlalchemy import text


def upgrade(connection) -> None:
    # Public-portal access token per (quote, contact). public_key is the
    # secret in /portal/quote/<key>. Same lifecycle gates as
    # invoice_invitations — the three NULL/expiry checks let staff rotate or
    # kill a leaked link without dropping the underlying quote.
    connection.execute(
        text(
            """
            CREATE TABLE quote_invitations (
                id                   SERIAL PRIMARY KEY,
                quote_id             INTEGER NOT NULL
                                     REFERENCES quotes(id) ON DELETE CASCADE,
                contact_id           INTEGER NOT NULL
                                     REFERENCES contacts(id) ON DELETE CASCADE,
                public_key           VARCHAR(64) NOT NULL UNIQUE,
                sent_at              TIMESTAMPTZ,
                last_resent_at       TIMESTAMPTZ,
                viewed_at            TIMESTAMPTZ,
                last_viewed_at       TIMESTAMPTZ,
                view_count           INTEGER NOT NULL DEFAULT 0,
                email_opened_at      TIMESTAMPTZ,
                expires_at           TIMESTAMPTZ,
                revoked_at           TIMESTAMPTZ,
                revoked_by_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                deleted_at           TIMESTAMPTZ,
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_quote_invitation_view_count_nonneg CHECK (view_count >= 0),
                CONSTRAINT uq_quote_invitation_quote_contact UNIQUE (quote_id, contact_id)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_quote_invitations_quote_deleted "
            "ON quote_invitations(quote_id, deleted_at)"
        )
    )
