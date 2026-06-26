import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.middleware.csrf import CSRFMiddleware
from api.middleware.security_headers import SecurityHeadersMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from api.routers import admin_attendance as admin_attendance_router
from api.routers import admin_booking as admin_booking_router
from api.routers import admin_cron_health as admin_cron_health_router
from api.routers import admin_booking_settings as admin_booking_settings_router
from api.routers import admin_holidays as admin_holidays_router
from api.routers import admin_me as admin_me_router
from api.routers import admin_sales_staff as admin_sales_staff_router
from api.routers import admin_open_shifts as admin_open_shifts_router
from api.routers import admin_schedule as admin_schedule_router
from api.routers import admin_shift_requests as admin_shift_requests_router
from api.routers import admin_shifts as admin_shifts_router
from api.routers import admin_staff as admin_staff_router
from api.routers import admin_staff_locations as admin_staff_locations_router
from api.routers import admin_time_off as admin_time_off_router
from api.routers import auth as auth_router
from api.routers import booking as booking_router
from api.routers import business_profile as business_profile_router
from api.routers import catalog as catalog_router
from api.routers import contacts as contacts_router
from api.routers import dashboard as dashboard_router
from api.routers import event_documents as event_documents_routers
from api.routers import admin_archive as admin_archive_router
from api.routers import admin_dependencies as admin_dependencies_router
from api.routers import admin_events as admin_events_router
from api.routers import event_participants as event_participants_router
from api.routers import events as events_router
from api.routers import invoices as invoices_routers
from api.routers import payments as payments_routers
from api.routers import portal as portal_routers
from api.routers import public_site as public_site_router
from api.routers import quotes as quotes_routers
from api.routers import sales as sales_router
from api.routers import sales_appointments as sales_appointments_router
from api.routers import sales_attendance as sales_attendance_router
from api.routers import sales_auth as sales_auth_router
from api.routers import sales_clock as sales_clock_router
from api.routers import sales_notifications as sales_notifications_router
from api.routers import sales_open_shifts as sales_open_shifts_router
from api.routers import sales_schedule as sales_schedule_router
from api.routers import sales_shift_requests as sales_shift_requests_router
from api.routers import sales_search as sales_search_router
from api.routers import sales_assignment as sales_assignment_router
from api.routers import sales_walk_ins as sales_walk_ins_router
from api.routers import sales_time_off as sales_time_off_router
from api.routers import sales_tried_on as sales_tried_on_router
from api.routers import search as search_router
from api.routers import special_orders as special_orders_routers
from api.routers import walk_in_leads as walk_in_leads_router
from config.settings import (
    APP_TIMEZONE,
    BOOKING_WIDGET_ALLOWED_ORIGINS,
    CORS_ORIGINS,
    validate_config,
)
from api.redis_rate_limit import close_client as close_redis_client
from database.connection import engine
from workers.daily import run_loop as run_daily_loop
from workers.notifications import run_loop as run_notifications_loop
from workers.schedule_monitor import run_loop as run_schedule_monitor_loop

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WIDGETS_DIR = _REPO_ROOT / "widgets"
_MARKETING_DIR = _REPO_ROOT / "marketing"


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    stop_event = asyncio.Event()
    notifications_task = asyncio.create_task(run_notifications_loop(stop_event))
    daily_task = asyncio.create_task(run_daily_loop(stop_event))
    schedule_monitor_task = asyncio.create_task(
        run_schedule_monitor_loop(stop_event)
    )
    try:
        yield
    finally:
        stop_event.set()
        for name, task in (
            ("notifications", notifications_task),
            ("daily", daily_task),
            ("schedule_monitor", schedule_monitor_task),
        ):
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                log.warning("%s worker did not stop within 5s; cancelling", name)
                task.cancel()
        await close_redis_client()


app = FastAPI(lifespan=lifespan)

# Auth + admin surface uses cookies/credentials. The public booking widget
# does not, so its origin list can be wider without weakening dashboard CORS.
_all_origins = sorted(set(CORS_ORIGINS) | set(BOOKING_WIDGET_ALLOWED_ORIGINS))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# E3: baseline security headers. Uses `setdefault` semantics inside the
# middleware so nginx-supplied values (admin/sales hosts already emit
# HSTS, nosniff, X-Frame-Options, Referrer-Policy) win in production;
# the middleware only fills in gaps. The new contribution over nginx
# is `Permissions-Policy`, scoped to the camera + geolocation surface
# the sales clock and admin staff-locations pages need.
app.add_middleware(SecurityHeadersMiddleware)

# D3: double-submit CSRF for cookie-authenticated requests. Skips safe
# methods, skips the login/PIN/password-reset bootstrap routes, and
# skips entirely when no session cookie is present (header-bearer
# callers like smokes and curl continue to work unchanged). Added LAST
# so it runs first on inbound — a CSRF reject never reaches the route
# handler or its DB session.
app.add_middleware(CSRFMiddleware)

app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(admin_me_router.router, prefix="/api/admin/me", tags=["admin-me"])
app.include_router(booking_router.router, prefix="/api/booking", tags=["booking"])
app.include_router(
    admin_booking_router.router, prefix="/api/admin/booking", tags=["admin-booking"]
)
app.include_router(
    admin_booking_settings_router.router,
    prefix="/api/admin/booking",
    tags=["admin-booking-settings"],
)
app.include_router(events_router.router, prefix="/api/events", tags=["events"])
app.include_router(
    admin_events_router.router,
    prefix="/api/admin/events",
    tags=["admin-events"],
)
app.include_router(
    admin_dependencies_router.router,
    prefix="/api/admin/dependencies",
    tags=["admin-dependencies"],
)
app.include_router(
    admin_archive_router.router,
    prefix="/api/admin",
    tags=["admin-archive"],
)
app.include_router(
    walk_in_leads_router.router,
    prefix="/api/walk-in-leads",
    tags=["walk-in-leads"],
)
app.include_router(
    event_participants_router.router,
    prefix="/api/events",
    tags=["event-participants"],
)
app.include_router(contacts_router.router, prefix="/api/contacts", tags=["contacts"])
app.include_router(
    event_documents_routers.event_documents_router,
    prefix="/api/events",
    tags=["event-documents"],
)
app.include_router(
    event_documents_routers.documents_router,
    prefix="/api/documents",
    tags=["event-documents"],
)
app.include_router(
    invoices_routers.event_invoices_router,
    prefix="/api/events",
    tags=["invoices"],
)
app.include_router(
    invoices_routers.invoices_router,
    prefix="/api/invoices",
    tags=["invoices"],
)
app.include_router(
    quotes_routers.event_quotes_router,
    prefix="/api/events",
    tags=["quotes"],
)
app.include_router(
    quotes_routers.quotes_router,
    prefix="/api/quotes",
    tags=["quotes"],
)
app.include_router(
    payments_routers.payments_router,
    prefix="/api/payments",
    tags=["payments"],
)
app.include_router(
    payments_routers.invoice_payments_router,
    prefix="/api/invoices",
    tags=["payments"],
)
app.include_router(
    payments_routers.event_payments_router,
    prefix="/api/events",
    tags=["payments"],
)
app.include_router(
    business_profile_router.router,
    prefix="/api/business-profile",
    tags=["business-profile"],
)
app.include_router(
    dashboard_router.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
)
app.include_router(
    catalog_router.router,
    prefix="/api/catalog",
    tags=["catalog"],
)
app.include_router(
    public_site_router.router,
    prefix="/api/public",
    tags=["public-site"],
)
app.include_router(
    search_router.router,
    prefix="/api/search",
    tags=["search"],
)
app.include_router(
    special_orders_routers.event_special_orders_router,
    prefix="/api/events",
    tags=["special-orders"],
)
app.include_router(
    special_orders_routers.special_orders_router,
    prefix="/api/special-orders",
    tags=["special-orders"],
)
app.include_router(sales_router.router, prefix="/api/sales", tags=["sales"])
app.include_router(
    sales_auth_router.router, prefix="/api/sales", tags=["sales-auth"]
)
app.include_router(
    sales_appointments_router.router,
    prefix="/api/sales/appointments",
    tags=["sales-appointments"],
)
app.include_router(
    sales_tried_on_router.router,
    prefix="/api/sales",
    tags=["sales-tried-on"],
)
app.include_router(
    admin_sales_staff_router.router,
    prefix="/api/admin/sales-staff",
    tags=["admin-sales-staff"],
)
app.include_router(
    admin_staff_router.router,
    prefix="/api/admin/staff",
    tags=["admin-staff"],
)
app.include_router(
    admin_staff_locations_router.router,
    prefix="/api/admin/staff-locations",
    tags=["admin-staff-locations"],
)
app.include_router(
    sales_clock_router.router,
    prefix="/api/sales/clock",
    tags=["sales-clock"],
)
app.include_router(
    sales_attendance_router.router,
    prefix="/api/sales/attendance",
    tags=["sales-attendance"],
)
app.include_router(
    admin_attendance_router.router,
    prefix="/api/admin/attendance",
    tags=["admin-attendance"],
)
app.include_router(
    admin_cron_health_router.router,
    prefix="/api/admin/cron-health",
    tags=["admin-cron-health"],
)
app.include_router(
    sales_schedule_router.router,
    prefix="/api/sales/schedule",
    tags=["sales-schedule"],
)
app.include_router(
    sales_shift_requests_router.router,
    prefix="/api/sales/schedule/shift-requests",
    tags=["sales-shift-requests"],
)
app.include_router(
    sales_open_shifts_router.router,
    prefix="/api/sales/schedule/open-shifts",
    tags=["sales-open-shifts"],
)
app.include_router(
    sales_search_router.router,
    prefix="/api/sales/search",
    tags=["sales-search"],
)
app.include_router(
    sales_notifications_router.router,
    prefix="/api/sales/me/notifications",
    tags=["sales-notifications"],
)
app.include_router(
    sales_walk_ins_router.router,
    prefix="/api/sales/walk-ins",
    tags=["sales-walk-ins"],
)
app.include_router(
    sales_assignment_router.router,
    prefix="/api/sales",
    tags=["sales-assignment"],
)
app.include_router(
    sales_time_off_router.router,
    prefix="/api/sales/time-off",
    tags=["sales-time-off"],
)
app.include_router(
    admin_shifts_router.router,
    prefix="/api/admin/shifts",
    tags=["admin-shifts"],
)
app.include_router(
    admin_shifts_router.override_router,
    prefix="/api/admin/shift-overrides",
    tags=["admin-shifts"],
)
app.include_router(
    admin_holidays_router.router,
    prefix="/api/admin/holidays",
    tags=["admin-holidays"],
)
app.include_router(
    admin_time_off_router.router,
    prefix="/api/admin/time-off",
    tags=["admin-time-off"],
)
app.include_router(
    admin_schedule_router.router,
    prefix="/api/admin/schedule",
    tags=["admin-schedule"],
)
app.include_router(
    admin_shift_requests_router.router,
    prefix="/api/admin/schedule/shift-requests",
    tags=["admin-shift-requests"],
)
app.include_router(
    admin_open_shifts_router.router,
    prefix="/api/admin/schedule/open-shifts",
    tags=["admin-open-shifts"],
)

# Phase 7 customer-facing portal. Public surface mounted at `/portal`
# (NOT `/api/portal` — the customer never sees `/api`). Staff invitation
# management lives at `/api/invoices/{id}/invitations` and
# `/api/quotes/{id}/invitations`.
app.include_router(
    portal_routers.portal_router, prefix="/portal", tags=["portal"]
)
app.include_router(
    portal_routers.invoice_invitations_router,
    prefix="/api/invoices",
    tags=["portal-invitations"],
)
app.include_router(
    portal_routers.quote_invitations_router,
    prefix="/api/quotes",
    tags=["portal-invitations"],
)

# Widget JS is canonical at repo /widgets and served at /widgets/* in dev so the
# embed URL matches production (where Nginx serves the same path from disk).
if _WIDGETS_DIR.exists():
    app.mount("/widgets", StaticFiles(directory=_WIDGETS_DIR), name="widgets")

# Marketing site served alongside the API in dev so the booking widget can be
# exercised end-to-end on a single origin. Production serves marketing through
# Nginx; this mount is a dev convenience only.
if _MARKETING_DIR.exists():
    app.mount(
        "/marketing",
        StaticFiles(directory=_MARKETING_DIR, html=True),
        name="marketing",
    )


@app.get("/api/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "disconnected"},
        )

    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar()
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "schema_missing"},
        )

    return {
        "status": "ok",
        "database": "connected",
        "migrations_applied": count,
        "timezone": APP_TIMEZONE,
    }
