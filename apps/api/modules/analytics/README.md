# analytics module

Analytics module: storefront analytics, attribution, Meta CAPI, sales
activity, dashboards, global search.

- **Enable flag:** MODULE_ANALYTICS_ENABLED (default true)
- **Router prefixes:** /api/dashboard, /api/admin/{storefront-analytics,sales-activity}, /api/search
- **Key services:** storefront_analytics_service, meta_capi_service, sales_activity, dashboard, search_service
- **Model file:** `database/models/analytics.py` (re-exported through the `database.models` facade)
- **Workers:** none (meta_capi tick runs on the scheduling schedule_monitor worker, gated by META_CAPI_ENABLED)
- **Cross-module dependencies:** core; booking (dashboard reads event workflow statuses / shop timezone)

Routers live in `modules/analytics/routers/`, services in `modules/analytics/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
