# Data retention and delete policy

This doc defines how rows are removed from the database. Every table belongs to
exactly one of the five tiers below. New tables must pick a tier explicitly in
their creating migration's docstring; new code that deletes rows must match the
tier rule for that table.

A guardrail smoke at
[tests/test_delete_policy_guardrail_smoke.py](../tests/test_delete_policy_guardrail_smoke.py)
scans `services/` and `api/routers/` for `session.delete()` / `db.delete()` /
`DELETE FROM` and fails CI if a call site references a table not in the explicit
allowlist for that delete style. This is the enforcement mechanism — adding a
new hard-delete to a Tier 1 or Tier 2 table will break the smoke until the
allowlist or the table's tier is updated.

## Tier 1 — Financial / user-facing soft-delete

Tables whose rows represent financial commitments or customer-visible artifacts.
Deletion is **soft** (`deleted_at TIMESTAMPTZ`, NULL for live rows). Reads always
filter `deleted_at IS NULL` unless intentionally including history.

| Table | Service helper | Read filter |
|---|---|---|
| `invoices` | `services.invoice_service.soft_delete_invoice` | `Invoice.deleted_at.is_(None)` |
| `invoice_invitations` | inline in invoice/portal services | `InvoiceInvitation.deleted_at.is_(None)` |
| `quotes` | `services.quote_service.soft_delete_quote` | `Quote.deleted_at.is_(None)` |
| `quote_invitations` | inline in quote/portal services | `QuoteInvitation.deleted_at.is_(None)` |
| `payments` | `services.payment_service.soft_delete_payment` | `Payment.deleted_at.is_(None)` |
| `event_documents` | `api.routers.event_documents.delete_document` | `EventDocument.deleted_at.is_(None)` |
| `contacts` | D3: `services.contact_service.archive_contact` (in flight) | `Contact.deleted_at.is_(None)` |
| `events` | D3: `services.event_service.archive_event` (in flight) | `Event.deleted_at.is_(None)` |
| `event_participants` | D3: `services.event_participants.remove_event_participant` (in flight) | `EventParticipant.deleted_at.is_(None)` |
| `special_orders` | D3: `services.special_order_service.archive_special_order` (in flight) | `SpecialOrder.deleted_at.is_(None)` |

**Rules:**
- No `session.delete()`, `db.delete()`, or raw `DELETE FROM` against these tables.
  Use the service helper, which sets `deleted_at = NOW()`.
- Every read path must filter `deleted_at IS NULL`. The existing partial unique
  indexes (`WHERE deleted_at IS NULL`) on `invoices`, `quotes`, and invitation
  tables — plus the ones added by migration 080 on `contacts` and
  `event_participants` — enforce uniqueness only on live rows; missing this
  filter in a read can return tombstoned rows that should be invisible.
- Hard-delete is reserved for retention sweeps, which do not exist for this tier
  today. If one is added, it lives in `services/` with a documented retention
  window and is exempted in the guardrail allowlist.
- **Portal-read exemption** (Gate 3 of the CRM record-deletion plan):
  customer-facing portal joins on `contacts` and `events` MUST NOT filter
  `deleted_at IS NULL`. A signed invoice/quote portal link resolves a specific
  financial artifact whose contact / event is identity context, not a filter
  target. Staff archive of the contact happens after the customer already has
  the link; the artifact must keep rendering. Implementation lives in
  `services/portal_*.py` and `services/invoice_pdf.py`. Admin and sales reads
  always filter.

## Tier 2 — CRM core, append-only (no delete path)

Tables holding customer or operational records that should never be removed in
the current product. There is no API endpoint, service helper, or admin UI for
deleting these rows.

| Table | Why append-only |
|---|---|
| `appointments` | Booking history; `appointment_session_events` references it. Lifecycle handled by `appointments.status` (`cancelled` / `no_show` / etc.) |
| `catalog_items` | Referenced by `appointment_tried_on_items` (RESTRICT) and quote/invoice line items via name/SKU snapshot. `catalog_items.is_active` is the deactivation knob. |

**Rules:**
- No `session.delete()`, `db.delete()`, or `DELETE FROM` against these tables
  from any service or router. The guardrail smoke fails if one appears.
- "Hide from the list" UX uses status fields (`appointments.status`,
  `catalog_items.is_active`), not deletion.
- The four CRM-core tables that previously lived here — `contacts`, `events`,
  `event_participants`, `special_orders` — moved to Tier 1 in D2 (migration
  080) when single-state soft-delete was introduced. See
  `docs/CRM_RECORD_DELETION_PLAN.md` for the phase-by-phase history.

## Tier 3 — Retention-managed hard-delete

Tables where rows expire on a schedule. Hard-delete only inside a documented
retention job; never from a user-facing endpoint.

| Table | Retention job | Window |
|---|---|---|
| `webhook_events` | `services.webhook_ingest.purge_old_events` | configurable cutoff |
| `staff_punches` (geo/IP fields only) | `services.attendance_geo_retention` (G2) | 30 days; clears columns, does not delete rows |

**Rules:**
- The retention job uses a parameterized `DELETE FROM <table> WHERE ...` against
  a time-bound predicate. The guardrail smoke allowlists these specific call
  sites by file + table.
- New retention sweeps must add an entry here, in the smoke allowlist, and in
  the systemd timer/cron that runs them.

## Tier 4 — Operational config hard-delete

Admin-only or record-owner configuration tables. The owner removes rows
directly; no business audit trail is required because these rows do not
represent customer-facing state.

| Table | Service / router |
|---|---|
| `appointment_availability_rules` | `api.routers.admin_booking_settings.delete_rule` |
| `appointment_blackouts` | `api.routers.admin_booking_settings.delete_blackout` |
| `staff_shifts` | `services.staff_shifts_admin.delete_shift` |
| `staff_shift_overrides` | `services.staff_shifts_admin.delete_override` |
| `staff_holidays` | `services.staff_holidays_admin.delete_holiday` |
| `staff_schedule_entries` | `services.staff_schedule.delete_entry` (draft rows only) |
| `staff_locations` | (column reset, not row delete — owner deactivates) |
| `recurring_unavailability` | `services.recurring_availability.delete_block` (stylist deletes their own row; ownership check enforced in the service) |

**Rules:**
- Hard-delete via `db.delete(row)` is fine; the row has no financial meaning.
- Reads do not need to filter; deleted rows simply do not exist.

## Tier 5 — Rebuild children inside parent transactions

Child rows that are recreated whenever the parent is patched. The parent's
PATCH/POST handler wipes the child set with `DELETE FROM` and re-inserts the
new set in the same transaction.

| Child table | Parent service |
|---|---|
| `invoice_line_items` | `services.invoice_service.update_invoice` |
| `invoice_installments` | `services.invoice_service.update_invoice` |
| `invoice_order_discounts` | `services.invoice_service.update_invoice` |
| `quote_line_items` | `services.quote_service.update_quote` |
| `quote_installments` | `services.quote_service.update_quote` |
| `quote_order_discounts` | `services.quote_service.update_quote` |
| `payment_allocations` | `services.payment_service.update_allocation` |

**Rules:**
- These `DELETE FROM` statements are allowlisted by file + table in the guardrail
  smoke. Adding a new rebuild-children child requires updating both this table
  and the allowlist.
- Hard-delete is always inside the same transaction as the re-insert; rollback
  is atomic.

## Special case — `appointment_tried_on_items`

Sales staff can remove a tried-on item via `services.sales_tried_on.remove_tried_on`,
which calls `db.delete(row)` and writes an `activity_log` breadcrumb in the same
transaction. The breadcrumb is the audit-trail substitute; there is no business
need to soft-delete the row itself because the activity log records what
happened. Allowlisted explicitly in the guardrail.

## Adding a new table — checklist

1. Pick a tier in the creating migration's docstring (`Tier 1`, `Tier 2`, ...).
2. If Tier 1: add `deleted_at TIMESTAMPTZ`, partial unique indexes, a service
   helper, and update this doc's Tier 1 table.
3. If Tier 2: do nothing schema-wise; just confirm no delete code path is added.
4. If Tier 3 / 4 / 5: add the call site to this doc and to the guardrail smoke's
   allowlist before merging.

## What this policy does not cover

- Column-level redaction (e.g., clearing `staff_punches.geo_lat` while keeping
  the row) — see the retention job docstrings.
- Cascading FK deletes (`ON DELETE CASCADE`) — these are schema-level and fire
  only when the parent row is deleted, which Tier 1/2 forbids. The cascades
  exist for Tier 4 admin-config children and Tier 5 rebuild-children.
- Database-side TRUNCATE or manual `psql` deletes — those are operator actions
  outside the application code path and not in scope for the guardrail smoke.
