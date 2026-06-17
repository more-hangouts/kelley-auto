from sqlalchemy import text


def upgrade(connection) -> None:
    # Phase 7: staff needs to be able to issue a fresh invitation for the
    # same contact after revoking a leaked link. The original constraint
    # uq_invitation_invoice_contact UNIQUE (invoice_id, contact_id) was
    # too strict — a revoked row blocked the rotation. Replace it with a
    # partial unique index that only applies to LIVE rows so revoked /
    # soft-deleted rows can stay in the table as audit history while the
    # new invitation slots in cleanly.
    connection.execute(
        text(
            "ALTER TABLE invoice_invitations "
            "DROP CONSTRAINT IF EXISTS uq_invitation_invoice_contact"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_invitation_invoice_contact_live "
            "ON invoice_invitations (invoice_id, contact_id) "
            "WHERE deleted_at IS NULL AND revoked_at IS NULL"
        )
    )

    connection.execute(
        text(
            "ALTER TABLE quote_invitations "
            "DROP CONSTRAINT IF EXISTS uq_quote_invitation_quote_contact"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_quote_invitation_quote_contact_live "
            "ON quote_invitations (quote_id, contact_id) "
            "WHERE deleted_at IS NULL AND revoked_at IS NULL"
        )
    )
