# CRM Record Deletion Plan

Created: 2026-06-02

This tracker keeps the CRM deletion work focused. Use it before each coding
session to decide what phase is active, what is deliberately out of scope, and
which product/schema decisions are still open.

## Current Direction

Use one deletion state for selected CRM-core records:

- `deleted_at IS NULL` means active.
- `deleted_at IS NOT NULL` means archived / in Recycle Bin.
- Rows older than the retention window may be hidden from the Recycle Bin later.
- Hard purge is deferred until retention rules are explicitly chosen.

The user-facing word can be "Archive" or "Move to Recycle Bin", but the schema
should stay one timestamp.

## Target Scope

Initial soft-delete targets:

- `contacts`
- `events`
- `event_participants`
- `special_orders`

Explicitly excluded for now:

- `appointments`: already uses lifecycle statuses such as cancelled / no-show.
- `catalog_items`: already uses `is_active` for deactivation.
- Financial rows: invoices, quotes, payments, invitations, documents keep their
  existing Tier 1 policy and service helpers.

## Core Decisions

| Decision | Current call | Why |
|---|---|---|
| One state vs archive + trash | One state: `deleted_at` | Avoids duplicated state machines and simpler read filters. |
| Audit storage | `activity_log` | Existing system already records domain events. Do not add reason/note columns to entity tables. |
| First build target | Dependency preview service | The confirmation modal is the highest-leverage CRM behavior. |
| Purge | Deferred | Needs retention/legal decision and should not block archive/restore. |
| Appointments | Excluded | Status fields already hide non-active appointment states. |
| Catalog items | Excluded | `is_active` already expresses the intended UX. |

## Decision Gates

Do not move past each gate until it is answered and written here.

### Gate 1: Activity Log Scope For Contacts

Problem: `activity_log.event_id` is not nullable, but a contact is not always
owned by exactly one event.

Options:

- Log contact archive/restore against the most relevant event when one exists.
- Add a contact-scoped audit table or extend `activity_log` to allow nullable
  `event_id`.
- Refuse contact archive unless the contact has an event context.

Current preference: unresolved. Decide during D1 after inspecting actual contact
flows and activity_log constraints.

Decision:

- **Resolved 2026-06-02 (D3).** Option (a) with a documented fallback.
  - **Anchor:** when the contact has at least one event (live OR
    deleted), the archive/restore activity row anchors to the most
    recently created of those events (`events.created_at DESC`).
    Picking by `created_at` rather than `event_date` keeps the choice
    deterministic for events that have no date set.
  - **Fallback:** when the contact has zero events ever, skip the
    `activity_log` write. The contact row's own `deleted_at` /
    `created_at` is the durable audit trail for a contact that never
    had an event timeline in the first place. D3's
    `archive_contact` / `restore_contact` helpers log a structured
    warning in this case so an operator grepping journals can spot
    audit-less archives if they ever need to.
  - **Why not (b):** loosening `activity_log.event_id` to nullable
    would require dropping the `events.id CASCADE` FK and rewriting
    every reader. The schema cost vastly exceeds the audit gap, which
    only opens for orphan contacts (test / duplicate rows).
  - **Why not (c):** refusing archive on event-less contacts blocks
    the most common archive case (test rows, duplicates created by
    mistake). Hurts the feature for no real audit gain.
  - **Activity types used:** `contact.archived` and `contact.restored`
    in `services/activity_log.py`. Payload includes
    `{anchor_event_id, dependency_snapshot, reason, note?}`.

### Gate 2: `event_participants.contact_id` FK Drift

Problem: the migration reportedly uses `ON DELETE SET NULL`, while the
SQLAlchemy model may describe different behavior.

Required before D2:

- Confirm actual production DB constraint.
- Align model and migration documentation.
- Update dependency preview logic to match real behavior.

Decision:

- **Resolved 2026-06-02 (D1).** No drift. Migration 015 originally
  created the FK as `ON DELETE SET NULL`, then migration 055
  (`055_event_participants_contact_required.py`) dropped and re-added
  it as `NOT NULL, ON DELETE RESTRICT` and added a DML probe that
  verifies the RESTRICT behavior. The SQLAlchemy model
  (`database/models.py` `EventParticipant.contact_id`) already declares
  `ondelete="RESTRICT", nullable=False`. Model and DB agree.
- **Implication for dependency reports**: under soft-delete (D2+),
  archiving a contact does not fire the FK because the row physically
  remains. So `event_participants.contact_id` being RESTRICT does NOT
  by itself block contact archive — the block is a product rule
  ("contact has active participants"), implemented in
  `services/record_dependencies.py`, not an FK rule.

### Inbound FK Inventory (D1 prereq)

Captured from `database/migrations/` — authoritative for what the live
DB enforces.

To `contacts.id`:

| Source | Column | On Delete | Notes |
|---|---|---|---|
| `events` | `primary_contact_id` | RESTRICT | NOT NULL |
| `event_participants` | `contact_id` | RESTRICT | NOT NULL after 055 |
| `appointments` | `contact_id` | SET NULL | |
| `invoices` | `contact_id` | RESTRICT | NOT NULL |
| `quotes` | `contact_id` | RESTRICT | NOT NULL |
| `payments` | `contact_id` | RESTRICT | NOT NULL |
| `invoice_invitations` | `contact_id` | CASCADE | |
| `quote_invitations` | `contact_id` | CASCADE | |

To `events.id`:

| Source | Column | On Delete | Notes |
|---|---|---|---|
| `event_participants` | `event_id` | CASCADE | |
| `event_status_change_events` | `event_id` | CASCADE | |
| `appointments` | `crm_event_id` | SET NULL | |
| `event_documents` | `event_id` | CASCADE | |
| `invoices` | `event_id` | RESTRICT | NOT NULL |
| `quotes` | `event_id` | RESTRICT | NOT NULL |
| `activity_log` | `event_id` | CASCADE | NOT NULL |
| `special_orders` | `event_id` | RESTRICT | NOT NULL |

To `event_participants.id`:

| Source | Column | On Delete | Notes |
|---|---|---|---|
| `invoices` | `event_participant_id` | SET NULL | added in 079 |
| `quotes` | `event_participant_id` | SET NULL | added in 079 |

To `special_orders.id`: none (leaf table).

### Gate 3: Portal Reads For Archived Contacts / Events

Problem: signed portal links may need to keep rendering old financial artifacts
even if the CRM contact or event is archived.

Current preference:

- Customer-facing financial reads should keep working for non-deleted invoices /
  quotes / payments.
- Admin CRM list views should hide archived contacts/events by default.

Decision:

- **Resolved 2026-06-02 (D2).** Portal joins ignore the soft-delete
  state on `contacts` and `events`. Signed portal links resolve a
  specific financial artifact (invoice / quote / payment / event
  document) — the contact and event are identity context for that
  artifact, not list-filter targets. If a customer received an
  invitation while their contact row was live, the portal must keep
  rendering even after staff archive the contact.
- **Where this applies (audit during D2-B):** `services/portal_*.py`,
  `services/invoice_pdf.py`, customer-facing routes under
  `api/routers/portal.py` and `event_documents.py` portal-key paths.
  These read `Contact` / `Event` for snapshot data only and do NOT
  filter on `deleted_at`.
- **Where the default filter applies:** every admin/staff read path on
  `Contact`, `Event`, `EventParticipant`, `SpecialOrder` filters
  `deleted_at IS NULL` by default. An explicit `include_deleted` flag
  is added only on helpers the Recycle Bin (D3) needs.
- **Sales floor reads:** treated as admin-side for filter purposes —
  archived CRM rows hide from sales views by default.

### Gate 4: Retention Window

Problem: Recycle Bin can show all archived rows forever, or only recent ones.

Current preference:

- Do not implement purge in D1-D3.
- If a window is needed for display, use 30 days as the UI label only.

Decision:

- Pending.

## Phase Tracker

### Next Slice

Recommended next step: finish D1 with the reusable React dependency confirm
modal.

Why now:

- The backend dependency endpoint is already live and read-only.
- The modal can be built without enabling archive/delete actions.
- D2 schema work should wait until the UI can clearly explain dependencies and
  blocking reasons.
- Browser verification is required after rebuilding the admin SPA.

Do not start D2 until:

- The modal renders dependency reports for all four entity types.
- Unsupported/missing entity responses have a clear UI state.
- D1 acceptance is updated below.

### D1: Dependency Service And Confirm Modal

Status: Complete (2026-06-02; modal browser-validated as part of D3-D1a on 2026-06-03)

Goal: build the dependency preview foundation without enabling deletion.

Backend tasks:

- [x] Add `services/record_dependencies.py`.
- [x] Define `DependencyCount` and `DependencyReport`.
- [x] Implement `get_record_dependencies(db, entity_type, entity_id)`.
- [x] Cover `contact`, `event`, `event_participant`, and `special_order`.
- [x] Add `GET /api/admin/dependencies/{entity_type}/{entity_id}`.
- [x] Enforce admin/staff auth consistently with existing admin routes
  (uses `require_admin_scope`).

Frontend tasks:

- [x] Add reusable dependency confirm modal component
  (`frontend/src/components/RecordDependenciesDialog.jsx`).
- [x] Show dependency counts, sample labels, and blocking reasons.
- [x] Disable confirm when `can_archive` or `can_restore` is false
  (and render no confirm button at all when the caller passes no
  `confirmLabel` — D1's read-only default).
- [x] Do not wire destructive archive actions yet (no page imports the
  component yet; D3 wires the action buttons on the existing
  ContactDetail / event Overview pages).

Tests:

- [x] Unit tests for zero dependencies (contact with no events/invoices).
- [x] Unit tests for mixed active/deleted dependencies (covered on
  invoices, which already carry `deleted_at`; target tables gain the
  column in D2 and tests will extend then).
- [x] Unit tests for blocking financial dependencies (active draft
  invoice on contact / event blocks archive; clears after soft-delete).
- [x] Smoke test for the dependency endpoint response shape, including
  400 on unsupported `entity_type`, 404 on missing id, and 401/403
  without admin auth.

Acceptance:

- [x] Dependency reports are correct enough to power a modal — verified
  by `tests/test_record_dependencies_smoke.py` and by the live D3-D1a/D1b/D2 dialogs.
- [x] No schema changes (D1 ships only `services/record_dependencies.py`
  + `api/routers/admin_dependencies.py` + the React component).
- [x] No new archive/delete behavior exposed to admins at D1 ship
  time; archive/restore actions land in D3.

### D2: Soft-Delete Schema And Read Filters

Status: Complete (2026-06-02)

Goal: reclassify selected CRM-core tables from append-only to soft-delete.

Schema tasks:

- [x] Add `deleted_at TIMESTAMPTZ` to `contacts`.
- [x] Add `deleted_at TIMESTAMPTZ` to `events`.
- [x] Add `deleted_at TIMESTAMPTZ` to `event_participants`.
- [x] Add `deleted_at TIMESTAMPTZ` to `special_orders`.
- [x] Add supporting deleted-row indexes (`idx_<table>_deleted_at`
  partial on `WHERE deleted_at IS NOT NULL`).
- [x] Recreate `uq_contacts_phone_e164` as partial unique with
  `deleted_at IS NULL`.
- [x] Recreate `uq_event_participants_quinceanera_per_event` as partial unique
  with `deleted_at IS NULL`.
- [x] Confirmed no other unique constraints overlap deletion on the
  four target tables (verified during the migration audit).

Model and read-filter tasks:

- [x] Add model columns in `database/models.py`.
- [x] Patch contact read paths to filter `deleted_at IS NULL` by default
  (`services/contact_service.py` `_lookup_contact`, `update_contact`,
  raw phone-collision SQL, `get_contact_context` event count,
  `get_linked_events`; `api/routers/contacts.py` `get_contact`).
- [x] Patch event read paths
  (`services/event_service.py` `promote_appointment_to_event`,
  `create_walk_in_event`, `change_event_status`, `get_board_data`;
  `api/routers/events.py` `get_event`, activity-log 404 guard;
  `services/sales_appointments.py` event detail + action verb;
  `services/sales_assignment.py` reassign + cascade preview;
  `services/notification_routing.py` lead-owner targeting;
  `services/dashboard.py` pipeline status counts;
  `api/routers/event_documents.py` upload/counts/list 404 guards;
  `api/routers/admin_booking.py` appointment context).
- [x] Patch participant read paths (`services/event_participants.py`
  parent-event guard; `services/buyer_journey.py` three lookups;
  `api/routers/events.py` participant list).
- [x] Patch special-order read paths (`services/special_order_service.py`
  `_get_or_raise`, `create_special_order` event guard,
  `list_for_event`).
- [x] Audited portal/financial reads. Gate 3 resolution: portal joins
  on `Contact` / `Event` stay unfiltered so signed customer links keep
  rendering past staff archive. Implementation lives in
  `services/portal_*.py` / `services/invoice_pdf.py`, none of which
  changed in this audit (they don't filter `deleted_at` today, by
  design).
- [x] No explicit `include_deleted` flag needed in D2 — the only call
  site that needs to see archived rows is the Recycle Bin (D3) and
  the dependency report (which already reports deleted counts via
  `services/record_dependencies.py`'s direct query).

Policy tasks:

- [x] Update `docs/DATA_RETENTION_AND_DELETE_POLICY.md` (Tier 1 table
  + portal-exemption note + Tier 2 reclassification history).
- [x] Move target tables from Tier 2 to Tier 1-style soft-delete policy.
- [x] Update `tests/test_delete_policy_guardrail_smoke.py` (the four
  models / four tables now live in `TIER1_MODELS` / `TIER1_TABLES`).
  Hard-delete remains forbidden — FORBIDDEN_ORM_TARGETS still equals
  the union, so the guardrail keeps blocking accidental `db.delete()`.
- [x] Keep hard-delete forbidden for the four target models/tables.

Tests:

- [x] Migration applies cleanly (`venv/bin/python -m
  database.migrations.runner` recorded 080 in `schema_migrations`).
- [x] Partial unique indexes allow reuse only when old row is deleted
  (covered by the migration DML probes and
  `tests/test_soft_delete_read_filters_smoke.py::check_phone_reuse_after_archive`).
- [x] Soft-deleted records disappear from default list/get endpoints
  (covered by `tests/test_soft_delete_read_filters_smoke.py` —
  contact/event/special-order all flip from 200 to 404 once
  `deleted_at` lands; archived events leave the pipeline board;
  archived events block `change_event_status`).
- [x] Existing D1 smoke still green; delete-policy guardrail still green.

Acceptance:

- [x] Schema supports soft-delete.
- [x] Admin-visible behavior is unchanged except hidden deleted rows if
  manually seeded (verified by the D2 smoke seeding + 404 / empty
  responses against the existing admin endpoints).
- [x] No hard-delete path exists for target CRM tables (guardrail
  unchanged in coverage; reclassification is categorical).

### D3: Archive / Restore UX And Recycle Bin

Status: Complete (2026-06-03; D3-D1a/D1b browser-validated by Luis, D3-D2/D3-D3 pending browser walk-through)

Goal: expose safe admin archive and restore flows.

Service tasks:

- [x] Add `archive_contact` and `restore_contact` (`services/contact_service.py`).
- [x] Add `archive_event` and `restore_event` (`services/event_service.py`).
- [x] Add `archive_event_participant` and `restore_event_participant`
  (`services/event_participants.py`). Renamed from the original
  `remove_event_participant` because the read-side already uses
  `status='removed'` for participant lifecycle; `archive_*` makes the
  Recycle-Bin verb pair symmetric across all four entities.
- [x] Add `archive_special_order` and `restore_special_order`
  (`services/special_order_service.py`).
- [x] Each archive helper calls `record_dependencies.get_record_dependencies`
  and refuses on `can_archive=False`.
- [x] Each archive helper writes an `activity_log` entry. Contact-side
  rows anchor to the contact's most recently created event (live or
  deleted); event-less contacts skip the activity row and log a
  warning (Gate 1 decision).
- [x] Each restore helper refuses restore when required parent rows
  are still archived (event for participant/special_order, contact
  for event). Contact restore additionally refuses when the
  partial-unique on `phone_e164` would collide with a live row.

API tasks:

- [x] Add admin archive/restore endpoints for contacts
  (`POST /api/admin/contacts/{contact_id}/archive` + `/restore`).
- [x] Add admin archive/restore endpoints for events
  (`POST /api/admin/events/{event_id}/archive` + `/restore`).
- [x] Add admin archive/restore endpoints for event participants
  (`POST /api/admin/events/{event_id}/participants/{pid}/archive` +
  `/restore`). Nested route verifies the path's `event_id` matches
  the row's `event_id` to defend against URL substitution.
- [x] Add admin archive/restore endpoints for special orders
  (`POST /api/admin/events/{event_id}/special-orders/{soid}/archive`
  + `/restore`). Same parent-id guard.
- [x] Add `GET /admin/recycle-bin?entity_type=…&page_size=&before_id=&since=&until=&deleted_by_user_id=`
  (`api/routers/admin_archive.py`). Keyset pagination via `before_id`;
  optional `since` / `until` clamps `deleted_at`; `deleted_by_user_id`
  filters by actor on the archive activity row (orphan-contact
  archives have no activity row to filter by, documented in the
  docstring). Returns `display_name` + `secondary_label` per item so
  the Recycle Bin UI doesn't round-trip for detail.
- [x] Use request body `{ reason, note? }` for archive. Restore takes
  no body.

Frontend tasks (sliced as D3-D1a / D3-D1b / D3-D2 / D3-D3):

- [x] D3-D1a — Contact pilot: archive button on
  `frontend/src/pages/ContactDetail.jsx`, wired through the existing
  `RecordDependenciesDialog`. Dialog now collects reason + optional
  note in archive mode, surfaces server-side errors without closing,
  and disables the confirm button while the mutation is in flight.
  Success bounces the user back via `navigate(-1)` so the archived
  contact's 404 page never greets them.
- [x] D3-D1b — Event surfaces (partial). Archive button on the event
  header in `frontend/src/pages/event/EventDetailLayout.jsx` (success
  bounces to `/pipeline`). Per-row archive `IconButton` in the
  Participants section of `frontend/src/pages/event/tabs/Overview.jsx`
  (success invalidates the `['event', id]` query so the archived
  participant disappears from the list). Reused the same
  `RecordDependenciesDialog` + reason picker. Special-order archive
  is **deferred**: no admin SPA surface lists special orders today,
  so there is no row to attach an icon button to. Special-order
  archive becomes reachable once D3-D2's Recycle Bin lands and once
  any future event-detail tab surfaces the special-orders list.
- [x] D3-D2 — Recycle Bin page at `/settings/recycle-bin`. Per-entity
  tabs (Contacts / Events / Participants / Special orders), keyset
  pagination via TanStack `useInfiniteQuery` + a "Load more" button,
  per-row Restore via `RecordDependenciesDialog` in
  `confirmMode='restore'`. Backend `RecycleBinItem` gained
  `parent_event_id` so the nested participant / special-order restore
  routes can be called from the bin without a second lookup. Settings
  index gained a Recycle Bin entry; route mounted in `App.jsx`.
- [x] D3-D3 — Timeline rendering: labels for the eight new
  `*.archived` / `*.restored` activity types in
  `frontend/src/pages/event/tabs/Activity.jsx`. Archive lines surface
  `reason` (via a local `ARCHIVE_REASON_LABEL` map mirroring the
  backend enum) and the optional `note` inline. Participant lines
  include the participant's display_name + role; special-order lines
  include size_label and the status-at-archive snapshot.

Tests:

- [x] Service tests for each archive/restore pair —
  `tests/test_archive_restore_services_smoke.py` (contact / event /
  participant / special-order; idempotency; sole-quince block;
  quince-slot guard; participant status flip; activity-row shape).
- [x] Activity log payload tests — same smoke verifies the
  `dependency_snapshot`, `reason`, and `note` keys land on every
  archive row, and that orphan-contact archives skip the row
  entirely per the Gate 1 fallback.
- [x] Recycle Bin smoke —
  `tests/test_recycle_bin_endpoint_smoke.py` (auth, bad
  entity_type, contains-archived-row, orphan-contact NULL audit,
  keyset pagination via `before_id`, `deleted_by_user_id` filter).
- [x] Archive → hidden → appears in Recycle Bin → restore → visible
  smoke — covered by the combined services / endpoints / recycle-bin
  smokes plus the D2 read-filter smoke
  (`tests/test_soft_delete_read_filters_smoke.py`).
- [x] Cleanup SQL handles new smoke fixtures
  (`scripts/cleanup_admin_smoke_pollution.sql` gained the
  D1 / D2 / D3A / D3B / D3C prefix families).

Acceptance:

- [x] Admin can archive and restore the four target entity types via
  the admin SPA (contact archive from `ContactDetail`, event archive
  from `EventDetailLayout`, participant archive from `Overview`,
  every entity restorable from the Recycle Bin page).
- [x] Confirmation shows dependency counts before archive
  (`RecordDependenciesDialog` is shared by every archive verb and is
  pre-fetched in `enabled: open && ...`).
- [x] Activity log records reason/note/dependency snapshot — verified
  by D3-A service smoke and the new D3-D3 timeline labels render the
  `reason` + `note` fields inline.
- [x] Recycle Bin shows archived records and supports restore — D3-D2
  page + D3-C endpoint, with `parent_event_id` carried for the nested
  restore routes.

### D4: Purge

Status: Deferred (decision reaffirmed at D3 closeout, 2026-06-03)

**Closeout decision:** purge stays deferred. Reasons:

- Single-tenant white-label deployment; storage cost of soft-deleted
  rows is negligible.
- No regulatory or contractual retention obligation has been
  identified that would force a purge cadence.
- The dependency report + activity_log payload already supply the
  audit substrate a future purge would build on; nothing about
  keeping rows in the bin forever is irreversible.
- Hard-delete on Tier 1 tables would require fresh thinking about
  the financial cascade arrows (CASCADE from `payment_allocations`,
  `invoice_line_items`, `event_documents`) and whether PII
  redaction is a better target than physical delete.
- The more pressing UX gap — when the bin grows large — is bulk
  restore + date/actor filters in the Recycle Bin UI, not a purge
  sweep. That can be addressed as a polish slice without touching
  the schema.

Re-open this section when any of the following triggers fires:

- A client request to legally remove a customer ("right to be
  forgotten" or similar). At that point design PII redaction first
  (clear identifying columns in place) before adding a hard-delete
  path.
- Storage growth on `contacts` / `events` / `event_participants` /
  `special_orders` exceeds a documented threshold that affects
  query latency.
- A retention policy is added to the business profile / legal
  framework.

Decision checklist (left unchecked on purpose — answer when the
section reopens):

- [ ] Retention window: 30, 60, 90 days, or never.
- [ ] Owner-only or admin-only.
- [ ] UI action, scheduled job, or both.
- [ ] Physical hard-delete vs PII redaction.
- [ ] Update policy, guardrail, and cleanup jobs.

## D3 Closeout Note (2026-06-03)

Full feature ships at the D3-D3 commit. Smoke matrix at closeout:

- `tests/test_delete_policy_guardrail_smoke.py` — OK
- `tests/test_record_dependencies_smoke.py` — OK
- `tests/test_soft_delete_read_filters_smoke.py` — OK
- `tests/test_archive_restore_services_smoke.py` — OK
- `tests/test_archive_restore_endpoints_smoke.py` — OK
- `tests/test_recycle_bin_endpoint_smoke.py` — OK

Known open items (deliberately out of D3 scope):

- **Special-order archive has no detail-page button** because no
  admin SPA surface lists live special orders today. They become
  archivable directly via API and restorable from the Recycle Bin.
  Adding a special-orders tab on the event detail layout is the
  natural follow-up.
- **Contacts have no `/contacts` index route.** D3-D1a's archive
  button lives on the contact detail page; reaching that page
  requires Global Search (⌘K) or a direct URL. A contacts list is
  scoped out of CRM deletion but worth tracking in the broader
  Contact UX backlog.
- **D3 timeline labels do not render on the contact's own page**
  because contacts have no timeline view. Contact-archive activity
  rows are anchored to the contact's most recent event per Gate 1,
  so they appear there, which matches the staff mental model
  ("this event's history includes the primary-contact archive").

## Risk List

- Missed read filter leaks archived CRM records into active views.
- Activity log may not naturally support contact-scoped events.
- Portal links may break if contact/event filters are applied too broadly.
- Partial unique indexes can block restore or duplicate creation.
- Restore can create inconsistent state if parent records remain archived.
- FK/model drift around `event_participants.contact_id` can mislead dependency
  reports.

## Investigation Checklist

Before D1:

- [ ] Inspect actual `activity_log` constraints and decide contact audit scope.
- [x] Inspect actual DB FK for `event_participants.contact_id`.
- [x] Inventory all inbound FKs to target tables.

Before D2:

- [x] Inventory unique constraints/indexes for target tables (only two
  partial uniques overlap deletion: `uq_contacts_phone_e164` and
  `uq_event_participants_quinceanera_per_event`).
- [x] Inventory all query sites for target models (captured in §6 of
  the original plan; all primary admin/sales reads patched in D2).
- [x] Decide portal read behavior (Gate 3 resolved: portal stays
  unfiltered).
- [x] Decide whether concurrent index rebuilds are needed (no —
  single-tenant VPS, small `contacts` table; brief locks acceptable).

Before D3:

- [ ] Decide archive reason enum.
- [ ] Decide route placement for Recycle Bin.
- [ ] Decide who can archive/restore: admin only, owner only, or staff with role.

## Suggested Archive Reasons

- `duplicate`
- `test_record`
- `created_by_mistake`
- `customer_requested`
- `other`

## Agent Prompt

Use this prompt when starting implementation work:

```text
Read docs/CRM_RECORD_DELETION_PLAN.md before making changes. Work only on the
currently active phase. Do not implement purge. Do not add separate archived_at
columns. Keep the schema to one deleted_at timestamp for contacts, events,
event_participants, and special_orders.

Before coding, update the phase status and answer any blocking Decision Gates.
After coding, update this tracker with completed tasks, changed files, tests run,
and any new risks or decisions.
```

## Progress Log

| Date | Phase | Update | Tests |
|---|---|---|---|
| 2026-06-02 | Planning | Tracker created. | Not run. |
| 2026-06-02 | D1 | Cleared Gate 2 (no FK drift, RESTRICT confirmed by 055). Inbound FK inventory captured. Added `services/record_dependencies.py`, `api/routers/admin_dependencies.py`, mounted at `/api/admin/dependencies`. Added smoke prefixes to `scripts/cleanup_admin_smoke_pollution.sql`. API service restarted; 4-probe verification clean. | `tests/test_record_dependencies_smoke.py` OK; `tests/test_delete_policy_guardrail_smoke.py` OK. |
| 2026-06-02 | D1 | Frontend slice. Added `getRecordDependencies` to `frontend/src/services/api.js` and the reusable `frontend/src/components/RecordDependenciesDialog.jsx` (MUI Dialog + TanStack Query). No page imports it yet — D3 wires the action buttons. Vite build clean; eslint clean. Component supports `confirmMode='archive'\|'restore'` and disables its confirm button by report.can_archive/can_restore so D3 wiring is a one-prop change. | `npm run build` + `npm run lint` clean. Browser verification pending (no page renders the component until D3). |
| 2026-06-02 | D2 | Gate 3 resolved (portal joins stay unfiltered for signed-link reads). Migration 080 added `deleted_at` to contacts/events/event_participants/special_orders, deleted-row partial indexes, and rewrote `uq_contacts_phone_e164` + `uq_event_participants_quinceanera_per_event` with `AND deleted_at IS NULL`. Inline DML probes verified the partial-unique reuse-after-archive behavior on the live DB. Model columns added; `_TARGET_TABLES_WITH_DELETED_AT` flipped to True. Read-filter audit landed across contact_service, event_service, event_participants, special_order_service, sales_appointments, sales_assignment, notification_routing, dashboard, buyer_journey, contacts/events/event_documents/admin_booking routers. Policy doc + guardrail reclassified the four tables from Tier 2 to Tier 1. API restarted; 4-probe clean. | `tests/test_soft_delete_read_filters_smoke.py` OK; D1 smoke OK; guardrail OK. |
| 2026-06-02 | D3-A | Gate 1 resolved (option a: anchor on the contact's most recently created event; skip the activity row + log warning when the contact has no events). Added eight service helpers: `archive_contact`/`restore_contact`, `archive_event`/`restore_event`, `archive_event_participant`/`restore_event_participant`, `archive_special_order`/`restore_special_order`. Each archive preflights via the D1 dependency report; each restore checks parent-archived + entity-specific guards (contact phone collision, quinceanera-slot conflict). Activity-log vocabulary gained `contact.archived`, `event.archived`, `event_participant.archived`, `special_order.archived` (+ `.restored` siblings) plus two new SubjectKind values. Shared `validate_archive_reason` + `dependency_snapshot` helpers live in `services/record_dependencies.py`. Smoke seeds + verifies idempotency, blocking, parent-archive restore guard, sole-quinceanera block, quinceanera slot guard, status flip on participant archive, activity-row shape including `dependency_snapshot`. | `tests/test_archive_restore_services_smoke.py` OK; D1 / D2 / guardrail all OK. |
| 2026-06-02 | D3-B | Eight admin endpoints in `api/routers/admin_archive.py` (POST archive + POST restore for each entity type), mounted at `/api/admin`. Nested participant and special-order routes verify the path's `event_id` matches the child row's parent — URL substitution returns 404. Domain error codes map to 400 (`invalid_reason`), 404 (`*_not_found`), 409 (`archive_blocked`, `parent_archived`, `restore_phone_collision`, `quinceanera_slot_taken`). Refined the contact dependency report to skip participants whose parent event is archived, so a contact whose entire event history is in the Recycle Bin can itself be archived. API restarted; 4-probe clean. | `tests/test_archive_restore_endpoints_smoke.py` OK; D1/D2/D3-A/guardrail all OK. |
| 2026-06-02 | D3-C | Recycle Bin endpoint `GET /api/admin/recycle-bin` added to the same router. Keyset pagination via `before_id`, optional `since`/`until` clamps on `deleted_at`, optional `deleted_by_user_id` filter (joins `activity_log` by `subject_kind`/`subject_id` + the entity's `*_ARCHIVED` activity type). Per-entity `display_name` + `secondary_label` builder so each row in the bin is self-describing. Orphan-contact archives (Gate 1 fallback) appear with NULL audit metadata — documented inline. API restarted; 4-probe clean. | `tests/test_recycle_bin_endpoint_smoke.py` OK; all five prior smokes plus guardrail OK. |
| 2026-06-02 | D3-D1a | Contact archive pilot frontend slice. `RecordDependenciesDialog` extended with reason picker (the five backend `ARCHIVE_REASONS`) + optional note field that surface only in archive mode; `onConfirm` now passes `{reason, note}`. New `archiveContact` / `restoreContact` API helpers in `frontend/src/services/api.js`. `ContactDetail.jsx` mounts the dialog from a new "Archive" header button, mutates via `archiveContact`, invalidates the contact + dependency queries on success, and bounces with `navigate(-1)` so the resulting 404 page never lands. `npm run build` + `npm run lint` clean; bundle replaced under `frontend/dist/` which nginx already serves from. Browser verification pending. | Backend smoke matrix unchanged (no API changes this slice). |
| 2026-06-03 | D3-D1b | Event + participant archive frontend slice. Added `archiveEvent` / `restoreEvent` / `archiveEventParticipant` / `restoreEventParticipant` / `archiveSpecialOrder` / `restoreSpecialOrder` to `frontend/src/services/api.js`. `EventDetailLayout.jsx` gains an Archive button next to the status dropdown; success invalidates the event + board queries and navigates to `/pipeline`. `Overview.jsx` adds a per-row archive `IconButton` in the Participants section that opens the same dependency dialog scoped to `event_participant`; success invalidates the event query so the row disappears from the list. Special-order archive is deferred because no admin SPA surface lists special orders today; the helper is in place for D3-D2 to reuse from the Recycle Bin. Browser validation completed by Luis. Follow-up code review found no blocker; next recommended slice is D3-D2 Recycle Bin page. | `npm run lint` clean except existing fast-refresh warning; `npm run build` clean; delete-policy guardrail OK. Backend smoke matrix unchanged (no API changes this slice). |
| 2026-06-03 | D3-D2 | Recycle Bin page slice. Added `frontend/src/pages/RecycleBin.jsx` mounted at `/settings/recycle-bin` and an entry on the Settings landing. Per-entity tabs (contact / event / event_participant / special_order). Each tab uses `useInfiniteQuery` for keyset pagination via the existing `?before_id=…` cursor and renders a "Load more" button when more pages exist. Each row shows display_name + secondary_label + relative deleted_at + actor + reason chip, and a Restore button that opens `RecordDependenciesDialog` in `confirmMode='restore'` for a final confirmation. `RecycleBinItem` gained `parent_event_id` so the nested participant + special-order restore routes can be called from the bin without a second lookup. New `listRecycleBin` API helper in `frontend/src/services/api.js`. API restarted; 4-probe clean. | `tests/test_recycle_bin_endpoint_smoke.py` OK, archive-endpoints smoke OK, delete-policy guardrail OK. Vite build + eslint clean. Browser verification pending. |
| 2026-06-03 | D3-D3 | Timeline rendering polish slice. `frontend/src/pages/event/tabs/Activity.jsx` gained renderers for the eight new activity types (`contact.archived`/`restored`, `event.archived`/`restored`, `event_participant.archived`/`restored`, `special_order.archived`/`restored`). Archive lines include `reason` (via a local `ARCHIVE_REASON_LABEL` map that mirrors the backend enum) and the optional `note` inline; participant lines include display_name + role; special-order lines include size_label + status-at-archive. Closeout pass updated the D3 acceptance boxes, reaffirmed D4 (purge) stays deferred, and ran the full smoke matrix — all six green. Vite build + eslint clean. Browser verification of Activity tab pending. | Full smoke matrix green: delete-policy guardrail, D1 dep, D2 read filters, D3-A services, D3-B endpoints, D3-C recycle-bin. |
