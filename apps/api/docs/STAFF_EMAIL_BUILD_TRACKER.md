# Transactional Email Build Tracker

Working tracker for the transactional email build. Promotional emails are explicitly out of scope. The catalog started at 38 kinds; #39 (`quote.approved_in_store`) was added 2026-05-18 in Phase 9.4 D3 to surface staff-witnessed in-store approvals on the daily digest.

**Design context:** [STAFF_NOTIFICATIONS_MAP.md](STAFF_NOTIFICATIONS_MAP.md) (architecture for staff fan-out, preferences, digests). This tracker is operational — what's done, what's next, where each template lives.

## Test-mode mechanism (Slice 0)

Set `EMAIL_DEV_REDIRECT=luis@morehangouts.com` on the VPS `.env`. With it set:

- Every outbound email — current AND future — has its `To:` rewritten to that address.
- Subjects are prefixed `[TEST -> original@recipient.com]` so the original recipient is visible at a glance.
- A banner above the email body says `TEST EMAIL — would have gone to original@recipient.com`.
- Unset the var (or set empty) to resume real delivery.

CLI for firing test renders without going through any real trigger:

```bash
cd /opt/bellas-xv  # or wherever the venv lives
python scripts/send_test_emails.py --kind booking.confirmation
python scripts/send_test_emails.py --kind all        # every registered fixture
python scripts/send_test_emails.py --list            # show registered kinds + status
```

Fixtures are synthesized in-script (deterministic, no DB dependency).

## Notification kind naming boundary

Three distinct name spaces exist in the codebase; conflating them is a footgun. A kind name that looks reasonable in one space is meaningless or actively wrong in another.

### 1. Event-bus kinds (dotted, used with `record_event`)

These flow through `services/notification_routing.py`. They appear in three registries:

- `TIMING_MODE` (`services/notification_routing.py`): how the dispatcher handles the kind (`real_time`, `digest`, `real_time_and_digest`, `direct`).
- `INTRINSIC_TARGETING` (`services/notification_routing.py`): the helper resolving "who this event is about."
- `STAFF_EMAIL_RENDERERS` (`services/notification_service.py`): which Python function renders the staff email.

Every catalog kind (#12-#39) that's on the staff event bus uses a dotted name (`staff.booking_assigned`, `staff.shift_added`, `quote.approved_in_store`, etc.). `record_event(kind=..., ...)` is the single producer surface.

### 2. Legacy enqueue keys (snake_case, used by `enqueue_for_*`)

`services/notification_service.py`'s legacy helpers — `enqueue_for_new_booking`, `enqueue_for_reschedule`, `enqueue_for_cancellation` — write directly to `notification_jobs.kind` using snake_case names:

- `booking_confirmation`
- `internal_new_booking`
- `enrichment_invitation`
- `reminder`
- `reschedule_confirmation`
- `cancellation_confirmation`

These are **template identifiers**, not event-bus kinds. They never go through `record_event`; the legacy helpers own delivery end-to-end. Keep them OUT of `TIMING_MODE` and `STAFF_EMAIL_RENDERERS`.

The dotted variants (`booking.confirmation`, `booking.reminder`, `booking.reschedule_confirmation`, `booking.cancellation_confirmation`, `booking.enrichment_invitation`, `booking.thank_you`, `booking.no_show_followup`, `admin.new_booking`) are FORWARD-LOOKING entries in `TIMING_MODE`. They document intent for the eventual B2 migration off the legacy helpers but currently have no producer and no renderer registered. Do not call `record_event(kind="booking.confirmation", ...)` until both are wired — the dispatcher will compute recipients, find no renderer, log a warning, and silently drop the email.

### 3. Customer portal kinds (used by `services/portal_email.py`)

Customer-facing kinds (`quote.sent`, `invoice.sent`, `invoice.reminder`, `payment.receipt`) dispatch directly via `services/portal_email.py`. They are intentionally NOT on the staff event bus because the recipient is the customer, not a staff role. They appear in the catalog table but not in `TIMING_MODE`, `INTRINSIC_TARGETING`, or `STAFF_EMAIL_RENDERERS`.

### Boundary invariants (enforced by smoke)

[`tests/test_notification_kind_naming_boundary_smoke.py`](../tests/test_notification_kind_naming_boundary_smoke.py) asserts:

1. Every kind in `INTRINSIC_TARGETING` exists in `TIMING_MODE`.
2. Every kind in `STAFF_EMAIL_RENDERERS` exists in `TIMING_MODE`.
3. Every `real_time` (or `real_time_and_digest`) kind that has either an intrinsic recipient OR a role-default subscriber has a renderer registered in `STAFF_EMAIL_RENDERERS`. Forward-looking exception: `admin.new_booking` (legacy uses `internal_new_booking`).
4. None of the snake_case legacy enqueue keys collide with `TIMING_MODE`.

If the smoke fails, you've either added a kind to one registry without the others, or you've collided the legacy and event-bus namespaces. Fix the registry membership rather than expanding the test allowlist — the allowlist exists to document an existing technical-debt state, not to absorb new drift.

---

## Approved decisions

1. **Catalog scope:** the 39 below; no waitlists, no quote-viewed, no late-clock-in alerts, no data-export-ready, no promotional. #39 (`quote.approved_in_store`) was added 2026-05-18 to give the lead/event owner visibility into staff-witnessed approvals via the admin daily digest. Any future additions append in the same numbered way and document the reason here.
2. **Template package layout:** `services/email_templates/` package, one file per domain (`booking.py`, `staff_auth.py`, `staff_schedule.py`, `staff_attendance.py`, `staff_time_off.py`, `staff_bookings.py`, `staff_financial.py`, `digests.py`, `portal_customer.py`). `services/notification_templates.py` becomes a shim re-export to keep existing imports working.
3. **Subject prefix:** `[TEST -> original@example.com]` exactly.
4. **Fixture realism:** synthesized, never DB-backed. Test runs must succeed against an empty DB.

## Standing reminders

- **All Day vs Partial Day** (catalog #21–24): the current `services/notification_templates.py:_format_time_off_window_text` formats dates only. Real time-off requests have a start/end timestamp pair; partial-day requests need to show `Wed May 28, 12:00 PM – 4:00 PM` instead of just `Wed May 28`. Honor the All Day vs Partial Day distinction we established earlier when those four templates ship.
- **No em dashes in customer-facing copy** (per [memory](../.claude/projects/-home-luis-bellas-xv/memory/feedback_copy_voice.md)). Applies to every kind whose audience is "Customer." Staff-internal copy is fine with em dashes.
- **Commit + push at the end of each row** (per [memory](../.claude/projects/-home-luis-bellas-xv/memory/project_commit_push_phase_slices.md)) — once Luis confirms the rendered email looks right in his inbox.

---

## Catalog (38 kinds)

Legend: ☐ not started · 🔧 in progress · ✅ shipped (renders + sends via test script) · 🔌 wired (fires on real trigger to real recipient)

A template is "shipped" when:
1. Renderer exists and returns a `RenderedEmail` with subject/text/html.
2. Test fixture exists in `scripts/send_test_emails.py`.
3. Luis has read the rendered version in his inbox and approved the copy.

A template is "wired" when it additionally fires automatically on the real trigger described in the Trigger column, against the real recipient described in the Audience column. Wiring is a separate later pass; the staff-facing wiring follows the architecture in [STAFF_NOTIFICATIONS_MAP.md](STAFF_NOTIFICATIONS_MAP.md).

**Wired-via paths.** A 🔌 row may reach the user through one of three pathways. The Status cell names which one applies; treat them as architectural variants of "the kind fires on its real trigger," not different completion levels.

- **via `record_event`** (target architecture): the originating service calls `notification_routing.record_event(kind=..., ...)`. This writes a `staff_notification_events` row first, fans out to `notification_jobs` for `real_time` kinds, and lets the daily/weekly digest workers summarize `digest` kinds. Per-user preferences and role defaults from [STAFF_NOTIFICATIONS_MAP.md §6](STAFF_NOTIFICATIONS_MAP.md#6-the-dispatcher) apply. Rows currently on this path: #12-#14, #18-#20, #15 (mixed with legacy), #39.
- **via legacy direct send** (`services/email_transport.send_rendered_safely` called inline from the originating service): the user gets the email but the staff event log, digest summaries, and preference overrides never see it. This is the pre-existing pattern; migration to `record_event` happens kind-by-kind. Rows currently on this path: #17, #21-#28, #31, #32, #33, #34.
- **via `portal_email`** (customer-facing dispatch): rows #8-#10 and #11. Intentionally bypasses the staff event bus because the recipient is the customer, not a staff role.

A kind being "wired via legacy direct send" is not a bug — it predates the event bus. It is a migration candidate when the staff event log or per-user preferences become useful for that kind.

### Customer — booking

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 1 | `booking.confirmation` | Customer | Books appointment | `services/email_templates/booking.py` (exists in `notification_templates.py`) | ✅ |
| 2 | `booking.reminder` | Customer | 24h before slot (cron) | `services/email_templates/booking.py` | 🔧 |
| 3 | `booking.enrichment_invitation` | Customer | Minutes after booking | `services/email_templates/booking.py` | 🔧 |
| 4 | `booking.reschedule_confirmation` | Customer | Reschedules | `services/email_templates/booking.py` | 🔧 |
| 5 | `booking.cancellation_confirmation` | Customer | Cancels | `services/email_templates/booking.py` | 🔧 |
| 6 | `booking.thank_you` | Customer | Day after appointment ran | `services/email_templates/booking.py` | ✅ |
| 7 | `booking.no_show_followup` | Customer | When appointment marked no-show | `services/email_templates/booking.py` | ✅ |

### Customer — portal (financial)

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 8 | `quote.sent` | Customer | Admin sends a quote | `services/email_templates/portal_customer.py` (exists in `portal_email.py`) | 🔌 fires from `services/portal_email.send_quote_invitations` (called by `POST /api/quotes/{id}/send`). Customer-facing path; intentionally bypasses the staff event bus. |
| 9 | `invoice.sent` | Customer | Admin sends an invoice | `services/email_templates/portal_customer.py` (exists in `portal_email.py`) | 🔌 fires from `services/portal_email.send_invoice_invitations`. Customer-facing; bypasses staff event bus. |
| 10 | `invoice.reminder` | Customer | Reminder cron | `services/email_templates/portal_customer.py` (exists in `portal_email.py`) | 🔌 fires from `services/portal_email.send_invoice_reminder` (called from `services/reminder_runner.py`). Customer-facing; bypasses staff event bus. |
| 11 | `payment.receipt` | Customer | Payment recorded on invoice | `services/email_templates/portal_customer.py` | ✅ |

### Staff — bookings on their calendar

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 12 | `staff.booking_assigned` | Assigned stylist | Booking created/assigned to their column | `services/email_templates/staff_bookings.py` | 🔌 (first production `record_event` call site — `sales_assignment.reassign_appointment`, lead cascade, and `walk_in_service` with `assigned_user_id`) |
| 13 | `staff.booking_rescheduled` | Affected stylists | Slot moved on/off their column | `services/email_templates/staff_bookings.py` | 🔌 (fires from `api/routers/booking.post_reschedule` via `notify_booking_rescheduled`; customer reschedule now carries `assigned_user_id` forward onto the new row so the same stylist keeps the booking) |
| 14 | `staff.booking_cancelled` | Assigned stylist | Slot on their column cancelled | `services/email_templates/staff_bookings.py` | 🔌 (paired with #12 via `services/staff_booking_notifications.py`; fires on reassign-loss, unassign, lead-cascade loss, and customer/admin/sales cancellation) |
| 15 | `admin.walk_in_lead_created` | Admins | Walk-in / phone lead captured | `services/email_templates/staff_bookings.py` | 🔌 fires from `services/walk_in_service.create_walk_in_lead` via both legacy direct admin email AND `notification_routing.record_event` (timing=`direct` per the routing decision). The event-log row feeds the admin daily digest summary alongside the immediate email. |
| 16 | `admin.new_booking` | Admins | Any new booking | `services/email_templates/staff_bookings.py` (exists as `internal_new_booking` in `notification_templates.py`) | 🔧 |

### Staff — schedule

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 17 | `staff.schedule_published` | Staff with shifts that week | Admin publishes week | `services/email_templates/staff_schedule.py` | 🔌 fires from `services/staff_schedule._send_schedule_published_emails` via legacy direct send (one email per affected staffer in the bulk `publish_week` flow). Not yet on `record_event`; migration deferred until the event log is needed for digest summary of "what got published." |
| 18 | `staff.shift_edited` | Affected staffer | Shift updated outside publish | `services/email_templates/staff_schedule.py` | 🔌 fires from `staff_schedule.update_published_entry` via `services/staff_schedule_notifications.notify_shift_edited`. Route: `PATCH /api/admin/schedule/entries/{id}/published`. Payload carries `old_shift` + `new_shift` snapshots so the renderer can render before/after without a second DB lookup. The draft `update_entry` path stays silent — staff have no prior notification to update. |
| 19 | `staff.shift_deleted` | Affected staffer | Shift removed | `services/email_templates/staff_schedule.py` | 🔌 fires from `staff_schedule.retract_published_entry` via `services/staff_schedule_notifications.notify_shift_deleted`. Route: `POST /api/admin/schedule/entries/{id}/retract`. Modelled as retract-to-draft (row survives as `status='draft'` audit) rather than hard delete. Payload carries the published-shift snapshot. |
| 20 | `staff.shift_added` | Affected staffer | Shift added outside publish | `services/email_templates/staff_schedule.py` | 🔌 fires from `staff_schedule._send_shift_added_event` via `services/staff_schedule_notifications.notify_shift_added`. Triggers: `create_entry(publish=True)` (grid's "create AND publish immediately") and `publish_entry` (per-cell publish). Bulk `publish_week` continues to use `staff.schedule_published` (#17) so a manager publishing 20 shifts doesn't generate 20 emails. |

### Staff — time off

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 21 | `admin.time_off_requested` | Admins | Staffer submits request | `services/email_templates/staff_time_off.py` | 🔌 fires from `services/time_off.py` (request-creation path) via legacy direct send. Not yet on `record_event`. |
| 22 | `staff.time_off_approved` | Requester | Admin approves | `services/email_templates/staff_time_off.py` | 🔌 fires from `services/time_off.py` (approve path) via legacy direct send. Not yet on `record_event`. |
| 23 | `staff.time_off_denied` | Requester | Admin denies | `services/email_templates/staff_time_off.py` | 🔌 fires from `services/time_off.py` (deny path) via legacy direct send. Not yet on `record_event`. |
| 24 | `staff.time_off_amended` | Requester | Admin amends after approval | `services/email_templates/staff_time_off.py` | 🔌 fires from `services/time_off.py` (amend path) via legacy direct send. Not yet on `record_event`. |

### Staff — attendance

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 25 | `staff.missing_clock_out` | Staffer | Cron flags missed punch | `services/email_templates/staff_attendance.py` | 🔌 fires from `services/staff_schedule._send_missing_clock_out_emails` (cron-driven) via legacy direct send. Not yet on `record_event`. |
| 26 | `admin.missing_clock_out` | Admins | Cron flags missed punch | `services/email_templates/staff_attendance.py` | 🔌 fires from `services/staff_schedule._send_missing_clock_out_emails` via legacy direct send. Not yet on `record_event`. |

### Staff — account / auth

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 27 | `admin.password_reset_request` | Admin/staff user | Forgot-password flow | `services/email_templates/staff_auth.py` (exists in `password_reset.py`) | 🔌 fires from `services/password_reset.py` via legacy direct send. Not yet on `record_event`. |
| 28 | `admin.password_changed` | Admin/staff user | Password reset completed | `services/email_templates/staff_auth.py` | 🔌 fires from `services/password_reset.py` via legacy direct send. Not yet on `record_event`. |
| 29 | `staff.welcome_new_user` | New staff user | Admin creates the account | `services/email_templates/staff_auth.py` | ✅ renderer ready; no production trigger wired yet (admin staff-creation flow doesn't fire this). |
| 30 | `staff.pin_reset` | Staff user | Admin resets their PIN | `services/email_templates/staff_auth.py` | ✅ renderer ready; no production trigger wired yet (admin PIN-reset flow doesn't fire this). |
| 31 | `staff.account_locked` | Staff user | Failed-PIN lockout triggers | `services/email_templates/staff_auth.py` | 🔌 fires from `services/sales_auth._send_account_locked_email` via legacy direct send. Not yet on `record_event`. |
| 32 | `staff.role_changed` | Staff user | Admin changes role/permissions | `services/email_templates/staff_auth.py` | 🔌 fires from `api/routers/admin_sales_staff._send_role_changed_email_safe` via legacy direct send. Not yet on `record_event`. |

### Staff — financial activity

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 33 | `staff.quote_signed` | Owner of the quote | Customer signs in portal | `services/email_templates/staff_financial.py` | 🔌 fires from `services/portal_service._send_quote_signed_email` via legacy direct send when the customer-portal sign flow lands. Not yet on `record_event`. In-store sign uses #39 instead, by design. |
| 34 | `staff.payment_received` | Owner of the invoice | Payment recorded | `services/email_templates/staff_financial.py` | 🔌 fires from `services/payment_service._send_payment_received_emails` via legacy direct send. Not yet on `record_event`. |
| 39 | `quote.approved_in_store` | Admins (daily digest); event owner (future) | Staff witnesses customer signing in-store | `services/staff_digest_runner._in_store_approvals_since` + section in `notification_templates.render_admin_daily_digest` | 🔌 emitted via `record_event` from `services/quote_service.approve_in_store`; timing=`digest` so the row lands in `staff_notification_events` and surfaces in the next admin daily digest run. Intrinsic targeting (`_owner_of_event`) is set for the lead owner; the daily-digest summary is the user-facing surface today, lead-owner-specific delivery waits on a "staff daily reads event log" extension. |

### Digests

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 35 | `digest.staff_daily` | Each scheduled staffer | Daily ~02:30 local (cron tick; B2.3 deferred 06:00 spec) | `services/email_templates/digests.py` + wired in `services/staff_digest_runner.py` | 🔌 |
| 36 | `digest.staff_weekly` | Staff with upcoming shifts | Sunday ~02:30 local (cron tick; B2.3 deferred Sun-18:00 spec) | `services/email_templates/digests.py` + wired in `services/staff_digest_runner.py` | 🔌 |
| 37 | `digest.admin_daily` | Admins | Daily ~02:30 local (cron tick; B2.3 deferred 06:00 spec) | `services/email_templates/digests.py` + wired in `services/staff_digest_runner.py` | 🔌 |

### On-demand

| # | Kind | Audience | Trigger | File | Status |
|---|---|---|---|---|---|
| 38 | `manual.resend_schedule` | All staff with shifts that week | Admin POSTs `/api/admin/schedule/weeks/{monday}/resend-published` | (reuses #17 renderer; wired via `staff_schedule.resend_published_week`) | 🔌 |

---

## Build order

After Slice 0 (DEV redirect + test script) verifies with item #1:

1. **#1** `booking.confirmation` — verify the redirect path works end-to-end (renderer already exists; this is purely the test-mechanism smoke).
2. **#30** `staff.pin_reset`
3. **#29** `staff.welcome_new_user`
4. **#27** `admin.password_reset_request` — verify under the new mechanism (renderer exists today).
5. **#28** `admin.password_changed`
6. **#31** `staff.account_locked`
7. **#32** `staff.role_changed`
8. Remaining customer-facing: **#6**, **#7**, **#11**
9. Staff schedule: **#17** → **#20**
10. Time-off wiring + All Day/Partial Day rewrite: **#21** → **#24**
11. Attendance: **#25**, **#26**
12. Staff booking assignment: **#12** → **#15** (then verify #16 exists under new file layout)
13. Staff financial: **#33**, **#34**
14. Customer portal verification: **#8**, **#9**, **#10** (existing — verify under new layout)
15. Digests: **#35**, **#36**, **#37** (largest single piece — fixture complexity)
16. On-demand: **#38**

---

## Wiring pass (after every template is shipped)

Once every row above is ✅ "shipped" (rendered, in inbox, copy approved), the second pass:

- Build the `staff_notification_events` table + `notification_preferences` per [STAFF_NOTIFICATIONS_MAP.md §5](STAFF_NOTIFICATIONS_MAP.md#5-schema).
- Build `services/notification_routing.py` per [§6](STAFF_NOTIFICATIONS_MAP.md#6-the-dispatcher).
- Hook each event surface to `record_event(...)` per the Trigger column.
- Unset `EMAIL_DEV_REDIRECT` on the VPS to flip to real delivery.

Until then, every send goes to Luis's inbox.
