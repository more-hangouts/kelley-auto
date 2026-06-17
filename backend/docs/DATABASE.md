# Database

PostgreSQL schema, migrations, and conventions.

## Migration runner

[database/migrations/runner.py](../database/migrations/runner.py) is a tiny,
forward-only runner. It:

1. Creates `schema_migrations` if missing
2. Discovers files matching `^(\d{3})_[a-z0-9_]+\.py$`
3. Runs any not yet recorded, in numeric order
4. Records each successful migration in `schema_migrations`

Invoke with:

```bash
venv/bin/python -m database.migrations.runner
```

Each migration file exports `upgrade(connection)`. No downgrade — migrations
go forward. To "undo," write a new migration that compensates.

## Naming + conventions

- Files: `NNN_create_<table>.py`, `NNN_add_<column>_to_<table>.py`,
  `NNN_remove_<thing>.py`. Three-digit number, lowercase, underscores.
- One DDL or backfill concern per file. Don't mix table creation with
  unrelated alters.
- Constraints get explicit names (`chk_events_status`, `uq_contacts_phone_e164`)
  so error messages are readable.
- Indexes named `idx_<table>_<columns>` for filter/sort indexes,
  `uq_<table>_<columns>` for unique. Partial indexes use `WHERE` to make the
  intent clear.

## Schema overview

### Identity + auth

| Table | Purpose |
|---|---|
| `users` | Internal staff (admin, sales). JWT-authenticated. |
| `password_reset_tokens` | Hashed reset links with expiry. |
| `integration_tokens` | OAuth tokens for upstream integrations (e.g., Meta CAPI). |

### Booking widget + appointments

| Table | Purpose |
|---|---|
| `appointments` | One row per booking submission. Holds attribution, device, behavior, `contact_id`, `crm_event_id`. |
| `appointment_availability_rules` | Per-weekday time windows + capacity. |
| `appointment_blackouts` | Date/time ranges that override rules (holidays, owner unavailable). |
| `appointment_visitors` | Anonymous visitor identity (UUID), first/last seen, attribution. |
| `appointment_session_events` | Widget telemetry: step completion, abandons, errors. |
| `appointment_enrichment_responses` | Post-booking survey (style/theme/court_size/photos). |
| `booking_widget_theme_settings` | Single-row store for widget theme + copy + flow rules. |
| `webhook_events` | Audit log for inbound webhooks (Meta CAPI, etc.). |
| `notification_jobs` | Pending/in-flight email/SMS sends. |

### CRM events

| Table | Purpose |
|---|---|
| `contacts` | Root identity. Phone-first dedup. |
| `events` | One per quinceañera (or future weddings/proms). FK to `contacts`. |
| `event_participants` | Celebrant + court members. Unique-active-quinceañera-per-event index. |
| `event_status_change_events` | Audit of every status transition. |

## Key indexes

These exist for a reason — don't remove without checking the query that needs
them.

| Index | Purpose |
|---|---|
| `uq_contacts_phone_e164` (partial, where not null) | Phone identity dedup |
| `idx_contacts_email_lower` (functional, partial) | Email fallback identity |
| `idx_appointments_slot_start_at` | Availability calculation + admin date range |
| `idx_appointments_status_slot` | Filter live bookings without scanning cancelled |
| `idx_appointments_contact_id` | "All appointments for this contact" |
| `idx_appointments_crm_event_id` (partial) | "All appointments for this event" |
| `idx_events_status` | Kanban column queries |
| `idx_events_status_changed_at` (DESC) | Card ordering within columns + at-risk reports |
| `idx_events_event_date` | Wedding-countdown / calendar views |
| `uq_event_participants_quinceanera_per_event` (partial) | Invariant: one active quince per event |
| `idx_event_status_changes_event` | Audit log lookup by event |

## Enum-like CHECK constraints

Plain `VARCHAR` columns with `CHECK (status IN (...))` are the pattern, not
Postgres ENUMs. Reasons:

- Adding a new value is a migration, not a `ALTER TYPE` dance.
- Removing a value is a rewrite of the constraint, no GRANT/REVOKE concerns.
- Mirrored in Python lookup tables ([services/event_workflow.py](../services/event_workflow.py)
  for events; inline tuples for appointment status). Keep both in sync.

## Pervasive denormalization (light)

JN-style `_name` companion columns are NOT used here. Instead, the API joins
on read where needed (board endpoint joins `events + contacts + users`) and
returns nested objects (`primary_contact: { id, display_name }`). Cleaner for
clients; fine at this data volume.

## Where data is denormalized

Two intentional denormalizations:

1. **Enrichment fields onto events on promotion.** When an appointment is
   promoted, `court_size`, `quince_theme`, `quince_theme_colors`,
   `budget_range` are copied from `appointment_enrichment_responses` to the
   new `events` row. The appointment row is the booking-time snapshot; the
   event row is the working draft staff edits going forward.
2. **`status_changed_at` on `events`.** Could be derived from the audit log
   max changed_at, but having it on the row keeps card-sort and at-risk
   queries fast.

## Backfill pattern

Migration 014 demonstrates the pattern for a column that needs to be
populated for existing rows:

1. `ADD COLUMN ... NULL` (no default — defaults force a table rewrite).
2. Multi-pass `INSERT ... SELECT DISTINCT ON ...` to dedupe identity.
3. Multi-pass `UPDATE ... FROM ...` to link existing rows.
4. `RAISE WARNING` (not error) if any orphans remain — manual reconciliation.
5. Leave the column nullable for now; tighten in a later migration once the
   write-path code populates it on insert.

## What you should NOT do

- Do not run migrations from app code at startup. The runner is explicit.
- Do not write down-migrations. Compensating migrations are clearer.
- Do not use SQLAlchemy ORM auto-create (`Base.metadata.create_all`) in any
  environment. Migrations are the source of truth.
- Do not add a `tenant_id`. We are one shop.
