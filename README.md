# Kelley Autoplex

Used-car dealership platform. Combines the **drivereliable** Next.js marketing site
(`frontend/`) with the **bellasxv** FastAPI + React/MUI back office (`backend/`),
rebranded for Kelley Autoplex.

The backend is the single system of record (inventory, CRM/deals, quotes, invoices,
payments, users, notifications). The public site reads everything from the backend
over HTTP.

**Docs:**
- **[MIGRATION_PLAN.md](MIGRATION_PLAN.md)** — phased build runbook (Phases 0–8).
- **[SPRINT_ROADMAP.md](SPRINT_ROADMAP.md)** — day-by-day MVP sprint (Days 0–10) with
  edge cases and a risk register. Same work, scheduled.
- **[VPS_SETUP.md](VPS_SETUP.md)** — production VPS provisioning, hardening, swap,
  and memory-leak mitigation (Phase 7 / Day 10, deferred until the server exists).

> **Status:** local build only — no server/hosting yet. Everything is configured via
> `.env` so a purchased server slots in later with only env + DNS changes.

## Layout

```text
kelley-auto/
├─ frontend/          # Next.js 15 public site (Payload/Prisma removed in Phase 4)
├─ backend/           # FastAPI API + workers + SQLAlchemy (system of record)
│  └─ frontend/       # React 19 + MUI admin & salesman SPA (Vite)
└─ MIGRATION_PLAN.md  # phased runbook
```

## Prerequisites

- Node 20+ and **pnpm** (public site) / **npm** (admin SPA)
- Python 3.11+
- PostgreSQL 14+ and Redis running locally

## Local dev

Copy the env templates first:

```bash
cp backend/.env.example          backend/.env
cp backend/frontend/.env.example backend/frontend/.env.local
cp frontend/.env.example         frontend/.env.local
```

### 1. Backend API (FastAPI) — http://127.0.0.1:8000

First create the Postgres role + database that match `DATABASE_URL` in
`backend/.env.example` (do this once; Redis just needs to be running):

```bash
# defaults match backend/.env.example — change both places if you prefer other creds
createuser bellas_xv_user --pwprompt   # set password: bellas_xv_pass
createdb   bellas_xv --owner=bellas_xv_user
redis-cli ping   # -> PONG (start redis-server if not running)
```

Then boot the API:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m database.migrations.runner          # forward-only migration runner
python scripts/seed_admin.py                  # create the first admin user (interactive)
uvicorn api.server:app --reload --host 127.0.0.1 --port 8000
```

### 2. Admin / salesman SPA (Vite) — http://127.0.0.1:5173

```bash
cd backend/frontend
npm install
npm run dev            # admin app; set VITE_FORCE_SUBDOMAIN=sales for the salesman portal
```

### 3. Public site (Next.js) — http://127.0.0.1:3000

```bash
cd frontend
pnpm install
pnpm dev
```

## Notes

- This is a **baseline import**: `frontend/` still contains Payload/Prisma and
  `backend/` still uses Bella's branding. Both are rebranded/removed in later phases
  per the migration plan — do not delete anything ahead of its phase.
- Secrets, DB URLs, SMTP/Twilio, cookie domains, and CORS origins all come from
  `backend/.env`. Generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
