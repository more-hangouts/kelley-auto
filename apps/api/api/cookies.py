"""D3: session + CSRF cookie naming and helpers.

The double-cookie pattern, set on every successful login/PIN response:

  - Session cookie (HttpOnly): the JWT itself. The browser sends it back
    on every request to `api.kelleyautoplex.com` because all surfaces share
    `.kelleyautoplex.com` as the cookie Domain.
  - CSRF cookie (NOT HttpOnly): a random nonce. The frontend reads it via
    `document.cookie` on each request and mirrors the value into an
    `X-CSRF-Token` header. The CSRF middleware verifies cookie == header
    on unsafe methods (POST/PATCH/PUT/DELETE). Header-bearer callers
    (smokes, curl) never present a session cookie so they skip the check.

The `__Secure-` prefix is browser-enforced: a cookie with that prefix
must be set with `Secure` (HTTPS only) or the browser refuses to store
it. Acts as a tripwire if someone ever sets these over plaintext.

Per-surface naming (admin vs sales) keeps the two contexts isolated:
clearing the admin cookies does not affect a parallel sales session in
the same browser, and a future sales-only bug cannot scramble admin
CSRF state. Both still share `Domain=.kelleyautoplex.com` so the browser
hands them back to `api.kelleyautoplex.com` from either surface.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Response

from config.settings import ACCESS_TOKEN_EXPIRE_MINUTES, SESSION_COOKIE_DOMAIN

ADMIN_SURFACE = "admin"
SALES_SURFACE = "sales"

ADMIN_SESSION_COOKIE = "__Secure-kelley_autoplex_session"
ADMIN_CSRF_COOKIE = "__Secure-kelley_autoplex_csrf"
SALES_SESSION_COOKIE = "__Secure-kelley_autoplex_sales_session"
SALES_CSRF_COOKIE = "__Secure-kelley_autoplex_sales_csrf"

CSRF_HEADER_NAME = "X-CSRF-Token"

# Cookie Max-Age matches the JWT lifetime. The HttpOnly session cookie
# carries the JWT itself; the CSRF cookie's lifetime is bound to it so
# both expire together. (The server still validates the JWT's `exp` and
# `tv` claims on every request — the cookie Max-Age is just a courtesy
# to the browser to stop sending stale cookies.)
COOKIE_MAX_AGE_SECONDS = ACCESS_TOKEN_EXPIRE_MINUTES * 60

_CSRF_NONCE_BYTES = 32


@dataclass(frozen=True)
class SurfaceCookies:
    surface: str
    session_name: str
    csrf_name: str


_ADMIN = SurfaceCookies(
    surface=ADMIN_SURFACE,
    session_name=ADMIN_SESSION_COOKIE,
    csrf_name=ADMIN_CSRF_COOKIE,
)
_SALES = SurfaceCookies(
    surface=SALES_SURFACE,
    session_name=SALES_SESSION_COOKIE,
    csrf_name=SALES_CSRF_COOKIE,
)


def surface_cookies(surface: str) -> SurfaceCookies:
    if surface == ADMIN_SURFACE:
        return _ADMIN
    if surface == SALES_SURFACE:
        return _SALES
    raise ValueError(f"unknown surface: {surface!r}")


def generate_csrf_token() -> str:
    """Cryptographically random CSRF nonce, URL-safe base64."""
    return secrets.token_urlsafe(_CSRF_NONCE_BYTES)


def _cookie_kwargs(*, http_only: bool, max_age: int | None) -> dict:
    """Set-Cookie attributes shared by session + CSRF cookies.

    `Secure=True` is required by the `__Secure-` prefix; the browser
    rejects the cookie otherwise. SameSite=lax keeps the cookie sent on
    top-level GETs and same-site fetch/XHR, which covers the admin →
    api and sales → api flows (shared eTLD+1).
    """
    kwargs = {
        "path": "/",
        "secure": True,
        "httponly": http_only,
        "samesite": "lax",
    }
    if SESSION_COOKIE_DOMAIN:
        kwargs["domain"] = SESSION_COOKIE_DOMAIN
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


def set_session_cookies(
    response: Response,
    *,
    surface: str,
    jwt_token: str,
) -> str:
    """Issue session + CSRF cookies for `surface` and return the CSRF nonce.

    Caller doesn't normally need the returned nonce — the cookie is the
    canonical source for the frontend, which reads it via `document.cookie`.
    Returned so smokes can assert on the value.
    """
    sc = surface_cookies(surface)
    csrf = generate_csrf_token()
    response.set_cookie(
        key=sc.session_name,
        value=jwt_token,
        **_cookie_kwargs(http_only=True, max_age=COOKIE_MAX_AGE_SECONDS),
    )
    response.set_cookie(
        key=sc.csrf_name,
        value=csrf,
        **_cookie_kwargs(http_only=False, max_age=COOKIE_MAX_AGE_SECONDS),
    )
    return csrf


def clear_session_cookies(response: Response, *, surface: str) -> None:
    """Erase session + CSRF cookies for `surface`.

    Sets each cookie to an empty value with `Max-Age=0` and the same
    Domain/Path so the browser overwrites and immediately expires the
    pair. Logout routes call this AFTER `bump_token_version()` so the
    JWT carried in the just-cleared cookie would have failed validation
    anyway — the cookie clear is the user-visible signal.
    """
    sc = surface_cookies(surface)
    response.set_cookie(
        key=sc.session_name,
        value="",
        **_cookie_kwargs(http_only=True, max_age=0),
    )
    response.set_cookie(
        key=sc.csrf_name,
        value="",
        **_cookie_kwargs(http_only=False, max_age=0),
    )
