# Sales/Admin Capability Map

## Purpose

This is the living reference for every capability that exists on either the admin or sales surface. It is the guardrail that Phase A of [SALES_ADMIN_SURFACE_ALIGNMENT.md](SALES_ADMIN_SURFACE_ALIGNMENT.md) asked for.

For each capability the map lists: the shared service, the admin route, the sales route, the admin UI surface, the sales UI surface, the auth/gate rule, the audit activity type, the notification event kind, and the current completion status.

Update this file in the same commit as any route, service, or UI change that adds or removes a capability. If the map is wrong, the alignment plan is wrong.

## Status Legend

- **complete** — shared service exists, both surfaces have routes and UI where appropriate, audit and notifications wired.
- **partial** — backend complete or mostly complete, but at least one surface is missing UI, or one side lacks an equivalent route.
- **sales-only** — capability is intentionally or currently sales-surface-only; admin parity not yet evaluated.
- **admin-only** — capability is intentionally or currently admin-surface-only; sales parity not yet evaluated.

## At-a-Glance Status

| Capability | Admin route | Sales route | Status |
|---|---|---|---|
| Walk-in lead creation | `POST /api/walk-in-leads` | `POST /api/sales/walk-ins` | complete |
| Lead/global search | `GET /api/search` | `GET /api/sales/search/leads` | complete |
| Appointment detail | `GET /api/admin/booking/appointments/{id}` | `GET /api/sales/appointments/{id}` | complete |
| Appointment status actions | `PATCH /api/admin/booking/appointments/{id}` | `POST /api/sales/appointments/{id}/status` | partial (sales lacks confirm/reschedule) |
| Internal notes | `PATCH /api/admin/booking/appointments/{id}` (notes field) | `PATCH /api/sales/appointments/{id}/notes` | complete |
| Tried-on logging | none | `GET/POST/PATCH/DELETE /api/sales/.../tried-on` | sales-only |
| Quote create / send / approve / convert | shared route, admin scope | shared route, sales scope | complete |
| Add participant | shared `POST /api/events/{id}/participants` | shared (deprecated alias also exists at `/api/sales/events/{id}/participants`) | complete |
| Participant buyer journey / pipeline signal | `PATCH /api/admin/booking/appointments/{id}/participant` + shared quote/invoice tag routes | `PATCH /api/sales/appointments/{id}/participant` + shared quote/invoice tag routes | partial (backend + admin read & appointment-tag UI + sales appointment-tag UI; quote/invoice tag UI is API-only) |
| Appointment assignment | none (admin reassigns via lead/event owner) | `PATCH /api/sales/appointments/{id}/assignment` | sales-only |
| Lead owner reassignment | `PATCH /api/admin/events/{event_id}/owner` + `GET /api/admin/events/{event_id}/cascade-preview` | `PATCH /api/sales/leads/{event_id}/assignment` + `GET /api/sales/leads/{event_id}/cascade-preview` | partial (sales + admin built, browser verification pending) |
| Assignable staff picker | none | `GET /api/sales/staff/assignable` | sales-only |
| Clock-in / kiosk lock | none | `POST /api/sales/auth/kiosk-lock` + clock-in endpoints | sales-only |
| Staff password management | `POST /api/admin/me/change-password`; `POST /api/admin/staff/{id}/send-password-reset` | none (sales uses PIN auth) | partial (backend + first-pass UI built; browser/build verification pending) |

Anything marked **partial** or **sales-only** is fair game for the Phase B–E work in the alignment doc. Anything marked **complete** should not be rebuilt without a reason added here.

## Capabilities

### Walk-in lead creation

- **Shared service:** [services/walk_in_service.py](../services/walk_in_service.py) `create_walk_in_lead`
- **Admin route:** [api/routers/walk_in_leads.py](../api/routers/walk_in_leads.py) `POST /api/walk-in-leads`
- **Sales route:** [api/routers/sales_walk_ins.py](../api/routers/sales_walk_ins.py) `POST /api/sales/walk-ins`
- **Admin UI:** [frontend/src/pages/BusinessProfile.jsx](../frontend/src/pages/BusinessProfile.jsx) walk-in form
- **Sales UI:** [frontend/src/sales/RepDashboard.jsx](../frontend/src/sales/RepDashboard.jsx) opens [frontend/src/sales/SalesWalkInDialog.jsx](../frontend/src/sales/SalesWalkInDialog.jsx).
- **Auth / gate:** admin = `require_admin_scope`. Sales = `require_floor_access("sales")`; sales also resolves assignee (default current user, validated against active sales users).
- **Audit:** `activity_log.EVENT_WALK_IN_CREATED` with `{appointment_id, contact_id, was_new_contact}`.
- **Notification:** `notification_routing.record_event(kind="admin.walk_in_lead_created", ...)`. Sales-side also calls `notify_booking_assigned` for the assignee.
- **Frontend API helpers:** `createWalkInLead` (admin) and `salesCreateWalkIn` (sales) in [frontend/src/services/api.js](../frontend/src/services/api.js).
- **Status:** complete. Both surfaces have a route and UI. Sales uses its own dialog so the floor form stays sales-safe and mobile-friendly while sharing the same backend service.

### Lead / global search

- **Shared service:** none. Intentionally split.
- **Admin route:** [api/routers/search.py](../api/routers/search.py) `GET /api/search` over `services/search_service.py`. Returns events, contacts, invoices, quotes, including monetary sublabels (balance, total).
- **Sales route:** [api/routers/sales_search.py](../api/routers/sales_search.py) `GET /api/sales/search/leads` over `services/sales_search_service.py`. Returns appointments, events, contacts only. Never exposes monetary or document-key fields.
- **Admin UI:** command palette in [frontend/src/components/DashboardLayout.jsx](../frontend/src/components/DashboardLayout.jsx).
- **Sales UI:** [frontend/src/sales/LeadSearch.jsx](../frontend/src/sales/LeadSearch.jsx).
- **Auth / gate:** admin = `require_admin_scope`. Sales = `require_sales_scope` (no attendance gate on reads).
- **Audit:** none (read-only).
- **Notification:** none.
- **Status:** complete. Keep the surfaces separate; do not collapse into a `sales_safe=true` flag.

### Appointment detail

- **Shared service:** partial. Admin uses its own response shape via `api/routers/admin_booking.py`. Sales uses [services/sales_appointments.py](../services/sales_appointments.py) `get_detail`.
- **Admin route:** [api/routers/admin_booking.py](../api/routers/admin_booking.py) `GET /api/admin/booking/appointments/{id}` returning `AppointmentDetail`.
- **Sales route:** [api/routers/sales_appointments.py](../api/routers/sales_appointments.py) `GET /api/sales/appointments/{id}` returning `AppointmentDetailResponse` (appointment + contact summary + event summary + participants + enrichment + recent activity).
- **Admin UI:** [frontend/src/pages/AppointmentsCalendar.jsx](../frontend/src/pages/AppointmentsCalendar.jsx) and the event tabs under [frontend/src/pages/event/](../frontend/src/pages/event/).
- **Sales UI:** [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx).
- **Auth / gate:** admin = `require_admin_scope`. Sales = `require_sales_scope`.
- **Audit:** none (read).
- **Notification:** none.
- **Status:** complete. Sales response is intentionally narrower than admin.

### Appointment status actions

- **Shared service:** sales uses `services/sales_appointments.py` `apply_status_action`. Admin status transitions are inlined in the admin booking router PATCH handler, which also calls `event_service.change_event_status` and `notification_service.enqueue_for_cancellation` on cancellation.
- **Admin route:** [api/routers/admin_booking.py](../api/routers/admin_booking.py) `PATCH /api/admin/booking/appointments/{id}` covering `pending|confirmed|attended|no_show|cancelled|rescheduled`.
- **Sales route:** [api/routers/sales_appointments.py](../api/routers/sales_appointments.py) `POST /api/sales/appointments/{id}/status` covering `arrived | no_show | cancelled` only.
- **Admin UI:** appointment row controls in [frontend/src/pages/AppointmentsCalendar.jsx](../frontend/src/pages/AppointmentsCalendar.jsx).
- **Sales UI:** action buttons in [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx).
- **Auth / gate:** admin = `require_admin_scope`. Sales = `require_floor_access("sales")`.
- **Audit:** `APPOINTMENT_ARRIVED`, `APPOINTMENT_NO_SHOW`, `APPOINTMENT_CANCELLED` (written once per real state change).
- **Notification:** `notify_booking_cancelled` on cancellation. Confirmation/reschedule are admin-only paths today.
- **Status:** partial. Sales has the three floor-relevant actions but not confirm or reschedule. If sales needs those, promote the transition logic out of `admin_booking.py` into a shared appointment status service first.

### Internal notes

- **Shared service:** [services/appointment_audit.py](../services/appointment_audit.py) `log_notes_edited` writes the activity row from both surfaces. The column mutation itself still happens inline in each PATCH handler (admin) / in `services/sales_appointments.py::update_internal_notes` (sales) — only the audit-row shape is centralized.
- **Admin route:** `PATCH /api/admin/booking/appointments/{id}` (the `internal_notes` field on `AppointmentPatch`).
- **Sales route:** `PATCH /api/sales/appointments/{id}/notes`.
- **Admin UI:** internal notes textbox in admin appointment detail.
- **Sales UI:** notes textbox in [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx).
- **Auth / gate:** admin = `require_admin_scope`. Sales = `require_floor_access("sales")`.
- **Audit:** both surfaces write `APPOINTMENT_NOTES_EDITED` with `{appointment_id, prior_length, new_length}` (no text — the activity timeline is read whole on every event detail load). Only emitted when the notes value actually changed AND the appointment has a linked CRM event to anchor the row.
- **Notification:** none.
- **Status:** complete. Phase 9.4 D2 closed the admin-side audit gap; the helper is the reference for future "edit on both surfaces, same audit shape" lifts.

### Tried-on logging

- **Shared service:** [services/sales_tried_on.py](../services/sales_tried_on.py).
- **Admin route:** none. Admin can see tried-on rows but not write them.
- **Sales routes:** [api/routers/sales_tried_on.py](../api/routers/sales_tried_on.py) `GET/POST /api/sales/appointments/{id}/tried-on`, `PATCH/DELETE /api/sales/tried-on/{id}`.
- **Admin UI:** read-only view inside event activity / appointment detail (verify).
- **Sales UI:** [frontend/src/sales/TriedOnSection.jsx](../frontend/src/sales/TriedOnSection.jsx).
- **Auth / gate:** sales reads = `require_sales_scope`. Sales mutations = `require_floor_access("sales")`. Mutation requires the appointment to have a linked event (returns 409 `event_required` otherwise).
- **Audit:** `APPOINTMENT_TRIED_ON_ADDED | UPDATED | REMOVED`. Payload deliberately omits `internal_sku`, `designer`, `style_number` to preserve SKU obfuscation.
- **Notification:** none.
- **Status:** sales-only by design. Admin should be able to see the rows; confirm visibility before promoting parity work.

### Quote create / send / approve / convert

- **Shared service:** [services/quote_service.py](../services/quote_service.py).
- **Routes (both surfaces use the same paths):** [api/routers/quotes.py](../api/routers/quotes.py)
  - `POST /api/events/{event_id}/quotes`
  - `PATCH /api/quotes/{id}`
  - `POST /api/quotes/{id}/send`
  - `POST /api/quotes/{id}/resend`
  - `POST /api/quotes/{id}/approve`
  - `POST /api/quotes/{id}/approve-in-store` (captures signature, IP, user agent)
  - `POST /api/quotes/{id}/reject`
  - `POST /api/quotes/{id}/cancel`
  - `POST /api/quotes/{id}/convert`
  - `DELETE /api/quotes/{id}` (admin-only)
- **Admin UI:** [frontend/src/pages/event/tabs/Quotes.jsx](../frontend/src/pages/event/tabs/Quotes.jsx), [frontend/src/components/QuoteEditor.jsx](../frontend/src/components/QuoteEditor.jsx).
- **Sales UI:** [frontend/src/sales/QuotesSection.jsx](../frontend/src/sales/QuotesSection.jsx).
- **Auth / gate:** mutations all use `require_floor_access("admin", "sales")`. Delete is admin-only.
- **Audit:** the full `QUOTE_*` family in [services/activity_log.py](../services/activity_log.py).
- **Notification:** quote send/resend wires through `services/portal_email.py` for the customer-facing invitations. In-store approval also emits `quote.approved_in_store` on the staff notification event bus with `digest` timing; intrinsic recipient is the event owner (via the new `_owner_of_event` helper in [services/notification_routing.py](../services/notification_routing.py)). Customer-portal sign does not yet emit a staff event; if that becomes a gap, register a separate kind or reuse the in-store kind there too.
- **Status:** complete. Single shared service, single shared route set, role differs only on delete.

### Add participant

- **Shared service:** [services/event_participants.py](../services/event_participants.py) `add_event_participant`.
- **Canonical route (both surfaces):** [api/routers/event_participants.py](../api/routers/event_participants.py) `POST /api/events/{event_id}/participants`.
- **Deprecated alias:** `POST /api/sales/events/{event_id}/participants` in `api/routers/sales.py` delegates to the same service.
- **Admin UI:** [frontend/src/components/AddParticipantDialog.jsx](../frontend/src/components/AddParticipantDialog.jsx) used from [frontend/src/pages/event/tabs/Overview.jsx](../frontend/src/pages/event/tabs/Overview.jsx).
- **Sales UI:** same dialog reused from [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx).
- **Auth / gate:** `require_floor_access("admin", "sales")`.
- **Audit:** `EVENT_PARTICIPANT_ADDED` with participant id, role, party size bucket, was_new_contact.
- **Notification:** none.
- **Status:** complete. Best-shape example in the project: one service, one canonical route, one UI component reused on both surfaces. Use as the reference when promoting other sales routes to shared ones.

### Participant buyer journey / pipeline signal

- **Schema:** [database/migrations/079_event_participant_id_on_buyer_rows.py](../database/migrations/079_event_participant_id_on_buyer_rows.py) adds nullable `event_participant_id` FKs to `appointments`, `quotes`, and `invoices` with `ON DELETE SET NULL`.
- **Shared service:** [services/buyer_journey.py](../services/buyer_journey.py) `attach_appointment_to_participant`, `attach_quote_to_participant`, and `attach_invoice_to_participant`. The buyer journey is implicit: an `event_participants` row plus buyer rows tagged to it.
- **Admin routes:**
  - [api/routers/admin_booking.py](../api/routers/admin_booking.py) `PATCH /api/admin/booking/appointments/{appointment_id}/participant`.
  - Shared quote route `PATCH /api/quotes/{quote_id}/participant`.
  - Shared invoice route `PATCH /api/invoices/{invoice_id}/participant`.
- **Sales routes:**
  - [api/routers/sales_appointments.py](../api/routers/sales_appointments.py) `PATCH /api/sales/appointments/{appointment_id}/participant`.
  - Shared quote route `PATCH /api/quotes/{quote_id}/participant`.
  - Shared invoice route `PATCH /api/invoices/{invoice_id}/participant`.
- **Admin UI:** partial. [frontend/src/pages/Pipeline.jsx](../frontend/src/pages/Pipeline.jsx) shows a named-buyer count chip on pipeline cards when `named_buyer_count > 0`. The event quick-view shows the per-buyer breakdown. [frontend/src/pages/event/tabs/Overview.jsx](../frontend/src/pages/event/tabs/Overview.jsx) `BookingDetail` renders a `buyer: <role> <name>` / `buyer: untagged` chip on each appointment row that opens the shared `ParticipantTagDialog` and saves via `adminTagAppointmentParticipant` (`PATCH /api/admin/booking/appointments/{id}/participant`); on success it invalidates `['event', eventId]` and `['events', 'board']` so the chip, per-buyer journey counts, and pipeline cards all refresh. Admin tags appointments from the event surface rather than a dedicated `/appointments/:id` page — that's the design intent (the event is the party container; admin doesn't need a standalone appointment detail), not a gap. Quote/invoice tagging remains API-only.
- **Sales UI:** partial. [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx) shows a buyer-journey chip and opens [frontend/src/sales/SalesParticipantTagDialog.jsx](../frontend/src/sales/SalesParticipantTagDialog.jsx) for appointment tagging. Quote/invoice tagging is API-only today.
- **Auth / gate:** admin = `require_admin_scope` or `require_floor_access("admin", "sales")` depending on mutation. Sales mutations should use `require_floor_access("sales")`; sales reads can stay on `require_sales_scope`.
- **Audit:** `appointment.participant_attached`, `quote.participant_attached`, and `invoice.participant_attached` with `{from_event_participant_id, to_event_participant_id}` payloads. Idempotent retries skip duplicate activity rows.
- **Notification:** none directly. If a future create-and-tag flow creates/assigns an appointment, reuse the existing booking-assignment notification path. Do not invent a participant-only notification stream unless a real staff workflow needs it.
- **Read surface:** [api/routers/events.py](../api/routers/events.py) exposes per-buyer data on both board/quick-view payloads and event detail. `GET /api/events/{id}` now includes linked quote and invoice summaries for the Overview buyer-journey section.
- **Board signal:** [services/event_service.py](../services/event_service.py) computes `named_buyer_count` from distinct tagged participants across appointments, quotes, and invoices. [api/routers/events.py](../api/routers/events.py) exposes it on board cards.
- **Status:** partial. Backend foundation, migration 079, audit, route surface, board count, admin pipeline chip, admin quick-view + Overview buyer breakdown, admin appointment-tag chip on the Event Overview Booking row, and sales appointment tagging UI are built. Remaining work: browser-verify both subdomains, design a direct quote/invoice tagging UI if a real workflow needs it (API path is live today), and consider later-day create-and-tag ergonomics.

Design direction:

- Keep **one shared quince event** as the party container. The event represents the celebration, shared primary contact context, status history, party/court context, and participant relationships.
- Allow **multiple buyer journeys under that event**. The celebrant, damas, chambelanes, parents, or other participants may each need their own appointment/date, try-on log, quote, invoice, and sales assignment.
- Reflect buyer journeys in the pipeline without duplicating the event. Phase 10.4 ships the lightest board signal: a named-buyer count chip. Phase 10.5/10.6 add the operational detail: quick-view and event Overview show that Anthony Mendez is a `chambelan` tied to the same quince event and is also an active customer journey, not merely a detail-row participant.
- Do not auto-overwrite `events.court_size` when a participant is added. `court_size` is the planned/estimated court size; named participants are actual captured people. The UI can show both, e.g. `Planned court: 10` and `Named buyers: 2`.
- Support both timing cases:
  - Same-day: Anthony comes in with the celebrant; the participant buyer journey can reuse today's appointment/session date or be created from the current event page.
  - Later-day: Anthony comes in separately; staff can search/find the celebrant event, add Anthony from that page, and create a separate appointment tied back to the same event.
- Data shape chosen in Phase 10.2:
  - `appointments.event_participant_id`, `quotes.event_participant_id`, and `invoices.event_participant_id` identify the named buyer while preserving the shared event relationship.
  - No separate buyer-journey table exists yet. Add one only if reporting, lifecycle state, or merge/retire controls outgrow the implicit FK model.
  - Avoid creating duplicate events for each court member unless the business intentionally wants separate celebration pipelines.

### Appointment assignment

- **Shared service:** [services/sales_assignment.py](../services/sales_assignment.py) `reassign_appointment`. Sales-named today; alignment doc Phase E proposes renaming to `services/assignment_service.py` once admin uses it.
- **Admin route:** none. Admin currently changes the underlying event owner via [api/routers/event_service.py](../services/event_service.py) workflows.
- **Sales route:** `PATCH /api/sales/appointments/{id}/assignment` with `{assigned_user_id: int | null}`.
- **Admin UI:** none specifically for per-appointment reassignment.
- **Sales UI:** "assigned: <name>" chip in [frontend/src/sales/AppointmentDetail.jsx](../frontend/src/sales/AppointmentDetail.jsx) header opens [frontend/src/sales/SalesAssignmentDialog.jsx](../frontend/src/sales/SalesAssignmentDialog.jsx) with scope=appointment selected. The dialog also exposes scope=lead when the appointment has a linked event (see next section).
- **Auth / gate:** `require_floor_access("sales")`. Validates target is an active sales user.
- **Audit:** `APPOINTMENT_REASSIGNED` with `{from_user_id, to_user_id, reason: "sales_reassignment"}`. Only written when the assignee actually changed and the appointment has a linked event.
- **Notification:** `notify_booking_cancelled` to previous owner BEFORE the column flip; `notify_booking_assigned` to new owner AFTER. The order matters because intrinsic targeting reads the current `assigned_user_id`.
- **Status:** sales-only. Backend + sales UI complete. Admin parity not yet designed — alignment doc Phase C owns the decision; Phase E owns the service rename once admin needs it.

### Lead owner reassignment (with cascade)

- **Shared service:** [services/sales_assignment.py](../services/sales_assignment.py) `reassign_event_lead` (mutation) + `lead_cascade_preview` (read-only preview). Phase 11 made the audit `reason` a per-call kwarg so admin and sales callers stamp distinct payloads.
- **Admin routes:** in [api/routers/admin_events.py](../api/routers/admin_events.py):
  - `PATCH /api/admin/events/{event_id}/owner` with `{owner_user_id: int | null}` — applies the move.
  - `GET /api/admin/events/{event_id}/cascade-preview` — returns the event owner + future-appointment list the mutation would touch. Both routes delegate to the shared service and pass `reason="admin_owner_change"`.
- **Sales routes:**
  - `PATCH /api/sales/leads/{event_id}/assignment` with `{owner_user_id: int | null}` — applies the move.
  - `GET /api/sales/leads/{event_id}/cascade-preview` — returns the event owner + the future appointments the mutation would touch. Sales-scope read, no attendance gate. Used by the dialog to render the cascade list before the user confirms.
- **Admin UI:** [frontend/src/components/AdminEventOwnerDialog.jsx](../frontend/src/components/AdminEventOwnerDialog.jsx) opened by the `Change` button next to the Owner KV on [Overview.jsx](../frontend/src/pages/event/tabs/Overview.jsx). Lead scope only — admin per-appointment reassignment stays deferred per the 2026-05-18 decision.
- **Sales UI:** same [SalesAssignmentDialog](../frontend/src/sales/SalesAssignmentDialog.jsx) as appointment assignment, with the scope toggle flipped to "All future appointments for this lead." The cascade preview list is the footgun mitigation that the 9.2 product decision in [SALES_REP_DASHBOARD_PHASES.md](SALES_REP_DASHBOARD_PHASES.md) called for.
- **Auth / gate:** sales PATCH = `require_floor_access("sales")`; sales preview GET = `require_sales_scope`. Admin routes = `require_admin_scope` with no floor gate (admin is not geofenced).
- **Cascade:** updates every appointment where `crm_event_id == event_id AND slot_start_at >= NOW()`. Past appointments are intentionally untouched for commission/attribution. Both surfaces delegate to the same shared service so the cascade rules cannot drift.
- **Audit:** parent `EVENT_REASSIGNED`; per cascaded appointment `APPOINTMENT_REASSIGNED` with `via: "lead_cascade"`. `reason` distinguishes the actor: `"sales_reassignment"` (sales default) vs `"admin_owner_change"` (admin route).
- **Notification:** cancel-old plus assign-new pair per cascaded appointment, regardless of which surface initiated the move.
- **Smoke:** [tests/test_admin_lead_reassignment_smoke.py](../tests/test_admin_lead_reassignment_smoke.py) covers the admin route end-to-end (preview, mutate, cascade, audit, idempotency, sales-token rejection, error cases, unassign).
- **Status:** partial. Backend + admin UI + sales UI + smoke complete. Browser verification on `admin.shopbellasxv.com` is operator-side; sales-side verification remains gated on the store geofence.

### Assignable staff picker

- **Shared service:** [services/sales_staff.py](../services/sales_staff.py) `list_assignable_sales_users`. Filters to `role='sales' AND is_active=true`.
- **Admin route:** none. Admin walk-in form passes `owner_user_id` directly; there is no equivalent "who can I assign to" endpoint.
- **Sales route:** `GET /api/sales/staff/assignable`.
- **Admin UI:** none for this specific shape.
- **Sales UI:** consumed inline by the walk-in and assignment flows (helper `salesListAssignableStaff` in api.js).
- **Auth / gate:** `require_sales_scope`.
- **Audit:** none.
- **Notification:** none.
- **Status:** sales-only. If admin needs the same shape (likely when admin gets the per-appointment reassignment surface), promote alongside `assignment_service`.

### Clock-in / kiosk lock

- **Shared service:** clock-in lives in `services/clock_in.py`; kiosk lock is route-level cookie clearing.
- **Admin route:** none.
- **Sales route:** `POST /api/sales/auth/kiosk-lock` (clears this device's sales session and CSRF cookies only; does not bump `users.token_version`). Plus clock-in endpoints under `/api/sales/auth/...` and `/api/sales/attendance/...`.
- **Admin UI:** admin can view attendance via [frontend/src/pages/AttendanceReview.jsx](../frontend/src/pages/AttendanceReview.jsx).
- **Sales UI:** [frontend/src/sales/ClockScreen.jsx](../frontend/src/sales/ClockScreen.jsx), [frontend/src/sales/PinLogin.jsx](../frontend/src/sales/PinLogin.jsx), idle/lock handling in `SalesAuthContext`.
- **Auth / gate:** PIN login + sales scope. Kiosk lock is intentionally device-scoped, not user-scoped, so other devices stay signed in.
- **Audit:** clock-in/out writes its own attendance rows.
- **Notification:** none on lock.
- **Status:** sales-only by definition; admin does not need parity here.

### Staff password management

- **Shared service:** [services/password_reset.py](../services/password_reset.py) `request_reset_for_user` reuses the public forgot-password token/email path for authenticated admin reset triggers.
- **Admin routes:**
  - [api/routers/admin_me.py](../api/routers/admin_me.py) `POST /api/admin/me/change-password`.
  - [api/routers/admin_staff.py](../api/routers/admin_staff.py) `POST /api/admin/staff/{user_id}/send-password-reset`.
- **Sales route:** none. Sales portal users authenticate with PINs; sales PIN reset stays under the sales-staff auth flows.
- **Admin UI:** [frontend/src/pages/SalesStaffSettings.jsx](../frontend/src/pages/SalesStaffSettings.jsx) Staff Profiles modal "Access & security" section. Current admin sees `Change Password`; another admin profile shows `Send Password Reset Link`; sales profiles keep PIN management.
- **Sales UI:** none.
- **Auth / gate:** `require_admin_scope`. Password change requires the current password. Reset trigger requires target `role='admin'` and `is_active=true`.
- **Audit:** none today. Revisit whether direct password changes should record a security activity row or emit `admin.password_changed` through the staff notification/event bus.
- **Notification:** reset link uses the existing password-reset email renderer/transport. Reset confirmation email is sent by the reset-confirm flow after a token is consumed.
- **Status:** partial. Backend, first-pass UI, API helpers, and smoke coverage exist; browser verification and frontend lint/build remain in Phase 12 of [SALES_REP_DASHBOARD_PHASES.md](SALES_REP_DASHBOARD_PHASES.md).

## Cross-cutting Infrastructure

These are the shared primitives every capability above depends on. Documented here so future changes do not silently invent parallel ones.

### Auth and gates

- `require_admin_scope` and `require_sales_scope` come from [database/auth.py](../database/auth.py).
- `require_floor_access(*scopes)` is the factory in [services/attendance_gate.py](../services/attendance_gate.py). It enforces scope membership plus, for sales tokens, the attendance gate (`clock_in.current_status` must not be `"out"`). Admin tokens bypass the gate. The gate itself can be disabled per-business via `business_profile.attendance_gate_enabled`.
- Use `require_floor_access` on any sales-touchable mutation. Read endpoints can stay on the plain scope dependency.

### Audit / activity log

- Write helper: `activity_log.log_activity(db, event_id, actor_kind, actor_user_id, activity_type, subject_kind, subject_id, payload)` in [services/activity_log.py](../services/activity_log.py).
- Caller owns the transaction; the helper does not commit.
- Idempotency is the caller's responsibility: only log when state actually changed.
- The full vocabulary of activity types is defined as constants at the top of [services/activity_log.py](../services/activity_log.py). Add new constants there; do not pass raw strings.

### Notification event bus

- Event recording: `notification_routing.record_event(db, kind, subject_kind, subject_id, actor_user_id, payload)` in [services/notification_routing.py](../services/notification_routing.py).
- Recipient resolution: `notification_routing.recipients_for(db, event)`. Three layers (intrinsic targeting, role defaults, per-user overrides).
- Timing modes per kind: `direct`, `real_time`, `real_time_and_digest`, `digest_only`.
- Staff booking helpers: `notify_booking_assigned`, `notify_booking_cancelled` in `services/staff_booking_notifications.py`. They handle ordering around assignment writes (cancel-old before column flip, assign-new after).
- When a write affects assignment, call the cancel-old helper BEFORE mutating `assigned_user_id`, and the assign-new helper AFTER. Otherwise intrinsic targeting will route to the wrong recipient.

### Frontend subdomain routing

- One frontend build serves both subdomains. Selector: `isSalesSubdomain()` in [frontend/src/services/api.js](../frontend/src/services/api.js). It checks `window.location.hostname.startsWith('sales.')` (and a `VITE_FORCE_SUBDOMAIN` override for local testing).
- Admin and sales use distinct CSRF cookies: `__Secure-bellas_xv_csrf` and `__Secure-bellas_xv_sales_csrf`.
- Admin pages live under [frontend/src/pages/](../frontend/src/pages/). Sales pages live under [frontend/src/sales/](../frontend/src/sales/). Genuinely shared components live under [frontend/src/components/](../frontend/src/components/). Reuse a component from `components/` only when it carries no surface-specific assumptions; `AddParticipantDialog` is the current good example.

## Known Gaps This Map Surfaces

These are the items the next slices should clear, ordered by what the map shows:

1. **Participant buyer journey edit surfaces** — partial. The backend can tag appointments/quotes/invoices to named event participants. Admin has pipeline, quick-view, and event Overview read surfaces plus appointment tagging on each Event Overview Booking row; sales has appointment tagging on its dedicated appointment-detail page. Quote/invoice participant tagging is API-only on both surfaces — design a direct UI only when a real workflow asks for it.
2. **Participant create-and-tag appointment flow** — deferred. Staff can create/book using the existing appointment flow and tag afterward. Build a one-shot "create appointment for this participant" flow only if real floor usage shows the extra step is painful.
3. **Admin lead-owner reassignment parity** — built in Phase 11. `PATCH /api/admin/events/{event_id}/owner` + cascade-preview + `AdminEventOwnerDialog` on event Overview, all delegating to `services/sales_assignment.py` (audit `reason="admin_owner_change"`). Smoke green. Browser verification on `admin.shopbellasxv.com` is the only remaining operator-side item.
4. **Admin appointment-level reassignment parity** — decision locked 2026-05-18: **deferred**. Admin keeps the event-owner reassignment pattern. Trigger to revisit: an admin reports they could not do something the sales floor could.
5. **`services/sales_assignment.py` naming** is sales-scoped. Phase 11 makes admin the second consumer, which unlocks the rename to `services/assignment_service.py`. Defer the rename to a follow-up commit after Phase 11 ships so the rename diff stays mechanical and isolated.

The other two map-surfaced gaps closed in Phase 9.4:

- ~~Admin equivalent of `APPOINTMENT_NOTES_EDITED`~~ — closed. Both surfaces emit the row via `services/appointment_audit.py`. See the Internal notes section.
- ~~Quote in-store approval staff notifications~~ — closed. `quote.approved_in_store` emitted with `digest` timing, intrinsic recipient = event owner. See the Quote section.

## Maintenance Rules

- Any new route under `/api/sales/...` or any new admin route that performs a write must add a row to the At-a-Glance table and a section below in the same commit.
- Renaming a shared service updates the relevant section, not a new one.
- Removing a capability removes its section and its row. Do not leave tombstones.
- If a capability moves from sales-only to shared, add the admin route/UI columns and flip status to complete only after audit and notification parity are verified.
