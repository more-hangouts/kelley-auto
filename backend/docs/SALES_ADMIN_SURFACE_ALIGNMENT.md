# Sales/Admin Surface Alignment

## Purpose

`admin.shopbellasxv.com` and `sales.shopbellasxv.com` should be two role-specific views over the same business system, not two separate products that slowly drift apart.

The guiding idea is:

- **Admin owns oversight, configuration, reporting, and sensitive business operations.**
- **Sales owns floor execution, quick capture, and appointment workflow.**
- **Both surfaces should share the same domain services, data model, audit trail, and notification/event bus wherever the underlying business action is the same.**

This document summarizes the current state of the Sales Rep Dashboard / shared tablet work, the architectural concerns found during review, and a methodical path to finish it cleanly.

## Current Read

The project is in a mostly healthy state. The backend was built more carefully than a quick patch: the sales endpoints generally act as thin wrappers around shared service logic, with sales-specific auth and attendance rules layered at the router boundary.

The remaining gaps are mostly product-surface gaps, especially in the sales frontend.

## What Is Already Good

### Shared Walk-In Engine

Admin walk-ins and sales walk-ins both flow through `services.walk_in_service.create_walk_in_lead`.

- Admin route: `POST /api/walk-in-leads`
- Sales route: `POST /api/sales/walk-ins`
- Shared service: `services/walk_in_service.py`

This is the right pattern. The route layer owns authentication, request/response shape, and transaction boundary. The service owns the real business write: contact, appointment, enrichment, event promotion, audit, and notification hooks.

Sales adds legitimate sales-only behavior:

- Requires `require_floor_access("sales")`.
- Defaults `assigned_user_id` to the current stylist.
- Validates assignment against active sales users.

That is not harmful duplication; it is role-specific policy around shared business logic.

### Separate Sales-Safe Search

Admin global search and sales lead search are intentionally separate.

- Admin route: `/api/search`
- Sales route: `/api/sales/search/leads`
- Sales service: `services/sales_search_service.py`

This is a reasonable security boundary. Admin search can include business-heavy or financial context. Sales search must never expose invoice totals, balances, quote totals, payment data, document keys, raw tokens, or marketing attribution.

A shared search service with a `sales_safe=true` flag would be fragile. A future admin field could accidentally leak into sales. Keeping a dedicated sales-safe query shape is professional and defensible.

### Kiosk Session Model

The shared tablet model is sound:

- `POST /api/sales/auth/kiosk-lock` clears only this device's sales session cookies.
- It does not bump `users.token_version`.
- Full logout still exists separately and invalidates that user's token family.
- Frontend idle warning and auto-lock are centralized in `SalesAuthContext`.

This fits the real-world store tablet workflow.

### Assignment Data Model

The project did not create an unnecessary `leads` table.

Current model:

- Lead/event owner: `events.owner_user_id`
- Appointment assignee: `appointments.assigned_user_id`

That matches the domain well. A lead can have a business owner, while specific appointments can have their own assigned stylist.

### Participants Need Buyer Journeys

The current participant model correctly ties court members, parents, and other people to the shared quince event. It does not yet make those participants visible as their own customer journeys in the pipeline.

That is now a planned capability. The business need is that a `chambelan`, `dama`, parent, or other participant may also buy from Bellas. They should remain tied to the celebrant's event while still being trackable as a buyer with their own appointment timing, try-on history, quote, invoice, and assignment.

The clean direction is not to create a duplicate event for each participant. Keep one shared event as the party container, then model participant buyer journeys underneath it.

### Audit And Notification Direction

Assignment and walk-in work is tied into activity logging and, increasingly, the notification event bus. This is the correct direction. Admin should be able to see what sales did, who did it, and when.

## Current Concerns

### Sales Frontend Is Behind The Backend

The backend has these pieces:

- `POST /api/sales/walk-ins`
- `GET /api/sales/staff/assignable`
- `PATCH /api/sales/appointments/{id}/assignment`
- `PATCH /api/sales/leads/{event_id}/assignment`

The frontend has API helpers for these. The walk-in workflow is now exposed; assignment controls remain open.

Known missing surfaces:

- An **admin lead-owner reassignment** control. Sales has one wired through [SalesAssignmentDialog.jsx](../frontend/src/sales/SalesAssignmentDialog.jsx); admin needs the equivalent, scoped as Phase 11 in [SALES_REP_DASHBOARD_PHASES.md](SALES_REP_DASHBOARD_PHASES.md). The sales-side appointment-reassign + lead-reassign controls are built and live.
- Clear UI refresh behavior after reassignment (currently invalidates `['event', eventId]` and `['events', 'board']` on sales — admin slice will mirror).

This means the backend is mostly ready, but the assignment parts of the store workflow are not complete for actual use.

### Assignment Service Is Sales-Named

`services/sales_assignment.py` contains logic that may become a shared CRM concept:

- Reassign one appointment.
- Reassign lead owner.
- Cascade lead reassignment to future appointments.
- Write audit rows.
- Trigger booking assignment/cancellation notifications.

Today it is only exposed through sales routes, so the name is understandable. Phase 11 (admin lead-owner reassignment) makes admin the second consumer, which is the trigger to rename to `services/assignment_service.py`. The rename will land in a follow-up commit after Phase 11 ships so the diff stays mechanical and isolated from new behavior.

Concern:

- Long-term, `sales_assignment.py` may become a shared business service with a sales-only name.

Preferred direction:

- Keep sales-specific route policy in `api/routers/sales_assignment.py`.
- Move shared assignment domain logic to a neutral module such as `services/assignment_service.py` or `services/appointment_assignment.py` when admin begins using the same behavior.

### Admin Parity For Sales Features Is Not Fully Mapped

The vision is that any capability available to sales should naturally be visible to admin, with stricter permissions and more context.

Right now, some capabilities exist in sales-specific routes before their admin-facing equivalent is explicitly mapped:

- Assignment/reassignment APIs exist under `/api/sales/...`.
- Admin may still need explicit UI/API paths for the same operation.
- Admin should see sales-created walk-ins and assignments through existing event/appointment views, but this should be verified.

The risk is not immediate breakage. The risk is gradual product drift.

### Route Duplication Needs A Rule

Duplicate routes are not automatically bad. They are acceptable when they represent different role policies over shared domain behavior.

But duplicate routes become unhealthy when they copy business logic.

Rule:

- **Good duplication:** Separate route wrappers, separate response shapes, separate auth gates.
- **Bad duplication:** Two routes independently implementing the same business write, status transition, audit behavior, or notification behavior.

Current state is mostly the good kind, especially for walk-ins.

### Sales Dashboard Doc Is Over-Optimistic

`docs/SALES_REP_DASHBOARD_PHASES.md` marks the overall plan complete, but the practical sales UI is not done.

The doc is accurate for many backend phases, but it can create a false sense that the product workflow is finished.

Recommended fix:

- Add a final "UI completion / parity hardening" phase, or update the tracking status to distinguish backend complete from sales-facing workflow complete.

### Post-Deploy Verification Still Matters

The plan explicitly leaves post-restart production verification open:

- systemd status
- `/api/health`
- functional `/api/sales/search/leads?q=test`
- API journal review
- browser check on `sales.shopbellasxv.com`

This should remain a required release step.

## Architecture Principles Going Forward

### 1. Shared Domain Services First

When admin and sales perform the same business action, implement the action once in `services/`.

Examples:

- Walk-in creation belongs in `walk_in_service`.
- Assignment should belong in a neutral assignment service if admin uses it.
- Appointment status transitions should stay centralized.
- Event status changes should stay in `event_service`.

Routes should be thin.

### 2. Role-Specific Routes Are Allowed

Separate routes are fine when role behavior differs.

Examples:

- Sales walk-ins require a punched-in stylist.
- Admin walk-ins do not.
- Sales search returns safe lead/appointment/contact presentation rows.
- Admin search can return full operational context.

This keeps security explicit and easier to test.

### 3. One Data Model

Do not create sales-only copies of business tables.

Preferred:

- `appointments.assigned_user_id`
- `events.owner_user_id`
- shared contacts
- shared events
- shared participant/buyer journey relationships
- shared activity log
- shared notification event stream

Avoid:

- `sales_leads`
- `sales_appointments`
- duplicate contact records
- duplicate quince events for court members unless the business intentionally wants separate celebration pipelines
- sales-only audit stores

### 4. Admin Sees Everything Sales Does

Any sales write should be visible to admin through one or more of:

- event detail
- appointment detail
- activity timeline
- notification log
- reporting views

If a sales action cannot be audited or seen from admin, it is not finished.

### 5. Sensitive Data Flows Downward Only By Design

Admin can see sales-safe data plus business-sensitive data.

Sales should see only what is needed for the floor:

- appointment details
- customer/contact info needed for service
- event context
- try-on logs
- quotes where appropriate
- assignment information

Sales should not receive:

- invoice totals unless explicitly approved
- balances
- payment history
- margin/profit fields
- business performance totals
- document storage keys
- raw invite tokens
- raw marketing attribution

### 6. Every Shared Action Gets Tests At The Boundary

For each shared business action, test:

- Admin can do what admin should do.
- Sales can do what sales should do.
- Sales cannot do admin-only operations.
- Punched-out sales users cannot mutate floor data when the attendance gate is enabled.
- Audit rows are written once.
- Notification events are emitted once.
- Repeated/idempotent requests do not spam audit timelines.

## Proposed Clean Path

### Phase A: Surface Map

The capability map now lives at [SALES_ADMIN_CAPABILITY_MAP.md](SALES_ADMIN_CAPABILITY_MAP.md). Treat it as the source of truth: update it in the same commit as any route, service, or UI change that adds or removes a capability.

The map confirms the current gaps the rest of this document plans for:

- Sales appointment and lead reassignment controls are missing on the frontend (Phase C).
- Admin parity for per-appointment reassignment is not designed yet (Phase C/E).
- `services/sales_assignment.py` remains sales-named (Phase E).
- Admin's appointment PATCH does not currently write `APPOINTMENT_NOTES_EDITED` even though sales does (worth resolving when the notes path moves to a shared service).
- Quote in-store approval is not currently routed through the staff notification event bus.

### Phase B: Finish Sales Walk-In UI Properly

Status: implemented in [frontend/src/sales/SalesWalkInDialog.jsx](../frontend/src/sales/SalesWalkInDialog.jsx) and wired from [frontend/src/sales/RepDashboard.jsx](../frontend/src/sales/RepDashboard.jsx).

The implemented UX:

Recommended UX:

- Primary `Add Walk-In` button on `RepDashboard`.
- Dialog or full-screen mobile-friendly form.
- Sections:
  - Contact
  - Celebrant/event
  - Enrichment/preferences
  - Assigned stylist
- Default assignee is current user.
- Optional assignee picker uses `GET /api/sales/staff/assignable`.
- Submit calls `POST /api/sales/walk-ins`.
- On success, navigate to returned `route`.
- Refresh today's appointments after create.
- Show attendance-gate error if stylist is not punched in.

Implementation should reuse admin form concepts where possible, but not by importing an admin-heavy component if that component carries admin-only assumptions.

### Phase C: Assignment UI And Admin Parity

Decide which assignment actions belong on each surface.

Sales likely needs:

- Assign this appointment to me.
- Assign this appointment to another stylist.
- Possibly reassign lead owner if the floor is allowed to manage ownership.

Admin likely needs:

- Full reassignment controls.
- Ability to view assignment history.
- Possibly bulk/cascade tools.

**Status (2026-05-18):**

- Admin lead-owner reassignment is scoped as Phase 11 in [SALES_REP_DASHBOARD_PHASES.md](SALES_REP_DASHBOARD_PHASES.md). Adds `PATCH /api/admin/events/{event_id}/owner` + cascade preview + an Overview-tab dialog. Delegates to `services/sales_assignment.py` so cascade rules and audit shape match sales exactly. Admin is not geofenced, so the route uses `require_admin_scope` with no floor gate.
- Admin per-appointment assignment parity stays deferred (2026-05-18 decision). Admin manages attribution through the event owner, which Phase 11 makes editable.

Clean implementation:

- Keep router-level policy separate.
- Use shared assignment service logic.
- If admin needs the same service, rename/move `services/sales_assignment.py` to a neutral module before expanding further.

### Phase D: Update The Dashboard Plan Status

Status: implemented. `docs/SALES_REP_DASHBOARD_PHASES.md` now has Phase 9, which separates backend completeness from user-facing workflow completion.

### Phase E: Service Naming Cleanup When Needed

Do not rename services just for aesthetics today if no code will use the new shape yet.

But before admin starts using assignment operations, promote the domain logic to a neutral module.

Possible target:

- `services/assignment_service.py`

Possible public functions:

- `reassign_appointment(...)`
- `reassign_event_owner_and_future_appointments(...)`
- `list_assignable_staff(...)` if staff filtering becomes shared

Sales and admin routers can then import the same service.

### Phase F: Verification Matrix

Before calling the shared tablet workspace complete, verify:

- Sales PIN login lands on dashboard.
- Lock/Switch clears only this tablet session.
- Idle lock fires after configured timeout.
- Clocked-out stylist can search but cannot create walk-ins or reassign.
- Clocked-in stylist can create a walk-in assigned to self.
- Clocked-in stylist can assign walk-in to coworker.
- Admin can see the created contact, appointment, event, activity, and assignment.
- Sales search does not return forbidden financial/payment fields.
- Admin search still works with full admin context.
- Today's appointments `Mine only` works once assigned rows exist.
- Assignment audit rows appear once.
- Assignment notifications do not duplicate.

### Phase G: Participant Buyer Journeys

Participant buyer journeys now have a backend foundation, admin read surfaces, and first-pass sales tagging UI.

Problem statement:

- Today, adding Anthony Mendez as a `chambelan` correctly creates an `event_participants` row and shows him on the event detail.
- Before Phase 10, the pipeline card was built from event-level fields, so Anthony did not appear as a pipeline card or active buyer.
- For the business, that was incomplete: Anthony may be a customer buying from Bellas, either on the same day as the celebrant or on a later visit.
- Phase 10 now lets appointments, quotes, and invoices be tagged to the specific `event_participants` row. The pipeline signal is `named_buyer_count`; the event quick-view and Overview tab provide the per-buyer breakdown.

Product direction:

- Keep the quince event as the shared party container.
- Let each participant become an implicit buyer journey tied to that event.
- Surface participant buyer journeys in the pipeline without losing the relationship to the celebrant's party. First pass: named-buyer count chip. Current detail pass: event quick-view and Overview breakdown with appointments, quotes, invoices, and deep-links.
- Support same-day and later-day workflows:
  - Same-day: add the participant from the celebrant's appointment/event and reuse today's context.
  - Later-day: search/find the celebrant event, add or open the participant, and create a separate appointment tied to the same event.

Data-model decision made in Phase 10.2:

- Chosen: add `event_participant_id` to appointments, quotes, and invoices so each buyer row can belong to a specific participant while still sharing the quince event.
- Deferred: a separate buyer-journey table. Add it only if the implicit FK model cannot support reporting, lifecycle state, or merge/retire controls.
- Avoid using `events.court_size` as the actual named-buyer count. Planned court size and captured buyers should be displayed separately.

Both admin and sales should use the same underlying service. Admin can have broader controls; sales should have the floor-safe, attendance-gated flow.

Current remaining gaps:

- Admin has the pipeline buyer-count chip, the quick-view + Overview per-buyer breakdown, and an appointment-tag chip on each Event Overview Booking row. Admin tags appointments from the event surface by design (no dedicated `/appointments/:id` page — the event is the party container).
- Sales can tag appointments from appointment detail; quote/invoice tagging is API-only until a UI is designed.
- Later-day create-and-tag-in-one-action is deferred; staff can create/book first and tag afterward.
- Browser verification remains operator-side, especially the admin Overview deep-links and sales geofence-protected chip/dialog path.

## Recommended Standards For Future Slices

Use this checklist before each implementation slice:

- Is this a shared business action or a surface-only convenience?
- If shared, is the domain logic in `services/`?
- Are route wrappers thin?
- Are admin and sales auth policies explicit?
- Does sales receive only sales-safe fields?
- Does admin have visibility into the sales action?
- Is the activity log updated?
- Are notification events emitted through the event bus, not one-off email shortcuts?
- Are tests covering both allowed and forbidden role paths?
- Did we avoid creating a second table/model for the same real-world thing?
- Did we update the capability map?

## Bottom Line

The current architecture is not a mess. It is mostly pointed in the right direction.

The main thing now is discipline:

- Finish the sales UI against the backend that already exists.
- Map admin/sales parity explicitly.
- Promote sales-named domain services to neutral shared services when admin begins using the same behavior.
- Keep security boundaries at the route/response layer.
- Keep business rules in shared services.

Slow and steady is the right call here. The next work should be small, deliberate slices that preserve the "two sides of the same coin" model instead of adding quick frontend patches that hide unfinished architecture underneath.
