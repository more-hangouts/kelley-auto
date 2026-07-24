# booking module

Booking module: public appointment booking, events/deals lifecycle,
participants, walk-ins, sales appointments, assignment.

- **Enable flag:** MODULE_BOOKING_ENABLED (default true)
- **Router prefixes:** /api/booking, /api/admin/booking, /api/walk-in-leads, /api/public, /api/sales/walk-ins
- **Key services:** booking_service, booking_contracts, event_service, event_workflow, event_participants, walk_in_service, sales_appointments, appointment_audit, staff_booking_notifications, sales_assignment
- **Model file:** `database/models/booking.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** analytics, contacts, core, inventory, scheduling

Routers live in `modules/booking/routers/`, services in `modules/booking/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.

## Cross-module coupling note

The dependency list above reflects the application's actual (pre-existing) coupling, made visible by the module boundaries — Phase 3 relocated code and rewrote import paths without adding any cross-domain dependency. The domain graph is not strictly acyclic: several routers are inherently cross-cutting (e.g. the global search and cross-entity archive surfaces, the dashboard's read of booking workflow statuses). Because every module package imports unconditionally, these imports resolve regardless of enable flags; disabling a module only unmounts its routers and stops its workers. Untangling the remaining coupling into a strictly acyclic graph would require behavior-changing refactors and is intentionally out of Phase 3 scope.
