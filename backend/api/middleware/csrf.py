"""D3: double-submit CSRF middleware for cookie-authenticated requests.

The browser path stores its JWT in an HttpOnly session cookie that JS
cannot read. That defeats XSS exfiltration, but it also re-opens the
classic cross-site request forgery channel: any third-party page can
trigger a request to api.shopbellasxv.com and the browser will attach
the cookie unbidden. The double-submit pattern closes that channel
without server-side session state:

  - On login, the server sets a second cookie (the CSRF nonce) WITHOUT
    HttpOnly so the legitimate frontend can read it.
  - On every unsafe request (POST/PATCH/PUT/DELETE), the frontend reads
    the CSRF cookie and mirrors its value into an `X-CSRF-Token` header.
  - This middleware verifies that the header value equals the cookie
    value before the request reaches the handler.

A cross-site attacker can neither read the CSRF cookie (cross-origin
JS cannot read response cookies from another origin) nor set the
`X-CSRF-Token` header on a navigation, so the double-submit comparison
fails and the request is rejected with 403.

The check fires only when a SESSION cookie is present on the request.
Bearer-token callers (smokes, scripts, curl) never carry a session
cookie, so they skip CSRF entirely and continue to authenticate via
the `Authorization` header. The transition path uses this to keep
existing tests green while the cookie flow rolls forward.

A small allow-list of paths bypasses the check even when a session
cookie happens to be present: the login + PIN routes (no useful CSRF
risk on credential-bearing requests, and bootstrapping a session
predates the cookie's existence on the first call), password reset
(anonymous flow), the public booking widget (anonymous), and the
HMAC-signed webhook ingest (own auth scheme).
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_403_FORBIDDEN

from api.cookies import (
    ADMIN_CSRF_COOKIE,
    ADMIN_SESSION_COOKIE,
    CSRF_HEADER_NAME,
    SALES_CSRF_COOKIE,
    SALES_SESSION_COOKIE,
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Prefix-match exemptions. Each entry covers either a credential-bearing
# bootstrap endpoint (no session cookie at request time on the first
# call; subsequent re-login traffic is intentionally exempt too — a
# credential-bearing request cannot be CSRF'd in any useful way), an
# anonymous public surface, or a separately-authenticated channel.
_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/password-reset/",
    "/api/sales/auth/pin",
    "/api/booking/",
    "/api/integrations/webhooks/",
)

# Pairing: each session cookie's CSRF check uses the partner CSRF cookie
# from the same surface. Order matters when both surfaces are present in
# the same browser — the admin pair is checked first, falling through to
# sales only if the admin session cookie is absent. (When both sessions
# are present, the auth dependency picks one based on the request's
# Origin; the CSRF middleware just needs at least one matching pair to
# accept the request.)
_PAIRS: tuple[tuple[str, str], ...] = (
    (ADMIN_SESSION_COOKIE, ADMIN_CSRF_COOKIE),
    (SALES_SESSION_COOKIE, SALES_CSRF_COOKIE),
)


def _is_exempt_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def _forbidden(detail: str) -> Response:
    return JSONResponse(
        status_code=HTTP_403_FORBIDDEN,
        content={"detail": detail},
    )


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce double-submit CSRF on cookie-authenticated unsafe requests.

    Order of checks (cheapest first):
      1. Safe HTTP method → pass.
      2. Path on the exempt list → pass.
      3. No session cookie present → pass (header-bearer or anonymous).
      4. At least one (session, csrf) pair where both are present and
         the request's `X-CSRF-Token` header matches the CSRF cookie →
         pass. Otherwise reject 403 with a deterministic `detail` so
         the smoke can assert the rejection reason.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        if _is_exempt_path(request.url.path):
            return await call_next(request)

        cookies = request.cookies
        session_present = any(
            cookies.get(session_name) for session_name, _ in _PAIRS
        )
        if not session_present:
            return await call_next(request)

        header_value = request.headers.get(CSRF_HEADER_NAME, "")
        if not header_value:
            return _forbidden("csrf_token_missing")

        for session_name, csrf_name in _PAIRS:
            if not cookies.get(session_name):
                continue
            csrf_cookie = cookies.get(csrf_name, "")
            if csrf_cookie and hmac.compare_digest(csrf_cookie, header_value):
                return await call_next(request)

        return _forbidden("csrf_token_invalid")
