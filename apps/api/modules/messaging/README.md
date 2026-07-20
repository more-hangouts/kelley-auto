# messaging module

Messaging module: omnichannel inbox, web chat, and Twilio/Meta webhooks.

- **Enable flag:** MODULE_MESSAGING_ENABLED (default true)
- **Router prefixes:** /api/inbox, /api/web-chat, /api/webhooks/{twilio,meta}
- **Key services:** inbox_service, web_chat_service, meta_client, meta_signature, twilio_signature, webhook_ingest
- **Model file:** `database/models/messaging.py` (re-exported through the `database.models` facade)
- **Workers:** none (webhook_ingest retention runs on the core daily worker)
- **Cross-module dependencies:** core (notification_routing, phone); booking + contacts + analytics (web chat creates events/contacts and tracks storefront analytics)

Routers live in `modules/messaging/routers/`, services in `modules/messaging/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
