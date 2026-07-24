from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 4a: schema-compatible split between document attachments and
    # canonical invoices. This migration teaches event_documents to hold
    # external invoice PDFs (kind='external_invoice') and to optionally link
    # back to a canonical invoices.id row, without moving any data yet.
    #
    # Legacy `kind='invoice'` rows are intentionally still allowed during the
    # rollback season. Phase 4b reclassifies each one to 'external_invoice'
    # and populates `linked_invoice_id`. Phase 13 drops the four `invoice_*`
    # columns and removes 'invoice' from the kind set.

    # 1. New nullable FK to canonical invoices. ON DELETE SET NULL so a
    #    canonical invoice deletion does not cascade-wipe the attached PDF;
    #    the file row survives as a record-only document.
    connection.execute(
        text(
            """
            ALTER TABLE event_documents
                ADD COLUMN linked_invoice_id INTEGER
                    REFERENCES invoices(id) ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_event_documents_linked_invoice "
            "ON event_documents(linked_invoice_id) "
            "WHERE linked_invoice_id IS NOT NULL"
        )
    )

    # 2. Broaden the kind enum to include 'external_invoice'. Keep 'invoice'
    #    in the allowed set for the duration of Phase 4 so existing rows do
    #    not violate the constraint mid-migration.
    connection.execute(
        text("ALTER TABLE event_documents DROP CONSTRAINT chk_event_documents_kind")
    )
    connection.execute(
        text(
            """
            ALTER TABLE event_documents
                ADD CONSTRAINT chk_event_documents_kind CHECK (
                    kind IN ('document', 'invoice', 'external_invoice')
                )
            """
        )
    )

    # 3. Allow the four legacy invoice_* columns to be populated on either
    #    'invoice' (legacy rows during rollback season) or 'external_invoice'
    #    (rows reclassified by Phase 4b). All other kinds must keep these
    #    columns NULL — a stray UPDATE on a contract row still cannot smuggle
    #    a money amount in. Phase 13 drops the columns entirely.
    connection.execute(
        text(
            "ALTER TABLE event_documents "
            "DROP CONSTRAINT chk_event_documents_invoice_fields_only_on_invoice"
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE event_documents
                ADD CONSTRAINT chk_event_documents_invoice_fields_only_on_invoice CHECK (
                    kind IN ('invoice', 'external_invoice')
                    OR (
                        invoice_amount_cents IS NULL
                        AND invoice_status IS NULL
                        AND invoice_issued_at IS NULL
                        AND invoice_paid_at IS NULL
                    )
                )
            """
        )
    )

    # 4. linked_invoice_id may only be populated when the file represents an
    #    external invoice attachment. A 'document' row (contract, photo, etc.)
    #    must not point at an invoice; a legacy 'invoice' row also keeps the
    #    new FK NULL until Phase 4b reclassifies it.
    connection.execute(
        text(
            """
            ALTER TABLE event_documents
                ADD CONSTRAINT chk_event_documents_linked_invoice_only_on_external CHECK (
                    linked_invoice_id IS NULL
                    OR kind = 'external_invoice'
                )
            """
        )
    )
