"""Explicit API module registry (Phase 3).

Router registration and worker startup used to be a flat wall of
``app.include_router(...)`` / ``asyncio.create_task(...)`` calls in
``api/server.py``. This registry names the application's domains and, for each,
its router mounts and background workers, so ``server.py`` can iterate an
explicit list instead of hand-listing every mount.

Design constraints (Phase 3):
- Router *mounting* happens synchronously at app construction (see
  ``iter_router_mounts``); workers start in the lifespan (see
  ``iter_enabled_workers``). Never mount routers inside the lifespan.
- Every module package imports unconditionally so SQLAlchemy models and
  string relationships stay discoverable and import errors surface at boot.
  A disabled module only means its *routers* are not mounted and its *workers*
  do not start — its Python package and models still import.
- ``enabled_flag=None`` marks a kernel module (core, contacts) that can never
  be disabled. Optional modules name a ``config.settings`` boolean that
  defaults True, so production behavior is unchanged until someone opts out.
- The registry is an explicit, hand-maintained list. No filesystem scanning,
  no dynamic imports, no plugin discovery.

In this commit the mounts still reference routers at their historical flat
``api.routers.*`` paths; later commits move each domain's routers/services into
``modules/<domain>/`` and update only the import lines here. The *order* of
``MOUNTS`` reproduces the historical server.py registration order exactly, which
keeps the generated OpenAPI path ordering byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from fastapi import APIRouter

# Routers, imported at their current flat paths (unchanged in this commit).
from api.routers import admin_archive as admin_archive_router
from modules.scheduling.routers import admin_attendance as admin_attendance_router
from modules.booking.routers import admin_booking as admin_booking_router
from modules.booking.routers import admin_booking_settings as admin_booking_settings_router
from api.routers import admin_cron_health as admin_cron_health_router
from api.routers import admin_dependencies as admin_dependencies_router
from api.routers import admin_events as admin_events_router
from modules.scheduling.routers import admin_holidays as admin_holidays_router
from api.routers import admin_me as admin_me_router
from api.routers import admin_notification_subscribers as admin_notification_subscribers_router
from modules.scheduling.routers import admin_open_shifts as admin_open_shifts_router
from modules.analytics.routers import admin_sales_activity as admin_sales_activity_router
from api.routers import admin_sales_staff as admin_sales_staff_router
from modules.scheduling.routers import admin_schedule as admin_schedule_router
from modules.scheduling.routers import admin_shift_requests as admin_shift_requests_router
from modules.scheduling.routers import admin_shifts as admin_shifts_router
from api.routers import admin_staff as admin_staff_router
from modules.scheduling.routers import admin_staff_locations as admin_staff_locations_router
from modules.analytics.routers import admin_storefront_analytics as admin_storefront_analytics_router
from modules.scheduling.routers import admin_time_off as admin_time_off_router
from api.routers import auth as auth_router
from modules.booking.routers import booking as booking_router
from api.routers import business_profile as business_profile_router
from modules.inventory.routers import catalog as catalog_router
from modules.contacts.routers import contacts as contacts_router
from modules.analytics.routers import dashboard as dashboard_router
from api.routers import event_documents as event_documents_routers
from api.routers import event_participants as event_participants_router
from api.routers import events as events_router
from modules.messaging.routers import inbox as inbox_router
from api.routers import invoices as invoices_routers
from api.routers import payments as payments_routers
from api.routers import portal as portal_routers
from modules.booking.routers import public_site as public_site_router
from api.routers import quotes as quotes_routers
from api.routers import sales as sales_router
from modules.scheduling.routers import sales_appointments as sales_appointments_router
from api.routers import sales_assignment as sales_assignment_router
from modules.scheduling.routers import sales_attendance as sales_attendance_router
from api.routers import sales_auth as sales_auth_router
from modules.scheduling.routers import sales_clock as sales_clock_router
from api.routers import sales_notifications as sales_notifications_router
from modules.scheduling.routers import sales_open_shifts as sales_open_shifts_router
from modules.scheduling.routers import sales_schedule as sales_schedule_router
from api.routers import sales_search as sales_search_router
from modules.scheduling.routers import sales_shift_requests as sales_shift_requests_router
from modules.scheduling.routers import sales_time_off as sales_time_off_router
from modules.booking.routers import sales_walk_ins as sales_walk_ins_router
from modules.analytics.routers import search as search_router
from api.routers import special_orders as special_orders_routers
from modules.inventory.routers import vin_decode as vin_decode_router
from modules.booking.routers import walk_in_leads as walk_in_leads_router
from modules.messaging.routers import web_chat as web_chat_router
from modules.messaging.routers import webhooks_meta as webhooks_meta_router
from modules.messaging.routers import webhooks_twilio as webhooks_twilio_router

# Workers, imported at their current flat paths (unchanged in this commit).
from workers.daily import run_loop as run_daily_loop
from workers.notifications import run_loop as run_notifications_loop
from workers.schedule_monitor import run_loop as run_schedule_monitor_loop


@dataclass(frozen=True)
class RouterMount:
    """A single ``app.include_router`` call, captured so the registry can
    replay it verbatim. ``include_kwargs`` carries every kwarg the historical
    call used (today always ``prefix`` + ``tags``); it is an open mapping so a
    future mount can add ``dependencies``/``responses``/``include_in_schema``
    without changing this type."""

    router: APIRouter
    include_kwargs: Mapping[str, Any]
    module: str


@dataclass(frozen=True)
class WorkerDef:
    """A background worker started in the lifespan. ``runner`` is the coroutine
    function invoked as ``runner(stop_event)`` — matching the historical
    ``asyncio.create_task(run_loop(stop_event))`` contract."""

    name: str
    runner: Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class ModuleDef:
    """An application domain. ``enabled_flag`` is the name of a boolean on
    ``config.settings`` (default True) that gates the module; ``None`` marks a
    kernel module that is always enabled and cannot be disabled."""

    name: str
    enabled_flag: str | None = None
    workers: tuple[WorkerDef, ...] = ()


# The eight application domains. Workers are attached to their owning module;
# core owns notifications + the cross-domain daily aggregator, scheduling owns
# the schedule monitor. Optional modules name a default-True settings flag.
MODULES: tuple[ModuleDef, ...] = (
    ModuleDef(
        "core",
        enabled_flag=None,
        workers=(
            WorkerDef("notifications", run_notifications_loop),
            WorkerDef("daily", run_daily_loop),
        ),
    ),
    ModuleDef("contacts", enabled_flag=None),
    ModuleDef("messaging", enabled_flag="MODULE_MESSAGING_ENABLED"),
    ModuleDef("deals", enabled_flag="MODULE_DEALS_ENABLED"),
    ModuleDef("inventory", enabled_flag="MODULE_INVENTORY_ENABLED"),
    ModuleDef(
        "scheduling",
        enabled_flag="MODULE_SCHEDULING_ENABLED",
        workers=(WorkerDef("schedule_monitor", run_schedule_monitor_loop),),
    ),
    ModuleDef("booking", enabled_flag="MODULE_BOOKING_ENABLED"),
    ModuleDef("analytics", enabled_flag="MODULE_ANALYTICS_ENABLED"),
)

_MODULES_BY_NAME = {m.name: m for m in MODULES}


# Ordered exactly as the historical api/server.py include_router sequence, so
# iterating this list and calling app.include_router(rm.router, **rm.include_kwargs)
# reproduces the byte-identical route + OpenAPI contract. Each mount is tagged
# with its owning module; the mount is skipped when that module is disabled.
MOUNTS: tuple[RouterMount, ...] = (
    RouterMount(auth_router.router, {"prefix": "/api/auth", "tags": ["auth"]}, "core"),
    RouterMount(admin_me_router.router, {"prefix": "/api/admin/me", "tags": ["admin-me"]}, "core"),
    RouterMount(admin_notification_subscribers_router.router, {"prefix": "/api/admin/notification-subscribers", "tags": ["admin-notification-subscribers"]}, "core"),
    RouterMount(inbox_router.router, {"prefix": "/api/inbox", "tags": ["inbox"]}, "messaging"),
    RouterMount(web_chat_router.router, {"prefix": "/api/web-chat", "tags": ["web-chat"]}, "messaging"),
    RouterMount(webhooks_twilio_router.router, {"prefix": "/api/webhooks/twilio", "tags": ["webhooks-twilio"]}, "messaging"),
    RouterMount(webhooks_meta_router.router, {"prefix": "/api/webhooks/meta", "tags": ["webhooks-meta"]}, "messaging"),
    RouterMount(booking_router.router, {"prefix": "/api/booking", "tags": ["booking"]}, "booking"),
    RouterMount(admin_booking_router.router, {"prefix": "/api/admin/booking", "tags": ["admin-booking"]}, "booking"),
    RouterMount(admin_booking_settings_router.router, {"prefix": "/api/admin/booking", "tags": ["admin-booking-settings"]}, "booking"),
    RouterMount(events_router.router, {"prefix": "/api/events", "tags": ["events"]}, "deals"),
    RouterMount(admin_events_router.router, {"prefix": "/api/admin/events", "tags": ["admin-events"]}, "deals"),
    RouterMount(admin_dependencies_router.router, {"prefix": "/api/admin/dependencies", "tags": ["admin-dependencies"]}, "core"),
    RouterMount(admin_archive_router.router, {"prefix": "/api/admin", "tags": ["admin-archive"]}, "core"),
    RouterMount(walk_in_leads_router.router, {"prefix": "/api/walk-in-leads", "tags": ["walk-in-leads"]}, "booking"),
    RouterMount(event_participants_router.router, {"prefix": "/api/events", "tags": ["event-participants"]}, "deals"),
    RouterMount(contacts_router.router, {"prefix": "/api/contacts", "tags": ["contacts"]}, "contacts"),
    RouterMount(event_documents_routers.event_documents_router, {"prefix": "/api/events", "tags": ["event-documents"]}, "deals"),
    RouterMount(event_documents_routers.documents_router, {"prefix": "/api/documents", "tags": ["event-documents"]}, "deals"),
    RouterMount(invoices_routers.event_invoices_router, {"prefix": "/api/events", "tags": ["invoices"]}, "deals"),
    RouterMount(invoices_routers.invoices_router, {"prefix": "/api/invoices", "tags": ["invoices"]}, "deals"),
    RouterMount(quotes_routers.event_quotes_router, {"prefix": "/api/events", "tags": ["quotes"]}, "deals"),
    RouterMount(quotes_routers.quotes_router, {"prefix": "/api/quotes", "tags": ["quotes"]}, "deals"),
    RouterMount(payments_routers.payments_router, {"prefix": "/api/payments", "tags": ["payments"]}, "deals"),
    RouterMount(payments_routers.invoice_payments_router, {"prefix": "/api/invoices", "tags": ["payments"]}, "deals"),
    RouterMount(payments_routers.event_payments_router, {"prefix": "/api/events", "tags": ["payments"]}, "deals"),
    RouterMount(business_profile_router.router, {"prefix": "/api/business-profile", "tags": ["business-profile"]}, "core"),
    RouterMount(dashboard_router.router, {"prefix": "/api/dashboard", "tags": ["dashboard"]}, "analytics"),
    RouterMount(admin_storefront_analytics_router.router, {"prefix": "/api/admin/storefront-analytics", "tags": ["storefront-analytics"]}, "analytics"),
    RouterMount(catalog_router.router, {"prefix": "/api/catalog", "tags": ["catalog"]}, "inventory"),
    RouterMount(vin_decode_router.router, {"prefix": "/api/admin/vin", "tags": ["vin"]}, "inventory"),
    RouterMount(public_site_router.router, {"prefix": "/api/public", "tags": ["public-site"]}, "booking"),
    RouterMount(search_router.router, {"prefix": "/api/search", "tags": ["search"]}, "core"),
    RouterMount(special_orders_routers.event_special_orders_router, {"prefix": "/api/events", "tags": ["special-orders"]}, "deals"),
    RouterMount(special_orders_routers.special_orders_router, {"prefix": "/api/special-orders", "tags": ["special-orders"]}, "deals"),
    RouterMount(sales_router.router, {"prefix": "/api/sales", "tags": ["sales"]}, "deals"),
    RouterMount(sales_auth_router.router, {"prefix": "/api/sales", "tags": ["sales-auth"]}, "core"),
    RouterMount(sales_appointments_router.router, {"prefix": "/api/sales/appointments", "tags": ["sales-appointments"]}, "scheduling"),
    RouterMount(admin_sales_staff_router.router, {"prefix": "/api/admin/sales-staff", "tags": ["admin-sales-staff"]}, "core"),
    RouterMount(admin_sales_activity_router.router, {"prefix": "/api/admin/sales-activity", "tags": ["admin-sales-activity"]}, "analytics"),
    RouterMount(admin_staff_router.router, {"prefix": "/api/admin/staff", "tags": ["admin-staff"]}, "core"),
    RouterMount(admin_staff_locations_router.router, {"prefix": "/api/admin/staff-locations", "tags": ["admin-staff-locations"]}, "scheduling"),
    RouterMount(sales_clock_router.router, {"prefix": "/api/sales/clock", "tags": ["sales-clock"]}, "scheduling"),
    RouterMount(sales_attendance_router.router, {"prefix": "/api/sales/attendance", "tags": ["sales-attendance"]}, "scheduling"),
    RouterMount(admin_attendance_router.router, {"prefix": "/api/admin/attendance", "tags": ["admin-attendance"]}, "scheduling"),
    RouterMount(admin_cron_health_router.router, {"prefix": "/api/admin/cron-health", "tags": ["admin-cron-health"]}, "core"),
    RouterMount(sales_schedule_router.router, {"prefix": "/api/sales/schedule", "tags": ["sales-schedule"]}, "scheduling"),
    RouterMount(sales_shift_requests_router.router, {"prefix": "/api/sales/schedule/shift-requests", "tags": ["sales-shift-requests"]}, "scheduling"),
    RouterMount(sales_open_shifts_router.router, {"prefix": "/api/sales/schedule/open-shifts", "tags": ["sales-open-shifts"]}, "scheduling"),
    RouterMount(sales_search_router.router, {"prefix": "/api/sales/search", "tags": ["sales-search"]}, "core"),
    RouterMount(sales_notifications_router.router, {"prefix": "/api/sales/me/notifications", "tags": ["sales-notifications"]}, "core"),
    RouterMount(sales_walk_ins_router.router, {"prefix": "/api/sales/walk-ins", "tags": ["sales-walk-ins"]}, "booking"),
    RouterMount(sales_assignment_router.router, {"prefix": "/api/sales", "tags": ["sales-assignment"]}, "deals"),
    RouterMount(sales_time_off_router.router, {"prefix": "/api/sales/time-off", "tags": ["sales-time-off"]}, "scheduling"),
    RouterMount(admin_shifts_router.router, {"prefix": "/api/admin/shifts", "tags": ["admin-shifts"]}, "scheduling"),
    RouterMount(admin_shifts_router.override_router, {"prefix": "/api/admin/shift-overrides", "tags": ["admin-shifts"]}, "scheduling"),
    RouterMount(admin_holidays_router.router, {"prefix": "/api/admin/holidays", "tags": ["admin-holidays"]}, "scheduling"),
    RouterMount(admin_time_off_router.router, {"prefix": "/api/admin/time-off", "tags": ["admin-time-off"]}, "scheduling"),
    RouterMount(admin_schedule_router.router, {"prefix": "/api/admin/schedule", "tags": ["admin-schedule"]}, "scheduling"),
    RouterMount(admin_shift_requests_router.router, {"prefix": "/api/admin/schedule/shift-requests", "tags": ["admin-shift-requests"]}, "scheduling"),
    RouterMount(admin_open_shifts_router.router, {"prefix": "/api/admin/schedule/open-shifts", "tags": ["admin-open-shifts"]}, "scheduling"),
    RouterMount(portal_routers.portal_router, {"prefix": "/portal", "tags": ["portal"]}, "deals"),
    RouterMount(portal_routers.invoice_invitations_router, {"prefix": "/api/invoices", "tags": ["portal-invitations"]}, "deals"),
    RouterMount(portal_routers.quote_invitations_router, {"prefix": "/api/quotes", "tags": ["portal-invitations"]}, "deals"),
)


def module_enabled(module_name: str, settings: Any) -> bool:
    """True if the named module's routers/workers should be active. Kernel
    modules (enabled_flag is None) are always enabled; optional modules read
    their boolean off ``settings`` (default True if the attribute is absent)."""
    mod = _MODULES_BY_NAME.get(module_name)
    if mod is None or mod.enabled_flag is None:
        return True
    return bool(getattr(settings, mod.enabled_flag, True))


def iter_router_mounts(settings: Any):
    """Yield the RouterMounts to include, in registration order, skipping any
    whose owning module is disabled. Called synchronously at app construction."""
    for mount in MOUNTS:
        if module_enabled(mount.module, settings):
            yield mount


def iter_enabled_workers(settings: Any):
    """Yield the WorkerDefs to start, for modules that are enabled. Called in
    the lifespan; the caller is responsible for asyncio.create_task."""
    for mod in MODULES:
        if mod.enabled_flag is None or bool(getattr(settings, mod.enabled_flag, True)):
            for worker in mod.workers:
                yield worker
