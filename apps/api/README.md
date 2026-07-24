# Kelley Autoplex — API

The FastAPI backend and single system of record for Kelley Autoplex: inventory,
CRM/deals, quotes, invoices, payments, staff, scheduling, messaging, and
notifications. Every other surface (admin SPA, sales surface, storefront) reads
and writes through this API over HTTP.

This app is **not** a pnpm workspace member — it is a standalone Python
application with its own `.venv`.

## Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI + SQLAlchemy 2.0 |
| Database | PostgreSQL (`psycopg2`) |
| Cache / rate limiting | Redis |
| Server | uvicorn (`api.server:app`), 2 workers in production |
| Auth | session cookie (HttpOnly) + readable CSRF cookie; CSRF header on unsafe methods |
| Email | Gmail API (OAuth2) with SMTP fallback; null transport when unconfigured |

## Module architecture (Phase 3)

The application is organized into **eight domain modules** under `modules/`:

`core`, `contacts`, `messaging`, `deals`, `inventory`, `scheduling`, `booking`,
`analytics`.

- **`core` and `contacts` are kernel modules** — always enabled, no flag.
- The other six each have a `MODULE_<NAME>_ENABLED` flag in `config/settings.py`,
  all defaulting to **true**. Disabling a module means its **routers are not
  mounted and its workers do not start** — its Python package and models still
  import unconditionally.
- **Routers mount synchronously** at app construction; **workers start in the
  lifespan**. The `modules/registry.py` `MOUNTS`/`MODULES` tables drive both, in
  an order that preserves the historical OpenAPI contract. Never mount a router
  inside the lifespan.
- Workers: `notifications` and `daily` belong to `core`; `schedule_monitor`
  belongs to `scheduling`.

New routers go in `modules/<domain>/routers/` and are registered in
`registry.py`; new services go in `modules/<domain>/services/`.

### Models facade

`database/models/` is split into per-domain files, but
`from database.models import <Name>` keeps working via the facade in
`database/models/__init__.py`. **Every model imports unconditionally**, so every
table registers on `Base.metadata` regardless of module flags. When you add a
model, put it in its domain file **and** re-export it (and add it to `__all__`)
in `__init__.py`.

### Migration compatibility surface

`apps/api/services/` is **not** an application service directory. It is a narrow,
permanent compatibility shim that re-exports exactly the two symbols migrations
`061` and `062` import at `upgrade()` time (`integration_tokens`,
`quote_signature_hmac`). Application code must import from `modules.*`; a guard
smoke (`tests/test_services_compat_guard_smoke.py`) fails if app source imports
`services.*`.

## Dev quickstart

```bash
cd apps/api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                                # fill in DATABASE_URL + SECRET_KEY at minimum
.venv/bin/python -m database.migrations.runner      # apply migrations (forward-only)
.venv/bin/python scripts/seed_admin.py              # first admin user (interactive)
.venv/bin/uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

API health: `curl http://127.0.0.1:8000/api/health`

The admin/sales SPA and storefront live in `apps/admin` and `apps/storefront`;
see the root [README.md](../../README.md) for running them.

## Smoke tests

Smoke tests are **standalone scripts, not pytest** — each mints its own ephemeral
fixtures and cleans up, and they must run **serially** (several mutate singleton
numbering rows). Run one directly:

```bash
.venv/bin/python tests/test_events_smoke.py
```

Run the whole handoff suite (the `PYTHON` override is required — the script's
default interpreter path does not exist):

```bash
PYTHON="$PWD/.venv/bin/python" scripts/smoke_handoff.sh
```

See [docs/TESTING.md](docs/TESTING.md).

## Deploy

Production is systemd + Caddy; the backend runs as `kelley-backend.service`
(uvicorn on `127.0.0.1:8000`). Deployment is driven from the repo root by
`deploy/build.sh` and `deploy/install.sh`.

```bash
# From the repo root — installs deps + runs migrations (does NOT restart):
deploy/build.sh --api
```

Migrations are applied explicitly:

```bash
.venv/bin/python -m database.migrations.runner
```

Full production procedures, rollback behavior, and release gates are in
[docs/OPERATIONS.md](../../docs/OPERATIONS.md). System design is in
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).

## Backend reference docs

Section references under [docs/](docs/) (backend-internal; some predate the
module restructure and are marked where they are superseded by the root docs):

- [Database](docs/DATABASE.md) — schema, migration runner, conventions
- [Testing](docs/TESTING.md) — smoke-test pattern, how to add one
- [Data retention & delete policy](docs/DATA_RETENTION_AND_DELETE_POLICY.md)
- [CRM](docs/CRM.md), [Booking](docs/BOOKING.md) — domain references
- Feature phase plans (`docs/*_PHASES.md`, `docs/*_PLAN.md`) — historical, shipped

## Conventions

- **Routers contain no business logic**; **services do not import FastAPI**.
- **Migrations are append-only and forward-only** — never edit, renumber, or
  squash an existing migration.
- **Phone (E.164) is the canonical contact identity**, not email.

## License

Private. Not open source.
