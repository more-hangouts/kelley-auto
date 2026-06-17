from sqlalchemy import text


def upgrade(connection) -> None:
    # Canonical invoices table. Money state lives here. The four PDF-render
    # columns (last_pdf_*) cache the WeasyPrint output state so Phase 8 can
    # show a Retry render button when generation fails. legacy_migration_run_id
    # is null for native invoices and stamped only by Phase 4b imports so the
    # rollback step can DELETE WHERE legacy_migration_run_id = '<run-uuid>'.
    connection.execute(
        text(
            """
            CREATE TABLE invoices (
                id                          SERIAL PRIMARY KEY,
                event_id                    INTEGER NOT NULL
                                            REFERENCES events(id) ON DELETE RESTRICT,
                contact_id                  INTEGER NOT NULL
                                            REFERENCES contacts(id) ON DELETE RESTRICT,
                invoice_number              VARCHAR(32) UNIQUE,
                status                      VARCHAR(16) NOT NULL DEFAULT 'draft',
                issue_date                  DATE NOT NULL DEFAULT CURRENT_DATE,
                due_date                    DATE,
                subtotal_cents              BIGINT NOT NULL DEFAULT 0,
                discount_cents              BIGINT NOT NULL DEFAULT 0,
                tax_cents                   BIGINT NOT NULL DEFAULT 0,
                total_cents                 BIGINT NOT NULL DEFAULT 0,
                paid_to_date_cents          BIGINT NOT NULL DEFAULT 0,
                balance_cents               BIGINT NOT NULL DEFAULT 0,
                terms                       TEXT,
                footer                      TEXT,
                public_notes                TEXT,
                private_notes               TEXT,
                po_number                   VARCHAR(64),
                created_by_user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
                sent_at                     TIMESTAMPTZ,
                viewed_at                   TIMESTAMPTZ,
                paid_at                     TIMESTAMPTZ,
                cancelled_at                TIMESTAMPTZ,
                cancellation_reason         TEXT,
                revision                    INTEGER NOT NULL DEFAULT 1,
                last_pdf_rendered_revision  INTEGER,
                last_pdf_rendered_at        TIMESTAMPTZ,
                last_pdf_render_error       TEXT,
                legacy_migration_run_id     UUID,
                deleted_at                  TIMESTAMPTZ,
                created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_invoice_status CHECK (
                    status IN ('draft', 'sent', 'partial', 'paid', 'cancelled', 'reversed')
                ),
                CONSTRAINT chk_invoice_amounts_nonneg CHECK (
                    subtotal_cents >= 0
                    AND tax_cents >= 0
                    AND total_cents >= 0
                    AND paid_to_date_cents >= 0
                ),
                CONSTRAINT chk_invoice_paid_le_total CHECK (
                    paid_to_date_cents <= total_cents
                ),
                CONSTRAINT chk_invoice_balance_consistent CHECK (
                    balance_cents = total_cents - paid_to_date_cents
                ),
                CONSTRAINT chk_invoice_revision_pos CHECK (revision >= 1),
                CONSTRAINT chk_invoice_number_when_not_draft CHECK (
                    status = 'draft' OR invoice_number IS NOT NULL
                )
            )
            """
        )
    )

    connection.execute(
        text(
            "CREATE INDEX idx_invoices_event_status_deleted "
            "ON invoices(event_id, status, deleted_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoices_contact_status_deleted "
            "ON invoices(contact_id, status, deleted_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_invoices_status_due_date "
            "ON invoices(status, due_date)"
        )
    )
    # Partial index that powers the AR rollup query: SUM(balance_cents)
    # WHERE status IN ('sent','partial') AND deleted_at IS NULL. Covering the
    # actual column queried keeps the rollup cheap as the table grows.
    connection.execute(
        text(
            """
            CREATE INDEX idx_invoices_outstanding_ar
            ON invoices(balance_cents)
            WHERE deleted_at IS NULL AND status IN ('sent', 'partial')
            """
        )
    )
