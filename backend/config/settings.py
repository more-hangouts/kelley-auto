import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _csv(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


DATABASE_URL = os.getenv("DATABASE_URL")
APP_TIMEZONE = os.getenv("APP_TIMEZONE")
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

SECRET_KEY = os.getenv("SECRET_KEY")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
CORS_ORIGINS = _csv("CORS_ORIGINS", "http://localhost:5173")

# D3: session + CSRF cookie domain. `.kelleyautoplex.com` lets the cookies
# set by api.kelleyautoplex.com flow to admin.* and sales.* — same eTLD+1,
# so SameSite=Lax is sufficient. Override to an empty string (or any
# falsy value) to omit the Domain attribute entirely; the TestClient and
# any future single-host deploy can run without the suffix.
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN", ".kelleyautoplex.com") or None

# Booking widget — public surface
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "http://localhost:3000")
WIDGET_PUBLIC_BASE_URL = os.getenv("WIDGET_PUBLIC_BASE_URL", "http://localhost:8000")

# Customer-facing invoice/quote portal. Used to substitute the public link
# into the email body that ships with mark_sent/resend. Production should
# point at the customer-facing host (e.g. https://kelleyautoplex.com); dev
# falls back to the API origin so the link is reachable from the same
# uvicorn process.
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", WIDGET_PUBLIC_BASE_URL)
BOOKING_WIDGET_ALLOWED_ORIGINS = _csv(
    "BOOKING_WIDGET_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000"
)

# Booking widget — signed token secrets. Fall back to SECRET_KEY in dev so the
# stack still boots; production should set both explicitly.
RESCHEDULE_TOKEN_SECRET = os.getenv("RESCHEDULE_TOKEN_SECRET") or SECRET_KEY
ENRICHMENT_TOKEN_SECRET = os.getenv("ENRICHMENT_TOKEN_SECRET") or SECRET_KEY
ATTRIBUTION_COOKIE_DOMAIN = os.getenv("ATTRIBUTION_COOKIE_DOMAIN") or None

# Booking widget — paid-ad conversion (server-side)
META_PIXEL_ID = os.getenv("META_PIXEL_ID") or None
META_CAPI_TOKEN = os.getenv("META_CAPI_TOKEN") or None
META_CAPI_TEST_EVENT_CODE = os.getenv("META_CAPI_TEST_EVENT_CODE") or None
GOOGLE_ADS_CONVERSION_ID = os.getenv("GOOGLE_ADS_CONVERSION_ID") or None
GOOGLE_ADS_CONVERSION_LABEL = os.getenv("GOOGLE_ADS_CONVERSION_LABEL") or None
GOOGLE_ADS_DEVELOPER_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or None

# Booking widget — product analytics
PLAUSIBLE_DOMAIN = os.getenv("PLAUSIBLE_DOMAIN") or None

# Booking widget — outbound email
SMTP_HOST = os.getenv("SMTP_HOST") or None
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME") or None
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or None
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or None
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Kelley Autoplex")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
BOOKING_INTERNAL_NOTIFICATION_EMAILS = _csv("BOOKING_INTERNAL_NOTIFICATION_EMAILS")

# When set, every outbound email is rewritten to land at this address
# regardless of its real recipient. Subjects get a `[TEST -> original@...]`
# prefix and an in-body banner so the original recipient stays visible.
# Used to read every template in one inbox before flipping to real delivery.
# Unset (or set empty) to resume real recipient delivery.
EMAIL_DEV_REDIRECT = os.getenv("EMAIL_DEV_REDIRECT") or None

# Public URL of the admin app, used to build "Open in admin" CTAs in staff
# notification emails. Default matches the current deployment subdomain.
ADMIN_BASE_URL = os.getenv("ADMIN_BASE_URL", "https://admin.kelleyautoplex.com").rstrip("/")

# Booking widget — outbound SMS (Twilio, wired in v1.5)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID") or None
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN") or None
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER") or None
TWILIO_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID") or None

# Event documents — local file storage (Phase 2 of EVENT_DETAIL_TABS_PHASES.md).
# `_BACKEND` is a forward-looking selector; only `local` is wired today. When
# B2/S3 lands later it becomes a real branch in services/document_storage.py.
DOCUMENT_STORAGE_BACKEND = os.getenv("DOCUMENT_STORAGE_BACKEND", "local")
DOCUMENT_STORAGE_ROOT = os.getenv(
    "DOCUMENT_STORAGE_ROOT", "/var/lib/kelley-autoplex/uploads"
)
DOCUMENT_UPLOAD_MAX_MB = int(os.getenv("DOCUMENT_UPLOAD_MAX_MB", "25"))

# Redis-backed rate limiter (Phase B1 of SECURITY_REMEDIATION_PLAN.md).
# REDIS_URL points at the localhost instance. RATE_LIMIT_FAIL_OPEN controls
# how the limiter degrades when Redis is unreachable: true allows requests
# through with a warning log line, false returns 503. Defaults to fail-open
# until B2 wires a real route, so a partial deploy never 503s production.
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
RATE_LIMIT_FAIL_OPEN = os.getenv("RATE_LIMIT_FAIL_OPEN", "true").lower() == "true"

# Integration-token at-rest encryption (Phase C1 of SECURITY_REMEDIATION_PLAN.md).
# Comma-separated Fernet keys, NEWEST FIRST. The first key encrypts new
# writes; every key in the list can decrypt. Rotate by prepending a new key,
# letting traffic rewrite rows, then dropping the trailing old key.
# Generate with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_TOKEN_KEYS = _csv("INTEGRATION_TOKEN_KEYS", "")

# Webhook event retention (Phase C2 of SECURITY_REMEDIATION_PLAN.md).
# The daily worker prunes `webhook_events` rows older than this. 90 days
# matches the audit recommendation: long enough for any "did we get the
# event?" forensic, short enough that stale provider headers don't pile
# up indefinitely.
WEBHOOK_EVENTS_RETENTION_DAYS = int(os.getenv("WEBHOOK_EVENTS_RETENTION_DAYS", "90"))

# Quote signature HMAC (Phase C3 of SECURITY_REMEDIATION_PLAN.md).
# 32-byte secret used to stamp HMAC-SHA256 over the canonical signed
# payload at quote-accept time. Unlike INTEGRATION_TOKEN_KEYS this is a
# single secret on purpose: rotation would invalidate every prior HMAC
# stamp on an evidentiary record. If the key is ever rotated, treat it
# as its own slice with a `signature_hmac_kid` column to preserve old
# verifications.
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
QUOTE_SIGNATURE_KEY = os.getenv("QUOTE_SIGNATURE_KEY") or None

_REQUIRED = ("DATABASE_URL", "APP_TIMEZONE", "SECRET_KEY")


def validate_config() -> None:
    missing = [name for name in _REQUIRED if not os.getenv(name)]
    if missing:
        print(
            "ERROR: missing required environment variables: " + ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)
