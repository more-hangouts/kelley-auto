# @kelley/brand-assets

Intended single source of truth for Kelley Autoplex brand assets (logos,
wordmarks, color tokens) shared across the storefront, admin SPA, and
transactional email.

**Status (Phase 2):** ownership placeholder only — assets have not been
centralized here yet.

## Important: do not break the email asset path

The FastAPI email transport renders the Kelley wordmark from a checked-in
copy at `apps/api/assets/email/kelley-wordmark.png`. That copy is
**intentionally retained inside `apps/api`** so email rendering has no
cross-package or build-time dependency. When brand assets are eventually
centralized here, `apps/api/assets/email/` must keep a checked-in copy (or
a build step must materialize one) — the email path must never depend on
resolving this package at runtime.
