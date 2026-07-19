"""Phase 4b data migration: lift legacy event_documents.kind='invoice' rows
into canonical `invoices` rows, and retag the source rows as
'external_invoice' attachments pointing back at the new canonical row.

Why this exists even though production today has zero such rows:
- The migration must be correct against a non-zero population (a row could
  appear between Phase 4a deploy and the Phase 4b maintenance window if
  staff upload a legacy-shaped invoice in the gap).
- It documents the lift transformation in code, which is what the
  test/rollback story is built on.
- The accompanying app-side changes (`outstanding_subq` swap,
  `document_counts` reshape, upload-route enum tighten) ship in the same PR
  but live in `services/` and `api/`. This migration's job is to ensure that
  by the time those reads run against the new schema, every legacy row has a
  canonical twin in `invoices`.

The full mapping spec lives in [docs/INVOICING_PHASES.md](../../docs/INVOICING_PHASES.md)
section 4b.1; this script is the executable form of that spec.

The migration is idempotent enough to be safe-rerun against a freshly
applied 025 schema with zero legacy rows: the SELECT returns no rows and
we early-return without touching `numbering_state`. It is NOT idempotent
on second-run after legacy rows have already been migrated — the source
rows now have `kind='external_invoice'` and the SELECT finds none. That's
the correct shape: each legacy row gets lifted exactly once.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def upgrade(connection) -> None:
    run_id = str(uuid.uuid4())

    legacy_rows = connection.execute(
        text(
            """
            SELECT
                d.id              AS doc_id,
                d.event_id        AS event_id,
                d.uploaded_by_user_id AS uploaded_by_user_id,
                d.invoice_amount_cents,
                d.invoice_status,
                d.invoice_issued_at,
                d.invoice_paid_at,
                d.created_at      AS doc_created_at,
                e.primary_contact_id AS contact_id
            FROM event_documents d
            JOIN events e ON e.id = d.event_id
            WHERE d.kind = 'invoice'
              AND d.deleted_at IS NULL
            ORDER BY d.created_at ASC
            """
        )
    ).all()

    if not legacy_rows:
        # Production case 2026-05-01: zero rows. Nothing to lift, nothing to
        # number, nothing to attach. The Phase 4b application-side swap (in
        # the same PR) handles the empty state by simply selecting from an
        # empty `invoices` table for kanban + counts.
        return

    # Reserve a contiguous block of invoice numbers up front under a row
    # lock so concurrent traffic in the maintenance window cannot collide.
    # The maintenance window stops the API, but the row lock is the
    # belt-and-suspenders safety net.
    non_draft_count = sum(
        1 for r in legacy_rows if (r.invoice_status or "draft") != "draft"
    )

    base_year = None
    base_seq = 0
    if non_draft_count > 0:
        seq_row = connection.execute(
            text(
                "SELECT invoice_year, invoice_seq FROM numbering_state "
                "WHERE id = 1 FOR UPDATE"
            )
        ).one()
        base_year = int(seq_row.invoice_year)
        base_seq = int(seq_row.invoice_seq)
        connection.execute(
            text(
                "UPDATE numbering_state SET invoice_seq = :s, "
                "updated_at = NOW() WHERE id = 1"
            ),
            {"s": base_seq + non_draft_count},
        )

    # Now lift each legacy row in order. Allocate sequential numbers from
    # the reserved block in `created_at` order so the visible numbering
    # mirrors the historical upload order.
    next_seq_offset = 1
    legacy_status_to_canonical = {
        "draft": "draft",
        "sent": "sent",
        "paid": "paid",
        "void": "cancelled",
    }

    for row in legacy_rows:
        legacy_status = (row.invoice_status or "draft").lower()
        canonical_status = legacy_status_to_canonical.get(legacy_status, "draft")

        amount = int(row.invoice_amount_cents or 0)
        paid = amount if canonical_status == "paid" else 0
        balance = amount - paid

        if canonical_status == "draft":
            invoice_number = None
        else:
            invoice_number = f"INV-{base_year}-{(base_seq + next_seq_offset):06d}"
            next_seq_offset += 1

        # contact_id: prefer events.primary_contact_id. The migration
        # SELECT joined on events, so the value is already in scope.
        if row.contact_id is None:
            raise RuntimeError(
                f"event_documents id={row.doc_id} attached to event_id="
                f"{row.event_id} which has no primary_contact_id; "
                "Phase 4b cannot lift this row. Resolve by setting a "
                "primary contact on the event before re-running."
            )

        new_invoice = connection.execute(
            text(
                """
                INSERT INTO invoices (
                    event_id,
                    contact_id,
                    invoice_number,
                    status,
                    issue_date,
                    subtotal_cents,
                    discount_cents,
                    tax_cents,
                    total_cents,
                    paid_to_date_cents,
                    balance_cents,
                    sent_at,
                    paid_at,
                    cancelled_at,
                    created_by_user_id,
                    created_at,
                    legacy_migration_run_id
                ) VALUES (
                    :event_id,
                    :contact_id,
                    :invoice_number,
                    :status,
                    COALESCE(CAST(:issued_at AS DATE), CAST(:created_at AS DATE)),
                    :amount,
                    0,
                    0,
                    :amount,
                    :paid,
                    :balance,
                    CASE WHEN :status IN ('sent','partial','paid','cancelled')
                         THEN COALESCE(:issued_at, :created_at) END,
                    :paid_at,
                    CASE WHEN :status = 'cancelled' THEN :created_at END,
                    :uploaded_by_user_id,
                    :created_at,
                    :run_id
                )
                RETURNING id
                """
            ),
            {
                "event_id": row.event_id,
                "contact_id": row.contact_id,
                "invoice_number": invoice_number,
                "status": canonical_status,
                "issued_at": row.invoice_issued_at,
                "created_at": row.doc_created_at,
                "amount": amount,
                "paid": paid,
                "balance": balance,
                "paid_at": row.invoice_paid_at,
                "uploaded_by_user_id": row.uploaded_by_user_id,
                "run_id": run_id,
            },
        ).scalar()

        # One synthetic line item: legacy rows had no itemization, so we
        # materialize a single line carrying the full amount. Future PDF
        # rendering will show this as "Imported from uploaded PDF".
        connection.execute(
            text(
                """
                INSERT INTO invoice_line_items (
                    invoice_id, sort_order, kind, description,
                    quantity, unit_price_cents, discount_cents,
                    tax_rate, tax_name,
                    line_subtotal_cents, line_tax_cents, line_total_cents
                ) VALUES (
                    :invoice_id, 0, 'service', 'Imported from uploaded PDF',
                    1, :amount, 0,
                    0, NULL,
                    :amount, 0, :amount
                )
                """
            ),
            {"invoice_id": new_invoice, "amount": amount},
        )

        # One installment row mirroring the lifted total. due_date defaults
        # to issue_date+30 days; paid_at carries the legacy paid stamp.
        # invoice_installments rejects amount=0 (Phase 1 CHECK); skip if
        # amount is zero — a legacy invoice with no money on it cannot
        # carry a real schedule. This shouldn't happen in practice but
        # guards the migration against a malformed legacy row.
        if amount > 0:
            connection.execute(
                text(
                    """
                    INSERT INTO invoice_installments (
                        invoice_id, sort_order, label,
                        amount_cents, due_date, paid_at
                    ) VALUES (
                        :invoice_id, 0, 'Balance',
                        :amount,
                        COALESCE(CAST(:issued_at AS DATE), CAST(:created_at AS DATE))
                            + INTERVAL '30 days',
                        :paid_at
                    )
                    """
                ),
                {
                    "invoice_id": new_invoice,
                    "amount": amount,
                    "issued_at": row.invoice_issued_at,
                    "created_at": row.doc_created_at,
                    "paid_at": row.invoice_paid_at,
                },
            )

        # Retag the source row as an external attachment that links back
        # to the canonical record. The four invoice_* columns survive
        # untouched for one-season rollback.
        connection.execute(
            text(
                """
                UPDATE event_documents
                SET kind = 'external_invoice',
                    linked_invoice_id = :inv,
                    updated_at = NOW()
                WHERE id = :doc
                """
            ),
            {"inv": new_invoice, "doc": row.doc_id},
        )
