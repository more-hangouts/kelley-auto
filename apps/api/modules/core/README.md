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
