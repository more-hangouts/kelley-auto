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

# Externally-reachable API origin used to build ABSOLUTE public media URLs
# (vehicle photos) for the storefront, which runs on a different origin and
# can't resolve origin-relative paths against the API. Defaults to 127.0.0.1
# in dev (matches the public site's next/image allowlist); set to
# https://api.kelleyautoplex.com in production.
PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
# Per-file cap for staff-uploaded vehicle photos.
VEHICLE_PHOTO_MAX_MB = int(os.getenv("VEHICLE_PHOTO_MAX_MB", "10"))

# Customer-facing invoice/quote portal. Used to substitute the public link
# into the email body that ships with mark_sent/resend. Production should
# point at the customer-facing host (e.g. https://kelleyautoplex.com); dev
# falls back to the API origin so the link is reachable from the same
# uvicorn process.
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", WIDGET_PUBLIC_BASE_URL)
BOOKING_WIDGET_ALLOWED_ORIGINS = _csv(
    "BOOKING_WIDGET_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8000"
)

# Booking widget — signed token secret. Falls back to SECRET_KEY in dev so the
# stack still boots; production should set it explicitly.
RESCHEDULE_TOKEN_SECRET = os.getenv("RESCHEDULE_TOKEN_SECRET") or SECRET_KEY
ATTRIBUTION_COOKIE_DOMAIN = os.getenv("ATTRIBUTION_COOKIE_DOMAIN") or None

# Booking widget — paid-ad conversion (server-side)
META_PIXEL_ID = os.getenv("META_PIXEL_ID") or None
META_CAPI_TOKEN = os.getenv("META_CAPI_TOKEN") or None
META_CAPI_TEST_EVENT_CODE = os.getenv("META_CAPI_TEST_EVENT_CODE") or None
# Master kill switch for ALL outbound Meta Conversions API delivery. Stays OFF
# until Pixel ID, access token, test event code, and consent language are in
# place. The storefront analytics tables and the outbound queue exist
# regardless — this only gates whether anything is actually sent to Meta.
META_CAPI_ENABLED = os.getenv("META_CAPI_ENABLED", "false").lower() == "true"
META_CAPI_API_VERSION = os.getenv("META_CAPI_API_VERSION", "v21.0")
# Storefront first-party analytics ingestion (POST /api/public/track). Kill
# switch for behavioral tracking, independent of any ad destination.
STOREFRONT_ANALYTICS_ENABLED = (
    os.getenv("STOREFRONT_ANALYTICS_ENABLED", "true").lower() == "true"
)

# ---------------------------------------------------------------------------
# API module enable flags (Phase 3). Each optional domain module's routers and
# workers are gated by one of these. All default true, so production behavior is
# unchanged unless someone explicitly opts a module out. Disabling a module only
# stops its routers from mounting and its workers from starting — every module
# package still imports unconditionally (models stay registered, string
# relationships resolve). core and contacts are kernel modules with no flag and
# can never be disabled. Read by modules/registry.py via getattr(settings, ...).
# ---------------------------------------------------------------------------
MODULE_MESSAGING_ENABLED = (
    os.getenv("MODULE_MESSAGING_ENABLED", "true").lower() == "true"
)
MODULE_DEALS_ENABLED = os.getenv("MODULE_DEALS_ENABLED", "true").lower() == "true"
MODULE_INVENTORY_ENABLED = (
    os.getenv("MODULE_INVENTORY_ENABLED", "true").lower() == "true"
)
MODULE_SCHEDULING_ENABLED = (
    os.getenv("MODULE_SCHEDULING_ENABLED", "true").lower() == "true"
)
MODULE_BOOKING_ENABLED = (
    os.getenv("MODULE_BOOKING_ENABLED", "true").lower() == "true"
)
MODULE_ANALYTICS_ENABLED = (
    os.getenv("MODULE_ANALYTICS_ENABLED", "true").lower() == "true"
)

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

# Outbound email via the Gmail API using OAuth2 (an "installed app" client
# plus a long-lived refresh token minted by a one-time consent as the
# sending mailbox). When all three OAuth values are present, the Gmail API
# transport takes precedence over SMTP. GMAIL_API_SENDER is the mailbox that
# granted consent — it is both the authenticated user and the From address,
# so consent must be granted as this address. Falls back to SMTP_FROM_EMAIL.
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID") or None
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET") or None
GMAIL_OAUTH_REFRESH_TOKEN = os.getenv("GMAIL_OAUTH_REFRESH_TOKEN") or None
GMAIL_API_SENDER = os.getenv("GMAIL_API_SENDER") or SMTP_FROM_EMAIL

BOOKING_INTERNAL_NOTIFICATION_EMAILS = _csv("BOOKING_INTERNAL_NOTIFICATION_EMAILS")
# Staff recipients for public storefront lead alerts (comma-separated). When
# unset, the service falls back to business_profile.email, then to every
# active admin user's email. Set this to override (e.g. a sales@ alias).
PUBLIC_LEAD_NOTIFY_EMAILS = _csv("PUBLIC_LEAD_NOTIFY_EMAILS")

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

# Twilio Voice — click-to-call bridge (CRM "Business number call").
# Optional, additive path beside the native tel: dialer: Twilio first calls the
# REP, then the answered leg is bridged to the CONTACT so the contact sees the
# business caller ID (TWILIO_VOICE_FROM_NUMBER, falling back to the SMS
# TWILIO_FROM_NUMBER). Hard-disabled by default — stays off until an authorized
# voice-capable number is configured. Reuses TWILIO_ACCOUNT_SID/AUTH_TOKEN.
#
# TWILIO_VOICE_REP_FALLBACK_NUMBER is the default number Twilio dials for the
# rep when the request doesn't carry a per-device callback number (the admin UI
# remembers the rep's number in localStorage and sends it explicitly). Single-
# rep shops can rely on the fallback alone.
TWILIO_VOICE_ENABLED = os.getenv("TWILIO_VOICE_ENABLED", "false").lower() == "true"
TWILIO_VOICE_FROM_NUMBER = (
    os.getenv("TWILIO_VOICE_FROM_NUMBER") or TWILIO_FROM_NUMBER
)
TWILIO_VOICE_REP_FALLBACK_NUMBER = (
    os.getenv("TWILIO_VOICE_REP_FALLBACK_NUMBER") or None
)

# Omnichannel inbox (Phase 2+). Outbound SMS stays hard-disabled until the
# A2P 10DLC campaign is approved — inbound lands regardless. Inbound webhook
# signature verification is REQUIRED by default (needs TWILIO_AUTH_TOKEN);
# only a dev/test box should ever set the require flag to false.
SMS_SENDING_ENABLED = os.getenv("SMS_SENDING_ENABLED", "false").lower() == "true"
INBOUND_SMS_REQUIRE_SIGNATURE = (
    os.getenv("INBOUND_SMS_REQUIRE_SIGNATURE", "true").lower() == "true"
)

# Outbound SMS quiet hours (shop-local, America/Chicago via APP_TIMEZONE). A
# staff reply attempted inside the window is refused with a clear reason and
# the composer can offer an override; automated sends never override. Default
# 21:00–08:00. Set START == END to disable the window entirely.
SMS_QUIET_HOURS_START = int(os.getenv("SMS_QUIET_HOURS_START", "21"))  # 9pm
SMS_QUIET_HOURS_END = int(os.getenv("SMS_QUIET_HOURS_END", "8"))  # 8am

# Meta (Facebook Messenger + Instagram DM) inbox channels — Phase 5.
# Inbound webhooks work with test/app-role accounts before App Review; real
# customer DMs need pages_messaging + instagram_manage_messages + human_agent
# approved. Outbound Meta replies stay hard-disabled until then.
META_APP_ID = os.getenv("META_APP_ID") or None
META_APP_SECRET = os.getenv("META_APP_SECRET") or None  # verifies X-Hub-Signature-256
META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN") or None
META_PAGE_ID = os.getenv("META_PAGE_ID") or None
META_IG_ACCOUNT_ID = os.getenv("META_IG_ACCOUNT_ID") or None
# Page access token — used to fetch sender profile (name/avatar) and, later,
# to send replies. Store here for v1; move to encrypted integration_tokens once
# a long-lived System-User token is in place.
META_PAGE_ACCESS_TOKEN = os.getenv("META_PAGE_ACCESS_TOKEN") or None
META_MESSAGING_ENABLED = os.getenv("META_MESSAGING_ENABLED", "false").lower() == "true"
INBOUND_META_REQUIRE_SIGNATURE = (
    os.getenv("INBOUND_META_REQUIRE_SIGNATURE", "true").lower() == "true"
)

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

# Encryption keys for at-rest BHPH lead-application PII (DOB, driver's
# license #, SSN, home address). Same Fernet/MultiFernet rotation scheme as
# INTEGRATION_TOKEN_KEYS — comma-separated, NEWEST FIRST — but a SEPARATE key
# so the two blast radii don't overlap. This key MUST be backed up securely:
# losing every key here makes existing encrypted applications unrecoverable.
# Generate with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
LEAD_PII_KEYS = _csv("LEAD_PII_KEYS", "")

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
