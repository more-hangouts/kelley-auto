from sqlalchemy import text


def upgrade(connection) -> None:
    # Public-portal access token per (invoice, contact). public_key is the
    # secret in /portal/invoice/<key>. The three gates — deleted_at IS NULL,
    # revoked_at IS NULL, expires_at IS NULL OR expires_at > NOW() — let staff
    # rotate or kill a leaked link without dropping the underlying invoice.
    connection.execute(
        text(
            """
            CREATE TABLE invoice_invitations (
                id                   SERIAL PRIMARY KEY,
                invoice_id           INTEGER NOT NULL
                                     REFERENCES invoices(id) ON DELETE CASCADE,
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

                CONSTRAINT chk_invitation_view_count_nonneg CHECK (view_count >= 0),
                CONSTRAINT uq_invitation_invoice_contact UNIQUE (invoice_id, contact_id)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoice_invitations_invoice_deleted "
            "ON invoice_invitations(invoice_id, deleted_at)"
        )
    )
