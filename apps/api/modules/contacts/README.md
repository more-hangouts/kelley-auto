# contacts module

Contacts module (kernel, non-disableable): contact/customer records, lead
intake + PII, buyer journey, sales lead search.

- **Enable flag:** none (kernel — always enabled)
- **Router prefixes:** /api/contacts
- **Key services:** contact_service, buyer_journey, lead_application_service, lead_pii_crypto, sales_search_service, public_lead_service
- **Model file:** `database/models/contacts.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** core; booking (event promotion on lead intake); analytics (attribution); inventory (public vehicle lookup on public leads)

Routers live in `modules/contacts/routers/`, services in `modules/contacts/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
