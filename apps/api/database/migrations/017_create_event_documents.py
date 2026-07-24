from sqlalchemy import text


def upgrade(connection) -> None:
    # Event documents: per-lead file uploads. Documents and invoices share
    # this table; `kind` discriminates. The four invoice_* columns are nullable
    # and only populated when kind='invoice', enforced by a CHECK constraint
    # so a stray UPDATE can't put an invoice amount on a contract row.
    connection.execute(
        text(
            """
            CREATE TABLE event_documents (
                id                   SERIAL PRIMARY KEY,
                event_id             INTEGER NOT NULL
                                     REFERENCES events(id) ON DELETE CASCADE,
                uploaded_by_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
                kind                 VARCHAR(16) NOT NULL,
                filename             VARCHAR(500) NOT NULL,
                content_type         VARCHAR(150) NOT NULL,
                byte_size            BIGINT NOT NULL,
                storage_key          VARCHAR(500) NOT NULL,
                label                VARCHAR(200),
                deleted_at           TIMESTAMPTZ,
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                invoice_amount_cents BIGINT,
                invoice_status       VARCHAR(16),
                invoice_issued_at    TIMESTAMPTZ,
                invoice_paid_at      TIMESTAMPTZ,

                CONSTRAINT chk_event_documents_kind CHECK (
                    kind IN ('document', 'invoice')
                ),
                CONSTRAINT chk_event_documents_invoice_status CHECK (
                    invoice_status IS NULL
                    OR invoice_status IN ('draft', 'sent', 'paid', 'void')
                ),
                CONSTRAINT chk_event_documents_invoice_fields_only_on_invoice CHECK (
                    kind = 'invoice'
                    OR (
                        invoice_amount_cents IS NULL
                        AND invoice_status IS NULL
                        AND invoice_issued_at IS NULL
                        AND invoice_paid_at IS NULL
                    )
                ),
                CONSTRAINT chk_event_documents_byte_size_nonneg CHECK (
                    byte_size >= 0
                )
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_documents_event_kind_deleted "
            "ON event_documents(event_id, kind, deleted_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_documents_event_created "
            "ON event_documents(event_id, created_at DESC)"
        )
    )
