# scheduling module

Scheduling module: staff shifts, schedules, time-off, attendance/clock,
punches, and the schedule-monitor worker.

- **Enable flag:** MODULE_SCHEDULING_ENABLED (default true)
- **Router prefixes:** /api/sales/{schedule,clock,attendance,time-off}, /api/admin/{schedule,shifts,holidays,time-off,attendance,staff-locations}, /api/sales/appointments
- **Key services:** staff_schedule (+presets/notifications), staff_shifts_admin, shift requests/notifications/expiry, shift_resolver, open_shifts, recurring_availability, auto_scheduler, time_off, staff_holidays_admin, clock_in/selfie/retention, attendance_close/pre_close/review/gate/geo_retention, missing_out_punch_cron, no_show_cron, staff_digest_runner
- **Model file:** `database/models/scheduling.py` (re-exported through the `database.models` facade)
- **Workers:** schedule_monitor
- **Cross-module dependencies:** core only

Routers live in `modules/scheduling/routers/`, services in `modules/scheduling/services/`.
Mounted and (for workers) started through `modules/registry.py`. Cross-module
imports are explicit, e.g. `from modules.core.services import notification_service`.
