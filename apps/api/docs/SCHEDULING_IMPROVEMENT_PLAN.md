# Scheduling Improvement Plan

Created: 2026-06-03

This tracker keeps the staff scheduling work focused: weekly schedule quality,
staff shift requests, open/pickup shifts, swaps, admin approval, and the smoke
tests that keep attendance and publishing behavior from drifting.

## Goal

Make scheduling feel good for both sides of the shop:

- Admins can build, inspect, publish, adjust, and explain schedules without
  hidden conflicts or out-of-band text threads.
- Staff can see their own schedule, see who else is working, mark recurring
  unavailability, request coverage, offer swaps, and pick up available shifts.
- Attendance, notifications, time off, and payroll-facing reports keep using
  one authoritative schedule model.

## Current State

The existing foundation is strong and should be preserved.

Backend:

- `staff_shifts`: recurring weekly templates.
- `staff_shift_overrides`: temporary date-range exceptions.
- `staff_schedule_entries`: concrete draft/published shifts that the admin grid
  writes and the resolver reads.
- `time_off_requests` plus `time_off_decision_events`: one-off approved /
  denied / cancelled time-off requests with audit history.
- `recurring_unavailability`: staff self-serve weekly unavailable blocks.
- `services.shift_resolver`: single source of truth for resolved shifts.
- `services.staff_schedule`: concrete schedule entry CRUD, publish, attendance
  stamping helpers, labor cost, appointment density warnings.
- `services.auto_scheduler`: generates reviewable draft weeks.

Frontend:

- Admin schedule grid at `frontend/src/pages/AdminScheduleGrid.jsx`.
- Admin finalized week view at `frontend/src/pages/AdminScheduleFinalizedWeek.jsx`.
- Admin time-off queue at `frontend/src/pages/AdminTimeOff.jsx`.
- Sales schedule page at `frontend/src/sales/Schedule.jsx`.
- Sales team schedule already contains disabled `Request cover` and
  `Request swap` affordances.

## Non-Negotiable Guardrails

- Routers stay thin. All scheduling decisions live in services.
- Services do not import FastAPI.
- `shift_resolver.resolve_active_shift` remains the single runtime resolver for
  clock-in, cron, and staff schedule reads.
- Published `staff_schedule_entries` remain the top scheduling precedence:
  published entry -> override -> recurring template -> no shift.
- Do not make `staff_schedule_entries.user_id` nullable for open shifts. Open
  shifts should be represented as request/posting rows until they are assigned.
- Published shift changes must notify affected staff through the existing staff
  notification/event routing pattern.
- No hard delete of published schedule history. Retractions, transfers, swaps,
  and approvals must leave an audit trail.
- Staff-visible team schedule stays privacy-bounded: names and times are OK;
  manager notes, attendance state, punch ids, wages, and private audit details
  are not.
- Any schedule mutation after publication must re-run conflict checks:
  overlapping published shift, approved time off, recurring unavailability,
  existing clock-in/attendance, and past/frozen shift windows.
- Every new workflow gets a smoke test before the phase is marked shipped.

## Current Gaps To Fix First

These should land before the larger request workflow because they affect trust
in the current grid.

### Gap 1: Time-Off Cells Can Hide Existing Entries

Admin grid cells with approved time off currently render the time-off block and
return early. If a draft already exists in that cell, the manager may not see it
or be able to delete it from the grid.

Desired behavior:

- Show the time-off warning.
- Still render any existing draft/published entries in the cell.
- Disable only the "add shift" affordance while approved time off covers the
  cell.

Smoke test:

- `tests/test_schedule_grid_time_off_visibility_smoke.py`
  - Seed approved time off and an overlapping draft.
  - `GET /api/admin/schedule/week` returns both the time-off block and the
    draft entry.
  - Frontend build still succeeds after the grid render change.

### Gap 2: Auto-Scheduler Ignores Recurring Unavailability

`services.auto_scheduler` skips approved time off but does not appear to use
`recurring_unavailability` as an eligibility blocker. Publish later skips those
drafts, which makes Generate create work the manager cannot publish.

Desired behavior:

- Auto-generation treats recurring unavailability like an eligibility blocker.
- Summary reports `skipped_unavailable_count` separately from
  `skipped_time_off_count`.
- Generated draft results should not include shifts that overlap active
  recurring unavailability.

Smoke test:

- `tests/test_auto_scheduler_recurring_unavailability_smoke.py`
  - Seed active sales staff and a recurring unavailable block.
  - Generate a draft week.
  - Assert no generated entry overlaps the unavailable block.
  - Assert summary includes the skipped unavailable count.

### Gap 3: Staff Schedule Payload Needs Entry Identity

`ResolvedShift` carries `schedule_entry_id`, but the sales schedule response
does not expose it. Staff actions need the concrete entry id to request cover,
drop, swap, or pickup.

Desired behavior:

- Sales `GET /api/sales/schedule` includes `schedule_entry_id` for published
  concrete schedule entries.
- Include `manager_notes` for the logged-in staff member's own schedule only.
- Keep team schedule sanitized; coworkers should not see manager notes.

Smoke test:

- `tests/test_sales_schedule_entry_identity_smoke.py`
  - Seed one published entry with manager notes.
  - Sales user's own schedule includes `schedule_entry_id` and notes.
  - Team schedule includes `entry_id` but not manager notes or attendance fields.

### Gap 4: Overlaps Are Mostly Advisory

Duplicate detection rejects exact start/end duplicates but not overlapping
intervals. Split shifts are valid, so overlap rules should be warning-first for
manual scheduling and hard-blocking for request approval.

Desired behavior:

- Admin grid week payload includes per-cell or per-user overlap warnings.
- Manual admin creation can remain allowed if the owner confirms.
- Shift request approval must hard-block overlaps unless explicitly designed as
  a split shift with non-overlapping intervals.

Smoke test:

- `tests/test_schedule_overlap_warnings_smoke.py`
  - Seed overlapping draft entries for one user.
  - Week payload surfaces an overlap warning with both entry ids.
  - Non-overlapping split shifts do not warn.

## Locked Product Decisions

### One Request Model

Use one table for staff schedule workflow requests rather than separate tables
for covers, swaps, drops, and pickups.

Proposed table: `staff_shift_requests`

Core columns:

- `id`
- `request_type`
  - `cover`: staff wants someone else to cover their shift.
  - `swap`: staff proposes trading shifts with another staff member.
  - `drop`: staff asks manager to remove them from a shift, with no proposed
    replacement yet.
  - `pickup`: staff asks to claim an open shift posting.
- `status`
  - `pending`
  - `accepted_by_staff`
  - `approved`
  - `denied`
  - `cancelled`
  - `expired`
- `source_entry_id`: the published shift being covered, dropped, or swapped.
- `target_entry_id`: the other shift in a swap, nullable otherwise.
- `open_shift_post_id`: optional link for pickup requests.
- `requester_user_id`
- `candidate_user_id`: proposed cover / swap / pickup staffer.
- `accepted_by_user_id`
- `accepted_at`
- `decided_by_user_id`
- `decided_at`
- `reason`
- `decision_notes`
- `created_at`
- `updated_at`

Proposed audit table: `staff_shift_request_events`

- `id`
- `request_id`
- `actor_kind`
- `actor_user_id`
- `action`
- `old_values`
- `new_values`
- `notes`
- `created_at`

### Open Shifts Are Not Schedule Entries Yet

Do not store open shifts as `staff_schedule_entries.user_id = NULL`.

Proposed table: `open_shift_posts`

- `id`
- `business_date`
- `starts_at_local`
- `ends_at_local`
- `late_grace_minutes`
- `source`
- `manager_notes`
- `status`: `open | claimed | cancelled | expired`
- `created_by_user_id`
- `claimed_by_user_id`
- `claimed_request_id`
- `created_at`
- `updated_at`

When a pickup is approved, create a normal published `staff_schedule_entries`
row for the approved staffer and close the open post.

### Manager Approval Is Required

Staff can request, offer, accept, or claim. Only admin approval mutates the
published schedule. This keeps labor, attendance, and notifications under owner
control.

### Requests Cannot Mutate Started Shifts

Requests are blocked once the shift has started or has any attendance stamped:

- `actual_clock_in_punch_id IS NOT NULL`
- `actual_clock_out_punch_id IS NOT NULL`
- `attendance_status IN ('present', 'late', 'no_show', 'missing_out_punch',
  'excused')`

The exact cutoff can be softened later, but v1 should be conservative.

## Open Decision Gates

Do not move into implementation for the affected phase until each gate is
answered and written here.

### Gate 1: Request Cutoff Window

Question: how close to a shift start can staff request cover/swap/drop?

Default recommendation:

- Staff can request until 12 hours before shift start.
- Admin can override until shift start if no attendance exists.
- Requests auto-expire at shift start.

Decision:

- Accepted as recommended (2026-06-03). Staff cutoff is 12h before
  shift start; admin can act until start when no attendance exists;
  pending requests auto-expire at start (expiry cron lands in Phase 5,
  but the 12h staff cutoff and the admin-until-start window are
  enforced from Phase 2).

### Gate 2: Staff Acceptance Required For Direct Cover

Question: if Maria requests Sofia to cover a shift, does Sofia need to accept
before the manager approves?

Default recommendation:

- Yes. Direct cover/swap flows should require the candidate staffer to accept,
  then manager approval finalizes.
- Open pickup can skip the staff acceptance step because claiming is the
  acceptance.

Decision:

- Accepted as recommended (2026-06-03). A direct cover/swap with a named
  candidate must reach `accepted_by_staff` before the admin can approve;
  the admin sees the conflict-checked candidate only after acceptance.
  Open pickup (Phase 3) skips the acceptance step.

### Gate 3: Visibility Of Open Requests

Question: can all staff see every open cover request, or only selected
candidate staff?

Default recommendation:

- Staff can see open pickup/cover opportunities.
- Staff can only see direct swap/cover requests that involve them.
- Admin can see all.

Decision:

- Accepted as recommended (2026-06-03). Open (un-candidated)
  pickup/cover postings are visible to all active sales staff; direct
  cover/swap requests are visible only to the requester, named
  candidate, and accepter; admin sees everything. (Phase 1 shipped the
  involved-only path; the "open board visible to all" path is added
  alongside open cover/pickup.) Drop approval in Phase 2 retracts the
  source entry to draft (conservative v1); converting a drop to an open
  post is deferred until `open_shift_posts` lands in Phase 3.

### Gate 4: Notification Channels

Question: email only, in-app only, or both?

Default recommendation:

- In-app/staff notification event always.
- Email for actionable staff requests and admin decisions.
- No SMS in v1.

Decision:

- Accepted as recommended (2026-06-03). Every transition writes a staff
  notification event (in-app); actionable steps (candidate nominated,
  candidate accepted, admin approved/denied) also send email via the
  existing best-effort transport. No SMS in v1.

## Phased Plan

### Phase 0: Correctness And Payload Hardening

Purpose: make the current schedule surfaces trustworthy before adding new
workflow state.

Backend tasks:

- Add schedule overlap warning computation to `services.staff_schedule.list_week`.
- Add recurring unavailability filtering to `services.auto_scheduler`.
- Add `schedule_entry_id` and own-shift `manager_notes` to sales schedule
  expansion.
- Keep team schedule allowlisted and sanitized.

Frontend tasks:

- Admin grid renders existing entries even inside approved time-off cells.
- Admin grid surfaces overlap warnings without blocking manual scheduling.
- Sales "My schedule" can display manager notes.

Smoke tests:

- `tests/test_schedule_grid_time_off_visibility_smoke.py`
- `tests/test_auto_scheduler_recurring_unavailability_smoke.py`
- `tests/test_sales_schedule_entry_identity_smoke.py`
- `tests/test_schedule_overlap_warnings_smoke.py`
- Existing regression set:
  - `tests/test_schedule_smoke.py`
  - `tests/test_schedule_resolver_smoke.py`
  - `tests/test_schedule_stability_smoke.py`
  - `tests/test_auto_scheduler_smoke.py`
  - `tests/test_recurring_availability_smoke.py`
  - `tests/test_sales_team_schedule_smoke.py`
  - `tests/test_time_off_endpoints_smoke.py`

Exit criteria:

- Current admin and sales schedule features behave the same or better.
- No new request tables yet.
- Frontend build succeeds.

### Phase 1: Shift Request Schema And Read-Only Queue

Purpose: introduce first-class request records without mutating schedules yet.

Backend tasks:

- Migration: create `staff_shift_requests`.
- Migration: create `staff_shift_request_events`.
- Add service `services/staff_shift_requests.py`.
- Add admin router under `/api/admin/schedule/shift-requests`.
- Add sales router under `/api/sales/schedule/shift-requests`.
- Implement create/list/get/cancel for request records.
- Add stable error codes for:
  - `entry_not_found`
  - `entry_not_published`
  - `entry_not_yours`
  - `entry_started`
  - `request_not_found`
  - `request_terminal`
  - `invalid_request_type`
  - `invalid_candidate`

Frontend tasks:

- Admin "Shift requests" tab under Staff -> Schedule & time off.
- Sales "Requests" section under Schedule.
- Keep approval buttons disabled or hidden until Phase 2.

Smoke tests:

- `tests/test_shift_requests_schema_smoke.py`
  - DML probes check status/type constraints.
  - FK behavior is explicit.
- `tests/test_shift_request_create_cancel_smoke.py`
  - Staff can create a cover/drop/swap request against own future published
    shift.
  - Staff cannot request against a coworker's shift.
  - Staff can cancel own pending request.
  - Terminal requests cannot be cancelled again.
- `tests/test_shift_request_rbac_smoke.py`
  - Sales token cannot hit admin queue.
  - Admin token cannot hit sales create endpoints unless explicitly dual-scoped.
  - Coworker cannot read private direct swap request unless involved.

Exit criteria:

- Requests are durable and auditable.
- No schedule mutation happens from request approval yet.

### Phase 2: Cover / Drop Requests And Admin Approval

Purpose: let staff put one of their shifts up for coverage and let admin
approve the transfer.

Backend tasks:

- Add `accept_request` for candidate staff.
- Add `decide_request` for admin.
- Implement cover approval:
  - Lock request row.
  - Lock source schedule entry.
  - Validate source entry is still published and future.
  - Validate candidate is active sales staff.
  - Validate no overlapping published schedule entry for candidate.
  - Validate no approved time off for candidate.
  - Validate no recurring unavailability conflict for candidate.
  - Transfer `staff_schedule_entries.user_id` to candidate.
  - Preserve source entry id so attendance and notifications remain tied to the
    concrete shift.
  - Write request event and schedule notification events.
- Implement drop approval:
  - Conservative v1: retract the source entry to draft or convert to open post,
    depending on Gate 3 decision.

Frontend tasks:

- Enable `Request cover` on user's own published future shifts.
- Add accept/decline action for staff who are nominated as candidate.
- Admin queue shows conflicts before approval.

Smoke tests:

- `tests/test_shift_cover_approval_smoke.py`
  - Requester creates cover request.
  - Candidate accepts.
  - Admin approves.
  - Source `staff_schedule_entries.user_id` changes to candidate.
  - Request becomes `approved`.
  - Request events include requested, accepted, approved.
- `tests/test_shift_cover_conflicts_smoke.py`
  - Candidate overlapping published shift blocks approval.
  - Candidate approved time off blocks approval.
  - Candidate recurring unavailability blocks approval.
  - Started shift blocks request/approval.
- `tests/test_shift_cover_notifications_smoke.py`
  - Requester and candidate receive appropriate staff notification events.
  - Old assignee receives removal/covered notice.
  - New assignee receives added/assigned notice.

Exit criteria:

- A real shift can be covered end-to-end.
- Existing attendance tests still pass after entry ownership transfer.

### Phase 3: Open Shifts And Pickup Board

Purpose: let managers post shifts that staff can claim.

Backend tasks:

- Migration: create `open_shift_posts`.
- Add admin create/cancel/list endpoints.
- Add sales list endpoint for open posts.
- Add sales pickup request creation.
- Add admin pickup approval:
  - Lock open post.
  - Validate post is still open.
  - Validate claimant eligibility and conflicts.
  - Create normal published `staff_schedule_entries` row.
  - Close post as `claimed`.
  - Link `claimed_request_id`.

Frontend tasks:

- Admin grid can create an open shift from an empty cell.
- Sales schedule gains "Open shifts" / "Pick up" tab.
- Admin request queue includes pickup claims.

Smoke tests:

- `tests/test_open_shift_pickup_smoke.py`
  - Admin posts open shift.
  - Sales user sees it.
  - Sales user claims it.
  - Admin approves.
  - Published schedule entry is created for claimant.
  - Open post is marked claimed.
- `tests/test_open_shift_pickup_conflicts_smoke.py`
  - Existing shift/time off/unavailability conflicts block approval.
  - Cancelled/claimed/expired post cannot be claimed again.
- `tests/test_open_shift_privacy_smoke.py`
  - Sales open-shift response does not leak manager-only fields beyond approved
    display copy.

Exit criteria:

- Admin can staff gaps without selecting a person up front.
- Staff can pick up opportunities from their portal.

### Phase 4: Swap Requests

Purpose: let staff propose trading one of their shifts with another published
shift.

Backend tasks:

- Implement swap request creation from sales team schedule.
- Validate requester owns `source_entry_id`.
- Validate target belongs to another active sales staff member.
- Candidate accepts or denies.
- Admin approves:
  - Lock both entries and request.
  - Validate both entries are published, future, and unstarted.
  - Validate each user can work the other's interval.
  - Swap `user_id` values.
  - Write request events.
  - Notify both staff.

Frontend tasks:

- Enable `Request swap` on coworker future published shifts.
- Staff request detail shows "your shift" and "their shift".
- Admin approval dialog shows before/after swap preview and conflicts.

Smoke tests:

- `tests/test_shift_swap_approval_smoke.py`
  - Staff A proposes swap with Staff B.
  - Staff B accepts.
  - Admin approves.
  - Entry ownership swaps.
  - Events and notifications are written.
- `tests/test_shift_swap_conflicts_smoke.py`
  - Time off/unavailability/overlapping third shift blocks swap.
  - Started shift blocks swap.
  - Terminal request cannot be re-decided.
- `tests/test_shift_swap_privacy_smoke.py`
  - Staff can only see swap requests involving themselves.

Exit criteria:

- Direct staff-to-staff swaps are safe and audited.

### Phase 5: Admin UX Polish And Operational Reporting

Purpose: make the system pleasant enough to use weekly.

Backend tasks:

- Add request counts to schedule week payload:
  - pending requests this week
  - open shifts this week
  - unresolved conflicts
- Add optional expiration cron:
  - pending requests expire at shift start or configured cutoff.
  - open posts expire at shift start.
- Cron writes `cron_run_state`.

Frontend tasks:

- Admin grid badges cells with pending request/open shift/conflict counts.
- Finalized week view includes open/pending state.
- Sales schedule shows request status inline on affected shifts.
- Add filters to admin request inbox: pending, accepted, approved, denied,
  cancelled, expired.

Smoke tests:

- `tests/test_shift_request_expiry_cron_smoke.py`
  - Expired requests/posts flip state.
  - Cron state is updated.
  - Running cron twice is idempotent.
- `tests/test_schedule_request_badges_smoke.py`
  - Week payload includes request/open-shift counts for affected dates.

Exit criteria:

- Owner can understand schedule exceptions from the grid without opening every
  row.

## Service Design Notes

### Validation Helper

Add a reusable helper in `services.staff_schedule` or a new
`services.schedule_conflicts` module:

```python
validate_staff_can_work_interval(
    db,
    *,
    user_id: int,
    starts_at_local: datetime,
    ends_at_local: datetime,
    exclude_entry_ids: set[int] | None = None,
) -> list[dict]
```

It should return structured conflicts rather than raising immediately:

- `published_overlap`
- `approved_time_off`
- `recurring_unavailability`
- `inactive_user`

Request approval can turn any non-empty list into a 409. Admin manual creation
can show warnings while still allowing the owner to proceed.

### Transfer Helper

Published schedule entry ownership changes should use one helper, not ad hoc
`entry.user_id = ...` scattered through request flows:

```python
transfer_published_entry(
    db,
    *,
    entry_id: int,
    from_user_id: int,
    to_user_id: int,
    actor_user_id: int,
    reason: str,
    request_id: int | None = None,
) -> StaffScheduleEntry
```

The helper owns:

- row lock
- source/current owner validation
- attendance-start validation
- conflict validation for destination user
- audit/request event write
- notification event write

### Notification Kinds

Candidate event kinds:

- `staff.shift_cover_requested`
- `staff.shift_cover_accepted`
- `staff.shift_cover_approved`
- `staff.shift_cover_denied`
- `staff.shift_swap_requested`
- `staff.shift_swap_accepted`
- `staff.shift_swap_approved`
- `staff.shift_swap_denied`
- `staff.open_shift_posted`
- `staff.open_shift_claim_approved`
- `staff.open_shift_claim_denied`

Add them to the staff notification map and renderer in the same phase that
emits them.

## Regression Set Before Any Scheduling Release

Run the new phase smoke plus the existing scheduling/attendance set:

```bash
venv/bin/python tests/test_schedule_smoke.py
venv/bin/python tests/test_schedule_resolver_smoke.py
venv/bin/python tests/test_schedule_stability_smoke.py
venv/bin/python tests/test_schedule_attendance_stamping_smoke.py
venv/bin/python tests/test_schedule_resend_smoke.py
venv/bin/python tests/test_sales_team_schedule_smoke.py
venv/bin/python tests/test_time_off_endpoints_smoke.py
venv/bin/python tests/test_recurring_availability_smoke.py
venv/bin/python tests/test_auto_scheduler_smoke.py
venv/bin/python tests/test_attendance_review_smoke.py
venv/bin/python tests/test_attendance_crons_smoke.py
venv/bin/python tests/test_attendance_reporting_smoke.py
```

Frontend:

```bash
cd frontend && npm run build
```

## Phase Tracker

| Phase | Status | Notes |
|---|---|---|
| Phase 0: correctness and payload hardening | done | Shipped 2026-06-03. Overlap warnings, recurring-unavailability in auto-scheduler, sales `schedule_entry_id` + own-shift `manager_notes`, grid renders entries inside time-off cells. 4 new smokes + regression set green. |
| Phase 1: request schema and read-only queue | done | Shipped 2026-06-03. Migration 081 (`staff_shift_requests` + append-only `staff_shift_request_events`), `services/staff_shift_requests.py`, admin + sales routers under `/api/{admin,sales}/schedule/shift-requests`, read-only admin tab + sales Requests tab (create/cancel only). 3 smokes + regression green. No schedule mutation. |
| Phase 2: cover/drop requests and approval | done | Shipped 2026-06-03. Candidate accept/decline, admin approve/deny, `transfer_published_entry` (cover) + `retract_published_entry_to_draft` (drop) with `validate_staff_can_work_interval` re-checks under a row lock, 12h staff cutoff, in-app + email cover/drop notifications. 3 smokes + notification/schedule regression green. |
| Phase 3: open shifts and pickup board | done | Shipped 2026-06-03. Migration 082 (`open_shift_posts` + deferred FK on `staff_shift_requests.open_shift_post_id`), `services/open_shifts.py`, sales board + claim, pickup approval in `decide_request` (creates published entry, closes post, expires losing claims), admin Open shifts tab + queue renders pickups. 3 smokes + regression green. Admin posts open shifts from a dedicated tab (cleaner than per-cell grid surgery). |
| Phase 4: swap requests | done | Shipped 2026-06-03. `swap_published_entries` (lock both, both-sided conflict re-checks, atomic owner swap), accept/decide swap branches, swap notifications, sales "Request swap" on coworker shifts + accept/decline, admin swap preview. No new tables/routes. 3 smokes + regression green. |
| Phase 5: UX polish and reporting | done | Shipped 2026-06-03. `schedule.shift_request_expiry` runs on the schedule monitor, expires stale requests at shift start and open posts at the 12h claim cutoff, and stamps `cron_run_state`. Admin week payload adds request/open/conflict counts, grid renders exception badges, and sales schedule shows inline active request status. 2 smokes + cover/pickup/swap/schedule regression green. |

## Recommended Next Slice

Start with Phase 0.

Why:

- It improves the current admin/staff schedule experience immediately.
- It reduces confusing generated drafts before request workflows exist.
- It exposes `schedule_entry_id`, which every later staff request action needs.
- It is low-risk compared with adding new request-state tables.

Do not start Phase 1 until Phase 0 smokes exist and pass.

Update (2026-06-03): Phase 0 is shipped. The four Phase 0 smokes exist
and pass alongside the scheduling/attendance regression set, the
frontend builds, and the week/sales payloads now carry overlap
warnings and `schedule_entry_id`. Phase 1 is unblocked; resolve the
four Open Decision Gates before starting Phase 2.
