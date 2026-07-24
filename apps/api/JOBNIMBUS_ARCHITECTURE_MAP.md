# JobNimbus Architecture Map (Inferred)

A reverse-engineered architectural map of JobNimbus, inferred from heavy hands-on use of its REST API in this codebase ([clients/jobnimbus.py](../clients/jobnimbus.py), [api/routers/webhooks/jobnimbus.py](../api/routers/webhooks/jobnimbus.py)). Use this as the blueprint for a stripped-down, contact-centric CRM you can rebuild for other clients.

> **Disclaimer:** JobNimbus has not published their schema. Everything below is *inferred* from API request/response shapes, error messages, filter semantics, and behavioral quirks observed in production. Field names like `jnid`, `record_type`, and `primary` are real (we send/receive them); the storage engine, table layout, and ID strategy are educated guesses.

---

## 1. The Core Mental Model

JobNimbus is a **contact-first CRM with polymorphic records and a generic activity stream**. Strip it down and there are really only ~6 concepts:

```
                         ┌────────────────┐
                         │    CONTACT     │  the "person/company" — root identity
                         │  (jnid, email, │
                         │   phone, addr) │
                         └────────┬───────┘
                                  │ primary (1:N)
                                  ▼
                         ┌────────────────┐
                         │      JOB       │  the "deal" / project
                         │ (jnid, status, │  N jobs per contact
                         │  workflow,$$$) │
                         └────────┬───────┘
                                  │
            ┌──────────┬──────────┼──────────┬──────────┐
            ▼          ▼          ▼          ▼          ▼
       ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐ ┌─────────┐
       │ FILES  │ │ACTIVITY│ │ESTIMATE│ │WORKORDER│ │MATERIAL │
       │ (PDFs, │ │(notes, │ │  /v2   │ │  /v2    │ │ ORDER   │
       │ photos)│ │ tasks, │ │        │ │         │ │  /v2    │
       │        │ │ status │ │        │ │         │ │         │
       │        │ │ changes│ │        │ │         │ │         │
       └────────┘ └────────┘ └────────┘ └─────────┘ └─────────┘
```

**The unifying primitive is the `related` link** — every "child" record points back at a parent via a `primary` (single owner) and `related[]` (multi-link) relationship object. This is how a single "file upload" endpoint can attach to either a contact OR a job: it doesn't know or care; it just stores `{ related: [{ id, type }] }`.

---

## 2. Identifier Strategy: `jnid`

Every entity carries an opaque string ID called `jnid`. Observed properties:

- **Globally unique across entity types** (contacts, jobs, files, activities all share the same ID space — never collide).
- **Not auto-incrementing** — looks like a hash/UUID-ish opaque token (typically 24-char hex-ish).
- **Returned as `jnid` on the entity itself**, but referenced as `id` inside relationship objects (`{primary: {id, type}}`).
- **Some endpoints duplicate it as `external_id`** when the record was imported from another system (Sales Rabbit, AccuLynx).

**Likely implementation:** A single `entities` table or per-type tables sharing an ID generator (e.g., a sequence with a prefix, or a shortened UUID). The opacity argues against integer PKs.

---

## 3. The Polymorphic Relationship Pattern

This is the heart of JN's flexibility. Look at any non-root record (file, activity, work order) and you'll see:

```json
{
  "jnid": "...",
  "primary": { "id": "<contact-or-job-jnid>", "type": "job" },
  "related": [
    { "id": "<jnid>", "type": "job" },
    { "id": "<jnid>", "type": "contact" }
  ]
}
```

- `primary` — single "owner" (used for default display/grouping).
- `related[]` — additional links. A note on a job might also be related to the contact, an estimate, etc.
- `type` field is the polymorphic discriminator: `"job"`, `"contact"`, `"task"`, etc.

Filter syntax we observed (Elasticsearch-flavored):

```json
{ "must": [ { "term": { "related.id": "<job_jnid>" } } ] }
```

**This is the smoking gun**: JN almost certainly stores its data in **Elasticsearch (or an ES-shaped index)** for read paths, with a relational source-of-truth behind it. The query DSL (`must` / `term` / `range` / `now-30d`) is straight from the ES query language. Most CRMs of this size do exactly this: Postgres for OLTP, ES for filtered list/search/scroll APIs.

### Stripped-down equivalent

For a clone you don't need ES. A single `relationships` join table is enough:

```sql
CREATE TABLE relationships (
  parent_id   TEXT NOT NULL,
  parent_type TEXT NOT NULL,            -- 'contact' | 'job' | ...
  child_id    TEXT NOT NULL,
  child_type  TEXT NOT NULL,            -- 'file' | 'activity' | 'estimate' | ...
  is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (parent_id, child_id, child_type)
);
CREATE INDEX ix_rel_child ON relationships (child_id, child_type);
CREATE INDEX ix_rel_parent ON relationships (parent_id, parent_type);
```

Use Postgres GIN/JSONB if you want ES-style filtering without the ops cost; reach for Meilisearch/Typesense only when you need full-text search.

---

## 4. Inferred Database Schema (per entity)

Field lists below are the **observed surface area** — every field this codebase reads or writes against the JN API.

### 4.1 `contacts`

The root identity. 1:N with jobs.

| Column | Type | Notes |
|---|---|---|
| `jnid` | TEXT PK | Opaque ID |
| `external_id` | TEXT | Optional — links to Sales Rabbit, AccuLynx, etc. |
| `display_name` | TEXT | **Globally unique constraint** — JN throws "duplicate display name" 400s; the codebase has progressive disambiguation (`Name (Lead)`, `Name (Lead-1234)`) |
| `first_name`, `last_name` | TEXT | |
| `email` | TEXT | Indexed; searched via `?email=` |
| `mobile_phone`, `home_phone` | TEXT | |
| `address_line1`, `address_line2`, `city`, `state_text`, `zip` | TEXT | Flat — not normalized |
| `record_type` | INTEGER | FK to a `record_types` lookup |
| `record_type_name` | TEXT | Denormalized (e.g., "Customer", "Lead") |
| `source` | INTEGER (FK) / `source_name` TEXT | Denormalized lookup of acquisition channel |
| `sales_rep` | TEXT (user jnid) | |
| `sales_rep_name` | TEXT | Denormalized |
| `status` | INTEGER | Status ID |
| `status_name` | TEXT | Denormalized |
| `date_created` | INTEGER (unix ts) | JN returns Unix timestamps everywhere |
| `date_updated` | INTEGER | "last modified" — JN uses `date_updated` not `date_modified` |
| `date_status_change` | INTEGER | Critical — only updates on status transition (used for change-stream queries) |
| `tags` | TEXT[] / JSONB | Free-form labels |

**Pattern: pervasive denormalization.** Every FK has a `_name` companion column. This makes list endpoints render-ready without joins (and explains why JN's API responses are so chunky).

### 4.2 `jobs`

The "deal" record. 1:N children of contacts.

| Column | Type | Notes |
|---|---|---|
| `jnid` | TEXT PK | |
| `external_id` | TEXT | |
| `number` | TEXT | Human-readable job number — searchable |
| `display_name` / `name` | TEXT | |
| `description` | TEXT | Free notes |
| `primary_contact_id` / `primary` | TEXT | FK to contacts.jnid (the owner) |
| `record_type` | INTEGER | Workflow type ID (Insurance / Retail / Service / Repairs) |
| `record_type_name` | TEXT | E.g., "Residential - Insurance" |
| `status` | INTEGER (or string) | Current workflow status |
| `status_name` | TEXT | E.g., "Appointment Scheduled", "In Production", "Paid & Closed" |
| `address_line1`, `city`, `state_text`, `zip` | TEXT | **Job has its own address** (jobsite ≠ contact's billing address) |
| `source_name` | TEXT | Acquisition channel — separate from contact's source |
| `sales_rep`, `sales_rep_name` | TEXT | |
| `approved_estimate_total` | NUMERIC | Sum of approved estimates (denormalized) |
| `approved_invoice_total` | NUMERIC | |
| `approved_invoice_due` | NUMERIC | Outstanding balance |
| `date_created`, `date_updated`, `date_status_change` | INT timestamps | |

> ⚠️ **Casing quirk:** JN returns `status_name` with inconsistent capitalization — e.g., `"Submitted For Approval"` vs `"Submitted for Approval"`. Always lowercase both sides when matching ([reference_jn_status_case.md](../.claude/projects/-home-luis-projects-mammoth-analytics/memory/reference_jn_status_case.md)).

### 4.3 `activities` (the kitchen sink)

A **single polymorphic table** for notes, tasks, status changes, and probably emails/SMS too. Discriminated by `record_type_name`.

| Column | Type | Notes |
|---|---|---|
| `jnid` | TEXT PK | |
| `record_type_name` | TEXT | `'Note'` \| `'Task'` \| `'Email'` \| `'StatusChange'` \| ... |
| `note` | TEXT | Body content (used for both notes and tasks) |
| `task_name` | TEXT | Title — only populated for tasks |
| `date_start`, `date_end` | INT | For tasks/appointments |
| `primary` | JSONB `{id, type}` | The job/contact this is "on" |
| `related` | JSONB array | Extra links |
| `created_by`, `created_by_name` | TEXT | User who logged it |
| `assigned_to_name` | TEXT | For tasks |
| `date_created`, `date_updated` | INT | |

**Single endpoint for everything**: `POST /activities` with `record_type_name` discriminator. This is a clean pattern worth copying.

### 4.4 `files`

Attachments — JN supports the same file linked to multiple jobs/contacts via the relationship table.

| Column | Type | Notes |
|---|---|---|
| `jnid` | TEXT PK | |
| `filename` | TEXT | |
| `content_type` | TEXT | |
| `description` | TEXT | |
| `size` | INTEGER | |
| `related` | JSONB array | Polymorphic owners |
| `date_created` | INT | |

**Upload mechanic:** `POST /files` with **JSON + base64** payload — *not* multipart. This simplifies their API gateway but means files get ~33% inflated in transit. Worth questioning for a clone.

**Download mechanic:** `GET /files/{jnid}` returns one of three forms (JN is messy here — handle all three):
1. Raw binary (when content-type isn't JSON)
2. JSON with `data` field (base64-encoded body)
3. JSON with `url` field (signed S3-style redirect)

For a clone: use multipart upload + S3/Spaces for storage; return signed URLs for download. Don't replicate the base64 pattern.

### 4.5 `estimates`, `workorders`, `materialorders` (the v2 family)

These live under `/v2/<entity>` instead of the v1 root, and their filter shape changed (relationship lookups via `{"term": {"related.id": jnid}}`). Strong indicator JN migrated their financial records to a newer service while leaving the rest on legacy v1.

Common shape:

| Column | Type | Notes |
|---|---|---|
| `jnid` | TEXT PK | |
| `number` | TEXT | Doc number |
| `status` / `status_name` | INT / TEXT | Workflow status |
| `record_type_name` | TEXT | Sub-type |
| `primary` | JSONB | Usually points at the job |
| `related` | JSONB[] | |
| `line_items` (or `items`) | JSONB[] | List of `{description, qty, unit_price, total}` |
| `total`, `amount`, `approved_estimate_total` | NUMERIC | **Different field names per entity** — defensive code in [parse_workorder](../clients/jobnimbus.py#L1175-L1261) tries multiple keys |
| `balance`, `amount_due`, `approved_invoice_due` | NUMERIC | Same — same name confusion |
| `date_signed`, `date_created`, `date_updated`, `date_status_change` | INT | |

**Key takeaway:** even *within JN's own API*, financial entities have inconsistent field names. They almost certainly have multiple teams owning these. For your clone, pick one canonical name (`total`, `balance`) and stick to it.

### 4.6 `users`, `record_types`, `sources`, `statuses` (lookups)

Configuration tables that drive dropdowns:

- `users` — internal staff (sales reps, admins). Referenced by `jnid`.
- `record_types` — pipeline definitions. A "record type" defines BOTH the entity flavor (Job vs Contact) AND its workflow. E.g., "Residential - Insurance" is a job record_type with its own status sequence.
- `sources` — acquisition channels (Self Generated, Referral, Canvassing).
- `statuses` — per-record-type workflow stages.

A multi-tenant CRM clone needs all four as tenant-scoped configuration tables.

---

## 5. The List/Search API Pattern

Every `GET /<entity>` endpoint accepts:

- `size` (page size — JN tolerates up to 1000 on `/files`)
- `from` (offset for pagination)
- `filter` — JSON-encoded ES-style query: `{"must": [{"range": {"date_status_change": {"gte": "now-7d"}}}]}`
- Sort field as a separate param

Behavioral quirks observed:

- Filters tolerate **relative date math** (`"now-30d"`, `"now-2h"`) — pure ES syntax.
- Some tenants reject the JSON `filter` param and require legacy date-range query strings (`?date_created_start=YYYY-MM-DD`). The codebase has a [fallback ladder](../clients/jobnimbus.py#L131-L160) that tries `date_updated`, `date_modified`, `date_created` filters in turn before degrading to legacy.
- Endpoint paths are **not stable across tenants**. The codebase probes `/workorders`, `/work_orders`, `/estimates`, `/invoices`, `/financials` to find which one a tenant has provisioned ([_resolve_workorder_endpoint](../clients/jobnimbus.py#L524-L599)). This screams "feature flags per tenant" or "tenant-specific schema migrations" — both signs of multi-tenancy that's been bolted on, not designed in.

For a clone: pick one endpoint shape per entity. Don't replicate this mess.

---

## 6. Webhooks

JN posts to subscriber URLs on:

- Contact created / updated / status_change
- Job created / updated / status_change
- (Probably) Activity created — though the codebase doesn't subscribe to this

Payload shape (observed):

```json
{
  "jnid": "...",
  "status_name": "In Production",
  "previous_status_name": "Estimating",
  "record_type_name": "Residential - Insurance",
  "job": { ... },        // sometimes nested
  "object": { ... },     // varies — JN inlines vs. nests inconsistently
  "data": { ... }
}
```

The codebase normalizes payload variants in [_extract_jn_status_name](../api/routers/webhooks/jobnimbus.py#L35-L46) — it checks the top-level, then `job`, `object`, `data` nodes. **Lesson for a clone:** standardize webhook envelope on day one. JN's inconsistency here is a tax everyone integrating with them pays.

Signature verification: header `x-jobnimbus-signature`, HMAC of the raw body against a per-tenant secret.

---

## 7. The Workflow / Status Engine

A **`record_type` + `status` pair** defines where a record is in its lifecycle. Each record_type has its own status sequence:

```
Residential - Insurance:
  New Lead → Appointment Scheduled → Appointment Ran → Estimating
    → Submitted For Approval → Job Approved → Signed Contract
    → In Production → Completed → Paid & Closed

Residential - Repairs:
  (different sequence)
```

**Implementation hint:** JN almost certainly has:

```sql
CREATE TABLE record_types (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER,
  entity_kind TEXT,         -- 'job' | 'contact'
  name TEXT,                -- 'Residential - Insurance'
);

CREATE TABLE statuses (
  id INTEGER PRIMARY KEY,
  record_type_id INTEGER REFERENCES record_types(id),
  name TEXT,
  sort_order INTEGER,
  is_terminal BOOLEAN
);

CREATE TABLE status_transitions (    -- optional but likely
  from_status_id INTEGER,
  to_status_id INTEGER,
  requires_permission TEXT
);
```

Status changes update the **`date_status_change` timestamp**, which is separate from `date_updated`. This is the single most useful field in the API — it lets you build change-streams without polling everything.

---

## 8. What This Tells Us About JN's Architecture

Pulling threads together, JobNimbus is most plausibly:

- **PostgreSQL** as primary OLTP store (or possibly MySQL — but the JSONB-friendly behavior around `primary`/`related` favors Postgres).
- **Elasticsearch index per tenant** (or per entity-type) for the filter API. The query DSL is too ES-shaped to be coincidence.
- **A monolithic Rails or .NET API** in front (the inconsistency in field naming, the `_name` denormalization habit, the "tries multiple endpoints" mess all point to a long-lived monolith with multiple generations of devs touching the same codebase).
- **Multi-tenant via row-level `tenant_id`**, with feature-flagged endpoints (`/v2/...` is opt-in per tenant).
- **S3 (or equivalent) for files**, fronted by a base64 JSON proxy — likely a leftover decision from an early API version.
- **Webhook fan-out via a queue** (SQS/Sidekiq), with at-least-once delivery — payload duplication suggests no exactly-once guarantee.

---

## 9. The Stripped-Down Clone — Recommended Schema

Here's a clean v1 you can ship. Multi-tenant, contact-first, polymorphic relationships, but without the legacy crud.

```sql
-- Tenancy
CREATE TABLE tenants (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE users (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  email        TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role         TEXT NOT NULL,       -- 'admin' | 'sales' | 'production' | ...
  UNIQUE (tenant_id, email)
);

-- Workflow config
CREATE TABLE record_types (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  entity_kind  TEXT NOT NULL CHECK (entity_kind IN ('contact','job')),
  name         TEXT NOT NULL,
  UNIQUE (tenant_id, entity_kind, name)
);

CREATE TABLE statuses (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  record_type_id  UUID NOT NULL REFERENCES record_types(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  sort_order      INTEGER NOT NULL,
  is_terminal     BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (record_type_id, name)
);

CREATE TABLE sources (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  name         TEXT NOT NULL,
  UNIQUE (tenant_id, name)
);

-- Core entities
CREATE TABLE contacts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id),
  external_id     TEXT,
  display_name    TEXT NOT NULL,
  first_name      TEXT,
  last_name       TEXT,
  email           TEXT,
  mobile_phone    TEXT,
  address         JSONB,                          -- {line1, line2, city, state, zip}
  record_type_id  UUID REFERENCES record_types(id),
  status_id       UUID REFERENCES statuses(id),
  source_id       UUID REFERENCES sources(id),
  owner_id        UUID REFERENCES users(id),       -- "sales rep"
  tags            TEXT[] NOT NULL DEFAULT '{}',
  custom_fields   JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status_changed_at TIMESTAMPTZ
);
CREATE INDEX ix_contacts_tenant_email ON contacts (tenant_id, lower(email));
CREATE INDEX ix_contacts_status_changed ON contacts (tenant_id, status_changed_at DESC);

CREATE TABLE jobs (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id),
  primary_contact_id UUID NOT NULL REFERENCES contacts(id),
  external_id       TEXT,
  number            TEXT,                       -- human-readable
  name              TEXT,
  description       TEXT,
  address           JSONB,                      -- jobsite, may differ from contact
  record_type_id    UUID REFERENCES record_types(id),
  status_id         UUID REFERENCES statuses(id),
  source_id         UUID REFERENCES sources(id),
  owner_id          UUID REFERENCES users(id),
  approved_total    NUMERIC(12,2) NOT NULL DEFAULT 0,
  invoiced_total    NUMERIC(12,2) NOT NULL DEFAULT 0,
  balance           NUMERIC(12,2) NOT NULL DEFAULT 0,
  custom_fields     JSONB NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status_changed_at TIMESTAMPTZ
);
CREATE INDEX ix_jobs_tenant_status_changed ON jobs (tenant_id, status_changed_at DESC);
CREATE INDEX ix_jobs_primary_contact ON jobs (primary_contact_id);

-- Polymorphic relationships (the JN secret sauce, simplified)
CREATE TABLE relationships (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  parent_type  TEXT NOT NULL,            -- 'contact' | 'job'
  parent_id    UUID NOT NULL,
  child_type   TEXT NOT NULL,            -- 'file' | 'activity' | 'estimate'
  child_id     UUID NOT NULL,
  is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (parent_type, parent_id, child_type, child_id)
);
CREATE INDEX ix_rel_child ON relationships (child_type, child_id);
CREATE INDEX ix_rel_parent ON relationships (tenant_id, parent_type, parent_id);

-- Activity stream (notes, tasks, calls, status changes) — single table, discriminated
CREATE TABLE activities (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id),
  kind          TEXT NOT NULL,            -- 'note' | 'task' | 'call' | 'email' | 'status_change'
  title         TEXT,
  body          TEXT,
  starts_at     TIMESTAMPTZ,
  ends_at       TIMESTAMPTZ,
  completed_at  TIMESTAMPTZ,
  created_by_id UUID REFERENCES users(id),
  assigned_to_id UUID REFERENCES users(id),
  payload       JSONB NOT NULL DEFAULT '{}',  -- kind-specific extras
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_activities_tenant_created ON activities (tenant_id, created_at DESC);

-- Files
CREATE TABLE files (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  filename     TEXT NOT NULL,
  content_type TEXT,
  size_bytes   BIGINT,
  storage_key  TEXT NOT NULL,            -- S3 key
  description  TEXT,
  uploaded_by_id UUID REFERENCES users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Financial records
CREATE TABLE estimates (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id),
  job_id        UUID NOT NULL REFERENCES jobs(id),
  number        TEXT,
  status        TEXT NOT NULL,           -- 'draft' | 'sent' | 'approved' | 'rejected'
  line_items    JSONB NOT NULL DEFAULT '[]',
  total         NUMERIC(12,2) NOT NULL DEFAULT 0,
  signed_at     TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- (workorders, invoices, material_orders follow the same shape)

-- Status change audit (for reporting)
CREATE TABLE status_change_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id),
  entity_type   TEXT NOT NULL,
  entity_id     UUID NOT NULL,
  from_status_id UUID REFERENCES statuses(id),
  to_status_id   UUID NOT NULL REFERENCES statuses(id),
  changed_by_id UUID REFERENCES users(id),
  changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_sce_entity ON status_change_events (entity_type, entity_id, changed_at DESC);

-- Webhook subscriptions
CREATE TABLE webhook_subscriptions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id),
  url          TEXT NOT NULL,
  secret       TEXT NOT NULL,
  events       TEXT[] NOT NULL,          -- ['job.status_change', 'contact.created', ...]
  is_active    BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE webhook_deliveries (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id UUID NOT NULL REFERENCES webhook_subscriptions(id),
  event_type      TEXT NOT NULL,
  payload         JSONB NOT NULL,
  status          TEXT NOT NULL,         -- 'pending' | 'success' | 'failed'
  retry_count     INTEGER NOT NULL DEFAULT 0,
  next_retry_at   TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 10. API Surface — Minimum Viable Routes

Mirror the JN-style polymorphic pattern but with a cleaner contract:

```
# Contacts
GET    /api/v1/contacts?status=...&record_type=...&updated_since=...
POST   /api/v1/contacts
GET    /api/v1/contacts/{id}
PATCH  /api/v1/contacts/{id}
DELETE /api/v1/contacts/{id}
GET    /api/v1/contacts/{id}/jobs
GET    /api/v1/contacts/{id}/files
GET    /api/v1/contacts/{id}/activities

# Jobs
GET    /api/v1/jobs?...
POST   /api/v1/jobs                 # body must include primary_contact_id
GET    /api/v1/jobs/{id}
PATCH  /api/v1/jobs/{id}
GET    /api/v1/jobs/{id}/files
GET    /api/v1/jobs/{id}/activities
GET    /api/v1/jobs/{id}/estimates

# Activities (single endpoint, discriminated)
POST   /api/v1/activities           # {kind, parent_type, parent_id, ...}
GET    /api/v1/activities?parent_id=...

# Files (multipart upload — NOT JN's base64 mistake)
POST   /api/v1/files                # multipart: file + parent_type + parent_id
GET    /api/v1/files/{id}           # returns 302 to signed S3 URL
DELETE /api/v1/files/{id}

# Webhooks (admin)
POST   /api/v1/webhook-subscriptions
GET    /api/v1/webhook-subscriptions
DELETE /api/v1/webhook-subscriptions/{id}

# Workflow config (admin)
GET/POST/PATCH/DELETE /api/v1/record-types
GET/POST/PATCH/DELETE /api/v1/statuses
GET/POST/PATCH/DELETE /api/v1/sources
```

**Filtering**: pick one. Either offer simple query-string filters (`?status=approved&updated_since=2026-01-01`) or expose a single `q` parameter that accepts a structured JSON DSL — but don't do both halfheartedly the way JN does.

---

## 11. Decisions JN Made That You Should Reconsider

| JN does | Reconsider because | Better default |
|---|---|---|
| `display_name` is globally unique | Forces ugly disambiguation suffixes | Allow duplicates; rely on email/phone for identity |
| Files via base64 JSON | 33% overhead, hard on big PDFs | Multipart upload, signed-URL download |
| Multiple workorder endpoint paths | Caller has to probe | One canonical path per entity |
| ES-style filter DSL on every list endpoint | Steep learning curve | Query-string filters for 90% of cases; full-text search via dedicated `/search` |
| Inconsistent total/balance field names across entities | Defensive parsing in every consumer | One canonical name (`total`, `balance`) |
| Unix timestamps (ints) for all dates | Easy to confuse with status IDs | ISO-8601 strings |
| `primary` and `related` in payloads | Conceptually nice but verbose | One `parent: {type, id}` field; many-to-many lives in a separate join |
| Per-tenant feature-flagged endpoints | Operational nightmare | Versioned API (`/v1`, `/v2`) with explicit deprecation |

---

## 12. Build Order for the Stripped-Down Clone

If I were starting fresh, the order would be:

1. **Tenants + users + auth** (JWT, simple roles).
2. **Contacts CRUD** with basic search.
3. **Jobs CRUD** with FK to contact.
4. **Record types + statuses** (workflow config).
5. **Activities** (single table, `kind` discriminator) — this unlocks the timeline UI.
6. **Files** with S3 + signed URLs.
7. **Status-change events table + automatic logging on UPDATE**.
8. **Webhooks** (subscriptions + retry queue) — copy the durable retry pattern from [api/utils/webhook_helpers.py](../api/utils/webhook_helpers.py).
9. **Estimates** as the first financial entity. Postpone work orders / material orders until you actually need them.
10. **Reporting view layer** — denormalized read models in views or a separate snapshot table.

Stop there. Everything else (custom fields UI, automation rules, mobile app, payments) is v2+.

---

## 13. Bellas XV Adaptation: Bridal / Quinceañera Shop Scope

The JobNimbus mental model maps unusually well to a dress shop, but it should be adapted rather than cloned. A bridal or quinceañera shop is not a roofing/insurance operation: the customer journey is event-centered, party-based, appointment-heavy, and inventory-aware.

### What maps cleanly

The best borrowed spine is:

```
Contact -> Event -> Participants -> Dress Orders / Alterations / Activities / Files
```

- **Contact -> Event -> Orders/Activities** is the right backbone. The bride, quinceañera, parent, or buyer is the root identity; the wedding, quinceañera, prom, or special occasion is the "deal"; dress orders, accessory orders, alteration work, fittings, notes, and files hang off the event.
- **Record types still matter.** `Wedding`, `Quinceañera`, `Prom`, `Mother of the Bride`, and `Special Occasion` each want their own workflow statuses, milestones, and reporting slices.
- **Activities-as-timeline is worth stealing.** Fitting notes, alteration updates, dress arrival events, phone calls, emails, and pickup notes can share one `activities` table with a `kind` discriminator.
- **`status_changed_at` is high-value.** For this business, "ordered -> arrived -> first fitting -> alterations complete -> picked up" is the operational UX. A wedding-day countdown and at-risk-order view can be built directly from status timestamps.
- **Polymorphic relationships are useful for notes/files.** A measurement photo, signed policy, alteration note, or invoice can attach to either the whole event or one participant/order.

### Where a direct clone misleads

- **A bridal sale is a party, not one job for one contact.** One event can include a bride, bridesmaids, mother of the bride, mother of the groom, flower girl, quinceañera court, or prom buyer. Each participant can have different measurements, dress order, alteration status, deadlines, and pickup status.
- **Inventory is a first-class domain.** Roofing materials are mostly job-cost inputs. Dresses need SKU identity, designer/style/color/size, sample vs sellable inventory, customer special orders, vendor purchase orders, expected arrival dates, and availability by store.
- **Appointments should stay their own domain.** This repo already has booking infrastructure in [api/routers/booking.py](/home/luis/bellas_xv/api/routers/booking.py) and appointment migrations starting at [005_create_appointments.py](/home/luis/bellas_xv/database/migrations/005_create_appointments.py). Keep appointments there and link them to events/participants by FK rather than flattening them into generic activities.
- **Multi-tenancy is not worth it for Bellas XV.** This is one shop. Use simple ownership/admin roles and avoid tenant-scoped complexity until there is a real multi-location or franchise requirement.

### Bridal-specific v1 schema spine

This is the realistic v1 shape I would build alongside the current booking system:

```sql
CREATE TABLE contacts (
  id BIGSERIAL PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  display_name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  preferred_language TEXT,
  address JSONB NOT NULL DEFAULT '{}',
  tags TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  primary_contact_id BIGINT NOT NULL REFERENCES contacts(id),
  event_type TEXT NOT NULL,          -- 'wedding' | 'quinceanera' | 'prom' | 'mother_of_bride' | 'special_occasion'
  event_name TEXT NOT NULL,
  event_date DATE,
  status TEXT NOT NULL,              -- workflow status for the event type
  status_changed_at TIMESTAMPTZ,
  owner_user_id BIGINT,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_events_date ON events (event_date);
CREATE INDEX ix_events_status_changed ON events (status_changed_at DESC);

CREATE TABLE event_participants (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  contact_id BIGINT REFERENCES contacts(id),
  role TEXT NOT NULL,                -- 'bride' | 'quinceanera' | 'bridesmaid' | 'mob' | 'mog' | 'flower_girl' | 'guest'
  display_name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  measurements JSONB NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_participants_event ON event_participants (event_id);

CREATE TABLE designers (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  contact_info JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE products (
  id BIGSERIAL PRIMARY KEY,
  designer_id BIGINT REFERENCES designers(id),
  category TEXT NOT NULL,             -- 'bridal_gown' | 'quince_dress' | 'bridesmaid' | 'accessory'
  style_number TEXT NOT NULL,
  name TEXT,
  color TEXT,
  size_label TEXT,
  sku TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE dress_orders (
  id BIGSERIAL PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  participant_id BIGINT REFERENCES event_participants(id),
  product_id BIGINT REFERENCES products(id),
  order_type TEXT NOT NULL,           -- 'sample_sale' | 'special_order' | 'accessory'
  status TEXT NOT NULL,               -- 'quoted' | 'ordered' | 'confirmed' | 'arrived' | 'fitting' | 'ready' | 'picked_up' | 'cancelled'
  status_changed_at TIMESTAMPTZ,
  vendor_order_number TEXT,
  expected_arrival_date DATE,
  arrived_at TIMESTAMPTZ,
  pickup_deadline DATE,
  price NUMERIC(12,2) NOT NULL DEFAULT 0,
  deposit_paid NUMERIC(12,2) NOT NULL DEFAULT 0,
  balance_due NUMERIC(12,2) NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_dress_orders_event ON dress_orders (event_id);
CREATE INDEX ix_dress_orders_participant ON dress_orders (participant_id);
CREATE INDEX ix_dress_orders_status_changed ON dress_orders (status_changed_at DESC);
CREATE INDEX ix_dress_orders_expected_arrival ON dress_orders (expected_arrival_date);

CREATE TABLE alterations (
  id BIGSERIAL PRIMARY KEY,
  dress_order_id BIGINT NOT NULL REFERENCES dress_orders(id) ON DELETE CASCADE,
  status TEXT NOT NULL,               -- 'not_started' | 'pinned' | 'in_progress' | 'ready_for_fit' | 'complete'
  status_changed_at TIMESTAMPTZ,
  assigned_to_user_id BIGINT,
  first_fitting_at TIMESTAMPTZ,
  final_fitting_at TIMESTAMPTZ,
  due_at TIMESTAMPTZ,
  quoted_price NUMERIC(12,2),
  final_price NUMERIC(12,2),
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE activities (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,                 -- 'note' | 'call' | 'email' | 'status_change' | 'fitting_note' | 'arrival_update'
  parent_type TEXT NOT NULL,          -- 'event' | 'participant' | 'dress_order' | 'alteration'
  parent_id BIGINT NOT NULL,
  title TEXT,
  body TEXT,
  payload JSONB NOT NULL DEFAULT '{}',
  created_by_user_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_activities_parent ON activities (parent_type, parent_id, created_at DESC);
```

Appointments remain in the existing appointment tables. Add nullable links such as `event_id`, `participant_id`, and/or `dress_order_id` to appointments when the CRM spine exists.

### Pieces to steal from JobNimbus

- Polymorphic relationships for files and notes attaching to an event, participant, dress order, or alteration.
- Single-table activities with a `kind` discriminator for timeline UI.
- `status_changed_at` on both `events` and `dress_orders`.
- Per-record-type workflows for `Wedding`, `Quinceañera`, `Prom`, and `Mother of the Bride`.
- Webhook subscription pattern later, especially for accounting, designer portals, or SMS/email automation.

### Pieces to skip

- Multi-tenancy, unless Bellas XV becomes a multi-shop product.
- Opaque `jnid` IDs. Use `BIGSERIAL` for simplicity or UUIDv7 if external-safe IDs become important.
- Elasticsearch-style filtering. Postgres indexes and ordinary query params are enough for one shop's data volume.
- Base64 file uploads. Keep multipart uploads and signed downloads.
- Generic financial entities copied from roofing. Bridal needs deposits, layaway/payment schedules, purchase orders, and balances tied to dress orders.

### Realistic implementation scope

There are two honest paths:

| Path | Scope | Realistic duration | Notes |
|---|---:|---:|---|
| Additive CRM spine | Events, participants, dress orders, alterations, timeline, links to current appointments | 2-3 focused weeks | Best fit for this repo because booking already exists. |
| Replacement shop OS | CRM spine plus inventory, vendor purchase orders, deposits/balances, file storage, reporting, staff workflows | 6-8 focused weeks | More useful long-term, but it becomes a full operating system. |

Recommended v1 is the additive path:

1. **Week 1:** database migrations, API CRUD for events/participants/dress orders, status timestamp helpers, and appointment linking.
2. **Week 2:** admin UI for event detail, party roster, dress order cards, alteration panel, and timeline activity stream.
3. **Week 3:** countdown/at-risk dashboard, arrival due dates, pickup deadlines, lightweight files/notes, and smoke tests.

Inventory should be v1.5, not day one. The first shippable win is knowing every event, every person in the party, every dress/order status, every fitting, and every date that can hurt you.

---

## Sources Used to Build This Map

- [clients/jobnimbus.py](../clients/jobnimbus.py) — full API client; every endpoint, payload shape, and quirk in production use.
- [api/routers/webhooks/jobnimbus.py](../api/routers/webhooks/jobnimbus.py) — webhook payload normalization.
- [api/utils/jobnimbus_helpers.py](../api/utils/jobnimbus_helpers.py) — contact identity matching / recovery.
- [database/models.py:443](../database/models.py#L443) — local mirror table (`JNJobHistory`) which captures the fields we actually use from JN jobs.
- [docs/JOBNIMBUS_ORDERS_API.md](./JOBNIMBUS_ORDERS_API.md) — prior notes on the v2 orders surface.
