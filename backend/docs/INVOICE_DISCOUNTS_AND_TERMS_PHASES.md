# Invoice & Quote Discounts and Payment Terms - Phased Plan

A focused refactor of the discount and payment-term experience on quotes and invoices. The goal is to let customers see how much they save, give them a real payment-plan choice on quotes, and let staff drive both surfaces from a small set of pre-configured options instead of hand-entering every discount and schedule.

## Goal

- Customers see a clear pre-discount subtotal, the discount applied, the savings dollar amount, and the post-discount total on every quote and invoice PDF.
- Quotes carry a payment schedule that survives conversion to an invoice. Today they do not.
- Staff pick from configured discount presets and a 1/2/3-payment plan dropdown instead of typing percentages and dates by hand.
- Tax is calculated on the post-discount taxable base, not the post-tax total. The customer's printed tax line reflects what they actually owe in tax after discounts apply.

## Decisions Locked

### Discounts

- **Order-level discounts are percent-only**, not amount or percent. Each row is stored as `Numeric(5,2)` in the 0-50 range. The combined stack is capped at 50% as a fat-finger guard; raise it if the business wants larger promos.
- **Discount presets are configured in BusinessProfile** as a `discount_presets` JSONB column. Shape: `[{id, label, percent, active}]`. At most 12 total presets (active + inactive combined). Seeded with three real presets on the existing `BusinessProfile` row at migration time:
  - Moonlight Ballroom - 10%
  - Military - 5%
  - Same-day - 2%
- **Stacking is allowed**: per-line discounts and one or more order-level discounts all apply, all render, and never replace each other. Multiple order discounts combine additively, e.g. Military 5% + Same-day 2% = 7%.
- **Snapshot each order discount on child rows**: `invoice_order_discounts` and `quote_order_discounts` store `preset_id`, `label`, `percent`, and `sort_order`. Presets still live inside `business_profile.discount_presets`; there is no FK because they are JSONB entries. Renaming a preset later does not rewrite history. If a saved preset is later deleted, update paths preserve the existing snapshot so unrelated edits do not fail.
- **Per-line discounts stay absolute cents in storage**, but the editor swaps to an opt-in slider UX. By default a line shows no discount controls. Staff click "Apply discount" on a specific line to reveal a 0-50% slider; the slider writes computed `discount_cents` into the existing column. Removing the discount clears the column and hides the slider.
- **"Custom %" is allowed in the editor** for one-off discounts that do not match a preset. It writes an order-discount row with `preset_id = NULL`, a label defaulting to `"Custom"`, and a 0-50 percent.

### Money Math (Tax After Discounts)

Today the order-level discount subtracts from the post-tax total. Going forward the order discount reduces the taxable base, so customers pay tax only on what they actually owe.

Per line, today:

```
line_sub = qty * unit - line_discount
line_tax = line_sub * rate
line_total = line_sub + line_tax
```

Per line, after this change, when the record has at least one order-discount row:

```
line_sub_pre_order = qty * unit - line_discount
line_sub = line_sub_pre_order * (1 - combined_order_discount_percent / 100)
line_tax = line_sub * rate
line_total = line_sub + line_tax
```

Order total:

```
total_cents = SUM(line_total)        # order discount already baked into lines
discount_cents = round(SUM(qty*unit - line_discount) * combined_order_discount_percent / 100)
```

The `discount_cents` column becomes a derived display value whenever order-discount rows exist. It is recomputed every time totals recompute. The child rows are the source of truth.

**Backward compatibility:** records with no order-discount child rows and `discount_cents > 0` keep the legacy post-tax subtraction, untouched. The new pre-tax math only runs when at least one child row exists. This protects already-sent invoices and quotes from total drift.

Worked example, $4,000 subtotal, 7% tax, 10% Moonlight Ballroom preset, no per-line discounts:

| Step | Legacy | New |
|---|---|---|
| Subtotal | $4,000.00 | $4,000.00 |
| Order discount | -$400.00 (after tax) | -$400.00 (off taxable base) |
| Taxable subtotal | $4,000.00 | $3,600.00 |
| Tax @ 7% | $280.00 | $252.00 |
| Total | $4,280 - $400 = $3,880.00 | $3,852.00 |

Customer saves an extra $28 in tax under the new math because the discount also shrank the tax base. This is the intended behavior.

### Payment Terms

- **Quotes get installments.** New `quote_installments` table mirrors `invoice_installments` minus payment-state columns (no `paid_at`, no `staff_notes`). Columns: `quote_id`, `label`, `amount_cents`, `due_date`, `sort_order`.
- **Quote-to-invoice conversion copies installments.** The current `services/quote_service.py:736` path mints a default 50/50 schedule. After this change, if the quote has installments, the invoice copies them line for line and skips the default. If the quote has no installments, the existing 50/50 default still applies.
- **Plan selector replaces free-form schedule editing** on both quote and invoice editors. The selector exposes:
  - Plan count: dropdown of 1, 2, 3
  - Deposit percent: numeric input clamped 50-100, defaulted from BusinessProfile
  - Generated rows with editable due dates and a locked auto-calculated amount column
  - "Custom amounts" toggle that unlocks amount editing as an escape hatch when the standard plan does not fit
- **Server enforces plan validity** on both quote and invoice writes:
  - Plan count must be 1, 2, or 3
  - Deposit (first installment) must be at least 50% of total
  - Sum of installments must equal total
  - Reject with 422 otherwise
- **Date anchoring rules** for the auto-generated schedule:
  - Deposit due = `issue_date + 14 days`
  - Final balance due = `event_date - 60 days` if event date exists and is more than 60 days out
  - Final balance due = `issue_date + 60 days` otherwise (no event date, or event is too soon)
  - For 3 payments, the middle payment due date is the midpoint between the deposit due date and the final balance due date
- **BusinessProfile defaults** for the plan selector: `default_payment_plan_count` (1, 2, or 3), `default_deposit_percent` (Numeric, >=50). Both nullable; UI falls back to "2 payments / 50% deposit" if unset.

### PDF Rendering

- Both quote and invoice PDFs render the same totals block, defined once in [templates/pdf/_lineitems.html](templates/pdf/_lineitems.html) (or a new `_totals.html` partial pulled in by both):

```
Subtotal:                       $X.XX
Line discounts:                -$A.AA   (only when any line has a discount)
Subtotal after line discounts:  $Y.YY   (only when line discounts exist)
Order discount: Moonlight Ballroom (10.00%)  -$B.BB   (one row per non-zero order discount)
Taxable subtotal:               $Z.ZZ
Tax:                            $T.TT
Total:                          $N.NN

You save: $A + $B
```

- The "You save" line only renders when total savings > 0.
- Each order discount row uses the snapshotted child-row `label` and `percent`, not a live preset lookup.
- The Tax row has no inline percent suffix. Line items can carry different tax rates today, so a single "(7%)" on the rolled-up tax line could lie. Add tax-rate grouping later if customers actually need that breakdown.
- No em dashes in the rendered copy. Use forward-slash separators or plain phrasing per the project's customer-copy rule.
- Quote PDFs additionally render the payment schedule the same way invoice PDFs do today (the existing block in [templates/pdf/invoice.html](templates/pdf/invoice.html#L29-L46)).

## Open Decisions

None blocking. The following are intentionally deferred:

- Whether to allow discount percentages above 50%. Current cap is a sanity guard, not a policy ceiling. Raise the cap whenever the business actually needs a larger promo.
- Whether per-line discount should also become percent-stored instead of absolute cents. Today line discounts are absolute cents, and the slider is a UX layer that computes cents from percent on the fly. If staff start needing percent history per line, revisit this. Until then, no schema churn.
- Whether the deposit floor should be configurable per quote/invoice rather than store-wide. Defer until the business actually wants to override 50% for a specific customer.

## Schema Changes

### `business_profile`

```
discount_presets               JSONB NOT NULL DEFAULT '[]'
default_payment_plan_count     SMALLINT NULL
default_deposit_percent        NUMERIC(5,2) NULL
```

Validation in service layer:

- `discount_presets` is a list with at most 12 total entries (active and inactive combined).
- Each entry: `id` (string, generated), `label` (string, 1-60 chars), `percent` (Numeric, 0-50), `active` (bool).
- `default_payment_plan_count IN (1, 2, 3)` when not null.
- `default_deposit_percent` between 50 and 100 when not null.

Migration seeds the existing BusinessProfile row with the three real presets:

```json
[
  {"id": "moonlight",  "label": "Moonlight Ballroom", "percent": 10, "active": true},
  {"id": "military",   "label": "Military",           "percent":  5, "active": true},
  {"id": "same_day",   "label": "Same-day",           "percent":  2, "active": true}
]
```

### `invoice_order_discounts`, `quote_order_discounts`

```
id              BIGSERIAL PRIMARY KEY
invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE
quote_id        INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE
sort_order      INTEGER NOT NULL DEFAULT 0
preset_id       TEXT NULL
label           TEXT NOT NULL
percent         NUMERIC(5,2) NOT NULL CHECK (percent >= 0 AND percent <= 50)
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

Each table uses its matching parent FK (`invoice_id` for invoices, `quote_id` for quotes) and has an index on `(parent_id, sort_order)`. Migration 051 backfills the old single-snapshot columns into one child row, normalizes blank legacy labels to `"Custom"`, validates with unique probe labels, then drops `discount_preset_id`, `discount_label`, and `discount_percent` from `invoices` and `quotes`.

`discount_cents` is unchanged in shape. Its meaning shifts to "calculated display value when order-discount rows exist, legacy absolute cents otherwise."

### `quote_installments` (new)

```
id              BIGSERIAL PRIMARY KEY
quote_id        BIGINT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE
sort_order      INTEGER NOT NULL DEFAULT 0
label           TEXT NULL
amount_cents    BIGINT NOT NULL
due_date        DATE NOT NULL
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

Index on `(quote_id, sort_order)`.

Per the project rule, validate the new table with real INSERTs in the migration smoke before declaring the phase done.

## API Contracts

### `GET /api/business-profile`

Includes `discount_presets`, `default_payment_plan_count`, `default_deposit_percent`.

### `PATCH /api/business-profile`

Accepts `discount_presets` as a full list (replace semantics), plus the two scalar defaults. Service-level normalization re-derives missing `id`s and rejects out-of-range values.

### `POST /api/quotes`, `PATCH /api/quotes/{id}`

Accept `order_discounts: [{preset_id? , label?, percent?}]` and `installments`. `order_discounts` has replace semantics; `[]` clears the stack. Preset rows snapshot the current BusinessProfile label/percent on write. Update paths preserve already-saved snapshots if the source preset was deleted after the record was created. The body's `discount_cents` is ignored when order-discount rows exist; the server computes it. `installments` is the same shape as the existing invoice installment input minus payment-state fields.

### `POST /api/invoices`, `PATCH /api/invoices/{id}`

Same discount surface as quotes. Existing installment shape unchanged but the server enforces the new plan validity rules (count, deposit floor, sum).

### Public read DTOs

Quote and invoice public DTOs gain:

- `order_discounts`
- `subtotal_pre_discount_cents` (calculated)
- `line_discount_total_cents` (calculated)
- `you_save_cents` (calculated)

The public PDF render context uses these directly so the templates do no math.

## Phase 0: Current State Audit

Already completed informally during plan negotiation. Locked findings:

- Order-level discount column exists on both invoices and quotes ([database/models.py:418](database/models.py#L418), [database/models.py:543](database/models.py#L543)) but the editors hard-code `discount_cents = 0` ([frontend/src/components/QuoteEditor.jsx:266](frontend/src/components/QuoteEditor.jsx#L266), [frontend/src/components/InvoiceEditor.jsx:259](frontend/src/components/InvoiceEditor.jsx#L259)).
- Per-line discount column exists and is editable inline.
- Invoice installments are fully modeled ([database/models.py:485-500](database/models.py#L485-L500)) with a free-form 50/50 default ([frontend/src/components/InvoiceEditor.jsx:131-150](frontend/src/components/InvoiceEditor.jsx#L131-L150)).
- Quote installments do not exist.
- Quote-to-invoice conversion mints a default schedule rather than copying ([services/quote_service.py:736](services/quote_service.py#L736)).
- PDF discount rendering is one generic `-$X` line ([templates/pdf/_lineitems.html:42-44](templates/pdf/_lineitems.html#L42-L44)). No "you save" framing. Quote PDF has no payment schedule block.

## Phase 1: Discount Presets in BusinessProfile

Purpose: make the dropdown source of truth real before any editor touches it.

Tasks:

- [x] Migration adds `discount_presets`, `default_payment_plan_count`, `default_deposit_percent` columns to `business_profile`.
- [x] Migration seeds the existing BusinessProfile row with the three locked presets (Moonlight Ballroom, Military, Same-day).
- [x] Migration validates with a real `UPDATE` round-trip on the singleton row. (`business_profile` has `CONSTRAINT chk_business_profile_singleton CHECK (id = 1)` from [database/migrations/023_create_business_profile.py:35](database/migrations/023_create_business_profile.py#L35), so a second `INSERT` is impossible. The "real INSERT" rule applies to `quote_installments` in Phase 4.)
- [x] SQLAlchemy model updated.
- [x] Service-layer normalization in `services/business_profile_service.py` (or wherever the existing setter lives): generate missing `id`s, reject percent out of 0-50, reject more than 12 entries, reject `default_deposit_percent` outside 50-100, reject `default_payment_plan_count` outside {1,2,3}.
- [x] API: include the new fields on GET and accept them on PATCH.
- [x] BusinessProfile UI: a "Discounts" section listing presets with add/edit/remove rows, an active toggle, and a percent input. A "Payment plan defaults" section with the count dropdown and deposit percent input.
- [x] Smoke tests in `tests/test_business_profile_smoke.py`: round-trip, validation rejects, seed presence.

Deliverable: staff can configure presets and plan defaults in BusinessProfile. Editors do not consume them yet.

## Phase 2a: Order-Level Discount Selector and New Tax Math

Purpose: replace the hardcoded zero with a real selector and switch to pre-tax discount math for any record that uses the new percent path.

Tasks:

- [x] Migration adds the original single-snapshot `discount_preset_id`, `discount_label`, `discount_percent` columns to both `invoices` and `quotes`. Phase 7 supersedes these with child tables and drops the parent columns.
- [x] Migration validates with a real round-trip on both tables.
- [x] Models updated.
- [x] Update `_compute_line_amounts` in [services/invoice_service.py](../services/invoice_service.py) and the equivalent in [services/quote_service.py](../services/quote_service.py) to accept the parent's order percent. When percent is set, apply it to `line_sub` before computing tax. When null, behave exactly as before.
- [x] Update `_recompute_totals` in [services/invoice_service.py](../services/invoice_service.py) and the quote equivalent: when an order-discount percent path is active, recompute every line (the tax base shifts), recompute `discount_cents` as derived, set `total_cents = SUM(line_total)`. When inactive, keep the existing `total = SUM(line_total) - discount_cents` path.
- [x] Editor change in [frontend/src/components/InvoiceEditor.jsx](../frontend/src/components/InvoiceEditor.jsx) and [frontend/src/components/QuoteEditor.jsx](../frontend/src/components/QuoteEditor.jsx): replace the hardcoded `discount_cents = 0` with an order-discount control above the totals block.
- [x] Editor surfaces savings inline: "Saves $X" hint next to the selector when a discount is selected.
- [x] Editor PATCH writes the order discount snapshot input. The server computes `discount_cents`.
- [x] API contract: services snapshot the preset's current label and percent at write time. Renaming a preset later does not change the snapshotted value on existing records.
- [x] Smoke tests:
  - Legacy record with `discount_cents = 1000` and no order-discount percent path keeps post-tax math.
  - New record with a 10% order discount produces pre-tax math; `discount_cents` matches `round(subtotal_pre_discount * 0.10)`.
  - Renaming a preset in BusinessProfile does not change the snapshotted label on an already-saved invoice.
  - Stacking: a line with a $50 line discount and an order with 10% Moonlight produces both savings in the totals.

Deliverable: staff can pick a discount preset on a quote or invoice and tax is computed on the post-discount taxable base. Old records are untouched.

## Phase 2b: Per-Line Discount Slider UX

Purpose: replace the always-visible per-line discount input with an opt-in slider so staff only think about line discounts on the lines that actually need one.

Tasks:

- [x] Replace the inline per-line `discount_cents` input in [frontend/src/components/InvoiceEditor.jsx](../frontend/src/components/InvoiceEditor.jsx) and [frontend/src/components/QuoteEditor.jsx](../frontend/src/components/QuoteEditor.jsx) with:
  - Default state: no discount UI, the line just shows price.
  - "Apply discount" button on the line.
  - Click expands a percent slider (0-50, step 1) plus a live "$X off" preview.
  - Slider writes `discount_cents = round(qty * unit_price * percent / 100)` into the existing column.
  - Removing the discount or setting the slider to 0 clears the column and collapses the UI.
- [x] No backend changes. Existing column and column semantics stay.
- [x] Smoke tests verify the slider writes the expected `discount_cents` for representative inputs.

Deliverable: staff can give a per-line discount with a slider on selected items only, while every other line stays clean.

## Phase 3: PDF Discount Breakdown

Purpose: the printed quote and invoice show the customer exactly what they saved.

Tasks:

- [x] Refactor [templates/pdf/_lineitems.html](../templates/pdf/_lineitems.html) totals block into [templates/pdf/_totals.html](../templates/pdf/_totals.html) to render the seven-row layout in the "PDF Rendering" section above.
- [x] Conditionally render rows: line discount row only when any line has a discount, order discount rows only when non-zero order discounts apply, "You save" only when total savings > 0.
- [x] Apply to both [templates/pdf/quote.html](../templates/pdf/quote.html) and [templates/pdf/invoice.html](../templates/pdf/invoice.html).
- [x] Use snapshotted order-discount child rows for each order discount row.
- [x] No em dashes anywhere in the rendered copy.
- [x] Smoke tests render representative PDFs and assert the totals lines appear or do not appear correctly:
  - No discounts: only Subtotal, Tax, Total render.
  - Order discount only: subtotal, order discount, taxable subtotal, tax, total, "You save" all render.
  - Line discount only: line discount line renders, no order discount line.
  - Both: all rows render, "You save" sums both.

Deliverable: customer-facing PDFs show pre-discount price, savings, and post-discount total clearly.

## Phase 4: Quote Installments

Purpose: give quotes the same payment-schedule shape invoices already have, so the conversion can carry the customer's chosen plan forward.

Tasks:

- [x] Migration adds `quote_installments` table per the schema above.
- [x] Migration validates with real INSERTs.
- [x] SQLAlchemy model + relationships.
- [x] Quote service: read/write installments alongside line items, same write-path shape as invoice installments minus payment-state columns.
- [x] [api/routers/quotes.py](../api/routers/quotes.py) accepts `installments` on POST/PATCH and includes them on GET.
- [x] Quote editor gets a payment-schedule section. **Initial implementation mirrors the invoice editor's free-form rows** so the two surfaces are at parity before Phase 5 swaps them both at once. Free-form is intentional here: Phase 5 introduces the constrained selector for both at the same time.
- [x] Quote-to-invoice conversion in [services/quote_service.py](../services/quote_service.py): if the quote has installments, copy them into invoice installments preserving labels, amounts, and due dates. If the quote has none, fall through to the existing default 50/50 path.
- [x] Smoke tests:
  - Round-trip a quote with three installments.
  - Convert that quote to an invoice and verify all three rows copied verbatim.
  - Convert a quote with no installments and verify the legacy 50/50 default still appears.

Deliverable: quotes carry payment terms; conversion preserves them.

## Phase 5: Plan Selector

Purpose: replace free-form schedule editing on both editors with the constrained 1/2/3 selector and the deposit floor.

Tasks:

- [x] Replace the schedule editor in [frontend/src/components/InvoiceEditor.jsx](../frontend/src/components/InvoiceEditor.jsx) and the corresponding new section in QuoteEditor with:
  - Plan count dropdown: 1, 2, 3 (defaulted from BusinessProfile, fallback to 2).
  - Deposit percent input (50-100, defaulted from BusinessProfile, fallback to 50).
  - Auto-generated rows: amount column locked, date column editable.
  - Toggle "Custom amounts" reveals editable amount cells (escape hatch).
- [x] Date math implementation: deposit = issue + 14d, final = max(event - 60d, issue + 60d), middle (3-payment only) = midpoint. Use the issue date currently shown in the editor.
- [x] Server-side validation in both invoice and quote services:
  - Reject if plan count is not in {1, 2, 3}.
  - Reject if deposit (first installment) is below 50% of total.
  - Reject if installments do not sum to total.
  - "Custom amounts" mode skips the deposit-floor check only if explicitly flagged on the request; the deposit floor remains the default.
- [x] Smoke tests:
  - Auto-generated 2-payment plan on a $4,000 quote with event in 90 days produces $2,000 / $2,000 with the documented dates.
  - Auto-generated 3-payment plan splits the middle correctly.
  - 49% deposit submission rejected with 422.
  - Plan count of 4 rejected with 422.
  - Custom amounts toggle accepts uneven amounts when flagged.

Deliverable: staff pick a plan from a dropdown, the system fills in amounts and dates, the server enforces the floor.

## Phase 6: Quote PDF Schedule and End-to-End Hardening

Purpose: close the loop on customer rendering and prove the whole flow holds.

Tasks:

- [x] Render the payment schedule block in [templates/pdf/quote.html](../templates/pdf/quote.html), reusing the partial that drives the invoice schedule today.
- [x] Add a "Payment terms" section header on the quote PDF.
- [x] End-to-end smoke: build a quote with a per-line discount, an order-level preset discount, and a 3-payment plan; render PDF; verify discount breakdown and schedule both appear; convert to invoice; verify discount snapshot and installment rows copied; render invoice PDF; verify same totals and schedule.
- [x] Update [tests/test_quotes_smoke.py](../tests/test_quotes_smoke.py) and [tests/test_invoices_smoke.py](../tests/test_invoices_smoke.py) with the end-to-end fixture.
- [x] Verify the VPS systemd hardening allows the new write paths.

Deliverable: discount and payment-term flow works end to end on both surfaces.

## Phase 7: Stacked Order Discounts

Purpose: move from a single order-level discount snapshot to a stack of snapshot rows, while keeping the Phase 2a pre-tax math and preserving old records.

Tasks:

- [x] Migration 051 creates `invoice_order_discounts` and `quote_order_discounts` with parent FKs, `sort_order`, nullable `preset_id`, non-empty `label`, and 0-50 `percent` checks.
- [x] Migration 051 backfills existing single-discount snapshots into one child row, normalizes NULL/blank labels to `"Custom"`, validates with unique DML probe rows, and drops the old single-snapshot parent columns.
- [x] SQLAlchemy models add `InvoiceOrderDiscount` and `QuoteOrderDiscount`; `Invoice` and `Quote` no longer expose `discount_preset_id`, `discount_label`, or `discount_percent`.
- [x] Shared `discount_snapshot.snapshot_order_discounts` resolves preset/custom rows, enforces per-row and combined 50% caps, and preserves deleted-preset snapshots on update when the row already existed.
- [x] Invoice and quote services read the combined percent from child rows, rerate existing line items when only the stack changes, and recompute `discount_cents` as a derived display value.
- [x] Quote-to-invoice conversion copies order-discount rows verbatim before recomputing invoice totals.
- [x] Routers accept `order_discounts` arrays on POST/PATCH, return `order_discounts` on GET, and map `combined_discount_too_high` to 422.
- [x] PDF totals render one row per non-zero order discount and allocate rounding crumbs to the final row so rendered savings sum to `discount_cents` exactly.
- [x] Editors replace the single discount selector with `OrderDiscountsControl`, an add/remove row UI, custom percent support, inactive/deleted preset display, and a combined-percent indicator.
- [x] Smoke coverage includes additive math, preset + custom coexistence, combined-cap rejection, per-row Pydantic rejection, clearing the stack, deleted-preset snapshot preservation, quote conversion fidelity, and per-discount PDF rows.

Deliverable: staff can stack order-level discounts on quotes and invoices, PDFs show each discount separately, conversion preserves the exact stack, and existing records remain editable even if their source preset is later removed.

## Non-Goals

- Per-line discount as a percent-stored column. Storage stays absolute cents.
- Configurable deposit floor per quote/invoice. The 50% floor is store-wide.
- Plan counts above 3.
- Stripe integration for installment-driven invoicing.
- Backfilling old invoices to the new tax math. Legacy records keep legacy math.
- Public-portal customer self-service for picking a plan. Staff drive the selector; customers see the result.

## Acceptance Criteria

- [x] BusinessProfile shows a working Discounts section with the three seeded presets and a Payment plan defaults section.
- [x] Quote editor and invoice editor both expose stacked order discounts with seeded presets, no-discount empty state, and Custom % rows.
- [x] Per-line discount UI is opt-in; lines without a discount show no discount controls.
- [x] Order-level discount on a new record reduces the taxable base; tax is calculated on the post-discount subtotal.
- [x] An invoice or quote with no order-discount rows and `discount_cents > 0` continues to compute totals exactly as it did before this work.
- [x] Customer-facing quote and invoice PDFs show pre-discount subtotal, line discount line (when any), one row per order discount (when applied) with snapshotted label/percent, taxable subtotal, tax, total, and a "You save $X" line.
- [x] Quotes carry a payment schedule and the conversion to invoice preserves it.
- [x] Plan selector enforces 1/2/3 payments with a 50% deposit floor; auto-generated dates follow the documented anchoring rules.
- [x] No customer-facing copy added by this work contains em dashes.
- [x] Existing smoke tests still pass; new smoke tests cover the cases enumerated in each phase.
