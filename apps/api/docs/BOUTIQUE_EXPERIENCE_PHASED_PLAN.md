# Boutique Experience phased plan

This plan covers the next layer after booking auto-promotion: making the sizing
calculator and boutique-prep questions a first-class lead-prep signal on the CRM
event/kanban side.

## Current architecture baseline

Recent booking-to-CRM updates changed the staff workflow:

- Public bookings still create an `appointments` row as the source record.
- `api/routers/booking.py:create_appointment` now auto-promotes the appointment
  to a quinceañera `events` row in `lead` status.
- Reschedules carry `crm_event_id` forward so the same lead keeps both visits.
- Customer cancellation by token and admin cancellation mirror onto the linked
  event as `cancelled`.
- `/api/events/{id}` now returns richer linked appointment details, and
  `/events/:id` has the staff-facing Booking section.
- The Appointments tab/page/manual promote UI has been removed. Calendar remains.
- `POST /api/events { from_appointment_id }` remains as an escape hatch/backfill
  path, not the normal staff workflow.

The next product goal is:

> Every lead card/event should show whether the customer completed the Boutique
> Experience questions, and staff should be able to read the answers before the
> appointment.

## Product model

Treat the sizing calculator and preference questions as a **Boutique Experience
profile**, not just free text in `appointments.customer_note`.

There are two valid customer entry points:

1. **Calculator-first browsing path**
   - Customer is not ready to book.
   - They complete sizing/preferences to get value first.
   - Result screen upsells booking: "Book with this profile."
   - Booking submission links the profile to the new appointment/lead.

2. **Booking-first committed path**
   - Customer books immediately.
   - Confirmation/scheduled emails invite them to complete the Boutique
     Experience profile before the visit.
   - The email button uses a signed token, so completion auto-attaches to the
     correct appointment without asking for confirmation code + phone.

Staff-facing state should be simple:

- `Not started`
- `Complete`
- Later, optional: `Started` / `Needs review`

## Phase 0: Confirm existing behavior and map touchpoints

Purpose: make sure tomorrow's work starts from the real current code, not an
older mental model.

Tasks:

- Re-read appointment statuses in `database/migrations/005_create_appointments.py`
  and `api/routers/admin_booking.py`.
- Re-read event statuses in `services/event_workflow.py` and migration 015.
- Re-check customer cancel/reschedule endpoints in `api/routers/booking.py`.
- Re-check admin cancellation mirroring in `api/routers/admin_booking.py`.
- Re-check full event response shape in `api/routers/events.py`.
- Re-check full event UI in `frontend/src/pages/EventDetail.jsx`.
- Re-check drawer summary in `frontend/src/components/EventQuickViewDrawer.jsx`.
- Re-check current fit-prep handoff in:
  - `widgets/bellas-fit-prep-tool.js`
  - `widgets/bellas-booking-widget.js`
  - `marketing/index.html`
  - `marketing/fit-prep.html`

Deliverable:

- Short implementation notes in the PR description or this doc's follow-up
  section if reality differs from the assumptions above.

## Phase 1: Define the Boutique Experience data contract

Purpose: stop relying on localStorage/free-text note stuffing as the primary
integration.

Preferred backend shape:

- Reuse/extend `appointment_enrichment_responses` if it already matches the
  desired ownership model: one profile per appointment.
- If it is too survey-specific, add a dedicated table such as
  `boutique_experience_profiles`.

Minimum fields:

- `id`
- `appointment_id` nullable at first, required once linked
- `visitor_id` nullable
- `session_id` nullable
- `source`: `pre_booking`, `post_booking_email`, `manual_attach`
- `submitted_at`
- Measurements:
  - `bust`
  - `waist`
  - `hips`
  - `height_ft`
  - `height_in`
- Computed sizing:
  - `estimated_size_low`
  - `estimated_size_high`
  - `size_by_bust`
  - `size_by_waist`
  - `size_by_hips`
  - `chart_source`
  - `off_chart`
- Preferences:
  - `style`
  - `back`
  - `budget`
  - `colors`
  - `likes`
  - `avoids`
- Staff display:
  - `summary`

API contract:

- `POST /api/booking/boutique-experience`
  - Creates an unlinked profile for calculator-first users.
  - Returns `profile_id`.
- `POST /api/booking/boutique-experience/{token}`
  - Creates or updates the profile for a token-linked appointment.
  - Token identifies the appointment; customer should not need to type phone.
- Booking submission accepts optional `boutique_experience_profile_id`.
  - On successful appointment creation, link that profile to the new appointment.

Security:

- Use signed tokens from `services/booking_tokens.py`, similar to reschedule and
  cancel.
- Token purpose should be explicit, e.g. `boutique_experience`.
- Do not put phone/email in the URL.

Deliverable:

- Migration/model/API contract decided and documented before UI changes.

## Phase 2: Backend write path

Purpose: make both customer entry points attach structured data to the right
appointment.

Tasks:

- Add or extend database model/migration.
- Add Pydantic request/response schemas in `services/booking_contracts.py`.
- Add token helper in `services/booking_tokens.py`.
- Add public endpoint(s) in `api/routers/booking.py`.
- Update `AppointmentSubmission` to accept an optional profile id.
- On `create_appointment`:
  - create appointment,
  - auto-promote to event,
  - link any pre-booking profile to the appointment,
  - preserve current best-effort behavior around event promotion/notifications.
- On reschedule:
  - keep the profile attached to the original appointment as historical data,
    and surface latest completed profile at the event level.
  - Do not duplicate profile rows unless the customer submits a new profile.

Important behavior:

- If profile linking fails, booking should still succeed.
- If profile submission by token fails, return a clear customer-safe error.
- Keep existing `/appointments/attach-note` for compatibility until the new
  path is fully live.

Deliverable:

- Structured profile rows can be created pre-booking, linked during booking,
  and created directly from a post-booking token link.

## Phase 3: Event API read shape

Purpose: make the lead board and full event page know whether Boutique
Experience is complete.

Tasks:

- Extend `LinkedAppointmentSummary` in `api/routers/events.py` with prep status:
  - `boutique_experience_status`
  - `boutique_experience_submitted_at`
  - `boutique_experience_summary`
  - `boutique_experience` structured object, at least on full detail
- Decide whether board cards need only status or also highlights.
  - Recommended quick drawer/card summary:
    - complete/incomplete
    - estimated size range
    - style
    - budget
  - Recommended full event view:
    - all measurements/preferences
    - generated summary
    - source/submitted timestamp

Deliverable:

- `GET /api/events/{id}` provides everything the full event page needs.
- Optional: `GET /api/events/board` provides lightweight prep status for faster
  card badges without an extra detail fetch.

## Phase 4: Fit prep widget update

Purpose: make the calculator feel like a Boutique Experience profile and remove
the confusing two-path handoff.

Tasks:

- Rename customer-facing copy from "sizing calculator" where appropriate to
  "Boutique Experience" or "Boutique Experience Profile."
- Keep sizing estimate as the immediate value, but frame preferences as how the
  stylist prepares.
- If page URL includes a valid token:
  - hide confirmation-code/phone attach UI,
  - show one CTA: "Send to my stylist",
  - submit to token endpoint,
  - show completed confirmation.
- If there is no token:
  - after result, CTA says "Book with this profile",
  - create an unlinked profile or save profile payload locally,
  - scroll/open booking widget with the profile id/payload attached.
- Keep localStorage fallback for browsers until the server profile path is
  stable.

Deliverable:

- Calculator-first and email-token flows both work without making the customer
  choose between confusing attach modes.

## Phase 5: Booking widget update

Purpose: make booking-first customers naturally complete the profile after
booking, and calculator-first customers book with their profile.

Tasks:

- Accept `boutiqueExperienceProfileId` or equivalent config in
  `BellasBookingWidget.init`.
- Include profile id in appointment submission.
- On success screen, add a CTA:
  - "Complete your Boutique Experience Profile"
  - link target should use the tokenized URL returned by the booking response,
    or a generated confirmation-email-style URL if added to `AppointmentResponse`.
- If a profile is already attached, show:
  - "Boutique Experience profile added"
  - avoid asking again.
- Move duplicated prefill-notice/remove logic out of marketing pages and into
  the booking widget if it still exists.

Deliverable:

- Booking-first path becomes: book appointment, then complete profile as the
  natural next step.

## Phase 6: Staff UI

Purpose: give staff a fast yes/no signal and a useful prep view.

Tasks:

- Update `EventQuickViewDrawer.jsx`:
  - keep summary-style.
  - show latest booking time/contact.
  - add Boutique Experience badge:
    - Complete / Not started
    - size estimate if complete
    - style/budget highlights if complete
- Update `EventDetail.jsx` Booking section:
  - add a clear Boutique Experience subsection per appointment/event.
  - show all structured answers when complete.
  - show "Not completed yet" when missing.
  - keep appointment lifecycle/source details already added.
- Consider staff action later:
  - "Send reminder" button once email templates are live.

Deliverable:

- Staff can open any lead and immediately know whether the customer filled out
  Boutique Experience questions.

## Phase 7: Email/token flow

Purpose: prepare for branded emails/reminders that drive profile completion.

Tasks:

- Extend notification templates in `services/notification_templates.py`:
  - confirmation email CTA,
  - reminder email CTA,
  - later optional "prep incomplete" nudge.
- Use brand colors/fonts already present in current templates.
- CTA copy direction:
  - "Complete your Boutique Experience Profile"
  - "Help us prepare dresses in your size, style, and budget before you arrive."
- Token URL:
  - `PUBLIC_SITE_URL/fit-prep.html?token=...`
  - token purpose: `boutique_experience`
- Notification service should only include nudge if profile is incomplete.

Deliverable:

- Email link opens the profile form and auto-attaches completion to the exact
  appointment/lead.

## Phase 8: Backfill and cleanup

Purpose: keep existing data readable while moving off the older note-attach
pattern.

Tasks:

- Leave existing `customer_note` summaries in place.
- Optional migration/script:
  - parse existing "Fit Prep Summary (Bella's XV)" notes into structured
    profile rows when feasible.
  - mark source as `legacy_note`.
- Keep `/appointments/attach-note` until no live email/link uses it.
- Once safe, remove old code paths:
  - confirmation-code + phone attach UI,
  - localStorage-only prefill UI,
  - dead marketing-page glue.

Deliverable:

- No existing customer prep notes disappear, and the new UI can gradually become
  the only customer path.

## Phase 9: Tests and verification

Backend smoke tests:

- `tests/test_booking_smoke.py`
  - booking without profile still succeeds and auto-promotes.
  - booking with pre-booking profile links it to appointment/event.
  - response includes token/profile CTA data if added.
- `tests/test_events_smoke.py`
  - event detail shows profile incomplete when missing.
  - event detail shows complete profile fields when present.
  - reschedule keeps event linkage and latest booking/profile shape stable.
- `tests/test_attach_note_smoke.py`
  - keep passing while legacy endpoint remains.
  - add deprecation coverage only if behavior changes.
- New focused smoke test:
  - tokenized Boutique Experience submission links to appointment without phone.
  - invalid/wrong-purpose token is rejected.

Frontend verification:

- `npm run build`.
- Manual browser check:
  - calculator-first -> book with profile -> event detail complete.
  - booking-first -> success CTA -> complete profile -> event detail complete.
  - email-token URL -> complete profile -> event detail complete.
  - missing profile lead shows incomplete badge.

Smoke command set:

```bash
venv/bin/python tests/test_booking_smoke.py
venv/bin/python tests/test_admin_booking_smoke.py
venv/bin/python tests/test_events_smoke.py
venv/bin/python tests/test_attach_note_smoke.py
cd frontend && npm run build
```

## Recommended implementation order for tomorrow

1. Confirm whether to reuse `appointment_enrichment_responses` or create a new
   `boutique_experience_profiles` table.
2. Add signed token purpose and backend contract.
3. Add structured write/read path.
4. Surface complete/incomplete in event detail API.
5. Update full event page first.
6. Update quick drawer second.
7. Update fit-prep token flow.
8. Update booking success CTA.
9. Add tests and run smoke suites.

## Open decisions

- Naming: "Boutique Experience Profile" vs "Fit Prep Profile".
- Data storage: extend `appointment_enrichment_responses` vs new table.
- Board-level data: fetch prep status in `/events/board` or only in drawer
  detail fetch.
- Legacy attach endpoint retirement timeline.
- Whether profile completion should append a readable summary to
  `appointments.customer_note` in addition to storing structured data.

## Phase 0 follow-up notes (2026-04-30)

Re-read of the touchpoints listed in Phase 0. Most of the assumed baseline
holds; a few items diverge from the doc and one is a real bug worth fixing
before later phases lean on it.

### Confirmed matches

- Auto-promote in `api/routers/booking.py:create_appointment` (line 292) runs
  best-effort after the appointment commits, exactly as described.
- Reschedule carries `crm_event_id` forward (`api/routers/booking.py:575`).
- Customer-token cancel mirrors onto the linked event
  (`api/routers/booking.py:640`); admin cancel mirrors at
  `api/routers/admin_booking.py:373`. Both swallow `EventServiceError`.
- `GET /api/events/{id}` returns `LinkedAppointmentSummary` with an embedded
  `LinkedAppointmentEnrichment` payload (`api/routers/events.py:144-167`,
  `:443-455`).
- `EventDetail.jsx` Booking section already shows the enrichment fields when
  present (`frontend/src/pages/EventDetail.jsx:138-169`); `EventQuickViewDrawer`
  shows latest-booking summary but no enrichment yet
  (`frontend/src/components/EventQuickViewDrawer.jsx:129-162`).
- `pages/Appointments.jsx` is removed (visible in `git status`); calendar route
  remains.
- `POST /api/events { from_appointment_id }` escape hatch still present
  (`api/routers/events.py:227`).

### Reality diverges from doc

- `appointments.status` no longer accepts `'abandoned'`. Migration 012
  (`database/migrations/012_remove_abandoned_appointment_status.py`) dropped it
  because abandons live in `appointment_session_events`. Current valid values
  are `pending|confirmed|attended|no_show|cancelled|rescheduled`. The doc's
  Current-architecture section is silent on this, but it's already in memory.
- `appointment_enrichment_responses` (migration 010) already covers the
  preferences half of the Boutique Experience profile: `dress_styles`, `colors`,
  `budget_range`, `quince_theme`, `quince_theme_colors`, `court_size`,
  `inspiration_photos`, `free_text`, plus `opened_at` / `submitted_at` and a
  raw payload. One row per appointment via `UNIQUE(appointment_id)` and
  `ON DELETE CASCADE`. It does **not** carry sizing/measurements, computed
  size range, or an explicit `source`. **Recommendation:** extend this table
  rather than introduce `boutique_experience_profiles` — the ownership model
  already matches and the table is already wired into both `events.py` and
  `admin_booking.py` read paths. Trade-off: forces null measurements for the
  legacy survey rows that predate the calculator merge, which is acceptable.
- A signed token purpose `enrichment` already exists in
  `services/booking_tokens.py` (TTL 30 days, separate
  `ENRICHMENT_TOKEN_SECRET`). Phase 1's "boutique_experience" purpose can
  reuse this rather than introduce a third secret. If the naming of the
  product moves to "Boutique Experience," rename the purpose constant in one
  pass; tokens are short-lived enough that re-keying is fine.

### Real gap to fix on the way

- The enrichment email is already wired and firing: `enqueue_for_new_booking`
  schedules an `enrichment_invitation` at T+2min
  (`services/notification_service.py:89-96`), the template renders an
  `enrichment_url(...)` link (`services/notification_templates.py:325-358`),
  and that helper builds `PUBLIC_SITE_URL/preferences/{token}`
  (`services/booking_tokens.py:78`). **There is no router serving
  `/preferences/{token}` and no marketing page at that path.** Customers who
  receive the email today click into a 404. Phase 2 needs to either redirect
  `/preferences/{token}` to the new fit-prep page with the token in the URL,
  or replace the helper with `fit-prep.html?token=...` once the widget knows
  how to redeem it. Either way, do not ship Phase 7 (email/token flow)
  without closing this; the email is already in production.

### Touchpoints inventoried for later phases

- Calculator-side handoff today is two paths in a single panel:
  `widgets/bellas-fit-prep-tool.js:883-1034` renders both "attach to existing
  appointment by code+phone" (POST `/api/booking/appointments/attach-note`)
  and "save to localStorage for the booking widget below" (`bxv_fit_prep_summary`
  key, also exposed via `BellasBookingWidget.setNote`). `marketing/index.html`
  and `marketing/fit-prep.html` both reach into that localStorage key to show a
  "summary added" notice with a Remove button. Phase 4 collapses all of this
  once a server-side profile id exists; Phase 8 can then delete the
  prefill-notice DOM and the `setNote` glue.
- The fit-prep tool already supports a URL-prefilled confirmation code
  (`?code=BX-XXXXXX`, `widgets/bellas-fit-prep-tool.js:134-144`,
  `:1097-1101`). The token-bearing URL in Phase 4 should hide the
  code+phone fields entirely rather than prefill them, since the token already
  identifies the appointment.
- `fit_prep_url(confirmation_code)` in `services/booking_tokens.py:81-89` is
  the current calculator link helper; it is not yet referenced from any
  template. Worth keeping in mind when the email CTA changes.

## Phase 1 decisions (2026-04-30)

Locking in the data contract and token strategy before any endpoint code in
Phase 2. Migration is already drafted and applied locally
(`database/migrations/016_extend_enrichment_for_boutique_experience.py`),
the SQLAlchemy model is updated, and the schema was validated with real
INSERTs covering NULL-appointment rows, multi-NULL coexistence under
UNIQUE, the appointment-linked path, the UNIQUE rejection of duplicate
appointment ids, and the CHECK rejection of unknown source values.

### Storage: extend `appointment_enrichment_responses`

Phase 0 found this table already has the right ownership model. Phase 1
extends it rather than introducing a parallel table, because the existing
event-detail and admin-detail read paths already join on it.

Migration 016 applied:

- `appointment_id` is now nullable. The existing UNIQUE index is
  unchanged, since Postgres treats NULLs as distinct, so multiple
  unlinked profiles coexist.
- New columns added (all nullable):
  - Identity: `visitor_id UUID`, `session_id VARCHAR(64)`,
    `source VARCHAR(32)`.
  - Measurements: `bust_inches`, `waist_inches`, `hips_inches` as
    `NUMERIC(4,1)`; `height_ft`, `height_in` as `SMALLINT`.
  - Computed sizing: `estimated_size_low`, `estimated_size_high`,
    `size_by_bust`, `size_by_waist`, `size_by_hips` as `SMALLINT`;
    `chart_source VARCHAR(120)`; `off_chart BOOLEAN`.
  - Calculator preferences: `style_preference`, `back_preference`,
    `budget_preference` as `VARCHAR(40)`; `color_preferences_text`,
    `likes`, `avoids` as `TEXT`.
  - Staff display: `summary TEXT`.
- `CHECK (source IN ('pre_booking', 'post_booking_email',
  'manual_attach', 'enrichment_survey', 'legacy_note'))`, NULL allowed
  for legacy rows that predate this migration.
- Partial index `idx_aer_visitor_id` on `visitor_id WHERE NOT NULL`, so
  we can find a returning visitor's pre-booking profile without
  re-asking for measurements.

The calculator's preference fields get their own columns rather than
overloading the survey's `dress_styles` and `colors` arrays, because the
shapes differ (single-select vs multi-select; free text vs chip array).
Staff UI in Phase 6 renders both blocks when both are populated.

Phase 3 derives "Complete / Not started" from `submitted_at IS NOT NULL`
plus the presence of either measurements or any preference column. No
extra status column.

### Token strategy: reuse `enrichment` purpose

The existing `enrichment` purpose, `ENRICHMENT_TOKEN_SECRET`, and 30-day
TTL in `services/booking_tokens.py` are reused. No new purpose constant.

Phase 2 will change `enrichment_url(...)` to mint
`PUBLIC_SITE_URL/fit-prep.html?token=...` instead of
`PUBLIC_SITE_URL/preferences/{token}`, so the email already going out at
T+2min reaches the calculator surface (which is the only customer
profile UI). In-flight emails minted before the cutover keep linking to
the dead `/preferences/{token}` for up to 30 days; that tail is
acceptable. If volume warrants it, Phase 8 can drop a Nginx redirect.

### API contract for Phase 2

Two new public endpoints under the existing `booking` router prefix
(`/api/booking`):

- `POST /api/booking/boutique-experience`
  - body shape `BoutiqueExperienceSubmission` (measurements, computed
    sizing, calculator preferences, summary, optional `visitor_id` /
    `session_id`); no `appointment_id` accepted on this path.
  - inserts a row with `source = 'pre_booking'` and `appointment_id =
    NULL`.
  - returns `{ profile_id }`.
- `POST /api/booking/boutique-experience/{token}`
  - same body shape.
  - resolves `appointment_id` from the token (purpose `enrichment`).
    Upserts: if a row with that `appointment_id` already exists, update
    in place; otherwise insert a fresh row with `source =
    'post_booking_email'`.
  - returns `{ profile_id, slot_start, timezone, confirmation_code }`.
  - 404 on bad or expired token; 409 if the appointment is in
    `cancelled`, `rescheduled`, `attended`, or `no_show`.

`AppointmentSubmission` gains an optional
`boutique_experience_profile_id: int | None`. After
`create_appointment` commits the new appointment and before
auto-promotion, the service links the profile by setting its
`appointment_id`. Linking is best-effort, the same way auto-promotion
is: if it fails, the booking still succeeds and the profile stays
orphaned for support to re-attach.

`POST /api/booking/appointments/attach-note` keeps working untouched for
backwards compatibility; Phase 8 retires it once no live email or marketing
link uses it. We do not add a `/preferences/{token}` redemption endpoint;
the dead link is closed by changing what `enrichment_url` mints.

### Naming

"Boutique Experience Profile" is the customer-facing label everywhere.
Internal column, table, and token-purpose names stay as-is to avoid a
noisy rename across the codebase.

## Phase 2 follow-up notes (2026-04-30)

Backend write path is live locally. Phase 3 can now lean on these endpoints
and contracts.

### What landed

- `services/booking_contracts.py`: `BoutiqueExperienceMeasurements`,
  `BoutiqueExperienceSizing`, `BoutiqueExperiencePreferences`,
  `BoutiqueExperienceSubmission`, `BoutiqueExperienceCreatedResponse`,
  `BoutiqueExperienceTokenResponse`. `AppointmentSubmission` gained the
  optional `boutique_experience_profile_id`.
- `services/booking_service.py`: `create_pre_booking_profile`,
  `upsert_profile_for_appointment`, `link_profile_to_appointment`, plus a
  shared `_apply_profile_payload` that stamps `submitted_at`, preserves
  the original `source` on upsert, and stashes the raw payload.
- `api/routers/booking.py`: `POST /api/booking/boutique-experience`
  (returns 201 with `profile_id`) and
  `POST /api/booking/boutique-experience/{token}` (200 on upsert; 404 on
  bad or wrong-purpose token; 409 on cancelled / rescheduled / attended /
  no-show appointments). `create_appointment` now links any pre-booking
  profile after the appointment commits, before auto-promotion, with the
  same swallow-and-log pattern auto-promotion uses.
- `services/booking_tokens.py`: `enrichment_url` now mints
  `PUBLIC_SITE_URL/fit-prep.html?token=...` instead of
  `PUBLIC_SITE_URL/preferences/{token}`. The existing T+2min email already
  reaches a real page; Phase 4 makes the page read the token. Today the
  page falls back to the existing code+phone flow, so the customer
  experience is strictly better than the prior 404.

### Behavior decisions made during the build

- `link_profile_to_appointment` is idempotent on the same appointment id
  but refuses to relink to a different one, refuses an unknown profile
  id, and refuses to link if another profile already exists for the
  target appointment. Caller swallows all `False` returns.
- `upsert_profile_for_appointment` reuses the row tied to that
  `appointment_id` and rewrites everything except `created_at` and the
  original `source`. Re-submitting via the email link replaces in place.
- `_apply_profile_payload` only stamps `visitor_id` if the payload
  carries one, so a token re-submit won't blank a visitor id captured by
  the original pre-booking row.

### Validation

A purpose-built smoke run exercised every code path against the local
DB and the FastAPI test client, then cleaned up:

- service: pre-booking insert, link success, link idempotence, link
  refuse on different appointment, link refuse on unknown id, upsert
  insert, upsert update reuses row, link refuse when target appt
  already has a profile.
- HTTP: POST /boutique-experience 201, POST /boutique-experience/{token}
  200, token re-submit upserts in place, bad token 404, wrong-purpose
  token 404, cancelled appt 409.

The five existing smoke suites (booking, attach-note, events,
admin-booking, notifications) all still pass, and the notifications run
exercised the new `enrichment_url` shape end-to-end without changes.

### What is intentionally not done in Phase 2

- No `GET /api/booking/boutique-experience/{token}`. Phase 4 will add it
  once the fit-prep page needs to render the customer's name + slot
  before the form.
- No `/preferences/{token}` redemption endpoint. The dead URL is closed
  by the helper change, not by serving the path. In-flight 30-day
  emails minted before this commit still 404; rate is low enough to
  accept, and Phase 8 can add a Nginx redirect if it stops being low.
- `POST /api/booking/appointments/attach-note` and the calculator's
  code+phone attach UI are untouched. Phase 4 / Phase 8 retire them.
- `customer_note` is not auto-populated from the profile summary. The
  open decision in the parent doc still applies; revisit during Phase 6
  staff-UI work.

## Phase 3 follow-up notes (2026-04-30)

Event read shape now exposes Boutique Experience status everywhere staff
look. Phase 6 can wire up the UI without touching the API.

### What landed

- `api/routers/events.py`: new `BoutiqueExperienceProfile` schema covers
  the full structured payload (measurements, computed sizing, calculator
  preferences, summary, source, submitted_at). `LinkedAppointmentSummary`
  gained `boutique_experience_status` (`complete` | `not_started`),
  `boutique_experience_submitted_at`, `boutique_experience_summary`, and
  the structured `boutique_experience` object.
- `BoardCardResponse` gained `boutique_experience_status`. The board does
  not carry highlights (size range, style, budget) yet; the drawer
  already fetches `/api/events/{id}` for those, so duplicating them on
  the card would just bloat the board payload without saving a request.
- `services/event_service.py`: `get_board_data` now joins a per-event
  `bool_or(submitted_at IS NOT NULL)` subquery against
  `appointment_enrichment_responses` so a single SQL trip computes the
  card flag across an event's full appointment history. Reschedules
  surface "complete" if the original visit's profile is complete, even
  after the new appointment lands.
- `_to_linked_appointment` and the new `_to_boutique_experience_profile`
  + `_profile_status` helpers in `events.py` keep the conversion logic
  in one place. `Decimal` measurements are coerced to `float` at the
  edge so the JSON payload stays simple for the React app.

### Decision change vs Phase 1 plan

Phase 1 follow-up said status would derive from `submitted_at IS NOT
NULL` *plus* the presence of measurements or preferences. Phase 3
implements just `submitted_at IS NOT NULL`. The presence-of-data check
moved one level up to a `model_validator` on
`BoutiqueExperienceSubmission`, so:

- One signal in two places (board SQL aggregation + Python detail
  helper) is consistent without a column-by-column SQL check.
- The validator rejects fully-empty payloads at the API edge with 422
  (was originally missed; see amendment below). With that gate in
  place, every row that has `submitted_at` set also has at least one
  meaningful field.
- If we ever see ghost-complete cards, tightening to a per-row check is
  a five-line change; loosening later is harder.

The structured `boutique_experience` object stays present even when
`submitted_at` is NULL, so a row mid-session (e.g. "started" if we add
that state later) still surfaces whatever the customer typed.

### Amendment (2026-04-30, post-review)

Review caught that `BoutiqueExperienceSubmission.model_validate({})`
originally succeeded — every nested object had a default factory and
every field was nullable, so `{}` slipped through and the write path
stamped `submitted_at`, marking the lead "complete" with no real data
behind it. That contradicted the claim immediately above this note.

Fix in `services/booking_contracts.py`: a `model_validator(mode="after")`
on `BoutiqueExperienceSubmission` now requires at least one of
measurements, sizing, preferences (non-empty string), or summary
(non-whitespace). Empty and placeholder-only submissions return 422.

Also caught: the Phase 3 behavior was only verified by an ad hoc
script. Added `tests/test_boutique_experience_smoke.py` covering
every Phase 1-3 contract: validator rejection, pre-booking create,
profile linking through booking submission, event detail structured
profile + status, board card status, token upsert, bad / wrong-purpose
token, cancelled-appt 409, and the cleared-`submitted_at` round trip.
All five existing smoke suites still pass.

## Phase 4 follow-up notes (2026-04-30)

Fit-prep widget is rebuilt around the token flow. The two-path
attach UI is gone.

### What landed

- `widgets/bellas-fit-prep-tool.js` was substantially rewritten:
  - Reads `?token=...` from the URL on init (was `?code=BX-...`).
  - State tracks `token` and a single `handoff` machine
    (`idle | submitting | sent | error`) instead of the old
    expanded/code/phone fields.
  - Result screen now renders one of two handoffs. With a token, a
    single "Send to my stylist" CTA POSTs to
    `/api/booking/boutique-experience/{token}` and shows a "Sent to
    your stylist · See you on $slot" success state. Without a token,
    a single "Book with this profile" CTA POSTs to
    `/api/booking/boutique-experience`, stores both the new
    `bxv_boutique_profile_id` localStorage key (Phase 5 will consume
    it) and the legacy `bxv_fit_prep_summary` summary (so today's
    booking widget still gets the prefill notice), then surfaces a
    "Continue to booking" button that scrolls to the booking widget.
  - On any server failure in the no-token path, falls back to the
    legacy localStorage handoff so the customer can still book with a
    summary attached. Plan called this out as the required behavior
    while the server profile path stabilizes.
  - Token-flow error states map 404 / 409 / 422 to specific customer
    copy and route everything else to a generic "call us" message.
  - `state.attach`, `submitAttach`, `attachPrepNote`,
    `formatAttachedSlot`, `isSummaryAdded`, `readCodeFromUrl`, and
    every CSS class under `-attach-*`, `-pill-add`, `-pill-added` are
    deleted. The widget no longer asks for a confirmation code or
    phone number.
  - Added `BellasBookingWidget.setBoutiqueExperienceProfileId(id)`
    bridge call alongside the legacy `setNote(summary)` so Phase 5
    has a clean handoff target.
- `marketing/fit-prep.html`: page title, meta, and intro copy switch
  from "Fit Prep & Sizing Calculator" to "Boutique Experience
  Profile" with new lede framing the page as pre-fitting prep rather
  than an educational sizing tool.
- Result-screen eyebrow inside the widget reads "Your Boutique
  Experience Profile". Form-step labels and the chart wording were
  left alone since they are accurate and not the renaming targets
  the plan called out.

### Validation

- `node --check` on the rewritten widget: clean.
- `npm run build` from `frontend/`: clean (same large-chunk warning
  the project already had).
- HTTP smoke against a local uvicorn:
  - `GET /marketing/fit-prep.html` -> 200.
  - `GET /widgets/bellas-fit-prep-tool.js` -> 200.
  - `POST /api/booking/boutique-experience` with `{}` -> 422.
  - `POST /api/booking/boutique-experience` with a real payload ->
    `{profile_id, source: pre_booking}`.
- All six back-end smokes still pass:
  `booking`, `attach-note`, `events`, `admin booking`,
  `notifications`, `boutique experience`.
- Static grep confirms every old attach-flow string and helper is
  gone from the widget, and every new endpoint path / CTA copy is
  present.

### What is intentionally not done in Phase 4

- **Manual browser smoke is unverified by me**, since this
  environment has no browser tooling. The widget's actual rendering,
  click handlers, error message display, and scroll behavior should
  be exercised in a real browser (Chromium / Safari / iOS Safari)
  before Phase 4 ships. Concrete checklist:
  - `/fit-prep.html` (no token) -> fill form -> "Book with this
    profile" -> success state shows "Continue to booking" -> click
    scrolls to the booking widget.
  - `/fit-prep.html?token={good}` -> fill form -> "Send to my
    stylist" -> success shows the slot label.
  - `/fit-prep.html?token=bogus` -> submit -> error copy renders,
    button re-enables on retry.
  - On submit success, devtools should show the new
    `bxv_boutique_profile_id` localStorage key (no-token path only).
- No Phase 5 booking-widget plumbing yet. The handoff bridge
  (`setBoutiqueExperienceProfileId`) is wired on the fit-prep side;
  the booking widget needs to add the matching method and start
  including the id in `POST /appointments`.
- No GET endpoint at `/api/booking/boutique-experience/{token}`
  yet. The plan kept this option open; right now the page does not
  render the customer's name / slot before the form. Add it if
  copy review wants the page to greet returning customers by name.
- Home-page CTA on `marketing/index.html` ("Try our sizing
  calculator") was left alone. It still points to the renamed page;
  flipping the CTA copy is a separate marketing decision.

### Amendment (2026-04-30, post-review)

Review caught two state-machine bugs in the rebuilt widget.

**1. Edit Answers leaves stale handoff state.** The result screen
checks `state.handoff.status === 'sent'` to render the success
indicator, but neither "‹ Edit answers" nor any input/pill mutation
reset the handoff. A customer could submit, click Edit Answers,
revise measurements, return to the result via "See my prep guide,"
and the screen would still say "Sent / Saved" without re-posting
the revised profile. Affected both token and no-token flows.

Fix: extracted the initial-state shape into a `freshHandoff()`
helper and reset `state.handoff = freshHandoff()` at two boundaries:
- The "See my prep guide" click in `renderStep2`. Each click is a
  fresh CTA opportunity, so the result screen always shows the
  submit button when the customer just (re)entered the result step.
- The "‹ Edit answers" click in `renderResult`. Drops the success
  indicator immediately on departure, so any future path back to
  the result step starts clean.

The CTA-click reset alone is sufficient to fix the bug, but resetting
on Edit Answers as well keeps the state machine symmetric and makes
the invariant easier to read.

**2. Server-save fallback can leave a stale profile id in
localStorage.** In `submitPreBookingHandoff`, both the
server-rejection branch (non-OK response) and the network-error
branch save the textual summary and mark the handoff `sent`, but
neither cleared `bxv_boutique_profile_id`. If the browser carried
a profile id from a previous session, Phase 5 would submit that
stale id alongside the new booking while the visible summary
reflected different answers.

Fix: both fallback branches now call `clearProfileId()` before
`saveSummary(...)`. After a fallback, the booking widget reads no
profile id and only the legacy summary is used, which matches the
data the customer actually approved.

Both fixes are wired and `node --check` is clean. All six back-end
smokes still pass. The browser-only manual smoke checklist from the
original Phase 4 follow-up still applies; add these two new cases:
- Submit (no-token), click Edit Answers, change a measurement, click
  "See my prep guide" again. Result screen should show "Book with
  this profile" again, not "Saved for your booking."
- Pre-seed `localStorage.setItem('bxv_boutique_profile_id', '999')`,
  open the page (no token), block the network on POST. After the
  fallback, devtools should show no `bxv_boutique_profile_id` key
  (only `bxv_fit_prep_summary`).

## Phase 5 follow-up notes (2026-04-30)

Booking widget is the natural next step for both customer entry points.

### What landed

- `services/booking_contracts.py`: `AppointmentResponse` gained
  `boutique_experience_url` (the tokenized fit-prep URL the success
  screen invites the customer to open) and
  `boutique_experience_attached` (`True` if a profile is already
  linked to the appointment).
- `api/routers/booking.py`: `_appointment_to_response` is now
  `_appointment_to_response(db, appt)` and computes both new fields.
  All four call sites (idempotent existing, new appointment, race
  fallback, reschedule) updated.
- `widgets/bellas-booking-widget.js`:
  - Reads `bxv_fit_prep_summary` and `bxv_boutique_profile_id` from
    localStorage at init. Config still wins: `prefillNote: ""`
    explicitly clears the handoff;
    `boutiqueExperienceProfileId: null` does the same for the id.
  - `state.boutiqueExperienceProfileId` rides along in the
    appointment submission body, and `state.notePrefilled` flips on
    so step 3 can render an in-widget prefill banner.
  - Public method `setBoutiqueExperienceProfileId(id)` exposed so
    the fit-prep widget's existing handoff bridge actually has a
    target. Accepts `null` to clear.
  - On success the widget calls `clearBoutiqueExperienceHandoff()`,
    which removes both localStorage keys in one shot. (Was
    previously only clearing the summary.)
  - Success screen branches on `c.boutique_experience_attached`:
    attached -> green "Boutique Experience profile added" badge.
    not attached -> "One last optional step" callout with a primary
    link to `c.boutique_experience_url`. Older API responses
    without those fields render the same as before.
  - Step 3 shows an in-widget prefill notice + Remove button when
    the note was prefilled and not yet edited away. Remove drops
    the textarea, clears `bxv_fit_prep_summary`, and re-renders.
- `marketing/index.html` and `marketing/fit-prep.html` both lost the
  duplicated `booking__prefill-notice` DOM, the inline glue that
  read localStorage, and the wired-up Remove button. The widget owns
  this end-to-end now. Marketing pages init with just
  `{ containerId, apiBaseUrl }`.

### Validation

- `node --check` clean for both widgets.
- `npm run build` clean (same chunk-size warning as before).
- All six back-end smokes pass:
  `booking`, `attach-note`, `events`, `admin booking`,
  `notifications`, `boutique experience`.
- Smoke regression: `test_booking_smoke.py` now asserts
  `boutique_experience_attached is False` and the URL contains
  `/fit-prep.html?token=` for a no-profile booking;
  `test_boutique_experience_smoke.py` asserts
  `boutique_experience_attached is True` for a booking made with a
  pre-booking profile id.

### What is intentionally not done in Phase 5

- **Manual browser smoke is unverified by me.** The success-screen
  branch, in-widget prefill banner, Remove button, and the localStorage
  cleanup on submit need a real browser. New cases on the manual
  checklist:
  - Calculator-first -> book with profile -> success screen shows
    green "profile added" badge, no CTA link.
  - Booking-first (no fit-prep) -> success screen shows "Complete your
    Boutique Experience Profile" CTA linking to
    `fit-prep.html?token=...`. Click opens the page; widget reads
    the token; submitting the form lands on an event detail page that
    flips status to `complete`.
  - On submit success, devtools should show neither
    `bxv_fit_prep_summary` nor `bxv_boutique_profile_id` in
    localStorage.
  - With a prefilled note, step 3 shows the in-widget banner with
    Remove. Remove drops the note and the banner, leaves the form
    submittable.
- No GET endpoint at `/api/booking/boutique-experience/{token}`.
  The success-screen CTA points the customer at the existing
  fit-prep page; the page does not greet the customer by name yet.
  Add a GET later if copy review wants it.
- Home-page sizing-CTA copy on `marketing/index.html` still reads
  "Try our sizing calculator" — left for a separate marketing pass.

### Amendment (2026-04-30, post-review)

Review caught that the init-time config resolution for
`boutiqueExperienceProfileId` treated `null` the same as "key
absent" and fell back to `readStoredProfileId()`. That contradicted
the documented contract ("`boutiqueExperienceProfileId: null`
clears") and would attach a stale localStorage id even when an
embed explicitly tried to suppress it.

Fix in `widgets/bellas-booking-widget.js`: only `=== undefined`
falls back to localStorage now. Any explicit value (including
`null`, `0`, or a non-numeric string) suppresses the localStorage
read and clears the id. The same shape applied to `prefillNote`
for symmetry: previously a `null` config value silently fell back
to the stored summary; now only an absent key does.

Code path is now:

```js
if (config.boutiqueExperienceProfileId === undefined) {
  state.boutiqueExperienceProfileId = readStoredProfileId();
} else {
  var n = parseInt(config.boutiqueExperienceProfileId, 10);
  state.boutiqueExperienceProfileId = (isFinite(n) && n > 0) ? n : null;
}
```

`node --check` clean; all six back-end smokes still pass. Add to
the manual browser checklist:
- Pre-seed `localStorage.setItem('bxv_boutique_profile_id', '42')`,
  embed the widget with
  `BellasBookingWidget.init({ ..., boutiqueExperienceProfileId: null })`,
  click through to a successful booking. Devtools network tab
  should show the POST body carrying `boutique_experience_profile_id: null`,
  not `42`. (Same-shape check for `prefillNote: null`.)

## Phase 6 follow-up notes (2026-04-30)

Staff UI surfaces Boutique Experience completion at every layer the
plan called out.

### What landed

- `frontend/src/utils/boutiqueExperience.js` (new): shared
  `STYLE_LABELS`, `BACK_LABELS`, `BUDGET_LABELS` dictionaries (mirrored
  from the fit-prep widget so staff see the same words customers
  picked) plus `formatSizeRange(profile)` that renders "Size 8-10",
  "Size 8", or `null` when nothing usable is set.
- `frontend/src/components/EventQuickViewDrawer.jsx`:
  - New "Boutique Experience" block after the "Latest booking"
    section. Header carries a status `Chip` (`success`/filled when
    complete, default/outlined when not started).
  - When complete, renders three KV rows: size estimate, style,
    budget. Pulls labels via the shared utility so the chip values
    are human-readable.
  - When not started, renders a single muted line: "Customer hasn't
    filled this out yet."
- `frontend/src/pages/EventDetail.jsx`:
  - New `BoutiqueExperienceBlock` component rendered inside each
    `BookingDetail` card, between the Source block and the legacy
    Enrichment-survey block. Status pill matches the drawer.
  - When complete, renders `submitted_at`, `source` (with
    underscores stripped for readability), an off-chart warning if
    set, a Sizing subsection (estimated range, by-measurement
    breakdown, raw measurements, height, chart name), a Style
    preferences subsection (style, back, budget, free-text colors,
    likes, avoids), and the customer-rendered summary in
    `whiteSpace: pre-line` so newlines survive.
  - When not started, renders "Customer hasn't filled out the
    Boutique Experience profile yet."
  - Existing "Enrichment survey" block stays untouched, since it
    holds different (survey-shape) data on the same row.

### Validation

- `npm run build` clean (same chunk-size warning as before;
  dist now `719.65 kB / gzip 226.92 kB`, up from `715.73 / 225.89`).
- All six back-end smokes still pass.
- Reusable utility module is unit-testable in isolation but I did
  not add a test file for it because the dictionaries are tiny and
  the format function is straightforward.

### What is intentionally not done in Phase 6

- **Manual browser smoke is unverified by me.** Staff-side checklist:
  - Open a lead with no profile -> drawer shows "Not started" pill,
    detail page shows "Customer hasn't filled out…" line.
  - Open a lead with a complete profile -> drawer shows green pill
    plus size / style / budget lines; detail page shows full
    Sizing + Style preferences blocks plus the customer summary.
  - Reschedule a lead and complete the profile through the new
    appointment -> the drawer should reflect the latest visit's
    completion state since `latestAppt` is the first item in the
    sorted list.
- "Send reminder" staff action is not added. Plan called it out as
  Phase 7 territory once the email templates exist.
- No copy review for the staff-facing strings ("Customer hasn't
  filled this out yet", "Boutique Experience"). They mirror the
  customer-facing labels for consistency.

## Phase 7 follow-up notes (2026-04-30)

Customer email surface now drives Boutique Experience completion.

### What landed

- `services/notification_templates.py`:
  - New `_profile_attached(appt)` helper resolves `object_session(appt)`
    and checks for a row in `appointment_enrichment_responses` with
    `submitted_at IS NOT NULL`. Falls back to `False` on a detached
    appointment so a template never errors on absent session context.
  - Shared CTA constants `_BE_CTA_LABEL = "Complete your Boutique
    Experience Profile"` and `_BE_INTRO = "Help us prepare dresses in
    your size, style, and budget before you arrive."` so the same
    customer-facing copy ships across confirmation, invitation, and
    reminder. Matches the plan's CTA direction verbatim.
  - `render_booking_confirmation` swapped the legacy
    `fit_prep_url(appt.confirmation_code)` (which pointed at
    `?code=BX-...`) for `enrichment_url(appt.id)` and turned the
    inline link into a primary CTA button. Reschedule and Cancel
    moved to secondary buttons.
  - `render_enrichment_invitation` adopted the new subject
    ("Complete your Boutique Experience Profile") and copy. Old
    "Help us prepare for your fitting" subject is gone; the email
    body explicitly frames this as building the profile.
  - `render_reminder` now renders the same primary CTA in a
    "Complete your Boutique Experience Profile" section, but only
    when `_profile_attached(appt)` is `False`. Customers who
    finished the profile see only the slot, quick prep, and
    reschedule/cancel buttons.
  - Internal removed-import cleanup: `fit_prep_url` is no longer
    imported by the templates module. The helper still exists in
    `services/booking_tokens.py` for now; Phase 8 can drop it.
- `tests/test_notifications_smoke.py` extended: after the existing
  enqueue-and-dispatch checks, the test now renders all three
  customer templates against the bound appointment, asserts the
  CTA copy + tokenized URL appear in confirmation and invitation,
  asserts the reminder includes the CTA when no profile is attached,
  inserts a complete profile row, re-renders the reminder, and
  asserts the CTA disappears. Cleans up the synthetic row.

### Validation

- All six back-end smokes still pass.
- Token already lands on `fit-prep.html?token=...` (Phase 2 helper
  flip), so the email links open the calculator surface that knows
  how to redeem the token (Phase 4 widget rebuild).
- Em dashes intentionally removed from the new strings per project
  copy preferences. Existing strings in unrelated templates were
  left alone.

### What is intentionally not done in Phase 7

- **The "prep incomplete" nudge email is not added.** The plan
  flagged it as "later optional" and the reminder's conditional
  CTA covers most of the value already. Adding it would require a
  new notification kind, a new enqueue cadence (e.g. T-72h or
  T-7d), and a new template — a worthwhile follow-up but not on
  the critical path to the deliverable.
- No SMS template change. The SMS reminder is short enough that a
  CTA there would not pay off; revisit when SMS gains capacity.
- Internal `internal_new_booking` template untouched. Staff signal
  is on the CRM detail page (Phase 6), not in the email body.
- No copy review pass yet. The CTA copy comes straight from the
  plan and may want a marketing edit before launch.

### Amendment (2026-04-30, post-review)

Review caught two bugs in Phase 7's first pass.

**1. Calculator-first customers were still asked to complete the
profile.** `enqueue_for_new_booking` always scheduled
`enrichment_invitation`, and `render_booking_confirmation` always
rendered the Boutique Experience CTA. A customer who completed the
profile before booking and submitted with
`boutique_experience_profile_id` set would still receive a "Complete
your Boutique Experience Profile" confirmation email and an
invitation email two minutes later, even though
`AppointmentResponse.boutique_experience_attached` was already
`True`.

Fix:
- `enqueue_for_new_booking` and `enqueue_for_reschedule` now skip
  the `enrichment_invitation` enqueue when
  `is_boutique_profile_attached(appt)` is `True`.
- `render_booking_confirmation` drops the entire CTA section
  (heading + intro + button) and the corresponding text-only block
  when a profile is already attached.

**2. The reminder's CTA suppression only checked the current
appointment.** `_profile_attached` filtered
`appointment_id == appt.id`, but Phase 1 keeps the completed
profile on the *original* appointment and creates a *new*
appointment on reschedule. So a customer who completed their
profile, then rescheduled, would see the CTA on the reminder for
the new appointment because the new appointment had no profile of
its own.

Fix: `_profile_attached` was promoted to a public
`is_boutique_profile_attached(appt)` and its query now spans every
appointment tied to the same `crm_event_id` (matching Phase 3's
board-level "any complete profile on this lead" aggregation). When
`crm_event_id` is `None` (legacy rows, pre-auto-promotion), it
falls back to the per-appointment check.

Both fixes are exercised by new sections in
`tests/test_notifications_smoke.py`:
- Calculator-first scenario: seeds a contact + event + appointment
  + complete profile, calls `enqueue_for_new_booking(db, appt)`,
  asserts `enrichment_invitation` is absent from the resulting
  jobs and that `render_booking_confirmation(appt)` drops the CTA.
- Reschedule-after-completion scenario: seeds an appt with a
  complete profile under a CRM event, then seeds a *second* appt
  on the same event without a profile, asserts
  `is_boutique_profile_attached(new_appt)` returns `True`, asserts
  the reminder for the new appt drops the CTA, and asserts
  `enqueue_for_reschedule` does not schedule
  `enrichment_invitation` for the new appt.

The legacy `_profile_attached` underscore alias was dropped; the
canonical name is `is_boutique_profile_attached`.

All six back-end smokes pass.

## Phase 8 follow-up notes (2026-04-30)

Legacy attach UI is fully retired. The Boutique Experience flow is the
only customer path now.

### What landed

- **Endpoint removed.** `POST /api/booking/appointments/attach-note` is
  gone from `api/routers/booking.py`. After Phases 4 and 7 there is no
  internal caller (the fit-prep widget no longer renders the
  code+phone attach UI, and no email template links to it). External
  embeds of the widget were never a thing for this single-tenant
  marketing site, so dropping the endpoint is safe.
- **Contracts removed.** `AttachNoteRequest` and `AttachNoteResponse`
  are gone from `services/booking_contracts.py` along with the
  confirmation-code normalization validator that only that path used.
- **Service helpers removed.** `attach_prep_note`,
  `AttachNoteOutcome`, `_count_recent_attach_attempts`,
  `_record_attach_event`, plus the related constants
  (`ATTACH_RATE_LIMIT_WINDOW`, `ATTACH_RATE_LIMIT_ATTEMPTS`,
  `ATTACH_NOTE_MAX_CHARS`, `ATTACH_LOOKUP_WINDOW`, `_CODE_PATTERN`)
  are gone from `services/booking_service.py`. Unused imports
  (`AppointmentSessionEvent`, `func`) cleaned up at the same time.
- **Token helper removed.** `fit_prep_url(confirmation_code)` is gone
  from `services/booking_tokens.py`. The legacy
  `fit-prep.html?code=BX-...` URL shape is no longer mintable. Only
  the tokenized `enrichment_url` remains as the customer-facing
  link.
- **Smoke removed.** `tests/test_attach_note_smoke.py` deleted; the
  endpoint it covered is gone.
- Historical `event_name='attach_note_attempt'` rows in
  `appointment_session_events` are left in place. They're useful for
  forensics ("did the legacy path ever succeed for this customer?")
  and have no live writers, so no DB cleanup is needed.

### Validation

- All five remaining back-end smokes pass:
  `booking`, `events`, `admin booking`, `notifications`,
  `boutique experience`.
- `npm run build` clean.
- Static grep across `*.py`, `*.js`, `*.jsx`, `*.html` confirms zero
  references to `AttachNote`, `attach_prep_note`, `attach-note`, or
  `fit_prep_url` in live code (only historical mentions remain in the
  plan doc).

### What is intentionally not done in Phase 8

- **Optional structured-from-customer-note migration is not
  shipped.** Plan called it "if feasible." Existing customer-note
  prep summaries remain readable via the staff Booking section (the
  Customer note KV row), so the data is not lost. Parsing the
  multi-line "Fit Prep Summary (Bella's XV)" blob into structured
  measurement/preference rows would be fragile, the per-row payoff
  is small for this dataset size, and `is_boutique_profile_attached`
  correctly returns `False` for those rows so they show as "Not
  started" in the staff UI without breaking anything.
  If a need surfaces later (e.g. analytics on legacy answers), the
  structured columns are nullable so a one-shot parse-and-INSERT
  script with `source='legacy_note'` slots in cleanly.
- **In-flight email tail still 404s for ~30 days on
  `/preferences/{token}`.** Phase 2 swapped `enrichment_url` to
  `/fit-prep.html?token=...`, so emails minted before that deploy
  may still carry the dead path until token TTL expires. Acceptable
  per the original plan; a Nginx redirect rule is the cheap fix if
  the rate is high enough to matter.
- **No Phase 9 yet.** Verification + tests across the full flow
  (calculator-first → book → event → reminder → reschedule cycles,
  plus the manual browser pass that has been the standing gap) is
  the natural next step.

## Phase 9 follow-up notes (2026-04-30)

Verification matrix consolidated. Phase 9's plan-as-written is satisfied
by code that landed in Phases 1-8 plus one HTTP-level reschedule case
added in this phase.

### Coverage matrix vs the plan

| Plan item | Status | Where |
| --- | --- | --- |
| booking without profile auto-promotes | covered | `tests/test_booking_smoke.py` |
| booking with pre-booking profile links it | covered | `tests/test_boutique_experience_smoke.py` |
| response includes profile CTA + token URL | covered | `tests/test_booking_smoke.py` (attached=False), `tests/test_boutique_experience_smoke.py` (attached=True) |
| event detail shows profile incomplete when missing | covered | `tests/test_boutique_experience_smoke.py` (initial state + cleared `submitted_at`) |
| event detail shows complete profile fields | covered | `tests/test_boutique_experience_smoke.py` |
| reschedule keeps event linkage and profile shape stable | covered | `tests/test_boutique_experience_smoke.py` (HTTP, this phase) + `tests/test_notifications_smoke.py` (service-layer) |
| tokenized profile submission attaches without phone | covered | `tests/test_boutique_experience_smoke.py` |
| invalid / wrong-purpose token rejected | covered | `tests/test_boutique_experience_smoke.py` |
| `attach-note` deprecation coverage | n/a | endpoint retired in Phase 8; smoke deleted |
| `npm run build` passes | covered | repeated each phase |

### What landed in Phase 9 specifically

- `tests/test_boutique_experience_smoke.py`:
  - New scenario step 7: book with `boutique_experience_profile_id`,
    POST `/api/booking/reschedule/{token}` to a different slot, then
    assert (a) the new appointment carries the original's
    `crm_event_id`, (b) exactly one profile row exists across the
    lead and it still belongs to the *original* appointment, (c)
    `GET /api/events/{id}` returns both appointments with the
    original showing `boutique_experience_status=complete` and the
    new one `not_started`, and (d) `GET /api/events/board` still
    shows the lead as `complete` because Phase 3's aggregation
    counts any complete profile in the lead.
  - `_next_open_slot()` gained a `skip` argument so the reschedule
    case can ask for a different slot than the original booking
    holds.
  - `_cleanup_event_id()` now walks `rescheduled_from_id`
    transitively so the rescheduled descendant rows (which carry a
    fresh `event_id` of NULL) are caught and the FK-ordered DELETE
    works under the unique-event_id constraint.
  - The "clear `submitted_at`" assertion (formerly step 8) now
    looks up the original appointment by `confirmation_code`
    instead of `appointments[0]`, since after the reschedule the
    list contains two entries ordered by `slot_start_at desc`.

### Smoke command set (current)

```bash
venv/bin/python tests/test_booking_smoke.py
venv/bin/python tests/test_admin_booking_smoke.py
venv/bin/python tests/test_admin_booking_settings_smoke.py
venv/bin/python tests/test_events_smoke.py
venv/bin/python tests/test_notifications_smoke.py
venv/bin/python tests/test_boutique_experience_smoke.py
cd frontend && npm run build
```

All six smokes + `npm run build` pass on the current main.

### Standing manual-browser checklist (still unverified by me)

These accumulated across Phases 4-6 and remain the only meaningful
verification gap. They are listed here in one place for the next
human pass.

**Fit-prep widget — token URL.**
- Land on `/fit-prep.html?token=<good>`. Form renders, fill it out.
- Result screen shows "Send to my stylist". Click sends a single
  POST and flips the screen to "Sent to your stylist · See you on
  $slot."
- Edit Answers, change a measurement, click "See my prep guide"
  again. Result screen shows the submit button again, not the
  success state.
- Bad token (`?token=garbage`) submits return an error with retry,
  button re-enables.

**Fit-prep widget — no token (calculator-first).**
- Land on `/fit-prep.html`. Form renders, fill it out.
- Result screen shows "Book with this profile". Click POSTs and
  flips to "Saved for your booking · Continue to booking."
- Click Continue: scrolls to the booking widget below.
- Devtools: localStorage has both `bxv_boutique_profile_id` and
  `bxv_fit_prep_summary` after the click; both are cleared after a
  successful booking submission.
- Pre-seed `bxv_boutique_profile_id`, init the booking widget with
  `boutiqueExperienceProfileId: null`. The submission body sends
  `null` (not the seeded id). Same shape for `prefillNote: null`.
- Block the network on the pre-booking POST. Fallback path saves
  `bxv_fit_prep_summary` and clears `bxv_boutique_profile_id`.

**Booking widget — success screen.**
- Calculator-first booking → success shows green "Boutique
  Experience profile added" badge. No CTA link.
- Booking-first (no fit-prep) → success shows "One last optional
  step" callout linking to `fit-prep.html?token=...`. Click opens
  the page; widget reads the token; submitting flips the lead to
  complete.

**Booking widget — step 3 prefill.**
- With a prefilled note, step 3 shows the in-widget banner with
  Remove. Remove drops the note + the banner; the form is still
  submittable.

**Staff UI.**
- Lead with no profile → kanban drawer pill is "Not started",
  detail page line says "Customer hasn't filled out…".
- Lead with a complete profile → drawer shows green pill plus
  size/style/budget; detail page shows full Sizing + Style
  preferences blocks plus the customer summary.

**Email visuals.**
- Inspect a booking confirmation HTML render: when no profile is
  attached, primary "Complete your Boutique Experience Profile"
  button + Reschedule/Cancel as secondary buttons. When a profile
  is attached, the CTA section is gone entirely.
- Inspect an enrichment_invitation render: subject "Complete your
  Boutique Experience Profile", primary CTA, opens the tokenized
  page.
- Inspect a reminder render: CTA appears when the lead has no
  complete profile, disappears when it does (including when the
  complete profile lives on the *original* appointment of a
  reschedule).

### What is intentionally not done

- No load test. Volume profile (low-dozens-per-week) doesn't
  warrant it.
- No frontend Playwright test. The widgets and React UI rely on
  manual smoke. Adding browser automation here would close the
  gap noted above; recommend it once a UI regression actually bites.
- No Phase 10 in the plan. The Boutique Experience milestone is
  considered complete after a successful manual browser pass.

### CI added (2026-04-30, post-write)

GitHub Actions runs the full smoke suite on every push and pull
request via [.github/workflows/smoke.yml](../.github/workflows/smoke.yml).
The workflow provisions a Postgres 16 service container, applies all
migrations, runs the six backend smokes sequentially (sequential
because several reserve real booking slots that would collide in
parallel), then installs frontend deps and runs `npm run build`.
The env block matches every key `config/settings.py` reads and sets
`SMTP_HOST=""` so the notifications smoke uses the null email
transport. Concurrency cancellation by ref keeps fast successive
pushes from stacking up. See [docs/TESTING.md](TESTING.md#ci) for
the contract.

### Validation

A purpose-built smoke covered both endpoints end-to-end through the
auth flow:

- no profile -> detail status `not_started`, board status `not_started`,
  structured object `null`.
- profile with `submitted_at` set -> detail status `complete`, board
  status `complete`, full structured object populated (measurements,
  sizing, all calculator preferences, summary, source).
- clearing `submitted_at` flips both surfaces back to `not_started`
  while leaving the structured object intact.

The five existing smoke suites (booking, attach-note, events,
admin-booking, notifications) all still pass.

### What is intentionally not done in Phase 3

- No board-level highlights (size range, style, budget). Drawer already
  fetches detail; adding them to the card payload would not save a
  round trip in practice.
- No `Started` / `Needs review` states. Plan keeps these as future
  options; the schema can accept them by extending the Literal without
  a migration.
- No new GET endpoint for the customer-facing fit-prep page. Phase 4
  will add `GET /api/booking/boutique-experience/{token}` so the page
  can render the customer's slot/name before the form.

