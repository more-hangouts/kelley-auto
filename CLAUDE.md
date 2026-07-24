# CLAUDE.md — engineering rules

Practical guardrails for working in this repository. For system design see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); for production procedures see
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Repository map

```text
apps/api/         FastAPI + SQLAlchemy + workers — system of record (Python .venv)
apps/admin/       Vite/React/MUI admin + sales SPA
apps/storefront/  Next.js public site
packages/         @kelley/shared-types (scaffolded), @kelley/brand-assets (placeholder)
deploy/           build.sh, install.sh, systemd units, Caddyfile, env templates
docs/             canonical architecture/operations docs; docs/archive/ = historical
```

The pnpm workspace covers `apps/admin`, `apps/storefront`, `packages/*`.
`apps/api` is a standalone Python app with its own `.venv` (not a workspace
member).

## JavaScript

- **Use pnpm, from the repository root.** pnpm 10 is pinned via `packageManager`.
  Do not use npm — the lockfile and `onlyBuiltDependencies` sandbox make npm
  installs wrong. Run app scripts with `pnpm --filter ./apps/<app> <script>`.

## Python / backend

### Tests are standalone scripts, not pytest

Backend tests are `tests/test_*_smoke.py` scripts that mint their own fixtures
and clean up; helpers are named `check_*` specifically so pytest does not collect
them. **Do not run pytest.** Run one directly, or run the whole handoff suite:

```bash
cd apps/api
.venv/bin/python tests/test_<name>_smoke.py                     # one smoke
PYTHON="$PWD/.venv/bin/python" scripts/smoke_handoff.sh          # full suite
```

The `PYTHON` override on the suite is **required** — the script's default
interpreter path does not exist (only `.venv` does). Smokes must run
**serially** (several mutate singleton numbering rows). Prefer running the
focused smokes for the domain you touched over the full ~90-test suite.

### Migrations are append-only and immutable

Migrations in `apps/api/database/migrations/` (`001_*.py` … `097_*.py`) are
historical artifacts. **Never edit, renumber, squash, or otherwise rewrite an
existing migration** — a replay must reproduce production byte-for-byte. New
schema work is a new numbered file. The runner is forward-only
(`python -m database.migrations.runner`); there is no downgrade.

`apps/api/services/` is the **narrow, permanent migration-compatibility surface**
for migrations `061`/`062` only. It is **not** an application service directory.
New application code must import from `modules.*`; a guard smoke
(`test_services_compat_guard_smoke.py`) fails if app source imports `services.*`.

### Where backend code belongs

- **Models** → `apps/api/database/models/<domain>.py`, **and** re-export in
  `database/models/__init__.py` (add to `__all__`). The facade must keep
  `from database.models import <Name>` working, and every model imports
  unconditionally so its table registers.
- **Routers** → `apps/api/modules/<domain>/routers/`, then register the mount in
  `modules/registry.py`. **Routers mount synchronously at app construction;
  never mount a router inside the lifespan.**
- **Services** → `apps/api/modules/<domain>/services/`. Services do not import
  FastAPI. Routers contain no business logic.
- **Workers** start in the lifespan via the registry — attach a `WorkerDef` to
  its owning module; do not start tasks ad hoc.

### Modules

Eight domains: `core`, `contacts`, `messaging`, `deals`, `inventory`,
`scheduling`, `booking`, `analytics`. **`core` and `contacts` are kernel modules
and cannot be disabled.** The other six have `MODULE_<NAME>_ENABLED` flags
(default true) in `config/settings.py`. Disabling a module only unmounts its
routers and stops its workers — **its package and models still import.** Do not
make module packages conditionally importable.

Cross-domain imports exist and are (for now) accepted; the dependency graph is
not acyclic. Don't add new cross-domain coupling casually, but untangling the
existing coupling is out of scope — see
[docs/ARCHITECTURE.md §15](docs/ARCHITECTURE.md#15-known-cross-domain-coupling).

## Admin / sales SPA

- **New API calls** go in the matching domain module under
  `apps/admin/src/services/api/` (`auth`, `core`, `contacts`, `deals`, `booking`,
  `analytics`, `documents`, `billing`, `businessProfile`, `dashboard`,
  `inventory`, `sales`, `staff`, `attendance`, `scheduling`, `messaging`). Every
  request uses the **single shared Axios instance** in `client.js` — there must
  never be a second `axios.create`. **Do not import the barrel `index.js` from
  inside `services/api/`** (cycle/TDZ risk).
- The export surface is frozen at **227 named + 1 default** in
  `EXPORTS.frozen`. If you add/remove/rename an API function, update
  `EXPORTS.frozen` in the same change and run
  `pnpm --filter ./apps/admin check:api-exports`.
- **New pages are `React.lazy` + `Suspense`** (see `App.jsx` and
  `sales/SalesApp.jsx`); `SalesApp` is itself lazy. Keep the convention so admin
  and sales never cross-load each other's chunks. `vite.config.js` `manualChunks`
  owns vendor grouping — don't put app source into a vendor chunk.

## Frontend build safety

- **`vite build`'s default output is `apps/admin/dist`, which Caddy serves live**
  in production (admin + sales). A normal build publishes immediately and a
  broken build takes down the live surfaces. **For verification, always build to
  a temporary directory:**
  `pnpm --filter ./apps/admin exec vite build --outDir "$(mktemp -d)" --manifest`.
- Likewise, `next build` writes `apps/storefront/.next`, the tree the running
  `next start` serves. Do not rebuild `.next` in place against a live process.

## Git and process

- **Preserve existing worktree changes** — never reset, stash, discard, or
  amend work you did not create. Stage explicit paths; never `git add .`.
- Keep tests **proportional to the change** — run the focused smokes for what you
  touched.
- **Do not restart production services** (`systemctl restart kelley-backend
  kelley-public`) or run production builds outside an explicit deployment window.
  Backend env/flags are read at process start and do not hot-reload.
