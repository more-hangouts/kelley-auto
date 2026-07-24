# Kelley Autoplex — Day 0 Baseline (Local Boot)

**Date:** 2026-06-25 · **Host:** abject-engineers (50.28.114.31), Ubuntu 24.04, 2c/4GB/80GB
**Repo:** `/opt/kelley` @ `7d1c626` (branch `main`)
**Goal (SPRINT_ROADMAP Day 0):** prove the imported apps run, lock the Kelley naming/env
baseline, and record failures *before* fixing them.

> Status: **Day 0 exit criteria met.** Three apps boot; baseline failures captured below.
> No behavior changes made — Payload/Prisma left in place (rewired Days 4–5).

---

## Running services (all bound to 127.0.0.1 — VS Code forwards the ports)

| Service        | URL                        | How it runs                              | State |
|----------------|----------------------------|------------------------------------------|-------|
| Backend API    | http://127.0.0.1:8000      | uvicorn (venv), single worker            | ✅ `/api/health` ok, db connected, 84 migrations |
| Admin/Sales SPA| http://127.0.0.1:5173      | Vite dev (`npm run dev`)                 | ✅ HTTP 200 |
| Public site    | http://127.0.0.1:3000      | `next dev` (Next 15 + Payload)           | ✅ HTTP 200 (data load fails — see F3) |
| Postgres 16    | 127.0.0.1:5432             | Docker `kelley-dev-postgres`             | ✅ db `kelley_autoplex`, pristine + migrated |
| Redis 7        | 127.0.0.1:6379             | Docker `kelley-dev-redis`                | ✅ |

`/api/health` → `{"status":"ok","database":"connected","migrations_applied":84,"timezone":"America/Chicago"}`

---

## Exit criteria

- [x] Backend health endpoint returns OK.
- [x] Admin SPA opens (HTTP 200 on :5173).
- [x] Admin shell says **Kelley Autoplex** and uses the dark dealership theme
      (`src/theme.js` → `mode: 'dark'`; brand in `DashboardLayout.jsx:75`).
- [x] Sales login/shell says **Kelley Autoplex** (`sales/PinLogin.jsx:186`, `sales/SalesLayout.jsx:83`).
- [x] Public site opens (HTTP 200 on :3000).
- [x] Baseline failures documented with exact command + error (below).

Env/doc checks: VPS spec ($17/mo, 2c/4GB/80GB/3TB, Ubuntu 24.04, 4GB swap) present in
README/SPRINT_ROADMAP/VPS_SETUP/MIGRATION_PLAN. Env examples carry Kelley defaults
(`kelley_user@.../kelley_autoplex`, blank `SESSION_COOKIE_DOMAIN` for localhost, Kelley CORS origins).

---

## Documented baseline failures (record-only; do NOT fix on Day 0)

### F1 — Public site: `pnpm install` / `pnpm run build` fail before reaching Next
```
$ cd frontend && pnpm install
 ERROR  packages field missing or empty
```
Cause: `frontend/pnpm-workspace.yaml` declares a workspace but has **no `packages:`**
field (it only carries a non-standard `allowBuilds:` map). Every modern pnpm rejects this.
Non-destructive workaround used for baseline only: `pnpm install --ignore-workspace`
(install succeeds), and invoke the Next binary directly for build (`pnpm run` re-reads the
workspace file and re-fails). Also present: a stray nested copy at `frontend/reliable-cars/`
(own package.json/lockfile) — inert (not a workspace member), worth removing later.

### F2 — Public site: `next build` fails type-check on Prisma 7
```
$ cd frontend && NODE_OPTIONS=--max-old-space-size=1536 ./node_modules/.bin/next build
 ✓ Compiled successfully in 73s          # Payload itself compiles
Failed to compile.
./src/lib/prisma.ts:1:10
Type error: Module '"@prisma/client"' has no exported member 'PrismaClient'.
```
Cause: Prisma 7 (`@prisma/client@^7`, `@prisma/adapter-pg`) no longer exports `PrismaClient`
from the package root without a generated client (`prisma generate`, changed output path).
Build compiles Payload fine; the failure is the legacy Prisma import. Belongs to the
Payload→backend rewire (Days 4–5); not fixed now.

### F3 — Public site: renders but vehicle data fails (Payload not configured)
```
getVehicles error: Error: missing secret key. A secret key is needed to secure Payload.
  src/lib/api.ts:25  getPayload({ config: configPromise })   payloadInitError: true
```
The homepage serves (HTTP 200) but still uses the **Drivereliable** baseline
(title "Reliable Used Cars — Quality Pre-Owned Vehicles") and reads inventory from Payload.
Payload needs `PAYLOAD_SECRET` + its own `DATABASE_URL`, neither of which is in
`frontend/.env.example`. Expected — the public site is rewired off Payload onto the FastAPI
public API on Days 4–5. Do not "fix" the Payload build early.

### F4 — Admin SPA: residual legacy branding (cosmetic baseline)
In-app shell is Kelley-branded, but the HTML document title is still legacy:
`backend/frontend/index.html` → `<title>Bellas XV</title>`; build emits a `bellas-logo*.svg`
asset; internal `Bellas`/`Quince`/`quinceañera` strings remain across ~10 components
(e.g. `AdminCatalog.jsx`, `EventDetailLayout.jsx`, `NewLeadDialog.jsx`). Consistent with the
README note: internal names change only when their sprint phase requires it.

### F5 — Backend smokes: not isolated; batched run hits append-only audit trigger
```
$ pytest tests/test_auth_smoke.py tests/test_catalog_router_smoke.py tests/test_events_smoke.py \
         tests/test_business_profile_smoke.py tests/test_sales_auth_smoke.py
ERROR collecting tests/test_events_smoke.py
psycopg2.errors.CheckViolation: DELETE on table event_status_change_events is forbidden:
audit tables are append-only            # trigger from migration 063
```
Run **individually, all 5 pass** (they're script-style assertion modules). Run **batched in
one pytest process against a shared DB**, `test_events_smoke.py`'s teardown deletes its
seeded `events`, which cascades a DELETE into the append-only `event_status_change_events`
table → CheckViolation. The smokes need per-test DB isolation (or to be run one at a time).
Reproduced on a fresh DB; not a code regression, an existing test-isolation gap.

---

## Open decision (flagged for "Before Day 2")
Sales shell **attendance / clock-in / time-off** surfaces are **not** hidden for Kelley v1.
They remain wired and are gated by `attendance_gate_enabled`, which defaults **on**
(`BusinessProfile.jsx`: `attendance_gate_enabled !== false`; `SalesProtectedRoute.jsx` routes
to `/clock`). Day 0 task asked to confirm these are hidden "unless the dealership explicitly
wants employee time tracking." Decision needed: leave gated-on, or disable for v1.

---

## Environment notes (how this box was set up for dev — no passwordless sudo)
Provisioning reverted NOPASSWD sudo, so Day 0 used sudo-free paths:
- **Node 20.20.2** via `nvm` (no apt). **pnpm 10.4.1** via corepack (pnpm 11 needs Node 22;
  pnpm 9 rejects the settings-only workspace file).
- **Backend venv**: `python3.12-venv`/ensurepip not installed → created with
  `python3 -m venv .venv --without-pip` then bootstrapped pip via `get-pip.py`.
- **Dev datastores** as clearly-named Docker containers (`kelley-dev-postgres`,
  `kelley-dev-redis`), published to 127.0.0.1 only, `--restart unless-stopped`.
- **`DOCUMENT_STORAGE_ROOT`** overridden to `/opt/kelley/var/uploads` (deploy-owned) for dev;
  production keeps `/var/lib/kelley-autoplex/uploads`. Sanctioned by `.env.example`.
- Secrets generated on-box into `backend/.env` (mode 600): SECRET_KEY, RESCHEDULE/ENRICHMENT
  token secrets, QUOTE_SIGNATURE_KEY, INTEGRATION_TOKEN_KEYS (Fernet).
- A throwaway `kelley_test` DB was used to run the smokes in isolation, then dropped;
  `kelley_autoplex` was dropped/recreated/re-migrated afterward so Day 1 starts pristine.
- Logs: `/opt/kelley/var/logs/{backend,admin-spa,public-site}.log`.

## Re-run cheatsheet
```bash
# datastores
docker start kelley-dev-postgres kelley-dev-redis
# backend
cd /opt/kelley/backend && ./.venv/bin/uvicorn api.server:app --host 127.0.0.1 --port 8000
# admin/sales SPA
source ~/.nvm/nvm.sh; cd /opt/kelley/backend/frontend && npm run dev -- --host 127.0.0.1 --port 5173
# public site (dev; build still fails per F1/F2)
source ~/.nvm/nvm.sh; cd /opt/kelley/frontend && ./node_modules/.bin/next dev -p 3000 -H 127.0.0.1
```
