# Sales Portal - Phased Plan

A separate sales-staff surface at `sales.shopbellasxv.com` that lets stylists prepare for appointments, log outcomes, build and sign quotes on the spot, add party participants, and clock in/out under a geofence. The admin app at `admin.shopbellasxv.com` stays the source of truth for business config, pipeline-wide reporting, and payments. Sales staff do not log into admin.

## Goal

Stylists open `sales.shopbellasxv.com` on a phone or iPad, type a PIN, clock in (geofenced), and see only what they need to run today's floor: today's appointments, drill-down detail, status quick-actions, dress try-on log, on-the-spot quote and signature capture, invoice conversion, and a guided "add participant" flow. Schedule and time-off live behind the same PIN. The admin app sees everything sales does (notes, status changes, signatures, attendance) through the existing activity log so the owner has one timeline.

The boundary is **role**, not feature. Sales has its own subdomain, its own login (PIN, no password), and its own JWT scope. Every backend endpoint a sales token can hit lives under `/api/sales/*` or is dual-scoped explicitly. Admin endpoints stay closed to sales tokens regardless of which subdomain calls them.

## Decisions To Lock

- **Subdomain**: `sales.shopbellasxv.com`. New nginx vhost, new Let's Encrypt cert, new entry in `CORS_ORIGINS`. The API itself stays at `api.shopbellasxv.com`.
- **One React bundle, two vhosts**: `sales.shopbellasxv.com` and `admin.shopbellasxv.com` both serve `frontend/dist/`. App boot reads `window.location.hostname` and mounts either the admin router or the sales router. Reasoning: zero npm-project duplication, sales can `import` admin components (QuoteEditor, InvoiceEditor, SignatureDialog) directly. Admin bundle weight on the sales subdomain is mitigated by lazy-loading admin routes at the route level, not by code splitting the build.
- **Auth reuses the `users` table**. Add `pin_hash`, `pin_failed_count`, `pin_locked_until`, `last_pin_used_at` columns. A sales staff member is a `users` row with `role='sales'` and a `pin_hash` populated. No separate `sales_staff` table.
- **PIN is 6 digits**. 4-digit PINs (10k space) are brute-forceable inside a lockout window; 6 digits (1M space) is the floor. Hash with bcrypt at the same cost factor as passwords.
- **PIN login is kiosk-style: tap name, then PIN**. The public picker returns active sales staff display names and usernames, but never `users.id`. The stylist taps their name, then enters a 6-digit PIN; the UI submits the selected username as `{identifier, pin}` behind the scenes. Username typing exists only as a fallback if the picker cannot load. Keep nginx rate-limiting and row-level lockouts on `/api/sales/auth/pin` because the picker makes target discovery easier.
- **Lockout is row-level**. After 5 failed attempts within 15 minutes, set `pin_locked_until = now() + 15 minutes` and require an owner unlock through admin if the user trips lockout twice in 24 hours. nginx-level rate limiting on `/api/sales/auth/pin` is additive, not a substitute.
- **JWT scope claim is the gate**. Admin tokens carry `scope='admin'`, sales tokens carry `scope='sales'`. `require_sales_scope` rejects non-sales tokens; `require_admin_scope` rejects sales tokens on admin routes; both reject tokens missing or carrying an unknown scope outright (401, no grace path). Migration 052 bumped `users.token_version` for every row, so every pre-cutover token is dead on arrival and admins re-logged in once.
- **Scope audit is mandatory before any sales token ships**. Current admin auth is mostly role checks and protected routers, not scoped JWTs. Phase 0 must produce the router/dependency map first; Phase 1 is not done until sales tokens receive 403s from every admin-only router and admin tokens are rejected from sales-only routes.
- **No silent scope upgrades**. A sales user cannot trade a PIN token for an admin token. The owner promoting a stylist to admin is a manual `users.role` change in admin UI plus password reset; not a sales-portal feature.
- **Pipeline auto-transition is a single composite handler**. Marking an appointment "arrived" runs three steps in one transaction: set `appointments.status='attended'` and stamp `attended_at`; if `crm_event_id` is null, auto-promote via `services/event_service.py`; if the event is in `lead`, transition it to `consulted` via `change_event_status` so `event_status_change_events` and `activity_log` both get written. "No-show" stamps `no_show_at` and does not touch the event status. "Cancelled" stamps `cancelled_at` and does not touch the event status.
- **Dress try-on is a new join table**, not a JSONB blob on appointments. Schema below. This keeps the catalog FK enforced and makes "what styles did Maria show on Tuesday" a one-line query.
- **Add-participant is a global flow, not a sales-only one.** Principle: every person who might buy a dress is their own contact (own profile, own measurements, own activity history, own quote/invoice history). The link from a contact to a specific party lives in one `event_participants` row with `contact_id` always populated; no code path creates a participant without a contact. Both surfaces share the same UX (parent name, celebrant name, contact info, party size lifted from the booking widget) rendered as an in-app MUI modal: stylists open it from the sales appointment detail, owners open it from the admin event Overview "Participants" section. Both call the same endpoint, reuse `services/contact_service.py:find_or_create_contact`, and write one `event_participants` row. The endpoint is scope-neutral by design (or aliased) so the planned `require_sales_scope` dependency does not lock admin out of its own write path. A follow-up migration tightens `event_participants.contact_id` to NOT NULL once any legacy orphan rows are backfilled.
- **Quote signing reuses existing schema and endpoint**. `quotes.signature_base64`, `signature_signed_at`, `signature_ip`, and `signature_name` already exist. `POST /api/quotes/{id}/approve-in-store` and the frontend `approveQuoteInStore` helper already exist; extend that path with `signature_user_agent VARCHAR(255)` instead of inventing a parallel signing endpoint. The existing `SignatureDialog.jsx` canvas component is the capture UI.
- **Geofence is server-side and circular**. Schema stores `(lat, lng, radius_m)` per location. The clock-in handler computes haversine distance against every active location and accepts the punch only if at least one is within radius. Client-supplied coordinates are inputs, never authority. The OpenHRApp 0.005-degree client bounding box is not the model; only its conceptual schema is.
- **Selfie on clock-in is configurable from day one**. Owner chooses `required`, `optional`, or `disabled` in admin settings. Recommendation for Bellas is `required`, but this is privacy-sensitive enough that it should not be hard-coded. Storage at `/var/lib/bellas-xv/uploads/clockin/<staff_id>/<punch_id>.webp`. The systemd unit override gets a new `ReadWritePaths` entry before the migration ships, not after.
- **Clock-in gates appointment operations, not the whole sales portal**. If punched out, a stylist can still see schedule, request time off, change PIN, and sign out. Today's appointment list/detail, appointment notes, tried-on logging, quotes, invoices, and participant creation require an active punch unless the owner disables the attendance gate.
- **Forgotten clock-outs auto-close server-side with explicit audit columns**. Stylists will forget to clock out. The system should close stale open punches from a server cron using shift/location `auto_session_close_time`, but it must mark `auto_closed = true` and `auto_close_reason IN ('past_date', 'max_time_reached')` instead of silently filling a normal out time. The sales and owner UIs surface "System closed, confirm hours" until an owner/stylist review confirms or adjusts the row.
- **Missed-punch correction is a workflow, not an owner side-channel**. Auto-close creates a review item, but stylists also need a way to say "I forgot to clock out; I actually left at 6:15." Add a structured correction request path with proposed times, reason, owner decision, before/after values, and an audit trail. Do not rely on texts, verbal requests, or editing free-text notes.
- **Attendance edits require before/after audit rows**. Any system auto-close, owner manual adjustment, void, or approved correction writes an append-only audit row with actor (`system` or user id), reason code, old values, new values, timestamp, and related punch/session id. Punch rows store current state; audit rows explain how they got there.
- **Attendance crons use business-local time, never raw UTC comparisons**. The VPS runs UTC, but boutique attendance rules are local business rules. Add a `to_business_local(dt)` / `business_now()` helper backed by `APP_TIMEZONE`, and use it in every attendance cron and shift comparison. Smoke tests must freeze time around local midnight and DST boundaries.
- **Selfies are converted to WebP before upload and retained by policy**. Native phone images can be huge. The sales UI converts camera captures to bounded WebP before upload, strips EXIF, and the backend enforces size/type limits. A retention job deletes old selfie files according to the owner setting while preserving punch metadata.
- **Shift windows include early, late, and runaway-session guardrails**. Staff shifts carry `late_grace_period_minutes`, `earliest_check_in_minutes`, `early_out_grace_minutes`, `auto_session_close_time`, `max_session_hours`, and `working_days`. This prevents gaming overtime by clocking in hours early, reports late/early-out cleanly, gives auto-close a deterministic local-time cutoff, and catches impossible 20-hour sessions.
- **Auto-close reminders precede auto-close**. Before auto-close, send an in-app/email reminder such as "Still working? Clock out or extend." If the stylist taps "still working", the system records an extension/ack and leaves the session open for owner review instead of closing blindly at the cutoff.
- **Holiday calendar is advisory, not blocking**. Bellas may work Saturdays and holidays. Attendance rows can link to `holiday_id` for future reporting/payroll multipliers, but holidays do not block clock-in/out.
- **Temporary shift overrides are first-class**. One-off schedule changes should not mutate the base shift. Add shift overrides for "Maria is covering Saturday 11-5 this week" so attendance rules, reminders, and auto-close resolve the override first, then assigned shift, then location/default policy.
- **Cron health is observable**. Every attendance cron records last-run timestamp, rows scanned, rows changed, and errors. Admin gets a visible warning if auto-close, reminders, or retention have not run within the expected window. A missing cron should fail smoke tests, not be discovered by payroll.
- **Time-off approval is single-tier**. Owner approves or denies. Schema can carry an optional `manager_user_id` so a future two-tier flow does not need a migration.
- **No SMS in v1**. The Twilio transport is still no-op. Sales-portal notifications (quote signed, time-off approved, etc.) go to email or stay in-app. SMS is its own project.
- **No payment capture in the sales portal in v1**. Quotes get signed, invoices get created and sent, but the customer pays through the existing `/portal` link or in person. Embedding payment capture in sales is a Phase-99 question, not a v1 feature.
- **Do not auto-mark an event sold when an invoice is merely sent**. `sold` should mean real customer commitment: deposit/payment recorded, or a manual owner/stylist action if Bellas decides signed invoice equals sale. In v1, keep `sold` manual unless payment capture/recording creates a clear server-side signal.
- **Sales sees all of today's appointments by default**, with a "show only mine" toggle that filters by `appointments.assigned_user_id`. Reasoning: a small floor team needs visibility on every walk-in; assignment can be informal. If team size grows past 5 the default flips.
- **Activity log actor**: every write a sales user makes records `actor_user_id = sales_user.id` and `actor_kind = 'staff'`. The owner's admin timeline shows "Maria marked arrived", not "system marked arrived".
- **No native app, no offline mode, no PWA install**. Mobile web only. Geolocation and camera work in iOS/Android Safari. Service workers, install prompts, and background sync are out of scope.

## Open Decisions

These need an answer before Phase 1 starts. Recommendations are mine; push back if any are wrong.

### Selfie policy

OpenHRApp captures a selfie on punch as a buddy-punching deterrent. Required adds friction; optional means a stylist can hand the iPad to a friend.

Recommendation: **required**, but implemented as an owner setting with three values: `required`, `optional`, `disabled`. A "skip" path should record an explicit reason and be visible in the attendance audit. If Bellas later runs a trusted kiosk-mode device, that device can be configured to skip the selfie while keeping GPS required.

### Multiple boutique locations

Schema supports many `staff_locations`. v1 product question: does Bellas have one location now, and if so should the geofence schema still be per-location-array-of-one, or hardcode the singleton until expansion is real?

Recommendation: **per-location array even with one row**. The query is `WHERE active=true`; the migration is the same; future-you does not need to refactor.

### Sales user PIN reset path

If a stylist forgets their PIN, who resets it and how?

Recommendation: **owner resets in admin UI**. Owner clicks a button that mints a new 6-digit PIN, displays it once, marks it as `force_change_on_next_login = true`. Stylist enters that PIN, is then prompted to choose their own. SMS reset is out (no SMS infra) and email reset has the wrong threat model (a stylist could lock out a coworker via inbox compromise).

### Sales staff seeing other stylists' notes

When Stylist A drills into an appointment Stylist B prepped, can A see B's notes?

Recommendation: **yes, all internal notes are shared**. The whole point of the activity log is shared visibility. A "private to me" notes feature is a different product.

### What counts as sold

Should the sales portal move an event from `consulted` to `sold`, and if so when?

Recommendation: **not on invoice send**. Sending an invoice is a request for commitment, not proof of commitment. Move to `sold` on recorded deposit/payment, or keep it manual in v1. If Bellas decides a signed quote or sent invoice is operationally enough to mean sold, add that as an explicit owner-configurable rule later.

## OpenHRApp Lessons To Borrow And Avoid

OpenHRApp is useful as a product reference, not as an implementation template. Its stack is different (PocketBase/SQLite + React/TS), and the Bellas app already has Postgres, FastAPI services, role-based admin flows, activity logs, quote/invoice infrastructure, and existing document storage patterns. The right move is to borrow lessons, not code.

Borrow these ideas:

- **Attendance as a first-class daily workflow**. OpenHRApp puts clock-in/out in quick actions and makes the current session state obvious. Bellas should do the same: after PIN login, show a clear clock state and make "Clock In", "Clock Out", "Today's Appointments", "Schedule", and "Time Off" the obvious actions.
- **Selfie plus GPS as deterrence, not absolute security**. Selfie capture helps prevent buddy punching; GPS verifies the device is near the boutique. Neither proves identity/location against a determined attacker, so the owner-facing audit trail matters.
- **Shift grace periods and attendance windows**. OpenHRApp computes present/late from shift start plus grace period and uses shift-level auto-close times. Bellas should include late grace, earliest check-in, early-out grace, working days, and auto-close time so the attendance model matches how people actually work.
- **Clear leave/time-off status labels**. OpenHRApp's `PENDING_MANAGER`, `PENDING_HR`, `APPROVED`, `REJECTED` pattern makes it obvious where a request is stuck. Bellas v1 is single-tier, but the UI should still show `pending`, `approved`, `denied`, and `cancelled` clearly, with `decided_by_user_id`, `decided_at`, and decision notes.
- **Attendance audit tools for owners**. OpenHRApp has admin audit concepts: filter by person/date/status, see location, inspect selfie, edit records, and export reports. Bellas Phase 7 should include owner visibility from the start, not just staff punching.
- **Forgotten check-out handling needs product design**. OpenHRApp's own notes show auto-close caused surprises when it ran from multiple layers, but no auto-close at all leaves open sessions behind. Bellas should auto-close server-side with explicit `auto_closed` fields and a visible "confirm hours" workflow, never with a silent normal-looking out time.
- **Missed-punch corrections and manager review queue**. OpenHRApp's standards doc calls out the gap: auto-closed or missing-punch records should not disappear into payroll. Bellas should have a queue for auto-closed sessions and correction requests, with owner approval before finalizing disputed hours.
- **Before/after audit trail**. OpenHRApp identifies that changing check-out in place loses the original state. Bellas should use append-only attendance audit rows for every system or human change.
- **Pre-close reminders and "still working?" control**. A reminder before cutoff reduces disputes. Bellas should send a pre-close notice and let the stylist explicitly extend/acknowledge that they are still working.
- **Max-hours runaway protection**. A fixed close time is not enough for odd shifts or forgotten overnight sessions. Bellas should also have a shift/location `max_session_hours` fallback.
- **Cron health checks**. OpenHRApp's standards call out cron failure as a single point of failure. Bellas should expose last-run health and fail smoke tests if the cron entrypoint is missing.
- **Business-local cron time matters**. OpenHRApp needed a local-time helper because UTC comparisons against local shift cutoffs broke around day boundaries and DST. Bellas must centralize `APP_TIMEZONE` conversion for all attendance cron and shift logic.
- **Selfie storage retention and WebP conversion**. OpenHRApp includes storage management thinking for old selfies and converts captures down before upload. Bellas should convert to bounded WebP, strip EXIF, and keep a retention setting such as `90`, `180`, `365` days, or `forever`, while preserving punch metadata.
- **Holiday calendar as metadata**. Holidays are useful reporting context, not a reason to block work. Bellas should tag punches with `holiday_id` when applicable and leave policy/payroll interpretation for reporting.
- **Temporary shift overrides**. OpenHRApp separates base shifts from temporary overrides. Bellas should do the same so one-off coverage does not corrupt recurring schedule definitions.
- **Location permission UX**. OpenHRApp has useful platform-specific copy and a high-accuracy-to-network fallback. Bellas should give clear iOS/Android/Safari guidance, retry with lower accuracy on timeout, capture `client_accuracy_m`, and show a "retry location" control.
- **Notification preferences**. OpenHRApp supports org-level notification enablement, quiet hours, and per-user muted types/digest frequency. Bellas can start simpler, but quiet hours apply only to informational notifications. Safety-critical attendance alerts, especially "still working? auto-close soon" and correction-decision notices, bypass quiet hours because suppressing them can cost confirmed hours.
- **Fast check-in matters more than rich upload fidelity**. OpenHRApp's scaling notes call out selfie upload on the critical check-in path as a rush-hour slowdown. Bellas should create the punch row first and attach the selfie after, or at least keep the endpoint small and observable.

Avoid these patterns:

- **No client-side geofence authority**. OpenHRApp's client-side location/bounding-box approach is not enough. Bellas stores raw coordinates but accepts/rejects punches only through a server-side haversine check against active locations.
- **No full-list attendance reads**. OpenHRApp's scaling plan flags `getFullList`-style unbounded reads as a major bottleneck. Bellas endpoints must be date-ranged and indexed from the start: today, current pay period, or explicit date range.
- **No cron jobs on peak minute boundaries**. OpenHRApp hit contention from every-minute attendance jobs. Bellas should avoid attendance cron work during boutique opening/closing rush windows and offset scheduled jobs away from `:00`.
- **No automatic writes from read paths**. Reading "current attendance status" must not auto-close or mutate a punch. Any reconciliation job is explicit, server-side, logged, and feature-flagged.
- **No free-text-only system actions**. OpenHRApp used remarks to encode auto-close meaning. Bellas should store structured columns first (`auto_closed`, `auto_close_reason`, confirmation status), with notes as supporting context.
- **No offline/PWA queue in v1**. OpenHRApp added sync queues for unreliable check-ins. Bellas v1 is mobile web, online-only. We can borrow the lesson by making failures visible and retryable, but service workers/background sync stay out of scope.
- **No hard-delete attendance records in normal admin flows**. OpenHRApp's audit docs allow delete, but Bellas should prefer voiding with reason/audit trail so time records remain explainable.

## Phase 0: Audit Existing Surfaces

Purpose: confirm the assumptions in "Decisions To Lock" against the live codebase, document any drift, and produce a leak-path note (admin endpoints a sales token must not reach).

Tasks:

- [x] Confirm `users.role` is a free string today and decide whether to add a CHECK constraint with the new `sales` value or stay open. Existing values: `admin`, `user`. Verify no FK or trigger depends on the column.
- [x] Enumerate every router currently mounted in `api/server.py` and mark each as `admin-only`, `public`, `portal-public`, or `dual-scope`. The dependency map informs which routers need a `require_admin_scope` guard added.
- [x] Inventory every endpoint that mutates an appointment, event, contact, quote, or invoice. The sales portal will hit some of these via new `/api/sales/*` wrappers; some will be reused directly with a scope guard.
- [x] Confirm the AuthContext token storage key (`bellas_xv_token`) and decide whether the sales subdomain reuses the same key or namespaces it as `bellas_xv_sales_token`. Recommendation: separate key, so an admin logging in on the same browser does not stomp a sales session and vice versa.
- [x] Audit `services/event_service.py` for the exact transition path `lead -> consulted` and confirm it is callable from a service-layer composite handler without going through the HTTP layer. The auto-promote-and-consult handler in Phase 3 must call services, not POST to itself.
- [x] Confirm `services/contact_service.py:find_or_create_contact` is reusable as-is for the add-participant flow, or note what needs adjustment.
- [x] Confirm the `appointments.assigned_user_id` column is populated anywhere today. If it is dormant, the "show only mine" toggle in Phase 2 needs a separate assignment UI; if it is set on appointment creation, Phase 2 inherits real data.
- [x] Verify the systemd unit file path on the VPS and the current `ReadWritePaths` value, so Phase 7's storage path addition does not skip the override and ship a feature that 500s on the first selfie upload.

Deliverable: leak-path note appended to this doc before Phase 1 schema work starts. Includes the dependency-guard map and the AuthContext storage decision.

### Phase 0 Findings (2026-05-08)

#### `users.role` today

- `users.role` is `String(20)`, `nullable=False`, `server_default 'user'`. No CHECK, no FK, no trigger ([database/models.py:30](database/models.py#L30)). `users.permissions` is `JSONB` with default `'[]'::jsonb` ([database/models.py:31](database/models.py#L31)).
- Only two values in code today: `admin` and `user`. Three role checks across the API: [api/routers/catalog.py:35](api/routers/catalog.py#L35) (`require_admin` for POST/PATCH), [api/routers/search.py:39](api/routers/search.py#L39) (`require_admin` router-level), and [api/routers/event_documents.py:181](api/routers/event_documents.py#L181) (admin-extended download paths).
- Login response surfaces `role` and `permissions`. The JWT does NOT carry either today ([database/auth.py:27-35](database/auth.py#L27-L35)).
- **Decision:** add a CHECK constraint covering the closed set `('admin', 'user', 'sales')` as part of migration `052_sales_user_pin.py`. Cheap, prevents drift, fits the same migration that adds the PIN columns. Run before any code path inserts `'sales'`.

#### JWT scope claim today

- The token contains only `sub`, `tv`, `iat`, `exp` ([database/auth.py:27-35](database/auth.py#L27-L35)).
- `get_current_user` returns the User row only. There is no `require_admin_scope` or `require_sales_scope` helper today; the three role checks above each do `user.role != "admin"` inline.
- **Decision (shipped in Phase 1):** `create_access_token` now mints a `scope` claim. Password login mints `scope='admin'`; PIN login mints `scope='sales'` via `create_sales_token`. New dependencies `require_admin_scope`, `require_sales_scope`, and `require_any_scope(...)` wrap `get_current_user_with_scope` ([database/auth.py:97](database/auth.py#L97)) and reject any token with a missing or unknown `scope` claim with a 401 — there is **no grace window**. Migration 052 bumped `users.token_version` for every row at deploy time, so every token issued before the cutover returned 401 immediately and admins re-logged in once. No code path treats unscoped tokens as admin.

#### Router map and scope classification

| Module | Prefix | Today's auth | Sales-portal classification | Notes |
|---|---|---|---|---|
| auth | `/api/auth` | public POST `/login` | public | login mints scope claim in Phase 1 |
| booking | `/api/booking` | unauthenticated | public | booking widget |
| portal | `/portal` + invitation paths | mixed (public via key + staff via auth) | mixed | staff invitation endpoints become sales-scope-allowed (send/resend) but DELETE stays admin-only |
| admin_booking | `/api/admin/booking` | per-route auth | admin-only | needs `require_admin_scope` |
| admin_booking_settings | `/api/admin/booking` | per-route auth | admin-only | needs `require_admin_scope` |
| events | `/api/events` | per-route auth | dual-scope | sales reads detail/activity; sales never calls `PATCH /events/{id}/status` directly |
| contacts | `/api/contacts` | per-route auth | dual-scope | sales reads/patches contact detail |
| catalog | `/api/catalog` | per-route + admin gate on POST/PATCH | mixed | sales gets GET only; POST/PATCH stay admin |
| quotes | `/api/quotes` + `/api/events/{id}/quotes` | per-route auth | dual-scope | sales gets full CRUD except DELETE |
| invoices | `/api/invoices` + `/api/events/{id}/invoices` | per-route auth | dual-scope | sales gets CRUD except DELETE |
| event_documents | `/api/events/{id}/documents` | per-route auth | dual-scope | sales reads/uploads; admin retains the extended-paths branch |
| payments | `/api/payments` + nested | per-route auth | admin-only | no sales payment capture in v1 |
| special_orders | `/api/special-orders` + nested | per-route auth | admin-only | sales views via aggregate event detail; mutations stay admin |
| business_profile | `/api/business-profile` | per-route auth | admin-only | sales reads come through aggregate detail endpoints |
| dashboard | `/api/dashboard` | per-route auth | admin-only | sales has its own surfaces |
| search | `/api/search` | router-level admin gate | admin-only | enumeration risk |
| sales | `/api/sales` | per-route auth | sales-only | already mounted at [api/server.py:169](api/server.py#L169) |

Note on the existing `sales` router: `POST /api/sales/events/{event_id}/participants` is already in production ([api/routers/sales.py:72](api/routers/sales.py#L72)). It currently passes admin tokens because there is no scope gate. Phase 1's gate uses `require_any_scope('admin', 'sales')` for this route so admin-side participant adding (planned in Phase 6) keeps working. Phase 6 then renames it to `POST /api/events/{event_id}/participants` and the `/api/sales/...` URL becomes an alias.

#### Mutating endpoints sales must reach

- Quotes: `POST /api/events/{event_id}/quotes`, `PATCH /api/quotes/{id}`, `POST /api/quotes/{id}/send`, `POST /api/quotes/{id}/resend`, `POST /api/quotes/{id}/approve`, `POST /api/quotes/{id}/approve-in-store` ([api/routers/quotes.py:865](api/routers/quotes.py#L865) — confirmed exists), `POST /api/quotes/{id}/cancel`, `POST /api/quotes/{id}/convert` ([api/routers/quotes.py:982](api/routers/quotes.py#L982) — actual path is `/convert`, **not** `/convert-to-invoice` as the doc body claims; Phase 5 must use `/convert`), `POST /api/quotes/{id}/pdf/retry`.
- Invoices: `POST /api/events/{event_id}/invoices`, `PATCH /api/invoices/{id}`, `POST /api/invoices/{id}/send`, `POST /api/invoices/{id}/pdf/retry`.
- Contacts: `PATCH /api/contacts/{id}`.
- Sales (existing): `POST /api/sales/events/{event_id}/participants`.
- Sales (new in Phase 3): `POST /api/sales/appointments/{id}/status`, `PATCH /api/sales/appointments/{id}/notes`.
- Sales (new in Phase 4): `POST /api/sales/appointments/{id}/tried-on`, `PATCH /api/sales/tried-on/{id}`, `DELETE /api/sales/tried-on/{id}`.
- Portal invitations sales sends: `POST /api/quotes/{id}/invitations`, `POST /api/invoices/{id}/invitations`, `POST /api/{quotes|invoices}/{id}/invitations/{inv_id}/resend`. Per the resend-dispatches-side-effect rule, sales-scoped resend must actually re-fire the email, not just bump `last_resent_at`.

#### Leak-path note (sales token must NOT reach any of these)

A sales JWT issued by Phase 1's `POST /api/sales/auth/pin` must produce 403 on every request below regardless of which subdomain made it. Phase 1 smoke tests must include one negative test per row, plus matching positive tests for the dual-scope routes above.

- Catalog mutations: `POST /api/catalog`, `PATCH /api/catalog/{id}`.
- Admin booking: every `POST/PATCH/DELETE` under `/api/admin/booking`, `/api/admin/booking-settings`.
- Payments: every route under `/api/payments`, `/api/invoices/{id}/payments`, `/api/events/{id}/payments`.
- Special orders: every route under `/api/special-orders`, `/api/events/{id}/special-orders`.
- Business profile mutations: `PATCH /api/business-profile`, logo POST/DELETE.
- Dashboard: every route.
- Search: every route.
- Quotes/invoices destructive: `DELETE /api/quotes/{id}`, `DELETE /api/invoices/{id}`.
- Events destructive/transition: `POST /api/events`, `PATCH /api/events/{event_id}/status`. Sales reaches `change_event_status` only through the Phase 3 service-layer composite handler.
- Portal invitation revoke: `DELETE /api/{quotes|invoices}/{id}/invitations/{inv_id}` — destructive on an already-sent link, admin-only.

#### Frontend AuthContext storage key

- Today: `TOKEN_STORAGE_KEY = 'bellas_xv_token'` ([frontend/src/services/api.js:3](frontend/src/services/api.js#L3); used in [frontend/src/contexts/AuthContext.jsx:18](frontend/src/contexts/AuthContext.jsx#L18) and [:34](frontend/src/contexts/AuthContext.jsx#L34)).
- **Decision:** sales subdomain uses a separate key `bellas_xv_sales_token` and a separate `SalesAuthContext`. Reason: production isolates by origin (`admin.shopbellasxv.com` vs `sales.shopbellasxv.com`), but the dev override flow and any future shared origin must not let an admin sign-in stomp a sales session or vice versa. One-line cost.

#### `services/event_service.py` reuse for Phase 3

- `change_event_status(db, *, event_id, new_status, actor_user_id=None, notes=None) -> Event` at [services/event_service.py:227-283](services/event_service.py#L227-L283). Pure service: no FastAPI Depends, no Request, no implicit current_user. Writes the event row, `event_status_change_events`, and `activity_log` in one transaction.
- `lead → consulted` is a permitted transition in `event_workflow.QUINCEANERA_STATUSES`; the function validates against the workflow set, not a tighter state machine.
- `promote_appointment_to_event(db, *, appointment_id, event_type='quinceanera', overrides=None, actor_user_id=None) -> Event` at [services/event_service.py:58-136](services/event_service.py#L58-L136). Idempotent guard: raises if `appointment.crm_event_id` is already set.
- **Confirmed:** the Phase 3 composite handler calls both functions from the service layer with no HTTP round-trip. Order: `promote_appointment_to_event` (only if `crm_event_id is None`), then `change_event_status(...,'consulted', ...)` (only if the event is currently `lead`).

#### `services/contact_service.py:find_or_create_contact` reuse for Phase 6

- Signature: `find_or_create_contact(db, *, phone_e164, email, phone=None, first_name=None, last_name=None) -> Contact` at [services/contact_service.py:35-75](services/contact_service.py#L35-L75). Returns `Contact`, **not** a tuple — Phase 6 must extend the return type.
- Phone normalization is the caller's responsibility — `services.booking_service.normalize_phone_e164` ([services/booking_service.py:60-72](services/booking_service.py#L60-L72)) is canonical.
- Dedupe rule: phone-then-email-when-no-phone. Two contacts can share an email if both have phones.
- The `event_participants` insert is NOT part of this function. Caller does it; the existing seed flow does the insert in [services/contact_service.py:202-210](services/contact_service.py#L202-L210).
- **Phase 6 changes:** (a) extend `find_or_create_contact` to return `(contact, was_new)`; (b) delete `_lookup_existing_contact` from [api/routers/sales.py:155-167](api/routers/sales.py#L155-L167) and consume the tuple directly; (c) tighten `event_participants.contact_id` to NOT NULL after backfilling synthetic contacts for any historical orphan rows.

#### `appointments.assigned_user_id` population

- Column defined at [database/models.py:105](database/models.py#L105). Created in [database/migrations/005_create_appointments.py:30](database/migrations/005_create_appointments.py#L30).
- **Status: dormant.** No code path writes to it. No frontend control sets it. Confirmed by repo-wide grep across `api/`, `services/`, `frontend/src/`.
- **Implication for Phase 2:** the "show only mine" toggle filters on a column that is empty today. Ship the toggle anyway — it becomes meaningful as soon as Phase 8's owner-side shift assignment surface lands (or earlier if Phase 2 adds a small "claim this appointment" button; logged as Phase 2 open question).

#### Systemd unit + ReadWritePaths

- Unit name: `bellas-xv-api.service`. Override path on VPS: `/etc/systemd/system/bellas-xv-api.service.d/override.conf`. The override carries memory caps and restart limits.
- The `ReadWritePaths` line lives in the base unit at `/etc/systemd/system/bellas-xv-api.service` (or `/lib/systemd/system/bellas-xv-api.service`). **Not in repo.** Prior phases (event documents, invoice PDFs, quote PDFs, receipts) write under `/var/lib/bellas-xv/uploads`.
- **Captured 2026-05-08 from the VPS** (`sudo systemctl cat bellas-xv-api.service | grep -i ReadWritePaths`):
  ```
  ReadWritePaths=/home/luis/bellas_xv/logs /var/lib/bellas-xv/uploads
  ```
  The Phase 7 selfie path `/var/lib/bellas-xv/uploads/clockin/<user_id>/<punch_id>.webp` inherits the second entry; the Slice 2B-3 retention cron's delete path is the same prefix. Re-capture this value any time the systemd unit is regenerated (rare); a forgotten regeneration is exactly the failure mode the Slice 2A `selfie_storage_unavailable` smoke catches.

#### Migration numbering

- Latest applied: `059_shifts_and_time_off.py`. Phase 1 → `052_sales_user_pin.py`. Phase 4 → `053_appointment_tried_on_items.py`. Phase 5 → `054_quote_signature_user_agent.py`. Phase 6 → `055_event_participants_contact_required.py`. Phase 7 Slice 1 → `056_clock_in.py`. Phase 7 Slice 2 → `057_business_profile_attendance_settings.py`. Phase 7 Slice 2B-3 → `058_cron_run_state.py`. Phase 8 Slice A → `059_shifts_and_time_off.py`. Numbers in this doc are current as of 2026-05-08.

#### Doc corrections to apply during Phase 5

- The doc references `POST /api/quotes/{id}/convert-to-invoice`. Real path is `POST /api/quotes/{id}/convert` ([api/routers/quotes.py:982](api/routers/quotes.py#L982)). Either update the doc body or rename the endpoint during Phase 5; renaming is the larger blast radius (frontend `services/api.js`, button copy, smoke tests). Recommendation: update the doc body to match the existing endpoint name.

## Phase 1: Auth Foundation And Subdomain Plumbing

Purpose: a stylist can navigate to `sales.shopbellasxv.com`, tap their name, enter a 6-digit PIN, and reach a placeholder landing page authenticated under a sales-scoped JWT. No business features yet. Everything else builds on this.

Tasks:

- [x] Migration `052_sales_user_pin.py`: add columns to `users` (`pin_hash VARCHAR(255) NULL`, `pin_failed_count INTEGER NOT NULL DEFAULT 0`, `pin_locked_until TIMESTAMPTZ NULL`, `last_pin_used_at TIMESTAMPTZ NULL`, `force_pin_change BOOLEAN NOT NULL DEFAULT false`). No backfill: existing admin users have `pin_hash = null` and cannot PIN-login.
- [x] Add `services/sales_auth.py` with `set_pin(user, pin)`, `verify_pin(user, pin)`, `is_locked(user)`, `record_failure(user)`, `record_success(user)`. PIN format validated as exactly 6 digits, no leading-zero shortcuts, no sequential patterns rejected at this layer (UI nudges, not enforced).
- [x] Add `database/auth.py` helpers: `create_sales_token(user)` mints a JWT with `scope='sales'`, `require_sales_scope` dependency, `require_admin_scope` dependency. The existing `get_current_user` does not change.
- [x] Add `api/routers/sales_auth.py`: `GET /api/sales/auth/staff-picker` returns active sales staff with minted PINs as `{username, full_name}` only (no `users.id`). `POST /api/sales/auth/pin` accepts `{identifier, pin}` where `identifier` is the selected username, returns a sales JWT or a 423 with retry-after when locked. Do not require or expose sequential `users.id` as the login handle. `GET /api/sales/auth/me` returns the staff member's display name, role, and unlock state.
- [x] Wire `require_admin_scope` onto every router that touches business config, payments, business profile, numbering, or pipeline mutations beyond the appointment status path. Sales tokens get a 403 on those routes regardless of subdomain.
- [x] Frontend: add hostname-based router boot in `frontend/src/App.jsx`. If `window.location.hostname` starts with `sales.`, mount `<SalesApp />` (new component); otherwise mount the existing admin app. Test with a `localhost?host=sales.shopbellasxv.com` query-param override or a `VITE_FORCE_SUBDOMAIN` env var so local builds can hit the sales tree without DNS.
- [x] New `SalesApp` component with its own AuthContext (`SalesAuthContext`), its own protected-route wrapper (`SalesProtectedRoute`), its own token storage key (`bellas_xv_sales_token`). The PIN entry screen is a 6-cell numeric input on mobile, no password fallback.
- [x] New `SalesLayout` component: mobile-first, no sidebar; topbar with stylist name and a sign-out button.
- [x] Owner-side admin UI to set or reset a stylist's PIN. Single screen at `/settings/sales-staff` lists `users.role='sales'`, owner clicks "set PIN" or "reset PIN", system mints a 6-digit PIN, displays it once, and stamps `force_pin_change = true`. The stylist's first PIN login redirects to a "choose your PIN" screen before the landing page.
- [x] **VPS-side**: Nginx vhost for `sales.shopbellasxv.com` serving `frontend/dist/` with the same SPA fallback as admin. Let's Encrypt cert added through certbot. (Live on the VPS; nginx config is not tracked in the repo.)
- [x] Add `https://sales.shopbellasxv.com` to `CORS_ORIGINS` in `.env` (and `.env.example`).
- [x] **VPS-side, non-optional before cutover**: Rate-limit `/api/sales/auth/pin` at nginx (burst=5, same shape as `/api/auth/login`). The row-level lockout in `services/sales_auth.py` protects against single-account guessing, but edge throttling is what shields the bcrypt verify cost and the DB write path from a coordinated attack across IPs. Deploy runbook step 4 below makes this explicit.
- [x] Smoke tests in [tests/test_sales_auth_smoke.py](tests/test_sales_auth_smoke.py): PIN mint, staff picker returns the minted stylist without exposing `id`, login round-trip, identifier privacy (unknown identifier returns the same body as wrong PIN), failure-counter increment, 5-failure lockout with `Retry-After`, owner unlock, force-PIN-change flow with old PIN invalidation, sales token 403 on admin-only routes, admin token 403 on `/api/sales/auth/me`, admin token passes the dual-gate `/api/sales/events/{id}/participants` route, admin clear-PIN.

Deliverable: a stylist with a freshly minted PIN can land on `sales.shopbellasxv.com`, tap their name, enter their PIN, see "Hello, Maria" on a placeholder page, sign out, and the owner can reset their PIN from admin.

### Phase 1 deploy runbook (VPS, 2026-05-08)

In rollout order. Each step is idempotent unless noted.

1. **Pull and rebuild**. Backend code change runs from `git pull && systemctl restart bellas-xv-api.service`. Frontend rebuild: `cd frontend && npm run build` so `frontend/dist/` carries the new SalesApp bundle.
2. **Apply migration 052**. `venv/bin/python -m database.migrations.runner`. The migration:
   - Adds the five PIN columns (`pin_hash`, `pin_failed_count`, `pin_locked_until`, `last_pin_used_at`, `force_pin_change`).
   - Adds `chk_users_role` covering `('admin', 'user', 'sales')`.
   - Adds partial index `idx_users_role_sales`.
   - **Bumps `users.token_version` for every row** — every active admin session is invalidated. Owner re-logs in with their existing email/password; the new login mints a token with `scope='admin'`. Tokens that pre-date the migration return 401.
3. **Update `.env`** on the VPS to include `https://sales.shopbellasxv.com` in `CORS_ORIGINS` (the repo `.env` was updated as part of this commit; sync-up the VPS file explicitly).
4. **Nginx vhost** for `sales.shopbellasxv.com`. Mirror the admin vhost: serve `frontend/dist/` with SPA fallback, proxy `/api/*` to the API. **Required** rate-limit on the PIN endpoint (this is not optional — it protects bcrypt verify cost and the DB write path from cross-IP guessing): add `limit_req_zone $binary_remote_addr zone=sales_pin:10m rate=5r/m;` at the http level, then on `location = /api/sales/auth/pin { limit_req zone=sales_pin burst=5 nodelay; ... }`. `certbot --nginx -d sales.shopbellasxv.com` for the Let's Encrypt cert. Reload nginx, then verify with `for i in 1 2 3 4 5 6 7 8; do curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.shopbellasxv.com/api/sales/auth/pin -H 'Content-Type: application/json' -d '{"identifier":"x","pin":"000000"}'; done` — expect a 503 from nginx well before 8 requests.
5. **Mint owner PINs** for any stylists you want to onboard. Settings → Sales staff → Add stylist → record the one-time PIN handed back. The stylist enters that PIN on first sign-in, is forced to choose their own.
6. **Verify** — from the VPS:
   - `curl -i https://sales.shopbellasxv.com/` returns the SPA index.
   - `curl -i -X POST https://api.shopbellasxv.com/api/sales/auth/pin -H 'Content-Type: application/json' -d '{"identifier":"non-existent","pin":"000000"}'` returns 401 (no enumeration).
   - Sign in as the owner at `admin.shopbellasxv.com`, mint a test PIN for a throwaway stylist, sign in at `sales.shopbellasxv.com` with that PIN, verify the change-PIN flow lands on the home placeholder.

### Phase 1 frontend wiring summary

- `frontend/src/services/api.js` — `isSalesSubdomain()`, `getActiveTokenStorageKey()`, sales auth helpers (`salesPinLogin`, `salesGetMe`, `salesChangePin`), admin sales-staff helpers (`listSalesStaff`, `createSalesStaff`, `mintSalesPin`, `clearSalesPin`, `unlockSalesStaff`).
- `frontend/src/contexts/SalesAuthContext.jsx` — separate auth context, separate `bellas_xv_sales_token` storage key.
- `frontend/src/sales/SalesApp.jsx` — own `<BrowserRouter>`, own `<QueryClientProvider>`, own routes (`/login`, `/change-pin`, `/`).
- `frontend/src/sales/{PinLogin,ChangePin,SalesHome,SalesLayout,SalesProtectedRoute}.jsx` — Phase 1 surface. `PinLogin` is a kiosk-style picker first, then a 6-digit PIN field; typed username is only a fallback if the picker cannot load. `ChangePin` enforces force-change before any other route loads.
- `frontend/src/App.jsx` — early hostname gate: `if (isSalesSubdomain()) return <SalesApp />` before the admin tree even mounts. Local override via `VITE_FORCE_SUBDOMAIN=sales`.
- `frontend/src/pages/SalesStaffSettings.jsx` — admin staff manager linked from Settings.

## Phase 2: Today's Appointments View

Purpose: a clocked-in (or temporarily, just signed-in) stylist sees today's appointments as a mobile card list and can tap into a detail view. Read-only. No status changes, no notes, no quotes yet.

Tasks:

- [x] New endpoint `GET /api/sales/appointments/today?mine=true|false` returns appointments where `slot_start_at::date = today (in APP_TIMEZONE)` ordered by `slot_start_at`. `mine=true` filters to `assigned_user_id = current_user.id`. Default is `mine=false`. Implemented as a half-open UTC range against the local-day boundaries so the index on `slot_start_at` stays usable.
- [x] Response shape includes: appointment id, confirmation code, slot start/end, party size, parent name, celebrant name, status, internal_notes preview, linked event id and status, linked enrichment summary if present. Plus a `has_assigned` flag the frontend uses to decide whether to enable the "Mine only" toggle.
- [x] New endpoint `GET /api/sales/appointments/{id}` returns the full detail: appointment fields, contact, linked event (if any), participants, enrichment responses, internal_notes, recent activity log entries (last 20, ordered newest-first, with live actor display name when available).
- [x] Both endpoints require `require_sales_scope`. They reuse existing service-layer functions wherever possible; do not duplicate query logic.
- [x] Frontend `SalesApp` route `/`: card list. One card per appointment. Card shows time, celebrant name, party size, status chip, enrichment-summary one-liner if present. ([frontend/src/sales/AppointmentsToday.jsx](frontend/src/sales/AppointmentsToday.jsx))
- [x] Frontend `/appointments/:id` route: stacked sections. Header (time, names, status chip), party (parent + celebrant + participants), enrichment (style, budget, theme, court, measurements), notes (read-only), recent activity. ([frontend/src/sales/AppointmentDetail.jsx](frontend/src/sales/AppointmentDetail.jsx))
- [x] Toggle "show only mine" persists in localStorage. Phase 0 confirmed `appointments.assigned_user_id` is dormant (no code path writes to it today). Until the column is populated, render the toggle **disabled with a helper tooltip** ("Available once appointments get assigned to specific stylists") so silently filtering an empty set never makes the view look broken. The toggle un-disables automatically once any appointment in the visible window has a non-null `assigned_user_id`.
- [x] Empty state when no appointments today.
- [x] Smoke test: [tests/test_sales_appointments_smoke.py](tests/test_sales_appointments_smoke.py) seeds 3 today + 1 yesterday + 1 tomorrow, asserts exactly 3 returned in chronological order; verifies `mine=true` filter, `has_assigned` flag, detail join shape, sales-only enforcement (admin token 403), and 404 on bad id.

Deliverable: stylists can prep for appointments. Read-only is the explicit ceiling for this phase.

## Phase 3: Status Quick-Actions And Internal Notes

Purpose: stylists log appointment outcomes in one tap. Marking "arrived" automatically promotes the appointment into the pipeline and moves the event from `lead` to `consulted`. Notes become editable and hit the activity timeline.

Tasks:

- [x] New endpoint `POST /api/sales/appointments/{id}/status` accepts `{action: "arrived" | "no_show" | "cancelled", notes?: string}` and is a single composite transaction in [services/sales_appointments.py:apply_status_action](services/sales_appointments.py):
  - For `arrived`: set `appointments.status = 'attended'`, stamp `attended_at = now()`. If `crm_event_id` is null, call `services.event_service.promote_appointment_to_event` (creates event in `lead`). If the linked event status is `lead`, call `services.event_service.change_event_status(event_id, 'consulted', actor_user_id, notes)` — that already double-writes `event.status_changed` into `activity_log`. Append `appointment.arrived` to `activity_log` with payload `{appointment_id, prior_event_status, new_event_status, promoted_event}`.
  - For `no_show`: set `appointments.status = 'no_show'`, stamp `no_show_at = now()`. Do not touch event status. `appointment.no_show` activity row written when an event is linked.
  - For `cancelled`: set `appointments.status = 'cancelled'`, stamp `cancelled_at = now()`. Do not touch event status. `appointment.cancelled` activity row written when an event is linked.
  - When the appointment has no event, the appointment-level activity row is skipped (the timeline is event-scoped). The appointment row's own `status` + timestamp columns remain the audit trail.
- [x] Idempotent re-tap. `apply_status_action` only writes the appointment-level activity row when something actually changed (`changed=true` in the response). `change_event_status` already no-ops when `new_status == event.status`, so a second `arrived` on a `consulted` event does not produce a noise audit row. Decision: did NOT add a `last_status_action_at` column — the activity log already records every action with a timestamp.
- [x] Status change (e.g. arrived → no_show) does not auto-revert the event status. Reverting `consulted` back to `lead` remains a manual admin action; the activity log shows both transitions.
- [x] New endpoint `PATCH /api/sales/appointments/{id}/notes` accepts `{internal_notes: string}`. Updates the column and writes `appointment.notes_edited` with payload `{appointment_id, prior_length, new_length}` only — never the prior or current text. Idempotent when the value did not change.
- [x] Frontend appointment detail: status chip is paired with a three-button quick-action row (Arrived / No-show / Cancelled). Tapping any opens a confirmation modal whose copy previews the exact effect ("Mark Maria's 2:15 PM appointment as Arrived. We'll move the linked event from Lead to Consulted."). Buttons are disabled once the appointment is in a terminal status. ([frontend/src/sales/AppointmentDetail.jsx](frontend/src/sales/AppointmentDetail.jsx))
- [x] Frontend appointment detail: notes section toggles into an inline editor with a Save button. Optimistic UI; on save we apply the response value and trigger a refetch so the timeline picks up the new `appointment.notes_edited` row.
- [x] Smoke tests in [tests/test_sales_appointments_actions_smoke.py](tests/test_sales_appointments_actions_smoke.py) cover: arrived with no event creates the event and transitions it to consulted; arrived on an existing lead event consults it; arrived on an already-consulted event writes neither a new `event.status_changed` activity row nor a new `event_status_change_events` row; no_show / cancelled stamp the appointment without touching event status; idempotent re-tap (`changed=false`, no new rows); notes patch records length deltas and never the text; idempotent notes patch (no new row when value unchanged); notes patch on an event-less appointment writes no activity row; sales-only enforcement (admin token 403 on both endpoints); 404 on unknown id; 422 on invalid action.

Deliverable: stylists run the floor in one tap each. The owner sees pipeline movement on the admin pipeline view in real time.

## Phase 4: Dress Try-On Log

Purpose: a stylist logs which dresses each appointment tries on, with size and like/dislike, so the owner can see "what got tried on but did not sell" and the stylist can revisit prior choices on follow-up visits.

Tasks:

- [x] Migration [`053_appointment_tried_on_items.py`](database/migrations/053_appointment_tried_on_items.py). Schema as planned, plus the unique constraint shipped with `NULLS NOT DISTINCT` (PG 15+) so two rows with `appointment_id, catalog_item_id, size_label = NULL` also collide — the default `UNIQUE` would have silently allowed that.
- [x] **Event-required guard.** Try-on rows are anchored on `appointment_id` but written against the linked event (so they live in CRM history). If `appointments.crm_event_id IS NULL`, the service refuses with `event_required` (409) and the frontend shows a "Mark arrived first" alert with a one-tap button that opens the Phase 3 Arrived modal. Read access (GET) does not require an event — empty list + `has_event=false` lets the section render the guide alongside an empty list. We deliberately do NOT auto-create an event from this path; Phase 3's Arrived button is the canonical promote/transition entry point.
- [x] New endpoints (gated on `require_sales_scope`):
  - `GET /api/sales/appointments/{id}/tried-on` (returns `{appointment_id, has_event, items: [...]}` so the UI can drive the guide without a separate detail call)
  - `POST /api/sales/appointments/{id}/tried-on`
  - `PATCH /api/sales/tried-on/{id}`
  - `DELETE /api/sales/tried-on/{id}`
- [x] Catalog **GETs are now dual-scope** (`require_any_scope("admin", "sales")`). Phase 1 had locked GETs to admin out of an over-cautious read of the SKU obfuscation policy; Phase 4 revises that — sales staff are still staff and search the same fields admins do (designer, style number, public code, color). The SKU policy applies to customer-facing surfaces and to activity-log payloads, not to staff reads. POST/PATCH stay admin-only.
- [x] Activity log: `appointment.tried_on_added`, `appointment.tried_on_updated`, `appointment.tried_on_removed`. Payloads carry only `tried_on_item_id`, `catalog_item_id`, `size_label`, and (on update) the changed-field names. Never `internal_sku`, `designer`, `style_number`, or `description_text`. The notes update path logs `fields: ["notes"]` only — never the text itself.
- [x] Frontend appointment detail: new "Tried on" section ([frontend/src/sales/TriedOnSection.jsx](frontend/src/sales/TriedOnSection.jsx)). Each row is a card with image (from `image_urls[0]`), public code, color, an inline-editable size text box, a like/dislike toggle, an optional notes box, and a remove button. The "Add dress" dialog uses an MUI Autocomplete bound to `/api/catalog?q=...` with debounced search; submission is blocked client-side when the chosen `(catalog_item_id, size)` already appears in the visible list (the server still enforces the same rule via the unique constraint). When the appointment has no linked event yet, the section renders an info alert with a "Mark arrived" action that opens the Phase 3 Arrived modal.
- [x] Smoke tests in [tests/test_sales_tried_on_smoke.py](tests/test_sales_tried_on_smoke.py): add with size, add without size, duplicate (size + size) returns 409, duplicate (NULL + NULL) ALSO returns 409 (`NULLS NOT DISTINCT`), different size of same dress is fine, list ordering oldest-first, list works without event (`has_event=false`), POST against an appointment with no event returns 409 `event_required`, PATCH only the set fields and writes one activity row, no-op PATCH does not write a noise audit row, DELETE returns 204 and writes activity, catalog ON DELETE RESTRICT blocks catalog deletion while try-on rows exist, activity payloads omit internal_sku/designer/style_number/description_text and never log notes text, sales-only enforcement, 404 on unknown ids, and dual-scope catalog GET works for sales tokens.

Deliverable: stylists log try-ons in a few taps. Owner can query "what got tried on this month" by joining the table.

## Phase 5: Quote, Sign On The Spot, Convert To Invoice

Purpose: stylists build a quote during the appointment, capture a customer signature on the iPad, and convert to an invoice. Reuses the existing `QuoteEditor.jsx`, `InvoiceEditor.jsx`, and `SignatureDialog.jsx` with no fork.

### Quote and invoice reality map (2026-05-08)

This is the existing behavior, audited from the routers and services. Phase 5 work is the small delta against this map; the doc text used to drift from reality (Phase 0 caught `convert-to-invoice` vs the real `/convert`), so this section is the contract Phase 5 builds against.

#### Quote status state machine

Allowed values per `chk_quote_status` ([database/migrations/027_create_quotes.py:62](database/migrations/027_create_quotes.py#L62)): `draft`, `sent`, `approved`, `rejected`, `converted`, `expired`, `cancelled`.

Service-enforced transitions (`code='invalid_transition'` on violation):

- `draft` → `sent` via `mark_sent` (allocates `quote_number`, creates invitations, sends email).
- `draft | sent` → `approved` via `approve_in_store` (stamps signature, allocates `quote_number` if from draft).
- `sent` → `approved` via `approve_quote` (customer-portal path; stamps signature).
- `sent` → `rejected` via `reject_quote`.
- `draft | sent` → `cancelled` via `cancel_quote`.
- `approved` → `converted` via `convert_to_invoice` (creates draft invoice, links via `converted_invoice_id`; idempotent on already-converted).
- `expired` is set by a cron sweep (Phase 11 of invoicing), not by any sales-portal path.
- `draft | rejected | expired | cancelled` → soft-delete via `soft_delete_quote` (sent/approved/converted are non-deletable).

CHECK invariants worth knowing:
- `chk_quote_signature_paired`: `signature_base64` and `signature_signed_at` are set together or both NULL.
- `chk_quote_approved_has_signature`: `status='approved'` implies a signed_at timestamp.
- `chk_quote_converted_consistent`: `status='converted'` ⇔ `converted_invoice_id IS NOT NULL`.
- `chk_quote_number_when_not_draft`: any non-`draft` row has a `quote_number`.

Signature columns: `signature_base64`, `signature_signed_at`, `signature_ip` (INET), `signature_name`. **Phase 5 adds `signature_user_agent VARCHAR(255)` via migration 054.**

#### Quote routes (current state — Phase 1 already wired the scopes)

| Method + Path | Scope | Behavior |
|---|---|---|
| `POST /api/events/{event_id}/quotes` | admin+sales | Create draft. Body has line items, terms, plan. |
| `GET /api/events/{event_id}/quotes` | admin+sales | List for event. |
| `GET /api/quotes` | admin+sales | Global search. |
| `GET /api/quotes/{id}` | admin+sales | Detail. |
| `PATCH /api/quotes/{id}` | admin+sales | Edit (service enforces editable statuses). |
| `POST /api/quotes/{id}/send` | admin+sales | `draft` → `sent`. Allocates quote_number, sends email. |
| `POST /api/quotes/{id}/resend` | admin+sales | Re-fire email. Status unchanged. Project memory: must actually re-dispatch the email, not just bump `last_resent_at`. |
| `POST /api/quotes/{id}/approve` | admin+sales | Customer-portal path; rare for staff. |
| `POST /api/quotes/{id}/approve-in-store` | admin+sales | **Phase 5 entry point.** `draft \| sent` → `approved`. Stamps signature columns. |
| `POST /api/quotes/{id}/reject` | admin+sales | `sent` → `rejected`. |
| `POST /api/quotes/{id}/cancel` | admin+sales | `draft \| sent` → `cancelled`. |
| `POST /api/quotes/{id}/convert` | admin+sales | `approved` → `converted`. Creates draft invoice, returns invoice detail. **Real path is `/convert`, NOT `/convert-to-invoice`.** |
| `DELETE /api/quotes/{id}` | **admin only** | Soft-delete. Sales gets 403. |
| `GET /api/quotes/{id}/pdf` | admin+sales | PDF render (cached by revision). |
| `POST /api/quotes/{id}/pdf/retry` | admin+sales | Re-render after a failed render. |

#### Invoice status state machine

Allowed values per `chk_invoice_status` ([database/migrations/018_create_invoices.py:49](database/migrations/018_create_invoices.py#L49)): `draft`, `sent`, `partial`, `paid`, `cancelled`, `reversed`.

Transitions sales touches in Phase 5: `draft` → `sent` via `send_invoice`. `partial` / `paid` come from the payments router (admin-only); sales never lands those. `cancelled` is reachable from non-`paid` statuses via the cancel endpoint (admin+sales).

#### Invoice routes (current state)

| Method + Path | Scope | Behavior |
|---|---|---|
| `POST /api/events/{event_id}/invoices` | admin+sales | Create draft (also called by quote `/convert`). |
| `GET /api/events/{event_id}/invoices` | admin+sales | List for event. |
| `GET /api/invoices` | admin+sales | Global search. |
| `GET /api/invoices/{id}` | admin+sales | Detail. |
| `PATCH /api/invoices/{id}` | admin+sales | Edit (line items, terms, installments). |
| `POST /api/invoices/{id}/send` | admin+sales | `draft` → `sent`. Sends email. |
| `POST /api/invoices/{id}/resend` | admin+sales | Re-fire email. Status unchanged. |
| `POST /api/invoices/{id}/cancel` | admin+sales | → `cancelled`. |
| `DELETE /api/invoices/{id}` | **admin only** | Soft-delete. Sales gets 403. |
| `GET /api/invoices/{id}/pdf` | admin+sales | PDF render. |
| `POST /api/invoices/{id}/pdf/retry` | admin+sales | Re-render. |

Payments and recording payments (`/api/payments`, `/api/invoices/{id}/payments`, `/api/events/{id}/payments`) stay admin-only across the board. `paid_to_date_cents` and `balance_cents` flip from those flows; sales never touches them.

#### Existing components Phase 5 reuses (no fork)

- [frontend/src/components/QuoteEditor.jsx](frontend/src/components/QuoteEditor.jsx) — drawer with line-item builder, plan selector, send + sign + convert buttons. Already calls `approveQuoteInStore` and `salesPostAppointmentStatus`-adjacent APIs. Imports `SignatureDialog` directly.
- [frontend/src/components/InvoiceEditor.jsx](frontend/src/components/InvoiceEditor.jsx) — the editor mounted by quote conversion's "open the new invoice" path.
- [frontend/src/components/SignatureDialog.jsx](frontend/src/components/SignatureDialog.jsx) — the signature canvas component. No admin-context coupling.

These components depend on services/api.js helpers and React Query, both of which the SalesApp tree already provides ([Phase 1](frontend/src/sales/SalesApp.jsx)). No prop drilling for auth — token comes through the axios interceptor automatically based on hostname.

### Tasks

- [x] Migration [`054_quote_signature_user_agent.py`](database/migrations/054_quote_signature_user_agent.py): adds `quotes.signature_user_agent VARCHAR(255) NULL`. DML probes round-trip the column (set + clear) and verify the 255-char limit rejects oversize values. The pre-existing `chk_quote_*` constraints stay intact.
- [x] Extended [`services/quote_service.approve_in_store`](services/quote_service.py) to accept an optional `signature_user_agent: str | None`, persisted with a 255-char truncation guard. The router pulls the value from the `User-Agent` request header. No parallel signing endpoint added — the existing `POST /api/quotes/{id}/approve-in-store` is already dual-scoped.
- [x] Scope wiring confirmation: Phase 1 already gated all quote and invoice routes per the table above. Phase 5 adds no new gates; the inventory now lives in this section so future drift is obvious.
- [x] Frontend appointment detail gets a "Quotes" section ([frontend/src/sales/QuotesSection.jsx](frontend/src/sales/QuotesSection.jsx)) that:
  - Fetches `/api/events/{event_id}/quotes` for the appointment's linked event.
  - "New quote" button mounts the existing [`QuoteEditor.jsx`](frontend/src/components/QuoteEditor.jsx) drawer with no fork. The drawer's send + sign + convert buttons all flow through the same admin-side service helpers; the sales axios client picks up the sales token from the hostname-aware storage key set up in Phase 1.
  - Each quote row shows quote number, status chip, total, and a relative-time stamp drawn from `converted_at | signature_signed_at | approved_at | sent_at | updated_at | created_at` in priority order.
  - The "Sign on iPad" affordance is the existing `SignatureDialog.jsx` mount inside `QuoteEditor` — Phase 5 reuses the existing path; no new signing UI.
  - When `QuoteEditor` calls `onConverted(newInvoiceId)`, `QuotesSection` flips into mounting [`InvoiceEditor.jsx`](frontend/src/components/InvoiceEditor.jsx) against the freshly-created draft invoice for any final tweaks before send.
  - When the appointment has no linked event (`event` is null in the detail payload), the section renders the same "Mark arrived first" guide pattern Phase 4 uses for the try-on log; the one-tap "Mark arrived" button opens the Phase 3 confirmation modal.
- [x] Event `sold` stays manual in v1. No code path in this phase auto-transitions the event when an invoice is sent. The smoke explicitly asserts the event status is unchanged after `POST /api/invoices/{id}/send`.
- [x] Smoke [tests/test_sales_quote_sign_convert_smoke.py](tests/test_sales_quote_sign_convert_smoke.py): builds a draft quote with two line items, signs in-store via `approve-in-store`, asserts the four legacy signature columns plus the new `signature_user_agent` are populated, PDF cache is invalidated (revision moves past `last_pdf_rendered_revision`), idempotent re-sign on an already-approved quote returns unchanged, `/convert` returns an invoice mirroring the line items, the quote flips to `converted` with `converted_invoice_id` linked, sending the invoice does NOT auto-flip the event to `sold`, and sales tokens get 403 on `DELETE /api/quotes/{id}`, `DELETE /api/invoices/{id}`, and the payments listing endpoints. Cleanup unwinds the converted-quote → invoice link before deleting either, so the bulk-delete pattern in [test_invoices_smoke.py](tests/test_invoices_smoke.py) does not trip the `chk_quote_converted_consistent` CHECK constraint via the FK's `ON DELETE SET NULL`.

Deliverable: full appointment-to-signed-quote-to-invoice flow runs from the sales subdomain on a phone or iPad. The customer signs on glass.

## Phase 6: Add Participant

Purpose: stylists and owners add a sister, friend, parent, or court member to the event without leaving the screen they are on. The flow guides through the same intake as the public booking widget so the new person becomes a real contact (own profile, own measurements, own activity/quote/invoice history) and the event's participant list is complete. This phase covers **both surfaces** (admin event Overview and sales appointment detail) because the underlying principle is global: no participant exists without a contact.

Tasks:

- [x] Canonical endpoint `POST /api/events/{event_id}/participants` lives in [api/routers/event_participants.py](api/routers/event_participants.py) and uses `require_any_scope("admin", "sales")`. Body: `{parent_first_name, parent_last_name?, celebrant_first_name, celebrant_last_name?, phone, email?, role?, party_size_bucket?}`. The handler returns a 404 for unknown event ids and a 422 with `detail='phone_invalid'` for unparseable phones. Returns `was_new_contact` plus the new contact id/display name in the payload.
- [x] The deprecated `/api/sales/events/{event_id}/participants` route is preserved as a thin alias in [api/routers/sales.py](api/routers/sales.py) — same Pydantic body, same service call, same gate. Marked `deprecated=True` in OpenAPI so any external integration that still points at the old URL keeps working through one rolling release; new frontend code calls `addEventParticipant(eventId, body)`. The duplicate `_lookup_existing_contact` helper is gone.
- [x] [services/contact_service.find_or_create_contact](services/contact_service.py) now returns `(contact, was_new)` so the endpoint trusts the service's signal directly. Both callers (booking widget public path + the participant route) updated to unpack the tuple. The activity-log payload reflects `was_new_contact` from the service, not from a parallel pre-lookup.
- [x] Activity log row: kind `event.participant_added`, `actor_kind='staff'`, `subject_kind='contact'`, `subject_id=contact.id`, payload `{participant_id, display_name, role, party_size_bucket, was_new_contact}`. Same shape on both routes; the smoke asserts both directions.
- [x] PartySizeBucket Literal pruned. Audited [widgets/bellas-booking-widget.js](widgets/bellas-booking-widget.js): the booking widget today emits only `pair`, `3_4`, `5_plus`. The legacy `solo`, `2_3`, `4_plus` values stay in the appointments-table CHECK constraint for historical rows but are deliberately rejected by the participant Pydantic Literal so the column does not collect drift values. Smoke verifies all three legacy values get a 422.
- [x] Migration [`055_event_participants_contact_required.py`](database/migrations/055_event_participants_contact_required.py): pre-flight asserts zero orphan rows (Phase 0 audit had confirmed this); ALTER COLUMN contact_id SET NOT NULL; DROP existing FK and re-add with `ON DELETE RESTRICT` so contact deletion blocks while participants reference it (cleaner failure mode than the old `SET NULL` → NOT NULL violation chain). DML probes verify both invariants. ORM model updated to match. **Phase 7's clock-in migration shifts to `056_clock_in.py` and Phase 8's shifts/time-off shifts to `057_shifts_and_time_off.py`.**
- [x] Frontend: shared [components/AddParticipantDialog.jsx](frontend/src/components/AddParticipantDialog.jsx) — three-step MUI Stepper (Parent → Celebrant → Contact). PartySizeBucket dropdown uses only the three canonical values. Calls the canonical `addEventParticipant` helper. Mounts on **both** surfaces: admin event Overview "Participants" (replaces the previous inline implementation in [pages/event/tabs/Overview.jsx](frontend/src/pages/event/tabs/Overview.jsx) — the inline version is gone) and sales appointment detail's "Party" section ([sales/AppointmentDetail.jsx](frontend/src/sales/AppointmentDetail.jsx)). Sales-side button is hidden until the appointment has a linked event; the section explains "once the appointment is checked in" rather than showing a non-functional button.
- [x] Smoke [tests/test_event_participants_smoke.py](tests/test_event_participants_smoke.py): `find_or_create_contact` tuple round-trip (new phone → True, repeat → False), canonical route with sales token + brand-new phone (`was_new_contact=True`, contact created, activity row written with the right shape), canonical route with admin token + existing phone (`was_new_contact=False`, no duplicate contact, second activity row recorded), deprecated `/api/sales/...` alias works for both tokens, PartySizeBucket Literal rejects all three legacy values with 422, unknown event id returns 404, unparseable phone returns 422 `phone_invalid`, migration 055 invariants (NULL contact_id rejected at the DB layer, ON DELETE RESTRICT blocks contact deletion while participants reference it).

Deliverable: stylists add the celebrant's mom, sister, and three court members during an appointment without leaving the iPad; owners do the same from the admin event detail when a walk-in lead phones in. Each becomes a real contact with a clean profile, and the schema enforces the rule.

## Phase 7: Clock-In, Clock-Out, Geofence

Purpose: stylists clock in only when physically at the boutique. Time is recorded per-staff per-day. Selfie is captured according to the owner policy (`required`, `optional`, or `disabled`). The PIN-login flow becomes PIN-then-clock-in for appointment operations; schedule, time-off, PIN change, and sign-out remain available while punched out.

### Phase 7 split — Slice 1 (foundation) before Slice 2 (selfies + UI)

Phase 7 has too many small coupled parts to land in one push. Split into two slices so the attendance state machine can settle before frontend polish or disk-write code touches it:

**Slice 1 — DB schema + service + endpoints sans selfies + smokes.** Scope: migration tables, ORM models, `services/business_time.py`, geofence/haversine service, `POST /api/sales/clock/in`, `POST /api/sales/clock/out`, `GET /api/sales/clock/status`, owner-side staff_locations seed endpoint, smoke. **No selfie code, no UI, no punched-out gates on existing sales endpoints.** Slice 1 does NOT touch disk and therefore does NOT need the VPS `ReadWritePaths` check before merging.

**Slice 2 — selfies + UI + attendance gates.** Scope: client-side WebP conversion + EXIF strip, server-side selfie storage under `/var/lib/bellas-xv/uploads/clockin/`, the `/clock` SalesApp screen, the punched-out gate on appointment operations (with a per-owner override), the "Mine only" toggle un-disable trigger if it ties into shifts. **Slice 2 is the disk-write surface; the VPS systemd write-path check below is a hard gate before any of this ships.**

### VPS write-path prerequisite (gate for Slice 2 only)

Before Slice 2 ships, run on the VPS:
```
sudo systemctl cat bellas-xv-api.service | grep -i ReadWritePaths
```
The output must include `/var/lib/bellas-xv/uploads` (or a parent of it). The clock-in selfies live under `/var/lib/bellas-xv/uploads/clockin/<user_id>/<punch_id>.webp` so they inherit the existing override; if the line is missing or points elsewhere, add `/var/lib/bellas-xv/uploads` to the unit's `ReadWritePaths` and `systemctl daemon-reload && systemctl restart bellas-xv-api.service` BEFORE merging Slice 2 code. A forgotten override produces 500s on the first selfie POST that look like CORS errors at the browser layer; check is cheap.

### Slice 1 tasks

- [ ] (Slice 2 gate, captured here for visibility): Systemd unit override check on the VPS — see the prerequisite block above. Slice 1 does NOT need this; Slice 2 must clear it before merging.
- [ ] Migration `056_clock_in.py` (renumbered from `055` after Phase 6 took 055):
  ```sql
  CREATE TABLE staff_locations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    latitude NUMERIC(10,7) NOT NULL,
    longitude NUMERIC(10,7) NOT NULL,
    radius_m INTEGER NOT NULL CHECK (radius_m BETWEEN 25 AND 1000),
    grace_minutes INTEGER NOT NULL DEFAULT 0 CHECK (grace_minutes BETWEEN 0 AND 120),
    default_auto_session_close_time TIME NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE TABLE staff_punches (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    direction VARCHAR(8) NOT NULL CHECK (direction IN ('in', 'out')),
    punched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR(20) NOT NULL DEFAULT 'recorded'
      CHECK (status IN ('recorded', 'late', 'early_out', 'unscheduled', 'manual_adjusted', 'void')),
    location_id INTEGER NULL REFERENCES staff_locations(id) ON DELETE SET NULL,
    shift_id BIGINT NULL,
    holiday_id INTEGER NULL,
    client_latitude NUMERIC(10,7) NULL,
    client_longitude NUMERIC(10,7) NULL,
    client_accuracy_m NUMERIC(10,2) NULL,
    distance_to_location_m NUMERIC(10,2) NULL,
    selfie_storage_key VARCHAR(255) NULL,
    auto_closed BOOLEAN NOT NULL DEFAULT false,
    auto_close_reason VARCHAR(24) NULL
      CHECK (auto_close_reason IN ('past_date', 'max_time_reached', 'max_session_hours')),
    auto_closed_at TIMESTAMPTZ NULL,
    hours_confirmation_status VARCHAR(20) NOT NULL DEFAULT 'not_required'
      CHECK (hours_confirmation_status IN ('not_required', 'needs_review', 'confirmed', 'adjusted')),
    hours_confirmed_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    hours_confirmed_at TIMESTAMPTZ NULL,
    user_agent VARCHAR(255) NULL,
    ip INET NULL,
    notes TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX idx_punches_user_day ON staff_punches(user_id, punched_at);
  CREATE TABLE staff_punch_audit_events (
    id BIGSERIAL PRIMARY KEY,
    punch_id BIGINT NULL REFERENCES staff_punches(id) ON DELETE SET NULL,
    actor_kind VARCHAR(20) NOT NULL CHECK (actor_kind IN ('system', 'staff', 'owner')),
    actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(40) NOT NULL,
    reason_code VARCHAR(60) NULL,
    old_values JSONB NOT NULL DEFAULT '{}'::jsonb,
    new_values JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX idx_staff_punch_audit_punch ON staff_punch_audit_events(punch_id, created_at DESC);
  CREATE TABLE staff_punch_correction_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    punch_id BIGINT NULL REFERENCES staff_punches(id) ON DELETE SET NULL,
    requested_check_in_at TIMESTAMPTZ NULL,
    requested_check_out_at TIMESTAMPTZ NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending', 'approved', 'denied', 'cancelled')),
    decided_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ NULL,
    decision_notes TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX idx_staff_punch_corrections_status ON staff_punch_correction_requests(status, created_at DESC);
  ```
  Shipped as [database/migrations/056_clock_in.py](database/migrations/056_clock_in.py). Two extras vs the brief: a partial index `idx_staff_locations_active` on `staff_locations(active) WHERE active IS TRUE` (Phase 7's geofence query filters on this every call), and a partial composite review-queue index on `staff_punches` covering the OR-set the owner attendance UI in Slice 2 will read (auto_closed, problematic statuses, needs_review). DML probes hit every CHECK constraint and confirm round-trip on staff_locations, staff_punches, staff_punch_audit_events, and staff_punch_correction_requests.
- [x] Service [services/clock_in.py](services/clock_in.py) with `haversine_m(lat1, lng1, lat2, lng2)`, `find_active_location_within_radius(client_lat, client_lng)`, `current_status(user_id)`, `today_punches(user_id)`, `punch_in(...)`, `punch_out(...)`. Module name was changed from the doc's planned `clock_in_service.py` to `clock_in.py` for consistency with the rest of `services/*` (`event_service.py`, `quote_service.py`, etc., all drop the `_service` suffix). `resolve_shift_for_user_at` is deferred to Phase 8 — Phase 7 Slice 1 marks every punch `status='unscheduled'` because no shift data exists yet, which automatically lands every row in the owner's review queue and is the right default for a no-shifts deploy.
- [x] [services/business_time.py](services/business_time.py) — `shop_tz()`, `business_now()`, `to_business_local(dt)`, `business_date(dt=None)` backed by `APP_TIMEZONE`. The today's-punches query in `clock_in.py` already uses these; future cron / shift code MUST use them rather than `datetime.now()` so the day-boundary and DST cases stay correct on the UTC VPS.
- [x] Endpoints (Slice 1 — JSON only, no multipart selfie yet):
  - `POST /api/sales/clock/in` accepts `{client_latitude, client_longitude, client_accuracy_m?}`. Rejects with 403 `outside_geofence` (detail carries `distance_m`, `closest_location_name`, `closest_location_radius_m`) if the client is outside every active location's radius. Rejects with 409 `already_punched_in` if the user's last non-void punch is `direction='in'`.
  - `POST /api/sales/clock/out` same body shape. Rejects with 409 `not_punched_in` if the user is already out. Punch-out does NOT enforce the geofence (a stylist may already be walking out); we still record the closest location and distance for the audit row.
  - `GET /api/sales/clock/status` returns `{state, last_punch, today_punches, timezone, business_date}`. Today's window is computed in business-local time via `business_time.business_date()`, then converted to a UTC half-open range so the `idx_staff_punches_user_day` index stays usable.
  - All three gated on `require_sales_scope`. Admin tokens get 403 from these routes.
- [x] Geofence enforcement: server compares the client coordinates against every `active=true` location's `(lat, lng, radius_m)` via haversine in the service layer. If none match, the closest location's distance is included in the response detail so the UI can render "you're 230m too far north" instead of a generic 403. Client coordinates are echoed back into the punch row for audit, never trusted as authority. The Slice 2 frontend will capture `client_accuracy_m`, retry with low-accuracy network location on timeout, and surface platform-specific permission copy.
- [x] Owner-side seed endpoint [api/routers/admin_staff_locations.py](api/routers/admin_staff_locations.py) at `/api/admin/staff-locations` (admin-scope only). GET list / POST create / PATCH update / DELETE soft-deactivate. The Pydantic Field constraints (`radius_m` ≥ 25, ≤ 1000; `grace_minutes` ≤ 120) mirror the DB CHECK so 422 fires before a CHECK violation surfaces. Soft-delete (flip `active=False`) keeps every historical punch's `location_id` audit attribution intact.
- [x] Smoke [tests/test_clock_in_smoke.py](tests/test_clock_in_smoke.py): haversine reference values (zero distance for identical points; 1° latitude ≈ 111.2km; 100m offset round-trips within 1m), admin seeds the geofence via the endpoint (sales token rejected with 403), status before any punch returns `state='out'`, punch in within 50m works and sets `status='unscheduled'` with the right distance, status reflects the punch, double punch-in returns 409 `already_punched_in`, punch out from 5km away accepted with the closest location attributed for audit, double punch-out returns 409 `not_punched_in`, punch in from 5km away rejected with 403 `outside_geofence` and the closest-location metadata in the detail, deactivating the location blocks future clock-ins (`closest_location_name=null`), `staff_punches.user_id ON DELETE RESTRICT` blocks user deletion while punches reference them, admin token rejected on `/api/sales/clock/status`. **Slice 2 smokes (selfie upload, punched-out gate on appointment ops, pre-close reminder, auto-close cron, correction approval) land with that slice.**

#### Slice 1 deliverable

Stylist with a sales token can hit `POST /api/sales/clock/in` from inside the geofence and land a punch row, get rejected outside the geofence with a debuggable distance, double-punch is impossible, and `GET /api/sales/clock/status` reflects today's history. Owner can seed the boutique geofence through the admin endpoint without touching SQL. The attendance state machine is live and shaken down before any disk-write or UI code touches it.

### Slice 2A tasks (selfie storage + server-enforced gate)

Slice 2A is the backend half of Slice 2: it lands the selfie write path with strict limits + a deployment-failure smoke that proves the failure mode is obvious, plus the server-enforced punched-out gate the user explicitly asked for ("Frontend gating is good UX; backend gating is what keeps the rule real once people have old tabs open"). The `/clock` SalesApp screen, the owner attendance review UI, and the cron family stay in Slice 2B.

- [x] Migration [`057_business_profile_attendance_settings.py`](database/migrations/057_business_profile_attendance_settings.py): adds `business_profile.attendance_gate_enabled BOOLEAN DEFAULT TRUE`, `selfie_policy VARCHAR(16) DEFAULT 'optional'` with a CHECK over `('required', 'optional', 'disabled')`, and `selfie_retention_days INTEGER DEFAULT 365` with a CHECK over `[1, 3650] | NULL`. ORM model + `business_profile_service._EDITABLE_FIELDS` updated; the existing admin PATCH endpoint accepts the three new fields with matching Pydantic constraints. DML probes round-trip every column and verify the CHECKs reject `selfie_policy='maybe'` and `selfie_retention_days=0`.
- [x] [services/clock_selfie.py](services/clock_selfie.py): tight backend limits in two phases. `validate_selfie_bytes(raw, declared_mime)` rejects on declared mime not in `image/{webp,jpeg,png}` (415), >1MB (413), or <200B (400), then opens with Pillow to confirm the bytes really decode as one of the allowed formats, converts to RGB, caps dimensions at 1024x1024, and re-encodes WebP at quality=80. EXIF and ICC profile are not carried over because the encoder only writes them when explicitly passed. `write_selfie_bytes(user_id, punch_id, webp_bytes)` does the disk write under `clockin/{user_id}/{punch_id}.webp` and maps `PermissionError` / `OSError` to a stable `selfie_storage_unavailable` 503 — that's the deployment-failure smoke target.
- [x] `POST /api/sales/clock/in` and `/out` are now multipart (`Form` parts for coords + `File` part for the selfie). Selfie validation runs BEFORE the punch row is created, so a bad selfie does not waste a punch_id. After the punch is created and `db.flush()`-ed, the selfie write fires with the new `punch.id`; if the disk write fails, the entire transaction is rolled back so a punch never exists without its selfie. Selfie policy enforced server-side: `disabled` rejects a present selfie (400 `selfie_disabled`), `required` rejects an absent one (400 `selfie_required`), `optional` accepts either way.
- [x] [services/attendance_gate.py](services/attendance_gate.py) — `require_floor_access(*allowed_scopes)` factory. Replaces `require_sales_scope` / `require_any_scope("admin", "sales")` on every mutation route the doc lists. Sales-scope tokens get checked against `current_status`; punched-out raises 403 `attendance_gate`. Admin-scope tokens always bypass — they aren't on the clock. Owner can flip `business_profile.attendance_gate_enabled = false` to make the gate a no-op without a deploy.
- [x] Gate applied to all sales-portal mutation endpoints: `POST /api/sales/appointments/{id}/status`, `PATCH /api/sales/appointments/{id}/notes`, `POST /api/sales/appointments/{id}/tried-on`, `PATCH/DELETE /api/sales/tried-on/{id}`, `POST /api/events/{event_id}/participants` and its `/api/sales/...` alias, every dual-scope quote mutation (`POST /api/events/{event_id}/quotes`, `PATCH /api/quotes/{id}`, `POST /api/quotes/{id}/{send,resend,approve,approve-in-store,reject,cancel,convert}`), every dual-scope invoice mutation (`POST /api/events/{event_id}/invoices`, `PATCH /api/invoices/{id}`, `POST /api/invoices/{id}/{send,resend,cancel}`), and the staff portal-invitation create + resend on both quote and invoice surfaces. Reads, PDF retrievals, PDF retry, and admin-only DELETEs deliberately stay outside the gate so on-the-way-to-the-shop list/detail still loads.
- [x] Slice 1 [tests/test_clock_in_smoke.py](tests/test_clock_in_smoke.py) updated to use `data=` (multipart Form) instead of `json=` so the now-multipart endpoints accept its bodies.
- [x] Slice 2A smoke [tests/test_clock_selfie_and_gate_smoke.py](tests/test_clock_selfie_and_gate_smoke.py): selfie happy path (Pillow round-trip → on-disk WebP under cap, max dimension ≤ 1024), wrong content-type → 415, oversize → 413, malformed-bytes-claiming-jpeg → 400, **storage-unavailable failure mode** (monkeypatch `document_storage.put_object` to raise `PermissionError` — simulates the systemd `ReadWritePaths` line missing — verifies the endpoint returns 503 with `code=selfie_storage_unavailable` AND that the rolled-back transaction left no punch row). Selfie policy enforcement: `required` + missing → 400, `disabled` + present → 400. Gate enforcement: sales token punched out → 403 on appointment status / notes / tried-on, admin token bypasses, sales token punched in → 200, owner setting `attendance_gate_enabled=false` makes the gate a no-op while the user is still out. Existing sales-mutation smokes (sales_appointments_actions, sales_tried_on, sales_quote_sign_convert, event_participants) updated to snapshot/disable/restore the gate around their runs via [tests/_attendance_helpers.py](tests/_attendance_helpers.py) so they remain focused on their own behavior.

#### Slice 2A deliverable

Stylist with a sales token can clock in with an optional selfie that lands as bounded WebP under the configured policy, can be blocked from mutating today's floor when punched out (independent of frontend state), and the deployment failure mode for selfie writes returns a stable 503 with the failure rolled back rather than a generic 500 that looks like CORS at the browser. Owner has a knob to disable the gate per-deploy without a code change.

### Slice 2B-1 tasks (stylist clock UI — shipped)

- [x] `/clock/status` response now exposes `attendance_gate_enabled` so the SalesApp can decide whether to redirect a punched-out stylist on load. The Slice 1 + Slice 2A smokes still pass against the new field.
- [x] Frontend [ClockScreen.jsx](frontend/src/sales/ClockScreen.jsx). Mobile-first: requests geolocation on mount, retries with low-accuracy network fallback on timeout, surfaces platform-specific permission copy on `PERMISSION_DENIED` / `POSITION_UNAVAILABLE` / `TIMEOUT`. Selfie capture via `getUserMedia({video: {facingMode: 'user'}})` only when `selfie_policy` is `required` or `optional`; the captured frame is drawn to a 1024-edge-bounded canvas at JPEG quality 0.85 before upload (lighter wire payload, server still re-encodes to WebP). Multipart POST via the new `salesPunchIn` / `salesPunchOut` helpers. On success: clear local capture, invalidate the shared clock-status query, and (for clock-in) honor a `?next=` redirect to wherever the stylist was originally trying to go.
- [x] [useClockStatus](frontend/src/sales/useClockStatus.js) hook — single React Query backing for clock state across the SalesApp tree. 15s stale, refetch on window focus. Disabled when no sales user is authenticated so it can be called unconditionally.
- [x] [SalesProtectedRoute.jsx](frontend/src/sales/SalesProtectedRoute.jsx) gates on clock state: when `attendance_gate_enabled` is true and `state == 'out'`, redirects to `/clock?next=<previous-path>`. Allowlist preserves `/clock` itself and `/change-pin` (Phase 8 will add `/schedule` and `/time-off`). Force-PIN-change still wins over the punch redirect.
- [x] [SalesLayout.jsx](frontend/src/sales/SalesLayout.jsx) topbar shows a "On the clock / Off" chip linking to `/clock`. Sign-out triggers a confirm dialog when punched in: "You're still clocked in. Sign out does NOT clock you out." Three actions: Cancel, Go clock out (routes to `/clock`), Sign out anyway (signs out without punching).
- [x] [attendanceGate.js](frontend/src/sales/attendanceGate.js) helper + inline error copy wired into [AppointmentDetail.jsx](frontend/src/sales/AppointmentDetail.jsx) (status quick-action modal + notes save) and [TriedOnSection.jsx](frontend/src/sales/TriedOnSection.jsx) (per-row patch + delete + add dialog). When the server returns 403 `attendance_gate`, the inline alert reads "Clock in to start working the floor" instead of the generic "That action failed". The proactive route guard usually catches the punched-out case before the user can even hit a mutation; the per-component handler covers the race where the cron auto-closes a session while the stylist still has the page open.
- [x] `/clock` route mounted in [SalesApp.jsx](frontend/src/sales/SalesApp.jsx). All sales smokes still pass; lint + build clean.

#### Slice 2B-1 deliverable

A stylist who PIN-logs into `sales.shopbellasxv.com` while punched out gets routed to `/clock`, requests location + (per policy) takes a selfie, and clocks in via multipart POST. The topbar tracks state with a tappable chip. Signing out while clocked in surfaces a confirm dialog so a forgotten clock-out does not happen silently. Mutations on appointment ops surface a clean "Clock in first" message rather than a generic error if the gate fires.

### Slice 2B-2 tasks (owner attendance review UI — shipped)

- [x] Owner-side admin UI: `/settings/sales-staff` gains a "Today's punches" panel rendered by [AttendanceReview.jsx](frontend/src/pages/AttendanceReview.jsx) in `mode='today_panel'` (compact, no totals/correction-queue, range locked to today), and a standalone [`/reports/attendance` page](frontend/src/pages/AttendanceReview.jsx) with the full surface: range toggle (Today / This week / Last 14 days), staff filter, "Needs review only" toggle with a count badge, paired hours by stylist + per-day breakdown, and the correction-request decision queue. CSV export is deliberately deferred — there's no payroll integration target yet, and the on-screen data is bounded enough for screenshot/PDF if Bellas needs to send it externally before Phase 8.
- [x] Owner attendance review queue: review-queue predicate ORs across `staff_punches.status IN ('late','early_out','unscheduled','manual_adjusted','void')`, `auto_closed=true`, and `hours_confirmation_status IN ('needs_review','adjusted')`. Implemented as a single SQL filter in [services/attendance_review.py](services/attendance_review.py); also returned as a `review_queue_count` so the toggle button renders a badge without a second roundtrip.
- [x] Sales/owner confirmation UI: auto-closed rows display chips ("Auto-closed (past_date)", "Needs review") and a Confirm button on both surfaces. Owner uses [admin attendance/punches/{id}/confirm](api/routers/admin_attendance.py); stylist uses the parallel [/api/sales/attendance/punches/{id}/confirm](api/routers/sales_attendance.py) under [MyAttendance.jsx](frontend/src/sales/MyAttendance.jsx). Both write a `punch.hours_confirmed` audit row with the actor.
- [x] Missed-punch correction UI: stylist's [MyAttendance.jsx](frontend/src/sales/MyAttendance.jsx) carries a "New correction" form (proposed in/out + reason). Owner sees the request in the [AttendanceReview.jsx](frontend/src/pages/AttendanceReview.jsx) "Correction requests" card with Approve/Deny dialogs (decision notes optional). Approval applies the proposed time to the linked punch via the same audit-row pattern manual_adjust uses, so the timeline reads consistently.
- [x] Attendance reads are date-ranged from day one: `range_key=today|current_week|pay_period` or explicit `from_date`/`to_date`. The admin attendance routes have no unbounded list endpoint; passing only one of from/to returns 422. Per the user's directive, every filter resolves through `services.business_time.business_date` so a punch at 11:30pm local on Saturday counts toward Saturday rather than the UTC-shifted Sunday.
- [x] **Append-only/audited adjustments**: per the user's "no hard deletes" directive, every owner edit goes through `manual_adjust` (writes `punch.manual_adjusted` audit row, status flips to `manual_adjusted`, hours flip to `adjusted`) or `void_punch` (writes `punch.voided`, sets status to `void` while preserving the row). There is no DELETE route; the smoke verifies the path returns 404. Reason is required on both, surfaced in the UI as a required textarea.
- [x] **Correction approve/deny is a separate action from manual adjust**: the user explicitly asked for this so the timeline stays understandable. `decide_correction_request` is its own service entrypoint and its own admin endpoint, distinct from `manual_adjust`. Approval applies the proposed time iff a `punch_id` is set on the request; denial is record-only. Both decisions stamp the request row's `decided_by_user_id` / `decided_at` / `decision_notes` and write a `correction_request` decision audit row.
- [x] **API responses expose both UTC and business-local timestamps**: punch rows carry `punched_at` (UTC ISO), `punched_at_local` (boutique-local ISO), and `business_date`. Correction-request rows carry the same pattern for `requested_check_in_at` / `requested_check_out_at`. The owner table renders local time and tooltips the UTC stamp so a payroll-style cross-check is one hover away.
- [x] **Sales-side punched-out allowlist** updated: `/clock`, `/change-pin`, and `/my-attendance` are reachable while clocked out. Filing a missed-punch correction has to work *while clocked out* — that's the whole point of the surface.
- [x] [tests/test_attendance_review_smoke.py](tests/test_attendance_review_smoke.py): 19 assertions covering scope rejection, the today/week/explicit-range bounds, the business_date attribution at 11:30pm local, review-queue filtering with badge count, staff filtering, totals derivation (8h paired session + 0h for unmatched late punch), confirm idempotency + audit row, manual_adjust audit row + reason-required guard, void preserving the row + excluding from totals, the full correction-request lifecycle (submit / pending queue / approve applies + audits, deny is record-only / cancel by owner-of-row), the same-row-not-yours guards, and the no-DELETE assertion. All 9 sales smokes still pass; lint and build clean.

### Slice 2B-3 tasks (cron family — shipped)

Slice 2B-3 was the hard pre-merge gate for the systemd `ReadWritePaths` check; the selfie retention cron deletes files under `/var/lib/bellas-xv/uploads/clockin/`, so the unit-file path discipline matters end-to-end. **VPS capture from 2026-05-08** lives in the "Systemd unit + ReadWritePaths" block above and confirms `/var/lib/bellas-xv/uploads` is covered.

- [x] **Migration [058_cron_run_state.py](database/migrations/058_cron_run_state.py)** — adds `cron_run_state` (singleton-per-cron-name with last started/finished, scanned/changed counters, error string, consecutive-failure count, `UNIQUE(name)`) and `attendance_pre_close_reminders` (idempotency table for the pre-close cron, `UNIQUE(punch_id, cutoff_business_date)`). DML probes round-trip the UPSERT pattern and verify both UNIQUE constraints reject collisions.
- [x] **Selfie retention cron** [services/clock_selfie_retention.py](services/clock_selfie_retention.py): walks every `staff_punches` row whose `selfie_storage_key IS NOT NULL` AND `punched_at < now - retention_days`, deletes the on-disk WebP via `document_storage.delete_object`, NULLs `selfie_storage_key` on the row, writes a `staff_punch_audit_events` row with `actor_kind='system'` and `action='selfie.retention_deleted'`. Punch rows are preserved — coordinates, timestamps, auto-close fields, audit attribution all stay. `selfie_retention_days IS NULL` → cron is a no-op (the "keep forever" path). Idempotent on second run because step 2 NULLs the key.
- [x] **Auto-close cron** [services/attendance_close.py](services/attendance_close.py): finds every user whose most-recent non-void punch is `direction='in'` past either `default_auto_session_close_time` for the in-punch's local date (`auto_close_reason='past_date'`) or `MAX_SESSION_HOURS=24` (`auto_close_reason='max_time_reached'`). Inserts a paired `direction='out'` row with `auto_closed=true`, `auto_close_reason`, `auto_closed_at=now`, `hours_confirmation_status='needs_review'`, and **`punched_at` set to the cutoff itself** (NOT `now`) so the timeline reflects when the session *should* have ended. Stamps the in-punch as `needs_review` so the owner reviews the whole session. Writes `punch.auto_closed` audit. **Idempotency** (the user's headline ask): re-checks `current_status` per candidate before inserting — a sibling tick that already closed the session causes the second tick to skip with `skipped_already_closed`. The smoke runs the tick twice and asserts identical row count, identical audit-row count, identical `hours_confirmation_status`. Never called from a read path; only the daily worker invokes `tick(db)`.
- [x] **Pre-close reminder cron** [services/attendance_pre_close.py](services/attendance_pre_close.py): for every open session whose location has `default_auto_session_close_time` set and whose cutoff is within `REMINDER_LEAD_MINUTES=30` of `now`, sends an email via `email_transport.get_email_transport().send(...)`: "Still working? Clock out by <local time>." The `attendance_pre_close_reminders(punch_id, cutoff_business_date)` UNIQUE prevents a second tick in the same window from sending a second email — the second INSERT raises `IntegrityError` inside a savepoint, gets swallowed, and the run reports `skipped_already_sent`. SMTP failure is logged, the marker row stays committed (no spam-resend on transient transport error), and the next pass leaves the punch alone for the day.
- [x] **Cron health/status surface** [api/routers/admin_cron_health.py](api/routers/admin_cron_health.py) at `GET /api/admin/cron-health`: returns one entry per `services.cron_state.ALL_CRON_NAMES` even when never run, with `last_started_at`, `last_finished_at`, `last_scanned_count`, `last_changed_count`, `last_error`, `consecutive_failures`, `is_stale` (no completion in over 2 days), and `ok` (no error AND not stale AND no consecutive failures). Sales tokens get 403. The owner-facing read is mounted on the [AttendanceReview.jsx](frontend/src/pages/AttendanceReview.jsx) page as a "Cron health" card so a missing tick is visible right next to the review queue rather than buried in the logs.
- [x] **Daily-worker integration** [workers/daily.py](workers/daily.py): each new cron runs in its own SessionLocal and its own `record_run` after the existing reminder pass. A failure in one tick is logged + re-raised inside `record_run` (so the cron-state row carries the error) but is swallowed at the worker layer so the other crons still run.
- [x] **`record_run` context** [services/cron_state.py](services/cron_state.py): the only writer to `cron_run_state`. Opens its own SessionLocal so a cron body that rolls back its main session doesn't also erase the failure stamp. On exception: stamps `last_error`, increments `consecutive_failures`, re-raises. On success: clears the error, zeros the failure counter, stamps `last_finished_at`.
- [x] **Smoke** [tests/test_attendance_crons_smoke.py](tests/test_attendance_crons_smoke.py): retention happy path (file deleted, key NULLed, audit row written, punch preserved) + idempotent second run + NULL retention = no-op. Auto-close: first tick closes the session with `past_date` reason, second tick is a no-op (no duplicate `out`, no duplicate audit row, `hours_confirmation_status` unchanged), third tick on a fresh user closes again. `max_time_reached` branch verified on a 30h-old session with no location cutoff. Pre-close: one send per `(punch, cutoff)` window, second tick skips via UNIQUE, far-from-cutoff session is skipped without writing a marker. Cron health: every cron name surfaces, transitions OK → stale → error → recovered as the cron-state row is mutated, sales token returns 403.
- [x] Reserved `holiday_id` on punches for Phase 8 holiday tagging. (The column was already left NULL with no FK in Slice 1 migration 056; Slice 2B-3 leaves it untouched. Phase 8's migration adds the `staff_holidays` FK.)
- [x] **Deferred to Phase 8 (and shipped there)**: earliest check-in enforcement, late/early-out classification, per-stylist shift cutoffs in pre-close + auto-close. All three landed in Phase 8 Slice B — `clock_in.py:_classify_in_status` / `_classify_out_status` tag `late` / `early_out`, the earliest-check-in window raises `too_early_for_shift` 403, and `attendance_close.py:_decide_close` / `_past_date_decision` precede the location default with the resolved shift's `max_session_hours` and `auto_session_close_time`. Coverage in [test_schedule_resolver_smoke.py](tests/test_schedule_resolver_smoke.py) and [test_attendance_crons_smoke.py](tests/test_attendance_crons_smoke.py).

#### Phase 7 deliverable (after all slices)

Stylists clock in only at the shop, with optional/required selfie + GPS captured under the existing systemd `ReadWritePaths`. Owner sees who is on the clock right now (today's punches panel under `/settings/sales-staff`), total hours by day with a "needs review" queue (`/reports/attendance`), an append-only audit trail of every adjust/void, a correction-request queue with approve/deny that's separate from manual adjust, and a cron-health card so a missed retention/reminder/auto-close tick lights up immediately. Forgotten clock-outs auto-close server-side with explicit `auto_closed=true` columns and a confirm-hours workflow on both surfaces. The Phase 8 schedule + time-off layer plugs straight into the existing `staff_punches.shift_id` and the cron cutoff branch.

## Phase 8: Schedule And Time-Off

Purpose: stylists see their assigned shifts for the next two weeks, request time off, and the owner approves or denies. Owner uses the existing admin to assign shifts.

### Phase 8 reality findings (2026-05-08, captured before Slice A code landed)

Doc-vs-reality drift items the user signed off on before Slice A:

1. **Migration number is `059`**, not the doc's original `057`. Phase 7 took 057 (business_profile attendance settings) + 058 (cron run state). Phase 8's renumbering chain is now 057 → 058 → 059. Phase 9 lands at 060 if needed.
2. **`staff_punches.shift_id` and `holiday_id` were left as plain nullable columns with no FK** in [056_clock_in.py](database/migrations/056_clock_in.py) per the explicit Phase 7 design call. Slice A wires both FKs with `ON DELETE SET NULL` so a deleted shift or holiday never breaks a historical punch row.
3. **Time-off audit lives in a sibling table.** Per the user's Phase 8 guardrail #3 ("time-off approval append-only/audited, like attendance adjustments"), the doc's `time_off_requests` schema (`decided_by_user_id` / `decided_at` / `decision_notes` on the row itself) is kept as the **latest-state** read surface, but the timeline lives in a new `time_off_decision_events` table that mirrors `staff_punch_audit_events`. Action vocabulary is locked at the schema level: `requested | approved | denied | cancelled | amended`.
4. **`pay_period` UI label** in [AttendanceReview.jsx](frontend/src/pages/AttendanceReview.jsx) stays "Last 14 days." Phase 8 schedule shifts don't define a payroll cycle, only a 2-week schedule view; the API contract `range_key=pay_period` keeps the rolling 14-day window unchanged so the frontend does not need a redeploy when Phase 9+ adds real payroll periods.
5. **No DB-level overlap enforcement** on `staff_shifts(user_id, time-range)`. Per guardrail #2, owner-visible only. Slice C will surface overlaps in the shift-assignment UI calendar; no UNIQUE or EXCLUDE USING gist constraint at the DB level.
6. **Holiday tagging fires on punch insert** inside `services.clock_in.punch_in/out` — single SELECT per punch against `staff_holidays(holiday_date, location_id)` with the captured-on-insert FK. The Slice B smoke verifies tagging for both global (location_id NULL) and per-location holidays.
7. **Time-off → shift visibility** is a read-side filter in Slice C: `/api/sales/schedule` suppresses shift instances whose date falls inside an approved (not pending, not cancelled, not denied) time-off request. No row writes; the schedule UI just doesn't show those days.
8. **`working_days` interpretation** (locked by the user's Slice A clarification): `starts_at`/`ends_at` are TIMESTAMPTZ anchors. The local time-of-day component repeats on each ISO weekday (1=Mon, 7=Sun) in `working_days`. The resolver carries `duration = ends_at - starts_at` and expands onto each weekday in the requested range, which makes overnight shifts (where the local time-of-day at `ends_at` is earlier than at `starts_at`, but `ends_at > starts_at` because the date is the next day) cleanly handled — the duration crosses local midnight rather than the time-of-day having to "wrap." Slice B's resolver smoke proves overnight expansion produces `(Sat 18:00, Sun 02:00)` for a 6h-overnight template anchored on Saturday.
9. **`staff_holidays` UNIQUE NULLS NOT DISTINCT** on `(holiday_date, location_id, name)` — required PG16 behavior so two "global" holidays (location_id IS NULL) with the same date+name actually collide. The Slice A migration probe AND the Slice A smoke both exercise this case explicitly.
10. **`time_off_decision_events.action` CHECK** is locked to `requested | approved | denied | cancelled | amended` at the schema level. A future state requires a migration; a code-only addition gets caught by the CHECK + the Slice A smoke's bad-action probe.
11. **`existing idx_staff_punches_review_queue` partial index** (from migration 056) already covers `status IN ('late', 'early_out', ...)`; those statuses become real once Slice B classifies on punch insert. No new index in Phase 8.

### Slice A tasks (schema + ORM — shipped)

- [x] **Migration [059_shifts_and_time_off.py](database/migrations/059_shifts_and_time_off.py)** lays down five tables and adds two FKs on `staff_punches`:
  - `staff_shifts(id, user_id, location_id, starts_at, ends_at, late_grace_period_minutes, earliest_check_in_minutes, early_out_grace_minutes, auto_session_close_time, max_session_hours, working_days INTEGER[], notes, created_by_user_id, created_at, updated_at)` with CHECK constraints on every numeric range, `ends_at > starts_at`, `array_length(working_days, 1) BETWEEN 1 AND 7`, AND `working_days <@ ARRAY[1..7]` (the additional containment CHECK keeps a typo'd weekday `8` out of the array — the original doc didn't have this).
  - `staff_shift_overrides(id, user_id, shift_id, starts_on, ends_on, reason, created_by_user_id, created_at)` with `ends_on >= starts_on` CHECK and `ON DELETE CASCADE` from both `users` and `staff_shifts`.
  - `staff_holidays(id, name, holiday_date, location_id, is_paid, multiplier, notes, created_at)` with **`UNIQUE NULLS NOT DISTINCT (holiday_date, location_id, name)`** (PG16-only; per the user's Phase 8 guardrail) and a CHECK that `multiplier IS NULL OR multiplier > 0`.
  - **Two FK additions on `staff_punches`**: `shift_id → staff_shifts(id) ON DELETE SET NULL`, `holiday_id → staff_holidays(id) ON DELETE SET NULL`. A deleted shift or holiday never breaks a historical punch row.
  - `time_off_requests(id, user_id, starts_at, ends_at, reason, status, decided_by_user_id, decided_at, decision_notes, manager_user_id, created_at, updated_at)` — the latest-state row, status CHECK locked to `pending | approved | denied | cancelled`.
  - **`time_off_decision_events(id, request_id, actor_kind, actor_user_id, action, old_values JSONB, new_values JSONB, notes, created_at)`** — append-only timeline mirroring `staff_punch_audit_events`. Action vocabulary CHECK locked to `requested | approved | denied | cancelled | amended` per the user's Slice A judgment call. `actor_kind` CHECK locked to `owner | staff | system`. `ON DELETE CASCADE` on the `request_id` FK so the timeline is cleaned up alongside the parent.
  - **DML probes** (per the project rule): every CHECK rejected by a malformed INSERT inside a savepoint, the `staff_punches.shift_id` FK on a deleted shift confirmed to set the column to NULL, and **the headline NULLS NOT DISTINCT case** (two duplicate global holidays with identical date+name) confirmed to fail at insert time. Same date+name with different `location_id` confirmed to be allowed.
- [x] **ORM models** in [database/models.py](database/models.py): `StaffShift`, `StaffShiftOverride`, `StaffHoliday`, `TimeOffRequest`, `TimeOffDecisionEvent`. `StaffPunch.shift_id` and `holiday_id` updated to declare the new `ForeignKey(... ondelete='SET NULL')`. Postgres `ARRAY(Integer)` import wired for `working_days`.
- [x] **[tests/test_phase8_schema_smoke.py — deleted in the 2026-05-17 smoke audit; see docs/SMOKE_TEST_AUDIT.md](SMOKE_TEST_AUDIT.md)** — ORM round-trip on every table, the NULLS-NOT-DISTINCT holiday probe at the application layer, FK SET NULL behaviors verified through SQLAlchemy sessions on both `shift_id` and `holiday_id`, the action vocabulary CHECK rejected via an ORM `IntegrityError`, the actor_kind CHECK rejected via an ORM `IntegrityError`, the time-off range CHECK rejected via an ORM `IntegrityError`, `ON DELETE CASCADE` on `staff_shift_overrides`/`time_off_decision_events`/`staff_shifts(user_id)` exercised end-to-end, and an overnight shift round-trip (Saturday 18:00 → Sunday 02:00, 8h duration that crosses local midnight).
- [x] **Doc reality findings** captured above (11 items, all approved). All 10 prior sales smokes still pass alongside the new Slice A smoke; lint + build clean.
### Slice B tasks (shift resolver + cron integration — shipped)

- [x] **[services/shift_resolver.py](services/shift_resolver.py)** ships `resolve_active_shift(db, *, user_id, as_of_local) → ResolvedShift | None` with precedence **override → assigned/base → None** (location-default fallback handled by the cron, not the resolver — keeps the cron path the only place that decides "no shift = use location default"). `as_of_local` is optional; defaults to `business_now()`. Override semantics: when an override row covers `as_of_local.date()`, the override's `shift_id` template is applied **even if its `working_days` doesn't include this weekday** — overrides are intentional schedule changes, not weekday filters. Multi-shift-per-day disambiguation: prefer the instance bracketing `as_of_local`, then closest upcoming, then closest past.
- [x] **`expand_shifts(db, *, user_id, from_date, to_date, suppress_time_off=True)`** walks each business-local date and produces `{business_date, shift, time_off_suppressed}`. The shift expansion uses `template.starts_at`'s local time-of-day + `template.ends_at - template.starts_at` as duration; overnight templates cleanly emit instances whose `ends_at_local` lands on the next calendar day. `suppress_time_off=True` (default) omits the shift on dates falling inside an approved time-off request and tags the day with `time_off_suppressed: True`. The cron-facing `resolve_active_shift` deliberately **does not** respect time-off suppression — a stylist who somehow punches in on an approved-off day still needs the cron to auto-close them.
- [x] **`find_holiday_id(db, *, biz_date, location_id)`** lookup helper. Per-location holiday wins over a same-day global (location_id IS NULL); returns the FK or None. Single SELECT, cheap.
- [x] **Earliest-check-in enforcement** in [services/clock_in.py:punch_in](services/clock_in.py): if a shift covers the punch's business date and `now < shift.starts_at - earliest_check_in_minutes`, raises `ClockInError("too_early_for_shift", 403, extra={"shift_starts_at": ..., "earliest_allowed_at": ...})` before the geofence check. Slice C will add the owner manual-punch admin endpoint that bypasses this. The frontend [ClockScreen.jsx](frontend/src/sales/ClockScreen.jsx) error map now renders a stylist-friendly "Too early to clock in. You can clock in starting at X" with the `earliest_allowed_at` parsed.
- [x] **Late / early-out classification** on punch insert via `_classify_in_status` / `_classify_out_status`: `'late'` when `now > shift.starts_at + late_grace_period_minutes` on a clock-in, `'early_out'` when a clock-out lands before `shift.ends_at - early_out_grace_minutes`. Punch-OUT classification resolves against the **in-punch's** business date so an overnight session is graded against the right shift template (the user pulled a Saturday-night shift, not Sunday's nothing). The existing `idx_staff_punches_review_queue` partial index already covers both statuses.
- [x] **Holiday tagging on punch insert** in both `punch_in` and `punch_out`: looks up via `find_holiday_id`, stamps `staff_punches.holiday_id` on the row. Holidays are advisory — they tag, never block.
- [x] **Cron integration** (per guardrail #4 — shift cutoffs feed in without removing the location-default fallback):
  - [services/attendance_close.py](services/attendance_close.py): `_decide_close` now takes `resolved` and prefers the shift's `max_session_hours` over the module's `MAX_SESSION_HOURS=24`; `_past_date_decision` prefers the shift's `auto_session_close_time` over `staff_locations.default_auto_session_close_time`. The Phase 7 location-default branch is preserved verbatim — Slice B *adds* shift-aware cutoffs, doesn't replace the fallback. `run_auto_close_pass` resolves the shift against the in-punch's business date so an overnight session uses yesterday's template.
  - [services/attendance_pre_close.py](services/attendance_pre_close.py): `_cutoff_for` checks the shift's `auto_session_close_time` first, falls back to the location default. The `(punch_id, cutoff_business_date)` UNIQUE marker still guarantees idempotency across either source — a tick on a shift cutoff and a later tick on a location cutoff for the same punch+day cannot fire twice.
- [x] **`now_override` plumbing** added to `punch_in`/`punch_out` so deterministic smokes can drive wall time without monkey-patching `datetime.now`. When `now_override` is None (production), the punch row's `punched_at` falls through to the DB default `NOW()` so production behavior is unchanged.
- [x] [tests/test_schedule_resolver_smoke.py](tests/test_schedule_resolver_smoke.py) covering the user's 9 Slice B invariants in order: (1) override > base, (2) base shift cutoff > location cutoff in auto-close, (3) approved time-off suppresses shift via `expand_shifts` while the cron-facing resolver still resolves on time-off days, (4) earliest-check-in 403 with `earliest_allowed_at` extra, (5) late status past the grace window, (6) early-out before the grace window, (7) holiday tagging stamps `holiday_id` (per-location wins over global, global fallback when no per-location, NULL when no holiday) without blocking, (8) auto-close + pre-close honor shift cutoff first then fall back to location cutoff (verified via the auto-out's `punched_at` time and via the pre-close email subject containing the shift cutoff time), (9) overnight shift expansion (Saturday 18:00 → Sunday 02:00, 8h crossing midnight). All 11 prior smokes still pass.

### Slice C tasks (endpoints — shipped)

- [x] **Sales endpoints** under [/api/sales/](api/routers):
  - `GET /api/sales/schedule?from_date=&to_date=` — [sales_schedule.py](api/routers/sales_schedule.py) — resolved schedule via `expand_shifts` with read-side time-off suppression. Date range required, max window 31 days.
  - `GET /api/sales/time-off` — [sales_time_off.py](api/routers/sales_time_off.py) — own requests newest-first.
  - `POST /api/sales/time-off` — file new request, writes `time_off_decision_events(action='requested', actor_kind='staff')` and emails the owner via `notification_templates.render_time_off_requested_to_owner` (best-effort: SMTP failure logged, request still committed).
  - `POST /api/sales/time-off/{id}/cancel` — POST verb instead of DELETE per the user's enforcement #1; row preserved with `status='cancelled'` plus a `cancelled` event. Idempotent on already-cancelled rows; 409 on already-decided rows; 403 on another stylist's row.
- [x] **Admin endpoints** under [/api/admin/](api/routers):
  - `GET/POST /api/admin/shifts`, `PATCH/DELETE /api/admin/shifts/{id}` — [admin_shifts.py](api/routers/admin_shifts.py) — full CRUD via `services/staff_shifts_admin.py`. PATCH validates the post-update state so a partial change cannot sneak past invariants.
  - `GET/POST /api/admin/shift-overrides`, `DELETE /api/admin/shift-overrides/{id}` — same router, sibling APIRouter mounted on its own prefix.
  - `GET /api/admin/shifts/overlaps?user_id=&from_date=&to_date=` — read-only overlap visualizer per the user's enforcement #6. **Never blocks a shift create**; the smoke explicitly creates an overlapping shift and verifies the POST returns 201, then asserts the overlap surfaces in the visualizer's response.
  - `GET/POST /api/admin/holidays`, `PATCH/DELETE /api/admin/holidays/{id}` — [admin_holidays.py](api/routers/admin_holidays.py) — owner CRUD. The schema's `UNIQUE NULLS NOT DISTINCT (holiday_date, location_id, name)` surfaces as a stable `holiday_already_exists` 409 (smoke probes the duplicate-global case).
  - `GET /api/admin/time-off?from_date=&to_date=&user_id=&status=` — [admin_time_off.py](api/routers/admin_time_off.py) — date-bounded queue. Both `from_date` and `to_date` required (enforcement #4); intersects on `[starts_at < to, ends_at > from]` so requests spanning the window are included.
  - `POST /api/admin/time-off/{id}/decide` `{status: 'approved'|'denied', decision_notes?}` — refuses re-decide on terminal status (409); writes the matching `approved`/`denied` event and emails the stylist via `notification_templates.render_time_off_decided_to_staff`.
  - `POST /api/admin/time-off/{id}/amend` `{starts_at?, ends_at?, decision_notes?}` — owner edits proposed times before approval; status stays `pending`; writes an `amended` event with old/new values. Refuses on terminal status (409).
- [x] **Notification templates** in [services/notification_templates.py](services/notification_templates.py): `render_time_off_requested_to_owner` and `render_time_off_decided_to_staff` return the same `RenderedEmail` shape as the existing booking templates. Owner email recipient resolves via `business_profile.email` first, falling back to active admins.
- [x] **Activity log entries deferred to a future slice.** Reality check: `services/activity_log.py` requires `event_id` (not nullable) and is keyed to CRM events. Time-off and shifts aren't event-tied; they have their own first-class audit surfaces (`time_off_decision_events` already shipped in Slice A; `staff_shifts.created_by_user_id` covers shift attribution). The original doc said "Shift create/update/delete also log if Phase 8 wants a history" — explicitly optional. v1 ships without it; if Phase 9 hardening wants a unified ops timeline, that's where to add it.
- [x] **Slice C enforcement smoke** [tests/test_time_off_endpoints_smoke.py](tests/test_time_off_endpoints_smoke.py) covers all 6 enforcement points: (1) DELETE on `/api/sales/time-off/{id}` returns 404/405 — POST cancel is the only path; (2) re-decide / amend / cancel on a terminal request all return 409; (3) sales user B cannot list or cancel sales user A's time-off (403 with `time_off_request_not_yours`); (4) admin `/api/admin/time-off` requires both `from_date` and `to_date` (422 on missing/inverted); (5) every state transition writes a paired `time_off_decision_events` row (`requested → amended → approved` chain asserted in the smoke); (6) creating an overlapping shift returns 201 and the overlap surfaces in the read-only visualizer (never blocked at create time). Plus full lifecycle, scope gating both directions, schedule read with time-off suppression, holiday duplicate 409, and shift delete via SET NULL on the punch FK. All 12 prior smokes still pass; lint + build clean.

### Slice D tasks (frontend — shipped)

- [x] **API helpers** in [frontend/src/services/api.js](frontend/src/services/api.js) for all 18 Slice C endpoints: `salesGetSchedule`, `salesListMyTimeOff`, `salesSubmitTimeOff`, `salesCancelTimeOff` on the stylist side; `listAdminShifts`/`createAdminShift`/`patchAdminShift`/`deleteAdminShift`, `listAdminShiftOverlaps`, `listAdminShiftOverrides`/`createAdminShiftOverride`/`deleteAdminShiftOverride`, `listAdminHolidays`/`createAdminHoliday`/`patchAdminHoliday`/`deleteAdminHoliday`, `listAdminTimeOff`/`decideAdminTimeOff`/`amendAdminTimeOff` on the admin side.
- [x] **Stylist [/schedule](frontend/src/sales/Schedule.jsx)** — two-week view with This week / Next week toggle, Mon-anchored. Renders one card per business-local day with the resolved shift's start/end times, an "Override" chip when `is_override`, an "Overnight" chip when `ends_at_local.date() != starts_at_local.date()`, and an "Off" chip on approved time-off days (read-side suppression already happens in the API). Reachable while clocked out.
- [x] **Stylist [/time-off](frontend/src/sales/TimeOff.jsx)** — list of past + pending requests, "Request time off" form (datetime-local pickers + reason), Cancel button on pending rows only. POST cancel verb (per Slice C enforcement #1); button label stays "Cancel" because it's about UX honesty, not HTTP method. Stale-UI 409 from a `time_off_request_terminal` triggers a refresh + clear error message. Reachable while clocked out.
- [x] **Sales topbar nav** in [SalesLayout.jsx](frontend/src/sales/SalesLayout.jsx) gains "Schedule" and "Time off" buttons. **`PUNCHED_OUT_ALLOWLIST`** in [SalesProtectedRoute.jsx](frontend/src/sales/SalesProtectedRoute.jsx) extended with `/schedule` and `/time-off` so a stylist who hasn't punched in can still view their shifts and file a time-off request.
- [x] **Owner [/settings/sales-staff/{user_id}/schedule](frontend/src/pages/SalesStaffSchedule.jsx)** — three sections: Base shifts (table + Add dialog with weekday chips, late grace, earliest check-in, early-out grace, auto-close time, max session hours, notes), Overrides (table + Add dialog driving off the existing shifts), Overlaps (read-only Alert per overlap with timestamps and source kind, surfaced for next 14 days). Per Slice C enforcement #6 the page never blocks a create on overlap; warnings are surfaced informationally. v1 deliberately omits PATCH dialogs — owner deletes + recreates instead, matches the same shape as Phase 7 Slice 2B-2 for void-vs-delete.
- [x] **Owner [/settings/time-off](frontend/src/pages/AdminTimeOff.jsx)** — date-range toggle (This month / Next 30 days / Next 90 days), pending requests partitioned to the top, Approve / Deny / Amend buttons inline. Decision dialog has optional notes; amend dialog has a heads-up that "the request stays pending after the amendment." Stale 409s on amend/decide trigger a refresh.
- [x] **Owner [/settings/holidays](frontend/src/pages/AdminHolidays.jsx)** — table CRUD. Add dialog with name, date, location dropdown (or "Global"), paid switch, multiplier, notes. The NULLS-NOT-DISTINCT 409 surfaces as **"A holiday with that date, location, and name already exists"** instead of a generic save error.
- [x] **Settings landing page** ([Settings.jsx](frontend/src/pages/Settings.jsx)) gains entries for Time off, Holidays, and Attendance review. **[SalesStaffSettings.jsx](frontend/src/pages/SalesStaffSettings.jsx)** gains a per-row "Schedule" link to the stylist's shift-assignment page.
- [x] Lint clean, build clean (1071 KB minified / 319 KB gzipped — modest growth from the new pages). All 13 prior smokes still pass; the frontend has no behavioral smoke since Slice D is read/render/dialog UI on top of already-smoked Slice C endpoints.

#### Phase 8 deliverable

Stylists self-serve their two-week schedule and file/cancel time-off requests from `sales.shopbellasxv.com`. Owner has one screen to approve/deny/amend time off (`/settings/time-off`), a per-stylist screen to manage shift templates and one-off date overrides (`/settings/sales-staff/{id}/schedule`), and a holidays calendar (`/settings/holidays`). Attendance crons resolve cutoffs through the same shift template that drives the stylist's `/schedule`, with the location-default fallback intact. Holidays remain advisory — they tag punches via `staff_punches.holiday_id` for reporting but never block clock-in/out. Time-off decisions are append-only-audited via `time_off_decision_events`; cancel uses POST so the row sticks around with `status='cancelled'` for the timeline; overlap detection is read-only and visualization-only per the user's enforcement #6.

## Phase 9: Hardening

Purpose: take the v1 working build and fix the rough edges that come from running it on a real floor.

### Sub-slice 1: Attendance Operations Admin Tools

Two operational gaps surface together: the owner cannot manage the boutique geofence from the UI (backend CRUD ships at `/api/admin/staff-locations` but no frontend page calls anything except the read used as a holiday-form dropdown), and attendance reporting tops out at "Last 14 days" with no monthly/quarterly presets and no CSV export. Bundle into one sub-slice because the same operator (the owner) needs both within the same `/settings` surface.

Reality check before scoping:
- Backend CRUD on staff locations is complete except `default_auto_session_close_time` is missing from `StaffLocationCreate` / `StaffLocationPatch` / `StaffLocationResponse` ([admin_staff_locations.py:25-65](api/routers/admin_staff_locations.py#L25-L65)). The column exists on the model ([models.py:1085](database/models.py#L1085)) — only the API surface is missing, so the auto-close cutoff cannot be set without a SQL UPDATE today.
- The `/api/admin/attendance/totals` and `/punches` endpoints already accept arbitrary `from_date`/`to_date` ([attendance_review.py:139](services/attendance_review.py#L139)). The bucket primitive (`_pair_hours_by_day` at [attendance_review.py:314](services/attendance_review.py#L314)) is daily. Range presets are limited to `today`, `current_week` (Mon-Sun), and `pay_period` (placeholder = today + previous 13 days).

Scope boundaries:
- Single-location semantics in the UI (matches reality) but the schema stays a per-location array.
- Reporting extends the existing `/totals` path. No separate reporting service or reports table.
- CSV only. No PDF, no email delivery, no scheduled report generation.
- No payroll, no commission accounting, no notification rules — those remain separate hardening items.

Sequenced delivery (each step lands as its own commit + smoke run before the next):

1. Backend schema/API fixes for staff locations (`default_auto_session_close_time` round-trip).
2. Staff-locations admin page.
3. Geofence test endpoint + UI button.
4. Reporting range presets and real pay-period anchor.
5. Bucketed totals (`?bucket=day|week|biweek|month`).
6. CSV export.

Steps 1-3 ship together as Priority 1 (Geofence Admin UI) for immediate operational value. Steps 4-6 ship together as Priority 2 (Reporting Ranges + CSV). Pause + smoke between the two priorities.

#### Priority 1: Geofence Admin UI (steps 1-3) — shipped

- [x] **Backend schema round-trip** in [admin_staff_locations.py](api/routers/admin_staff_locations.py): added `default_auto_session_close_time` (`time | None`) to `StaffLocationCreate`, `StaffLocationPatch`, and `StaffLocationResponse`. Patch uses a membership-only check (`"default_auto_session_close_time" in fields_set`) so an explicit null clears the cutoff and falls back to `MAX_SESSION_HOURS=24` in the auto-close cron. `from_row` reads the existing column. No migration needed.
- [x] **Geofence test endpoint** `POST /api/admin/staff-locations/{id}/test-geofence` accepting `{latitude, longitude}`, returning `{inside, distance_m, radius_m}`. Calls `services.clock_in.haversine_m` directly — the same function the punch gate uses ([clock_in.py:94](services/clock_in.py#L94)) — so a passing test guarantees a real punch from the same coords also passes. 404 on unknown id; 422 on out-of-range lat/lng. Inactive locations remain testable so the owner can validate coords before reactivating. Read-only.
- [x] **Frontend admin page** [/settings/staff-locations](frontend/src/pages/AdminStaffLocations.jsx) matching the `AdminTimeOff.jsx` / `AdminHolidays.jsx` shape. Table CRUD with name, coordinates, radius, grace, auto-close, active chip. Create/edit dialog with name, latitude, longitude, `radius_m`, `grace_minutes`, `default_auto_session_close_time` (`<input type="time">`), active toggle (edit-only). Two helper buttons: **"Use my current location"** (calls `navigator.geolocation.getCurrentPosition`, populates lat/lng to 7 decimals with accuracy reported), and **"Test my GPS against this geofence"** (edit-mode only since it needs a saved id, hits the test endpoint, surfaces inside/outside + distance + radius + accuracy in a Material UI Alert). Soft-delete via the existing DELETE; the trash icon is hidden for already-inactive rows.
- [x] **API helpers** in [api.js](frontend/src/services/api.js): `createAdminStaffLocation`, `patchAdminStaffLocation`, `deleteAdminStaffLocation`, `testStaffLocationGeofence`. (`listAdminStaffLocations` already existed.)
- [x] **Settings landing entry** in [Settings.jsx](frontend/src/pages/Settings.jsx) for "Staff locations" between Holidays and Attendance review, plus the route in [App.jsx](frontend/src/App.jsx).
- [x] **Smoke** [tests/test_staff_locations_smoke.py](tests/test_staff_locations_smoke.py) (runs serially): create with the new field, raw `SessionLocal` row read confirms the value persists in Postgres, patch round-trip, patch with explicit null clears the column (verified via raw row read), test endpoint returns `inside=true` with `distance_m ≈ 50m` for a 50m offset and `inside=false` with `distance_m ≥ 4_500m` for a 5km offset, 404 on unknown id, soft-delete keeps the test endpoint functional, sales token gets 403 on every route, lat=999 returns 422 at the Pydantic boundary. Existing [test_clock_in_smoke.py](tests/test_clock_in_smoke.py) still passes against the modified router.

##### Priority 1 deliverable

Owner can seed, edit, and soft-deactivate boutique geofences from `/settings/staff-locations`, including the auto-close cutoff that drives the cron. The "Test my GPS" button proves the geofence is correctly configured without forcing a real clock-in. Backend has no SQL-only fields left for the staff-locations row.

#### Priority 2: Reporting Ranges + CSV (steps 4-6) — shipped

- [x] **Migration** [060_business_profile_biweekly_anchor.py](database/migrations/060_business_profile_biweekly_anchor.py) adds `business_profile.biweekly_anchor_date DATE NULL`. DML probe round-trips a real date and confirms existing rows default to NULL. Applied via `python -m database.migrations.runner`.
- [x] **Range presets** in [attendance_review.py:resolve_window](services/attendance_review.py#L165): added `current_month`, `last_month`, `current_quarter` / `last_quarter` (calendar quarters Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec), and made `pay_period` switch behavior on the anchor (anchor-aligned 14-day window when set, legacy rolling "today + previous 13" when null). Existing keys unchanged. `resolve_window` gained an optional `biweekly_anchor` param so callers thread the value through; `list_punches` and `staff_totals` look up the anchor once via `_read_biweekly_anchor`.
- [x] **Bucketed totals** on `GET /api/admin/attendance/totals?bucket=day|week|biweek|month`. New `_rebucket()` aggregates the existing `_pair_hours_by_day` map into ISO weeks (key = the Monday's date), anchor-aligned biweeks (key = window start date, modulo-14 from the anchor handles negative diffs), or `YYYY-MM` months. Response gains `bucket` echo, `biweekly_anchor_date` echo, and a `by_bucket` array per stylist alongside the existing `by_day` (both shipped in parallel — old frontends keep working). 422 `pay_period_anchor_missing` when the anchor is null and `bucket=biweek` is requested; 422 `invalid_bucket` for unknown values.
- [x] **CSV export** `GET /api/admin/attendance/totals/export.csv` accepts the same query params as the JSON endpoint and streams `text/csv` with `Content-Disposition: attachment; filename="attendance-{from}-{to}-{bucket}.csv"`. One row per (staff, bucket_key) plus a trailing `TOTAL` row per stylist. Owner-only via `require_admin_scope`.
- [x] **Frontend range picker + custom dates + bucket toggle** on [AttendanceReview.jsx](frontend/src/pages/AttendanceReview.jsx). `RANGE_OPTIONS` extended with This month / Last month / This quarter / Last quarter; the legacy "Last 14 days" label is now "Pay period" since the underlying behavior changes when an anchor is set. Two `<input type="date">` pickers below the preset row let the owner enter an arbitrary range; selecting a preset clears the dates and vice versa. Bucket toggle (Day / Week / Biweek / Month) sits in the totals card header next to a new "Export CSV" button. The 422 `pay_period_anchor_missing` is caught and rendered as a soft warning ("Set Biweekly anchor on the business profile, then try again") rather than a generic error toast.
- [x] **API helpers** in [api.js](frontend/src/services/api.js): `listAttendanceTotals` now documents the `bucket` param, and a new `downloadAttendanceTotalsCsv(params)` fetches the CSV as a `Blob` with the auth header attached (a plain `<a download>` would not include the bearer), then synthesizes a click on a hidden anchor and revokes the object URL.
- [x] **Smoke** [tests/test_attendance_reporting_smoke.py](tests/test_attendance_reporting_smoke.py) (runs serially per project rule on shared singleton state — captures + restores the prior `biweekly_anchor_date` so it's safe to run alongside other attendance smokes when serialized): default `bucket=day` preserves the existing `by_day` shape; `bucket=week` aggregates Mon+Tue from the same ISO week into one row; `bucket=month` collapses days within the same `YYYY-MM`; `current_quarter` / `last_quarter` resolve to ~90-day windows; `last_month` covers a 32-days-ago session when it lands in the prior month; `bucket=biweek` returns 422 with no anchor and aggregates correctly with the anchor set; `pay_period` falls back to a rolling 14-day window with no anchor and to an anchor-aligned 14-day window with one; `invalid_bucket` and `invalid_range_key` both return 422; CSV endpoint returns parseable `text/csv` whose data rows match the JSON shape; sales token gets 403 on both JSON and CSV endpoints. Per-user assertions only (project rule on global-pass smokes). Existing [test_attendance_review_smoke.py](tests/test_attendance_review_smoke.py) still passes against the new service.

##### Priority 2 deliverable

Owner can pull attendance totals for any natural reporting window — today, this week, the active biweekly pay period, this month, last month, this quarter, last quarter, or an arbitrary date range — bucketed by day/week/biweek/month, and export the result as CSV. The biweekly anchor is owner-configurable via the existing business-profile admin path.

#### Sub-slice 1 commit/push cadence

Per the project rule on phase slices: commit + push from the VPS at the end of each priority (after smoke is green), not after each individual step. Two commits total for this sub-slice.

### Remaining hardening tasks

- [ ] Bundle size: confirm the sales subdomain bundle does not pull admin code at first paint. Use `vite build --mode analyze` (or rollup-plugin-visualizer) to confirm. Lazy-load admin routes if not already.
- [x] Audit every `/api/sales/*` endpoint for proper `require_sales_scope`. Audit every admin route that should be admin-only for `require_admin_scope`. Both audits land as a checklist appended to this doc. Shipped as Phase 9 sub-slice 2 (see "Sub-slice 2: Scope-Guard Audit" below).
- [ ] Audit activity-log payload writes from the sales portal. Per the catalog SKU obfuscation policy, do not embed `internal_sku`, `designer`, `style_number`, or staff `description` text in any activity payload. Tried-on entries reference `catalog_item_id` and let the renderer resolve via `customer_sku` on read.
- [ ] PIN brute-force review: confirm nginx rate limit is in place, confirm row-level lockout works, confirm a successful PIN resets the failure count. Add a metric counter for failed-PIN-attempts-per-day visible on the owner dashboard.
- [x] Selfie EXIF strip: confirmed via [test_attendance_owner_settings_smoke.py](tests/test_attendance_owner_settings_smoke.py) — synthetic JPEG with Make/Model and GPS-IFD fields round-trips through `validate_selfie_bytes` and the resulting WebP carries no top-level EXIF and no GPS keys. The pipeline relies on Pillow's WebP encoder dropping EXIF when `exif=` is not passed; the smoke is the assertion that this stays true.
- [x] Selfie retention: cron + audit shipped in Phase 7 Slice 2B-3 ([clock_selfie_retention.py](services/clock_selfie_retention.py)); the owner-facing decision was the missing piece. Now exposed via the [BusinessProfile.jsx](frontend/src/pages/BusinessProfile.jsx) "Sales staff and attendance" section as a "Selfie retention (days)" field with blank meaning "keep forever". Coverage in [test_attendance_owner_settings_smoke.py](tests/test_attendance_owner_settings_smoke.py).
- [ ] Geofence telemetry: confirm `client_accuracy_m` is captured for every punch and that the haversine check uses raw coordinates (not coordinates plus accuracy buffer). Add an admin "outliers" view of punches with accuracy worse than 100m.
- [ ] Rush-hour behavior: load-test 10 stylists clocking in within 30 seconds. Confirm check-in is not blocked by expensive dashboard reads, scheduled jobs, or selfie upload. Attendance endpoints must stay bounded by date range and indexed columns.
- [ ] Auto-close cron existence smoke: create an open punch row, freeze the clock past `auto_session_close_time` in `APP_TIMEZONE`, run the cron entrypoint directly, and assert `auto_closed=true`, `auto_close_reason` is set, `hours_confirmation_status='needs_review'`, and an audit/activity row exists. This smoke runs serially and fails the build/test gate if the cron entrypoint is missing.
- [ ] Cron health smoke: simulate a failed/missing auto-close run and confirm the admin health payload/banner reports stale `last_run_at` instead of staying silent.
- [ ] Timezone boundary smoke: freeze the clock around business-local midnight and a DST boundary, run schedule lookup and auto-close logic, and assert the business date is correct even though the VPS/system clock is UTC.
- [ ] Attendance report export: owner can export CSV for a bounded date range with configurable columns (staff, date, status, in/out, location, distance/accuracy, auto-close/review state, notes). PDF/email report delivery is deferred unless owner asks for it.
- [ ] Notification settings: add owner-level controls for attendance notifications (`enabled`, quiet hours, reminder lead time) and staff-level mute/preference only if notification volume becomes annoying. Auto-close/correction-decision notifications should remain hard to accidentally suppress.
- [ ] Client diagnostic logging: add a lightweight path to report clock-in failures with route, user agent, geolocation error code, and API status, with sensitive fields stripped. A full bug-report system is out of scope, but the clock-in path needs enough telemetry to diagnose phone/browser permission issues.
- [ ] Documentation: write `docs/SALES_PORTAL.md` with operator-facing instructions (how to invite a stylist, how to set a PIN, how to set a geofence, how to schedule a shift, how to approve time off). This is internal documentation, not customer copy.
- [ ] Run all sales smokes serially per the project rule on shared singleton state. Add a `pytest -m sales --serial` command if needed.
- [ ] Confirm every new write feature has been validated with real INSERTs against the production schema before declaring the phase done.
- [ ] Production rollout checklist: nginx vhost live, cert valid, CORS origin added, systemd override applied, migrations applied in order, frontend rebuilt, stylist PINs minted, geofence seeded, smoke punch from inside the boutique, smoke punch from outside the boutique fails as expected.

Deliverable: production-ready sales portal that has been used on the floor for at least one week without an owner needing to fix something live.

### Sub-slice 2: Scope-Guard Audit

Walked every router mounted in [api/server.py](api/server.py) and confirmed each route's auth dependency. The full inventory ran across 30 router files; the table below groups by mount prefix and lists only the *expected* dep for each prefix's routes (the audit confirmed every route follows the convention except for the three flagged below).

#### Conventions confirmed

| Mount prefix | Reads | Mutations | Notes |
|---|---|---|---|
| `/api/auth/*` | public (`/login`), `require_admin_scope` (`/me`) | n/a | Sales has its own `/api/sales/auth/*` lane. |
| `/api/booking/*` | public | public | Customer booking widget. Rate-limited at nginx. |
| `/api/portal/*` | public | public | Customer portal. Rate-limited at nginx, public-key gated per row. |
| `/api/admin/*` | `require_admin_scope` | `require_admin_scope` | Owner-only. |
| `/api/sales/*` | `require_sales_scope` | `require_floor_access("sales")` | Sales scope; mutations gated on punch state. |
| `/api/events/*`, `/api/contacts/*`, `/api/catalog/*` | `require_any_scope("admin", "sales")` | `require_floor_access("admin", "sales")` for floor mutations; `require_admin_scope` for destructive (DELETE) | Dual-scope reads, floor-gated writes. |
| `/api/quotes/*`, `/api/invoices/*` | `require_any_scope("admin", "sales")` | `require_floor_access("admin", "sales")` for create/send/sign/convert/patch; `require_admin_scope` for DELETE and invitation DELETE | Soft-deletes are admin-only by intent. |
| `/api/payments/*` | `require_admin_scope` | `require_admin_scope` | Sales does not record payments per non-goal "Payment capture inside the sales portal." |
| `/api/special-orders/*`, `/api/events/{id}/special-orders` | `require_admin_scope` | `require_admin_scope` | Admin/buyer function, not floor work. |
| `/api/dashboard/*`, `/api/business-profile/*`, `/api/search/*` | `require_admin_scope` | `require_admin_scope` | Admin-only by purpose. |

#### Findings + fixes

Three document mutation routes were using `require_any_scope("admin", "sales")` instead of `require_floor_access("admin", "sales")`, allowing a punched-out sales token to mutate documents. Inconsistent with quote/invoice/tried-on/participant mutations and with the Phase 7 punch-gate policy.

- [x] [event_documents.py:upload_document](api/routers/event_documents.py) — `POST /api/events/{event_id}/documents` swapped to `require_floor_access("admin", "sales")`.
- [x] [event_documents.py:patch_document](api/routers/event_documents.py) — `PATCH /api/documents/{document_id}` swapped to `require_floor_access("admin", "sales")`.
- [x] [event_documents.py:delete_document](api/routers/event_documents.py) — `DELETE /api/documents/{document_id}` swapped to `require_floor_access("admin", "sales")`. The existing `_can_delete` ownership check (uploader or admin) stacks on top.
- [x] Smoke extended in [test_clock_selfie_and_gate_smoke.py](tests/test_clock_selfie_and_gate_smoke.py): a sales token punched out trying to upload to `/api/events/{id}/documents` now returns 403 `attendance_gate`, alongside the pre-existing assertions for appointment status, notes, participants, and tried-on. Existing [test_event_documents_smoke.py](tests/test_event_documents_smoke.py) (admin-token only) still passes.

#### Anomalies considered and dismissed

- **Special orders, payments, dashboard, search, business-profile** — admin-only by intent (non-goal "Payment capture inside the sales portal", and special orders are an inventory/buyer flow). Kept `require_admin_scope`.
- **Quote/invoice/invitation DELETE** — admin-only on purpose; destructive ops follow the same pattern across routers, not the floor-mutation pattern. Kept `require_admin_scope`.
- **`/api/auth/me`** — admin-scoped on purpose; sales has its own `/api/sales/auth/me`. Two parallel scope lanes by design.
- **Public booking and portal routes** — intentionally unauthenticated; row-level public-key + nginx rate limits are the gate, not bearer tokens.

#### Sub-slice 2 deliverable

Three real punch-gate leaks closed; the doc table above is the live source-of-truth checklist. Future router additions get a one-line audit by checking the convention for their mount prefix.

### Sub-slice 3: Owner Settings Exposure + EXIF Confirmation

Three loose ends bundled together:

1. **EXIF strip confirmation.** The selfie pipeline relied on a Pillow implementation detail (WebP encoder drops EXIF when `exif=` is not passed). Added an explicit assertion in [test_attendance_owner_settings_smoke.py](tests/test_attendance_owner_settings_smoke.py) that builds a JPEG carrying Make/Model + GPS-IFD fields, runs `validate_selfie_bytes`, and asserts the resulting WebP has no top-level EXIF and an empty GPS IFD. Closes the Phase 9 "confirm" item without modifying production code.

2. **BusinessProfile API: missing fields on GET.** `attendance_gate_enabled`, `selfie_policy`, and `selfie_retention_days` were accepted in PATCH (Phase 7 Slice 2) but not returned on GET, so the frontend had no way to render the current values. Extended [BusinessProfileResponse](api/routers/business_profile.py), [BusinessProfileView](services/business_profile_service.py), and `_to_view`/`_to_response` to include all three.

3. **`biweekly_anchor_date` was unwired.** Phase 9 sub-slice 1 Priority 2 added the column and the reporting service reads it, but the BusinessProfile API never exposed it. Added to `BusinessProfilePatch` (with explicit-null clear support), the `_EDITABLE_FIELDS` set in the service, the patch handler (with ISO-string fallback for client convenience), and the response/view shape.

4. **Frontend: new "Sales staff and attendance" section** in [BusinessProfile.jsx](frontend/src/pages/BusinessProfile.jsx) with a switch for the gate, a select for the selfie policy, a numeric field for retention days (blank = keep forever), and a date picker for the biweekly pay-period anchor (blank = rolling 14-day window).

Smoke [test_attendance_owner_settings_smoke.py](tests/test_attendance_owner_settings_smoke.py) covers: EXIF strip on a real JPEG, GET response includes all four new fields, PATCH round-trips each field including explicit-null clear of retention and anchor, raw-row check via SessionLocal confirms persistence, 422 on malformed date and out-of-range retention. Existing [test_business_profile_smoke.py](tests/test_business_profile_smoke.py) still passes.

## Non-Goals (v1)

- Customer-facing features. Sales is a staff portal. The customer keeps using `/portal` and emails.
- Payment capture inside the sales portal.
- Embedded Stripe terminal or card-reader integration.
- Multi-business / multi-tenant. Bellas is one shop.
- SMS notifications.
- Native iOS or Android app.
- Offline mode, PWA install, service workers.
- Two-tier time-off approval workflow.
- Commission calculation or tip accounting.
- Stylist-to-stylist chat or task assignment.
- Customer self-service (for example, the customer signing the quote on their own phone instead of in-store) - that path already exists via `/portal` and is unchanged by this plan.

## Risks And Things To Watch

- **Systemd write path drift**. Adding clock-in selfie storage without updating the systemd unit's `ReadWritePaths` ships a feature that 500s on first use. Phase 7 puts the override change before the migration in the task list.
- **Spoofed geolocation**. Mobile browsers will accept a developer-set lat/lng. The geofence check is a deterrent, not a security boundary. Pair with the selfie capture for buddy-punching deterrence and accept that a determined attacker can defeat both.
- **PIN brute-force**. 6-digit PIN is 1M space, but the lockout window matters more than the PIN length. Confirm the lockout is row-level and not just nginx, since nginx rate limit is per-IP and a coordinated attack can spread across IPs.
- **Bundle weight**. One bundle for both subdomains is correct for v1, but if the admin app grows past 2MB JS, the sales subdomain's first paint suffers. The mitigation is route-level lazy loading; revisit if the metric drifts.
- **Pipeline auto-transition surprises**. Stylists may tap "arrived" before the appointment actually arrives (e.g. when prepping the room). The auto-promote-and-consult ships a side effect that is hard to undo. Confirmation modal in Phase 3 surfaces the consequence; if owners report churn, change the trigger to require an explicit "consulted" action separate from "arrived".
- **Sold status semantics**. Moving an event to `sold` too early will pollute the pipeline. Do not use invoice send as the v1 trigger. Use payment/deposit recorded or manual transition unless the owner explicitly defines signed invoice as sold.
- **Quote signature legality**. We capture `signature_base64`, `signature_signed_at`, `signature_ip`, `signature_name`, and (after Phase 5) `signature_user_agent`. This is solid evidentiary capture for a small-business e-signature; it is not a ESIGN-Act formal package. If a signed quote ever gets challenged in court, expect questions about consent disclosure and signer identity verification. Consider a "By signing you agree..." pre-signature checkbox if the lawyer asks.
- **Activity log volume**. Sales actions on a busy Saturday could log 200+ entries. The existing log has no retention policy. Phase 9 should confirm pagination works and decide if a retention policy is needed before the table grows past a few million rows.
- **Stylist sees customer financial data**. Sales tokens read invoices and payments-applied state. Confirm with the owner that this is acceptable; if not, scope the read access more tightly in Phase 5.
- **Time-off conflicts**. v1 does not enforce non-overlap between approved time-off and assigned shifts. Owner sees the conflict in the UI but the system does not block. If this turns into a coordination problem, add a constraint in a follow-up phase.
- **Silent auto-close payroll disputes**. Forgotten clock-outs need auto-close, but a silent normal-looking out time creates payroll disputes. Auto-close must be server-side only, structured (`auto_closed`, reason, confirmation status), visible in sales/admin UI as "needs review", and never executed by a read path.
- **Manual edit opacity**. Owner edits to time records are as sensitive as system auto-close. Every adjustment needs before/after values, actor, reason, and affected staff notification so payroll has a defensible trail.
- **Timezone drift in attendance cron**. Attendance rules are business-local even though the VPS runs UTC. Every shift lookup, holiday tag, and auto-close cutoff must use the centralized `APP_TIMEZONE` helper and frozen-clock tests around local midnight/DST.
- **Rush-hour write path**. Clock-in/out happens in bursts. Keep attendance reads bounded, avoid scheduled write jobs during opening/closing windows, and avoid making image upload the thing that delays the punch confirmation.
- **Selfie storage growth**. Every-punch camera capture gets expensive quickly, especially from phones. Convert to bounded WebP before upload, strip EXIF, enforce backend size limits, and keep retention configurable before the feature reaches real usage.
- **Cron failure invisibility**. Scheduled jobs can disappear after deployment changes. Cron health must be visible in admin and covered by a smoke test so a missing auto-close job is not discovered days later.
- **Geolocation permission friction**. iOS/Android browser and installed-web-app permission states are confusing. The clock screen needs practical retry/help copy and should capture enough error telemetry to distinguish denied permission, timeout, low accuracy, and outside-geofence cases.

## Reference Material

- OpenHRApp (https://github.com/mimnets/OpenHRApp): conceptual reference for attendance UX, selfie deterrence/WebP compression, async selfie upload, shift grace periods, earliest check-in windows, temporary shift overrides, local-time attendance cron fixes, leave/time-off status labels, notification preferences/quiet hours, attendance audit screens, missed-punch correction gaps, holiday metadata, auto-close lessons, geolocation permission UX, report exports, cron health, and storage-retention thinking. Also a cautionary reference: avoid client-side geofence authority, unbounded attendance reads, auto-closing sessions from read paths, free-text-only system actions, hard-delete time records, peak-window cron contention, and selfie upload on the critical punch-confirmation path. Build Bellas' implementation natively in FastAPI/Postgres with server-side haversine and structured audit columns.
- Existing booking widget at `widgets/bellas-booking-widget.js`: UX reference for the add-participant flow.
- Existing `frontend/src/components/QuoteEditor.jsx`, `InvoiceEditor.jsx`, `SignatureDialog.jsx`: components to mount inside the sales layout. Do not fork.
- Existing `services/event_service.py`: the `change_event_status` function is the only path to move events between pipeline statuses. The Phase 3 composite handler calls into it; do not write a parallel transition path.
- Existing `services/contact_service.py:find_or_create_contact`: the canonical contact-write path. Phase 6 calls it; do not write a parallel one.
