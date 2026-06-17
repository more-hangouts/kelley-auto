# Kelley Autoplex

Used-car dealership platform. Combines the **drivereliable** Next.js marketing site
(`frontend/`) with the **bellasxv** FastAPI + React/MUI back office (`backend/`),
rebranded for Kelley Autoplex.

The backend is the single system of record (inventory, CRM/deals, quotes, invoices,
payments, users, notifications). The public site reads everything from the backend
over HTTP. See **[MIGRATION_PLAN.md](MIGRATION_PLAN.md)** for the full build runbook.

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

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# create the database, then run migrations (forward-only runner)
python -m database.migrations.runner
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
