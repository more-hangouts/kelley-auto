# @kelley/shared-types

Canonical cross-app domain enums (event types, inbox channels) shared
between the admin SPA and the storefront.

**Status (Phase 2):** scaffolded, not yet consumed by the apps. Wiring the
frontends to import from here is deferred to a later phase so the monorepo
move stays free of application-internal refactoring. The Python API in
`apps/api` remains the runtime authority for validating these values.
