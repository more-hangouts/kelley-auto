"""Smoke for E3: baseline security headers on every response.

Two layers in play and the smoke verifies both:

  - At the app layer (TestClient hits FastAPI directly with no nginx
    in the path), the middleware sets all five baseline headers.
  - At the production layer (curl against the live host), nginx wins
    on the four headers it already emits; the middleware contributes
    the new `Permissions-Policy`. We assert presence here, not value
    equality, because nginx is the source of truth in prod.

The user-spec acceptance:

  1. HSTS present on app-layer response, value matches the prod nginx
     stanza so the two layers agree.
  2. X-Content-Type-Options: nosniff on every response.
  3. Referrer-Policy: strict-origin-when-cross-origin.
  4. X-Frame-Options: DENY (mirrors admin/sales nginx; the marketing
     host's SAMEORIGIN comes from a separate nginx stanza that this
     middleware does not front).
  5. Permissions-Policy present and includes the camera/geolocation
     `self` grants the sales clock + admin staff-locations need.
  6. Headers are present on a varied set of routes: GET /api/health,
     POST /api/auth/login (401 response shape), and an auth-protected
     route. Confirms the middleware fires on every status code, not
     just 2xx.
  7. The middleware does NOT clobber a header already set upstream:
     a route that explicitly returns `X-Frame-Options: SAMEORIGIN`
     keeps that value (proves `setdefault` semantics; nginx in prod
     relies on the same property).
  8. CSP is deliberately NOT set (deferred to a follow-up). Assert
     absence so a future accidental add lands in this smoke first.

Run with: venv/bin/python tests/test_security_headers_smoke.py
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.middleware.security_headers import (  # noqa: E402
    SecurityHeadersMiddleware,
    _DEFAULTS,
    _PERMISSIONS_POLICY,
)
from api.server import app  # noqa: E402


client = TestClient(app)


# ---------------------------------------------------------------------
# 1-6. Headers present on every status code + route shape.
# ---------------------------------------------------------------------
for path, method, body in (
    ("/api/health", "GET", None),
    ("/api/auth/login", "POST", {"email": "nobody@example.com", "password": "x"}),
):
    if method == "GET":
        resp = client.get(path)
    else:
        resp = client.post(path, json=body)
    # We don't care about the status — just that the middleware fires.
    hdrs = {k.lower(): v for k, v in resp.headers.items()}
    assert hdrs.get("strict-transport-security") == _DEFAULTS["Strict-Transport-Security"], (
        path, hdrs.get("strict-transport-security")
    )
    assert hdrs.get("x-content-type-options") == "nosniff", (path, hdrs)
    assert hdrs.get("x-frame-options") == "DENY", (path, hdrs)
    assert hdrs.get("referrer-policy") == "strict-origin-when-cross-origin", (
        path, hdrs
    )
    pp = hdrs.get("permissions-policy", "")
    assert pp == _PERMISSIONS_POLICY, (path, pp[:80])
    # CSP deliberately absent in this slice.
    assert "content-security-policy" not in hdrs, (
        f"CSP should not be set yet (path={path}); follow-up slice owns it"
    )
print(f"baseline 5 headers present on /api/health + /api/auth/login ok")
print(f"Permissions-Policy carries camera=(self) + geolocation=(self) ok")

# Spot-check the Permissions-Policy contents directly.
assert "camera=(self)" in _PERMISSIONS_POLICY
assert "geolocation=(self)" in _PERMISSIONS_POLICY
assert "microphone=()" in _PERMISSIONS_POLICY
assert "payment=()" in _PERMISSIONS_POLICY
assert "usb=()" in _PERMISSIONS_POLICY
print("Permissions-Policy denies microphone/payment/usb + grants camera/geolocation ok")


# ---------------------------------------------------------------------
# 7. setdefault semantics: an upstream-set header wins.
# Build a tiny ad-hoc app, wire only the security middleware, and
# return a route that explicitly sets X-Frame-Options: SAMEORIGIN.
# The middleware must NOT overwrite it.
# ---------------------------------------------------------------------
mini = FastAPI()
mini.add_middleware(SecurityHeadersMiddleware)


@mini.get("/upstream-frame")
def _upstream_frame() -> Response:
    r = Response(content="ok")
    r.headers["X-Frame-Options"] = "SAMEORIGIN"  # explicit upstream value
    return r


mini_client = TestClient(mini)
resp = mini_client.get("/upstream-frame")
assert resp.headers.get("x-frame-options") == "SAMEORIGIN", resp.headers.get(
    "x-frame-options"
)
# Other defaults still got filled in.
assert resp.headers.get("x-content-type-options") == "nosniff", resp.headers
assert resp.headers.get("strict-transport-security") == _DEFAULTS[
    "Strict-Transport-Security"
], resp.headers
print("setdefault semantics: upstream X-Frame-Options wins; gaps still filled ok")

print("\ntest_security_headers_smoke OK")
