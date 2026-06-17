"""Smoke for Redis-backed rate limiter (Phase B1).

Exercises the limiter dependency against a live local Redis. Covers:
  - under-limit returns 200
  - over-limit returns 429 with Retry-After
  - per-IP scoping (two IPs get independent buckets)
  - Redis unreachable + fail-open: 200
  - Redis unreachable + fail-closed: 503

The fail-open/closed paths monkeypatch `check_rate_limit` to raise
`RateLimitBackendUnavailable` so we exercise the dep's error-handling
branch deterministically without taking Redis down for real.

The tests do not wire any real route. B2-B4 do that.
"""

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import redis_rate_limit as rrl  # noqa: E402


_TAG = uuid.uuid4().hex[:8]

mini_app = FastAPI()

mini_app.add_api_route(
    "/burn",
    lambda: {"ok": True},
    methods=["GET"],
    dependencies=[
        Depends(rrl.rate_limit(bucket=f"b1-{_TAG}-burn", limit=3, window=60))
    ],
)

_open_dep = rrl.rate_limit(bucket=f"b1-{_TAG}-open", limit=1, window=60)
mini_app.add_api_route(
    "/openburn",
    lambda: {"ok": True},
    methods=["GET"],
    dependencies=[Depends(_open_dep)],
)

_closed_dep = rrl.rate_limit(bucket=f"b1-{_TAG}-closed", limit=1, window=60)
mini_app.add_api_route(
    "/closedburn",
    lambda: {"ok": True},
    methods=["GET"],
    dependencies=[Depends(_closed_dep)],
)

client = TestClient(mini_app)


# ---------------------------------------------------------------------------
# Under / over / per-IP
# ---------------------------------------------------------------------------

for i in range(1, 4):
    resp = client.get("/burn", headers={"X-Forwarded-For": "9.9.9.9"})
    assert resp.status_code == 200, (i, resp.status_code, resp.text)

resp = client.get("/burn", headers={"X-Forwarded-For": "9.9.9.9"})
assert resp.status_code == 429, resp.text
assert resp.json()["detail"] == "rate_limited", resp.text
assert resp.headers.get("Retry-After") == "60", resp.headers
print("dep 429 on overflow ok")


for i in range(1, 4):
    resp = client.get("/burn", headers={"X-Forwarded-For": "8.8.8.8"})
    assert resp.status_code == 200, (i, resp.status_code, resp.text)
print("per-ip scoping ok")


# ---------------------------------------------------------------------------
# Fail-open: monkeypatch check_rate_limit to raise BackendUnavailable.
# ---------------------------------------------------------------------------

_original_check = rrl.check_rate_limit


async def _broken_check(**kwargs):
    raise rrl.RateLimitBackendUnavailable("simulated redis down")


assert rrl.RATE_LIMIT_FAIL_OPEN is True, rrl.RATE_LIMIT_FAIL_OPEN
rrl.check_rate_limit = _broken_check
try:
    resp = client.get("/openburn", headers={"X-Forwarded-For": "7.7.7.7"})
    assert resp.status_code == 200, resp.text
    print("fail-open allows ok")
finally:
    rrl.check_rate_limit = _original_check


# ---------------------------------------------------------------------------
# Fail-closed: flip module-level flag, monkeypatch, expect 503.
# ---------------------------------------------------------------------------

_original_flag = rrl.RATE_LIMIT_FAIL_OPEN
rrl.RATE_LIMIT_FAIL_OPEN = False
rrl.check_rate_limit = _broken_check
try:
    resp = client.get("/closedburn", headers={"X-Forwarded-For": "6.6.6.6"})
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "rate_limit_backend_unavailable", resp.text
    print("fail-closed returns 503 ok")
finally:
    rrl.check_rate_limit = _original_check
    rrl.RATE_LIMIT_FAIL_OPEN = _original_flag


# ---------------------------------------------------------------------------
# Cleanup: flush our scoped keys
# ---------------------------------------------------------------------------

redis_client = rrl.get_client()
cursor = 0
while True:
    cursor, keys = redis_client.scan(cursor=cursor, match=f"rl:b1-{_TAG}-*:*")
    if keys:
        redis_client.delete(*keys)
    if cursor == 0:
        break
redis_client.close()
print("cleanup done")
print("test_redis_rate_limit_smoke OK")
