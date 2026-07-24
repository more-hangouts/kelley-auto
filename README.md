# Kelley Autoplex

Platform for a used-car dealership: a public marketing/storefront site, an admin
+ sales back office, and a FastAPI backend that is the single system of record
(inventory, CRM/deals, quotes, invoices, payments, staff, scheduling,
notifications). Every surface reads and writes through the backend over HTTP.

This repository is a **pnpm monorepo**, live in production behind systemd + Caddy
on a single VPS. The JavaScript apps share one workspace; the Python API runs in
its own virtualenv.

## Architecture at a glance

```text
Browser
  └─ Caddy (TLS, reverse proxy)
       ├─ kelleyautoplex.com / www   → storefront  (Next.js, :3000)
       ├─ api.kelleyautoplex.com      → API         (FastAPI/uvicorn, :8000)
       └─ admin. / sales.*            → admin SPA static build (apps/admin/dist)
```

- **API** — FastAPI + SQLAlchemy, organized into eight domain modules
  (`core`, `contacts`, `messaging`, `deals`, `inventory`, `scheduling`,
  `booking`, `analytics`). Routers mount synchronously; background workers run in
  the app lifespan.
- **Admin SPA** — one Vite/React build serving both the admin surface and the
  sales surface; which one renders is chosen at runtime by hostname
  (`sales.*` → sales).
- **Storefront** — Next.js public site that reads business data from the API.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and
[docs/OPERATIONS.md](docs/OPERATIONS.md) for production procedures.

## Layout

```text
kelley/
├─ apps/
│  ├─ api/            # FastAPI + SQLAlchemy + workers — system of record (Python venv)
│  ├─ admin/          # Vite/React/MUI admin + sales SPA
│  └─ storefront/     # Next.js public marketing/storefront site
├─ packages/
│  ├─ shared-types/   # @kelley/shared-types — shared enums (scaffolded; see its README)
│  └─ brand-assets/   # @kelley/brand-assets — brand asset ownership placeholder
├─ deploy/            # build.sh, install.sh, systemd units, Caddyfile, env templates
└─ docs/              # canonical architecture/operations docs + docs/archive/
```

The pnpm workspace (`pnpm-workspace.yaml`) covers `apps/admin`, `apps/storefront`,
and `packages/*`. **`apps/api` is not a workspace member** — it is a standalone
Python app with its own `.venv`.

## Prerequisites

- Node 20+ and **pnpm 10** (pinned via `packageManager` in the root `package.json`)
- Python 3.11+
- PostgreSQL 14+ and Redis running locally

## Setup

Install JavaScript dependencies once from the repository root:

```bash
pnpm install
```

Create the API virtualenv and install its dependencies:

```bash
cd apps/api
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Copy the environment templates for each app you plan to run
(`apps/api/.env`, `apps/admin/.env.local`, `apps/storefront/.env.local`) from
their respective `.env.example` files, then fill in local values.

## Local development

### API — http://127.0.0.1:8000

Create the Postgres role/database matching `DATABASE_URL`, make sure Redis is up,
then run the forward-only migration runner and start uvicorn:

```bash
cd apps/api
.venv/bin/python -m database.migrations.runner    # apply migrations
.venv/bin/python scripts/seed_admin.py            # first admin user (interactive)
.venv/bin/uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

Health check: `curl http://127.0.0.1:8000/api/health`.

### Admin + sales SPA — http://127.0.0.1:5173

```bash
pnpm --filter ./apps/admin dev
```

Both surfaces are served by the same build. The dev server renders the admin
surface by default; to work on the **sales** surface without DNS, set
`VITE_FORCE_SUBDOMAIN=sales`:

```bash
VITE_FORCE_SUBDOMAIN=sales pnpm --filter ./apps/admin dev
```

See [apps/admin/README.md](apps/admin/README.md) for the SPA's API-client layout,
lazy-route conventions, and the production-build warning.

### Storefront — http://127.0.0.1:3000

```bash
pnpm --filter ./apps/storefront dev
```

## Tests

Backend behavior is covered by standalone smoke scripts (not pytest). Run the
whole suite from the API directory:

```bash
cd apps/api
PYTHON="$PWD/.venv/bin/python" scripts/smoke_handoff.sh
```

The `PYTHON` override is required. See [docs/OPERATIONS.md](docs/OPERATIONS.md)
and [CLAUDE.md](CLAUDE.md) for details.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, modules, data flow, boundaries
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — production topology, build/deploy, migrations, troubleshooting
- [CLAUDE.md](CLAUDE.md) — engineering rules and guardrails for contributors
- [deploy/README.md](deploy/README.md) — production serving runbook (systemd + Caddy)
- [docs/archive/](docs/archive/) — historical plans and baselines (not authoritative)

## Warnings

- **Migrations are append-only and immutable.** Never edit, renumber, or squash
  an existing migration in `apps/api/database/migrations/`. Add a new numbered
  file. See [CLAUDE.md](CLAUDE.md).
- **Production builds write in place.** `deploy/build.sh --admin` builds directly
  into `apps/admin/dist`, which Caddy serves live; the storefront build writes
  into the running `apps/storefront/.next` tree. Do not run production builds
  casually. For local admin bundle verification, always build to a temporary
  `--outDir`. See [docs/OPERATIONS.md](docs/OPERATIONS.md).
