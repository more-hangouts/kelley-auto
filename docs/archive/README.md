# Archived documentation

These documents are **historical and not authoritative.** They are retained for
context — completed build plans, provisioning runbooks, and baselines from
earlier phases of the project. They may describe old paths, hostnames, product
branding (Bella's XV), or a pre-monorepo layout that no longer matches the code.

For current, authoritative documentation see:

- [../../README.md](../../README.md) — orientation and quickstart
- [../ARCHITECTURE.md](../ARCHITECTURE.md) — system design and boundaries
- [../OPERATIONS.md](../OPERATIONS.md) — production procedures
- [../../CLAUDE.md](../../CLAUDE.md) — engineering rules

## Contents

| File | What it was |
|---|---|
| [BASELINE_DAY0.md](BASELINE_DAY0.md) | Day-0 boot snapshot of the initial baseline |
| [MIGRATION_PLAN.md](MIGRATION_PLAN.md) | Phased (0–8) build runbook for the original migration |
| [SPRINT_ROADMAP.md](SPRINT_ROADMAP.md) | Day 0–10 MVP sprint plan with risk register |
| [STORE_FRONT_ANALYTICS_AND_CAPI_PLAN.md](STORE_FRONT_ANALYTICS_AND_CAPI_PLAN.md) | Storefront analytics + Meta CAPI plan (shipped) |
| [VPS_SETUP.md](VPS_SETUP.md) | Earlier VPS provisioning plan (describes a Docker Compose approach; production actually runs systemd + Caddy — see [../OPERATIONS.md](../OPERATIONS.md)) |
| [PORTING_GUIDE_ANALYTICS_COMMS.md](PORTING_GUIDE_ANALYTICS_COMMS.md) | Analytics/comms porting reference for a follow-on project, with a 21-row pitfalls table. Its CORS-looks-like-500 lesson is carried into [../OPERATIONS.md](../OPERATIONS.md#17-troubleshooting) |

Backend-internal historical references remain under
[../../apps/api/docs/](../../apps/api/docs/); a few there carry a "superseded"
banner where they would otherwise contradict the current root docs.
