# Staff Notifications Architecture Map

A forward-engineered design for automated emails (and, later, SMS) sent **to staff** in response to admin-app events: new bookings, daily appointment digests, posted schedules, shift edits, time-off decisions, missed clock-outs, and similar.

> **Status:** design only. No code in this doc has been written. Treat the schema and routes as a target to build toward in phased slices ([§9](#9-build-order)), not as something already in the repo.
>
> The customer-facing notification path (`booking_confirmation`, `reschedule_confirmation`, `cancellation_confirmation`, `enrichment_invitation`, `reminder`) already exists in [services/notification_service.py](../services/notification_service.py) and runs through [workers/notifications.py](../workers/notifications.py). This document extends that infrastructure for the staff-facing case — it does **not** replace it.

---

## 1. The Core Mental Model

A notification is the product of five orthogonal things:

```
       ┌─────────────────────────────────────────────────────────┐
       │  EVENT  ×  RECIPIENT SET  ×  CHANNEL  ×  TEMPLATE       │
       │             │                                            │
       │             └─► TIMING (real-time | daily | weekly       │
       │                          | on-demand)                    │
       └─────────────────────────────────────────────────────────┘
```

- **Event** — a thing that happened (`booking.created`, `schedule.published`, `time_off.approved`). Stable string key.
- **Recipient set** — the staff users who should hear about it. Derived from `(role defaults) ⊕ (per-user overrides) ⊕ (event-intrinsic targeting)`. The third term is the one people forget: a "your shift was edited" event has a single intrinsic recipient (the affected staffer), independent of preferences.
- **Channel** — email today; SMS later (the transports already exist as [`SmtpEmailTransport`](../services/email_transport.py) + [`NoopSmsTransport`](../services/sms_transport.py)).
- **Template** — `(event_kind, channel)` → renderer that returns `RenderedEmail(subject, text, html)` or `SmsMessagePayload`.
- **Timing** — when the email actually goes out. Real-time events enqueue immediately. Digests accumulate events between fires and render one composite email.

**The unifying primitive is the event.** Today every customer email is enqueued inline at the call site (`enqueue_for_new_booking` is called right after `booking_service.create_appointment`). That works for one-recipient flows. For staff fan-out (1 event → N recipients × M timing patterns) the call site shouldn't know who's subscribed, only that something happened. So we split:

```
  CALL SITE                EVENT LOG                 DISPATCHER
  ─────────                ─────────                 ──────────
  record_event(            staff_notification        ┌─ real-time fan-out
    kind="schedule.       _events                    │   → notification_jobs (one per recipient)
    published",                  │                   │
    subject_kind="week",         │  ─────────────►   ├─ digest accumulator (no-op at write
    subject_id=44,               │                   │   time; daily/weekly worker reads
    payload={...})               │                   │   the log and renders the batch)
                                                     │
                                                     └─ on-demand admin trigger
                                                         (renders + enqueues immediately)
```

The call site does one thing: writes a row to `staff_notification_events`. The dispatcher (run synchronously inside the same transaction for real-time hooks, asynchronously by the daily/weekly worker for digests) decides who gets what and when.

---

## 2. What Already Exists (Don't Rebuild)

Before designing anything new, the inventory the rest of this doc builds on:

| Concern | Where it lives | Status |
|---|---|---|
| SMTP send | [`services/email_transport.py`](../services/email_transport.py) (`SmtpEmailTransport` / `NullEmailTransport` dev fallback) | Keep as-is |
| SMS send | [`services/sms_transport.py`](../services/sms_transport.py) (`NoopSmsTransport`, Twilio stubbed pending 10DLC) | Keep as-is |
| Template renderers | [`services/notification_templates.py`](../services/notification_templates.py) (plain Python, returns `RenderedEmail`) | Extend with staff templates |
| Send queue | `notification_jobs` table ([models.py:926](../database/models.py#L926)) + [`notification_service.dispatch_job`](../services/notification_service.py#L217) | Extend schema (see [§5.1](#51-extending-notification_jobs)) |
| Worker | [`workers/notifications.py`](../workers/notifications.py) — asyncio, 30s poll, `FOR UPDATE SKIP LOCKED` | Reuse unchanged |
| Daily cron | [`workers/daily.py`](../workers/daily.py) — runs at 02:30 local | Add new daily-digest task to its run list |
| Audit logs (per-domain) | `activity_log` (CRM/event-scoped), `staff_punch_audit_events`, `time_off_decision_events` | Read-only sources for digest payloads; **not** the event bus — too narrow |

**The single most important reuse:** `notification_jobs` already has the retry/locking/poll semantics we want. We extend its schema rather than create a parallel table.

---

## 3. Event Taxonomy

Every notification kind is a stable, lowercased, dotted string: `<domain>.<verb>`. New kinds get added here first, then their template + routing.

### 3.1 Booking events

| `kind` | When | Default recipients | Intrinsic targeting |
|---|---|---|---|
| `booking.created` | Customer completes booking widget | Admins + sales role | Stylist on the affected column (if assigned at create time) |
| `booking.rescheduled` | Customer or admin moves an appointment | Admins | Stylist losing the slot + stylist gaining the slot |
| `booking.cancelled` | Customer cancels via link or admin cancels | Admins | Stylist who was assigned |
| `booking.abandoned` | `appointment_session_events` gets a terminal abandon row (see [memory: abandon telemetry](../.claude/projects/-home-luis-bellas-xv/memory/project_abandon_telemetry_storage.md)) | Admins only, **digest only** — too noisy real-time | (none) |

### 3.2 Schedule events

| `kind` | When | Default recipients | Intrinsic targeting |
|---|---|---|---|
| `schedule.published` | Admin clicks Publish on [`admin_schedule.py:206`](../api/routers/admin_schedule.py#L206) | **All staff with a shift in the published week** | (the recipient set IS the targeting — every staffer gets their own personalized rendering) |
| `schedule.shift_edited` | Staff schedule entry created/updated outside the publish flow ([`admin_schedule.py:137`](../api/routers/admin_schedule.py#L137), [:164](../api/routers/admin_schedule.py#L164)) | (none by default) | The affected staffer **only** |
| `schedule.shift_deleted` | Entry removed | (none by default) | The affected staffer |

### 3.3 Time-off events

| `kind` | When | Default recipients | Intrinsic targeting |
|---|---|---|---|
| `time_off.requested` | Sales staff submits ([`sales_time_off.py:60`](../api/routers/sales_time_off.py#L60)) | Admins | (none) |
| `time_off.approved` | Admin approves | (none by default) | The requesting staffer |
| `time_off.denied` | Admin denies | (none by default) | The requesting staffer |
| `time_off.amended` | Admin amends after approval | (none by default) | The requesting staffer |

### 3.4 Attendance events

| `kind` | When | Default recipients | Intrinsic targeting |
|---|---|---|---|
| `attendance.missing_out_punch` | `missing_out_punch_cron` flags a staffer who clocked in but never out | Admins, **digest only** | (the staffer themselves does NOT get auto-emailed; this is an admin signal) |
| `attendance.late_clock_in` | Clock-in arrives >15min after shift start | Admins, **digest only** | (none) |

### 3.5 Digest "events" (synthesized, not recorded)

These don't come from `staff_notification_events` — they're computed at fire time by reading current DB state.

| `kind` | When | Recipients | Content |
|---|---|---|---|
| `digest.daily_appointments` | Each weekday 06:00 local | Every active staff user with a shift today | Today's appointments on their column + the day's total |
| `digest.weekly_schedule` | Each Sunday 18:00 local | Every active staff user with a shift in the upcoming week | Their next-7-day schedule |
| `digest.admin_daily` | Each weekday 06:00 local | Admins | New bookings since yesterday, time-off requests waiting on decision, attendance exceptions, abandoned bookings count |

### 3.6 On-demand kinds

Triggered by an explicit admin click, not by an event:

| `kind` | Trigger | Recipients |
|---|---|---|
| `manual.resend_schedule` | Admin clicks "Resend this week's schedule to all staff" | All staff with shifts that week |
| `manual.broadcast` | Admin uses a free-text "send to all staff" tool (future) | All active staff |

---

## 4. The Recipient Routing Model

Three layers, evaluated in order. Later layers override earlier ones.

```
            ROLE DEFAULTS                           PER-USER OVERRIDES                   EVENT-INTRINSIC
            (table or hardcoded)                    (notification_preferences table)     (computed at dispatch time)
            ─────────────                           ──────────────────────────────       ────────────────────────────
  admin:    [booking.*, schedule.*,                 user 7 (admin) sets                  schedule.shift_edited:
             time_off.requested,                    schedule.shift_edited = on            → always send to the affected
             attendance.*,                                                                  staffer regardless of prefs
             digest.admin_daily,                    user 12 (sales) sets                 booking.created:
             digest.daily_appointments=off]         time_off.approved = off               → always send to the assigned
                                                    (despite role default = on)            stylist regardless of prefs
  sales:    [time_off.approved,
             time_off.denied,
             time_off.amended,
             digest.daily_appointments,
             digest.weekly_schedule]
```

**Important asymmetry:** intrinsic targeting can only *add* recipients, never remove them. A staffer cannot opt out of "your shift was edited" — that would defeat the safety purpose of the notification. The preferences table only governs role-default subscriptions.

In code, the routing function is roughly:

```python
def recipients_for(event: StaffNotificationEvent) -> list[Recipient]:
    intrinsic = INTRINSIC_TARGETING[event.kind](event)   # may be []
    subscribed = users_subscribed_to(event.kind)         # role default + override merge
    return dedupe(intrinsic + subscribed)
```

with `users_subscribed_to` computed by the SQL in [§5.3](#53-querying-the-effective-subscription).

---

## 5. Schema

Three new tables, one extension to an existing table. All migrations go in [`database/migrations/`](../database/migrations/) — next number is **072**.

### 5.1 Extending `notification_jobs`

The existing table is appointment-bound (`appointment_id` is the only foreign key for subject). For staff events the subject is a shift, a published week, a time-off request, or nothing at all. Add a polymorphic subject pair + a recipient FK:

```sql
-- migration 077: staff_notifications_foundation (shipped)

ALTER TABLE notification_jobs
  ADD COLUMN subject_kind TEXT,             -- 'appointment' | 'schedule_week' | 'shift' | 'time_off' | 'digest' | NULL
  ADD COLUMN subject_id   BIGINT,           -- nullable; meaningless without subject_kind
  ADD COLUMN recipient_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- The existing appointment_id column stays. Backfill subject_kind/subject_id
-- for legacy rows in the same migration:
UPDATE notification_jobs
   SET subject_kind = 'appointment',
       subject_id   = appointment_id
 WHERE appointment_id IS NOT NULL
   AND subject_kind IS NULL;

CREATE INDEX ix_notif_jobs_subject ON notification_jobs (subject_kind, subject_id);
CREATE INDEX ix_notif_jobs_recipient ON notification_jobs (recipient_user_id);
```

> Why not a new `staff_notification_jobs` table? Because the worker, retry semantics, `FOR UPDATE SKIP LOCKED` claim loop, and dispatch logic are already correct and battle-tested. Forking the table forks the worker. The cost of widening the existing table is one nullable FK and a polymorphic pair — much smaller than the cost of two parallel queues.

### 5.2 New table — `staff_notification_events` (the event log)

The append-only log of "things that happened that staff might be told about." Real-time hooks write to this AND enqueue jobs in the same transaction. Digest workers read this between digest fires to compose their content.

```sql
CREATE TABLE staff_notification_events (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,                   -- 'booking.created', 'schedule.published', etc.
    subject_kind  TEXT,                            -- mirror of notification_jobs
    subject_id    BIGINT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- who caused it (NULL = system)
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,                 -- snapshot of render-needed fields
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Consumed-by markers for digest workers, so each digest only sees events
    -- it hasn't summarized yet. NULL means "no digest has covered this row."
    daily_digest_consumed_at  TIMESTAMPTZ,
    weekly_digest_consumed_at TIMESTAMPTZ
);

CREATE INDEX ix_sne_kind_occurred  ON staff_notification_events (kind, occurred_at DESC);
CREATE INDEX ix_sne_subject        ON staff_notification_events (subject_kind, subject_id);
CREATE INDEX ix_sne_daily_pending  ON staff_notification_events (occurred_at)
   WHERE daily_digest_consumed_at IS NULL;
CREATE INDEX ix_sne_weekly_pending ON staff_notification_events (occurred_at)
   WHERE weekly_digest_consumed_at IS NULL;
```

**Payload snapshotting (the JN lesson applied):** at write time, the call site copies the render-needed fields into `payload` — staffer display name, shift start/end, customer first name, confirmation code, etc. This way the render-at-send path doesn't need to rejoin against tables whose rows may have moved on. Mirrors the `_name` denormalization pattern from JobNimbus, for the same reason: render-readiness without join overhead.

### 5.3 New table — `notification_preferences` (per-user overrides)

```sql
CREATE TABLE notification_preferences (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_kind TEXT NOT NULL,
    enabled    BOOLEAN NOT NULL,        -- explicit override; NOT NULL = user has touched this row
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, event_kind)
);
```

The **role defaults** can either live in a sister table or hardcoded in `services/notification_routing.py`. Recommendation: hardcoded in Python initially (in a `ROLE_DEFAULTS: dict[str, dict[str, bool]]` literal) — it's a tiny enum that changes only when the engineering team ships new event kinds, not when ops makes preference changes. Promote to a table only if/when admins need to edit role defaults from the UI.

#### Querying the effective subscription

```sql
-- "is user 7 subscribed to schedule.shift_edited?"
SELECT COALESCE(p.enabled, :role_default) AS is_subscribed
  FROM users u
  LEFT JOIN notification_preferences p
    ON p.user_id = u.id
   AND p.event_kind = 'schedule.shift_edited'
 WHERE u.id = 7;
```

`:role_default` is resolved in Python from the `ROLE_DEFAULTS[user.role][event_kind]` lookup before the query runs.

### 5.4 (Defer) Digest send log

`digest.*` jobs go through `notification_jobs` like everything else. The `subject_kind = 'digest'` + a `payload.digest_window = '2026-05-17'` is enough to dedupe "did we already send today's daily digest?" — a unique partial index makes it enforceable:

```sql
CREATE UNIQUE INDEX uq_one_digest_per_user_per_window
  ON notification_jobs (recipient_user_id, kind, (payload ->> 'digest_window'))
  WHERE subject_kind = 'digest' AND status IN ('pending','sent');
```

This prevents a daily worker re-fire (e.g., after a crash + restart on the same day) from double-sending.

---

## 6. The Dispatcher

A single new module: `services/notification_routing.py`. The existing `notification_service.py` keeps owning the queue (enqueue, claim, dispatch); the new module owns the event → jobs mapping.

```python
# services/notification_routing.py

def record_event(
    db: Session,
    *,
    kind: str,
    subject_kind: str | None = None,
    subject_id: int | None = None,
    actor_user_id: int | None = None,
    payload: dict | None = None,
) -> None:
    """Write the event to the log AND fan out real-time jobs in one txn.

    Digest-only events skip the fan-out and rely on the daily/weekly
    worker to read the log later. The TIMING_MODE registry below decides.
    """
    event = StaffNotificationEvent(
        kind=kind,
        subject_kind=subject_kind,
        subject_id=subject_id,
        actor_user_id=actor_user_id,
        payload=payload or {},
    )
    db.add(event)
    db.flush()

    if TIMING_MODE[kind] in ("real_time", "real_time_and_digest"):
        for recipient in recipients_for(db, event):
            notification_service.enqueue_staff_job(
                db,
                kind=kind,
                channel="email",
                recipient_user_id=recipient.user_id,
                recipient_email=recipient.email,
                subject_kind=subject_kind,
                subject_id=subject_id,
                payload=event.payload,
            )

# ─── Registries ─────────────────────────────────────────────────────────

TIMING_MODE: dict[str, str] = {
    "booking.created":            "real_time",
    "schedule.published":         "real_time",
    "schedule.shift_edited":      "real_time",
    "time_off.requested":         "real_time",
    "time_off.approved":          "real_time",
    "booking.abandoned":          "digest",            # noisy → digest only
    "attendance.missing_out_punch": "digest",
    # ...
}

ROLE_DEFAULTS: dict[str, dict[str, bool]] = {
    "admin": { ... },
    "sales": { ... },
}

INTRINSIC_TARGETING: dict[str, Callable[[StaffNotificationEvent], list[Recipient]]] = {
    "schedule.shift_edited": _affected_staffer_of_shift,
    "booking.created":       _assigned_stylist_of_appointment,
    # most kinds: no intrinsic recipient → return []
}
```

The render path stays identical to the existing customer flow: `dispatch_job` looks up `kind` in the renderer registry, calls the renderer, hands the result to `email_transport.send()`.

---

## 7. Worker Integration

### 7.1 Real-time path (no new worker)

`record_event` → `notification_jobs` rows with `due_at = NOW()` → existing [`workers/notifications.py`](../workers/notifications.py) picks them up on its next 30s tick. **Nothing new to run.**

### 7.2 Daily digest task

Add to the [`workers/daily.py`](../workers/daily.py) run list, alongside `reminder_runner.run_daily()` etc.:

```python
# workers/daily.py
await asyncio.to_thread(staff_digest_runner.run_daily)
```

The runner:

1. Picks the digest window: `today` in `APP_TIMEZONE`, formatted as `YYYY-MM-DD`.
2. For `digest.daily_appointments`: loops over every active staff user with a shift today, builds their personal payload (today's appointments on their column from `staff_schedule_entries` + `appointments`), enqueues one `notification_jobs` row with `subject_kind='digest'`, `payload.digest_window='2026-05-17'`. The unique partial index from [§5.4](#54-defer-digest-send-log) prevents duplicates.
3. For `digest.admin_daily`: aggregates new `staff_notification_events` rows since the last admin digest, plus open time-off requests, plus attendance exceptions. Marks consumed rows with `daily_digest_consumed_at = NOW()`.

### 7.3 Weekly digest task

New file `workers/weekly.py` (or fold into `workers/daily.py` and gate by `weekday == Sunday`). Fires once per week at 18:00 local. Same shape as daily, different payload.

### 7.4 On-demand path

Admin clicks "Resend this week's schedule" in the UI → POST to `/api/admin/notifications/send`:

```http
POST /api/admin/notifications/send
{
  "kind": "manual.resend_schedule",
  "subject_kind": "schedule_week",
  "subject_id": 44
}
```

Handler calls `notification_routing.send_on_demand(...)`, which renders + enqueues jobs immediately (no event-log write — on-demand sends are not "events that happened," they're commanded sends).

---

## 8. API Surface

Admin-only endpoints for preferences + on-demand send. All routes live in a new `api/routers/admin_notifications.py`.

```
# Preferences (per user)
GET    /api/admin/notifications/preferences/{user_id}
PUT    /api/admin/notifications/preferences/{user_id}
       { "schedule.shift_edited": true, "time_off.approved": false, ... }

# Effective subscription view (read-only, with role-default merge applied)
GET    /api/admin/notifications/preferences/{user_id}/effective
       → { "schedule.shift_edited": { "enabled": true, "source": "override" },
           "booking.created":       { "enabled": true, "source": "role_default" } }

# Event catalog (so the UI can render preference toggles)
GET    /api/admin/notifications/event-kinds
       → [ { "kind": "schedule.shift_edited",
             "label": "Your shift was edited",
             "category": "schedule",
             "timing": "real_time",
             "intrinsic": false } ]

# On-demand send
POST   /api/admin/notifications/send
       { "kind": "manual.resend_schedule", "subject_kind": "schedule_week", "subject_id": 44 }

# Send log (for the "did this email actually go out?" admin debug view)
GET    /api/admin/notifications/jobs?recipient_user_id=7&kind=schedule.published&limit=50
```

The sales portal needs read/write access to **its own** preferences only:

```
GET    /api/sales/me/notifications/preferences
PUT    /api/sales/me/notifications/preferences
```

---

## 9. Build Order

Ten slices. Each is independently mergeable, smoke-verifiable, and committable per [the phase-slice commit policy](../.claude/projects/-home-luis-bellas-xv/memory/project_commit_push_phase_slices.md).

1. **Migration 077** — extend `notification_jobs` (subject pair + `recipient_user_id`), create `staff_notification_events`, create `notification_preferences`, create the digest dedupe unique index. Validate with real INSERTs per the [schema-validation policy](../.claude/projects/-home-luis-bellas-xv/memory/feedback_validate_schema_with_real_inserts.md). **Shipped in B1.**

2. **Routing module skeleton** — `services/notification_routing.py` with `record_event`, `recipients_for`, the three registries (`TIMING_MODE`, `ROLE_DEFAULTS`, `INTRINSIC_TARGETING`) all populated with the [§3](#3-event-taxonomy) taxonomy but no event kinds wired yet.

3. **Dispatcher extension** — extend `notification_service.dispatch_job` to look up renderers via a registry keyed by `(kind, channel)`. Add `enqueue_staff_job` helper. Keep customer flows working untouched.

4. **First real-time event: `schedule.published`** — hook into [`admin_schedule.py:206`](../api/routers/admin_schedule.py#L206). Add the renderer to `services/notification_templates.py`. End-to-end browser test against admin.shopbellasxv.com (per [no-local-dev-server policy](../.claude/projects/-home-luis-bellas-xv/memory/project_no_local_dev_server.md)).

5. **Preferences API + sales-portal UI** — read + write preferences. Ship the toggle UI before adding more events so opt-out is available from day one.

6. **Real-time hooks for `booking.*` + `time_off.*` + `schedule.shift_edited`** — five events in one slice (they share the same hook pattern, so doing them together is cheaper than splitting).

7. **Daily digest runner** — `digest.daily_appointments` for staff + `digest.admin_daily` for admins. Wire into [`workers/daily.py`](../workers/daily.py).

8. **Weekly digest runner** — `digest.weekly_schedule`. Same shape as daily.

9. **On-demand send endpoint + UI** — admin button on the published-schedule view ("Resend to all staff").

10. **Admin send-log debug view** — `GET /api/admin/notifications/jobs` + a small admin page that lists recent jobs filtered by recipient. Useful first for ops debugging, then as the foundation for a "delivery health" dashboard.

Stop there. Things explicitly **out of scope** for v1:

- **SMS to staff.** Wait for the Twilio 10DLC registration that's already pending. The architecture supports it (channel is a column) but adding a second channel before the first one is stable is premature.
- **Push notifications / web push.** No mobile app exists.
- **In-app notification feed / bell icon.** That's a UI surface, not a delivery channel — additive later.
- **Per-event quiet hours.** Wait for someone to actually complain about 6am emails.
- **Notification grouping by thread (Slack-style).** No.
- **Editable email templates in the admin UI.** Templates stay in code until template-edit cadence justifies the build cost.

---

## 10. Decisions Worth Calling Out

| Decision | Why |
|---|---|
| Extend `notification_jobs`; don't fork a `staff_notification_jobs` table | One queue means one worker, one retry policy, one dispatch path. The polymorphism cost (two nullable columns) is much smaller than parallel infrastructure cost. |
| Snapshot render data into event/job `payload` at write time | Mirrors JN's `_name` denormalization. Decouples render-at-send from source-table mutations (a stylist renames themselves; yesterday's "your shift was edited" email still says the right name). |
| Per-user overrides are explicit-only (no `null` row for "default") | Existence of a row in `notification_preferences` = the user has touched it. Absence = role default applies. Avoids ambiguity between "user defaulted to off" and "user explicitly chose off." |
| Intrinsic targeting cannot be opted out of | "Your shift was edited" is a safety notice, not a preference. The preferences table only governs role-default subscriptions. |
| Role defaults hardcoded in Python, not a table | They change at engineering-deploy cadence, not at ops-edit cadence. Promote to a table only when admins demand UI editing. |
| Digest events recompute from current DB state, not from accumulated rows | A daily appointments digest needs *today's appointments as they exist now*, not an accumulation of every `booking.created` row. Only admin-facing digests (which summarize *activity since last fire*) read the event log. |
| No `tenant_id` anywhere | Per the [white-label model](../.claude/projects/-home-luis-bellas-xv/memory/project_white_label_per_tenant_deployment.md), this is single-tenant per deployment. Don't pre-add multi-tenancy. |
| No `deleted_at` on any new table | Per the [soft-delete policy](../.claude/projects/-home-luis-bellas-xv/memory/feedback_soft_delete_policy.md). Add it when a delete UX exists. |
| Daily worker stays inside the FastAPI process | Same reasoning as `workers/notifications.py` — single-process is fine for v1 traffic, `FOR UPDATE SKIP LOCKED` makes it safe to split out later without code changes. |
| New disk-write paths? **No.** | Everything is DB-only. No files, no caches. If that changes, see [VPS hardening notes](../.claude/projects/-home-luis-bellas-xv/memory/project_vps_hardening_writes.md). |

---

## 11. Open Questions (To Resolve Before Slice 1)

These don't block the design but want explicit answers before code lands:

1. **Default off vs. default on for new staff?** When a new user is created, do they start subscribed to their role's defaults (more useful, more spammy on day 1) or unsubscribed (clean, but every new hire has to discover the preferences page)? Recommendation: subscribed.
2. **What does "all staff with a shift in the published week" mean for `schedule.published`?** Staff whose `staff_schedule_entries` row exists in that week range, or all currently-active staff regardless of whether they're scheduled? Recommendation: only staff with an entry — quieter, and the unscheduled ones don't need it.
3. **Should `booking.created` notify the assigned stylist even if no stylist is assigned at create time?** Today, stylist assignment may happen later via admin edit. Either we fire `booking.assigned` as a separate event, or we only fire `booking.created` once (no stylist) and notify on later assignment via `booking.updated`. Recommendation: add a `booking.stylist_assigned` event in slice 6.
4. **Time-zone handling for "06:00 local" digests.** `APP_TIMEZONE` is shop-wide; staff TZs aren't tracked. For now use shop TZ. Per-staff TZ is a v2+ concern.

---

## Appendix: Glossary

- **Event** — an immutable record in `staff_notification_events`. Written by call sites.
- **Job** — a row in `notification_jobs`. Represents "send this email to this recipient at this time."
- **Kind** — stable string identifier for an event type (`booking.created`).
- **Subject** — the domain entity an event/job is about. Polymorphic via `(subject_kind, subject_id)`.
- **Channel** — `email` | `sms`.
- **Recipient** — a `User` row, resolved to an email/phone at dispatch time.
- **Timing mode** — `real_time` | `digest` | `real_time_and_digest`.
- **Intrinsic targeting** — the recipient set is computed from the event itself, not from preferences (`schedule.shift_edited` → the affected staffer, always).
- **Role default** — the subscription a user gets purely from their `users.role` column, in the absence of a `notification_preferences` row.
- **Override** — a row in `notification_preferences` that takes precedence over the role default.
