# Infrastructure

Backend foundation for **Bellas XV** (San Antonio, TX). This document inventories everything currently built in the repository.

## Stack

| Layer       | Technology                                |
| ----------- | ----------------------------------------- |
| Language    | Python 3 (venv-based)                     |
| Web framework | FastAPI 0.115.0                         |
| ASGI server | Uvicorn 0.32.0 (`[standard]` extras)      |
| ORM         | SQLAlchemy 2.0.36                         |
| Database    | PostgreSQL (via `psycopg2-binary` 2.9.10) |
| Config      | `python-dotenv` 1.0.1 + `.env` file       |
| Timezone    | `America/Chicago` (San Antonio local)     |

Pinned in [requirements.txt](requirements.txt).

## Repository layout

```
bellas_xv/
├── api/                      FastAPI application
│   ├── __init__.py
│   └── server.py
├── config/                   Environment + settings loading
│   ├── __init__.py
│   └── settings.py
├── database/                 DB engine, models, migrations
│   ├── __init__.py
│   ├── connection.py
│   ├── models.py
│   └── migrations/
│       ├── __init__.py
│       ├── runner.py
│       ├── 001_create_users.py
│       ├── 002_create_password_reset_tokens.py
│       ├── 003_create_integration_tokens.py
│       └── 004_create_webhook_events.py
├── scripts/                  (empty — placeholder)
├── tests/                    (only __init__.py — no tests yet)
├── venv/                     Local virtualenv (gitignored)
├── .env                      Local environment (gitignored)
├── .env.example              Template for required env vars
├── .gitignore
├── README.md                 Dev quickstart
└── requirements.txt          Pinned Python deps
```

## Configuration

[config/settings.py](config/settings.py) loads `.env` from the project root and exposes:

- `DATABASE_URL` — Postgres connection string (**required**)
- `APP_TIMEZONE` — IANA tz, e.g. `America/Chicago` (**required**)
- `APP_ENV` — defaults to `development`
- `LOG_LEVEL` — defaults to `INFO`

`validate_config()` exits the process with status 1 and a stderr message if any required var is missing. It is invoked from the FastAPI lifespan handler at startup, so a misconfigured deployment fails fast instead of serving broken responses.

[.env.example](.env.example) ships the development defaults:

```
DATABASE_URL=postgresql://bellas_xv_user:bellas_xv_pass@localhost:5432/bellas_xv
APP_TIMEZONE=America/Chicago
APP_ENV=development
LOG_LEVEL=INFO
```

## Database layer

### Engine + session ([database/connection.py](database/connection.py))

- `engine` — single SQLAlchemy `Engine` against `DATABASE_URL` with `pool_pre_ping=True` (drops dead connections before reuse).
- `SessionLocal` — `sessionmaker(bind=engine, autocommit=False, autoflush=False)`.
- `Base` — declarative base for ORM models.
- `get_db()` — generator dependency for FastAPI route injection (`yield`/`finally close`).

### ORM models ([database/models.py](database/models.py))

Mirror the migration schema 1:1 so the app code can use ORM access alongside raw SQL. Models defined:

- `User` (`users`)
- `PasswordResetToken` (`password_reset_tokens`)
- `IntegrationToken` (`integration_tokens`) — note: Python attr is `extra_metadata` mapped to SQL column `metadata` (avoids SQLAlchemy reserved-name collision).
- `WebhookEvent` (`webhook_events`)

### Migration system ([database/migrations/runner.py](database/migrations/runner.py))

A small home-grown migration runner — no Alembic.

- Scans `database/migrations/` for files matching `^(\d{3})_[a-z0-9_]+\.py$`.
- Tracks applied migrations in a `schema_migrations(migration_id, applied_at)` table that the runner creates on first execution.
- Sorts migrations by their numeric prefix and applies any not yet recorded.
- Each migration is wrapped in a single transaction (`engine.begin()`); the row in `schema_migrations` is inserted in the same transaction so a failed `upgrade()` rolls back atomically.
- On failure: prints `FAILED`, writes the error to stderr, returns exit code 1.
- Each migration module exposes `def upgrade(connection) -> None`. No `downgrade()` defined.

Run with:

```bash
python -m database.migrations.runner
```

### Schema (migrations 001–004)

**001 — `users`** ([001_create_users.py](database/migrations/001_create_users.py))
Auth-ready user table with `username`, `email`, `hashed_password`, `full_name`, `is_active`, `role` (default `'user'`), `permissions` JSONB array, `token_version` (for JWT invalidation), `created_at`, `last_login`. Indexed on `email` and `username`.

**002 — `password_reset_tokens`** ([002_create_password_reset_tokens.py](database/migrations/002_create_password_reset_tokens.py))
Password reset flow. Stores `token_hash` (not raw token), `expires_at`, `used_at`, FK to `users(id) ON DELETE CASCADE`. Indexed on `user_id` and `expires_at` (latter for sweeping expired tokens).

**003 — `integration_tokens`** ([003_create_integration_tokens.py](database/migrations/003_create_integration_tokens.py))
OAuth/integration credential storage, one row per `provider` (unique). Holds `access_token`, `refresh_token`, `token_type` (default `'Bearer'`), `expires_at`, `owner_uri`, `organization_uri`, free-form `metadata` JSONB.

**004 — `webhook_events`** ([004_create_webhook_events.py](database/migrations/004_create_webhook_events.py))
Inbound webhook ingestion log. Captures `source`, `event_type`, `external_id`, full `payload` and `headers` JSONB, processing state (`processed`, `processed_at`, `error_message`, `retry_count`). Indexes:
- `(source, processed)` — find unprocessed events per source.
- `(received_at DESC)` — recency queries.
- Partial unique on `(source, external_id) WHERE external_id IS NOT NULL` — dedup webhook deliveries while allowing many rows without an external id.

## API ([api/server.py](api/server.py))

Single FastAPI app instance. Lifespan handler runs `validate_config()` on startup.

### Routes

- `GET /api/health` — three-state health check:
  1. **DB unreachable** → 503 `{"status":"error","database":"disconnected"}`
  2. **DB up but `schema_migrations` table missing** → 503 `{"status":"error","database":"schema_missing"}`
  3. **OK** → 200 `{"status":"ok","database":"connected","migrations_applied":<count>,"timezone":<APP_TIMEZONE>}`

The schema-presence check makes accidental "running against an unmigrated DB" loud rather than silent.

## Local development

From [README.md](README.md):

```bash
cd bellas_xv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # edit if needed
python -m database.migrations.runner
uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

Smoke test: `curl http://127.0.0.1:8000/api/health`.

## What is *not* yet built

For a clear picture of remaining work:

- **No business endpoints** — only `/api/health`. No auth, user, integration, or webhook routes.
- **No password hashing / JWT layer** — `users.hashed_password` and `users.token_version` columns exist but no code reads or writes them.
- **No tests** — `tests/` contains only an empty `__init__.py`. No test runner configured.
- **No scripts** — `scripts/` is empty.
- **No migration downgrades / no Alembic** — the home-grown runner is forward-only.
- **No CI, no Dockerfile, no deployment config** — local dev only.
- **No structured logging** — `LOG_LEVEL` is read but not wired to anything.
- **No CORS, rate limiting, request middleware** — bare FastAPI app.
