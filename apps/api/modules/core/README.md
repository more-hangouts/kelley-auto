# core module

Core module (kernel, non-disableable): auth, users/staff, business profile,
notifications infra, webhooks/cron state, document storage, shared primitives
(phone, confirmation codes, booking tokens, business time).

- **Enable flag:** none (kernel — always enabled)
- **Router prefixes:** /api/auth, /api/admin/{me,staff,sales-staff,dependencies,archive,cron-health,notification-subscribers}, /api/business-profile, /api/sales/{auth,me/notifications,search}
- **Key services:** auth (sales_auth, password_reset, sales_staff), business_profile_service, business_time, document_storage, upload_validation, integration_tokens, cron_state, record_dependencies, activity_log, notification_service/routing/templates/preferences/subscriber, email_transport, sms_transport, and shared primitives phone, confirmation_codes, booking_tokens
- **Model file:** `database/models/core.py` (re-exported through the `database.models` facade)
- **Workers:** notifications, daily
- **Cross-module dependencies:** none (core is the dependency floor)

Routers live in `modules/core/routers/`, services in `modules/core/services/`.
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
