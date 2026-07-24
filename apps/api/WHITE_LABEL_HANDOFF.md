# White-Label Handoff — Strategy

This is the strategy document for adapting the Bella's XV platform to a new
client. It commits the deployment model, enumerates what each new client gets,
and names the trigger that would justify revisiting the model later.

Step-by-step provisioning lives in
[`docs/CLIENT_DEPLOYMENT_RUNBOOK.md`](docs/CLIENT_DEPLOYMENT_RUNBOOK.md) (Phase
H3 deliverable). The rebrand surface inventory lives in
[`docs/WHITE_LABEL_REBRAND_SURFACE.md`](docs/WHITE_LABEL_REBRAND_SURFACE.md)
(Phase H2 deliverable). Both are pending at the time this strategy doc was
written; H1 ships the decision so H2 and H3 know what they're building toward.

## Decision

Each new client ships as an independent deployment from one shared hardened
codebase. The codebase is this repository, with the Phase A through G security
remediation applied. The repository stays single-tenant in its data model; the
"multi-tenant" story is solved at the operating-system layer by running a
separate deployment per client.

We are not doing tenant_id columns. We are not doing Postgres row-level
security. We are not retrofitting the schema. If those become necessary later,
the *Future pivot* section below names the conditions under which we would
revisit.

## Why this and not multi-tenant

The original Phase H plan in `SECURITY_REMEDIATION_PLAN.md` assumed a
tenant_id + RLS multi-tenant retrofit. We rejected it for the first 1-3
clients on three grounds:

1. **Data isolation is automatic.** Separate databases on separate operating
   systems cannot leak across each other through a missed `WHERE tenant_id =`
   clause, because there is no shared connection that could see both. Every
   SQL query is already correctly scoped to one tenant simply by virtue of
   which connection ran it.
2. **The audit applies per deployment.** Every hardening slice that shipped in
   Phases A through G — TLS tightening, rate limits, encrypted integration
   tokens, audit append-only triggers, JWT migration, password reset, upload
   validation, cookie auth, systemd sandbox, scoped sudo, file permissions,
   fail2ban, pg_hba lockdown, retention sweeps — applies cleanly to a fresh
   deployment with zero new code paths. A tenant_id retrofit would have meant
   re-validating every one of those slices under cross-tenant query patterns.
3. **Retrofit risk is high, near-term value is low.** Adding tenant_id across
   a codebase built single-tenant means touching every service-layer query,
   adding a context propagation primitive, writing RLS policies, and adding
   regression coverage for cross-tenant leakage. That is multiple weeks of
   work whose value only materializes at the third or fourth client. For one
   or two clients, a separate deployment costs less wall-clock time and ships
   with stronger isolation guarantees.

The tradeoff we accepted: operating cost scales linearly with client count.
Each security patch must be applied N times. Each deployment has its own
backups, its own monitoring, its own DNS, its own TLS certs. That is a real
cost. It is the right cost to pay until client count justifies otherwise.

## Per-client deployment boundaries

Every new client deployment gets its own:

- **VPS** (or, at minimum, an isolated database instance on shared
  infrastructure — see the H3 runbook for the per-DB-on-shared-VPS variant if
  full per-VPS provisioning is not justified).
- **Postgres database**, with its own role, its own `pg_hba.conf` posture per
  F7, and its own migration history.
- **`.env` file** with newly minted secrets. No secret is ever copied from
  one deployment to another. Each deployment generates its own:
  - `SECRET_KEY` (JWT signing)
  - `INTEGRATION_TOKEN_KEYS` (Fernet keys for integration-token encryption,
    per C1)
  - `QUOTE_SIGNATURE_KEY` (HMAC for signed-quote evidence, per C3)
  - `RESCHEDULE_TOKEN_SECRET` and `ENRICHMENT_TOKEN_SECRET` (booking-link
    signing, per G1)
- **Domain set** — typically four hosts: marketing, admin, sales, api. Each
  with its own certbot cert. The cookie domain (`SESSION_COOKIE_DOMAIN`, per
  D3) is set to the client's apex so admin/sales/api can share auth state
  without cross-client leakage.
- **Storage paths** for document uploads (`DOCUMENT_STORAGE_ROOT`), per-VPS
  filesystem, mode 750 per F4.
- **Backup destination**. No client backups land in another client's
  directory. Backup retention and offsite copy policy is per-client.
- **Outbound channels**: SMTP credentials, Twilio SID/token/number, Meta
  Pixel ID, Google Ads conversion tokens, Plausible domain. None of these are
  shared. If a client doesn't use a given channel, its env vars stay unset.
- **Monitoring + alerting** scoped to that VPS. The fail2ban jails from F5
  watch the client's own access log. Audit append-only triggers from C4 fire
  on the client's own DB.

What is *not* per-client: the source code. One repository, one set of tests,
one CI workflow. A security patch lands in the repo, then propagates to each
deployment as a `git pull` + `pip install -r requirements.txt` + restart.

## What "white-label" actually means here

The first new-client deployment is going to need cosmetic and language changes
on top of the secure baseline. That work splits into two buckets:

- **Already parameterized** — the deployment can change this without a code
  edit. Most business identity lives in the `business_profiles` table
  (logo, business name, contact info, default invoice/quote terms). Domain
  routing lives in `.env` and nginx config. Outbound channel config lives in
  `.env`.
- **Currently hardcoded** — the deployment requires either a code edit or a
  follow-up parameterization slice. Examples likely include the event-status
  vocabulary in `services/event_workflow.py`, the boutique-specific copy in
  customer-facing templates, the workflow language in some admin views, and
  the booking-widget questions in `widgets/`.

Phase H2 (rebrand surface audit) produces the definitive inventory of which
bucket each touchpoint falls into. Phase H3 (runbook) tells the deploying
operator how to handle each bucket during onboarding. Neither H2 nor H3
performs the parameterization work itself. That work is justified slice by
slice as real second-client requirements surface, not speculatively before the
first new client proves what actually matters.

## Future pivot

Revisit a tenant_id + RLS multi-tenant retrofit when any of these triggers
fires:

1. **Client count makes per-deploy ops painful.** Working number: five or
   more active deployments. At that point, applying a security patch to
   every deployment becomes a meaningful fraction of a week, and the
   per-deployment monitoring surface stops fitting in one operator's head.
2. **Shipping a security patch takes more than one work day.** If the
   per-deployment patch loop crosses that threshold even with fewer than
   five clients, the operational cost has already overtaken the retrofit
   cost.
3. **A feature genuinely benefits from cross-tenant aggregation.** Examples:
   cross-client analytics, a shared product catalog, a global search across
   all client data. If product strategy ever wants any of those, the
   per-deploy model becomes structurally wrong.

When any trigger fires, open a new major phase modeled on the original
multi-tenant H1-H5 plan. The first slice of that phase produces a fresh
design doc that takes the then-current schema as input; the rest follow as
written, with the benefit that the audit hardening is already in place on
every deployment that needs to be migrated.

## What's included in the codebase

A deployment of this codebase brings:

- Backend FastAPI source: `api/`, `services/`, `database/`, `workers/`,
  `config/`
- Frontend React source: `frontend/src/`, `frontend/public/`, build config
- Public widgets: `widgets/`
- Customer + PDF templates: `templates/`
- Smoke tests: `tests/`
- Utility scripts: `scripts/`
- Reusable docs: `README.md`, `docs/`, `INFRASTRUCTURE.md`, `VPS_HARDENING.md`
- The full security remediation history in `SECURITY_REMEDIATION_PLAN.md` —
  Phases A through G shipped, D complete, F is 6/7 with F3 (SSH source IP
  allowlist / VPN gate) deferred and accepted as residual risk, parking lot
  closed-or-accepted. Every new deployment inherits this baseline.

Heavy local/runtime folders are intentionally excluded from version control:
`venv/`, `frontend/node_modules/`, `frontend/dist/`, `.git/` of the local
clone, caches, logs, and local environment files.

## Most useful docs for a new client deployment

- [`SECURITY_REMEDIATION_PLAN.md`](SECURITY_REMEDIATION_PLAN.md) — the
  hardening baseline every deployment must apply
- [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md) — VPS layout and provider notes
- [`VPS_HARDENING.md`](VPS_HARDENING.md) — operating-system hardening
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system-level architecture
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema overview and migration model
- [`docs/CRM.md`](docs/CRM.md), [`docs/BOOKING.md`](docs/BOOKING.md) —
  domain models
- [`docs/SALES_PORTAL_PHASES.md`](docs/SALES_PORTAL_PHASES.md),
  [`docs/INVOICING_PHASES.md`](docs/INVOICING_PHASES.md) — feature-area
  histories
- [`docs/TESTING.md`](docs/TESTING.md) — the smoke pattern every deployment
  inherits

Once H2 and H3 ship, this list will be joined by
`docs/WHITE_LABEL_REBRAND_SURFACE.md` and
`docs/CLIENT_DEPLOYMENT_RUNBOOK.md`, which are the two artifacts an operator
actually opens when standing up a new client.
