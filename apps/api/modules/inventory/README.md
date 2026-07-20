# inventory module

Inventory module: vehicle catalog, VIN tooling, pricing, special orders.

- **Enable flag:** MODULE_INVENTORY_ENABLED (default true)
- **Router prefixes:** /api/catalog, /api/admin/vin
- **Key services:** catalog_service, public_inventory_service, vin, vin_decode, vin_ocr, pricing, special_order_service
- **Model file:** `database/models/inventory.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** core (document_storage, upload_validation)

Routers live in `modules/inventory/routers/`, services in `modules/inventory/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
