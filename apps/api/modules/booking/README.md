# booking module

Booking module: public appointment booking, events/deals lifecycle,
participants, walk-ins, sales appointments, assignment.

- **Enable flag:** MODULE_BOOKING_ENABLED (default true)
- **Router prefixes:** /api/booking, /api/admin/booking, /api/walk-in-leads, /api/public, /api/sales/walk-ins
- **Key services:** booking_service, booking_contracts, event_service, event_workflow, event_participants, walk_in_service, sales_appointments, appointment_audit, staff_booking_notifications, sales_assignment
- **Model file:** `database/models/booking.py` (re-exported through the `database.models` facade)
- **Workers:** none
- **Cross-module dependencies:** core; contacts (contact upsert)

Routers live in `modules/booking/routers/`, services in `modules/booking/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
