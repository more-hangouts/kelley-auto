# contacts module

Contacts module (kernel, non-disableable): contact/customer records, lead
intake + PII, buyer journey, sales lead search.

- **Enable flag:** none (kernel — always enabled)
- **Router prefixes:** /api/contacts
- **Key services:** contact_service, buyer_journey, lead_application_service, lead_pii_crypto, sales_search_service, public_lead_service
- **Model file:** `database/models/contacts.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** analytics, booking, core, inventory

Routers live in `modules/contacts/routers/`, services in `modules/contacts/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Cross-module coupling note

The dependency list above reflects the application's actual (pre-existing) coupling, made visible by the module boundaries — Phase 3 relocated code and rewrote import paths without adding any cross-domain dependency. The domain graph is not strictly acyclic: several routers are inherently cross-cutting (e.g. the global search and cross-entity archive surfaces, the dashboard's read of booking workflow statuses). Because every module package imports unconditionally, these imports resolve regardless of enable flags; disabling a module only unmounts its routers and stops its workers. Untangling the remaining coupling into a strictly acyclic graph would require behavior-changing refactors and is intentionally out of Phase 3 scope.
