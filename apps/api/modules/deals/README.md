# deals module

Deals module: events (deal records), invoices, quotes, payments, portal,
lead applications, special-order/participant/document surfaces.

- **Enable flag:** MODULE_DEALS_ENABLED (default true)
- **Router prefixes:** /api/events, /api/invoices, /api/quotes, /api/payments, /api/special-orders, /api/sales, /api/admin/events, /api/admin/dependencies (core), /portal
- **Key services:** invoice_service, invoice_pdf, quote_service, quote_signature_hmac, discount_snapshot, payment_service, portal_service, portal_email, reminder_runner
- **Model file:** `database/models/deals.py` (re-exported through the `database.models` facade)
- **Workers:** none (reminder_runner runs on the core daily worker)
- **Cross-module dependencies:** core; inventory (catalog lookups on line items); analytics (payment attribution)

Routers live in `modules/deals/routers/`, services in `modules/deals/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Migration-compatibility note

`apps/api/services/` is a permanent compatibility surface for immutable
historical migrations only. Migration 061 imports
`services.integration_tokens.encrypt` and migration 062 imports
`services.quote_signature_hmac.compute_hmac`; both re-export from this module's
current implementation so a fresh migration replay resolves without editing the
(immutable) migration files. New application code and new migrations must use the
current `modules.*` paths or migration-local helpers — do not add further
`services.*` compatibility exports casually (a guard test enforces this).
