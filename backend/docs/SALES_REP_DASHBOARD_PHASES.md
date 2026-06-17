# Sales Rep Dashboard & Kiosk Quick-Switch — Phased Plan

Turn the sales portal home into a stylist-centric dashboard with shared-tablet "Lock / Switch" support, sales-safe global lead search, and a walk-in macro that assigns the new appointment to the current stylist. Companion to [SALES_PORTAL_PHASES.md](SALES_PORTAL_PHASES.md); does not replace it.

## Goal

After a stylist enters their PIN, they land on a dashboard that shows clock status, today's appointments, a global lead search box, and a primary "Add walk-in" action. The tablet on the floor is shared, so any rep can tap "Lock / Switch", a coworker enters their PIN, and the session swaps without bumping anyone else's JWT family. Sales staff can search the whole CRM by name/phone/event without ever seeing invoice totals, balances, or payment data.

## Working environment

Build, lint, and smoke locally; verify in a browser against `admin.shopbellasxv.com` and `sales.shopbellasxv.com` only after the VPS rebuild + service restart. See [project_no_local_dev_server](../../.claude/projects/-home-luis-bellas-xv/memory/project_no_local_dev_server.md). Browser checks are handed to Luis — no headless Chromium on the VPS.

## Decisions locked

- **Kiosk lock is cookie-only, not a token bump.** `POST /api/sales/auth/kiosk-lock` clears the sales session/CSRF cookies for this device without incrementing `users.token_version`. The stronger `POST /api/sales/auth/logout` keeps its current "bump token_version, invalidate everywhere" behavior for the explicit sign-out case. Reason: bumping `token_version` on a shared-tablet quick lock would silently log the stylist out of every other device they touched today — see [api/routers/sales_auth.py:210](../api/routers/sales_auth.py#L210).
- **Quick Switch is PIN re-entry, not a session merge.** Lock clears the cookie, the PIN picker reappears, the next stylist enters their PIN, a fresh sales cookie is issued, React state refreshes via `/sales/auth/me`. No "switch user" endpoint that trades tokens.
- **Idle lock is 2-minute warn / 5-minute auto-lock.** Activity = pointer, key, touch. Numbers live in one constant so the owner can tune them later without a code hunt.
- **No new `leads.assigned_to` column.** Leads are not a table; they are `events` with `status='lead'`. Use `appointments.assigned_user_id` ([database/models.py:129](../database/models.py#L129)) for the stylist on a specific appointment and `events.owner_user_id` ([database/models.py:357](../database/models.py#L357)) for the CRM lead/event owner. `events.owner_user_id` already has an index from migration 015.
- **Walk-in macro defaults `assigned_user_id` and `owner_user_id` to the punched-in stylist.** `actor_user_id` stays the current user for audit so "created by" and "assigned to" are not conflated. Caller may override `assigned_user_id` from a sales-scoped staff picker.
- **Sales global search is a separate router, not a flag on the admin search.** Sales results never include invoice totals, balances, paid-to-date, quote totals, discounts, payment data, document storage keys, raw payloads, marketing attribution, or tokens. Stripping in middleware is fragile; a parallel `/api/sales/search/leads` endpoint with its own response shape is the safer cut. Admin global search at [api/routers/search.py:52](../api/routers/search.py#L52) stays admin-only.
- **Read-only sales search does not require an active punch; mutations do.** `require_sales_scope` is enough for `GET /api/sales/search/leads`. `POST /api/sales/walk-ins`, the assignment PATCHes, and any other floor-mutation route stay behind `require_floor_access("sales")` (see [services/attendance_gate.py:75](../services/attendance_gate.py#L75)).
- **Dashboard is a real route, not a refactor of `AppointmentsToday`.** Today, the post-login landing is `/` → `AppointmentsToday` ([frontend/src/sales/SalesApp.jsx:31](../frontend/src/sales/SalesApp.jsx#L31), [frontend/src/sales/SalesApp.jsx:49](../frontend/src/sales/SalesApp.jsx#L49)). New `/` → `RepDashboard`, which embeds `AppointmentsToday` as a section. Clock-in redirect moves from `/` to `/dashboard` ([frontend/src/sales/screens/ClockScreen.jsx:306](../frontend/src/sales/screens/ClockScreen.jsx#L306)) — or keep `/` and embed; either way, one canonical landing.
- **Optional `/api/sales/dashboard` aggregate is deferred.** Ship the dashboard with three React Query calls first (appointments, search, staff). Roll up into one endpoint only if real device latency on the floor justifies it.
- **Assignable-staff is all active sales users, not "currently clocked in".** A rep taking a Tuesday walk-in for a Friday fitting must be able to assign it to a coworker who is off that day. Clocked-in-only is too narrow.
- **Lead reassignment cascades to future-dated appointments only.** `PATCH /api/sales/leads/{event_id}/assignment` sets `events.owner_user_id` and updates `appointments.assigned_user_id` on every appointment tied to that event with `slot_start_at >= now()`. Past appointments stay frozen so commission and historical attribution remain accurate.
- **Lead search is always global; no "Mine only" toggle.** "Today's Appointments" is already the stylist's "mine" view. Search is the path for "my mom called earlier" — an unassigned or someone-else's lead the rep has to find from a cold start. Adding a personal filter would defeat the use case.
- **Smoke tests run serially and seed identifiable names.** Any new smoke prefix added here gets added to [scripts/cleanup_admin_smoke_pollution.sql](../scripts/cleanup_admin_smoke_pollution.sql) in the same commit. See [feedback_smokes_run_serially](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_smokes_run_serially.md) and [feedback_smoke_cleanup_sql_prefixes](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_smoke_cleanup_sql_prefixes.md).
- **Commit and push at each phase boundary from the VPS** once the phase's smoke is green. See [project_commit_push_phase_slices](../../.claude/projects/-home-luis-bellas-xv/memory/project_commit_push_phase_slices.md).

## Tracking

- [x] Phase 1: Kiosk lock backend route + frontend lock overlay
- [x] Phase 2: `RepDashboard` route, embed `AppointmentsToday`, move clock-in redirect
- [x] Phase 3: Sales-safe lead search service + router (`GET /api/sales/search/leads`)
- [x] Phase 4: Walk-in service accepts `assigned_user_id`; existing admin route keeps its signature
- [x] Phase 5: Sales walk-in endpoint (`POST /api/sales/walk-ins`) defaulting to current stylist
- [x] Phase 6: Assignment endpoints + assignable-staff picker
- [x] Phase 7: Indexes / backfill migration if production data needs it
- [x] Phase 8: Smoke suite (kiosk lock, sales search RBAC, walk-in assignment, punched-out gating)
- [ ] Phase 9: UI completion + admin/sales parity hardening (sales-side walk-in dialog, assignment pickers, admin parity audit, service rename)

The Phase 1-8 tick marks describe backend + route completeness. The user-facing sales workflow is not finished until Phase 9 ships. See [SALES_ADMIN_SURFACE_ALIGNMENT.md](SALES_ADMIN_SURFACE_ALIGNMENT.md) and [SALES_ADMIN_CAPABILITY_MAP.md](SALES_ADMIN_CAPABILITY_MAP.md) for the doctrine and the per-capability source-of-truth.

---

## Phase 1: Kiosk lock + Quick Switch

Backend:

- [x] Add `POST /api/sales/auth/kiosk-lock` in [api/routers/sales_auth.py](../api/routers/sales_auth.py). Clear the sales session cookie and CSRF cookie via the existing cookie-clearing helper. Do **not** touch `users.token_version`. Return `204`.
- [x] Keep `POST /api/sales/auth/logout` ([api/routers/sales_auth.py:210](../api/routers/sales_auth.py#L210)) as the stronger "log out everywhere" path.

Frontend:

- [x] Extend `SalesAuthContext` to track pointer/key/touch/wheel activity at the document level. Expose `lock()` and idle-warning state.
- [x] Constants: `IDLE_WARN_MS = 2 * 60_000`, `IDLE_LOCK_MS = 5 * 60_000`. Centralize so they can be tuned without grepping.
- [x] After `IDLE_LOCK_MS` of no activity, call `kiosk-lock`, clear in-memory auth, route to `/login?locked=idle`.
- [x] Reuse the PIN login screen after manual or idle lock, with `?locked=` copy distinguishing manual vs idle lock.
- [x] Add a header "Lock / Switch" button in `SalesLayout` that calls `kiosk-lock`.
- [x] On successful PIN entry, the new sales cookie issued by the existing `/api/sales/auth/pin` flow replaces the cleared cookie; React state refreshes from the login response and `/sales/auth/me` on page reload.

Smoke (manual, on `sales.shopbellasxv.com`):

- Stylist A logs in on tablet, opens a second device with the same account. On tablet, tap Lock / Switch. Confirm the second device's session is still alive (i.e. `token_version` not bumped).
- Stylist A logs in, idles 5 min. Auto-lock fires, picker reappears.
- Stylist A locks, Stylist B enters PIN, dashboard shows B's name and B's "Mine only" appointments. Refresh page, still B.
- Hit `POST /api/sales/auth/logout` from A's other device: that device is now signed out; tablet (now on B) unaffected.

Smoke (server, added to `tests/`):

- [x] `test_sales_kiosk_lock_smoke.py`: POST kiosk-lock clears sales cookies and returns 204; `users.token_version` unchanged.

---

## Phase 2: RepDashboard

- [x] Create [frontend/src/sales/RepDashboard.jsx](../frontend/src/sales/RepDashboard.jsx). Phase 2 ships the greeting hero + embedded `AppointmentsToday`; stylist name / clock chip / Lock-Switch are already in the topbar so the dashboard body skips duplicating them. Global Lead Search and Add-walk-in sections land in Phase 3 and Phase 5 respectively — not scaffolded as inert UI in this slice.
- [x] Route `/` → `RepDashboard` in [frontend/src/sales/SalesApp.jsx](../frontend/src/sales/SalesApp.jsx). No `/appointments/today` alias added; nothing currently deep-links to that path.
- [x] Post-clock-in redirect in [frontend/src/sales/ClockScreen.jsx:344](../frontend/src/sales/ClockScreen.jsx#L344) already targets `/`, which is now the dashboard route. No path change required.
- [x] Add `Dashboard` link to the SalesLayout topbar (xs-hidden, sm-visible to match the existing nav buttons). `Lock / Switch` already shipped in Phase 1; the clock chip is the `Clock` quick action. `Add Walk-in` is deferred to Phase 5 — not surfaced as a disabled button to avoid inert UI.

Smoke (manual):

- Log in. Land on dashboard, not raw appointment list. Clock status chip matches `/api/sales/attendance/me` state.
- Clock in. Redirect goes to dashboard, not `/`.
- "Mine only" toggle on Today's Appointments still filters by `appointments.assigned_user_id` ([services/sales_appointments.py:87](../services/sales_appointments.py#L87)).

---

## Phase 3: Sales-safe lead search

Backend:

- [x] New [api/routers/sales_search.py](../api/routers/sales_search.py) exposing `GET /api/sales/search/leads?q=&limit=`. Registered at `/api/sales/search` in `api/server.py`.
- [x] Auth: `require_sales_scope`. No attendance gate on read.
- [x] New service [services/sales_search_service.py](../services/sales_search_service.py). Does not call into the admin invoice/quote branches; uses its own SQL across `contacts`, `events`, and `appointments` only.
- [x] Result shape:
  ```json
  {
    "query": "maria",
    "results": [
      {
        "type": "event",
        "id": 42,
        "label": "Sofia Garcia - Quince",
        "sublabel": "Lead · Maria Garcia · Aug 15 2026",
        "contact_id": 17,
        "assigned_user_id": 5,
        "route": "/appointments/123"
      }
    ]
  }
  ```
- [x] Allowed result `type` values: `contact`, `event`, `appointment`. Each result carries only ids and presentation strings; no monetary fields, no notes, no document keys, no marketing attribution, no tokens. Contact/event results only surface when the entity has an associated appointment to route through — the sales portal has no contact-only or event-only detail page yet.

Frontend:

- [x] [frontend/src/sales/LeadSearch.jsx](../frontend/src/sales/LeadSearch.jsx): debounced (250 ms) React Query against `/sales/search/leads`. Rendered as a section between the dashboard greeting and `AppointmentsToday`.
- [x] Each result is a `CardActionArea` that navigates to `/appointments/{id}` — the only sales-portal detail route that exists today.

Smoke (server):

- [x] [tests/test_sales_search_rbac_smoke.py](../tests/test_sales_search_rbac_smoke.py): 401 unauthenticated, 403 admin token, 200 sales token; recursive scan for forbidden monetary keys; 422 on `q` shorter than 2 chars; per-result key set asserted.
- [x] [tests/test_sales_search_results_smoke.py](../tests/test_sales_search_results_smoke.py): seeds contact + event + appointment, asserts name fragment / accent-folded fragment / phone digits / event theme / raw confirmation code / hyphenated confirmation code each return the expected result type with `route` pointing back to the seeded appointment.

---

## Phase 4: Walk-in service assignment hook

- [x] Extend [services/walk_in_service.py](../services/walk_in_service.py) `create_walk_in_lead` to accept `assigned_user_id: int | None = None`.
- [x] When `assigned_user_id` is provided: set `Appointment.assigned_user_id = assigned_user_id` and override `Event.owner_user_id` with the same id in the same transaction. `actor_user_id` stays the caller's id.
- [x] When `assigned_user_id` is `None`: admin route signature unchanged. Note: existing `event_service` behavior still falls back `Event.owner_user_id` to `actor_user_id` when no explicit owner is supplied — this is intentional pre-existing behavior of the admin walk-in path, not new in Phase 4. `Appointment.assigned_user_id` stays NULL in this case.

Smoke (server):

- [x] [tests/test_walk_in_assignment_smoke.py](../tests/test_walk_in_assignment_smoke.py): calls the service with `assigned_user_id=<sales user>` and asserts both fields equal that id; calls with `None` and asserts `appt.assigned_user_id IS NULL` plus `event.owner_user_id == actor_user_id` (pre-existing event_service fallback); both cases verify the `event.walk_in_created` activity_log row carries `actor_user_id` = caller.

---

## Phase 5: Sales walk-in endpoint

- [x] Added [api/routers/sales_walk_ins.py](../api/routers/sales_walk_ins.py) with `POST /api/sales/walk-ins`. Registered at `/api/sales/walk-ins` in `api/server.py`. Request payload reuses the admin walk-in Pydantic models for contact/event/enrichment to avoid drift.
- [x] Dependency: `require_floor_access("sales")` — punched-out stylists are 403'd; read-only sales paths still work per Phase 3.
- [x] Request schema accepts optional `assigned_user_id`. Server resolves `None` → `current_user.id`. Validation rejects non-existent or non-sales ids via the shared helper in [services/sales_staff.py](../services/sales_staff.py) (Phase 6's `GET /api/sales/staff/assignable` will reuse the same filter).
- [x] Calls `create_walk_in_lead` with the resolved assignment; admin walk-in path is untouched.
- [x] Response: `{ appointment_id, event_id, contact_id, assigned_user_id, route }` — no optimistic UI in the dashboard since the transaction writes three rows.

Smoke (server):

- [x] [tests/test_sales_walk_in_smoke.py](../tests/test_sales_walk_in_smoke.py): six cases — default assignment = self, explicit coworker assignment, admin-id rejected (400), non-existent id rejected (400), admin token rejected at scope check (403), gate-enabled punched-out sales user rejected (403 `attendance_gate`). Attendance gate state is captured and restored on teardown.

---

## Phase 6: Assignment endpoints + assignable-staff picker

- [x] `GET /api/sales/staff/assignable` in [api/routers/sales_assignment.py](../api/routers/sales_assignment.py). Returns `[{id, full_name}]` for active sales users via the shared helper at [services/sales_staff.py](../services/sales_staff.py). No attendance gate (off-shift stylists planning ahead still need the list).
- [x] `PATCH /api/sales/appointments/{id}/assignment` behind `require_floor_access("sales")`. Validates the new assignee through the same `sales_staff.is_assignable_sales_user` filter the Phase 5 walk-in endpoint uses. Nullable body field allows explicit unassign. Flat route — no parent/child id verification needed.
- [x] `PATCH /api/sales/leads/{event_id}/assignment`: sets `events.owner_user_id` and cascades in the same transaction. Cutoff is `slot_start_at >= NOW()` (UTC). Past-dated appointments stay frozen so commission attribution is preserved. Response carries `cascaded_appointment_ids` so the dashboard can update the affected rows in place.
- [x] Audit: [services/sales_assignment.py](../services/sales_assignment.py) writes one `event.reassigned` parent row plus one `appointment.reassigned` per cascaded appointment, all anchored to the same `event_id`. Payload carries `{from_user_id, to_user_id, reason: "sales_reassignment"}`; cascade-child rows also carry `via: "lead_cascade"`. Two new activity types registered in [services/activity_log.py](../services/activity_log.py). Idempotent no-ops skip audit writes to keep the timeline clean.

Smoke (server):

- [x] [tests/test_sales_assignment_smoke.py](../tests/test_sales_assignment_smoke.py): picker filters out admin + inactive sales users; sales user reassigns appointment to coworker (200, audit row with from/to/actor/reason); admin id rejected (400); inactive sales id rejected (400); non-existent appointment id (404); admin token at scope check (403); explicit unassign with `None` works; idempotent same-value patch is 200 with no extra audit row.
- [x] [tests/test_sales_lead_reassignment_cascade_smoke.py](../tests/test_sales_lead_reassignment_cascade_smoke.py): seeds 1 past + 2 future appointments; PATCH lead asserts `event.owner_user_id` moves, both future appointment rows cascade, past appointment stays at A, response `cascaded_appointment_ids` matches exactly the two future ids, activity_log carries exactly 1 event-level row + 2 appointment-level rows (none for the past appointment), all four rows carry actor + reason; idempotent re-PATCH is 200 with empty cascade and no extra rows; admin token rejected (403); non-existent event (404); invalid assignee (400).

---

## Phase 7: Indexes and optional backfill

Confirm against production schema before writing a migration — do not pre-add unused indexes.

- [x] Added partial `idx_appointments_assigned_user_id` in [database/migrations/078_index_appointments_assigned_user_id.py](../database/migrations/078_index_appointments_assigned_user_id.py) — `ON appointments(assigned_user_id) WHERE assigned_user_id IS NOT NULL`. Shape mirrors the existing `idx_events_owner_user_id` from migration 015.
- [ ] `idx_appointments_assigned_slot` on `(assigned_user_id, slot_start_at)` deferred — single-column partial is sufficient for current "Mine only" + cascade workloads. Revisit when the floor reports a slow schedule view.
- [x] Verified `idx_events_owner_user_id` exists from migration 015; skipped.
- [ ] Optional consistency backfill deliberately **not** shipped — per the plan it requires floor confirmation that legacy unassigned appointments should adopt their event's owner. The migration is a no-op data-wise.

Per [feedback_validate_schema_with_real_inserts](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_validate_schema_with_real_inserts.md): the migration runs an in-transaction `pg_indexes` probe that asserts the index name + partial predicate (`where (assigned_user_id is not null)`), so a drift between the CREATE statement and what the planner stores aborts the apply before commit. Full Phase 1–6 smoke suite re-run post-apply: all nine green.

---

## Phase 8: Full smoke pass + cleanup SQL update

- [x] All seven new smokes registered in [scripts/smoke_handoff.sh](../scripts/smoke_handoff.sh) under a "sales rep dashboard" sub-section of the sales-portal block: kiosk lock, sales search RBAC, sales search results, walk-in assignment service-level, sales walk-in route, sales assignment, lead-reassignment cascade.
- [x] [scripts/cleanup_admin_smoke_pollution.sql](../scripts/cleanup_admin_smoke_pollution.sql) carries every naming family the new smokes seed: usernames sweep via the existing `admin-smoke-%` / `sales-smoke-%` prefixes; contacts/events sweep via `Sales Search Smoke %`, `Walk-In Assign Smoke %`, `Sales Assign Smoke %`; emails sweep via `sssmoke-%@example.com`, `walkin-assign-%@example.com`, `sa-smoke-%@example.com`, `sa-cascade-%@example.com`. End-to-end verification: serial run of all 7 followed by the cleanup SQL preview shows 0 rows of mine remaining.
- [x] Global-pass safety audited per [feedback_global_pass_smokes](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_global_pass_smokes.md): every count assertion in the new smokes is scoped per-event-and-per-subject (`_count_reassign_rows`), per-event (`_activity_rows`), or membership (`seed_id in result_ids`). No table-wide totals.
- [ ] Per [feedback_post_restart_verification](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_post_restart_verification.md): after Luis deploys, run the four probes — systemd status, `/api/health`, a functional `/api/sales/search/leads?q=test` request, and `journalctl -u bellas-xv-api -n 200` — before declaring the rollout green. This step is hands-off-by-design for Claude (browser/VPS work belongs to the operator).

---

## Phase 9: UI completion + admin/sales parity hardening

Phases 1-8 wired the backend, the kiosk session model, and a real sales-safe lead search. They did not finish the floor-facing workflow. The Phase A audit in [SALES_ADMIN_CAPABILITY_MAP.md](SALES_ADMIN_CAPABILITY_MAP.md) confirmed three missing UI surfaces, a sales-named shared service, and a handful of asymmetries worth resolving before declaring "two sides of the same coin" done.

The order below is the alignment doc's Phase B → C → E → F sequence, mapped onto this tracker.

### 9.1 Sales Add Walk-In dialog (alignment doc Phase B)

- [x] Phase A: shared capability map landed in [docs/SALES_ADMIN_CAPABILITY_MAP.md](SALES_ADMIN_CAPABILITY_MAP.md).
- [x] Primary `Add Walk-In` button on `RepDashboard`. Currently the topbar `Add Walk-in` slot is deferred (Phase 2 note); this slice fills it.
- [x] Dialog/full-screen mobile-friendly form. Sections: contact, celebrant/event, enrichment/preferences, assigned stylist.
- [x] Default assignee = current user. Optional assignee picker calls `salesListAssignableStaff` ([frontend/src/services/api.js](../frontend/src/services/api.js)).
- [x] Submit calls `salesCreateWalkIn`. On success navigate to returned `route` and refresh today's appointments.
- [x] Surface the attendance-gate 403 (`attendance_gate`) as a clear "punch in first" inline error, not a generic toast.
- [x] Reuse admin form patterns where they carry no admin-only assumptions; do not import [frontend/src/pages/BusinessProfile.jsx](../frontend/src/pages/BusinessProfile.jsx) directly.
- [ ] Smoke verification: serial run of `tests/test_sales_walk_in_smoke.py` after rebuild; manual browser path on `sales.shopbellasxv.com` as the final gate (handed to Luis per [project_vps_no_headless_chromium](../../.claude/projects/-home-luis-bellas-xv/memory/project_vps_no_headless_chromium.md)).

### 9.2 Sales assignment controls (alignment doc Phase C)

Decision locked: the floor owns BOTH per-appointment and lead-level reassignment. Lead-level opens behind a cascade-preview list so the rep sees every future appointment the move will touch before confirming — the footgun mitigation the "appointment-only vs lead w/ cascade preview" choice picked. Backend supports this with a new `GET /api/sales/leads/{event_id}/cascade-preview` read endpoint.

- [x] "assigned: <name>" chip in [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx) header opens [frontend/src/sales/SalesAssignmentDialog.jsx](../frontend/src/sales/SalesAssignmentDialog.jsx). Scope toggle (appointment / lead), `salesListAssignableStaff`-backed picker, explicit unassign supported.
- [x] Lead-scope option opens a cascade preview pulled from `GET /api/sales/leads/{event_id}/cascade-preview` ([api/routers/sales_assignment.py](../api/routers/sales_assignment.py), service `lead_cascade_preview` in [services/sales_assignment.py](../services/sales_assignment.py)). Same `slot_start_at >= NOW()` cutoff as the PATCH so preview and mutation never disagree.
- [x] AppointmentDetail re-fetches via the existing `refreshTick` effect after a successful reassignment. Today's appointments list relies on React Query's natural refetch on remount — no aggressive invalidation needed since the floor navigates back via the breadcrumb.
- [x] `GET /api/sales/appointments/{id}` enriched with `assigned_user_full_name` and event `owner_user_id` / `owner_full_name`, so the chip and dialog render the current state without depending on the staff-picker list (which filters to active sales users only).
- [x] Smoke verification: new [tests/test_sales_lead_cascade_preview_smoke.py](../tests/test_sales_lead_cascade_preview_smoke.py) registered in `scripts/smoke_handoff.sh`. Existing `tests/test_sales_assignment_smoke.py` and `tests/test_sales_lead_reassignment_cascade_smoke.py` re-run green after the response-shape change. Manual browser path on `sales.shopbellasxv.com` handed to Luis per [project_vps_no_headless_chromium](../../.claude/projects/-home-luis-bellas-xv/memory/project_vps_no_headless_chromium.md).

### 9.3 Service naming cleanup (alignment doc Phase E)

- [ ] Trigger condition: do this before admin imports any assignment domain logic, not before. Premature rename adds churn without a caller. See [SALES_ADMIN_SURFACE_ALIGNMENT.md](SALES_ADMIN_SURFACE_ALIGNMENT.md) Phase E.
- [ ] When the trigger fires: move `services/sales_assignment.py` → `services/assignment_service.py`. Public functions to keep stable: `reassign_appointment`, `reassign_event_lead`, `list_assignable_staff` (the last via `services/sales_staff.py` if it stays).
- [ ] Sales router keeps the role policy at the route boundary; admin router imports the same neutral service.
- [ ] Single commit: rename + every import update + smoke re-run. No grace-period re-exports.

### 9.4 Admin parity audit (alignment doc Phase C continued)

Items the Phase A audit surfaced. Decisions locked 2026-05-18:

- [x] **D1 — Per-appointment reassignment on admin: deferred.** Admin keeps the event-owner reassignment pattern. The shared service rename in 9.3 stays gated on this — premature rename adds churn without a caller. Revisit trigger: an admin reports a workflow the sales floor can do that admin cannot.
- [x] **D2 — Admin notes activity log: shipped.** New [services/appointment_audit.py](../services/appointment_audit.py) `log_notes_edited` centralizes the `APPOINTMENT_NOTES_EDITED` payload shape. Sales-side `update_internal_notes` and admin `PATCH /api/admin/booking/appointments/{id}` both call it. Smoke [tests/test_admin_appointment_notes_audit_smoke.py](../tests/test_admin_appointment_notes_audit_smoke.py) covers first-write, idempotent re-PATCH, length delta, status-only PATCH no-op, no-event no-op. Existing sales actions smoke re-runs green.
- [x] **D3 — Quote in-store approval staff notifications: shipped (digest).** `quote.approved_in_store` registered in `TIMING_MODE` with `digest` timing. New `_owner_of_event` intrinsic targeting helper resolves the event owner from `subject_kind='event'/subject_id=event_id`. `quote_service.approve_in_store` emits the event row after the activity_log writes. Smoke [tests/test_quote_approved_in_store_notification_smoke.py](../tests/test_quote_approved_in_store_notification_smoke.py) covers the happy path + the owner-less event case. Existing quotes and sales quote-sign-convert smokes re-run green.
- [x] **D4 — Admin assignable-staff endpoint: deferred with D1.** Only needed when admin per-appointment reassignment ships. Decision tracked alongside D1.

### 9.5 Verification matrix (alignment doc Phase F)

Run on `sales.shopbellasxv.com` and `admin.shopbellasxv.com` after the 9.1-9.4 slices land. Verification pass executed 2026-05-18 after the API restart that put `4d40424` into production.

**Post-restart four-probe gate** (per [feedback_post_restart_verification](../../.claude/projects/-home-luis-bellas-xv/memory/feedback_post_restart_verification.md)):

- [x] `systemctl status bellas-xv-api`: active (running) since 2026-05-18 15:16:04 UTC.
- [x] `https://api.shopbellasxv.com/api/health` returns `{"status":"ok","database":"connected","migrations_applied":78,"timezone":"America/Chicago"}`.
- [x] Functional probe: `GET /api/sales/leads/1/cascade-preview` returns 401 unauthenticated — route is deployed (would be 404 if the new code were not live).
- [ ] `journalctl -u bellas-xv-api -n 200` — operator can read with `adm`/`systemd-journal` group; this user is not in those groups, so log inspection is operator-side.

**Code-path verification** (covered by automated smokes run from the deployed checkout):

- [x] Punched-out stylist can search but cannot create walk-ins or reassign — `tests/test_sales_walk_in_smoke.py` and `tests/test_sales_assignment_smoke.py` both exercise the 403 `attendance_gate` path.
- [x] Punched-in stylist can create a walk-in assigned to self and to a coworker — `tests/test_sales_walk_in_smoke.py` covers six cases including both default-self and explicit-coworker.
- [x] Sales search response does not contain `balance_cents`, `total_cents`, `paid_cents`, document keys, or raw tokens — `tests/test_sales_search_rbac_smoke.py` (recursive forbidden-key scan) re-ran green.
- [x] Assignment audit rows appear exactly once per real state change; idempotent re-PATCHes do not duplicate — `tests/test_sales_assignment_smoke.py` and `tests/test_sales_lead_reassignment_cascade_smoke.py` both assert this directly.
- [x] Assignment notifications do not duplicate; cancel-old fires before column flip, assign-new fires after — same two smokes cover the ordering invariant.
- [x] Admin notes PATCH writes `APPOINTMENT_NOTES_EDITED` matching the sales-side shape — `tests/test_admin_appointment_notes_audit_smoke.py` green; production DB has 0 rows of this type (wiring deployed, just untouched by real admin edits since restart).
- [x] In-store quote approval emits `quote.approved_in_store` on the staff event bus — `tests/test_quote_approved_in_store_notification_smoke.py` green; production DB has 6 rows under this kind (smoke residue from earlier development with `actor_user_id` NULLed by FK SET NULL — minor cleanup-SQL gap, not a wiring problem).
- [x] Naming-boundary invariants hold — `tests/test_notification_kind_naming_boundary_smoke.py` green.

**Browser path** (operator-side per [project_vps_no_headless_chromium](../../.claude/projects/-home-luis-bellas-xv/memory/project_vps_no_headless_chromium.md)):

- [ ] Sales PIN login lands on `RepDashboard`.
- [ ] Lock/Switch clears only this tablet session; the same user on another device stays signed in.
- [ ] Idle lock fires at the configured timeout; manual lock is distinguishable in the PIN screen copy.
- [ ] Admin sees the created contact, appointment, event, activity row, and notification event for every sales action through the admin UI.
- [ ] Admin search still returns full operational context.
- [ ] `Mine only` on today's appointments filters correctly once assignments exist.

These six remain pending operator browser-verification. The underlying code paths are confirmed green by the smokes above; what's left is a user-experience pass that no CLI agent can perform.

### 9.6 Smoke residue cleanup follow-up

- [x] Cleanup SQL hardened in `4e4a3df`. `scripts/cleanup_admin_smoke_pollution.sql` now deletes `staff_notification_events` tied to smoke-created events and appointments by `subject_kind` + `subject_id`, not only by actor/user linkage. This closes the future-run residue gap where `actor_user_id` can be nulled before notification rows are swept.

- [ ] Existing production residue: six stale `quote.approved_in_store` `staff_notification_events` rows remain from earlier smoke/test activity. They predate the cleanup hardening because their parent events were already deleted. Remove with the one-shot SQL from commit `4e4a3df` when the operator is ready.

- [ ] `journalctl -u bellas-xv-api -n 200` remains operator-gated until the deploy user has `adm` / `systemd-journal` access or the command is run with sudo.

- [ ] Browser-only verification remains operator-side: sales dashboard Add Walk-In, sales assignment chip/dialog, cascade preview, admin notes activity timeline, in-store quote approval event, and final cross-surface sanity pass.

---

## Phase 10: Participant buyer journeys in the pipeline

New product requirement captured 2026-05-18: event participants should be able to become first-class buyer journeys in the pipeline. A `chambelan`, `dama`, parent, or other participant can be a customer buying from Bellas, not merely a row on the celebrant's event detail.

Status after commits `05922cd` through `c7f32af`: the Phase 10 foundation, admin buyer-journey read surfaces, and admin + sales appointment tagging UIs are built. `event_participant_id` exists on appointments, quotes, and invoices; shared tagging routes and audit rows exist; pipeline board cards expose `named_buyer_count`; admin pipeline cards show a buyer-count chip; event quick-view and Overview surfaces show per-buyer journeys; admin tags appointments from the Event Overview Booking row chip; and sales tags appointments from the dedicated sales appointment-detail page. Remaining work is browser verification on both subdomains and optional quote/invoice-tag ergonomics if a workflow needs a direct UI (API path is live today).

Production note: migration 079 was applied on the VPS database as of the 2026-05-18 verification pass. `/api/health` reports `migrations_applied: 79`.

### 10.1 Product decisions before code

- [x] Decide whether pipeline should render participant buyer journeys as separate cards, nested cards under the shared quince event, or both depending on view density. First pass: keep one event card, show a named-buyer count chip, and expose per-buyer detail in quick-view/event detail instead of duplicating event cards.
- [x] Decide whether the pipeline status lane represents the shared event's status, the participant buyer journey's status, or a combined/derived state. First pass: lane remains the shared event status; participant journey state is represented by tagged buyer rows.
- [x] Decide how quotes/invoices should attach when the buyer is a participant. Chosen: tag quotes/invoices directly with `event_participant_id` while preserving the shared `event_id`.
- [x] Decide whether sales can create a participant buyer journey from global lead search, from event detail only, or from both. First pass: sales can tag from appointment detail after opening the linked event/appointment context; one-shot creation from search/event detail is deferred.
- [ ] Decide admin controls for merging, retiring, or correcting participant buyer journeys.

### 10.2 Data model direction

- [x] Keep one shared quince event as the party container. Do not create duplicate quince events for each court member unless the business intentionally wants separate celebration pipelines.
- [x] Model named participants as actual people already linked through `event_participants.contact_id`.
- [x] Add a way for buyer rows to identify the specific participant buyer they belong to. Migration 079 adds nullable `event_participant_id` FKs to `appointments`, `quotes`, and `invoices`, while appointments keep `crm_event_id` and quotes/invoices keep `event_id`.
- [x] Keep `events.court_size` as the planned/estimated court size. Do not auto-overwrite it when a participant is added. Display planned court size separately from named buyer count.

### 10.3 Shared service and API plan

- [x] Add a shared service for tagging buyer rows to an existing event participant. `services/buyer_journey.py` covers appointments, quotes, and invoices.
- [x] Admin route: tag/untag appointment buyer journey with `PATCH /api/admin/booking/appointments/{id}/participant`.
- [x] Sales route: tag/untag appointment buyer journey with `PATCH /api/sales/appointments/{id}/participant`, attendance-gated for mutation.
- [x] Shared quote route: tag/untag quote buyer journey with `PATCH /api/quotes/{id}/participant`, available to admin and sales.
- [x] Shared invoice route: tag/untag invoice buyer journey with `PATCH /api/invoices/{id}/participant`, available to admin and sales.
- [x] Support same-day flow at the data/API layer: participant came in with the celebrant; staff can tag the current appointment/quote/invoice to that participant.
- [ ] Support later-day one-shot flow: participant came in separately; staff searches the celebrant/event, creates a separate appointment, and tags it to the same event participant in one action. Current workaround is create/book first, then tag afterward.
- [x] Audit every tag/untag action with event id, participant id transition, subject id, and actor. Activity kinds: `appointment.participant_attached`, `quote.participant_attached`, `invoice.participant_attached`.

### 10.4 Pipeline/UI plan

- [x] Extend board payload with a light participant buyer journey signal. `named_buyer_count` counts distinct participants tagged by appointment, quote, or invoice.
- [x] Pipeline card shows a clear participant/buyer signal. Admin pipeline cards now render a buyer-count chip when `named_buyer_count > 0`.
- [x] Event quick view shows participant buyer journeys with per-buyer counts and links into the event context.
- [x] Full event detail remains the source of truth for the party; the current chip links users back into the event/card workflow rather than creating duplicate event records.
- [x] Sales and admin share backend service logic while keeping sales-safe fields and attendance gates.
- [x] Sales appointment detail shows a buyer-journey chip and `SalesParticipantTagDialog` for tagging/untagging the current appointment.
- [x] Admin appointment tagging lives on the Event Overview Booking row (`frontend/src/pages/event/tabs/Overview.jsx` `BookingDetail`): a `buyer: …` chip on each appointment opens the shared `ParticipantTagDialog` and saves via `adminTagAppointmentParticipant`. Admin uses the event surface rather than a dedicated `/appointments/:id` page — the event is the party container.

### 10.5 Event quick-view breakdown

- [x] `GET /api/events` / board quick-view payload includes per-participant buyer breakdown data.
- [x] Event quick-view can show participant buyer journeys from tagged appointments/quotes/invoices without loading duplicate event cards.
- [x] Smoke coverage: `tests/test_event_detail_buyer_breakdown_smoke.py`.

### 10.6 Event Overview buyer journeys

- [x] `GET /api/events/{id}` returns linked `quotes` and `invoices` alongside appointments; soft-deleted quotes/invoices are excluded from journey rows.
- [x] Event Overview tab renders a Buyer Journeys section: named participant cards plus an `Untagged` legacy/celebrant card.
- [x] Appointment rows deep-link by scrolling to the matching Booking row.
- [x] Quote/invoice rows deep-link to the Quotes/Invoices tabs with `?edit=<id>`, and the tabs consume/strip that param after opening the editor.
- [x] Smoke coverage: `tests/test_event_detail_journey_payload_smoke.py`.

### 10.7 Verification and remaining operator work

- [x] Apply migration 079 on production. Runner output: `no pending migrations (79 already applied)`.
- [x] Verify `/api/health` reports `migrations_applied: 79`.
- [x] Run registered smokes: `test_event_participant_fk_smoke.py`, `test_appointment_participant_tag_smoke.py`, `test_quote_invoice_participant_tag_smoke.py`, `test_board_named_buyer_count_smoke.py`, `test_event_detail_buyer_breakdown_smoke.py`, `test_event_detail_journey_payload_smoke.py`, and `test_events_smoke.py`.
- [ ] Browser verify admin pipeline buyer-count chip on `admin.shopbellasxv.com`.
- [ ] Browser verify admin event Overview Buyer Journeys section and quote/invoice/booking deep-links.
- [ ] Browser verify sales appointment buyer chip/dialog on `sales.shopbellasxv.com` from an on-site/geofence-allowed network.
- [ ] Browser verify quote/invoice tagging once a direct tagging UI exists; until then, API smokes cover the backend.

---

## Phase 11: Admin lead-owner reassignment

Today admin can see the event owner on the pipeline cards and the Event Overview tab, but cannot change it. The only way an event's `owner_user_id` gets set is on creation; there is no PATCH route in `api/routers/events.py` that updates the owner column, and `POST /api/admin/events` is the only place admin supplies it. Sales has the surface (`PATCH /api/sales/leads/{event_id}/assignment` + cascade preview, wired into [SalesAssignmentDialog.jsx](../frontend/src/sales/SalesAssignmentDialog.jsx)), but the route is `require_floor_access("sales")` so admin cannot reuse it. This blocks the "rotate a lead off a former employee" case the operator hit on 2026-05-18.

### 11.1 Product decisions (locked)

- [x] Cascade scope mirrors sales: future-dated appointments only (`slot_start_at >= NOW()`). Past appointments stay frozen for commission/attribution. Same as `services.sales_assignment.reassign_event_lead`.
- [x] Picker source mirrors sales: `role='sales' AND is_active=true` from `services.sales_staff.list_assignable_sales_users`. Admin users are never themselves valid owners.
- [x] `owner_user_id = None` (unassign) is a valid target. Admin can clear the owner just like sales can.
- [x] Co-ownership / multiple owners is **not** in scope. The owner column stays a single FK. Revisit only if a real workflow asks for two stylists on one lead.
- [x] Admin per-appointment assignment parity stays **deferred** (2026-05-18 decision). Admin keeps the event-owner reassignment pattern; this Phase 11 slice closes that single gap without expanding it.

### 11.2 Backend

- [x] `PATCH /api/admin/events/{event_id}/owner` in new [api/routers/admin_events.py](../api/routers/admin_events.py) mounted at `/api/admin/events` in [api/server.py](../api/server.py). Auth: `require_admin_scope`. No floor gate (admin is not geofenced per `[[project_sales_geofence]]`). Payload `{owner_user_id: int | None}`. Response: `{event_id, owner_user_id, cascaded_appointment_ids}`.
- [x] `GET /api/admin/events/{event_id}/cascade-preview`. Auth: `require_admin_scope`. Same response shape as sales' `LeadCascadePreviewResponse`.
- [x] Both delegate to `services/sales_assignment.py` (`reassign_event_lead`, `lead_cascade_preview`) — same cascade rules, audit rows, notification ordering. No duplicated logic.
- [x] Audit `reason` is now a per-call parameter on `reassign_event_lead` (defaults to `"sales_reassignment"` so existing sales callers are unchanged); the admin router passes `reason="admin_owner_change"`.
- [x] `/api/sales/staff/assignable` relaxed from `require_sales_scope` to `require_any_scope("admin", "sales")`. Response shape and `sales_staff.list_assignable_sales_users` filter unchanged. `tests/test_sales_assignment_smoke.py` updated to expect 200 for admin tokens (with admin still excluded from the picker rows).

### 11.3 Frontend

- [x] [frontend/src/components/AdminEventOwnerDialog.jsx](../frontend/src/components/AdminEventOwnerDialog.jsx). Lead-scope only. Renders current-owner readout, staff picker with "Unassigned" option, cascade preview of future appointments with current assignee shown, and a "Past appointments stay frozen for attribution" hint.
- [x] Edit affordance on [frontend/src/pages/event/tabs/Overview.jsx](../frontend/src/pages/event/tabs/Overview.jsx) next to the "Owner" KV — a small `Change` button (matches the Primary contact section's `Edit` pattern).
- [x] API client helpers in [frontend/src/services/api.js](../frontend/src/services/api.js): `adminReassignEventOwner(eventId, ownerUserId)` and `adminGetOwnerCascadePreview(eventId)`. Reuses the existing `salesListAssignableStaff` helper.
- [x] On success, invalidates `['event', eventId]` and `['events', 'board']` so the Owner KV, pipeline owner display, and per-appointment assignee chips all refresh.
- [x] Error handling maps: `event_not_found` (404) → "This event no longer exists. Reload and try again." `invalid_assigned_user_id` (400) → "Pick an active sales stylist."

### 11.4 Audit & notifications

- [x] One `EVENT_REASSIGNED` activity row with payload `{from_user_id, to_user_id, reason: "admin_owner_change"}`.
- [x] Per cascaded appointment: one `APPOINTMENT_REASSIGNED` row with `{from_user_id, to_user_id, reason: "admin_owner_change", via: "lead_cascade"}`.
- [x] Notification cascade: existing `notify_booking_cancelled` (to previous assignee) + `notify_booking_assigned` (to new assignee) per cascaded appointment. No new notification kind. The cancel-then-assign ordering is preserved by the shared service.

### 11.5 Smoke

- [x] [tests/test_admin_lead_reassignment_smoke.py](../tests/test_admin_lead_reassignment_smoke.py) registered in [scripts/smoke_handoff.sh](../scripts/smoke_handoff.sh). Coverage:
  - cascade-preview returns exactly the two future appts of the target event;
  - `PATCH owner` moves event.owner to B; future appts→B (including a previously-unassigned future appt); past appt frozen at A;
  - an unrelated event under the same owner is **not** touched;
  - `activity_log` carries one `event.reassigned` row + one `appointment.reassigned` per cascaded appt, all with `reason: "admin_owner_change"`, `actor_user_id` = admin, `via: "lead_cascade"` on the cascade rows;
  - idempotent re-PATCH (B → B) returns empty `cascaded_appointment_ids` and writes no new audit rows;
  - sales token rejected on both admin routes (403); admin can read `/api/sales/staff/assignable` (Phase 11 relaxation); non-existent event → 404; invalid assignee (admin id) → 400; unassign (null) succeeds.
- [x] New `Admin Owner Reassign Smoke %` display-name family + `admin-owner-reassign-%@example.com` email family added to [scripts/cleanup_admin_smoke_pollution.sql](../scripts/cleanup_admin_smoke_pollution.sql) (both the contacts filter and the events filter).
- [x] Existing sales smokes (`test_sales_lead_reassignment_cascade_smoke.py`, `test_sales_lead_cascade_preview_smoke.py`, `test_sales_assignment_smoke.py`) re-run and pass; the only change to sales coverage is the picker-auth assertion update noted in 11.2.

### 11.6 Service rename (deferred trigger)

- [ ] `services/sales_assignment.py` will have two consumers (sales router + admin router) once this ships. The capability-map punch list item 4 calls for a rename to `services/assignment_service.py` once admin uses it. **Defer the rename to a follow-up commit** to keep this slice readable; the rename is mechanical (LSP find-replace across ~25 import sites + `SalesAssignmentError` → `AssignmentError`).

### 11.7 Browser verification

- [ ] Admin sees `Owner: <name> [Edit]` on event Overview.
- [ ] Edit opens dialog with current owner pre-selected; the picker excludes admin-role users.
- [ ] Selecting a different sales user populates the cascade preview list with future appointments only.
- [ ] Save writes the change; toast confirms; Overview, pipeline card owner, and per-appointment assignee chips all refresh.
- [ ] Activity timeline shows the `EVENT_REASSIGNED` row plus per-cascade `APPOINTMENT_REASSIGNED` rows.
- [ ] Setting owner to "Unassigned" succeeds and clears the owner field.

### 11.8 Out of scope

- Co-ownership / multiple owners per event.
- Admin per-appointment assignment parity (still deferred 2026-05-18).
- A general `PATCH /api/admin/events/{event_id}` for arbitrary field edits. This slice adds one targeted route for the owner column specifically; broader admin event editing is a separate question.

---

## Phase 12: Staff Profile password actions

Product input captured 2026-05-18: the Staff Profile modal's "Access & security" section should be actionable for admin-password users. This is not a sales PIN feature; PIN reset remains sales-staff-specific.

Due-diligence audit before adding this phase: this request is not greenfield. The backend and first-pass Staff Profiles UI already exist:

- `POST /api/admin/me/change-password` in [api/routers/admin_me.py](../api/routers/admin_me.py).
- `POST /api/admin/staff/{id}/send-password-reset` in [api/routers/admin_staff.py](../api/routers/admin_staff.py).
- Shared reset-token flow in [services/password_reset.py](../services/password_reset.py), including `request_reset_for_user`.
- Frontend helpers `changeOwnAdminPassword` and `sendStaffPasswordReset` in [frontend/src/services/api.js](../frontend/src/services/api.js).
- Staff Profiles modal already renders `Change Password` for the current admin and `Send Password Reset Link` for another admin.
- Smoke coverage exists in [tests/test_admin_password_management_smoke.py](../tests/test_admin_password_management_smoke.py).

### 12.1 Security rules

- [x] Self-service password change verifies `current_password` before changing `hashed_password`.
- [x] Self-service password change bumps `users.token_version` and issues a fresh admin session cookie so old sessions are revoked while the current browser stays usable.
- [x] Authenticated reset trigger requires admin scope.
- [x] Authenticated reset trigger only targets active admin users; sales users still use PIN reset flows.
- [x] Reset trigger reuses the public forgot-password token/email path instead of inventing a separate admin-only token.
- [x] Self-service password change dispatches the same "your password was changed" tripwire email as the reset-confirm path. Decision (2026-05-18): mirror the reset path's direct-SMTP pattern via a new `services.password_reset.notify_password_changed` public wrapper, not `notification_routing.record_event`. The infra-routing entry exists for the dev `send_test_emails.py` only; both production password paths intentionally stay on direct-SMTP for consistency. Asymmetric-tripwire risk closed: a hijacked admin session that flips the password now also pings the user out-of-band.

### 12.2 UI rules

- [x] In Staff Profiles, editing your own admin profile shows `Change Password`.
- [x] Editing another active admin profile shows `Send Password Reset Link`.
- [x] Sales users keep the PIN management UI; password actions do not appear for sales profiles.
- [x] Change Password opens a sub-dialog with current password, new password, and confirm password.
- [x] Reset trigger disables while sending and shows a success message.
- [ ] Browser verify the modal states on `/settings/staff/profiles` for: own admin profile, another admin profile, and a sales profile.
- [ ] Browser verify the current session remains usable after a successful self-service password change.

### 12.3 Validation

- [x] `venv/bin/python tests/test_admin_password_management_smoke.py` passes. Phase 12.1 extended the smoke to assert the self-service tripwire email is dispatched (subject, recipient, body marker).
- [x] `cd frontend && npm run lint` clean (1 pre-existing CommandPaletteContext warning, unrelated).
- [x] `cd frontend && npm run build` clean.
- [ ] If frontend copy or dialog behavior changes, keep the smoke plus add focused component/UI coverage only if the repo has an established pattern for this modal. (No FE changes in Phase 12 — backend-only tripwire wire-up.)

---

## Phase 13: ClockScreen kiosk-card UI polish

Product input captured 2026-05-18: the sales clock screen should feel like a single modern kiosk punch card, not three separate form cards. Keep this as a UI refactor only unless a real bug is found in geofence, selfie, or punch submission behavior.

### 13.1 Design direction

- [x] Wraps the clock interface in a centered `Box` with `maxWidth: 460, mx: 'auto'`. Sits inside `SalesLayout`'s existing 720-wide center, so the card itself caps at ~460px on desktop.
- [x] Status indicator is a filled `Chip` regardless of state (was previously `variant="outlined"` for the off-clock case). Visually static; never reads as a tap target.
- [x] Attendance, Selfie, trusted-network, and GPS sections all live inside one `Card` / `CardContent` / `Stack`. The four prior outlined cards collapsed into one.
- [x] Selfie action button uses `fullWidth variant="outlined"` so it reads as a full-width secondary action inside the main card.
- [x] GPS status moved to a compact inline `Stack` row directly above the primary punch button, separated only by a thin `borderTop`.
- [x] `CircularProgress size={14}` shows alongside the GPS text while resolving; "Improving location · ±Nm so far" copy preserved when `coordsProgress` is set.
- [x] Primary punch button is `fullWidth size="large"` with `py: 1.75, fontSize: '1.1rem', fontWeight: 600` — visually dominant.
- [x] Button label = `Waiting for location…` when disabled only because GPS isn't ready (computed via `waitingForLocation = !submitting && !gpsReady`). When the only blocker is a missing required selfie, the label stays as the action verb so the selfie label above carries the explanation.
- [x] Camera preview, retake (`ReplayIcon` button), permission-error (`cameraError` alert), GPS-error (`coordsError` alert), trusted-network row, and selfie-required behavior all preserved byte-for-byte in handler logic; only the wrapper presentation changed.

### 13.2 Implementation guardrails

- [x] No changes to attendance, geofence, trusted-network, selfie storage, or punch API call sites. `handleSubmit`, `salesPunchIn`/`salesPunchOut`, `sampleBestPosition`, `describeGateError`, and all `useState` blocks are untouched.
- [x] Detailed GPS/selfie error messages preserved inside the unified card: `coordsError` renders as a `severity="warning"` alert below the GPS row; `cameraError` renders inside the selfie section; `submitError` renders just above the primary button.
- [x] Mobile-first ergonomics preserved: card spacing is `Stack spacing={2}`, no marketing-style padding; full-width buttons.
- [x] Accessibility preserved: primary action is a real `<Button>`; retry-location and retake-selfie remain reachable `IconButton`s with `aria-label`s.

### 13.3 Validation

- [x] `cd frontend && npm run lint` clean (1 pre-existing CommandPaletteContext warning, unrelated).
- [x] `cd frontend && npm run build` clean.
- [ ] Browser verify desktop layout is a single centered card, not full-width cards. (Operator-side per `[[project_vps_no_headless_chromium]]`.)
- [ ] Browser verify mobile/tablet layout still fits without text overlap.
- [ ] Browser verify disabled copy changes to `Waiting for location…` while GPS is pending.
- [ ] Browser verify selfie-required, selfie-optional, GPS-denied, and trusted-network states still render correctly.

---

## Out of scope for this plan

- SMS notifications on assignment (no SMS infra; see [SALES_PORTAL_PHASES.md](SALES_PORTAL_PHASES.md)).
- Payment capture from the dashboard.
- Native app / offline mode / PWA install.
- A dedicated `leads` table. CRM doctrine stays: lead = `event` with `status='lead'`.
- Multi-tenant DB partitioning of search results; see [project_white_label_per_tenant_deployment](../../.claude/projects/-home-luis-bellas-xv/memory/project_white_label_per_tenant_deployment.md).
