# inventory module

Inventory module: vehicle catalog, VIN tooling, pricing, special orders.

- **Enable flag:** MODULE_INVENTORY_ENABLED (default true)
- **Router prefixes:** /api/catalog, /api/admin/vin
- **Key services:** catalog_service, public_inventory_service, vin, vin_decode, vin_ocr, pricing, special_order_service
- **Model file:** `database/models/inventory.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** core

Routers live in `modules/inventory/routers/`, services in `modules/inventory/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Cross-module coupling note

The dependency list above reflects the application's actual (pre-existing) coupling, made visible by the module boundaries — Phase 3 relocated code and rewrote import paths without adding any cross-domain dependency. The domain graph is not strictly acyclic: several routers are inherently cross-cutting (e.g. the global search and cross-entity archive surfaces, the dashboard's read of booking workflow statuses). Because every module package imports unconditionally, these imports resolve regardless of enable flags; disabling a module only unmounts its routers and stops its workers. Untangling the remaining coupling into a strictly acyclic graph would require behavior-changing refactors and is intentionally out of Phase 3 scope.
