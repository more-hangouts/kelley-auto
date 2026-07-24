# Contact UX — Phased Plan

Make the contact identity visible and editable on the event detail page, so the "shared phone, stale name" case (returning customer books under a different celebrant) becomes self-explanatory and self-fixable for staff.

## Goal

Today, when a customer rebooks under a different celebrant name on the same phone, the auto-promote correctly creates a new lead with the celebrant-derived event name, but the underlying contact retains the original name. That is the correct CRM behavior — contact is stable identity, appointment is event context — but staff have no in-product affordance to:

1. See at a glance that celebrant and contact differ on a given lead.
2. Correct a wrong contact name without dropping into the database.
3. Merge two contact rows that turned out to be the same person.

This plan adds those three affordances in three phases, ordered by cost.

## Working environment

All build and verification work happens on the VPS. There is no local dev server. Smoke tests that say "visit `/events/<id>`" mean visit the deployed admin host (`admin.shopbellasxv.com`) after the VPS rebuild and service restart, not a localhost URL.

## Decisions locked

- The CRM doctrine is set: contact = stable identity, appointment = event context. See [docs/CRM.md](CRM.md). This plan does not retroactively rename past events or participants when a contact name is corrected; the appointment-frozen names stand.
- Phase A is frontend-only and ships first. It surfaces the divergence with no backend change by comparing the event's primary contact to the event's active quinceañera participant snapshot.
- Phase B adds single-contact edit. No merge logic. A phone collision returns a 409 that is the natural prompt to build Phase C.
- Phase C is built only when real duplicate-contact incidents appear in production. Until then, Phase B's "fix the name on the existing contact" path covers the observed case.
- No contact audit log in v1. There is no `contacts_audit` table today and adding one is its own decision; revisit when staff ask "who changed this name?"
- No retroactive cascade of name changes onto past events. Future bookings naturally pick up the corrected name on the next promote.
- No backend expansion in Phase A. `EventDetailResponse.appointments[]` does not currently expose appointment celebrant names, and the kanban board payload does not expose participants. Any "other names seen on this contact across events" signal is Phase B+ backend scope, not Phase A.

## Tracking

- [x] Phase A: Celebrant-vs-contact signal (frontend-only)
- [x] Phase B: Contact edit (single contact, no merge)
- [ ] Phase C: Contact merge (deferred until duplicates appear)

---

## Phase A: Celebrant-vs-contact signal

Purpose: make the divergence visible on event surfaces that already have the data, with zero backend change. In `EventDetailResponse`, `event.event_name` is celebrant-derived at promotion, `event.primary_contact.display_name` is the contact identity, and `event.participants[]` carries the per-appointment celebrant snapshot. The board payload does not carry participants, so board-card work stays out of Phase A.

Tasks:

- [ ] Add a small frontend helper, colocated with event UI code, that returns the active quinceañera participant display name from `event.participants[]` and normalizes names for comparison. Use case-insensitive trimmed comparison; do not try fuzzy matching in Phase A.
- [ ] Update [frontend/src/pages/event/EventDetailLayout.jsx:118-120](../frontend/src/pages/event/EventDetailLayout.jsx#L118-L120). Subtitle currently shows only `primary_contact.display_name`. Render "Contact: {primary_contact.display_name}" as a small caption below `event.event_name` only when `primary_contact.display_name` differs from the active quinceañera participant name. When equal, collapse back to the current single-line layout.
- [ ] Update [frontend/src/pages/event/tabs/Overview.jsx:331-335](../frontend/src/pages/event/tabs/Overview.jsx#L331-L335) "Primary contact" section. When the active quinceañera participant differs from the contact display name, append a compact caption: "Celebrant on this event: Chumba Casino". Do not list names across other events in Phase A because the current endpoint does not provide them.
- [ ] Update [frontend/src/components/EventQuickViewDrawer.jsx](../frontend/src/components/EventQuickViewDrawer.jsx). Mirror the treatment only if the drawer has enough data. Today the board card payload lacks participants, so the drawer can safely keep showing the contact subtitle until Phase B adds backend support, or it can show `Contact: {name}` unconditionally under the event name.
- [ ] Decide subtitle ordering once and apply it consistently. Default: celebrant-first (matches `event.event_name`, which is also celebrant-derived). Contact identity is the secondary line. Mature CRMs differ on this — Salesforce shows opportunity-first, HubSpot shows deal-first with contact below. Celebrant-first matches what the cards already show.

Smoke test (manual, on `admin.shopbellasxv.com` after rebuild):

- Open the Chumba Casino lead (or any lead where celebrant != contact). Header shows "Chumba Casino's Quince" with "Contact: debbie" below. Overview "Primary contact" section shows "Celebrant on this event: Chumba Casino".
- Open a lead where the contact name matches the celebrant (e.g. Marisa). Header shows the single-line layout. No celebrant mismatch caption appears.
- Quick-view drawer remains coherent: either it shows the same contact subtitle pattern with the data it has, or it remains unchanged until the backend payload grows.

Deliverable: visual change only. No backend, no schema, no new endpoints. Unblocks staff confusion on the event detail page without expanding the board API.

Scope validation note, 2026-05-01:

- Current `EventDetailResponse` exposes `participants[]`, so event-detail mismatch detection can be frontend-only.
- Current `EventDetailResponse.appointments[]` does not expose appointment celebrant fields, and `BoardCardResponse` does not expose participants. Cross-event "also booked under" lists remain Phase B+ backend scope.
- Drawer correction: `EventQuickViewDrawer` already enriches its card by fetching the full `getEvent(card.id)` detail, so `detail.participants[]` is available in the drawer too. Drawer can fully participate in Phase A without any backend payload change. Comparison falls back gracefully while `detail` is loading (it shows the original single-line subtitle until detail arrives, then swaps in the contact caption on mismatch).

Validation note, 2026-05-01:

- Helper `frontend/src/utils/eventCelebrant.js` added: `getCelebrantName(event)` returns the active quinceañera participant's display_name; `celebrantDiffersFromContact(event)` does case-insensitive trimmed compare against `primary_contact.display_name`. Returns false on missing data so the UI defaults to the existing single-line layout.
- `EventDetailLayout.jsx` header subtitle: when celebrant differs, renders "Contact: {display_name}" as a small caption below the event name. When equal, original layout unchanged.
- `Overview.jsx` "Primary contact" section: when celebrant differs, appends "Celebrant on this event: {name}" caption under the contact fields.
- `EventQuickViewDrawer.jsx` subtitle: same caption-on-mismatch treatment, gated on the async `detail` query so it stays coherent during load.
- `cd frontend && npm run lint` passed.
- `cd frontend && npm run build` passed. Vite still reports the existing large-chunk warning on the main JS bundle.
- No backend changes. No new endpoints, no schema changes, no new tests required.
- Manual browser smoke still pending — happens on `admin.shopbellasxv.com` after VPS rebuild and service restart.

---

## Phase B: Contact edit

Purpose: let staff fix a wrong contact name (the "debbie should be Maria" case) without touching the database. Single-contact edit only — no merge.

Backend tasks:

- [ ] New router `api/routers/contacts.py`. Endpoints in v1:
  - `GET /api/contacts/{id}` returns editable contact fields plus lightweight context counts (`event_count`, `appointment_count`) and, if cheap, recent celebrant names seen on linked events.
  - `PATCH /api/contacts/{id}` body `{ first_name?, last_name?, display_name?, email?, phone?, notes?, tags? }`. Auth-gated via `get_current_user`.
  - Re-normalize `phone_e164` server-side when `phone` changes, reusing `booking_service.normalize_phone_e164`.
  - When `first_name` or `last_name` changes and no explicit `display_name` is sent, recompose `display_name` from first+last using the same logic as `contact_service._compose_display_name`. If an explicit `display_name` is sent, trim and validate it but do not infer first/last from it.
  - Catch `IntegrityError` on the `phone_e164` unique index. Return 409 `phone_collision` with the colliding contact's id in the body. The 409 is the natural Phase C entry point.
- [ ] New service function `services/contact_service.py:update_contact`. Centralizes the normalization and recompose logic so the router stays thin.
- [ ] Wire the router into [api/server.py](../api/server.py).
- [ ] Smoke test in `tests/test_contacts_smoke.py` (new file) or appended to `tests/test_events_smoke.py`:
  - GET contact requires auth and returns the expected editable fields.
  - Rename succeeds; `display_name` auto-recomposes when first/last change without an explicit display_name.
  - Explicit `display_name` override succeeds without rewriting first_name/last_name.
  - Phone change re-normalizes `phone_e164`.
  - Phone collision returns 409 with the colliding id.
  - Auth required.

Frontend tasks:

- [ ] Add `updateContact(id, patch)` to [frontend/src/services/api.js](../frontend/src/services/api.js).
- [ ] New `frontend/src/components/ContactEditDialog.jsx`. MUI dialog with fields for first/last/display/email/phone/notes. Match the visual style of [ConfirmDialog.jsx](../frontend/src/components/ConfirmDialog.jsx).
- [ ] Add an "Edit" button to the Overview "Primary contact" section that opens the dialog.
- [ ] On save, invalidate `['event', eventId]` and `['events', 'board']` so the kanban subtitle refreshes.
- [ ] When the API returns 409 `phone_collision`, surface a clear error in the dialog ("Phone in use by contact #123") with a placeholder for the future merge action.
- [ ] After a successful rename, Phase A's mismatch caption should naturally disappear once the contact display name matches the event's active quinceañera participant.
- [ ] If the Phase B GET endpoint includes recent alternate celebrant names, show them as read-only context in the dialog or Primary contact section. Keep it out of the board unless staff ask for it.

Smoke test (manual):

- Edit the "debbie" contact. Change first_name to "Maria", last_name to "Garcia". Save. Header refreshes; "Primary contact" section shows the new name; kanban subtitle updates on next refetch.
- Past event names ("debbie's Quince") still show the original frozen name. This is intentional per the doctrine.
- Try to set a phone already in use by another contact. 409 surfaces as a readable error in the dialog.
- Refresh the page after each successful save. Changes persist.

Deliverable: staff can self-serve the "wrong contact name" fix. Eliminates the most common reason to ask engineering to touch the database.

Scope validation note, 2026-05-01:

- `contacts` already has `first_name`, `last_name`, `display_name`, `email`, `phone`, `phone_e164`, `tags`, and `notes`, so Phase B needs no migration.
- `display_name` should remain an explicit editable display field, but first/last are the canonical components used for recomposition when no display override is supplied.

Validation note, 2026-05-01:

- Backend:
  - `services/contact_service.py` adds `ContactServiceError`, `update_contact`, and `get_contact_context`.
  - `api/routers/contacts.py` exposes `GET /api/contacts/{id}` and `PATCH /api/contacts/{id}`. Wired in `api/server.py` at prefix `/api/contacts`.
  - Display-name precedence: explicit `display_name` always wins; otherwise first/last triggers a recompose; otherwise display stays as-is. Empty/whitespace explicit display rejected with 422 `display_name_required`.
  - Phone changes re-normalize `phone_e164` via `booking_service.normalize_phone_e164`.
  - Phone collision returns 409 with `{"code": "phone_collision", "conflict_contact_id": <id|null>}` so the future Phase C merge flow has a direct entry point.
  - Implementation note: chose a raw-SQL pre-check for the phone collision rather than the savepoint+IntegrityError pattern used by `find_or_create_contact`. That pattern works for INSERTs because the failed row is brand new and the savepoint discards it cleanly; an UPDATE leaves the dirty attributes in the SQLAlchemy identity map, so the next autoflush re-raises outside the savepoint and PendingRollbackError takes down the request. The pre-check sidesteps that. A belt-and-suspenders `IntegrityError` catch on flush still surfaces a clean 409 if a concurrent insert wins the race between check and flush.
- Frontend:
  - `frontend/src/services/api.js` adds `getContact(id)` and `updateContact(id, patch)`.
  - New `frontend/src/components/ContactEditDialog.jsx`. Loads via React Query, edits in local state, computes a diff patch on save (only sends changed fields, per `model_fields_set` doctrine; clears via `null`).
  - Surfaces 409 `phone_collision` as a readable error with the conflict id, plus a forward note that merge tooling lands later.
  - Renders `alternate_celebrants` as small chips inside the dialog so staff see the "also booked under" signal exactly where they would correct it.
  - Save invalidates `['events', 'board']` and all `['event', ...]` queries so the kanban subtitle and Phase A captions refresh immediately.
  - Overview "Primary contact" section gets an "Edit" button that opens the dialog.
- Tests:
  - New `tests/test_contacts_smoke.py`: auth required, GET 401/404/200, recompose vs explicit display, empty display rejected, phone re-normalize, phone collision 409 with conflict id, contact unchanged after collision, clear-via-null, notes/tags round-trip.
  - `venv/bin/python tests/test_contacts_smoke.py` passed.
  - Regression smokes passed: `tests/test_events_smoke.py`, `tests/test_booking_smoke.py`, `tests/test_boutique_experience_smoke.py`.
  - `cd frontend && npm run lint` passed. `cd frontend && npm run build` passed (existing large-chunk warning unchanged).
- Manual browser smoke still pending — runs on `admin.shopbellasxv.com` after VPS rebuild + service restart.
- Carryover for VPS deploy: nothing extra. No new env vars, no new write paths (so no `ReadWritePaths` change needed in the systemd unit). Migrations: none.

---

## Phase C: Contact merge (deferred)

Build only when a real duplicate-contact incident appears in production. The Chumba Casino case is **not** one — it's a single contact with a stale name, fixed by Phase B.

Trigger to revisit (demand-driven, not calendar-driven): after Phase B has been live for a few weeks, run a duplicate-contact query (contacts sharing email, or fuzzy-matching display_name with different phone_e164) and check API logs for `409 phone_collision` events. If either surfaces real cases, build the merge flow. If neither does, leave Phase C deferred and re-check on the next prompt.

Pre-build investigation:

- [ ] Grep `ForeignKey("contacts.id"` to enumerate every table that needs reassignment. Today the known set is `appointments.contact_id`, `events.primary_contact_id`, `event_participants.contact_id`. Confirm before writing.
- [ ] Decide soft-delete vs hard-delete on the loser. Soft-delete (add `merged_into_id` + `merged_at` columns) preserves audit and enables undo. Hard-delete is simpler but irreversible. Pick before scoping the migration.

Backend tasks:

- [ ] `POST /api/contacts/{winner_id}/merge` body `{ loser_id }`. Single transaction:
  1. Reassign FKs from loser to winner across the enumerated tables.
  2. Loser's `phone_e164` and `email` are dropped silently if they differ from winner. Document this on the confirm dialog so staff know what's lost.
  3. Soft-delete (preferred) or hard-delete the loser row. `events.primary_contact_id` is `RESTRICT` — reassign must complete before the delete or the request 500s.
- [ ] New service function `services/contact_service.py:merge_contacts`. Admin-gated.
- [ ] Smoke test: appointments and events follow the winner; loser is unreachable; events created from the loser's old appointments still resolve correctly via their `crm_event_id`.

Frontend tasks:

- [ ] "Merge into…" button inside `ContactEditDialog`. Opens a `ContactSearchPicker` (search by name/phone/email).
- [ ] Diff-style confirm dialog: "Will reassign N appointments and M events to {winner}. {loser} will be removed. Loser's phone {x} and email {y} will be dropped." Big destructive-action confirm.
- [ ] Phase B's 409 `phone_collision` error gets a "Merge with that contact?" link that opens this flow.

Optional — duplicate suggestions:

- [ ] `GET /api/contacts/{id}/suggested-duplicates` returns contacts sharing email or fuzzy-matching name. Surface as a chip in `ContactEditDialog`.
- [ ] Skip for v1. Only build if staff are missing duplicates manually.

Deliverable: staff can resolve real duplicate-contact incidents without engineering involvement.

---

## Open questions (not blocking)

- Subtitle ordering on the event header: celebrant-first (matches `event.event_name`) or contact-first (matches Salesforce/HubSpot identity-first conventions)? Default is celebrant-first; revisit if staff ask.
- Whether Phase B's contact edit should optionally cascade-rename future event names that derive from the contact. Default: no cascade — once a celebrant name is frozen on an event, it stays. Revisit if staff want a "rename related events" checkbox on the dialog.
- Whether to add a `contacts_audit` table when Phase B ships, or wait until staff ask "who changed this." Default: wait.
- Whether the Phase A "Also booked under" caption should show on the kanban card itself, not just on event detail. Default: detail only — kanban cards are deliberately terse.
- Phase C soft-delete vs hard-delete on the loser contact. Pick when scoping C, not now.
