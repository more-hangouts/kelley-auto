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

from modules.registry import iter_router_mounts, iter_enabled_workers
import config.settings as settings
from config.settings import (
    APP_ENV,
    APP_TIMEZONE,
    BOOKING_WIDGET_ALLOWED_ORIGINS,
    CORS_ORIGINS,
    validate_config,
)
from modules.core.services import email_transport
from api.redis_rate_limit import close_client as close_redis_client
from database.connection import engine

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WIDGETS_DIR = _REPO_ROOT / "widgets"


def _warn_if_email_delivery_disabled() -> None:
    """At boot, shout if outbound email will silently no-op. In production a
    NullEmailTransport means every lead alert / transactional email is dropped
    — exactly the silent failure that let a live lead sit un-notified. We log
    it LOUD (CRITICAL in prod) so it can't hide; the /api/health payload also
    surfaces it for uptime monitors."""
    kind = email_transport.active_email_transport_kind()
    if kind != "null":
        log.info("email delivery active via %s transport", kind)
        return
    msg = (
        "EMAIL DELIVERY DISABLED — resolved transport is NullEmailTransport; "
        "no mail (lead alerts, booking, digests) will actually be sent. "
        "Configure GMAIL_OAUTH_* or SMTP_* in the environment."
    )
    if APP_ENV == "production":
        log.critical("%s [APP_ENV=production]", msg)
    else:
        log.warning(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    _warn_if_email_delivery_disabled()
    stop_event = asyncio.Event()
    # Start the workers of every enabled module. With all modules enabled
    # (production default) this is notifications, daily, schedule_monitor —
    # the same three loops, in the same order, as before the registry.
    worker_tasks = [
        (worker.name, asyncio.create_task(worker.runner(stop_event)))
        for worker in iter_enabled_workers(settings)
    ]
    try:
        yield
    finally:
        stop_event.set()
        for name, task in worker_tasks:
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                log.warning("%s worker did not stop within 5s; cancelling", name)
                task.cancel()
        await close_redis_client()


app = FastAPI(lifespan=lifespan)

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
# callers like smokes and curl continue to work unchanged). Keep CORS
# outside this middleware so CSRF rejects still carry browser-readable
# CORS headers.
app.add_middleware(CSRFMiddleware)

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

# Mount every enabled module's routers in registration order (see
# modules/registry.py). With all modules enabled this reproduces the
# historical include_router sequence exactly, so the route and OpenAPI
# contract is unchanged.
for _mount in iter_router_mounts(settings):
    app.include_router(_mount.router, **_mount.include_kwargs)

# Widget JS is canonical at repo /widgets and served at /widgets/* in dev so the
# embed URL matches production (where Nginx serves the same path from disk).
if _WIDGETS_DIR.exists():
    app.mount("/widgets", StaticFiles(directory=_WIDGETS_DIR), name="widgets")


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

    email_kind = email_transport.active_email_transport_kind()
    email_ok = email_kind != "null"
    body = {
        "status": "ok",
        "database": "connected",
        "migrations_applied": count,
        "timezone": APP_TIMEZONE,
        "email_transport": email_kind,
        "email_delivery_enabled": email_ok,
    }
    if not email_ok:
        # Degrade visibly for monitors without failing the whole app: the DB
        # is fine, so return 200 but flag the outage prominently.
        body["status"] = "degraded"
        body["warnings"] = ["email_delivery_disabled"]
    return body
