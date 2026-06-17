# Bellas XV

The platform behind a quinceañera and bridal shop in San Antonio, TX.

A booking widget on the marketing site captures leads. Staff promote them
into a CRM event, then drag the event through a kanban from `lead` all the
way to `picked_up`. Quotes and invoices are built from a product catalog of
gowns seeded from several designer and distributor sites. One small shop,
one stack, no multi-tenancy.

## Surfaces

- `shopbellasxv.com` — marketing site + public booking widget
- `admin.shopbellasxv.com` — staff admin SPA (React)
- `sales.shopbellasxv.com` — stylist sales portal (kiosk PIN login,
  same React bundle, hostname-routed)
- `api.shopbellasxv.com` — FastAPI backend

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI 0.115 + SQLAlchemy 2.0 + PostgreSQL |
| Auth | JWT (HS256) bearer tokens |
| Public widget | Vanilla JS, embedded on the marketing site |
| Admin SPA | React 19 + MUI 6 + Vite, react-query, dnd-kit |
| Reverse proxy | Nginx + Certbot |
| Process supervisor | systemd (`bellas-xv-api.service`) |

## Docs

Section-by-section reference under [docs/](docs/):

- [Architecture overview](docs/ARCHITECTURE.md) — what runs where, how
  requests flow, where to add new code
- [CRM: contacts, events, kanban](docs/CRM.md) — the events domain, status
  workflow, promotion path
- [Booking widget + admin appointments](docs/BOOKING.md) — public submission
  flow, slot algorithm, attribution capture, admin surface
- [Database](docs/DATABASE.md) — schema, migration runner, indexes,
  conventions
- [Frontend (admin SPA)](docs/FRONTEND.md) — pages, react-query patterns,
  drag-drop, build
- [Testing](docs/TESTING.md) — smoke-test pattern, how to add one

Phase plans (feature-by-feature roadmaps with reality findings,
slices, and shipped/deferred markers):

- [Sales Portal](docs/SALES_PORTAL_PHASES.md) — stylist kiosk surface,
  geofenced clock-in, attendance review, shifts, time-off, holidays
- [Scheduling Improvement Plan](docs/SCHEDULING_IMPROVEMENT_PLAN.md) —
  shift requests, pickup/open shifts, swaps, admin approval, and smoke-test
  phases
- [Global Search](docs/GLOBAL_SEARCH_PHASES.md) — trigram + document
  indexes, command palette
- [Catalog and vendor seeds](docs/CATALOG_SKU_OBFUSCATION_PHASES.md) — SKU
  obfuscation, the Products gallery/list browse page, and the multi-vendor
  seed scrapers (`scripts/seed_catalog/`)
- [Invoice Discounts and Terms](docs/INVOICE_DISCOUNTS_AND_TERMS_PHASES.md)
  — stacked discounts, plan selector, quote installments

Operational / planning docs at the repo root:

- [INFRASTRUCTURE.md](INFRASTRUCTURE.md) — VPS layout, services, env vars
- [VPS_HARDENING.md](VPS_HARDENING.md) — memory caps, sysctl, log rotation
- [BOOKING_WIDGET_PHASES.md](BOOKING_WIDGET_PHASES.md) — booking widget
  feature roadmap (planning)
- [FIT_PREP_TOOL_PLAN.md](FIT_PREP_TOOL_PLAN.md) — enrichment / fit-prep
  feature plan
- [JOBNIMBUS_ARCHITECTURE_MAP.md](JOBNIMBUS_ARCHITECTURE_MAP.md) — reverse
  engineering of JobNimbus's CRM that informed our event model

## Dev quickstart

```bash
git clone git@github.com:more-hangouts/bellasxv.git
cd bellasxv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in DATABASE_URL + SECRET_KEY at minimum
venv/bin/python -m database.migrations.runner
venv/bin/uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

In a second shell:

```bash
cd frontend
npm install
echo 'VITE_API_URL=http://127.0.0.1:8000/api' > .env.local
# To hit the sales tree on localhost without DNS, also set:
# echo 'VITE_FORCE_SUBDOMAIN=sales' >> .env.local
npm run dev
```

API health: `curl http://127.0.0.1:8000/api/health`

## Smoke tests

```bash
venv/bin/python tests/test_booking_smoke.py
venv/bin/python tests/test_admin_booking_smoke.py
venv/bin/python tests/test_notifications_smoke.py
venv/bin/python tests/test_events_smoke.py
venv/bin/python tests/test_boutique_experience_smoke.py

# Sales Portal (Phase 1-8):
venv/bin/python tests/test_sales_auth_smoke.py
venv/bin/python tests/test_clock_in_smoke.py
venv/bin/python tests/test_time_off_endpoints_smoke.py
```

Each script mints its own ephemeral fixtures and cleans up. See
[docs/TESTING.md](docs/TESTING.md). Note: smokes that mutate
singleton/numbering rows or walk the global table must run
**serially**, not in parallel — running two cron-pass smokes
concurrently will trip each other.

## Deploy

```bash
# Build the admin SPA (output goes to frontend/dist/, served by nginx)
cd frontend && npm run build

# Restart the API to pick up backend changes
sudo systemctl restart bellas-xv-api
```

DB migrations are applied explicitly:

```bash
venv/bin/python -m database.migrations.runner
```

See [INFRASTRUCTURE.md](INFRASTRUCTURE.md) for the full VPS layout.

## Conventions

A few load-bearing rules — explained in the section docs but worth calling
out at the top:

- **Routers contain no business logic** ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#boundaries))
- **Services do not import FastAPI** — they're plain modules
- **Migrations are forward-only**, numbered sequentially, mirrored as Python
  CHECK-constraint workflows where applicable ([docs/DATABASE.md](docs/DATABASE.md#migration-runner))
- **Phone (E.164) is the canonical contact identity**, not email ([docs/CRM.md](docs/CRM.md#contact-identity))

## License

Private. Not open source.
