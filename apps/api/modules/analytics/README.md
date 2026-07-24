# analytics module

Analytics module: storefront analytics, attribution, Meta CAPI, sales
activity, dashboards, global search.

- **Enable flag:** MODULE_ANALYTICS_ENABLED (default true)
- **Router prefixes:** /api/dashboard, /api/admin/{storefront-analytics,sales-activity}, /api/search
- **Key services:** storefront_analytics_service, meta_capi_service, sales_activity, dashboard, search_service
- **Model file:** `database/models/analytics.py` (re-exported through the `database.models` facade)
- **Workers:** none (meta_capi tick runs on the scheduling schedule_monitor worker, gated by META_CAPI_ENABLED)
- **Cross-module dependencies:** booking, core

Routers live in `modules/analytics/routers/`, services in `modules/analytics/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Cross-module coupling note

The dependency list above reflects the application's actual (pre-existing) coupling, made visible by the module boundaries — Phase 3 relocated code and rewrote import paths without adding any cross-domain dependency. The domain graph is not strictly acyclic: several routers are inherently cross-cutting (e.g. the global search and cross-entity archive surfaces, the dashboard's read of booking workflow statuses). Because every module package imports unconditionally, these imports resolve regardless of enable flags; disabling a module only unmounts its routers and stops its workers. Untangling the remaining coupling into a strictly acyclic graph would require behavior-changing refactors and is intentionally out of Phase 3 scope.
