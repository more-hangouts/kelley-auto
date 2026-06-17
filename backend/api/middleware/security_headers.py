"""Baseline security headers, defensively duplicated at the app layer.

Phase E3 of SECURITY_REMEDIATION_PLAN.md. Nginx already emits four
headers on every host (HSTS, X-Content-Type-Options, X-Frame-Options,
Referrer-Policy). This middleware exists so the same headers are
present even when nginx isn't in front of the app — direct loopback
debugging, TestClient runs, future deploys that route some traffic
straight at uvicorn. The `setdefault` semantics mean nginx's values
win when nginx is in the path; the middleware only fills in gaps.

The new contribution beyond nginx is `Permissions-Policy`, scoped to
the actual feature surface the frontends use:

  - camera=self        — sales clock-in selfie capture
                         (`navigator.mediaDevices.getUserMedia`)
  - geolocation=self   — sales clock-in + admin staff-locations
                         (`navigator.geolocation.getCurrentPosition`)
  - everything else    — denied (empty allowlist `()`)

CSP is deliberately NOT set here. Vite-built bundles, Google Fonts
preconnects, and the inline-style patterns the admin SPA relies on
need their own design pass — `report-only` first, then enforce —
and that's a separate slice. Better to ship the four header
fallbacks plus Permissions-Policy now and not break the SPA than
to land a half-tuned CSP that prints console errors at every page
load. Tracked as an E-phase follow-up.

The starter `X-Frame-Options: DENY` matches the admin/sales hosts.
The public marketing host (shopbellasxv.com) is served by a separate
nginx stanza that sets `SAMEORIGIN`; that stanza isn't fronted by
this middleware, so the divergence stays where it is.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Permissions-Policy is the modern replacement for Feature-Policy.
# Syntax: `feature=allowlist`, where the allowlist is one of:
#   ()          — denied for every origin (most restrictive)
#   (self)      — allowed for the same origin only
#   (self "https://x") — allowed for self + named origins
# Ordering features alphabetically keeps diffs reviewable.
_PERMISSIONS_POLICY = ", ".join(
    [
        "accelerometer=()",
        "ambient-light-sensor=()",
        "autoplay=()",
        "battery=()",
        "bluetooth=()",
        "camera=(self)",
        "display-capture=()",
        "document-domain=()",
        "encrypted-media=()",
        "fullscreen=(self)",
        "geolocation=(self)",
        "gyroscope=()",
        "hid=()",
        "idle-detection=()",
        "magnetometer=()",
        "microphone=()",
        "midi=()",
        "payment=()",
        "picture-in-picture=()",
        "publickey-credentials-get=()",
        "screen-wake-lock=()",
        "serial=()",
        "sync-xhr=()",
        "usb=()",
        "xr-spatial-tracking=()",
    ]
)


# Headers the middleware fills in when nginx hasn't already. Each
# value matches what the production nginx stanza emits, so a request
# that comes through nginx and through the middleware ends up with
# the same final value either way.
_DEFAULTS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": _PERMISSIONS_POLICY,
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline security headers to every response.

    `setdefault` semantics so an upstream proxy (nginx) that has
    already set a header wins. The middleware only fills in headers
    that aren't already present. This keeps the production behavior
    unchanged on the admin/sales hosts (nginx still owns those four
    values) and gives the same protection on every other code path —
    loopback debugging, tests, any future direct-to-uvicorn deploy.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in _DEFAULTS.items():
            response.headers.setdefault(name, value)
        return response
