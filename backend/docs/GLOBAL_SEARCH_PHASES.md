# Global Search - Phased Plan

A command-palette-style global search for Bellas XV that lets staff jump straight to any record (event, contact, invoice, quote, special order) by typing a fragment of a name, phone, theme, or document number. Opens from a header search input and from a `Cmd-K` / `Ctrl-K` shortcut anywhere in the admin app.

## Goal

Staff should be able to summon a search dialog from any page, type a few characters, and see live, ranked, type-grouped results across every record they care about. Picking a result navigates straight to that record. The search must keep feeling instant as the system fills with real history (thousands of contacts, hundreds of events per year, multi-year invoice ledger).

The important boundary is not "is this faster than scrolling the pipeline" - the pipeline filter answers that. The boundary this feature owns is "I want this specific record, and I do not care which page I am on right now." It is a navigation primitive, not a list view.

## Decisions To Lock

- **One unified backend endpoint, not per-entity search.** Command palettes need a single roundtrip per keystroke that returns mixed result types. Splitting into `/events/search`, `/contacts/search`, etc. forces the frontend to fan out and merge. The endpoint is `GET /api/search?q=&types=&limit=` (router declares `/search`, mounted with `prefix="/api/search"`).
- **Results are a discriminated union by `type`.** Each result row carries `{ type, id, label, sublabel, score }`. The `type` field is the seam that lets future entity types ship without changing the palette's render code.
- **Per-type cap, not pagination.** Up to ~8 results per type, ~30 total. A user who needs more results meant to be on a list page, not a palette. There is no "load more" inside the palette.
- **Postgres `pg_trgm` + GIN indexes, not full-text search.** Trigram indexes give the substring + fuzzy "as-you-type" match shape this UI needs ("Lor" → "Lorena"). FTS is built for documents and lexemes and handles short partial-token queries badly.
- **Accent and case insensitive matching via `unaccent(lower(...))` expression indexes.** Bellas customers are heavily Spanish-named (Hernández, María, Peña, Núñez). Plain `pg_trgm` will not reliably make `hernandez` match `Hernández`. Indexes are built on `unaccent(lower(col))`, queries normalize the user's input the same way before matching, and the `unaccent` extension is enabled in the same Phase 1 migration as `pg_trgm`. This is locked, not optional.
- **Tiered ranking, not a single similarity score.** Each entity query is a tiered union: exact match, prefix match, substring match, trigram-similar (`similarity > 0.3`). Tiebreaker inside a tier is `updated_at DESC`. This guarantees that someone typing the start of a name sees that name first, regardless of how long it is.
- **Phone and email are pre-processed at the API layer, not at the index.** If the query is digits-heavy, normalize to an E.164 fragment before matching `contacts.phone_e164`. If it contains `@`, search the local part of `contacts.email` separately. The trigram indexes still exist but the API decides which one to consult.
- **Minimum query length is 2 characters.** No fetch on a single character. The frontend short-circuits before hitting the network.
- **Debounce is ~150ms with AbortController cancellation.** React Query with `keepPreviousData: true` keeps the list from flickering between keystrokes. Stale requests are aborted, not just ignored.
- **Authorization runs through the existing admin auth dependency.** The endpoint is admin-only. There is no per-row scoping today because Bellas is single-tenant; the dependency exists to ride future tenancy or RBAC for free.
- **The palette is a single component mounted at the app root.** It lives inside `DashboardLayout.jsx` so every authed page has it. The header search input is a button that opens the dialog, not a text input that grows. Simpler state, no focus juggling.
- **The server owns navigation routes, not the frontend.** Each result row carries a server-computed `route` string. The frontend just calls `navigate(result.route)`. There is no frontend type-to-route dispatcher. Adding a new entity is one backend index + one query branch that emits a route. The palette UI never learns about new types. A backend URL-scheme change does not require a coordinated frontend ship.
- **Use restrained per-type row icons in v1.** Section headers alone are too subtle when an event and contact share nearly identical labels. The palette uses a leading event icon for `event` rows and a leading person icon for `contact` rows, while avoiding louder chips or per-section color blocks.
- **The search endpoint never reads `notes` or other internal free-text fields.** Even though the requester is staff, indexing arbitrary internal notes would put deleted-customer data and stale comments into the results stream. Searchable fields are an explicit allowlist per entity.
- **Customer-facing public codes (`BVX-NNNNN`, invoice numbers, quote numbers) are first-class search terms.** Staff routinely ask "what is BVX-00042" or "pull up invoice 1042." These get exact-match-first ranking on their respective tables.

## Locked Decisions (formerly open)

These were the three open questions in the first draft. They are now locked.

### 1. Event result destination

**Locked: navigate to `/events/:eventId`**, which the route tree in `frontend/src/App.jsx` already redirects to `/events/:eventId/overview`. Consistent destination regardless of which page the user was on, and matches the "open the record" mental model. The pipeline-local quick-view drawer stays for drag-drop context only and is not the global-search destination.

### 2. Contact result destination

**Locked: build a minimal contact detail page in Phase 3, and expand `GET /api/contacts/{id}` as part of that phase.** The current response shape (`api/routers/contacts.py`) returns `event_count`, `appointment_count`, and `alternate_celebrants` only. The contact detail page needs `address` and a real linked-events list. Phase 3 must ship that contract change. Specifics under "Phase 3" below.

### 3. Phase 1 entity scope

**Locked: Option A, events + contacts only.** Invoices, quotes, and special orders have a known number-based exact-lookup shape that is straightforward to add in Phase 4 once the unified endpoint and palette are proven. Phase 1 stays focused on the harder shape: fuzzy name search across events and contacts.

## Endpoint Shape

Locked v1 contract:

```
GET /api/search?q=<query>&types=<csv>&limit=<int>
```

The FastAPI router declares the path as `/search`. It is mounted in `api/server.py` with `prefix="/api/search"`, matching the existing pattern (`/api/events`, `/api/contacts`, etc.). Frontend calls go through the configured API base, so the call site is just `/search` relative to that base.

- `q` (required, min length 2): the user's typed query, untrimmed substring matched against the searchable fields below.
- `types` (optional, default = all enabled types): comma-separated list of `event`, `contact`, `invoice`, `quote`. The endpoint returns 400 on unknown types, not silent ignore. `special_order` is intentionally not enabled until a staff special-orders UI exists.
- `limit` (optional, default 8, max 20): per-type cap. Total returned is at most `limit * types.length`.

Response:

```json
{
  "query": "lor",
  "results": [
    {
      "type": "event",
      "id": 42,
      "label": "Lorena Hernández - Quince",
      "sublabel": "Sold · Aug 15 2026 · Floral theme",
      "score": 3.91,
      "route": "/events/42"
    },
    {
      "type": "contact",
      "id": 17,
      "label": "Lorena Hernández",
      "sublabel": "(956) 555-0142 · lor@example.com",
      "score": 3.88,
      "route": "/contacts/17"
    }
  ]
}
```

- `score` is `tier_number + similarity` so the frontend can do a stable secondary sort across types if it ever needs to interleave (it does not in v1, but the field is reserved).
- `route` is computed server-side and is the single source of truth for navigation. The frontend just calls `navigate(result.route)`. There is no client-side type-to-route map.
- `label` and `sublabel` are pre-formatted display strings. The frontend does not assemble them.

## Searchable Fields

Per-entity allowlists. Anything not on this list is intentionally not searchable.

| Type | Fields |
|---|---|
| `event` | `event_name`, `quince_theme` |
| `contact` | `display_name`, `email`, `phone_e164` (digits-only match), `first_name`, `last_name` |
| `invoice` | `invoice_number`, associated event/contact name (joined) |
| `quote` | `quote_number`, associated event/contact name (joined) |
| `special_order` | deferred: order number / public code, associated event/contact name (joined) |

`notes`, `tags`, `address`, internal SKUs, and free-text staff fields are explicitly not indexed. If staff want to search those, that is a different feature with different constraints (and likely a different rationale check).

## Indexes

Phase 1 ships event/contact indexes. Phase 4 adds invoice/quote document-number indexes and the supporting special-order indexes, while leaving the special-order result type disabled until its UI exists.

All indexes are built on `unaccent(lower(col))` so accent and case folding happen at index time. Queries normalize the user's input the same way before matching. `unaccent` is `IMMUTABLE`-marked in this migration so it can be used in expression indexes; if it is not already, the migration creates an `IMMUTABLE` wrapper function (`f_unaccent(text)`) and the indexes use that wrapper.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Wrapper required because the bundled unaccent() is STABLE, not IMMUTABLE,
-- and expression indexes require IMMUTABLE.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  AS $$ SELECT public.unaccent('public.unaccent', $1) $$
  LANGUAGE SQL IMMUTABLE PARALLEL SAFE;

CREATE INDEX contacts_display_name_trgm
  ON contacts USING gin (f_unaccent(lower(display_name)) gin_trgm_ops);
CREATE INDEX contacts_email_trgm
  ON contacts USING gin (f_unaccent(lower(email))        gin_trgm_ops);
CREATE INDEX contacts_phone_e164_trgm
  ON contacts USING gin (phone_e164                      gin_trgm_ops);  -- digits, no unaccent needed
CREATE INDEX events_event_name_trgm
  ON events   USING gin (f_unaccent(lower(event_name))   gin_trgm_ops);
CREATE INDEX events_quince_theme_trgm
  ON events   USING gin (f_unaccent(lower(quince_theme)) gin_trgm_ops);
```

Query path uses `f_unaccent(lower(q))` against the same expression; that is the only way the planner will use the expression index. If a query forgets the wrapper, the index will be silently skipped and the search will sequential-scan in production. The Phase 1 service layer must centralize the normalize-then-match call so this cannot drift.

`first_name` and `last_name` reuse the `display_name` trigram in v1 because `display_name` is required and the API can match against it directly. If split-name search becomes a real user need, add the two indexes in a later phase.

## Phases

### Phase 1 - Backend foundation (events + contacts)

- Migration via the repo's hand-rolled runner (`database/migrations/`, numbered sequentially): enable `pg_trgm` and `unaccent`, create the `f_unaccent` IMMUTABLE wrapper, create the five expression GIN indexes above. **Shipped as `045_search_trigram_indexes.py`.**
- New router: `api/routers/search.py`, single endpoint `GET /search`. Mounted in `api/server.py` with `prefix="/api/search"`.
- New service: `services/search_service.py`. Per-type query functions return ranked rows. The router composes them in parallel (or sequential, depending on session/transaction shape) and returns the union. The service exposes a single `_normalize(q)` helper used by both the index expressions and the runtime query so they cannot drift.
- Each result row's `route` is computed in the service: events emit `/events/:eventId`, contacts emit `/contacts/:contactId`. Frontend never assembles these.
- Tiered ranking implemented as a `UNION ALL` per entity, with each branch tagged with its tier number, and an outer `ORDER BY tier ASC, updated_at DESC LIMIT n`.
- Phone preprocessing: if `q` matches `^[\d\s\-\(\)\+]+$`, strip to digits and search `phone_e164` only (no unaccent on this branch).
- Email preprocessing: if `q` contains `@`, split on `@` and search `email` against the local part separately, full string against full email.
- Tests use real INSERTs against contacts and events with realistic names ("Lorena Hernández", "María José Vargas"), then assert that `hernandez` matches `Hernández`, `maria` matches `María`, partial phones land in the right tier, and theme strings rank as documented. Includes an `EXPLAIN`-based assertion that the query plan uses the trigram index, not a sequential scan, for queries above the min length.

### Phase 2 - Palette UI

- New component `frontend/src/components/CommandPalette.jsx`.
- Mounted in `DashboardLayout.jsx`. Global keydown listener for `Cmd-K` / `Ctrl-K` toggles open. `Esc` closes. `/` opens (only when no input is focused).
- Header search button in the dashboard layout: a styled-as-input button that opens the palette on click. No real text input in the header.
- React Query hook `useSearch(query)` with 150ms debounce, `keepPreviousData: true`, AbortController on supersession, gated by `query.length >= 2`.
- Results list is grouped by type with section headers. Keyboard nav: `↑` / `↓` move selection across all results regardless of group. `Enter` activates. `Tab` jumps to the next group's first item.
- Rows include restrained leading icons by known type: event icon for events, person icon for contacts. Unknown future types still render without an icon until the frontend is updated.
- Picking a result closes the palette and calls `navigate(result.route)`. No client-side type-to-route map exists. Phase 2 wires this directly because the server already returns `route`; there is nothing extra for Phase 3 to add on the routing layer.
- Empty state copy: under 2 chars, show a hint. Zero results, show "No matches." Loading, show a small spinner inline at the top of the result list, never a full-screen blocker.

### Phase 3 - Contact detail page and API contract expansion

Routing is already done in Phase 2 because `result.route` is server-computed. This phase is about giving the `contact` route somewhere real to land.

**API contract change to `GET /api/contacts/{id}`.** The current response (`api/routers/contacts.py`) returns `event_count`, `appointment_count`, and `alternate_celebrants`. It does not return `address` or a list of linked events. Phase 3 expands the response to:

```python
class ContactResponse(BaseModel):
    # ... existing fields stay ...
    address: dict                          # new — already on the model
    linked_events: list[LinkedEventSummary]  # new

class LinkedEventSummary(BaseModel):
    id: int
    event_name: str
    event_type: str
    status: str
    event_date: date | None
    route: str   # server-computed, e.g. "/events/42"
```

`linked_events` is sourced from `events.primary_contact_id = :contact_id`, ordered `event_date DESC NULLS LAST, created_at DESC`. The existing `event_count` field stays for backwards compatibility but `linked_events.length` is the same number; clients should prefer the list.

**New page `frontend/src/pages/ContactDetail.jsx`.** Route added to `frontend/src/App.jsx`: `<Route path="contacts/:contactId" element={<ContactDetail />} />`. Shows display name, contact methods, address (if present), tags, and the linked events list with status chips. Each linked-event row is a navigation target via its server-supplied `route`. Read-only in this phase. Edit lives behind the existing `ContactEditDialog`.

**Validation.** Browser-verify on the VPS after rebuild: open palette from the pipeline page, search "Lor", pick a contact, confirm the detail page renders with address and linked events, click a linked event and land on its overview tab, back button returns to the contact page.

### Phase 4 - Extend to invoices and quotes

- Add per-type service branches in `search_service.py`. Number-based types lean exact-match-first; the join to `events.event_name` / `contacts.display_name` is what makes "Hernández invoice" work.
- Add the missing indexes. Note that `special_orders` does not have a `public_code` column — that lives on `catalog_items`. Phase 4 creates the special-order supporting indexes now, but does not expose `special_order` as an `ALLOWED_TYPES` member until a staff UI exists. Search results must route to a page that can render the selected object; today there is no React special-orders tab or detail page.

```sql
CREATE INDEX invoices_number_trgm
  ON invoices USING gin (lower(invoice_number) gin_trgm_ops);
CREATE INDEX quotes_number_trgm
  ON quotes   USING gin (lower(quote_number)   gin_trgm_ops);
CREATE INDEX special_orders_vendor_order_number_trgm
  ON special_orders USING gin (lower(vendor_order_number) gin_trgm_ops);
-- catalog_items.public_code is already UNIQUE-indexed for equality lookups
-- (see migration 041). For trigram search of public codes, add:
CREATE INDEX catalog_items_public_code_trgm
  ON catalog_items USING gin (lower(public_code) gin_trgm_ops);
```

Document numbers (invoice, quote, vendor order) and `BVX-NNNNN` codes are case-insensitive but accent-free by construction, so the `f_unaccent` wrapper is not required on these branches. Use plain `lower()` instead.

- Each enabled branch emits its own server-computed `route`. Invoices route to `/events/:eventId/invoices`; quotes route to `/events/:eventId/quotes`. The frontend palette does not assemble these routes.
- Deferred special-order branch: when a staff special-orders UI exists, add `special_order` to `ALLOWED_TYPES`; match against `special_orders.vendor_order_number` and the joined `catalog_items.public_code`, plus `events.event_name` / `contacts.display_name` for "Hernández dress order"-style queries; route to that new UI.

Verify column names against the live schema before writing the migration. As of this doc, `invoices.invoice_number` and `quotes.quote_number` exist (`database/models.py`) and `special_orders` has `vendor_order_number` and `catalog_item_id` (`database/migrations/043_create_special_orders.py`).

### Phase 5 - Hardening

- Rate limit the endpoint per session: typing fast should not hammer the DB harder than the indexes can serve. Cap to a few requests per second per user, with the latest query winning.
- Telemetry: log query, result count per type, and chosen-result-type to the existing activity log infrastructure. This data tells us which types are actually getting picked and whether ranking is wrong.
- Empty-query analytics: when a user types and picks nothing, that is a search miss. Capture it to drive future ranking work.
- Optional: cache the most recent ~10 picks per user in `localStorage` and surface them as a "Recent" section when the palette opens with an empty query. Disabled by default until usage telemetry says it is worth the complexity.

## Non-Goals

- **Saved searches, advanced filters, boolean operators.** This is a navigation tool, not a query builder.
- **Search across attachments, PDFs, document content.** Out of scope. If invoice PDF search becomes a real need, that is a separate feature with separate infrastructure.
- **Search across deleted/archived rows.** v1 only returns active rows. Archived event search is a list-page concern.
- **Multi-tenant scoping or per-user permissions.** Bellas is single-tenant admin. The endpoint goes through admin auth and that is sufficient until tenancy lands.
- **Replacing the pipeline filter.** The pipeline filter (separate, smaller feature) and global search coexist. They answer different questions.

## Validation Gates Between Phases

- **End of Phase 1:** real INSERTs against contacts and events, then concrete `curl` queries against the deployed endpoint that demonstrate prefix, substring, and fuzzy matches in the right tier order. No phase-2 work starts until ranking looks right on real data.
- **End of Phase 2:** browser-verify the palette opens, debounces, navigates the result list with the keyboard, closes on `Esc` and on result pick, and that picking an event navigates to `/events/:eventId`. Picking a contact may 404 in this phase because the contact detail page does not exist yet; that is expected and Phase 3 fixes it.
- **End of Phase 3:** browser-verify the full path: pipeline page → `Cmd-K` → type "lor" → pick contact → land on contact detail page → back button returns to pipeline.
- **End of Phase 4:** verify invoice and quote results appear, rank sensibly against the others, and route to the correct existing event tabs. Confirm special-order supporting indexes exist, while `special_order` remains disabled until its staff UI ships.
- **End of Phase 5:** confirm telemetry data is flowing into activity log and is queryable.
