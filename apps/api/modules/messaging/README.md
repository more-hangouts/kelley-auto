# messaging module

Messaging module: omnichannel inbox, web chat, and Twilio/Meta webhooks.

- **Enable flag:** MODULE_MESSAGING_ENABLED (default true)
- **Router prefixes:** /api/inbox, /api/web-chat, /api/webhooks/{twilio,meta}
- **Key services:** inbox_service, web_chat_service, meta_client, meta_signature, twilio_signature, webhook_ingest
- **Model file:** `database/models/messaging.py` (re-exported through the `database.models` facade)
- **Workers:** none (webhook_ingest retention runs on the core daily worker)
- **Cross-module dependencies:** analytics, booking, contacts, core

Routers live in `modules/messaging/routers/`, services in `modules/messaging/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Cross-module coupling note

The dependency list above reflects the application's actual (pre-existing) coupling, made visible by the module boundaries — Phase 3 relocated code and rewrote import paths without adding any cross-domain dependency. The domain graph is not strictly acyclic: several routers are inherently cross-cutting (e.g. the global search and cross-entity archive surfaces, the dashboard's read of booking workflow statuses). Because every module package imports unconditionally, these imports resolve regardless of enable flags; disabling a module only unmounts its routers and stops its workers. Untangling the remaining coupling into a strictly acyclic graph would require behavior-changing refactors and is intentionally out of Phase 3 scope.
