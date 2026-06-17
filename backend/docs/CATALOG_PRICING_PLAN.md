# Catalog Pricing Plan

How catalog dresses get a sales-floor price, where the logic lives, and
what is intentionally deferred. The goal is that a sales rep just sees the
dress and a price — no multiplier math, ever.

## Source of truth

Retail price is **computed from wholesale cost**, not from Morilee's MSRP
(MSRP is stored only as a reference). Wholesale is the single input and the
only number that changes month to month.

- Wholesale comes from the **Morilee New York Consolidated Price List**
  (.xlsx in Google Drive, maintained by store staff).
- Each workbook **tab** is updated in place at its own cadence and carries
  its own as-of date in the tab name (e.g. `Quince as of 6126` =
  2026-06-01). The **filename date is not reliable** for currency — we read
  the tab name.

## Pricing rules (authoritative)

Shelf price (full package = standard crown/tiara + petticoat + steam +
garment bag) = `wholesale * multiplier`:

| Wholesale band | Multiplier |
| --- | --- |
| $299–399 | ×4.0 |
| $400–599 | ×3.5 |
| $600–799 | ×3.25 |
| $800 and up | ×3.0 (no ceiling; over $999 stays ×3.0) |
| below $299 | no rule — flagged for manual pricing |

Quote-time adjustments (per sale, **never** stored on the catalog row):

- Dress Only (no package): multiplier −0.25.
- Remove crown/tiara −$100 · remove petticoat −$100 · remove steam −$50.
- Discount ≤5% = rep discretion; **>5% requires manager authorization, no
  exceptions** (surfaced as a flag; the price is still computed).

## Rounding style — CURRENT: exact cents (revisit if owner wants)

The shelf price is stored as the **exact** computed value rounded to the
cent. The ×3.0 band yields clean dollars ($999 → $2,997.00), but ×3.25 /
×3.5 produce fractional cents ($699 × 3.25 = **$2,271.75**; $649 × 3.25 =
**$2,109.25**).

This was a deliberate choice (2025-06 build): keep the math faithful to the
written rules rather than impose a presentation rule. **If the owner later
prefers tidier shelf prices**, add a single rounding step in
`services/pricing.py` (e.g. round up to the nearest whole dollar, or to
`.99`). Because all pricing flows through that one module and the next
price-list import recomputes every row, changing the rounding rule and
re-running `apply_price_list.py --apply` will restate every catalog price
consistently — no per-row edits. Old quotes/invoices are unaffected (they
snapshot their own price; see below).

## Architecture

- **`services/pricing.py`** — the only place the math lives.
  `shelf_price_cents()` powers the catalog card; `calculate_dress_price()`
  powers quote-time selections. All money in integer cents.
- **`catalog_items`** (migration 084) stores the inputs/provenance behind
  the price: `wholesale_cents`, `wholesale_as_of` (parsed from the tab
  name), `wholesale_source`. Computed retail lands in the existing
  `unit_price_cents`.
- **History is safe on reimport**: quote/invoice line rows snapshot their
  own `unit_price_cents`, so re-running an import updates the catalog
  card price for new sales without ever mutating past documents.

## Ingestion

- **Now (done)**: backfill via
  `scripts/seed_catalog/apply_price_list.py`. Dry-run by default (prints
  old→new price, wholesale, multiplier, as-of, MSRP reference, and writes a
  JSON report); `--apply` commits. First run applied the
  `Quince as of 6126` tab to all 459 Morilee quince rows.
- **Monthly**: re-run the script against the latest workbook; review the
  dry-run diff, then `--apply`. The report is the audit artifact.

## Deferred (not built yet)

- **Admin "upload price list" screen** — let staff drop the new .xlsx and
  get the same dry-run review + apply in the browser, no engineer in the
  loop. Deferred until the pricing model is confirmed correct on real
  selling. Same `services/pricing.py` and report logic power it.
- **Other tabs / designers** — only the Quince dress tab is wired in. The
  workbook also has Bridal, Julietta, Grace, Bridesmaids, MLNY, Party, and
  accessories tabs (each with its own as-of date and cadence). Extend
  `apply_price_list.py` (it already takes `--tab` and `--designer`) when
  those catalogs are scraped and ready.
- **32 unscraped F26 quince styles** exist in the price list but have no
  catalog rows yet (no images/descriptions). They'll price automatically
  once a scraper run adds them.
- **Sub-$299 manual pricing UX** — none today (no quince dress is below the
  floor). If a future tab has them, they're reported as
  `needs_manual_pricing` and skipped; a manager would set those by hand.
