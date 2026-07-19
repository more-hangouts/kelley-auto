# Architecture

High-level map of the Bellas XV platform — what runs where, how requests flow,
and which boundaries to respect when adding code.

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | FastAPI 0.115 + SQLAlchemy 2.0 + psycopg2 | Single uvicorn service (2 workers) |
| Database | PostgreSQL | Single instance, no read replicas |
| Auth | JWT (HS256) via `python-jose` | Bearer tokens, stored client-side |
| Public widget | Vanilla JS in `widgets/` | Loaded directly by marketing site |
| Admin SPA | React 19 + MUI 6 + Vite | Built static, served by nginx |
| Reverse proxy | Nginx | TLS via Certbot, three vhosts |
| Process supervisor | systemd | `bellas-xv-api.service` |
| Background work | asyncio tasks in API process | Notification worker + daily reminder/expiry pass |

## Surfaces

```
shopbellasxv.com           marketing site + public booking widget (static)
admin.shopbellasxv.com     admin SPA (built React, served by nginx)
api.shopbellasxv.com       FastAPI proxied from 127.0.0.1:8000
```

## Request flow

```
Customer books a quince consult:
   widgets/booking-widget.js
   -> POST api.shopbellasxv.com/api/booking/appointments
   -> api/routers/booking.py
   -> services/booking_service.py (slot validation, dedup)
   -> services/contact_service.py (find-or-create contact)
   -> INSERT appointments + appointment_enrichment_responses
   -> services/notification_service.py enqueues confirmation
   -> notification_jobs row picked up by workers/notifications

Staff promotes a lead to a CRM event:
   admin.shopbellasxv.com /appointments
   -> click row -> drawer -> "Promote to Event"
   -> POST api.shopbellasxv.com/api/events { from_appointment_id }
   -> api/routers/events.py
   -> services/event_service.promote_appointment_to_event()
   -> INSERT events + event_participants + event_status_change_events
   -> appointments.crm_event_id set
   -> drawer refreshes into the linked state

Staff drags a card on the kanban:
   admin.shopbellasxv.com /pipeline
   -> @dnd-kit drop handler
   -> @tanstack/react-query optimistic mutation
   -> PATCH api.shopbellasxv.com/api/events/{id}/status
   -> services/event_service.change_event_status()
   -> UPDATE events.status + INSERT event_status_change_events
```

## Repository layout

```
api/
  server.py               FastAPI app, middleware, lifespan, route mounting
  routers/
    auth.py               login, /me, password reset
    booking.py            public widget endpoints (no auth)
    admin_booking.py      admin appointments (list, detail, patch)
    admin_booking_settings.py
                          theme/copy/flow + availability rules + blackouts
    events.py             CRM events: board, detail, status, promote, workflow

services/                 domain logic (no FastAPI imports)
  booking_service.py      slot algorithm, normalization, code generation
  booking_contracts.py    Pydantic shapes shared with the widget
  booking_tokens.py       signed reschedule/cancel/enrichment tokens
  contact_service.py      find-or-create contact with phone-first identity
  event_service.py        promote, change_status, get_board_data, walk-in
  event_workflow.py       status definitions per event type
  notification_service.py email/SMS enqueue + delivery
  notification_templates.py
                          subject/body templates
  email_transport.py      SMTP send
  sms_transport.py        Twilio send

database/
  connection.py           SQLAlchemy engine + session factory
  models.py               every table as a SQLAlchemy declarative class
  auth.py                 password hashing, get_current_user dep
  migrations/
    runner.py             discover-and-apply migrations idempotently
    NNN_*.py              numbered forward-only migrations

frontend/
  src/
    pages/                top-level routes
    components/           shared UI building blocks
    contexts/             React context (auth)
    services/api.js       single axios client + named API functions
    theme.js              MUI theme override
  widgets/                ❌ not here — public widget lives at repo root

widgets/                  public booking widget assets (vanilla JS)
marketing/                static marketing pages
workers/                  background loops (notifications)
tests/                    smoke tests, run as scripts not pytest
config/settings.py        env-driven config + validate_config()
```

## Boundaries

- **Routers contain no business logic.** Services do the work; routers
  translate HTTP <-> service calls.
- **Services do not import FastAPI.** A service is testable in isolation.
- **Models do not contain methods.** Pure declarative; behavior lives in
  services.
- **Frontend hits one base URL.** `VITE_API_URL` points at api.shopbellasxv.com
  in production; localhost in dev.

## Where to add things

| Adding... | Goes in... |
|---|---|
| A new HTTP endpoint | `api/routers/<surface>.py` |
| A new domain operation (the verb) | `services/<domain>_service.py` |
| A new table | `database/models.py` + new migration |
| A new background job | `workers/` + cron-style asyncio loop |
| A new admin page | `frontend/src/pages/<Page>.jsx` + route in `App.jsx` |
| A new public widget | `widgets/<widget>.js` |

## Invariants worth knowing

A handful of rules that aren't obvious from a single file but are
load-bearing across the invoicing surface. If you find yourself
"fixing" any of them, read this section first.

### Invoice and quote numbering

- Numbers come from `numbering_state` (id=1) under a `SELECT ... FOR
  UPDATE` row lock. Allocated only at first send. Drafts have no
  number; the CHECK `chk_invoice_number_when_not_draft` keeps that
  invariant at the DB layer.
- Year rollover resets the sequence to 1. Format is
  `INV-YYYY-NNNNNN` and `Q-YYYY-NNNNNN`. The width is fixed; do not
  trim leading zeros for display.
- **Gaps are intentional.** If a staff member voids a draft after the
  number was allocated, the slot stays consumed. Don't write a
  "compact the sequence" job — auditors expect monotonic, sparse
  numbers, not contiguous ones.
- The concurrent-send smoke (`test_invoices_concurrent.py`) proves
  ten parallel sends yield ten distinct numbers. If you change the
  allocation path, run that smoke.

### Customer-portal URLs and keys

- Portal lives at `/portal` (NOT `/api/portal`). The customer never
  sees `/api`. Staff invitation management lives under
  `/api/invoices/{id}/invitations` and `/api/quotes/{id}/invitations`.
- Each invitation row has a `public_key` minted by
  `secrets.token_urlsafe(32)` — high-entropy, URL-safe, unguessable.
  One invitation per (invoice, contact); soft-delete via `deleted_at`,
  hard-revoke via `revoked_at`, TTL via `expires_at`.
- The portal applies all three gates on every read:
  `deleted_at IS NULL AND revoked_at IS NULL AND (expires_at IS NULL
  OR expires_at > NOW())`. A failed gate renders the same generic
  "this link is not available" page (404) so a probe can't tell
  which case it hit.
- Anything that emails a portal link (Phase 11 reminders, the
  "Resend" verb) MUST use the same gate or it will email a dead
  link. See `services/reminder_runner._live_invitation`.

### Activity log vocabulary

- Every emitted `activity_type` must be a member of
  `services/activity_log._KNOWN_TYPES`. The writer logs a warning
  rather than raising on unknown strings (so a typo doesn't crash
  the calling transaction), and the activity smoke
  (`test_activity_log_smoke.py:check_emitted_types_match_known_vocabulary`)
  fails the build if the log table contains anything outside the set.
- Adding a new activity verb: add the constant in `activity_log.py`,
  add it to `_KNOWN_TYPES`, and add a renderer in
  `frontend/src/pages/event/tabs/Activity.jsx`. Without the renderer
  staff sees the raw string in the activity feed.

## Background workers

Two asyncio tasks live in the FastAPI lifespan (`api/server.py`):

- **`workers/notifications`** — drains `notification_jobs` for
  appointment confirmation/reminder/cancel emails and SMS. Started in
  Phase 1 of the booking work, polymorphic shape is appointment-tied.
- **`workers/daily`** — runs `services/reminder_runner.run_daily`
  once per local day (02:30 in `APP_TIMEZONE`). Two passes:
  reminder1/2/3 dispatch with per-installment idempotency stamps,
  then quote expiry (sent → expired for `expires_at < today`).
  Today is computed against `APP_TIMEZONE`, not system tz, so a UTC
  host running near midnight UTC doesn't fire the next business
  day's reminders early.

Both workers are idempotent. Restarting the API mid-day is safe: the
notifications worker reads `notification_jobs.status`, and the daily
worker reads `installment_reminder_state.*_sent_at`.

## Rate limiting

Two distinct limiters with different shapes:

- **Portal — per-IP, 60/min.** `api/routers/portal.py:_rate_limit`.
  Public surface, anti-enumeration. Sliding window keyed by
  `request.client.host`.
- **Staff money-changing routes — per-user, 60/min.**
  `api/rate_limit.py`. Applied to invoice send/resend, invoice PDF
  GET/retry, payment create, refund create. Per-user (not per-IP)
  because admins NAT through one office IP. Buckets are shared
  across the rate-limited routes: a runaway loop on one verb burns
  the budget for every other money-changing verb on the same
  account, which is the desired signal.

Both limiters store state in-process. If the deploy ever scales to
multiple workers, swap the dicts for Redis.

## Backup and retention

| Class | Where | Backup story |
|---|---|---|
| Canonical financial rows | `invoices`, `invoice_line_items`, `invoice_installments`, `invoice_invitations`, `quotes`, `quote_line_items`, `quote_invitations`, `payments`, `payment_allocations`, `refund_events`, `activity_log`, `business_profile`, `numbering_state`, `installment_reminder_state` | Postgres backup. Retain indefinitely. |
| Cache/regeneratable | Generated PDFs under `DOCUMENT_STORAGE_ROOT/{invoices,quotes,receipts}` | Re-render on demand. Prune only under disk pressure. |
| User-uploaded, not regeneratable | `event_documents` files, business logo file under `DOCUMENT_STORAGE_ROOT/business/logo.<ext>` | Lives on `/var/lib/bellas-xv/uploads`. Object storage is a v2 question. |

The `invoice_invitations` and `quote_invitations` `public_key` values
are part of the financial backup set — restoring without them would
break every customer bookmark.

## Catalog privacy boundary

Catalog rows carry two identities: staff-facing vendor identifiers and
customer-facing Bellas public codes. Customer portals, PDFs, emails,
SMS, signed-link JSON, and any future payment-provider payload must use
the public render DTOs from `services.catalog_service`, not raw ORM
rows. Public render DTOs are allowlisted and tested against forbidden
keys such as internal SKU, designer/style metadata, staff notes,
private notes, product keys, payment references, and terminal-state
reasons.

Customer-facing free text is also guarded on write. Invoice and quote
public notes, terms, footers, line public descriptions, rejection
reasons, and cancellation reasons are rejected if they contain any
known catalog identifier. Staff-only details belong in private/internal
fields. `catalog_items.public_code` is immutable: service code refuses
to patch it, and migration 044 adds a database trigger so raw SQL cannot
quietly rewrite a code already issued to a customer.

## Pending maintenance

- **Drop legacy `event_documents.invoice_*` columns.** Phase 4b
  swapped reads to `linked_invoice_id` and the upload route has been
  rejecting `kind='invoice'` since then. One season after Phase 4b,
  ship a migration that drops the four legacy columns and removes
  `invoice` from `chk_event_documents_kind`. Pre-flight in the
  migration: assert `COUNT(*) WHERE kind='invoice' = 0` and that
  every former legacy row has `kind='external_invoice'` plus a
  non-null `linked_invoice_id`. The legacy columns may still hold
  rollback data; the migration intentionally discards it. Don't
  apply this migration yet — it's tracked as a future task, not
  pending work.
- `external_invoice` stays as a permanent kind for vendor PDFs and
  third-party bills.

## Things this architecture is NOT designed for

- Multi-tenancy. One shop, one DB. Don't add `tenant_id` columns.
- High write throughput. Single uvicorn process, 2 workers. Fine for one shop.
- Realtime push. No WebSockets. The kanban refetches; that's enough at this
  data volume.
