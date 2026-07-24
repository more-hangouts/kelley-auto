# Invoicing — Phased Plan

A native invoicing, quoting, and payment-tracking layer that lives next to `events` in this codebase. Inspired by Invoice Ninja's data model, rebuilt in FastAPI/React/Postgres so the shop runs on one stack with one login, one URL, one set of permissions.

## Goal

A quinceañera mom signs a contract, pays a $200 deposit, and gets a receipt. Sixty days before the event she gets a reminder, pays the balance, and the event flips to `sold`. All of this happens inside `admin.shopbellasxv.com` for staff and inside a single signed link for the customer. No second platform, no Docker sidecar, no separate auth surface, no exported CSV reconciliation. Long-term, the same primitives carry the shop into payment plans, dunning, and per-event profitability without re-platforming.

## Why this shape, not Invoice Ninja itself

Invoice Ninja v5 is a Laravel monolith. Pulling it in as a Docker sidecar would double the ops surface (a second DB, a second auth model, a second container to harden under systemd, a second URL for staff to context-switch into) for features that map cleanly onto five new tables and a handful of routers in the existing FastAPI app. The Invoice Ninja schema and feature shapes are still load-bearing reference material — every decision below cites the column or behavior we're borrowing — but the implementation lives natively in this repo.

## Working environment

All build and verification work happens on the VPS. There is no local dev server. Smoke tests that say "visit `/events/<id>/invoices`" mean visit `admin.shopbellasxv.com` after the VPS rebuild and service restart, not a localhost URL. New disk-write paths need a `ReadWritePaths` entry in the systemd unit before they will work in production.

## Decisions locked

- **Native rebuild, not a sidecar.** Invoice Ninja's source is reference material for schema and feature shape, not code we deploy.
- **Money in cents.** Every monetary column is `BIGINT` named `*_cents`. No `Numeric` columns for amounts. Tax rates are `Numeric(7,5)` (e.g. `0.08250` for 8.25%).
- **USD only in v1.** No exchange_rate, no currency_id, no multi-currency. Add later if the shop opens a second location with different currency.
- **A real `invoice_installments` table from day one, not a single deposit column.** Invoice Ninja models the deposit as one column on the invoice (`partial` + `partial_due_date`); we deviate because Phase 12 admits payment plans of three to six installments are likely. v1 always materializes two rows: deposit and balance. Phase 12 just adds more rows. Reporting against installments stays uniform whether the schedule is two rows or six. The hard invariant: `SUM(installments.amount_cents) = invoices.total_cents`, enforced on write, with at least one row required before an invoice can transition to `sent`.
- **Line items live in their own table, not a JSON column.** Invoice Ninja uses a JSON `line_items` column for performance reasons in a multi-tenant SaaS. We get cleaner reporting, indexable line-level history, and per-line audit by giving items a real table. The on-disk price is one extra join per invoice render. Source decision: ours, not theirs.
- **Quotes are a sibling table to invoices, not a discriminator on the same table.** The lifecycle is different (approve/reject vs sent/partial/paid), the signature capture only matters on quotes, and the conversion path "approved quote → invoice" is cleaner across two tables. Mirrors Invoice Ninja's `quotes` table. Source: `app/Models/Quote.php`.
- **Payments use a polymorphic allocation table.** A single `payments` row can be applied to one or many invoices via `payment_allocations`. Mirrors Invoice Ninja's `paymentables` table. Source: `app/Models/Payment.php` `paymentables` morphToMany.
- **Refunds and overpayments live on `payments`, never on `invoices`.** A `payments.unapplied_cents > 0` row means the customer paid more than was allocated; that money sits on the payment until staff allocate it to a future invoice or issue a refund. `invoices.paid_to_date_cents <= total_cents` is the hard invariant — invoices never see overpayment. Refunds are not new payment rows; they are decrements on the original payment via `payments.refunded_cents` and per-allocation `payment_allocations.refunded_cents`, mirroring Invoice Ninja's `Payment.refunded` + `Paymentable.refunded` columns. There is no `is_refund` flag and no negative `amount_cents`.
- **Public client portal is signed-link only, and invitations are core, not portal-specific.** No customer login. A `/portal/invoice/<key>` URL with a 256-bit random key lets the mom view, accept, sign, and (later) pay her invoice. The `invoice_invitations` schema lands in Phase 1 and `quote_invitations` lands in Phase 5, even though the public routes that consume them don't ship until Phase 7. Mark-as-sent always creates an invitation; the portal is just one consumer. Invitations carry `deleted_at`, `expires_at`, and `revoked_at` so staff can rotate or kill a leaked link without dropping the invoice. Mirrors Invoice Ninja's `invoice_invitations.key`. Source: `app/Models/InvoiceInvitation.php`.
- **PDF rendering via WeasyPrint.** Pure-Python, HTML+CSS in, PDF out. Output cached in `document_storage` under `invoices/{id}/{revision}.pdf` so existing storage primitives and disk-space guards apply unchanged. PDFs are cache artifacts in v1 and can be regenerated from the DB. Phase 13 documents exactly what is canonical, what is cache, and what gets backed up.
- **Numbers are assigned on first send, not on draft create. Gaps are accepted.** Drafts carry a placeholder identifier (`DRAFT-{id}`) until staff hit Send. The first send allocates the next number under a row lock on `numbering_state` and stamps it on the row. A cancelled or reversed invoice keeps its number forever; it is the audit trail. This trades a gap-free sequence for two real benefits: a draft that gets thrown away never burns a number, and a number you see on a paper trail always points to a real document.
- **Business profile lands in Phase 1, not Phase 8.** PDF rendering needs legal business name, address, phone, email, logo path, default tax label, default invoice terms, default footer, and default payment instructions. A `business_profile` singleton table holds them. The editing surface ships in Phase 3 so PDFs in Phase 8 have something to render against.
- **Activity log is a real table, not derived.** Mirrors Invoice Ninja's `activities`. Per-event timeline (created, sent, viewed, signed, paid, late) is a Phase 9 deliverable, not a Phase 1 nice-to-have.
- **Document attachment and financial record are split, immediately.** Production today has `event_documents.kind='invoice'` rows that conflate "PDF on disk" with "money owed". Phase 4a makes the schema compatible; Phase 4b moves every legacy row's financial state into a canonical `invoices` row, and the original document row's `kind` flips to `'external_invoice'` with a new `linked_invoice_id` FK pointing at the canonical row. New uploads after Phase 4b use `kind='external_invoice'` (a vendor's PDF, an alterations subcontractor's bill) and either link to an existing invoice or stand alone as a record-only file. The four `invoice_*` columns on `event_documents` survive Phase 4b unread and are dropped in Phase 13 once a season has confirmed the new shape.
- **Events get a service-level delete guard, not a schema change.** `invoices.event_id` is `ON DELETE RESTRICT`, but the production app does not hard-delete events today; it cancels them via status. Test cleanup paths that hard-delete events get a service helper (`event_service.delete_event`) that refuses if any non-cancelled invoices or quotes exist. The FK semantics enforce the floor; the service enforces the policy.
- **Recurring invoices and credit memos are deferred.** Quinceañera revenue is one-shot per event; recurring is a future-shop feature. Credits/refund-as-store-credit are rare; in v1 a refund is a decrement on the original payment, not a new credit row.
- **Customer-facing copy follows the repo voice.** No em dashes, no robotic listy "X, and Y" patterns. Receipt headlines and portal CTAs read like a person wrote them.

## Tracking

- [x] Phase 0: Confirm baseline (code-side complete; VPS-side tasks listed in the Phase 0 validation note)
- [x] Phase 1: Schema for invoices, line items, installments, invitations, business profile
- [x] Phase 2: Invoice service + totals + CRUD API
- [x] Phase 3: Invoice editor UI
- [x] Phase 4a: Split document schema from invoice financials
- [x] Phase 4b: Migrate legacy `event_documents.kind='invoice'` rows
- [x] Phase 5: Quotes and contracts
- [x] Phase 6: Payments and deposit handling
- [x] Phase 7: Public client portal
- [x] Phase 8: PDF generation
- [x] Phase 9: Activity timeline
- [x] Phase 10: Pipeline integration and AR rollup
- [x] Phase 11: Reminders and dunning
- [ ] Phase 12: Recurring and payment plans (deferred — build only when staff ask for it)
- [x] Phase 13: Tests, ops, and cleanup

## Current status

As of 2026-05-03: phases 0–11 and 13 complete and **deployed** to production. The "VPS" in this project's layout is the dev box itself, so migrations 018–039 are live on the production DB. Bundles shipped via `sudo systemctl restart bellas-xv-api` + fresh `npm run build` of the frontend dist:

1. Phases 1+2+3+4a+4b at 18:39 UTC.
2. Phase 5 at 18:54 UTC. Phases 1–5 were recorded in git as `343d3e8`.
3. Phase 6 at 19:32 UTC, recorded as `3ee00e2`.
4. Phase 7 at 20:05 UTC, recorded as `6b87671`.
5. Phase 8 at 20:35 UTC, recorded as `b9599bc`.
6. Phases 9 + 10 bundled at 21:30 UTC, recorded as `e1dd7e6`. Phase 9 also includes review-fix migration 037 + activity cache invalidation across the staff editors. Phase 10 needed no new migrations — both surfaces read existing columns.
7. Phase 11 at 02:?? UTC, recorded as `f61a289`. Migrations 038 + 039, daily worker, late-fee schedule rebalance, quote-expiry sweep.
8. Phase 13 (this commit). No new migrations; tests + per-user rate limiter + ARCHITECTURE.md updates only.

`/api/health` reports `migrations_applied: 39`. Browser smoke for Phases 1–4b passed visually with the editor polish backlog pinned (see Known v1 gaps under Phase 3); Phases 5–11 still need authenticated browser smoke. Phase 12 (recurring + payment plans) is deferred per the plan — schema is forward-compatible so a future build only adds a cron + multi-installment editor surface, no migration. **Invoicing track is feature-complete for v1, with Phase 13 closing it out: concurrent-send smoke, activity vocabulary check, per-user rate limiter on money-changing routes, and ARCHITECTURE.md updates covering numbering invariants, portal URL pattern, background workers, rate limiting, backup/retention, and the legacy-column drop plan.**

Originally framed deploy bundle (kept for historical context — Phase 1+2+3+4a+4b together):

- Migrations 018–026 (six new tables, the line-item money tightening from migration 024, the schema-compatible event_documents split from migration 025, and the legacy-row data lift from migration 026)
- New service modules [services/invoice_service.py](../services/invoice_service.py), [services/business_profile_service.py](../services/business_profile_service.py)
- New routers [api/routers/invoices.py](../api/routers/invoices.py) (mounted at `/api/events/{id}/invoices` and `/api/invoices`) and [api/routers/business_profile.py](../api/routers/business_profile.py) (mounted at `/api/business-profile`)
- Backend Phase 4b swaps shipped on the existing event_documents surface: kanban `outstanding_subq` re-sources from canonical `invoices` (broadens to include `partial`), `document-counts` response shape becomes `{document, external_invoice, outstanding_invoices}`, the upload route's `_DocumentKind` Literal swaps `invoice` → `external_invoice` (with optional `linked_invoice_id` form field), and the PATCH route returns `422 invoice_fields_retired` on writes to the legacy `invoice_*` columns
- Substantially expanded frontend: invoice editor drawer, replaced Invoices tab (now also lists external_invoice attachments + a real "Attach external PDF" upload flow), business profile settings page, global invoice search page, dashboard entry points, money utils + CurrencyInput component
- `database/models.py:EventDocument` carries `linked_invoice_id` (added in 4a, populated by 4b's data lift and by fresh external_invoice uploads)
- Regression tests: [tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) (29 checks), [tests/test_invoices_smoke.py](../tests/test_invoices_smoke.py) (17 checks), [tests/test_business_profile_smoke.py](../tests/test_business_profile_smoke.py) (13 checks), and [tests/test_event_documents_smoke.py](../tests/test_event_documents_smoke.py) (rewritten to drive the new shape: legacy `kind='invoice'` upload returns 422, PATCH `invoice_*` returns 422 `invoice_fields_retired`, canonical-invoice creation drives the kanban + counts assertions, `linked_invoice_id` validation paths covered)

No new disk-write paths beyond what's already covered: the business logo file lands under `/var/lib/bellas-xv/uploads/business/logo.<ext>`, inside the existing `ReadWritePaths` root from Event Detail Tabs Phase 2. PDF rendering arrives in Phase 8 and that's when the `ReadWritePaths` / disk-space surface gets its next look.

VPS-side tasks still open (non-blocking until the phase that needs them):

- Confirm `/var/lib/bellas-xv/uploads` is covered by the systemd unit's `ReadWritePaths` line. **Needed before Phase 8 PDFs.** Likely already covered (logo upload exercised the same root locally without any additional config).
- Stand up a staging clone of the production DB plus the uploads tree. **Needed before Phase 4b runs in production.** Production currently has zero `event_documents.kind='invoice'` rows (verified 2026-05-01), so Phase 4b's row-migration step is a no-op against current data; staging still matters for the schema-and-app swap.
- Manual browser smoke for Phase 3 + Phase 4b against `admin.shopbellasxv.com` after rebuild and service restart. The editor + the new "External attachments" / "Attach external PDF" flow are large enough that lint + build alone don't catch every UX regression.

End-of-day handoff (2026-05-01): the next session can either start Phase 5 (quotes, see Phase 5 section below for scope) or focus on packaging the Phase 1+2+3+4a+4b deploy bundle for the VPS. There is no work-in-progress code. The Phase 3 review iteration block and the Phase 4a/4b validation notes capture every meaningful decision since the last green compile.

Local regression sweep all green: `test_business_profile_smoke`, `test_invoices_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_booking_smoke`, `test_contacts_smoke`, `test_boutique_experience_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_notifications_smoke`. Frontend `npm run lint` clean, `npm run build` succeeds with the pre-existing large-chunk warning. Pytest broadly run across `tests/` collects zero from the script-style invoice smokes (intentional — they execute via `venv/bin/python tests/<file>.py`).

---

## Phase 0: Confirm baseline

**Status:** Code-side complete 2026-05-01. Two VPS-side asks open (production row count returned zero, ReadWritePaths and staging clone deferred to the phases that need them — see Current status above).

Purpose: catch divergence between this plan and the real code before touching anything. Inventory what already exists so the migration path is clean.

Tasks:

- [ ] Re-read [database/migrations/017_create_event_documents.py](../database/migrations/017_create_event_documents.py) and confirm the four `invoice_*` columns and the `chk_event_documents_invoice_fields_only_on_invoice` CHECK are exactly as the model code expects.
- [ ] Count production rows: `SELECT count(*) FROM event_documents WHERE kind='invoice' AND deleted_at IS NULL` and break down by `invoice_status`. Save the count to a Phase 0 validation note. The Phase 4b migration must produce the same total in `invoices`.
- [ ] Re-read [services/event_service.py](../services/event_service.py) `outstanding_subq` block (lines 337–348). The subquery selects from `event_documents`. Phase 4b swaps it to select from `invoices`.
- [ ] Re-read [api/routers/event_documents.py](../api/routers/event_documents.py) `document_counts` route. The `outstanding_invoices` count comes from the same source. Phase 4b swaps that too.
- [ ] Re-read [frontend/src/pages/event/tabs/Invoices.jsx](../frontend/src/pages/event/tabs/Invoices.jsx) (and confirm its current shape: file uploader plus four invoice fields). Phase 3 replaces it.
- [ ] Confirm the upload directory `/var/lib/bellas-xv/uploads` is writable and has the systemd `ReadWritePaths` entry already (it does, from Event Detail Tabs Phase 2). PDFs will land under `invoices/{id}/{revision}.pdf` in the same root.
- [ ] Verify the [tests/test_event_documents_smoke.py](../tests/test_event_documents_smoke.py) suite still passes. It is the regression net for Phase 4.
- [ ] Prepare a staging copy of the production DB and uploads tree. Phase 4b must run there before production with the API routes and frontend bundle from the migration PR.
- [ ] Record the staging validation checklist in this document before Phase 1 starts: kanban counts match, event invoice tabs load, document-counts shape is understood, and rollback commands have been dry-run on staging.

Deliverable: a Phase 0 note appended below with the legacy-invoice row count and any divergence found.

Phase 0 validation note, 2026-05-01:

Code-side audit (local, complete):

- `database/migrations/017_create_event_documents.py` matches `database/models.py:EventDocument` exactly. The four `invoice_*` columns are `BIGINT/VARCHAR(16)/TIMESTAMPTZ/TIMESTAMPTZ` and the four CHECK constraints (`chk_event_documents_kind`, `chk_event_documents_invoice_status`, `chk_event_documents_invoice_fields_only_on_invoice`, `chk_event_documents_byte_size_nonneg`) are all present on the table. No divergence.
- `services/event_service.py:337-348` `outstanding_subq` confirmed: groups by `event_id`, flags `bool_or(kind='invoice' AND invoice_status='sent' AND deleted_at IS NULL)`. Phase 4b will swap this to `invoices` table reading `status IN ('sent','partial')`. Note: the current query treats only `sent` as outstanding; Phase 4b's swap broadens to `sent|partial` to reflect the new lifecycle, which is a small behavior change staff need to know about (a partially-paid invoice now lights up the kanban badge, where today it would not).
- `api/routers/event_documents.py:287-324` `document_counts` confirmed: returns `document` (file count), `invoice` (file count of `kind='invoice'`), `outstanding_invoices` (count of `kind='invoice' AND invoice_status='sent'`). Phase 4b's response shape: `outstanding_invoices` moves to `invoices` table; the `invoice` count becomes `external_invoice` (count of attached PDF rows that are not the canonical record). Frontend update to follow.
- `frontend/src/pages/event/tabs/Invoices.jsx` is 581 lines, file-uploader-based, with status pill, amount cell, totals header, and delete confirm dialog. Status enum is `draft|sent|paid|void`. Status mapping for Phase 4b legacy migration: `draft→draft`, `sent→sent`, `paid→paid`, `void→cancelled`. Phase 3 replaces this whole file.
- `tests/test_event_documents_smoke.py` passes against the local DB. All 27 assertions green, including kind filter, soft delete, size rollback, path traversal, board flag, and counts. This is the regression net for Phase 4.
- Local DB has 0 rows in `event_documents` where `kind='invoice' AND deleted_at IS NULL` (clean dev DB, expected). Production DB count is the load-bearing number — see VPS-side tasks below.
- `config/settings.py:67-69` confirms `DOCUMENT_STORAGE_ROOT` defaults to `/var/lib/bellas-xv/uploads`. The Phase 8 PDF cache will live under the same root at `invoices/{id}/{revision}.pdf` and inherit existing disk-space guards.

VPS-side tasks deferred to user:

- [x] Run on production: `SELECT count(*) FROM event_documents WHERE kind='invoice' AND deleted_at IS NULL` and a breakdown by `invoice_status`. **Result, 2026-05-01: total=0, no rows by status.** Phase 4b's row migration is a no-op against current production data. The Phase 4a schema change and Phase 4b's app-side swap (kanban subquery, document_counts response shape, upload route rejecting `kind='invoice'`) are still load-bearing and must ship together. Staging validation can focus on schema/code; we do not need a representative row-count copy for the row migration step.
- [ ] Confirm `/var/lib/bellas-xv/uploads` is owned by the API service user and is in the systemd unit's `ReadWritePaths` (it should be, from Event Detail Tabs Phase 2). Capture the unit file's `ReadWritePaths` line in this note.
- [ ] Stand up a staging copy of the production DB and uploads tree before Phase 4b runs in production. Phase 4b's full PR (migration + service swap + frontend bundle) must run there with a representative row count first. The doc captures the rollback dry-run as a hard prerequisite.

Open follow-ups before Phase 1 starts:

- The migration numbering in this doc starts at 018. Confirm 018 is the next available number on the VPS (`ls database/migrations/` should show 017 as the highest). Local repo confirms 017 is the latest.
- Phase 1's `business_profile` singleton seeds `legal_name='Bellas XV'` in the migration. The full row gets edited from the Settings UI in Phase 3; until then, PDFs would render with the placeholder. Phase 8 ships after Phase 3 so this never surfaces to a customer.

---

## Phase 1: Schema for invoices, line items, installments, invitations, business profile

**Status:** Complete 2026-05-01. Migrations 018–024 applied locally. ORM models added to [database/models.py](../database/models.py). Schema smoke [tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) covers every CHECK and UNIQUE across the six tables (26 checks). Three review findings closed in-phase (line-item money floor, pytest-safety, coverage gap) — see review iteration below. Not yet on VPS.

Purpose: stand up every canonical table the rest of the plan depends on. Nothing renders yet. Every table is verified with real INSERTs before moving on, per repo convention. The six tables land together because each later phase assumes them; splitting the schema work creates layering bugs (mark-as-sent referencing invitations that don't exist yet, PDF rendering against a profile that hasn't shipped).

### 1.1 New migration `018_create_invoices.py`

Columns on `invoices`:

- `id SERIAL PRIMARY KEY`
- `event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE RESTRICT` — invoices outlive events; the service layer guards event delete (see Decisions Locked).
- `contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT` — denormalized for portability when an event's primary contact changes.
- `invoice_number VARCHAR(32) UNIQUE` — nullable. Drafts have no number. Numbers are allocated at first send (Phase 2). Once stamped, the column never changes, even on cancel.
- `status VARCHAR(16) NOT NULL DEFAULT 'draft'` — one of `draft|sent|partial|paid|cancelled|reversed`. Mirrors Invoice Ninja's `STATUS_DRAFT=1`...`STATUS_REVERSED=6` but stored as text for readability. (Source: `app/Models/Invoice.php` lines 116–123.)
- `issue_date DATE NOT NULL DEFAULT CURRENT_DATE`
- `due_date DATE` — derived display field. The real schedule lives in `invoice_installments`. Kept on the invoice row as `MAX(installments.due_date)` for index access in the reminder cron and AR rollup. Recomputed by the service whenever installments change.
- `subtotal_cents BIGINT NOT NULL DEFAULT 0` — sum of line totals before invoice-level discount/tax.
- `discount_cents BIGINT NOT NULL DEFAULT 0` — invoice-level discount applied after subtotal.
- `tax_cents BIGINT NOT NULL DEFAULT 0` — total tax across all lines plus invoice-level tax if any.
- `total_cents BIGINT NOT NULL DEFAULT 0` — final amount the customer owes. Computed, never user-edited.
- `paid_to_date_cents BIGINT NOT NULL DEFAULT 0` — `SUM(allocations.applied_cents - allocations.refunded_cents)`. Computed in the same transaction by `_recompute_invoice_totals` in Phase 6.
- `balance_cents BIGINT NOT NULL DEFAULT 0` — `total_cents - paid_to_date_cents`. Computed.
- `terms TEXT` — payment terms shown on the PDF.
- `footer TEXT` — footer text on the PDF.
- `public_notes TEXT` — visible to customer in the portal.
- `private_notes TEXT` — staff-only.
- `po_number VARCHAR(64)`
- `created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `sent_at TIMESTAMPTZ` — first time the invoice was emailed.
- `viewed_at TIMESTAMPTZ` — first time the customer opened the portal link.
- `paid_at TIMESTAMPTZ` — when balance hit zero.
- `cancelled_at TIMESTAMPTZ`
- `cancellation_reason TEXT`
- `revision INTEGER NOT NULL DEFAULT 1` — bumped on every edit after `sent`. Powers the `invoices/{id}/{revision}.pdf` cache key in Phase 8.
- `last_pdf_rendered_revision INTEGER` — last revision that successfully rendered to PDF.
- `last_pdf_rendered_at TIMESTAMPTZ`
- `last_pdf_render_error TEXT` — latest WeasyPrint/storage error, shown to staff with a Retry render button in Phase 8. Cleared on successful render.
- `legacy_migration_run_id UUID` — null for native invoices. Phase 4b stamps imported rows so rollback can delete exactly the rows created by that run.
- `deleted_at TIMESTAMPTZ` — soft delete.
- `created_at`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

CHECK constraints:

- `chk_invoice_status` — status in the allowed set.
- `chk_invoice_amounts_nonneg` — every `*_cents` column except `discount_cents` is `>= 0`.
- `chk_invoice_paid_le_total` — `paid_to_date_cents <= total_cents`. Hard invariant. Overpayment lives on `payments.unapplied_cents`, never on the invoice.
- `chk_invoice_balance_consistent` — `balance_cents = total_cents - paid_to_date_cents`.
- `chk_invoice_revision_pos` — `revision >= 1`.
- `chk_invoice_number_when_not_draft` — `(status = 'draft') OR (invoice_number IS NOT NULL)`. Once an invoice leaves draft it must have a number forever.

Indexes:

- `(event_id, status, deleted_at)` for the per-event tab list.
- `(contact_id, status, deleted_at)` for "all invoices for this customer".
- `(status, due_date)` for the reminder cron in Phase 11.
- `(status, deleted_at)` partial WHERE `deleted_at IS NULL AND status IN ('sent', 'partial')` — the AR rollup query.

### 1.2 New migration `019_create_invoice_line_items.py`

Columns on `invoice_line_items`:

- `id SERIAL PRIMARY KEY`
- `invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE`
- `sort_order INTEGER NOT NULL DEFAULT 0` — display order.
- `kind VARCHAR(16) NOT NULL DEFAULT 'product'` — one of `product|service|alteration|fee`. Maps to Invoice Ninja `type_id` but with text values. Skip task/expense kinds in v1.
- `product_key VARCHAR(120)` — SKU or short label. Free text in v1; future phase can promote to a product catalog.
- `description TEXT NOT NULL` — line description shown to the customer.
- `quantity NUMERIC(10,2) NOT NULL DEFAULT 1`
- `unit_price_cents BIGINT NOT NULL` — per-unit price.
- `discount_cents BIGINT NOT NULL DEFAULT 0` — line-level discount, fixed amount only in v1. Invoice Ninja supports percent via `is_amount_discount` boolean; deferred.
- `tax_rate NUMERIC(7,5) NOT NULL DEFAULT 0` — e.g. `0.08250`. Single tax slot per line in v1. Invoice Ninja supports three; Texas sales tax is a single rate so the simpler shape covers Bellas.
- `tax_name VARCHAR(40)`
- `line_subtotal_cents BIGINT NOT NULL` — `quantity * unit_price - discount`. Computed.
- `line_tax_cents BIGINT NOT NULL` — `line_subtotal * tax_rate` rounded half-even.
- `line_total_cents BIGINT NOT NULL` — `line_subtotal + line_tax`.
- `notes TEXT` — staff-private notes about this line.
- `created_at`, `updated_at TIMESTAMPTZ`

CHECK constraints:

- `chk_line_kind` — kind in the allowed set.
- `chk_line_quantity_pos` — `quantity > 0`.
- `chk_line_unit_price_nonneg` — `unit_price_cents >= 0`.
- `chk_line_discount_le_subtotal` — `discount_cents <= quantity * unit_price_cents`.
- `chk_line_tax_rate_range` — `tax_rate >= 0 AND tax_rate < 1`.

Index: `(invoice_id, sort_order)`.

### 1.3 New migration `020_create_invoice_installments.py`

The payment schedule. Replaces the single-deposit-column shape from the prior plan draft. v1 always materializes two rows (deposit + balance) on send; Phase 12 lets staff add more.

Columns on `invoice_installments`:

- `id SERIAL PRIMARY KEY`
- `invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE`
- `sort_order INTEGER NOT NULL DEFAULT 0`
- `label VARCHAR(60) NOT NULL` — `Deposit`, `Balance`, `Installment 1`, etc.
- `amount_cents BIGINT NOT NULL` — required-to-pay amount for this installment.
- `due_date DATE NOT NULL`
- `paid_at TIMESTAMPTZ` — stamped when the installment is fully covered by allocations whose target date is on or before this row's `due_date`. Phase 6 task to compute.
- `staff_notes TEXT` — staff-only note for extension requests, special arrangements, or payment-plan context. Not rendered on customer-facing PDFs or portal pages.
- `created_at`, `updated_at TIMESTAMPTZ`

CHECK constraints:

- `chk_installment_amount_pos` — `amount_cents > 0`.

Indexes:

- `(invoice_id, sort_order)`.
- `(due_date)` for the reminder cron — reminders fire against installment due dates, not invoice-level `due_date`.

Application-level invariant (enforced by `invoice_service`, not the DB because it spans rows): `SUM(installments.amount_cents) WHERE invoice_id = X = invoices.total_cents` whenever the invoice is `sent` or beyond. The service refuses to mark-as-sent if the schedule is empty or out of balance.

### 1.4 New migration `021_create_invoice_invitations.py`

Public-portal access tokens. The schema lands in Phase 1 because Phase 2's `mark_sent` creates an invitation row; the routes that consume it are Phase 7. Mirrors Invoice Ninja's `invoice_invitations` table.

Columns:

- `id SERIAL PRIMARY KEY`
- `invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE`
- `contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE`
- `public_key VARCHAR(64) NOT NULL UNIQUE` — `secrets.token_urlsafe(32)`.
- `sent_at TIMESTAMPTZ`
- `last_resent_at TIMESTAMPTZ`
- `viewed_at TIMESTAMPTZ` — first portal open.
- `last_viewed_at TIMESTAMPTZ`
- `view_count INTEGER NOT NULL DEFAULT 0`
- `email_opened_at TIMESTAMPTZ` — for a future tracking pixel.
- `expires_at TIMESTAMPTZ` — optional. Null means never expires.
- `revoked_at TIMESTAMPTZ` — staff explicitly killed the link.
- `revoked_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `deleted_at TIMESTAMPTZ` — soft delete (e.g. wrong contact).
- `created_at`, `updated_at TIMESTAMPTZ`

UNIQUE `(invoice_id, contact_id)` so each contact gets one invitation per invoice. Resends reuse the same key.

Index: `(public_key)` (already covered by the UNIQUE), and `(invoice_id, deleted_at)` for the editor's "show all invitations on this invoice" lookup.

The portal route in Phase 7 looks up by `public_key` and checks `deleted_at IS NULL AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())`. All three gates land in Phase 1.

### 1.5 New migration `022_create_numbering_state.py`

Single-row table to allocate invoice and quote numbers atomically on first send.

Columns:

- `id SMALLINT PRIMARY KEY DEFAULT 1`
- `invoice_year SMALLINT NOT NULL`
- `invoice_seq INTEGER NOT NULL DEFAULT 0`
- `quote_year SMALLINT NOT NULL`
- `quote_seq INTEGER NOT NULL DEFAULT 0`
- `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

Seeded with `(1, EXTRACT(YEAR FROM NOW()), 0, EXTRACT(YEAR FROM NOW()), 0)` in the migration. Phase 2 service grabs a row lock with `SELECT ... FOR UPDATE`, increments the appropriate counter (resetting to 1 if the year rolled over), and returns the formatted number. The number is allocated only at the moment of first send; a draft that never sends never burns a number.

### 1.6 New migration `023_create_business_profile.py`

PDF rendering target. Singleton table with one row.

Columns:

- `id SMALLINT PRIMARY KEY DEFAULT 1`
- `legal_name VARCHAR(200) NOT NULL`
- `display_name VARCHAR(200)` — what shows on the PDF header. Defaults to `legal_name` if null.
- `address_line1 VARCHAR(200)`
- `address_line2 VARCHAR(200)`
- `city VARCHAR(120)`
- `state VARCHAR(40)`
- `postal_code VARCHAR(20)`
- `country VARCHAR(2) NOT NULL DEFAULT 'US'`
- `phone VARCHAR(40)`
- `email VARCHAR(255)`
- `website VARCHAR(255)`
- `logo_storage_key VARCHAR(500)` — file in `document_storage` under `business/logo.{ext}`.
- `default_tax_rate NUMERIC(7,5) NOT NULL DEFAULT 0`
- `default_tax_name VARCHAR(40)` — e.g. `TX Sales`.
- `default_invoice_terms TEXT`
- `default_invoice_footer TEXT`
- `default_payment_instructions TEXT` — `Pay by check to ..., Zelle to ..., Cash accepted in store.` Rendered on every invoice PDF and in the portal.
- `updated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `created_at`, `updated_at TIMESTAMPTZ`

Seeded with a placeholder row (`legal_name='Bellas XV'`) in the migration; staff fill in the rest from the editor in Phase 3.

### 1.7 ORM models

Add `Invoice`, `InvoiceLineItem`, `InvoiceInstallment`, `InvoiceInvitation`, `NumberingState`, `BusinessProfile` to [database/models.py](../database/models.py) matching the migrations. No relationships declared on the SQLAlchemy side beyond the FK columns; the service layer joins explicitly.

### 1.8 Real-INSERT validation

Per the [validate-schema-with-real-INSERTs](MEMORY.md) standing rule:

- [ ] Insert a draft invoice with no number, one line item. Confirm `total_cents = line_total - discount + tax` after a hand-run UPDATE simulating the Phase 2 service.
- [ ] Insert two installment rows summing to the invoice total. Confirm a third row (which would unbalance) fails the application-level invariant when the service runs the check, even though the DB CHECK does not catch it (cross-row).
- [ ] Try to flip an invoice to `sent` with `invoice_number IS NULL`. Expect `chk_invoice_number_when_not_draft` rejection.
- [ ] Try to insert a line with `quantity = 0`. Expect `chk_line_quantity_pos` rejection.
- [ ] Try to insert an invoice with `paid_to_date_cents > total_cents`. Expect `chk_invoice_paid_le_total` rejection.
- [ ] Insert two invitations for the same `(invoice_id, contact_id)`. Expect the UNIQUE to reject the second.
- [ ] Increment the numbering counter from two concurrent psql sessions with `SELECT ... FOR UPDATE`; confirm sequential output and no duplicates. (Gaps are accepted in this design and tested in Phase 2.)
- [ ] Insert an invoice with `legacy_migration_run_id` and confirm it can be selected by that UUID alone. This is the Phase 4b rollback handle.

Deliverable: every Phase 2+ table exists in the production DB. Constraints reject bad data. No UI, no API yet.

Phase 1 validation note, 2026-05-01:

Migrations 018–024 applied on the local dev DB without error. Migrations 018–023 are the six tables; migration 024 (`024_tighten_invoice_line_item_money_checks.py`) adds four follow-up CHECKs (`chk_line_discount_nonneg`, `chk_line_subtotal_nonneg`, `chk_line_tax_nonneg`, `chk_line_total_nonneg`) that an expanded schema smoke caught as missing in 019. ORM models added to [database/models.py](../database/models.py) for `Invoice`, `InvoiceLineItem`, `InvoiceInstallment`, `InvoiceInvitation`, `NumberingState`, `BusinessProfile`.

[tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) covers all six tables. 26 checks pass:

- invoices: draft + line item ok, invalid status rejected, send without number rejected, paid>total rejected, balance inconsistent rejected, revision=0 rejected, duplicate invoice_number rejected.
- invoice_line_items: invalid kind rejected, zero quantity rejected, negative unit price rejected (trips either `chk_line_unit_price_nonneg` or `chk_line_discount_le_subtotal` since both validly fire), discount over subtotal rejected, tax_rate>=1 rejected, negative tax_rate rejected, all four negative-money cases (discount/subtotal/tax/total) rejected.
- invoice_installments: two-row sum to total ok (cross-row sum is the service's job, Phase 2), zero installment rejected, negative installment rejected.
- invoice_invitations: duplicate (invoice, contact) rejected, duplicate public_key rejected, negative view_count rejected.
- numbering_state: singleton CHECK rejects id=2, seq nonneg CHECK rejects -1, concurrent `SELECT ... FOR UPDATE` from two threads produces sequential bumps with no duplicates.
- business_profile: singleton CHECK rejects id=2, tax_rate range CHECK rejects 1.0.
- Plus: `legacy_migration_run_id` rollback handle works (`DELETE WHERE legacy_migration_run_id = '<uuid>'` returns exactly the imported rows).

Pytest-safety: internal helpers are named `check_*` rather than `test_*` so a broad `pytest tests/` run does not collect them as parameterless test functions. `pytest tests/test_invoice_schema_smoke.py --collect-only` now reports zero collected. Direct script execution remains the canonical run path.

Regression smokes all pass: `test_event_documents_smoke`, `test_events_smoke`, `test_booking_smoke`, `test_contacts_smoke`, `test_boutique_experience_smoke`.

VPS-side production migration: deferred until Phase 2 is also ready (so the rebuild ships schema + service in one rollout). The migrations are independent of any application code — they can be applied any time `venv/bin/python -m database.migrations.runner` runs on the VPS.

Phase 1 review iteration, 2026-05-01:

Three findings from a fresh review pass got addressed in-phase rather than deferred:

1. **Negative line money slipped through 019.** The original `invoice_line_items` migration enforced `discount_cents <= quantity * unit_price_cents` and `unit_price_cents >= 0` but not non-negativity on `discount_cents`, `line_subtotal_cents`, `line_tax_cents`, or `line_total_cents`. A real INSERT with negative line totals committed silently. Fixed in migration 024. All downstream migration numbers in this doc bumped by one (Phase 4a is now 025, Phase 13's column drop is now 036).
2. **The first smoke was not pytest-safe.** Internal helpers named `test_X(arg, arg)` got collected by pytest as parameterless tests with fixture errors. Renamed to `check_*` so pytest skips them. The file still runs as a script and is named `test_invoice_schema_smoke.py` to keep the regression-suite glob convention.
3. **Coverage gaps explained how (1) slipped through.** The original smoke claimed "every CHECK constraint" but only covered some. Expanded to provoke every CHECK and UNIQUE in the six Phase 1 tables, plus the new ones from migration 024. The expansion is what surfaced (1) in the first place.

---

## Phase 2: Invoice service + totals + CRUD API

**Status:** Complete 2026-05-01. Service [services/invoice_service.py](../services/invoice_service.py), router [api/routers/invoices.py](../api/routers/invoices.py), wired into [api/server.py](../api/server.py). Smoke [tests/test_invoices_smoke.py](../tests/test_invoices_smoke.py) covers all 14 plan scenarios plus auth gating (17 checks). Three review findings closed in-phase (log enrichment, mark_sent default contact wording, resend notification wording deferred to Phase 7). Not yet on VPS — ships together with Phase 1.

Purpose: a working backend without UI. Curl-verifiable end-to-end. The service layer holds money math; the router stays thin.

### 2.1 Service `services/invoice_service.py`

Pure-Python, no FastAPI imports. Takes a `Session` and returns dataclasses.

Functions:

- `create_invoice(db, *, event_id, contact_id, line_items, installments, due_date=None, terms=None, footer=None, public_notes=None, private_notes=None, po_number=None, actor_user_id) -> Invoice`. Inserts a `draft` invoice with `revision=1` and `invoice_number=NULL`. Inserts `invoice_line_items` rows. Inserts `invoice_installments` rows (typically two: deposit + balance). Calls `_recompute_totals` and `_validate_schedule`. The schedule may be empty for a draft; the validate call only enforces the `SUM(installments) = total` invariant when it's non-empty.
- `update_invoice(db, *, invoice_id, patch, actor_user_id) -> Invoice`. Patches editable fields, replaces line items if provided, replaces the installment schedule if provided, recomputes totals, and bumps `revision` only if `status != 'draft'`. Refuses to edit a `paid` or `cancelled` invoice (raise `InvoiceServiceError("invoice_locked")`). Editing a `sent` or `partial` invoice is allowed but `_validate_schedule` enforces the schedule sum and refuses if any installment marked `paid_at` would be invalidated.
- `mark_sent(db, *, invoice_id, actor_user_id, contact_ids=None) -> Invoice`. Transitions `draft -> sent`. Refuses if line items are empty, the schedule is empty, or the schedule sum differs from `total_cents`. Allocates an `invoice_number` via `_assign_invoice_number` (the only path that ever writes the column). Stamps `sent_at`. Creates one `invoice_invitations` row per contact in `contact_ids` (defaulting to `invoice.contact_id`, the billing contact — usually the event's primary contact, but the invoice can be created against a different contact if the customer wants the bill to land elsewhere) using `secrets.token_urlsafe(32)` for `public_key`. Reuses the existing invitation key on resend if the row already exists for that contact. Phase 7 wires the actual email send and notification-job enqueue; Phase 2 just stamps and returns.
- `resend_invoice(db, *, invoice_id, contact_ids, actor_user_id)`. Allowed on `sent` or `partial`. Reuses each existing invitation key, updates `sent_at` and `last_resent_at`. Phase 7 enqueues the actual notification jobs against the refreshed invitations; Phase 2 only flips the timestamps so the Phase 7 worker has a fresh signal to act on.
- `cancel_invoice(db, *, invoice_id, reason, actor_user_id) -> Invoice`. Transitions any non-paid status to `cancelled`, stamps `cancelled_at` and `cancellation_reason`. The `invoice_number` is preserved forever (audit trail). Refunds-to-customer happen in Phase 6.
- `get_invoice(db, invoice_id) -> InvoiceDetail` (dataclass with line items, installments, and active invitations inline).
- `list_invoices_for_event(db, event_id, *, include_deleted=False) -> list[InvoiceSummary]`.
- `_recompute_totals(invoice, line_items)` — pure function, no DB access. Computes subtotal/tax/total per line, sums up, sets `invoice.subtotal_cents`, `invoice.tax_cents`, `invoice.total_cents`. Banker's rounding (half-even) on every cents conversion.
- `_validate_schedule(invoice, installments)` — enforces `SUM(installments.amount_cents) = invoice.total_cents` when installments is non-empty. Raises `InvoiceServiceError("schedule_unbalanced", ...)` with the actual sum and expected total in the message so the editor can show a useful error.
- `_assign_invoice_number(db) -> str` — wraps the `numbering_state` row lock and returns `INV-2026-000123`. Called only from `mark_sent` and the legacy migration in Phase 4. Drafts never call it.

Money math rule: every multiplication that produces cents (`quantity * unit_price`, `subtotal * tax_rate`) does the math in `Decimal` with `ROUND_HALF_EVEN` to integer cents. Never `float`. The line-level rounding error and the invoice-level rounding error are stamped onto each line individually so the printed PDF adds up to the printed total.

Rounding rule, locked: v1 computes tax per line, rounds each line to cents, then sums the displayed line totals into the invoice total. The printed invoice is internally consistent because the total is the sum of the printed lines. Do not add an invoice-level `rounding_adjustment_cents` column unless a future invoice-level tax regime requires reconciling against tax computed once on the subtotal.

Numbering semantics, locked: numbers are allocated on first send. A cancelled invoice keeps its number forever. A deleted draft never had a number. Gaps in the visible sequence are expected and benign — they correspond to drafts that were thrown away — and the cancelled rows themselves explain the rest. This is the standard auditable accounting pattern; "gap-free" sequences require holding numbers under transactional locks until final issuance, and the cost is more complex than the benefit at this scale.

### 2.2 Router `api/routers/invoices.py`

Mount at `/api/invoices`, with a sibling helper mounted at `/api/events/{event_id}/invoices`.

- `POST /api/events/{event_id}/invoices` — body is `InvoiceCreate` (line items inline). Returns 201 with `InvoiceDetail`.
- `GET /api/events/{event_id}/invoices` — list invoices for an event. Supports `?status=` and `?include_deleted=`.
- `GET /api/invoices` — global invoice search for staff. Supports `?q=` (invoice number or customer name), `?status=`, `?date_from=`, `?date_to=`, and `?event_id=`. Default sort is newest sent/created first. This lands before the UI because staff will search by invoice number as soon as numbers exist.
- `GET /api/invoices/{id}` — full detail with line items.
- `PATCH /api/invoices/{id}` — partial update. Line items array fully replaces if present.
- `POST /api/invoices/{id}/send` — transitions to `sent` (Phase 7 wires the actual email; v1 just flips status and stamps `sent_at`).
- `POST /api/invoices/{id}/cancel` — body `{ reason }`. Transitions to `cancelled`.
- `DELETE /api/invoices/{id}` — soft delete. Refuses if any allocated payments exist (Phase 6 enforces).

Every route requires `get_current_user`. Structured log line on every state change with `user_id`, `event_id`, `invoice_id`, `from_status`, `to_status`, matching the [event_documents.py](../api/routers/event_documents.py) pattern.

Pydantic schemas: `InvoiceCreate`, `InvoiceUpdate`, `LineItemInput`, `LineItemResponse`, `InvoiceSummary`, `InvoiceDetail`. `model_fields_set` doctrine for clearable optional fields, matching [api/routers/contacts.py](../api/routers/contacts.py).

### 2.3 Smoke tests `tests/test_invoices_smoke.py`

- Create a draft invoice with three line items of varying tax rates and a two-row installment schedule. Totals match hand-computed expected. Schedule sum equals total.
- Try to create a draft with a schedule that doesn't sum to total. 422 `schedule_unbalanced`.
- Patch the invoice with new line items. Totals recompute. `revision` does NOT bump on draft. `invoice_number` remains NULL.
- `POST /send`. Status flips to `sent`. `sent_at` populated. `invoice_number` allocated and matches `INV-{year}-{nnnnnn}`. An `invoice_invitations` row exists for the event's primary contact with a 64-char `public_key`.
- Try to send with an empty schedule. 422 `schedule_required`.
- Try to send with line items but a schedule sum that drifted from total because of a recent line-item edit. 422 `schedule_unbalanced`.
- Patch a `sent` invoice (e.g. update public_notes). `revision` bumps to 2. `invoice_number` does not change.
- Patch a `paid` invoice. 422 `invoice_locked`.
- Cancel a `sent` invoice. Status flips to `cancelled`. `invoice_number` preserved.
- Create a new draft, immediately delete. List excludes it. Numbering counter unchanged (the draft never had a number).
- Two concurrent sends produce sequential numbers with no duplicates. Cancelled invoices in between show as gaps in the displayed sequence; the test asserts this is expected.
- Numbering rolls over correctly when the year crosses (mock `now()` in the service).
- Resend an invoice to the same contact. Existing invitation row keeps its key; only `sent_at` updates.
- Global invoice search finds a sent invoice by invoice number, customer display name, status, and date range.

Deliverable: backend works end-to-end via curl. Frontend still shows the legacy uploader.

Phase 2 validation note, 2026-05-01:

Service [services/invoice_service.py](../services/invoice_service.py) shipped with: `create_invoice`, `update_invoice`, `mark_sent`, `resend_invoice`, `cancel_invoice`, `soft_delete_invoice`, `get_invoice_detail`, `list_invoices_for_event`, `search_invoices`, plus the internals `_compute_line_amounts`, `_recompute_totals`, `_validate_schedule`, `_assign_invoice_number`, `_ensure_invitations`. Money math runs in `Decimal` with `ROUND_HALF_EVEN`. The locked rounding rule holds: invoice `total_cents = SUM(line_total_cents) - discount_cents`, so the printed lines always sum to the printed total.

Router [api/routers/invoices.py](../api/routers/invoices.py) exposes nine endpoints across two routers wired into [api/server.py](../api/server.py):

- `POST /api/events/{event_id}/invoices` — create (returns 201)
- `GET /api/events/{event_id}/invoices` — per-event list with `?status=` and `?include_deleted=`
- `GET /api/invoices` — global staff search with `?q=`, `?status=`, `?event_id=`, `?date_from=`, `?date_to=`, `?include_deleted=`, `?limit=`
- `GET /api/invoices/{id}` — detail with line items + installments + invitations inline
- `PATCH /api/invoices/{id}` — partial update, `model_fields_set` doctrine, line_items and installments arrays REPLACE all rows when present
- `POST /api/invoices/{id}/send` — draft → sent, allocates `invoice_number`, creates invitation
- `POST /api/invoices/{id}/resend` — re-emit invitations, reuses existing keys
- `POST /api/invoices/{id}/cancel` — preserves number forever
- `DELETE /api/invoices/{id}` — soft delete

Every route auth-gated via `get_current_user`. Errors map to status codes via `_ERROR_STATUS_MAP` (404 for not-found, 422 for transition/locking/validation, 400 fallback). Structured log lines on every state change match the `event_documents.py` pattern.

[tests/test_invoices_smoke.py](../tests/test_invoices_smoke.py) covers all 14 Phase 2.3 cases plus auth gating. 17 checks pass:

- 401 on every auth-gated route; admin login then bearer header for the rest.
- Create draft with three line items (no-tax, 8.25%, 8.25% banker's-round-to-even) and a two-row schedule. Subtotal=$1250, tax=$20.62, total=$1270.62. Sum of printed line totals equals invoice total.
- Create with unbalanced schedule (sum=$0.01 vs total=$1270.62) returns 422 `schedule_unbalanced` with both `schedule_sum_cents` and `total_cents` in the error body.
- Patch a draft replacing line items: totals recompute, `revision` stays at 1, `invoice_number` stays null.
- POST /send: status flips to `sent`, `sent_at` populated, `invoice_number` matches `INV-{year}-NNNNNN` (15 chars), one `invoice_invitations` row exists with a `public_key` of length ≥ 30.
- Send with empty schedule → 422 `schedule_required`.
- Send with empty line items → 422 `line_items_required`.
- Send with drifted schedule (lines patched, schedule not) → 422 `schedule_unbalanced`.
- Patch a `sent` invoice (public_notes update) → `revision` bumps to 2, `invoice_number` unchanged.
- Patch a `paid` invoice (status forced via SQL since Phase 6 isn't here yet) → 422 `invoice_locked`.
- Cancel a `sent` invoice → status `cancelled`, `invoice_number` preserved, `cancellation_reason` recorded.
- Create + immediately delete a draft: list excludes it, `numbering_state.invoice_seq` unchanged (drafts never burn a number).
- Two threads each grabbing `SELECT ... FOR UPDATE` on `numbering_state.id=1` allocate sequential `INV-2026-NNNNNN` numbers with no duplicates.
- Year rollover: with current invoices wiped and `invoice_year` set to `current-1`, the next send produces `INV-{current_year}-000001`. Test restores numbering_state on exit.
- Resend to the same contact: existing invitation row keeps its `public_key`, only `last_resent_at` changes.
- Global search: by invoice number prefix, by contact name fragment, by status, by date range covering today, by event_id — every query finds the test invoice.

Pytest-safety: internal helpers in this file are named `check_*`. `pytest tests/test_invoices_smoke.py --collect-only` reports zero — no fixture errors, no false reds. Same convention as the schema smoke.

Regression suite all green: `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_booking_smoke`, `test_contacts_smoke`, `test_boutique_experience_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_notifications_smoke`.

VPS-side production migration: still deferred together with Phase 1's migrations. The combined Phase 1+2 deploy ships migrations 018–024, the new service module, the router, and `api/server.py` updates in one rollout. No new write paths added (no PDFs yet — those land in Phase 8) so the systemd `ReadWritePaths` line does not need to change for this rollout.

Phase 2 review iteration, 2026-05-01:

Three findings from a fresh review pass got addressed in-phase:

1. **Structured logs were missing planned fields.** The plan called for `user_id`, `event_id`, `invoice_id`, `from_status`, `to_status` on every state-change log line; the first cut included only `user_id` and `invoice_id` on most events. Each handler now `_peek_invoice` for the pre-state status and event_id (one extra PK lookup per write), then logs the full set: `event_id`, `from_status`, `to_status`, plus action-specific extras (`reason` on cancel, `revision` on update, `invoice_number` on send, `contact_ids` on resend). For create, `from_status=None`; for delete, `from_status` and `to_status` are both the pre-state status since the soft-delete doesn't change the status field.
2. **`mark_sent` default contact wording was misleading.** The plan said invitations default to "the event's primary contact"; the code defaults to `invoice.contact_id`, which is set from the event's primary contact at create time but can be explicitly overridden by the caller. Doc updated to reflect: "defaulting to `invoice.contact_id`, the billing contact — usually the event's primary contact, but the invoice can be created against a different contact if the customer wants the bill to land elsewhere."
3. **`resend_invoice` notification-job wording belonged in Phase 7.** The plan said `resend_invoice` "enqueues fresh notification jobs", but Phase 7 owns the notification kinds (`invoice_sent`, `quote_sent`, etc.) and the email transport. Phase 2 only flips `sent_at` and `last_resent_at` so the Phase 7 worker has a fresh signal to act on. Doc updated to reflect this split explicitly.

Re-run after the log change: `tests/test_invoices_smoke.py` still 17/17. Regression sweep on `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_booking_smoke`, `test_contacts_smoke`, `test_boutique_experience_smoke` all green. Pytest still collects zero from the script-style smokes (exit code 5 from `pytest --collect-only` is the expected "no tests collected" signal, not a failure).

---

## Phase 3: Invoice editor UI

**Status:** Complete 2026-05-01. Backend slice for `business_profile` (service + router + smoke) shipped alongside the frontend so the editor can pre-fill defaults and the PDF generator (Phase 8) has something to render against. Frontend: invoice editor drawer, replaced Invoices tab, business profile settings page, and global invoice search page all live. Manual browser smoke still pending — runs on `admin.shopbellasxv.com` after VPS rebuild and service restart.

Purpose: replace the upload-based Invoices tab with a structured editor that creates real `invoices` rows. The legacy uploader stays available behind an "Attach external invoice PDF" affordance for the rare case where staff are receiving an invoice from a third-party vendor (e.g. an alterations subcontractor) rather than issuing one.

Tasks:

- [ ] Add `listInvoices`, `getInvoice`, `createInvoice`, `updateInvoice`, `sendInvoice`, `cancelInvoice`, `deleteInvoice` to [frontend/src/services/api.js](../frontend/src/services/api.js).
- [ ] Replace the body of `frontend/src/pages/event/tabs/Invoices.jsx` with a list view. Each row: invoice number, total, status pill, balance, deposit-due chip if applicable. Click opens an editor drawer.
- [ ] Build `frontend/src/components/InvoiceEditor.jsx`. Sections:
  - Header: customer name (from event primary contact), invoice number (placeholder `Draft` until first send, then read-only), issue date, derived `due_date` (read-only, equals max installment date).
  - Line items: a sortable list. Each row has description, quantity, unit price, optional discount, optional tax rate. "Add line" button appends. Trash icon removes. Drag handle reorders.
  - Payment schedule: a sub-list of installments. Defaults to two rows on new invoice: `Deposit` (50% of total, due 14 days from issue) and `Balance` (50%, due 60 days before event date if known, else 30 days from issue). Staff can edit labels, amounts, dates, or add a third row to make it a payment plan. Bottom of section shows a live "Schedule total: $X / Invoice total: $Y" indicator that turns red when the two diverge.
  - Totals panel: subtotal, discount, tax, total. Live-recomputed client-side as the user types and re-verified server-side on save.
  - Footer: terms, footer, public notes, private notes, PO number — collapsed under "More options" by default. Defaults pre-fill from `business_profile`.
  - Action bar: Save Draft, Send, Cancel, Delete. Send opens a confirm dialog with the customer's email pre-filled and disables itself if line items are empty or the schedule is unbalanced.
- [ ] Currency input component that stores cents but displays dollars. Reuse for unit price, discount, installment amount. Mask: `$1,234.56`.
- [ ] When invoice total changes (line items edited), the editor offers a "Rebalance schedule proportionally" affordance rather than silently rewriting the schedule. Staff explicit-click confirms.
- [ ] Empty-state copy: "No invoices on this event yet. Create one to send a deposit request or final bill." (No em dashes, no listy patterns.)
- [ ] React Query keys: `['event', id, 'invoices']` and `['invoice', id]`. Invalidate on every mutation.
- [x] Tab badge: replace the existing `outstanding_invoices` count source after Phase 4b lands. Done 2026-05-01: `document_counts` re-sources from canonical `invoices` and the badge wiring is unchanged.
- [x] Hide the legacy uploader behind an "Attach external PDF" link in the tab header. Done 2026-05-01: the button is now a real upload flow (Phase 4b) that posts `kind='external_invoice'` and surfaces the result in a new "External attachments" section below the canonical invoice list.
- [ ] Add an invoice search entry point from the global dashboard/search surface. Minimum v1: invoice number, customer name, status, and date range. No bulk actions in v1; the list layout should not paint us into a corner, but bulk send/mark-paid/print waits until staff ask.

Business profile editor (also Phase 3, since Phase 8 depends on it):

- [ ] New page `frontend/src/pages/BusinessProfile.jsx` under Settings. Form fields for every column on `business_profile`, with logo upload (reusing `document_storage`).
- [ ] Defaults from `business_profile` flow into the invoice editor's terms/footer/payment-instructions fields when blank.

Smoke test (manual on `admin.shopbellasxv.com` after rebuild):

- Create an invoice with two lines totaling $1,250 and an 8.25% tax rate. Total reads $1,353.13. Default schedule shows two rows summing to $1,353.13. Save as draft. Reload. Persists.
- Edit the second line's quantity so the total changes. Editor warns that the schedule is now unbalanced. Click Rebalance. Schedule rows update proportionally. Save. Persists.
- Set the deposit row to $200 (rebalance pushes the rest into the balance row). Send. Confirm dialog blocks because schedule no longer sums to total; user adjusts and resends.
- Send a balanced invoice. Status pill flips to `Sent`, invoice number `INV-2026-000001` appears, list row shows the deposit chip.
- Cancel a sent invoice. Status pill flips to `Cancelled`. Editor goes read-only. Number is preserved.
- Create a fresh invoice immediately after a cancellation. Number is `INV-2026-000002` (gap of 1 from the cancelled invoice). Smoke test asserts this gap is intentional.
- Try to delete a sent invoice with a recorded payment (after Phase 6). Server 409s with a readable error.

Deliverable: shop staff can author, edit, and send a real invoice from inside an event without leaving `admin.shopbellasxv.com`.

Phase 3 validation note, 2026-05-01:

Backend slice (small) shipped with the Phase 3 frontend so the editor has APIs to call:

- New service [services/business_profile_service.py](../services/business_profile_service.py) — `get_profile`, `update_profile`, `set_logo`, `remove_logo`. Logo upload reuses `services/document_storage` under a fixed `business/logo.<ext>` key so the existing disk-space guards apply. Replacing the logo with a different extension deletes the prior on-disk file so we don't leave orphans.
- New router [api/routers/business_profile.py](../api/routers/business_profile.py) at `/api/business-profile`: `GET`, `PATCH`, `POST /logo`, `DELETE /logo`, `GET /logo`. Auth-gated; the Phase 7 portal will fetch the logo server-side so customers never need a token.
- Wired into [api/server.py](../api/server.py) at prefix `/api/business-profile`.
- Smoke [tests/test_business_profile_smoke.py](../tests/test_business_profile_smoke.py) covers 13 scenarios including 401 on every route, get/patch round trip, unknown-field rejection, empty-legal-name rejection, invalid tax rate rejection (caught by Pydantic before the service), country normalization to upper, PNG/SVG round trip, ext-replacement deletion of the old file, type/size rejection.

Frontend pieces:

- [frontend/src/utils/money.js](../frontend/src/utils/money.js) — `formatUSD`, `formatDollars`, `parseDollars` with banker's rounding doctrine, `parseTaxRate`/`formatTaxRate` for percent input. Centralizes display logic that was previously inlined in the legacy Invoices.jsx.
- [frontend/src/components/CurrencyInput.jsx](../frontend/src/components/CurrencyInput.jsx) — reusable cents-storing dollar input. Stores integer cents; the user types dollars; commit happens on blur or Enter. Used everywhere the editor takes money.
- [frontend/src/services/api.js](../frontend/src/services/api.js) — added invoice CRUD (`listInvoices`, `getInvoice`, `createInvoice`, `updateInvoice`, `sendInvoice`, `resendInvoice`, `cancelInvoice`, `deleteInvoice`, `searchInvoices`) and business profile (`getBusinessProfile`, `updateBusinessProfile`, `uploadBusinessLogo`, `deleteBusinessLogo`, `fetchBusinessLogoBlob`).
- [frontend/src/components/InvoiceEditor.jsx](../frontend/src/components/InvoiceEditor.jsx) — full editor in a right-anchored drawer (920px wide). Sections: header (customer, number, issue date, derived due date), line items (with up/down reorder buttons; v1 ships without drag handles since `@dnd-kit` is in deps but not yet wired — TODO for v1.1), payment schedule (default deposit/balance generator, `Rebalance proportionally` action when drift), totals panel, collapsed "More options" with terms/footer/notes/PO number pre-filled from `business_profile`, action bar (Save Draft, Send, Cancel, Delete) with confirm dialogs. Send button disabled when line items empty or schedule unbalanced. Backend errors translate to readable messages ("Payment schedule ($X) does not match invoice total ($Y)").
- [frontend/src/pages/event/tabs/Invoices.jsx](../frontend/src/pages/event/tabs/Invoices.jsx) — replaced. Now a list view with totals header (billed/paid/outstanding), `New invoice` button, and `Attach external PDF` placeholder for Phase 4b. Each row click opens the editor drawer for that invoice. Empty-state copy follows repo voice: "No invoices on this event yet. Create one to send a deposit request or final bill."
- [frontend/src/pages/BusinessProfile.jsx](../frontend/src/pages/BusinessProfile.jsx) — full settings form with sections (Identity, Contact, Address, Default tax, Invoice defaults, Logo). Logo preview as 96px Avatar with upload/replace/delete. Tax rate input accepts both `8.25%` and `0.0825` and normalizes via `parseTaxRate`.
- [frontend/src/pages/Settings.jsx](../frontend/src/pages/Settings.jsx) — index now lists Business profile as a tappable section.
- [frontend/src/pages/InvoicesGlobal.jsx](../frontend/src/pages/InvoicesGlobal.jsx) — `/invoices` route, search by number/customer/status/date range. URL query params drive the search so links are shareable. Each row routes to `/events/<id>/invoices` for the editor.
- [frontend/src/pages/Dashboard.jsx](../frontend/src/pages/Dashboard.jsx) — placeholder replaced with two cards (Pipeline, Invoices) so staff have an entry point to invoice search from the home page.
- [frontend/src/App.jsx](../frontend/src/App.jsx) — added `/invoices` and `/settings/business-profile` routes.

Validation:

- `cd frontend && npm run lint` passed clean.
- `cd frontend && npm run build` passed. Vite still reports the existing large-chunk warning for the main JS bundle (pre-existing, mentioned in earlier phase docs).
- Backend regression sweep all green: `test_business_profile_smoke`, `test_invoices_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_booking_smoke`, `test_contacts_smoke`, `test_boutique_experience_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_notifications_smoke`.
- Manual browser smoke still pending — happens on `admin.shopbellasxv.com` after VPS rebuild and service restart.

Carryover for VPS deploy: ships together with Phase 1+2 in one rollout. No new env vars. New write paths covered by existing `/var/lib/bellas-xv/uploads` `ReadWritePaths` (logo file lands under `business/logo.<ext>` in the same root). The combined Phase 1+2+3 deploy now bundles migrations 018–024, three new backend modules, two new routers wired into server.py, and a substantially expanded frontend bundle.

Known v1 gaps (do not block ship; tracked here for v1.1):

- Drag-and-drop reorder on line items uses up/down arrows. `@dnd-kit/sortable` is in the deps but the editor uses arrow buttons for v1 simplicity. Upgrade is a self-contained line-items component swap.
- "Attach external PDF" button is a placeholder alert. Wires up properly in Phase 4b when `event_documents.kind='external_invoice'` is the canonical attachment shape.
- Bulk actions (multi-select, mark paid in bulk, batch print) are deliberately out of scope for v1 per the plan.
- **Invoice editor polish backlog (pinned 2026-05-02 by user during deploy smoke).** Live walkthrough on `admin.shopbellasxv.com` exposed cosmetic + UX changes the user wants but not now. Picked up in a dedicated polish session before Phase 8 PDFs (PDFs render whatever the editor produces, so the editor needs to be settled first). User has not yet enumerated the specific changes — capture them on the next pass through the Invoices tab.

Phase 3 review iteration, 2026-05-01:

Two real frontend bugs caught in review and fixed in-phase:

1. **New draft did not become editable after first save.** `InvoiceEditor` accepted `invoiceId` as a prop from the parent and stayed in create mode after `createInvoice` succeeded. Effects: a second click on Save would call `createInvoice` again and produce a duplicate draft, and Send stayed disabled because `!isEditing`. Fix: editor now accepts an `onCreated(newId)` prop and the parent (`Invoices.jsx`) hands the new id back via `setEditingId(newId)`. The cache seed via `queryClient.setQueryData(['invoice', newId], data)` keeps the upcoming `useQuery` synchronous so there's no flicker as the drawer transitions from "new" to "edit existing".
2. **Logo preview likely 401'd in the browser.** The original `businessLogoUrl(updated_at)` helper returned a plain `/api/business-profile/logo?v=...` URL fed into `<Avatar src>`. The browser does not attach the bearer token to a plain `<img>` request, so the auth-gated endpoint would return 401 and the Avatar would render broken. Fix: replaced `businessLogoUrl` with `fetchBusinessLogoBlob()` that does an Axios fetch (interceptor adds the token) and returns the image as a Blob; `BusinessProfile.jsx` runs a `useEffect` that creates an object URL from the blob and revokes it on cleanup or on `has_logo` / `updated_at` change.

Re-validated after fixes: lint clean, build succeeds, backend regression sweep all green. Manual browser smoke is still the next ask before declaring Phase 3 fully done in production.

---

## Phase 4a: Split document schema from invoice financials

**Status:** Code-side complete and validated locally 2026-05-01. Migration 025 applied to local DB. Migration to staging + production deferred to the combined Phase 1+2+3+4a deploy.

Purpose: make the document table capable of representing external invoice PDFs without doing any financial data migration yet. This creates a clean rollback point before the maintenance-window work.

This phase is schema-compatible. It deploys while the app still reads legacy `event_documents.kind='invoice'` rows for counts and kanban.

### 4a.1 Migration `025_split_documents_from_invoices.py`

Schema changes:

- Add `linked_invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL` to `event_documents`. Nullable. Populated for migrated rows.
- Update the `chk_event_documents_kind` CHECK to include `external_invoice` in the allowed set. Keep `invoice` in the allowed set for the duration of Phase 4 so existing rows don't violate the constraint mid-migration.
- Update the `chk_event_documents_invoice_fields_only_on_invoice` CHECK so the four `invoice_*` columns may remain populated only on `kind IN ('invoice', 'external_invoice')` during the rollback season. All other document kinds must keep those fields NULL. Phase 13 drops the four columns and removes `invoice` from the allowed kind set.
- Add a fourth CHECK `chk_event_documents_linked_invoice_only_on_external` so the new `linked_invoice_id` may only be populated when `kind='external_invoice'`. Plain `'document'` rows and rollback-season `'invoice'` rows must keep it NULL — this means a stray UPDATE cannot smuggle a canonical-invoice pointer onto the wrong row, and Phase 4b's data migration is the only path that turns a legacy `'invoice'` row into a `'external_invoice'` row that links to its canonical twin.
- Add a partial index `idx_event_documents_linked_invoice ON event_documents(linked_invoice_id) WHERE linked_invoice_id IS NOT NULL`. Cheap to maintain (only covers the small fraction of rows that point at a canonical invoice); makes the Phase 4b/8 reverse lookup ("what file PDFs are attached to this canonical invoice?") an index hit rather than a seq scan over event_documents.

Validation:

- [x] Migration applies on local DB with existing `kind='invoice'` rows unchanged.
- [x] Schema smoke (`tests/test_invoice_schema_smoke.py`) extended with three Phase 4a checks: kind enum (incl. `external_invoice`), `invoice_*` columns scoped to `invoice`/`external_invoice` kinds, `linked_invoice_id` scoped to `external_invoice` plus `ON DELETE SET NULL` behavior. Total schema smoke now 29 checks, all green.
- [x] `tests/test_event_documents_smoke.py` regression sweep still green: kind filter, soft delete, size rollback, path traversal, board flag, and counts all unchanged.
- [x] Canonical invoice routes from Phases 1–3 unaffected: `tests/test_invoices_smoke.py`, `tests/test_business_profile_smoke.py`, `tests/test_invoice_schema_smoke.py` all green after migration 025.
- [x] Full local regression sweep green: `test_invoice_schema_smoke`, `test_invoices_smoke`, `test_business_profile_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_booking_smoke`, `test_boutique_experience_smoke`, `test_contacts_smoke`, `test_notifications_smoke`.
- [ ] Migration applies on production with existing `kind='invoice'` rows unchanged. Deferred to combined Phase 1+2+3+4a deploy. Production has zero `kind='invoice'` rows today (verified Phase 0), so this is just a no-op DDL apply on the legacy data side.
- [ ] Existing Invoices tab still loads the legacy uploads after deploy. **Note:** Phase 3 already replaced the file-uploader-based Invoices tab with the canonical-invoice list view, so on production this validation collapses to "Documents tab still lists files". Will be confirmed as part of the Phase 3 manual browser smoke after VPS rebuild.

Code changes:

- [database/migrations/025_split_documents_from_invoices.py](../database/migrations/025_split_documents_from_invoices.py) — schema changes above, plus the four new CHECK + index.
- [database/models.py](../database/models.py) `EventDocument` gains `linked_invoice_id`. The router and service layers are intentionally left alone in 4a — uploads still produce `kind='invoice'`, kanban still reads legacy rows. Phase 4b is when the upload route swaps to `kind='external_invoice'` and the kanban subquery rewrites against `invoices`.
- [tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) gains three Phase 4a `check_*` functions and a `user_id` lookup in `main()` to populate `event_documents.uploaded_by_user_id`.

Deliverable: the schema can hold canonical invoices plus external invoice PDFs, but no production data has moved yet. The application still behaves identically to pre-4a — this phase is purely a schema enabler for 4b.

---

## Phase 4b: Migrate legacy rows and swap application reads

**Status:** Code-side complete and validated locally 2026-05-01. Migration 026 applied to local DB (no-op against zero legacy rows; round-tripped against synthesized rows in test). All app-side swaps shipped. Production deploy still pending; bundles with Phase 1+2+3+4a in a single rollout.

Purpose: lift production data from the upload-based v1 invoice tab into the canonical `invoices` table, retag the source rows as `external_invoice` attachments, and stop creating `kind='invoice'` event_documents rows entirely. Rebuild the kanban `has_outstanding_invoice` subquery against the new source. The legacy `event_documents.invoice_*` columns survive Phase 4b unread for one-season rollback safety.

This is the highest-risk phase. It ships as an application PR plus a maintenance-window migration run after Phase 4a is already deployed.

### 4b.1 Migration/data script `026_migrate_event_documents_invoices.py`

Data migration steps, all in one transaction:

For each row in `event_documents` where `kind='invoice'` AND `deleted_at IS NULL`, ordered by `created_at ASC`:

- Insert a row into `invoices` with:
  - `event_id` from the document.
  - `contact_id` resolved by joining to `events.primary_contact_id`.
  - `status` mapped from the legacy `invoice_status` field: `draft -> draft` (no number allocated), `sent -> sent` (number allocated below), `paid -> paid` (number allocated), `void -> cancelled` (number allocated, since cancelled rows in the new shape always have a historical number).
  - `invoice_number` allocated from `numbering_state.invoice_seq` for any non-draft status. Drafts stay numberless. The migration walks `numbering_state` once at the start and reserves a contiguous block large enough for all non-draft legacy rows, then increments `invoice_seq` to the end of the block. (Concurrent app traffic during the maintenance window would also draw from this counter; the migration runs during a maintenance window with the API stopped.)
  - `total_cents = invoice_amount_cents`, `subtotal_cents = invoice_amount_cents`, `tax_cents = 0`.
  - `paid_to_date_cents = invoice_amount_cents` if status is `paid`, else `0`.
  - `balance_cents = total_cents - paid_to_date_cents`.
  - `issue_date = COALESCE(invoice_issued_at::date, created_at::date)`.
  - `paid_at = invoice_paid_at`.
  - `created_by_user_id = uploaded_by_user_id`.
  - `created_at = event_documents.created_at` (preserve historical timestamp).
  - `legacy_migration_run_id = <uuid generated at migration start>`.
- Insert a single `invoice_line_items` row: `description='Imported from uploaded PDF'`, `quantity=1`, `unit_price_cents=total_cents`, `tax_rate=0`, `line_total_cents=total_cents`.
- Insert a single `invoice_installments` row: `label='Balance'`, `amount_cents=total_cents`, `due_date=issue_date+30 days`, `paid_at=invoice_paid_at`. (Legacy rows had no schedule; we materialize a single-row schedule so the new invariants hold.)
- Update the source `event_documents` row: `kind='external_invoice'`, `linked_invoice_id=<new invoice id>`. The four `invoice_*` columns stay populated (rollback safety) but the application stops reading them.

After every legacy row is migrated:

- Application changes shipped in the same PR:
  - Update `services/event_service.py` `outstanding_subq` to select from `invoices` where `status IN ('sent', 'partial') AND deleted_at IS NULL`, joined on `event_id`.
  - Update `api/routers/event_documents.py` `document_counts` so `outstanding_invoices` comes from `invoices`. The `document` count keeps its existing source. The `invoice` count is removed from the response (replaced by an `external_invoice` count for the legacy attachment list).
  - Update the upload route in `api/routers/event_documents.py` to reject new `kind='invoice'` uploads with a 422 `kind_retired` error. The frontend's "Attach external PDF" path switches to `kind='external_invoice'` with an optional `linked_invoice_id` form field.
  - Update [frontend/src/pages/event/tabs/Invoices.jsx](../frontend/src/pages/event/tabs/Invoices.jsx) to render the migrated invoices as canonical rows and the linked PDFs as a "Original uploaded PDF" download chip on each row's detail.

### 4b.2 Validation

- [x] Migration 026 round-trips against synthesized legacy rows in all four statuses (draft/sent/paid/void → draft/sent/paid/cancelled). Test seeded four rows at distinct `created_at` timestamps, ran the upgrade, asserted: contiguous numbering allocation in `created_at` order, money math (`paid` rows get full payment + zero balance, `cancelled` carries cancelled_at, `draft` keeps no number), single synthetic line item per lifted invoice, single synthetic installment per lifted invoice with `due_date = issue_date + 30 days`, source rows retagged to `external_invoice` with correct `linked_invoice_id`. Pre-state and post-state of `numbering_state.invoice_seq` confirmed the reservation moved by exactly the non-draft count.
- [x] Rollback handle exercised: deleting all `invoices` with the test run_id, restoring source `kind='invoice'` and clearing `linked_invoice_id` returns the dataset to its pre-migration shape. Source `invoice_*` columns are untouched throughout the lift, so the rollback is lossless.
- [x] Backend Phase 4b swaps shipped:
  - [services/event_service.py](../services/event_service.py) `outstanding_subq` re-sources from `Invoice` (status IN `('sent', 'partial')`, `deleted_at IS NULL`). Small UX broadening: a partial-pay invoice now lights the kanban badge where pre-Phase-4b it would not.
  - [api/routers/event_documents.py](../api/routers/event_documents.py) `document_counts` returns `{document, external_invoice, outstanding_invoices}`. Document and external_invoice counts come from one event_documents query; outstanding_invoices from a second query against the canonical invoices table.
  - Upload route's `_DocumentKind = Literal["document", "external_invoice"]`. Legacy `kind='invoice'` returns 422 (FastAPI form validation). New optional `linked_invoice_id` form field; route validates the target invoice belongs to the same event and returns `linked_invoice_id_only_on_external_invoice` / `linked_invoice_id_not_on_event` 422 errors for the wrong shapes.
  - PATCH route's invoice_* fields return `422 invoice_fields_retired` when written. The columns survive on the row for read so older clients and the rollback story still work.
  - `DocumentResponse` now exposes `linked_invoice_id` so the frontend can render attachment-to-canonical links.
- [x] Frontend Phase 4b swaps shipped:
  - [frontend/src/services/api.js](../frontend/src/services/api.js) `uploadEventDocument` accepts optional `linkedInvoiceId`.
  - [frontend/src/pages/event/tabs/Invoices.jsx](../frontend/src/pages/event/tabs/Invoices.jsx) "Attach external PDF" button is now a real flow: hidden file input → upload as `kind='external_invoice'` → invalidate documents + counts queries → snackbar feedback. New "External attachments" section below the canonical invoice list shows uploaded vendor PDFs with a Download button each; if a PDF is linked to a canonical invoice (Phase 4b lift, or future opt-in linking), the row shows "linked to INV-YYYY-NNNNNN".
- [x] [tests/test_event_documents_smoke.py](../tests/test_event_documents_smoke.py) rewritten end-to-end: drives a canonical invoice through the service layer, asserts the new `external_invoice` count and the `outstanding_invoices` count source, asserts kanban `has_outstanding_invoice` rises and falls with canonical-invoice status changes, asserts the legacy upload + PATCH paths return 422 with the right error codes, asserts both `linked_invoice_id` rejection paths.
- [x] Full local regression sweep green: `test_event_documents_smoke`, `test_invoice_schema_smoke`, `test_invoices_smoke`, `test_business_profile_smoke`, `test_events_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_booking_smoke`, `test_boutique_experience_smoke`, `test_contacts_smoke`, `test_notifications_smoke`. Frontend `npm run lint` clean, `npm run build` succeeds with the pre-existing large-chunk warning.
- [x] `event_documents.invoice_*` columns confirmed NOT dropped in Phase 4b — Phase 13 owns the column drop and the removal of `invoice` from the kind set.

VPS-side validation deferred to deploy:

- [ ] Pre-migration: snapshot `SELECT id, invoice_status, invoice_amount_cents, invoice_paid_at, uploaded_by_user_id, created_at FROM event_documents WHERE kind='invoice' AND deleted_at IS NULL` to a flat file under `~/migrations/026_pre_snapshot.json`. Production count is zero today (verified Phase 0), so the snapshot is empty by design — still capture it as a rollback marker.
- [ ] Run the migration on a staging copy of the DB with the Phase 4b app code. Diff: `SELECT count(*), SUM(invoice_amount_cents) FROM event_documents WHERE kind='invoice'` from the snapshot must equal `SELECT count(*), SUM(total_cents) FROM invoices WHERE legacy_migration_run_id = '<run uuid>'` after the migration.
- [ ] Confirm kanban renders correctly with the swapped subquery on staging.
- [ ] Confirm the frontend loads without console errors on event overview, documents, invoices, and pipeline (this folds into the Phase 3 manual browser smoke after VPS rebuild).
- [ ] Production migration in a maintenance window with the API stopped. Backup first (uploads + Postgres) and record the `legacy_migration_run_id` returned by the migration so a future rollback can target the right rows.

Rollback plan: if the migration fails or the post-migration kanban shows wrong counts, the application changes are reverted (single PR), and the data migration is rolled back by deleting all `invoices` rows created by this migration (a `legacy_migration_run_id` column on `invoices` for that one phase makes this clean), restoring `event_documents.kind='invoice'` on the migrated source rows, and clearing `linked_invoice_id`. The four `invoice_*` columns on `event_documents` are untouched throughout, so nothing is lost.

Deliverable: every existing invoice now lives in the canonical table as a real financial record, with its original PDF attached as an `external_invoice` document. New uploads use the new shape. The kanban badge is unchanged from a user's perspective (and now also lights for partial-pay invoices, which is a small intentional widening).

---

## Phase 5: Quotes and contracts

**Status:** Code-side complete and **deployed** 2026-05-02. Migrations 027/028/029 applied to production DB. Service + router + frontend live after `sudo systemctl restart bellas-xv-api` + `npm run build` of dist.

Purpose: a quinceañera mom usually signs a contract before paying anything. The contract is essentially a quote plus terms plus a signature. Lifting Invoice Ninja's `quotes` model gives us this for free.

### 5.1 Migrations `027_create_quotes.py`, `028_create_quote_line_items.py`, `029_create_quote_invitations.py`

`quotes` shape: same as `invoices` with these differences:

- `quotes.status` enum: `draft|sent|approved|rejected|converted|expired`. Mirrors Invoice Ninja `STATUS_DRAFT=1` ... `STATUS_EXPIRED=-1`. Source: `app/Models/Quote.php`.
- `quotes.expires_at DATE` instead of `due_date`. Reminder cron in Phase 11 expires quotes past this date.
- `quotes.approved_at TIMESTAMPTZ`, `quotes.rejected_at TIMESTAMPTZ`, `quotes.converted_at TIMESTAMPTZ`, `quotes.converted_invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL`.
- `quotes.signature_base64 TEXT`, `quotes.signature_signed_at TIMESTAMPTZ`, `quotes.signature_ip INET`, `quotes.signature_name VARCHAR(120)`. Mirrors Invoice Ninja `signature_base64`/`signature_date`/`signature_ip` on `invoice_invitations`. We put it on the quote itself because in this shop the signed quote is the contract.
- No `paid_to_date_cents`, no `balance_cents`, no installments. Quotes don't carry money state; they convert into an invoice that does.
- Numbering: `Q-2026-000123` from `numbering_state.quote_seq`. Same on-send-only-allocation rule as invoices.

`quote_line_items`: identical shape to `invoice_line_items` with `quote_id` instead of `invoice_id`.

`quote_invitations`: identical shape to `invoice_invitations` with `quote_id` instead of `invoice_id`. Same `public_key`, `viewed_at`, `revoked_at`, `expires_at`, `deleted_at` columns. Same UNIQUE `(quote_id, contact_id)`. Created by `quote_service.mark_sent`, consumed by Phase 7 portal routes.

### 5.2 Service `services/quote_service.py`

- `create_quote`, `update_quote`, `mark_sent` — same shape as invoice equivalents.
- `approve_quote(db, *, quote_id, signature_base64, signature_name, signature_ip)` — captures the signature, stamps `approved_at`, `signature_signed_at`, `signature_ip`. Refuses if status != `sent`.
- `reject_quote(db, *, quote_id, reason)` — flips status to `rejected`.
- `convert_to_invoice(db, *, quote_id, actor_user_id) -> Invoice` — copies line items, terms, footer, and notes. Creates a draft invoice with the default two-row installment schedule (deposit + balance) based on the invoice total and event date. Stamps `converted_at` and `converted_invoice_id` on the quote. Status becomes `converted`.

### 5.3 Router and editor

- `api/routers/quotes.py` mirrors invoices.py with the extra `approve` and `convert` actions.
- `frontend/src/pages/event/tabs/Quotes.jsx` is a new tab. Sub-routes: `/events/:id/quotes`. Update [frontend/src/pages/event/EventDetailLayout.jsx](../frontend/src/pages/event/EventDetailLayout.jsx) tab list.
- Editor reuses the line-item editor from `InvoiceEditor` via a shared `LineItemTable` component refactor.
- "Convert to Invoice" button on an `approved` quote opens a confirm dialog and routes the user to the new invoice's editor in `draft` state.

Smoke test:

- Create a quote with three line items. Send. The quote shows in the customer portal in Phase 7.
- Approve a quote with a signature pad in the portal. Status flips, `approved_at` populates.
- Convert to invoice. New invoice in `draft` with the same line items. Quote status is now `converted`.
- Try to edit a converted quote. 422 `quote_locked`.

Deliverable: staff send contracts as quotes. Customers sign in the portal. Approved quotes one-click convert to invoices.

Phase 5 validation note, 2026-05-02:

Schema (migrations 027/028/029):

- 027 `quotes`: status enum `draft|sent|approved|rejected|converted|expired|cancelled` (added `cancelled` beyond the spec; staff-initiated cancel is the symmetric counterpart to `cancel_invoice` and a frequent ask). `expires_at DATE` instead of `due_date`. Added `rejection_reason TEXT` and `cancellation_reason TEXT` for symmetry with the invoice cancellation reason column. Signature columns paired by `chk_quote_signature_paired` (base64 + signed_at must both be present or both NULL). `approved` requires a captured signature (`chk_quote_approved_has_signature`). `converted` is biconditional with `converted_invoice_id` (`chk_quote_converted_consistent`). Partial index `idx_quotes_expiring_sent` keys the Phase 11 expiry sweep on the small subset of sent quotes.
- 028 `quote_line_items`: shape mirrors `invoice_line_items`. The four nonneg-money checks tightened in invoice migration 024 are baked in from creation, so quotes never need a follow-up tightening.
- 029 `quote_invitations`: identical to `invoice_invitations` keyed on `quote_id` instead of `invoice_id`.
- One spec-emergent finding: `cancel_quote` cannot accept `draft` status because chk_quote_number_when_not_draft would reject the transition (drafts have no number). Service raises `cancel_draft_not_allowed` with a hint to soft-delete instead. Documented in [services/quote_service.py](../services/quote_service.py) docstring and surfaced in the editor as the deliberately scoped "Delete draft" button (only shown on drafts).

Service [services/quote_service.py](../services/quote_service.py):

- `create_quote`, `update_quote`, `mark_sent`, `resend_quote`, `approve_quote`, `reject_quote`, `cancel_quote`, `soft_delete_quote`, `convert_to_invoice`, `get_quote_detail`, `list_quotes_for_event`, `search_quotes`. Plus `QuoteServiceError` with `code` + `extra` matching the invoice service contract.
- Reuses `LineItemInput` and `_compute_line_amounts` from `invoice_service` so the per-line money math agrees with the invoice surface by construction. The only cross-module coupling.
- `_assign_quote_number` mirrors `_assign_invoice_number` shape — same `SELECT FOR UPDATE` pattern on `numbering_state`, just on the `quote_seq`/`quote_year` columns. Concurrent quote+invoice sends serialize on the same singleton row.
- `convert_to_invoice` is idempotent on a converted quote: returns the existing canonical invoice rather than creating a duplicate. Default schedule: 50/50 deposit + balance; balance due 30 days before `event_date` (or `issue_date + 30 days` if event is too soon or missing). Total of zero skips schedule generation entirely (Phase 1's installment CHECK requires positive amounts).
- `_LOCKED_STATUSES = {'approved', 'rejected', 'converted', 'expired', 'cancelled'}` — anything terminal is frozen against further edits. `draft` and `sent` remain editable; `sent` edits bump `revision`.

Router [api/routers/quotes.py](../api/routers/quotes.py):

- 12 routes: `POST/GET /events/{id}/quotes`, `GET /quotes` (search), `GET/PATCH/DELETE /quotes/{id}`, `POST /quotes/{id}/{send,resend,approve,reject,cancel,convert}`. Convert returns the new invoice's full detail so the frontend can route directly to its editor without a second round-trip.
- Mounted in [api/server.py](../api/server.py) at `/api/events/{id}/quotes` and `/api/quotes`. Wired alongside the existing invoices/business-profile routers.
- Error map covers `quote_not_found`, `quote_locked`, `invalid_transition`, `line_items_required`, `signature_required`, `cancel_draft_not_allowed`, `quote_not_deletable`, `conversion_inconsistent`. All mapped to 4xx (`conversion_inconsistent` → 500 since it would indicate a CHECK constraint had silently desynced from the service code).

Frontend:

- [frontend/src/services/api.js](../frontend/src/services/api.js) — added `listQuotes`, `getQuote`, `createQuote`, `updateQuote`, `sendQuote`, `resendQuote`, `approveQuote`, `rejectQuote`, `cancelQuote`, `convertQuoteToInvoice`, `deleteQuote`.
- [frontend/src/components/QuoteEditor.jsx](../frontend/src/components/QuoteEditor.jsx) — drawer editor (920px wide, mirrors InvoiceEditor pattern). Header: customer + issue_date + expires_at. Line items table with up/down reorder buttons (no drag-and-drop — same v1 simplification as InvoiceEditor; both upgrade together when LineItemTable is extracted as a shared component). Totals panel. Collapsed "More options" with terms/footer/notes/PO. Action bar: Save Draft (drafts/sent), Send (drafts only), Cancel quote (sent only), Reject (sent only), Convert to invoice (approved only), Delete (drafts only). Banker's rounding helper duplicated from InvoiceEditor for now — extraction belongs to the editor polish backlog.
- [frontend/src/pages/event/tabs/Quotes.jsx](../frontend/src/pages/event/tabs/Quotes.jsx) — list view with status pill, expires-on chip for sent quotes, "→ Invoice #N" chip for converted quotes (clicks navigate to the linked invoice). New quote button opens the editor drawer.
- [frontend/src/pages/event/EventDetailLayout.jsx](../frontend/src/pages/event/EventDetailLayout.jsx) — added Quotes tab to the rail (between Documents and Invoices). Also fixed a Phase 4b regression on this file: the Invoices tab badge now keys on `outstanding_invoices` (canonical source) rather than the retired `invoice` count from pre-Phase-4b document_counts.
- [frontend/src/App.jsx](../frontend/src/App.jsx) — added `/events/:id/quotes` route under the EventDetailLayout outlet.
- Skipped for v1: shared `LineItemTable` component refactor between InvoiceEditor and QuoteEditor (the spec called for it; deferred to the editor polish backlog so the in-place quote work doesn't disrupt the just-shipped InvoiceEditor). Skipped: staff-side approve UI with signature pad — approval lives in Phase 7's portal. The backend `POST /quotes/{id}/approve` route exists for completeness but the UI doesn't yet expose it. Skipped: global quote search page (the per-event tab is enough for v1; staff search by event from the pipeline). Skipped: tab badge count for Quotes — would need a dedicated counts endpoint.

Validation:

- [tests/test_quotes_smoke.py](../tests/test_quotes_smoke.py) — 15 service-level checks covering create/update/send/approve/reject/cancel/soft-delete/convert with all the lifecycle gates: empty-line send rejected, sent-edit revisions, signature-required gate, draft-cancel rejected, convert idempotency, convert refused on non-approved, plus list/search.
- [tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) — 7 new `check_*` Phase 5 entries (status enum, number-when-not-draft, signature pairing, approved-needs-signature, converted-consistent both directions, line nonneg money, invitation UNIQUE). Total schema smoke now 36 checks.
- Full backend regression sweep all green: `test_invoice_schema_smoke`, `test_quotes_smoke`, `test_invoices_smoke`, `test_business_profile_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_booking_smoke`, `test_boutique_experience_smoke`, `test_contacts_smoke`, `test_notifications_smoke`. Frontend `npm run lint` clean, `npm run build` succeeds with the pre-existing large-chunk warning.
- API live verification: `sudo systemctl restart bellas-xv-api` succeeded. `GET /api/quotes` returns 401 (auth required, not 404) confirming routes mounted. Browser smoke for the Quotes tab itself is the next ask before declaring this fully validated in production.

---

## Phase 6: Payments, allocations, refunds, deposit handling

**Status:** Code-side complete and **deployed** 2026-05-02. Migrations 030/031/032/033/034 applied to production DB (`migrations_applied: 34` in /api/health). Service + router + frontend live after `sudo systemctl restart bellas-xv-api` at 19:32 UTC + `npm run build`.

Purpose: record money received. Apply it across one or more invoices. Surface partial-payment status. Handle deposit-then-balance and overpayment cleanly. Refunds claw back from the original payment, never as a new negative-amount row.

### 6.1 Migrations `030_create_payments.py` and `031_create_payment_allocations.py`

Columns on `payments`:

- `id SERIAL PRIMARY KEY`
- `contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT`
- `payment_number VARCHAR(32) UNIQUE` — `PMT-2026-000123`. Allocated immediately on create (no draft state for payments). Cancelled payments keep their number.
- `amount_cents BIGINT NOT NULL` — gross received. Always positive. Refunds do not create a new negative row.
- `applied_cents BIGINT NOT NULL DEFAULT 0` — `SUM(payment_allocations.applied_cents - payment_allocations.refunded_cents)`. Computed in the same transaction by `_recompute_payment_totals`.
- `unapplied_cents BIGINT NOT NULL DEFAULT 0` — `amount_cents - refunded_cents - applied_cents`. The "unallocated" pool. May exceed zero if the customer paid more than was allocated; that's overpayment and lives here, never on an invoice.
- `refunded_cents BIGINT NOT NULL DEFAULT 0` — total refunded back to the customer from this payment, across all allocations and any refund of the unapplied portion. Bumped only by `record_refund`.
- `payment_date DATE NOT NULL DEFAULT CURRENT_DATE`
- `method VARCHAR(20) NOT NULL` — one of `cash|check|card|transfer|zelle|other`.
- `transaction_reference VARCHAR(120)` — check number, last four of card, Zelle confirmation.
- `status VARCHAR(16) NOT NULL DEFAULT 'completed'` — one of `pending|completed|failed|partially_refunded|refunded|cancelled`. Mirrors Invoice Ninja `STATUS_*` on Payment minus `is_refund`. The status is derived: `refunded_cents == 0 → completed`; `0 < refunded_cents < amount_cents → partially_refunded`; `refunded_cents == amount_cents → refunded`. `pending`, `failed`, and `cancelled` are explicit and not derived from refund state.
- `notes TEXT`
- `created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `created_at`, `updated_at`, `deleted_at`

Columns on `payment_allocations`:

- `id SERIAL PRIMARY KEY`
- `payment_id INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE`
- `invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT` — invoices outlive payments for AR audit.
- `applied_cents BIGINT NOT NULL` — positive. The portion of this payment applied to this invoice.
- `refunded_cents BIGINT NOT NULL DEFAULT 0` — positive, bumped when a refund claws back from this allocation. Always `<= applied_cents`.
- `created_at`, `updated_at TIMESTAMPTZ`

CHECK constraints:

- `chk_payment_method`, `chk_payment_status` — enums.
- `chk_payment_amount_pos` — `amount_cents > 0`. No negative payments anywhere.
- `chk_payment_refunded_le_amount` — `refunded_cents <= amount_cents`.
- `chk_payment_unapplied_nonneg` — `unapplied_cents >= 0`. The service is responsible for keeping `applied + refunded + unapplied = amount`.
- `chk_alloc_applied_pos` — `applied_cents > 0`.
- `chk_alloc_refunded_le_applied` — `refunded_cents <= applied_cents`. A refund cannot exceed what was applied to that invoice in the first place.

UNIQUE constraint: `(payment_id, invoice_id)`. A payment row applies to a given invoice at most once. Two separate payments to the same invoice are two rows.

Indexes: `payments(contact_id, payment_date DESC)`, `payment_allocations(invoice_id)`.

### 6.2 Service-level recomputation, not triggers

Every payment write calls `_recompute_payment_totals(payment_id)` and `_recompute_invoice_totals(invoice_id)` in the same transaction. Database triggers are tempting but make tests harder and obscure the rules.

`_recompute_payment_totals(payment)`:

- `applied_cents = SUM(allocations.applied_cents - allocations.refunded_cents)`.
- `unapplied_cents = amount_cents - refunded_unapplied_cents - applied_cents`. (Where `refunded_unapplied_cents` is the slice of `refunded_cents` that came from refunding the unallocated pool, tracked on a small `refund_events` audit table; see 6.4.)
- Derive `status` from the refund-state rules above unless `pending|failed|cancelled` is set explicitly.

`_recompute_invoice_totals(invoice)`:

- `paid_to_date_cents = SUM(allocations.applied_cents - allocations.refunded_cents)` for `payment_allocations` joined to non-cancelled `payments`.
- `balance_cents = total_cents - paid_to_date_cents`.
- Status transitions:
  - `paid_to_date == 0` and not cancelled → `sent` (or whatever the prior non-paid state was — drafts can't have allocations).
  - `0 < paid_to_date < total_cents` → `partial`.
  - `paid_to_date >= total_cents` → `paid`, stamp `paid_at`.
  - A refund that pulls `paid_to_date` back below `total` flips `paid → partial` and clears `paid_at`.
  - Cancelled and reversed remain explicit and never auto-derived.
- Walk `invoice_installments` ordered by `due_date ASC`. For each installment, if cumulative `paid_to_date` covers its sort-prefix, stamp `paid_at` (idempotent). The reminder cron in Phase 11 reads installment-level `paid_at` to know whether to fire.

### 6.3 Service `services/payment_service.py`

- `record_payment(db, *, contact_id, amount_cents, method, payment_date, transaction_reference, notes, allocations: list[Allocation], actor_user_id) -> Payment`. `allocations` is `[(invoice_id, applied_cents), ...]`. Refuses if `SUM(allocations) > amount_cents` (over-allocation). Refuses if any allocation would push an invoice's `paid_to_date` over its `total_cents` (per-invoice over-allocation). Allows `SUM(allocations) < amount_cents`; the remainder goes to `unapplied_cents`.
- `apply_unapplied(db, *, payment_id, invoice_id, applied_cents, actor_user_id)`. Applies previously-unallocated funds to a new invoice. Same per-invoice bound check.
- `unapply_allocation(db, *, allocation_id, actor_user_id)`. Removes an allocation that hasn't been refunded. Bumps the payment's `unapplied_cents`. Useful when staff allocate to the wrong invoice.
- `record_refund(db, *, payment_id, amount_cents, refund_method, refund_reference, notes, allocation_refunds: list[AllocationRefund] | None, actor_user_id) -> RefundEvent`. `allocation_refunds` is `[(allocation_id, refund_cents), ...]` and may be `None` to refund only from the unapplied pool. Refuses if the requested total exceeds the payment's currently-refundable amount (`amount - refunded`). Inserts a `refund_events` audit row, bumps `payments.refunded_cents`, bumps each `payment_allocations.refunded_cents`, recomputes both payment and invoice totals.
- `void_payment(db, *, payment_id, reason, actor_user_id)`. Allowed only on `pending`. Flips status to `cancelled`. Refuses to void a `completed` payment — that's what `record_refund` is for.

### 6.4 Audit migration `032_create_refund_events.py`

A small append-only table that records every refund operation. The service uses it to compute `refunded_unapplied_cents` per payment and to render the activity timeline.

Columns:

- `id SERIAL PRIMARY KEY`
- `payment_id INTEGER NOT NULL REFERENCES payments(id) ON DELETE RESTRICT`
- `amount_cents BIGINT NOT NULL` — total refunded by this event, across all allocations and the unapplied pool.
- `from_unapplied_cents BIGINT NOT NULL DEFAULT 0` — slice that came out of the unapplied pool.
- `from_allocations_json JSONB NOT NULL DEFAULT '[]'` — `[{allocation_id, refund_cents}]` for the audit trail.
- `refund_method VARCHAR(20) NOT NULL`
- `refund_reference VARCHAR(120)`
- `notes TEXT`
- `actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

CHECK: `amount_cents > 0`, `from_unapplied_cents >= 0`, `from_unapplied_cents <= amount_cents`.

### 6.5 Deposit flow, end-to-end

The deposit is the first installment in `invoice_installments` and the first allocation in `payment_allocations`.

1. Staff create an invoice with `total_cents=200000` and a two-row schedule: `Deposit $200 due 2026-06-15`, `Balance $1,800 due 2026-08-30`.
2. Send. Status `sent`, number allocated, invitation row created. Customer sees the schedule in the portal.
3. Customer pays $200. Staff record a payment: `amount_cents=20000`, allocations `[(invoice_id, 20000)]`. Service recomputes: invoice `paid_to_date=20000`, `balance=180000`, status `partial`. The deposit installment's `paid_at` stamps.
4. Customer pays $1,800. Staff record a payment: `amount_cents=180000`, allocations `[(invoice_id, 180000)]`. Status flips to `paid`, invoice `paid_at` stamps. Both installments now have `paid_at`.

Overpayment example: customer mistakenly pays $2,500 in step 4. Staff record `amount_cents=250000`, allocations `[(invoice_id, 180000)]`. Invoice flips to `paid`. Payment row has `applied_cents=180000`, `unapplied_cents=70000`. Staff later either refund the $700 via `record_refund` with `from_unapplied=70000`, or apply it to a future invoice for the same family.

Refund example: customer cancels two months in. Original payment row exists with `amount=200000`, `applied=200000`. Staff call `record_refund(payment_id, amount_cents=150000, allocation_refunds=[(alloc_id, 150000)])`. `payments.refunded_cents=150000`, allocation `refunded_cents=150000`, `payment.applied_cents` recomputes to `50000`, invoice `paid_to_date` drops to `50000`, invoice flips `paid → partial`, `paid_at` clears, deposit installment's `paid_at` clears. Activity log row `payment.refunded`.

### 6.6 Router and UI

- `api/routers/payments.py` mounted at `/api/payments` with sibling helpers at `/api/invoices/{id}/payments`. Refund endpoint is `POST /api/payments/{id}/refunds`.
- A "Payments" sub-section inside the Invoice editor lists allocated payments per invoice with an inline "Record payment" button.
- A top-level "Payments" tab on the event (`/events/:id/payments`) shows every payment for the event's contact, allocated, unapplied, and refunded.
- Refund UI: opens a dialog showing the payment's allocations and unapplied pool, lets staff pick an amount per source, records via `record_refund`.

Smoke tests:

- Record a $200 payment against a $2,000 invoice with the two-row schedule. Status flips to `partial`. Deposit installment's `paid_at` stamps.
- Record an $1,800 payment against the same invoice. Status flips to `paid`. Both installments have `paid_at`.
- Record a $2,500 payment with $1,800 allocated. Invoice flips to `paid`. Payment shows `unapplied=$700`.
- Refund $300 from the unapplied pool only. Payment `unapplied` drops to $400, `refunded=$300`, status `partially_refunded`. Invoice unchanged.
- Refund $500 from the allocation only. Allocation `refunded_cents=$500`. Invoice `paid_to_date` drops by $500, status flips back to `partial`, `paid_at` clears.
- Try to refund $4,000 from a $2,000 payment. 422 `refund_exceeds_remaining`.
- Try to allocate $2,500 of a $2,000 payment. 422 `over_allocation`.
- Try to allocate $200 to an invoice already at `paid_to_date=total`. 422 `invoice_overallocation`.
- `chk_payment_amount_pos` rejects an attempt to insert a payment with negative amount.

Deliverable: every dollar in and out of the shop has a row. Refunds, overpayments, and deposit-then-balance all flow through the same primitives without conflicting invariants.

Phase 6 validation note, 2026-05-02:

Schema (migrations 030–034):

- 030 `payments`: amount + applied + refunded + unapplied as separate columns. Invariant `amount = applied + refunded + unapplied` enforced by `chk_payment_amount_consistent` — defense in depth against a service-layer bug. `chk_payment_amount_pos` (no negative payments anywhere). `chk_payment_method` enum (`cash|check|card|transfer|zelle|other`). `chk_payment_status` enum (`pending|completed|partially_refunded|refunded|failed|cancelled`). `chk_payment_number_when_not_pending` mirrors the invoice number-when-not-draft pattern.
- 031 `payment_allocations`: per-(payment, invoice) row with `applied_cents` (positive) and `refunded_cents` (`<= applied`). `UNIQUE (payment_id, invoice_id)` so each (payment, invoice) link is one row. `ON DELETE RESTRICT` on `invoice_id` — invoices outlive payments for AR audit.
- 032 `refund_events`: append-only audit table. `from_unapplied_cents` tracks the unapplied-pool slice of a refund; `from_allocations_json JSONB` captures the per-allocation breakdown. `ON DELETE RESTRICT` on `payment_id` prevents losing the refund history.
- 033 extended `numbering_state` with `payment_year` + `payment_seq` (PMT-YYYY-NNNNNN allocation pattern; same `SELECT FOR UPDATE` lock as invoices/quotes).
- 034 follow-up: widened `payments.status` from `VARCHAR(16)` to `VARCHAR(24)` because `partially_refunded` is 18 characters (caught in lifecycle smoke; original 030 sized too tight). The chk_payment_status CHECK accepted the literal but the column itself truncated.

Service [services/payment_service.py](../services/payment_service.py):

- `record_payment` — gross funds + per-invoice allocations. Refuses over-allocation, per-invoice over-allocation, allocation to draft/cancelled/reversed/deleted invoices, duplicate invoice in same payment. Allocations may sum to less than amount; the remainder goes to unapplied.
- `apply_unapplied(payment_id, invoice_id, applied_cents)` — moves funds from the unapplied pool onto a different invoice. Idempotent on (payment, invoice) — bumps an existing allocation rather than creating a duplicate (UNIQUE constraint enforces this anyway). Rejects allocation that would push paid_to_date over total.
- `unapply_allocation(allocation_id)` — returns funds from a non-refunded allocation to the unapplied pool. Rejects if any portion has been refunded.
- `record_refund(payment_id, amount_cents, refund_method, ...)` — splits between `from_unapplied_cents` and per-allocation refund slices. The two slices must sum exactly to `amount_cents` (`refund_split_mismatch` if not). Each allocation refund cannot exceed (applied - already_refunded). Computes the FULL post-refund payment state Python-side BEFORE any flush so the row is internally consistent at every flush boundary — caught a real bug during smoke development where the chk_payment_amount_consistent CHECK fired mid-mutation when the auto-flush hit refunded-bumped-but-applied-not-yet-recomputed.
- `void_payment(payment_id)` — only on `pending`. Refuses `completed` (use `record_refund` instead). Idempotent on `cancelled`.
- `soft_delete_payment(payment_id)` — only on cancelled/pending/failed payments with no allocations and no refunds.
- `_recompute_invoice_totals(invoice_id)`: `paid_to_date_cents = SUM(applied - refunded)` over non-cancelled non-deleted payments. Status transitions: `paid_to_date == 0 → 'sent'`, `0 < paid_to_date < total → 'partial'` (clear paid_at), `paid_to_date >= total → 'paid'` (stamp paid_at). Cancelled/reversed/draft never auto-derived. Walks `invoice_installments` ordered by `due_date ASC` and stamps/unstamps `paid_at` per row idempotently — refunds clear an installment's paid_at when the cumulative drops below its sort-prefix.

Router [api/routers/payments.py](../api/routers/payments.py):

- 9 routes mounted across three prefixes: `/api/payments` (record/get/refund/void/delete + apply unapplied + unapply allocation), `/api/invoices/{id}/payments` (list payments touching one invoice), `/api/events/{id}/payments` (list every payment for the event's primary contact).
- Error map covers 17 service-layer error codes mapped to 422 (validation/state) or 404 (not found). Refund errors are surfaced verbatim to the frontend so the dialog can show the staff-facing translation.

Frontend:

- [frontend/src/services/api.js](../frontend/src/services/api.js) — added `recordPayment`, `getPayment`, `applyUnapplied`, `unapplyAllocation`, `recordRefund`, `voidPayment`, `deletePayment`, `listPaymentsForInvoice`, `listPaymentsForEvent`.
- [frontend/src/components/PaymentRecorder.jsx](../frontend/src/components/PaymentRecorder.jsx) — modal for recording. Pulls open invoices for the event, lets staff allocate per-invoice with a "Fill" button to auto-fill from the remaining payment amount. Live indicator shows allocated vs unapplied. Banker's-rounding-compatible allocation math.
- [frontend/src/components/RefundDialog.jsx](../frontend/src/components/RefundDialog.jsx) — modal for refund. Splits across "From unapplied pool" and per-allocation slices; the running total compares against (amount - refunded). Each allocation row caps at its own (applied - refunded) remaining.
- [frontend/src/pages/event/tabs/Payments.jsx](../frontend/src/pages/event/tabs/Payments.jsx) — Payments tab. Totals header (received/applied/unapplied/refunded). Per-payment row with status pill, method, amount/unapplied/refunded summaries, "Refund" button on completed/partially_refunded payments.
- [frontend/src/pages/event/EventDetailLayout.jsx](../frontend/src/pages/event/EventDetailLayout.jsx) — added Payments tab to the rail (after Invoices).
- [frontend/src/App.jsx](../frontend/src/App.jsx) — added `/events/:id/payments` route under the EventDetailLayout outlet.
- Skipped for v1 (pinned to editor polish backlog): in-editor "Payments" sub-section inside InvoiceEditor. The Phase 6 spec called for it but doing it would require extracting a PaymentList component shared between the editor and the Payments tab, which is the same kind of refactor the v1.1 polish pass owns. The Payments tab is the primary surface for v1.

Validation:

- [tests/test_payments_smoke.py](../tests/test_payments_smoke.py) — 13 service-level checks covering the full deposit→partial→balance→paid lifecycle, overpayment, refund-from-unapplied (invoice unchanged), refund-from-allocation (paid → partial, paid_at clears, balance installment paid_at clears), apply_unapplied to a different invoice, unapply_allocation returns to pool, plus 5 rejection paths (over_allocation, invoice_overallocation, refund_exceeds_remaining, refund_split_mismatch, void on completed). Validates the `chk_payment_amount_consistent` invariant at every payment touch.
- [tests/test_invoice_schema_smoke.py](../tests/test_invoice_schema_smoke.py) — 7 new Phase 6 `check_*` entries covering the schema CHECKs and FK behaviors. Total schema smoke now 43 checks.
- Full backend regression sweep all green (14 smokes): `test_invoice_schema_smoke`, `test_payments_smoke`, `test_quotes_smoke`, `test_invoices_smoke`, `test_business_profile_smoke`, `test_event_documents_smoke`, `test_events_smoke`, `test_admin_booking_smoke`, `test_admin_booking_settings_smoke`, `test_auth_smoke`, `test_booking_smoke`, `test_boutique_experience_smoke`, `test_contacts_smoke`, `test_notifications_smoke`. Frontend `npm run lint` clean, `npm run build` succeeds with the pre-existing large-chunk warning.
- API live verification: `/api/health` returned `migrations_applied: 34`, `POST /api/payments` returned 401 (auth required, not 404). Bare `GET /api/payments` correctly returns 405 because that exact collection path only supports POST. Authenticated browser smoke is the next ask.

---

## Phase 7: Public client portal

Purpose: the mom clicks a link in her email and sees her invoice without logging in. She can view, accept (on quotes), pay-mark, and download the PDF. No second auth surface, no account creation, no app to install.

The `invoice_invitations` and `quote_invitations` schema landed in Phase 1 and Phase 5 respectively. Phase 7 ships only the public-facing routes, the templates, and the revoke/expire surface for staff. Mark-as-sent already creates invitation rows; this phase is the consumer.

### 7.1 Public router `api/routers/portal.py`

Mounted at `/portal` (NOT `/api/portal` — the customer never sees `/api`). NOT auth-gated; key-gated. Every route checks `deleted_at IS NULL AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())` on the invitation before serving.

- `GET /portal/invoice/{public_key}` — server-rendered Jinja template. Shows: customer name, invoice number, line items, totals, the installment schedule with per-row paid/due state, download-PDF button, and a "Pay" CTA that v1 just opens a "Contact Bellas to pay" modal. Phase 11 wires Stripe Checkout.
- `GET /portal/invoice/{public_key}/pdf` — streams the cached PDF from `document_storage`.
- `GET /portal/quote/{public_key}` — same shape, with an "Accept and sign" button.
- `POST /portal/quote/{public_key}/accept` — body `{ signature_base64, signature_name }`. Records the signature on the quote, stamps `approved_at`, sets `quote_invitations.last_viewed_at` and the quote's signature columns. Captures `request.client.host` into `signature_ip`.
- `POST /portal/invoice/{public_key}/view-receipt` — internal endpoint hit by the page on load to stamp `viewed_at` and increment `view_count`. Idempotent on `viewed_at`.

Staff-side routes (auth-gated, under `/api/invoices/{id}/invitations` and `/api/quotes/{id}/invitations`):

- `GET` — list invitations for a doc.
- `POST` — create an invitation for an additional contact.
- `POST /{invitation_id}/revoke` — stamps `revoked_at` and `revoked_by_user_id`. The portal route immediately starts returning 404. Staff can issue a fresh invitation for the same contact (new key) afterwards.
- `POST /{invitation_id}/resend` — bumps `sent_at`, re-enqueues the email. Same key.
- `DELETE` — soft-delete (sets `deleted_at`).

Rate limit on the portal endpoints: 60 requests/minute per IP. Prevents key-enumeration attacks.

### 7.2 Portal templates `templates/portal/`

Server-rendered HTML, mobile-first. No bundled React. Reasons:

- A customer should not download a 2 MB admin JS bundle to view a $2,000 invoice.
- Server-rendered HTML works without JS — important for older Android browsers in this shop's customer base.
- Cleaner separation of concerns: the admin SPA is for staff; the portal is for customers.

Templates: `invoice.html`, `quote.html`, `receipt.html`, `accepted.html`. Style with a single `portal.css` that matches the Bellas brand. Copy in plain prose, no em dashes, no listy patterns.

CTA copy examples:

- "Your deposit of $200 is due June 15. Balance of $1,800 is due September 1."
- "Pay your deposit" / "Pay your balance" / "Mark as paid in person"
- "Read the contract and sign"

### 7.3 Email sending

Reuse `services/email_transport.py` and `services/notification_service.py`. New notification kinds: `invoice_sent`, `quote_sent`, `payment_received`, `invoice_paid_in_full`. Each enqueues a `notification_jobs` row with the portal URL substituted in.

Smoke tests:

- Mark an invoice as sent. An invitation row exists. The portal URL renders the invoice on a logged-out browser.
- Hit the portal URL twice. `view_count` is 2, `last_viewed_at` updates, `viewed_at` is unchanged after the first hit.
- Submit a signature on a quote portal. Quote flips to `approved`, signature columns populated.
- Hammering the portal endpoint past 60/min returns 429.
- Staff revokes an invitation. The portal route returns 404 on the next hit.
- Staff soft-deletes an invitation (wrong contact). Same 404 behavior.
- An invitation past `expires_at` returns 404. (Bypassed in v1; the column is there for future "this link expires in 30 days" policy.)
- Issue a fresh invitation for the same contact after revoke. New key works; old key still 404s.

Deliverable: customer-facing surface exists. One link per invoice and quote. No customer accounts. Leaked links can be killed without dropping the underlying invoice.

### 7.4 Validation note (2026-05-02)

Shipped one bundle:

- New deps: `jinja2==3.1.6` (added to `requirements.txt`, installed in venv).
- New env: `PORTAL_BASE_URL` (in `config/settings.py`); falls back to `WIDGET_PUBLIC_BASE_URL` so dev runs out of the box.
- New service [services/portal_service.py](../services/portal_service.py): three-gate invitation lookup (`deleted_at` / `revoked_at` / `expires_at`), view stamping, signature capture wrapper, full staff invitation lifecycle (list, add, revoke, resend, soft-delete). The router catches `PortalServiceError` and maps codes to 404/410/422.
- New service [services/portal_email.py](../services/portal_email.py): customer-facing email rendering and **synchronous** dispatch via the existing `email_transport`. v1 deviation from plan §7.3 — the original spec wanted `notification_jobs` rows. The existing `notification_jobs` schema FKs `appointment_id` and the worker bails on a missing appointment row, so making it polymorphic would have meant a real schema refactor whose only customer is portal email. Sync send works because the portal URL is fixed at the moment staff hits Send (no render-at-send drift), and SMTP failures surface as a 502 with `code: email_send_failed` so the staff UI can offer Resend without confusing the lifecycle (the invoice is still flagged as sent in the DB). When dunning lands in Phase 11 we revisit this and either widen `notification_jobs` or stand up a portal-specific queue.
- New router [api/routers/portal.py](../api/routers/portal.py): public surface mounted at `/portal/...`, staff invitation management at `/api/invoices/{id}/invitations` and `/api/quotes/{id}/invitations`. 16 routes total (8 public, 8 staff).
- Templates [templates/portal/](../templates/portal/): `base.html`, `invoice.html`, `quote.html` (with the signature-pad JS), `accepted.html`, `gone.html`, plus a shared `_lineitems.html` partial and a `static/portal.css`. Server-rendered, mobile-first, no admin SPA bundle.
- Migration [035_partial_unique_invitations.py](../database/migrations/035_partial_unique_invitations.py): the original `UNIQUE (invoice_id, contact_id)` on both invitation tables blocked staff from issuing a fresh invitation for the same contact after a revoke. Replaced with a partial unique index that scopes uniqueness to live rows only (`WHERE deleted_at IS NULL AND revoked_at IS NULL`), so the audit trail of revoked rows can stack while a new live row slots in cleanly. Caught by the smoke test the first time it tried to rotate a key.
- Rate limiting: in-process sliding-window deque per IP, 60 req/min. Single uvicorn worker today; multi-worker would require Redis. Documented in the router; smoke verifies a 429 within 70 hits.
- IP capture: `signature_ip` is INET-typed, but `TestClient` sets `client.host == "testclient"`. The router's `_client_ip` parses through `ipaddress.ip_address` and returns `None` on un-parseable values — production sees real IPs, tests see `None`, signature inserts succeed in both.
- Staff resend hardening: `POST .../invitations/{id}/resend` now both bumps the invitation timestamps and synchronously re-dispatches the portal email for that invitation. Staff revoke/resend/delete routes also verify the invitation belongs to the invoice or quote id in the URL before mutating it.
- Smoke [tests/test_portal_smoke.py](../tests/test_portal_smoke.py): 11 checks. All green.
  1. Mark-sent invoice's invitation key resolves on `/portal/invoice/<key>` to a 200 HTML page with the customer's name + the doc-kind label.
  2. Unknown key returns 404 (not 401 — the gate must hide existence).
  3. Two `view-receipt` hits leave `view_count = 2`, `viewed_at` unchanged after first stamp, `last_viewed_at` advancing.
  4. Sent quote's signature-pad submission flips status to `approved`, populates the signature columns, and the `/accepted` page renders.
  5. Empty signature payload rejected at the Pydantic boundary (422).
  6. Revoke returns 410-equivalent; portal returns 404 immediately; a fresh staff-issued invitation produces a different `public_key` that works while the old one still 404s.
  7. Soft-delete returns 404 on the next portal hit.
  8. Backdated `expires_at` returns 404 even when the row is otherwise live.
  9. Staff resend bumps `last_resent_at`, exercises the portal email path, and wrong-parent revoke returns 404 without killing the live link.
  10. `POST /api/invoices/{id}/invitations` against a draft invoice returns 422 (`invalid_transition`) — drafts cannot have invitations.
  11. 70 hits on the same portal endpoint trip the 60/min rate limiter (429).
- Regression sweep: all 15 script-style smokes green, including the new portal smoke plus invoices, quotes, payments, schema, event documents, events, bookings, contacts, auth, notifications, business profile, and admin booking surfaces.
- Frontend: no admin-side surface added in this phase. Customer portal is server-rendered HTML and lives on a different host in production. Staff still view invitations via the existing invoice/quote detail surface; a small staff-side "Manage links" panel (revoke / new contact / resend) is on the polish backlog.
- Skipped for v1: full PDF download (404-stubbed; lands in Phase 8), Stripe payment CTA (Phase 11+), staff-side "Manage invitations" panel (polish backlog), webhooks for email opens (the `email_opened_at` column exists; nothing populates it yet — needs a tracking-pixel route or SES webhook).

---

## Phase 8: PDF generation

Purpose: a downloadable, printable, archivable record. Stored in `document_storage` so it inherits disk-space guards, backup decisions, and the same mount.

### 8.1 Service `services/invoice_pdf.py`

Uses WeasyPrint. Inputs: `Invoice` + line items + business profile (logo, address, brand color).

- `render_invoice_pdf(invoice_id) -> Path` — renders to `invoices/{id}/{revision}.pdf` under `DOCUMENT_STORAGE_ROOT`. Idempotent: if the file already exists for the current `(id, revision)` it returns the path without re-rendering.
- `render_quote_pdf(quote_id)` — same pattern, stored under `quotes/{id}/{revision}.pdf`.
- `render_payment_receipt_pdf(payment_id)` — under `receipts/{id}.pdf`. Receipts don't have revisions because they're immutable.
- On success, stamp `last_pdf_rendered_revision` and `last_pdf_rendered_at` on the invoice and clear `last_pdf_render_error`.
- On failure, catch the WeasyPrint/storage exception, write a concise `last_pdf_render_error`, return a 503 from the download route with `pdf_render_failed`, and expose a staff-facing Retry render action. Never leave a partial file at the final cache key.

Cache invalidation: every `update_invoice` that bumps `revision` makes the previous PDF stale. The first download after the bump lazily re-renders. Staff can also click Retry render if the latest attempt failed. A later optimization can add a background render queue, but v1 does not need one.

### 8.2 Templates `templates/pdf/`

`invoice.html`, `quote.html`, `receipt.html`. Plain HTML+CSS, print-stylesheet-friendly. WeasyPrint handles the rendering. One CSS file shared across all three so the brand stays consistent.

Brand assets (logo, address, phone, email, default tax label, payment instructions) come from the `business_profile` singleton landed in Phase 1 and edited via the Settings page from Phase 3. The PDF renderer reads the singleton on every render so the latest profile applies retroactively to old invoices when re-rendered.

### 8.3 Wire-up

- `GET /api/invoices/{id}/pdf` — staff download endpoint, auth-gated. Returns the PDF.
- `GET /portal/invoice/{public_key}/pdf` — customer download endpoint from Phase 7, key-gated. Returns the PDF.
- Both endpoints call `render_invoice_pdf` lazily so a fresh deploy doesn't have to backfill PDFs.
- Staff editor shows a small warning if `last_pdf_render_error` is set and includes a Retry render button that calls `POST /api/invoices/{id}/pdf/retry`.

### 8.4 Dependencies

WeasyPrint pulls in Cairo, Pango, GDK-PixBuf system libraries. Add to the deploy doc:

```
sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0
```

Add `weasyprint` to [requirements.txt](../requirements.txt). Test on the VPS that the libs are actually installed before merging Phase 8.

Smoke tests:

- Render a draft invoice PDF. Open it. Numbers match the editor.
- Edit the invoice (bump `revision`), re-render. New PDF, old one cached but stale — confirm the route returns the new revision.
- Render a paid invoice receipt. The receipt shows the payment date, method, and remaining balance.
- Try to render with no business logo set. PDF still renders with a text-only header.
- Simulate a WeasyPrint failure. Route returns 503, `last_pdf_render_error` is populated, no partial PDF exists at the final cache key. Retry after fixing the failure clears the error.

Deliverable: every invoice, quote, and payment has a downloadable PDF.

### 8.5 Validation note (2026-05-02)

Shipped one bundle:

- New deps: `weasyprint==63.1` (added to `requirements.txt`, installed in venv). System libs (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libcairo2`, `libgdk-pixbuf-2.0-0`) were already present on the VPS. No `apt install` needed; future fresh deploys still need the doc note in §8.4.
- No new migrations. The `last_pdf_rendered_revision` / `last_pdf_rendered_at` / `last_pdf_render_error` columns on `invoices` and `quotes` landed in Phase 1; receipts have no error column on the `payments` model so a render failure surfaces only as a 503 from the route (next render attempt re-runs).
- No `ReadWritePaths` change needed. The systemd unit already covers `/var/lib/bellas-xv/uploads`, and the PDF cache writes live under it (`invoices/{id}/{rev}.pdf`, `quotes/{id}/{rev}.pdf`, `receipts/{id}.pdf`).
- New service [services/invoice_pdf.py](../services/invoice_pdf.py): owns rendering, caching, error stamping. Atomic write via `tempfile.mkstemp` in the same directory + `os.replace` so a mid-render crash never poisons the cache key. Lazy by default (`ensure_*_pdf`); force-render path (`render_*_pdf`) used by the staff Retry button. Receipts are immutable so they have no revision in the cache key.
- New templates [templates/pdf/](../templates/pdf/): `invoice.html`, `quote.html`, `receipt.html`, plus `_base.html`, `_lineitems.html`, and `_pdf.css`. Print-targeted CSS (Letter size, page-counter footer). Brand pulled from the `business_profile` singleton. Logo missing falls back to a text header without crashing.
- Routes: `GET /api/invoices/{id}/pdf` + `POST /api/invoices/{id}/pdf/retry`; `GET /api/quotes/{id}/pdf` + `POST /api/quotes/{id}/pdf/retry`; `GET /api/payments/{id}/receipt.pdf`. Customer-facing: `GET /portal/invoice/{key}/pdf` + `GET /portal/quote/{key}/pdf` (both run through the three-gate invitation lookup before serving). Replaced the Phase 7 portal PDF stub.
- Detail responses now expose `last_pdf_rendered_revision`, `last_pdf_rendered_at`, `last_pdf_render_error` on invoices and quotes so the staff editor can render the Retry banner without a separate fetch.
- Frontend wiring: per-row "PDF" buttons on the Invoices, Quotes, and Payments tabs (axios-blob → object-URL → new tab so the Bearer token rides along — direct `<a href>` would 401). InvoiceEditor + QuoteEditor show a "Retry render" warning banner when `last_pdf_render_error` is populated and a discreet "View PDF" link otherwise. Customer portal pages add a "Download PDF" CTA to both invoice and quote views.
- Smoke [tests/test_invoice_pdf_smoke.py](../tests/test_invoice_pdf_smoke.py): 10 checks. All green.
  1. Render a sent invoice PDF; bytes start with `%PDF`, file > 1KB.
  2. Cache hit: backdated `last_pdf_rendered_at` doesn't move on a second `ensure_*`; on-disk mtime unchanged.
  3. Revision bump: a metadata-only patch bumps `revision` and the next ensure renders to a new key while the old key file persists on disk (we never delete cached revisions).
  4. Quote PDF embeds a customer signature successfully (approve_quote → render → magic bytes).
  5. Receipt PDF renders; magic bytes valid.
  6. Logo cleared on the business profile → invoice still renders with a text-only header.
  7. Failure path: monkey-patched `weasyprint.HTML.write_pdf` to raise; service stamps `last_pdf_render_error`, **no partial PDF sits at the final cache key** (verified explicitly), and a successful retry clears the error.
  8. Staff `GET /api/invoices/{id}/pdf` returns `200 application/pdf` with `%PDF` bytes.
  9. Portal `GET /portal/invoice/{key}/pdf` serves bytes for a live invitation; revoking the invitation flips it to 404 immediately.
  10. Receipt route gates on auth (no token → 401/403).
- Regression sweep: `test_invoices_smoke`, `test_quotes_smoke`, `test_payments_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_portal_smoke`, `test_invoice_pdf_smoke` — all green.
- Skipped for v1: bulk re-render of pre-Phase-8 invoices (lazy render handles it on next download); background render queue (the spec says v1 doesn't need one); per-installment receipt (one receipt PDF per `payments` row); rendering an invoice _signature_ block when the matching quote was signed (the data is on the quote PDF, not the invoice — staff cross-references from the timeline once Phase 9 lands); Stripe payment CTA on the portal invoice page (Phase 11+).

---

## Phase 9: Activity timeline

Purpose: every event detail page gets a "what happened" timeline. Staff stop asking "did we send this yet?" because the answer is on the page.

### 9.1 Migration `033_create_activity_log.py`

Mirrors Invoice Ninja's `activities` table. Source: `app/Models/Activity.php`.

Columns:

- `id BIGSERIAL PRIMARY KEY`
- `event_id INTEGER REFERENCES events(id) ON DELETE CASCADE` — every activity is scoped to an event in this shop.
- `actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL` — null for customer-portal actions.
- `actor_kind VARCHAR(16) NOT NULL` — `staff|customer|system`.
- `activity_type VARCHAR(40) NOT NULL` — string enum; see vocabulary below.
- `subject_kind VARCHAR(20)` — `invoice|quote|payment|event|contact`.
- `subject_id INTEGER`
- `payload JSONB NOT NULL DEFAULT '{}'` — small structured details (e.g. `{ "from_status": "draft", "to_status": "sent" }`).
- `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`

Vocabulary, mirroring Invoice Ninja activity types but as strings:

- Invoice: `invoice.created`, `invoice.updated`, `invoice.sent`, `invoice.viewed`, `invoice.paid`, `invoice.cancelled`, `invoice.reminder_sent`.
- Quote: `quote.created`, `quote.updated`, `quote.sent`, `quote.viewed`, `quote.approved`, `quote.rejected`, `quote.converted`, `quote.signed`.
- Payment: `payment.created`, `payment.refunded`, `payment.voided`.
- Event: `event.status_changed` (already covered by `event_status_change_events`; either move it here or keep both — pick before Phase 9 ships).

### 9.2 Service `services/activity_log.py`

- `log_activity(db, *, event_id, actor_user_id, actor_kind, activity_type, subject_kind, subject_id, payload={})` — single insert, no commit.
- Each domain service (invoice, quote, payment) calls `log_activity` after every state change. Wired in via decorators or explicit calls; pick one and stay consistent.

### 9.3 Frontend timeline

- New `frontend/src/pages/event/tabs/Activity.jsx` tab.
- Single fetch: `GET /api/events/{id}/activity?limit=100`. Reverse-chronological list. Each row: actor avatar (or system icon for customer/system), activity description, subject link, timestamp.
- Render copy from a small client-side dictionary: `invoice.sent` → "Sent {invoice_number} to {customer}". Keeps the JSON payload small and the render localization-ready.

Smoke tests:

- Send an invoice. Activity row appears with `invoice.sent`.
- Customer views in portal. Activity row appears with `invoice.viewed`, `actor_kind='customer'`.
- Record a payment. Activity row appears.
- Pagination works at 100/page (rare in v1, but the endpoint should support it).

Deliverable: every state change on every event leaves a trail.

### 9.4 Validation note (2026-05-02)

Shipped one bundle:

- New migration [036_create_activity_log.py](../database/migrations/036_create_activity_log.py): `activity_log` table with `(event_id, id DESC)` index for keyset pagination plus a partial subject-lookup index. CHECK constraints enforce the actor-kind enum, the staff-must-have-actor invariant, and the subject-pair invariant (`subject_kind` and `subject_id` are both set or both NULL). Plan said migration 033; we're at 036 now since prior phases added 034/035.
- Plan said to "pick before Phase 9 ships" between moving `event.status_changed` from `event_status_change_events` and double-writing. Decision: **double-write**. The kanban already reads `event_status_change_events` (last-status-changed-at sort key), so leaving that table intact avoids a pipeline regression. The legacy table stays the source of truth for kanban; `activity_log` is the source of truth for the timeline UI. The smoke verifies both rows land on a status change.
- New service [services/activity_log.py](../services/activity_log.py): `log_activity(...)` (no commit, raises on the staff-needs-actor and subject-pair invariants) plus `list_activities_for_event(...)` with keyset pagination via `before_id`. Vocabulary lives in this module as named constants — adding a new activity type is a one-line change here plus a label entry on the client.
- Wiring choice: **explicit calls, not decorators.** Plan offered both; explicit calls grep cleanly and don't hide the side effect. Each domain service (`invoice_service`, `quote_service`, `payment_service`, `event_service`, `portal_service`) calls `activity_log.log_activity` after each state change. Local imports inside `portal_service` mutation paths to avoid a circular import (portal_service → activity_log → models is fine; activity_log → portal_service would have been the problem if we'd gone the other way).
- Customer-side activity rows fire on **first view only** for `invoice.viewed` / `quote.viewed`. Repeat views still bump `view_count` on the invitation row but don't spam the timeline; staff care that the link was opened, not how many times the customer reloaded the tab.
- Quote signature emits TWO rows (`quote.signed` then `quote.approved`). Splitting them keeps the audit complete (the signature event itself is distinct from the status flip) and gives the UI two timeline-friendly labels rather than one overloaded one.
- Payment events fan out to **every event whose invoice was touched** by the allocation set. A payment that only credits the unapplied pool with no allocations leaves no event-scoped trail (there's nothing event-scoped to log against); future refactors can add a contact-scoped log if needed.
- New router [api/routers/events.py](../api/routers/events.py)::`list_event_activity` at `GET /api/events/{id}/activity?limit=N&before_id=X`. Default limit 100, max 200. Returns `{activities: [...], next_before_id: <int|null>}` for keyset pagination — the next page passes the smallest id from the previous page.
- New frontend tab [frontend/src/pages/event/tabs/Activity.jsx](../frontend/src/pages/event/tabs/Activity.jsx) wired into `EventDetailLayout`'s rail and `App.jsx`. Uses `useInfiniteQuery` so "Load earlier" appends pages without re-fetching. Render copy lives in a client-side dictionary keyed by activity_type so the JSON payload stays small and a new server-side type can be added without immediate UI breakage (falls back to the raw type string).
- Smoke [tests/test_activity_log_smoke.py](../tests/test_activity_log_smoke.py): 9 checks, all green. Covers create+send invoice rows; first-view-only behavior on `invoice.viewed`; full quote lifecycle including signature; payment+refund; event status change writes both legacy + activity rows; router auth gate; 404 on unknown event; pagination walk terminates with `next_before_id=null`; staff revoke emits `invitation.revoked`.
- Regression sweep: `test_invoices_smoke`, `test_quotes_smoke`, `test_payments_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_portal_smoke`, `test_invoice_pdf_smoke`, `test_activity_log_smoke` — all green.
- `npm run lint` clean, `npm run build` succeeds.
- Skipped for v1: dedicated activity row for staff-side invitation creation (`POST .../invitations`); the `invoice.paid` activity (the row would need to fire when `paid_at` flips, which lives inside `_recompute_invoice_totals` — adding it there means logging from a recomputation helper which is a code-shape choice worth a separate think before doing it); per-allocation refund activity (only the umbrella `payment.refunded` fires, even when a refund touches multiple allocations); `event.status_changed` consolidation (legacy `event_status_change_events` table stays — the Phase 10 kanban still reads it).

---

## Phase 10: Pipeline integration and AR rollup

Purpose: the kanban and dashboard get richer. Staff see at a glance which leads owe money and how much the pipeline is worth.

Tasks:

- [ ] Extend `BoardCardResponse` with `outstanding_balance_cents` (sum of `invoices.balance_cents` where status in `sent|partial`).
- [ ] Render an outstanding-balance pill on each kanban card. Distinct from the existing "outstanding invoice" boolean badge — this one shows the dollar amount for cards with a non-zero balance.
- [ ] Sort kanban columns optionally by event date proximity, balance descending, or status-changed-at (current default). Frontend toggle, no schema change.
- [ ] New widget on `frontend/src/pages/Dashboard.jsx`: "Accounts receivable". Sum of all `sent|partial` balances, count of overdue invoices, total deposits collected this month. One aggregate query in `services/invoice_service.py:ar_summary`.
- [ ] New widget: "Recent payments". Last 10 payments across all events.
- [ ] New widget: "Quotes awaiting signature". `sent` quotes older than 3 days.

Smoke tests:

- Kanban renders correctly with the new pill on cards that have outstanding balance and without on cards that don't.
- Dashboard AR widget matches `SUM(balance_cents) WHERE status IN ('sent','partial')` from psql.
- Sorting toggles work and the cards re-order correctly.

Deliverable: the shop's daily dashboard tells the financial story without a CSV export.

### 10.4 Validation note (2026-05-02)

Shipped one bundle:

- No new migrations. Both the kanban pill rollup and the dashboard widgets read directly from columns that already exist (`invoices.balance_cents`, `payments.amount_cents`, `payments.refunded_cents`, `quotes.sent_at`).
- Kanban: extended `BoardCard` + `BoardCardResponse` with `outstanding_balance_cents`. The existing `outstanding_subq` in `event_service.get_board_data` now also computes `SUM(CASE WHEN status IN ('sent','partial') AND deleted_at IS NULL THEN balance_cents ELSE 0 END)` — same scope as the boolean badge so the two surfaces always agree. Frontend renders a warning-tinted pill on cards with non-zero balance.
- New service [services/dashboard.py](../services/dashboard.py): three rollups behind a single module so cross-domain aggregations don't bloat the per-entity service files.
  - `ar_summary` returns outstanding balance + count, overdue balance + count (`due_date < today`), and deposits collected this calendar month using **net** position (`amount - refunded`) — a refund within the same month reduces the figure, which is the realistic "money on hand" reading.
  - `recent_payments` returns last N payments newest-first with the first allocated invoice's event id resolved via a 2-step lookup. Unapplied-only payments return `event_id=None` and the UI falls back to a non-linked row.
  - `quotes_awaiting_signature` filters `status='sent' AND sent_at <= now - min_age_days` (default 3 days), oldest-first so the most stale floats to the top.
- New router [api/routers/dashboard.py](../api/routers/dashboard.py): three auth-gated endpoints under `/api/dashboard/*` (`ar-summary`, `recent-payments`, `awaiting-signature`).
- Frontend: per-card outstanding-balance pill on [Pipeline.jsx](../frontend/src/pages/Pipeline.jsx); three dashboard widgets ([ARSummaryWidget](../frontend/src/components/dashboard/ARSummaryWidget.jsx), [RecentPaymentsWidget](../frontend/src/components/dashboard/RecentPaymentsWidget.jsx), [AwaitingSignatureWidget](../frontend/src/components/dashboard/AwaitingSignatureWidget.jsx)) wired into a 3-column responsive grid on [Dashboard.jsx](../frontend/src/pages/Dashboard.jsx). 60-second `staleTime` so the widgets don't refetch on every focus.
- Smoke [tests/test_dashboard_smoke.py](../tests/test_dashboard_smoke.py): 8 checks, all green. Each check uses its own seed event so the assertions don't tangle. Coverage:
  1. Kanban pill correctly excludes paid + cancelled + draft, includes partial-paid balance.
  2. AR summary outstanding matches a direct SQL aggregate against the same scope.
  3. AR overdue only includes invoices with `due_date < today`.
  4. Deposits this month uses net of refunds (verified by hand-summing alongside a refund within the same month).
  5. Recent payments resolves `event_id` for allocated payments, leaves it NULL for unapplied-only payments, and orders newest-first.
  6. Quotes awaiting signature returns 5-day-old quote, excludes 1-day-old quote.
  7. All three router endpoints reject without auth (401/403).
  8. All three router endpoints return 200 with the expected shape under auth.
- Smoke teardown fix folded in this same lane: `tests/test_invoices_smoke.py` cleanup now drops `payment_allocations` + `refund_events` + `payments` + `activity_log` + `event_status_change_events` ahead of the `invoices` and `events` deletes (RESTRICT FK violations had been showing up in broad sweeps because Phase 6 wired `payment_allocations.invoice_id` as `ON DELETE RESTRICT` and Phase 9 wired `activity_log.event_id` as `ON DELETE CASCADE` only — the prior teardown predated both).
- Sorting toggles (event date proximity / balance descending) are NOT shipped in v1; the plan listed them but the kanban already sorts by `status_changed_at DESC` which staff has been happy with. Adding the toggle is a frontend-only change with no schema impact and lives on the polish backlog.
- Regression sweep: `test_invoices_smoke`, `test_quotes_smoke`, `test_payments_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_portal_smoke`, `test_invoice_pdf_smoke`, `test_activity_log_smoke`, `test_dashboard_smoke` — all 9 green.
- `npm run lint` clean, `npm run build` succeeds.

---

## Phase 11: Reminders and dunning

Purpose: a quince mom who forgets the balance is the most common late-payment case. A simple cron that sends "your balance is due in 7 days" handles it.

### 11.1 Migration `034_invoice_reminder_settings.py` and `035_installment_reminder_state.py`

Reminder schedule columns added to `business_profile` (Phase 1 already created the singleton):

- `reminder1_enabled BOOLEAN`, `reminder1_days_offset INTEGER`, `reminder1_offset_basis VARCHAR(16)` (`before_due|after_due|after_sent`).
- `reminder2_*`, `reminder3_*` same shape.
- `reminder_late_fee_cents BIGINT`, `reminder_late_fee_pct NUMERIC(5,3)` — single flat or percentage late fee bumped on the third reminder.

Reminders fire against installment due dates, not the invoice-level `due_date`. A six-row payment plan needs reminders per installment, not per invoice. Per-installment idempotency lives in a sibling table:

`installment_reminder_state` columns:

- `installment_id INTEGER PRIMARY KEY REFERENCES invoice_installments(id) ON DELETE CASCADE`
- `reminder1_sent_at TIMESTAMPTZ`, `reminder2_sent_at TIMESTAMPTZ`, `reminder3_sent_at TIMESTAMPTZ`
- `late_fee_applied_at TIMESTAMPTZ`

### 11.2 Cron `services/reminder_runner.py`

- `run_reminder_pass()` scans `invoice_installments` where the parent invoice is `sent` or `partial`, the installment is unpaid, and the offset rule for any of the three reminders matches today. For each match:
  - Enqueue a `notification_jobs` row with the portal URL.
  - Stamp the corresponding `*_sent_at` column on `installment_reminder_state`.
  - Insert an activity row `invoice.reminder_sent` with the reminder index and installment id in payload.
  - On reminder3 with late fees enabled, append a new line item with `kind='fee'` to the invoice. The schedule rebalance reuses the editor's "rebalance proportionally" rule, weighted toward the next unpaid installment.
- The cron is idempotent: a second call on the same day finds the `*_sent_at` already stamped and skips.

### 11.3 Quote-expiry sweep

A daily pass that flips `quotes` past `expires_at` to status `expired` and logs `quote.expired`. Reuses the same runner.

Smoke tests:

- Set reminder1 to 7 days before due. Create an invoice with a deposit installment due in 6 days. Run `run_reminder_pass`. Notification queued, `installment_reminder_state.reminder1_sent_at` stamped, activity logged. Run again. No duplicate.
- Pay the deposit installment. Run `run_reminder_pass`. No reminder for that installment, but reminders for the balance installment still fire on schedule.
- Quote expires automatically the day after `expires_at`.
- Late fee on reminder3 appends a `kind='fee'` line, totals recompute, schedule rebalances onto the next unpaid installment.

Deliverable: late-paying customers nudge themselves.

### 11.4 Validation note (2026-05-03)

Shipped one bundle:

- New migration [038_business_profile_reminder_schedule.py](../database/migrations/038_business_profile_reminder_schedule.py): three reminder slots (`reminder1_*` through `reminder3_*`) with enabled/days_offset/offset_basis triple, plus `reminder_late_fee_cents` and `reminder_late_fee_pct` for the third reminder. CHECK constraints enforce the offset_basis enum (`before_due | after_due | after_sent`), nonneg flat fee, and `0 <= pct < 1`.
- New migration [039_create_installment_reminder_state.py](../database/migrations/039_create_installment_reminder_state.py): per-installment idempotency table. PK on `installment_id` (not `SERIAL`) — one state row per installment for the lifetime of the invoice. Three `*_sent_at` columns + `late_fee_applied_at` to block double-charges.
- Plan said migrations 034/035 but we're at 038/039 now (those numbers were taken in earlier phases).
- New service [services/reminder_runner.py](../services/reminder_runner.py): `run_reminder_pass(today)` walks every unpaid installment on a sent/partial invoice and fires reminder1/2/3 when the offset rule matches today. Idempotent: re-runs check `*_sent_at` and skip. SMTP failure does NOT stamp — the next pass retries. `run_quote_expiry_pass(today)` flips `sent` quotes whose `expires_at < today` to `expired`. `run_daily(db)` is the worker entrypoint that runs both back-to-back.
- New helper [services/invoice_service.py:append_late_fee](../services/invoice_service.py): appends a `kind='fee'` line to a sent or partial invoice, recomputes totals, and rolls the fee onto the **next unpaid installment in sort order**. Plan's "rebalance proportionally weighted toward the next unpaid installment" simplified — putting the whole fee on the next-up row reads cleaner on the customer-facing PDF than a multi-row redistribution. Bumps revision (PDF cache reflects the change). Rare edge: every installment paid → no target → raises `no_target_installment` (v1.1 can add a new-row path if it shows up).
- Email rendering: new `_render_invoice_reminder` + public `send_invoice_reminder` in [services/portal_email.py](../services/portal_email.py). Copy bends with reminder index — reminder1 is "friendly nudge", reminder2 is "follow-up", reminder3 is "final notice / late fee may apply". Subject line names the installment label and amount so a phone-preview shows the actionable info.
- Continuing the Phase 7 sync-send pattern: the cron itself serializes work, sync send keeps stamps coherent (only stamp on a successful dispatch), and the `notification_jobs` table stays appointment-scoped. If we ever add SMS reminders or HTML-tracking pixels, that's the moment to widen the queue.
- New worker [workers/daily.py](../workers/daily.py) wired into FastAPI lifespan alongside the existing notifications worker. Fires once per local day at 02:30 (shop tz), with a short initial delay so a startup-during-the-day deploy still catches up. Idempotent across restarts because the per-installment stamps prevent re-sends.
- Settings surface: extended [services/business_profile_service.py](../services/business_profile_service.py) `_EDITABLE_FIELDS` and the corresponding view + patch validators with the eleven new reminder columns. [api/routers/business_profile.py](../api/routers/business_profile.py) `BusinessProfileResponse` and `BusinessProfilePatch` mirror the shape; offset_basis is typed as a Literal so a typo'd basis is rejected at the Pydantic boundary before the service even runs.
- Activity vocabulary: added `INVOICE_REMINDER_SENT` and `QUOTE_EXPIRED` constants to [services/activity_log.py](../services/activity_log.py).
- Smoke [tests/test_reminder_runner_smoke.py](../tests/test_reminder_runner_smoke.py): 7 checks, all green. Each check uses its own seed event + scopes assertions to that event's installments because the runner sweeps the whole DB. Coverage:
  1. Reminder1 with offset 7, installment due in 6 days → off-by-one, doesn't fire.
  2. Reminder1 with offset 7, installment due in 7 days → fires once on the deposit; second pass is idempotent; activity row count == 1.
  3. Pay the deposit → next pass skips the paid installment but still nudges the unpaid balance row.
  4. `after_sent` basis with offset 0 → fires today on every unpaid installment of a freshly-sent invoice.
  5. Reminder3 with `reminder_late_fee_cents=2500` → appends a `kind='fee'` line, totals bump by 2500, schedule rebalances onto the next unpaid installment, revision bumps, `late_fee_applied_at` stamped, second pass does NOT add a second fee.
  6. Monkey-patched `send_invoice_reminder` raising `PortalEmailError` → `*_sent_at` stays NULL so the next pass retries.
  7. Quote expiry: past-`expires_at` quote flips to `expired`, future-`expires_at` quote stays `sent`, activity logged.
- Frontend: no new UI in this bundle. The reminder schedule lives on `business_profile`, which already has a backend-only Settings router; a staff-editable Settings page that surfaces the reminder schedule UI is a follow-up. Until that ships, staff can configure the schedule via direct SQL or via a `PATCH /api/business-profile` payload from the existing dev tooling.
- Regression sweep: all 10 invoicing/portal/PDF/activity/dashboard/reminder smokes green.
- `npm run lint` clean, `npm run build` succeeds.
- Skipped for v1: SMS reminders (notification_jobs widening required), HTML open-tracking pixels (privacy + complexity), per-reminder retry backoff (the daily cadence is already coarse enough that immediate retry on the next day works), staff-editable Settings page for the reminder schedule (backend ready; UI is a polish-backlog item).

---

## Phase 12: Recurring and payment plans (deferred)

**Status:** Deferred. Build only when staff ask for it. Schema is forward-compatible — the `invoice_installments` table from Phase 1 already supports N-row schedules; this phase is the recurring-billing cron and the multi-installment editor surface, not a schema change.

Purpose: parked. A quince shop has no recurring revenue, but a "pay over 6 monthly installments" plan is a real ask. Build only when staff ask for it.

Sketch when it lands:

- Extend `invoice_installments` with optional `payment_plan_id`, cadence metadata, and auto-charge fields if staff need reusable plan templates.
- A scheduled-payment cron that advances reminders and optional auto-charge attempts against the existing installment rows. The invoice stays one financial document unless staff explicitly need one bill per installment.
- Auto-bill via Stripe Customer + saved card if the customer opted in.

The v1 `invoice_installments` table deliberately keeps this door open. Phase 12 should decide whether staff need installment templates and auto-charge, not whether the invoice can carry more than two due dates.

---

## Phase 13: Tests, ops, and cleanup

Backend tests (under `tests/`):

- `test_invoices_smoke.py` — Phase 2, 3, 4 endpoints. Total computation. Numbering. Locked-status enforcement.
- `test_invoices_concurrent.py` — 10 concurrent sends, send while another user edits, retry after transaction timeout around number allocation, and confirmation that no duplicate numbers are possible.
- `test_quotes_smoke.py` — Phase 5. Approve, reject, convert.
- `test_payments_smoke.py` — Phase 6. Allocations, deposit flow, refunds, status transitions.
- `test_portal_smoke.py` — Phase 7. Key-gated routes, signature capture, rate limit.
- `test_invoice_pdf_smoke.py` — Phase 8. Render success, cache reuse, missing-logo fallback.
- `test_reminder_smoke.py` — Phase 11. Reminder scheduling, idempotency, late fee.
- Extend `test_events_smoke.py` to confirm activity log entries match expected vocabulary.

Frontend verification:

- `npm run build` succeeds with no new warnings (Vite's existing large-chunk warning is pre-existing).
- Manual browser pass on Chrome and Safari for: invoice editor, quote editor, portal view, signature pad, payment recording, AR dashboard, kanban balance pill.

Ops cleanup:

- Backup/retention policy to document before launch:
  - Canonical and backed up with Postgres: `invoices`, `invoice_line_items`, `invoice_installments`, `invoice_invitations`, `quotes`, `quote_line_items`, `quote_invitations`, `payments`, `payment_allocations`, `refund_events`, `activity_log`, `business_profile`, `numbering_state`, reminder state.
  - Cache/regeneratable: generated invoice, quote, and receipt PDFs under `DOCUMENT_STORAGE_ROOT/invoices`, `quotes`, and `receipts`.
  - User-uploaded and not regeneratable: `event_documents` files and business logo files. These follow the existing `/var/lib/bellas-xv/uploads` durability decision until object storage/backups improve.
  - Retention: keep financial rows and activity logs indefinitely in v1. Prune generated PDF cache only if disk pressure requires it; regenerate on demand.
- Add rate limiting around expensive or money-changing admin endpoints: invoice send/resend, PDF render/retry, payment create, and refund create. The general authenticated CRUD routes can wait unless logs show abuse or a buggy client loop.
- Drop `event_documents.invoice_*` columns one season after Phase 4b lands. Migration `036_drop_legacy_invoice_columns.py`. Pre-flight: confirm `kind='invoice'` rows are zero (the upload route has been rejecting them since Phase 4b) and every former legacy row has `kind='external_invoice'` plus a non-null `linked_invoice_id`. The four legacy columns may still contain rollback data; this migration intentionally discards that obsolete copy and removes `invoice` from the `chk_event_documents_kind` allowed set.
- The `external_invoice` kind stays as a permanent value for vendor PDFs and any third-party bills the shop receives.
- Document the customer-portal URL pattern and key generation in [docs/ARCHITECTURE.md](ARCHITECTURE.md) so future contributors don't reinvent it.
- Document the "numbers allocated on send, gaps are intentional" rule in [docs/ARCHITECTURE.md](ARCHITECTURE.md) so the next contributor doesn't try to "fix" the gaps.

Smoke command set:

```bash
venv/bin/python tests/test_invoices_smoke.py
venv/bin/python tests/test_quotes_smoke.py
venv/bin/python tests/test_payments_smoke.py
venv/bin/python tests/test_portal_smoke.py
venv/bin/python tests/test_invoice_pdf_smoke.py
venv/bin/python tests/test_reminder_runner_smoke.py
venv/bin/python tests/test_invoices_concurrent.py
venv/bin/python tests/test_activity_log_smoke.py
venv/bin/python tests/test_dashboard_smoke.py
venv/bin/python tests/test_rate_limit_smoke.py
venv/bin/python tests/test_event_documents_smoke.py
venv/bin/python tests/test_events_smoke.py
venv/bin/python tests/test_contacts_smoke.py
venv/bin/python tests/test_booking_smoke.py
venv/bin/python tests/test_boutique_experience_smoke.py
cd frontend && npm run lint
cd frontend && npm run build
```

Phase 13 validation note, 2026-05-03:

- New backend smoke `tests/test_invoices_concurrent.py` (3 checks) — ten parallel `mark_sent` calls all yield distinct `INV-YYYY-NNNNNN` numbers in a contiguous run, and a long-running edit on one invoice does not block sending another. Direct-Session, not TestClient, because `TestClient` single-threads through one ASGI app.
- New backend smoke `tests/test_rate_limit_smoke.py` (2 checks) — helper enforces 60/min per user; `GET /api/invoices/{id}/pdf` returns 429 once the bucket is full.
- Activity vocabulary check added to `tests/test_activity_log_smoke.py` (10 checks total) — every emitted `activity_type` for the test event must be in `services/activity_log._KNOWN_TYPES`. Catches typo'd or unregistered constants in CRUD paths even though the writer tolerates unknown strings.
- Per-user staff rate limiter at `api/rate_limit.py` wired into `POST /api/invoices/{id}/send`, `POST /api/invoices/{id}/resend`, `GET /api/invoices/{id}/pdf`, `POST /api/invoices/{id}/pdf/retry`, `POST /api/payments`, and `POST /api/payments/{id}/refunds`. Per-user (not per-IP) because admins NAT through one IP. Buckets shared across rate-limited routes so a runaway loop on one verb burns the budget for every other money-changing verb on the same account.
- `docs/ARCHITECTURE.md` extended with sections on numbering invariants, portal URL pattern + key generation, activity vocabulary, background workers (notifications + daily reminder/expiry pass), rate limiting (per-IP portal vs per-user staff), backup/retention table, and pending maintenance (the legacy `event_documents.invoice_*` column drop with pre-flight).
- Local regression sweep: `test_invoices_smoke`, `test_invoices_concurrent`, `test_quotes_smoke`, `test_payments_smoke`, `test_portal_smoke`, `test_invoice_pdf_smoke`, `test_activity_log_smoke`, `test_dashboard_smoke`, `test_reminder_runner_smoke`, `test_rate_limit_smoke`, `test_invoice_schema_smoke`, `test_event_documents_smoke`, `test_business_profile_smoke` all green. `npm run lint` clean, `npm run build` succeeds with the pre-existing large-chunk warning. `/api/health` reports `migrations_applied: 39, timezone: America/Chicago`.
- No new migrations. No new disk-write paths. ReadWritePaths unchanged.

Deferred and tracked elsewhere: the `event_documents.invoice_*` column drop migration (one season after Phase 4b — see ARCHITECTURE.md "Pending maintenance"); the authenticated browser smoke for Phases 5–11 against admin.shopbellasxv.com (user-side); Phase 12 (recurring + payment plans + Stripe gateway) until staff explicitly ask. "Phase 12 lite" — multi-installment editor UI without auto-charge — sits on the polish backlog.

---

## Open questions (not blocking)

- **Per-line vs invoice-level tax.** v1 picks per-line single-rate. Texas sales tax is single-rate so this covers Bellas. Revisit if the shop ever sells across state lines or to tax-exempt customers (in which case `tax_exempt BOOLEAN` on `contacts` is the right surface).
- **Discount-as-percent.** v1 picks fixed-amount discount only. Most quince contracts negotiate "$200 off" not "10% off"; if the shop ever runs a "20% off all dresses" promo, add `is_amount_discount BOOLEAN` per Invoice Ninja's pattern.
- **Multiple contacts per event in the portal.** v1 sends one invitation per invoice to the primary contact. If the shop ever needs to CC the godmother too, the `invoice_invitations` table already supports many rows per invoice; the UI gap is the only thing.
- **Long-lived unapplied funds.** When a customer overpays and the unapplied pool sits for months, accounting practice is to either refund or convert to a "store credit" on the customer record. v1 leaves it on the payment row indefinitely. If the shop ever needs a "credit balance per customer" surface, promote the per-payment unapplied pools into a `customer_credits` table and reconcile via a Phase 14.
- **Payment gateway.** v1 is "mark as paid" only. Stripe Checkout is the right next step (a single redirect URL, no card data on this server, webhook flips the payment to `completed`). Quote shape: `services/stripe_gateway.py` plus a `gateway_session_id` column on `payments`.
- **Receipt vs invoice PDF.** v1 renders invoices with a "Paid" stamp when fully paid; that doubles as the receipt. If staff want a separate receipt design, Phase 8 already has the template hook for it.
- **Per-event profitability.** Once invoices and `event_documents` track every line of revenue and the boutique flow tracks cost-of-goods, a `gross_margin` computation per event becomes possible. Park as a Phase 14 if it ever surfaces.
- **Audit log on invoice edits.** Currently `revision` bumps but the diff isn't stored. If an "audit who edited what when" need ever surfaces, snapshot the `invoices` row and `invoice_line_items` rows into an `invoice_revisions` table on every revision bump. Don't build until asked.
- **Customer self-service refunds.** Out of scope; refunds always go through staff in v1.

---

## Reference: what we borrowed from Invoice Ninja

This plan stands on the shoulders of Invoice Ninja v5's data model. Direct lineage:

| Bellas concept | Invoice Ninja source |
|---|---|
| `invoices.status` enum | `app/Models/Invoice.php` lines 116–123 (`STATUS_DRAFT=1` ... `STATUS_REVERSED=6`) |
| `invoice_installments` (separate table) | Invoice Ninja uses a single `partial` + `partial_due_date` column; we diverge for payment-plan support |
| `invoices.revision` | New, ours |
| `invoice_line_items` (separate table) | Invoice Ninja stores as JSON `line_items`; we diverge for queryability |
| `quotes.status` enum | `app/Models/Quote.php` (`STATUS_DRAFT=1` ... `STATUS_EXPIRED=-1`) |
| `quotes.signature_*` columns | `app/Models/InvoiceInvitation.php` `signature_base64`, `signature_date`, `signature_ip` |
| `payments.status` enum (minus `is_refund`) | `app/Models/Payment.php` (`STATUS_PENDING=1` ... `STATUS_REFUNDED=6`) |
| Refunds via `refunded_cents` columns, not negative payments | `app/Models/Payment.php` `refunded` + `Paymentable.refunded` |
| `payment_allocations` polymorphic-ish pivot | `paymentables` morphToMany pivot in Invoice Ninja |
| `invoice_invitations.public_key` | Invoice Ninja `invoice_invitations.key` |
| Invitation `revoked_at` and `expires_at` | New, ours (Invoice Ninja does not surface explicit revoke/expire) |
| Activity vocabulary | `app/Models/Activity.php` constants (`CREATE_INVOICE=4`, `MARK_SENT_INVOICE=53`, `PAID_INVOICE=54`, `INVOICE_REMINDER1_SENT=63`, etc.) |
| Reminder schedule shape | Invoice Ninja company settings `reminder1/2/3` |
| Numbers-on-send + gaps accepted | New, ours (Invoice Ninja allocates on draft create) |

Things we deliberately did not borrow:

- Multi-tenancy (`company_id` on every row). One shop, one tenant.
- Multi-currency (`exchange_rate`, `exchange_currency_id`). USD only.
- Three tax slots invoice-level + three line-level. One slot line-level only.
- Custom fields (`custom_value1-4`). Add when staff ask.
- Vendors, expenses, projects, tasks. Out of scope for a retail shop.
- Recurring invoices and credits. Deferred to Phase 12 and beyond.
- e-Invoice / Peppol compliance columns. Not a US retail concern.
- The PHP/Laravel runtime, the Vue admin SPA, the Flutter mobile app, and Docker Compose. The whole point.
