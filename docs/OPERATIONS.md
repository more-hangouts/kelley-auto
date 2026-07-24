# Operations

Production procedures for Kelley Autoplex. For system design see
[ARCHITECTURE.md](ARCHITECTURE.md); for the serving runbook see
[../deploy/README.md](../deploy/README.md); for engineering rules see
[../CLAUDE.md](../CLAUDE.md).

> **Do not casually restart production services or run production builds.** The
> build scripts write in place (see §5), and an unexpected backend restart loads
> the current on-disk source (see §20). Treat every action here as
> deploy-window work.

## 1. Production topology

Single VPS, systemd-supervised processes behind Caddy (TLS + reverse proxy).
PostgreSQL and Redis run locally. There is **no Docker Compose**.

```text
Browser → Caddy → { storefront :3000 | API :8000 | admin/sales static dist }
```

## 2. Services, ports, hosts

| Service (systemd) | Process | Bind | WorkingDirectory |
|---|---|---|---|
| `kelley-backend.service` | uvicorn `api.server:app` (2 workers) | `127.0.0.1:8000` | `/opt/kelley/apps/api` |
| `kelley-public.service` | Next.js `next start` | `127.0.0.1:3000` | `/opt/kelley/apps/storefront` |

Caddy hosts (`deploy/Caddyfile`):

| Public host | Serves | Backed by |
|---|---|---|
| `kelleyautoplex.com`, `www.` | storefront | reverse_proxy `127.0.0.1:3000` |
| `api.kelleyautoplex.com` | API `/api/*` | reverse_proxy `127.0.0.1:8000` (25 MB body limit) |
| `admin.kelleyautoplex.com` | admin SPA (static) | file server, root `apps/admin/dist`, SPA `try_files … /index.html` |
| `sales.kelleyautoplex.com` *(optional)* | sales surface | same `apps/admin/dist` (self-routes by host) |

Uploaded vehicle photos persist at `/var/lib/kelley-autoplex/uploads`
(`ReadWritePaths` on the backend unit).

## 3. Environment-file locations

| App | Live env file | Template |
|---|---|---|
| API | `apps/api/.env` (EnvironmentFile of `kelley-backend.service`) | `apps/api/.env.example`, `deploy/env/backend.prod.env` |
| Storefront | `apps/storefront/.env.production` (baked at build + EnvironmentFile) | `deploy/env/public.env.production` |
| Admin | `apps/admin/.env.production` (baked at build) | `deploy/env/admin.env.production` |

## 4. Environment variables (names only)

Never print or commit secret values. The names below are grouped by concern;
consult the live env files and templates for the full set.

- **Core / DB / session**: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`,
  `ACCESS_TOKEN_EXPIRE_MINUTES`, `SESSION_COOKIE_DOMAIN`, `CORS_ORIGINS`,
  `APP_ENV`, `APP_TIMEZONE`, `LOG_LEVEL`, `RATE_LIMIT_FAIL_OPEN`.
- **URLs**: `PUBLIC_SITE_URL`, `PUBLIC_API_BASE_URL`, `ADMIN_BASE_URL`,
  `WIDGET_PUBLIC_BASE_URL`, `PORTAL_BASE_URL`, `BOOKING_WIDGET_ALLOWED_ORIGINS`,
  `ATTRIBUTION_COOKIE_DOMAIN`.
- **Signing/crypto keys**: `RESCHEDULE_TOKEN_SECRET`, `ENRICHMENT_TOKEN_SECRET`,
  `INTEGRATION_TOKEN_KEYS`, `QUOTE_SIGNATURE_KEY`, `LEAD_PII_KEYS`.
- **Email**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`, `SMTP_USE_TLS`, `GMAIL_OAUTH_CLIENT_ID`,
  `GMAIL_OAUTH_CLIENT_SECRET`, `GMAIL_OAUTH_REFRESH_TOKEN`, `GMAIL_API_SENDER`,
  `BOOKING_INTERNAL_NOTIFICATION_EMAILS`, `PUBLIC_LEAD_NOTIFY_EMAILS`.
- **SMS (Twilio)**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `TWILIO_FROM_NUMBER`, `TWILIO_MESSAGING_SERVICE_SID`.
- **Meta**: `META_PIXEL_ID`, `META_CAPI_TOKEN`, `META_CAPI_TEST_EVENT_CODE`,
  `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `META_PAGE_ID`,
  `META_IG_ACCOUNT_ID`, `META_PAGE_ACCESS_TOKEN`.
- **Ads/analytics**: `GOOGLE_ADS_CONVERSION_ID`, `GOOGLE_ADS_CONVERSION_LABEL`,
  `GOOGLE_ADS_DEVELOPER_TOKEN`, `PLAUSIBLE_DOMAIN`.
- **Storage/misc**: `DOCUMENT_STORAGE_BACKEND`, `DOCUMENT_STORAGE_ROOT`,
  `DOCUMENT_UPLOAD_MAX_MB`, `WEBHOOK_EVENTS_RETENTION_DAYS`.
- **Feature switches** (see §14): `META_CAPI_ENABLED`, `META_MESSAGING_ENABLED`,
  `SMS_SENDING_ENABLED`, `STOREFRONT_ANALYTICS_ENABLED`, and the six
  `MODULE_*_ENABLED` flags.
- **Storefront** (`NEXT_PUBLIC_*` baked at build): `NEXT_PUBLIC_API_BASE_URL`,
  `API_BASE_URL`, `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_GA_ID`,
  `NEXT_PUBLIC_META_PIXEL_ID`.
- **Admin**: `VITE_API_URL` (must end in `/api`).

## 5. Build scripts (`deploy/build.sh`)

Run as the `deploy` user. `build.sh` does **not** `git pull` and does **not**
restart services.

| Flag | What it does | Writes to |
|---|---|---|
| `--api` | `cd apps/api`; pip install requirements into `apps/api/.venv`; run migrations | `.venv` + database |
| `--admin` | `pnpm install --frozen-lockfile`; `pnpm --filter ./apps/admin build` | **`apps/admin/dist` (the live Caddy root)** |
| `--storefront` | `pnpm install --frozen-lockfile`; `pnpm --filter ./apps/storefront build` (bakes `NEXT_PUBLIC_*`) | **`apps/storefront/.next` (the running Next tree)** |
| `--all` | api → admin → storefront, in that order | all of the above |

> **⚠️ Both frontend builds write in place** into the directories that are being
> served live. See §16 and §20.

## 6. Migrations

```bash
cd /opt/kelley/apps/api
.venv/bin/python -m database.migrations.runner
```

Forward-only; applied files are tracked in `schema_migrations`. Migrations are
immutable — never edit or renumber an existing file. There is **no downgrade
path**; a schema rollback is a manual, forward-fixing migration.

## 7. Smoke suite

```bash
cd /opt/kelley/apps/api
PYTHON="$PWD/.venv/bin/python" scripts/smoke_handoff.sh          # add --keep-going to run past failures
```

The `PYTHON` override is **required** — the script's default interpreter path
(`venv/bin/python`) does not exist; only `.venv` does. The suite runs ~90
standalone smoke scripts **serially** (several mutate singleton numbering rows)
and finishes with a psql residue sweep that fails the run if fixtures leaked.

The smoke suite is **backend-focused** — it does not exercise the admin/sales SPA
in a browser (see §19).

## 8. Health checks

```bash
curl http://127.0.0.1:8000/api/health
```

`GET /api/health` returns:

- `503` when the database is unreachable (`database: disconnected`) or the schema
  is missing (`schema_missing`).
- `200 status: ok` with `migrations_applied`, `timezone`, `email_transport`, and
  `email_delivery_enabled`.
- `200 status: degraded` with `warnings: [email_delivery_disabled]` when the
  email transport is the null transport (unconfigured email).

> `apps/api/scripts/health_check.sh` is a **stale leftover from a prior project**
> (it checks `bellas-xv-api`, nginx, and `shopbellasxv.com` certs). Do **not**
> use it for Kelley; the health check is the `/api/health` endpoint above.

## 9. systemd status and journals

```bash
systemctl status kelley-backend kelley-public
journalctl -u kelley-backend -n 200 --no-pager
journalctl -u kelley-public  -n 200 --no-pager
```

The backend unit has `Restart=always` — see §20 for the implication.

## 10. install.sh behavior

`sudo bash deploy/install.sh` (run as root) installs the systemd units and
Caddyfile:

1. Validates the candidate Caddyfile before installing it.
2. Backs up the two units and `/etc/caddy/Caddyfile` with timestamped `.bak`
   files (plus a one-time `.orig`).
3. Installs the units + Caddyfile, `systemctl daemon-reload`, restarts
   `kelley-backend` and `kelley-public`, reloads Caddy.
4. Polls readiness (see §11).

It does **not** touch the database and does **not** build application artifacts.

## 11. Readiness polling

After restart, `install.sh` polls until healthy (bounded by
`READINESS_TIMEOUT` / `READINESS_INTERVAL`, default ~30s / 1s):

- `http://127.0.0.1:8000/api/health`
- `http://127.0.0.1:3000/`

## 12. Staged releases, backup, and rollback

Frontend releases are **staged, versioned, and recoverable** (Phase 6). A build
never touches the live artifacts; promotion switches a symlink.

- **Stage** (unprivileged, never touches live) — builds admin + storefront into
  `releases/<git-sha>/`, writes a checksummed manifest, runs preview HTTP checks
  and the browser E2E gate, and marks the manifest `validated`:

  ```bash
  deploy/stage-release.sh --sha <full-git-sha>
  ```

- **Promote** (privileged, in a deployment window) — verifies the validated
  manifest, stops the storefront, atomically switches `apps/admin/dist` and
  `apps/storefront/.next-current` to the release, restarts, and polls readiness;
  it preserves the previous pointers and prints the exact rollback command. The
  **first** promotion moves the current Phase‑2 `dist`/`.next` into
  `releases/phase2-baseline/` so the pre‑restructure build stays recoverable:

  ```bash
  sudo bash deploy/promote-release.sh <release-id>
  ```

- **Rollback** — switch back to a known validated release (or
  `phase2-baseline`); add `--with-backend-phase2` to also restore the Phase‑2
  backend code (`4ab6c5d`):

  ```bash
  sudo bash deploy/rollback-release.sh <release-id> [--with-backend-phase2]
  ```

Atomicity: the **admin** switch is atomic (symlink `rename(2)`). The
**storefront** switch is **not** atomic — `next start` holds its `.next` open, so
the service is stopped, switched, then started; expect a few seconds of
storefront 502 at the proxy during promotion. Old releases are never deleted by
these scripts.

- **Config layer** (systemd units, Caddyfile): `install.sh` keeps timestamped
  `.bak` and one-time `.orig` backups and prints the exact rollback commands.
- **Database**: the runner is forward-only with no downgrade; recovery is a
  forward-fixing migration and/or a database backup restore.

> The legacy `deploy/build.sh --admin`/`--storefront` still build **in place**
> and remain dangerous — use the staged release scripts above for any deploy.

## 13. Caddy validation

```bash
caddy validate --config /etc/caddy/Caddyfile          # validate before install
systemctl reload caddy                                 # apply (install.sh does this)
```

`install.sh` validates the candidate Caddyfile before installing it.

## 14. Module and feature flags

Backend module flags (`apps/api/config/settings.py`), all default **true**;
`core` and `contacts` have no flag (always on):

`MODULE_MESSAGING_ENABLED`, `MODULE_DEALS_ENABLED`, `MODULE_INVENTORY_ENABLED`,
`MODULE_SCHEDULING_ENABLED`, `MODULE_BOOKING_ENABLED`, `MODULE_ANALYTICS_ENABLED`.

Disabling a module unmounts its routers and stops its workers; models still
import (see [ARCHITECTURE.md §4](ARCHITECTURE.md#4-backend-modules)).

## 15. Gmail / Twilio / Meta switches

- **Gmail** — no boolean flag; the Gmail API transport activates when
  `GMAIL_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` / `_REFRESH_TOKEN` are set and takes
  precedence over SMTP. An empty `SMTP_HOST` with no Gmail config yields the null
  transport (email "sent" is dropped; `/api/health` reports `degraded`).
- **Twilio / SMS** — `SMS_SENDING_ENABLED` defaults **false** (outbound SMS is
  hard-disabled until A2P 10DLC approval). Inbound webhook signature checking is
  on by default. There is no Twilio SDK dependency; signature verification is
  implemented in-house.
- **Meta** — `META_CAPI_ENABLED` defaults **false** (outbound CAPI kill switch);
  `META_MESSAGING_ENABLED` defaults **false**. Inbound webhook signature checking
  is on by default.
- **Storefront analytics** — `STOREFRONT_ANALYTICS_ENABLED` defaults **true**.

## 16. Admin and sales hostname behavior

The admin and sales surfaces are the **same static build** in `apps/admin/dist`.
The app self-routes by hostname (`sales.*` → sales). When the
`sales.kelleyautoplex.com` block in the Caddyfile is enabled (it ships
**commented out** — uncomment it and add the DNS record to serve the sales host),
Caddy serves both hosts from that one directory, and a single admin build deploys
both surfaces at once.

## 17. Troubleshooting

**An apparent CORS error on one endpoint is usually a backend 500.** When an
unhandled exception escapes `CORSMiddleware`, the error response carries no
`Access-Control-Allow-Origin` header, so the browser reports it as a CORS block.
If the SPA shows a CORS error on **one** endpoint while others work, it is almost
certainly a 500 — check the backend logs for the traceback, not the CORS config.
Confirm with:

```bash
curl -i -H "Origin: <spa-origin>" https://api.kelleyautoplex.com/<endpoint>
```

A `401`/`404` response that **includes** CORS headers proves the CORS config is
fine and the earlier failure was a server error.

*(This lesson is carried forward from the analytics/comms porting guide, now in
[archive/PORTING_GUIDE_ANALYTICS_COMMS.md](archive/PORTING_GUIDE_ANALYTICS_COMMS.md).)*

**Email shows as `degraded`** in `/api/health`: the null transport is active —
neither Gmail OAuth nor SMTP is configured. Set the Gmail OAuth vars (§4) or
`SMTP_*`.

**Backend `.env` changes have no effect**: flags and env are read at process
start and do not hot-reload; a change requires a service restart inside a deploy
window.

## 18. Release checklist

1. Ensure the intended git ref is checked out (build.sh does not pull).
2. `deploy/build.sh --api` (or the relevant targets) as `deploy`.
3. Run the smoke suite (§7) — it must pass.
4. **Complete the browser E2E gate (§19).**
5. Address the staged-artifact preflight for any frontend deploy (§20).
6. `sudo bash deploy/install.sh` as root (or a scoped restart inside the window).
7. Confirm `/api/health` is `ok` and the storefront root responds (§8, §11).

## 19. Browser E2E release gate

A pinned-Playwright browser E2E suite lives at `apps/admin/e2e/` and runs
**inside the official `mcr.microsoft.com/playwright:v1.61.1-noble` Docker image**,
so it needs no Chromium libraries on the host (the VPS has none, and none are
installed). It builds temporary admin and forced-sales bundles (never the live
dist), mocks the API deterministically for authenticated pages, keeps the
unauthenticated redirects real, and asserts route rendering, lazy-chunk loading,
admin/sales chunk isolation, and the absence of console/page errors and asset
404s across desktop and mobile viewports.

```bash
pnpm --filter ./apps/admin run test:e2e:docker
```

It is a **required release gate** and runs automatically inside
`deploy/stage-release.sh` — a release is only marked validated if it passes.

## 20. Window B (Phase 6) preflight risks

1. **Staged/recoverable artifact promotion — IMPLEMENTED (§12).** Frontend
   releases now stage into `releases/<sha>/`, validate before promotion, and
   switch via symlink with the previous artifacts retained. Use
   `deploy/stage-release.sh` then `deploy/promote-release.sh`; do **not** use the
   in-place `build.sh --admin`/`--storefront` for a deploy.
2. **Browser E2E gate — IMPLEMENTED (§19).** The pinned-Playwright Docker suite
   (`apps/admin/e2e/`) runs as part of `stage-release.sh` and must pass before a
   release is marked validated.
3. **Unexpected backend restart loads current source.** The running backend was
   started before the Phase 3 modularization; `kelley-backend.service` has
   `Restart=always`. A crash, restart, or reboot will bring the backend up on the
   **current modularized source** ahead of the planned Window B. Phase 3/4 are
   well verified, but this is an operational fact to plan around.
