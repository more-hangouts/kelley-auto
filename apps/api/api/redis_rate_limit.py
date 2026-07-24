"""Redis-backed rate limiter for public / anonymous surfaces.

Phase B1 of SECURITY_REMEDIATION_PLAN.md. Complements api/rate_limit.py
(per-user in-process; for authenticated staff money endpoints).

This module covers the inverse case: public POSTs (login, sales PIN,
booking widget, portal token lookups) where the actor is an IP, not a
user, and where state needs to survive across worker processes so a
distributed attacker can't rotate connections to bypass.

Buckets are fixed-window counters keyed on `rl:<bucket>:<scoped>` with
TTL equal to the window length. Fixed-window is simpler than
sliding-log and adequate for our budgets (single-digit per-minute
buckets where boundary slop is in the noise).

Implementation note: we use redis-py's *sync* client wrapped in
asyncio.to_thread. The async client pins connections to the creating
event loop, which is a poor fit for FastAPI's TestClient (one loop per
request) and for any future multi-worker uvicorn deploy. Sync IO to a
local Unix-domain-equivalent loopback Redis is sub-millisecond and the
threadpool hop is negligible.

Fail-open behavior: when Redis is unreachable, the limiter degrades by
the RATE_LIMIT_FAIL_OPEN env flag (true → log warning and allow; false
→ 503). Keep fail-open true during initial rollout so a Redis blip
does not 503 the site while no real route has been wired yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

import redis as redis_sync
from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from config.settings import RATE_LIMIT_FAIL_OPEN, REDIS_URL

log = logging.getLogger(__name__)

_redis: Optional[redis_sync.Redis] = None


def get_client() -> redis_sync.Redis:
    """Lazily build a singleton sync Redis client.

    The client uses a connection pool internally; idle connections are
    reused across calls. Timeouts kept short so a Redis hang cannot
    pile up request latency.
    """
    global _redis
    if _redis is None:
        _redis = redis_sync.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _redis


async def close_client() -> None:
    """Drain the connection pool. Hook from server shutdown.

    Sync client cleanup is itself sync; we run it in a thread so the
    shutdown handler can stay async.
    """
    global _redis
    if _redis is not None:
        client = _redis
        _redis = None
        try:
            await asyncio.to_thread(client.close)
        except Exception as exc:  # noqa: BLE001
            log.warning("rate_limit.close_client_failed", extra={"error": str(exc)})


class RateLimitBackendUnavailable(Exception):
    """Redis is unreachable. Caller decides whether to fail-open or 503."""


def check_rate_limit_sync(
    *, key: str, limit: int, window: int
) -> tuple[bool, int]:
    """Sync variant: increment counter at `key` with TTL `window`.

    Returns (allowed, count). Raises RateLimitBackendUnavailable on
    Redis failure. Sync routes (the existing login + PIN handlers) call
    this directly for per-identifier checks; the dep factory wraps it
    in asyncio.to_thread for the per-IP path.
    """
    try:
        client = get_client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        count, _ = pipe.execute()
    except RedisError as exc:
        raise RateLimitBackendUnavailable(str(exc)) from exc
    count = int(count)
    return count <= limit, count


async def check_rate_limit(
    *, key: str, limit: int, window: int
) -> tuple[bool, int]:
    """Increment counter at `key` with TTL `window`. Return (allowed, count).

    Allowed when post-increment count is <= limit. The TTL is refreshed
    on every increment which is a known fixed-window characteristic —
    callers that need strict sliding behavior should layer on top.
    """
    return await asyncio.to_thread(
        check_rate_limit_sync, key=key, limit=limit, window=window
    )


def flush_for_testing(patterns: list[str] | None = None) -> None:
    """Test helper: flush rate-limit keys so an unrelated smoke can
    exercise rapid-fire flows on a rate-limited route without tripping
    the production limiter.

    Smokes that want to *test* the limiter should NOT call this. Smokes
    that hit a rate-limited route incidentally (e.g., test_sales_auth
    exercising row-lockout via 5 bad PIN attempts) should call it once
    at entry.

    Defaults to the buckets touched by the B2 wiring; pass `patterns`
    to scope to a specific bucket family.
    """
    redis = get_client()
    pats = patterns or [
        "rl:login_ip:*",
        "rl:login_email:*",
        "rl:pin_ip:*",
        "rl:pin_identifier:*",
        "rl:booking_create_ip:*",
        "rl:booking_telemetry_ip:*",
        "rl:booking_profile_ip:*",
        "rl:booking_confirm_ip:*",
        "rl:booking_confirm_email:*",
        "rl:booking_token_ip:*",
        "rl:portal_ip:*",
        "rl:portal_key:*",
        "rl:password_reset_ip:*",
        "rl:password_reset_email:*",
        "rl:password_reset_confirm_ip:*",
    ]
    for pat in pats:
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor=cursor, match=pat)
            if keys:
                redis.delete(*keys)
            if cursor == 0:
                break


def enforce_or_raise(
    *,
    bucket: str,
    scoped: str,
    limit: int,
    window: int,
    request: Request | None = None,
) -> None:
    """Sync helper for inline per-identifier checks in sync routes.

    Raises 429 on overflow, 503 (or pass-through) on backend failure
    matching the dep factory's semantics. Use this when the key depends
    on a request body field that the FastAPI dep can't see ergonomically
    (e.g., parsed email/identifier).

    Pass `request` to honor the TestClient bypass: when the caller is
    starlette's TestClient and no X-Forwarded-For is set, the limit is
    skipped so unrelated smoke tests do not 429-mask their own
    assertions. Smokes that want to test the limiter set X-Forwarded-For
    explicitly.
    """
    if request is not None and _client_ip(request) == _TESTCLIENT_BYPASS:
        return
    key = f"rl:{bucket}:{scoped}"
    try:
        allowed, count = check_rate_limit_sync(
            key=key, limit=limit, window=window
        )
    except RateLimitBackendUnavailable as exc:
        if RATE_LIMIT_FAIL_OPEN:
            log.warning(
                "rate_limit.backend_unavailable",
                extra={"bucket": bucket, "scoped": scoped, "error": str(exc)},
            )
            return
        log.error(
            "rate_limit.backend_unavailable_fail_closed",
            extra={"bucket": bucket, "scoped": scoped, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rate_limit_backend_unavailable",
        )

    if not allowed:
        log.info(
            "rate_limit.exceeded",
            extra={
                "bucket": bucket,
                "scoped": scoped,
                "count": count,
                "limit": limit,
                "window": window,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limited",
            headers={"Retry-After": str(window)},
        )


_TESTCLIENT_BYPASS = "__bypass_testclient__"


def _client_ip(request: Request) -> str:
    """Resolve the client IP, honoring nginx's X-Forwarded-For prepend.

    Nginx is configured to set X-Forwarded-For; if absent (direct
    loopback test, or future deploys), fall back to request.client.host.

    Returns the `_TESTCLIENT_BYPASS` sentinel when starlette's TestClient
    is the caller AND no X-Forwarded-For is set. The dep factory below
    checks for this sentinel and skips the limiter entirely so existing
    smoke tests that incidentally hit a rate-limited route do not 429
    themselves. Smokes that want to test the limiter (or simulate a real
    production IP) opt in by setting X-Forwarded-For per-request.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        host = request.client.host
        if host == "testclient":
            return _TESTCLIENT_BYPASS
        return host
    return "unknown"


def rate_limit(
    *,
    bucket: str,
    limit: int,
    window: int,
    key_fn: Callable[[Request], str] | None = None,
) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency enforcing `limit`/`window` for `bucket`.

    `bucket` is a stable string (e.g. "login_ip", "pin_email") so logs
    and metrics can attribute hits. The default scope is the client IP;
    pass `key_fn` to scope per-account or per-email instead.

    The returned dependency raises 429 on overflow with a Retry-After
    header equal to the window.
    """

    async def dep(request: Request) -> None:
        scoped = key_fn(request) if key_fn else _client_ip(request)
        if scoped == _TESTCLIENT_BYPASS:
            return
        key = f"rl:{bucket}:{scoped}"
        try:
            allowed, count = await check_rate_limit(
                key=key, limit=limit, window=window
            )
        except RateLimitBackendUnavailable as exc:
            if RATE_LIMIT_FAIL_OPEN:
                log.warning(
                    "rate_limit.backend_unavailable",
                    extra={
                        "bucket": bucket,
                        "scoped": scoped,
                        "error": str(exc),
                    },
                )
                return
            log.error(
                "rate_limit.backend_unavailable_fail_closed",
                extra={
                    "bucket": bucket,
                    "scoped": scoped,
                    "error": str(exc),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="rate_limit_backend_unavailable",
            )

        if not allowed:
            log.info(
                "rate_limit.exceeded",
                extra={
                    "bucket": bucket,
                    "scoped": scoped,
                    "count": count,
                    "limit": limit,
                    "window": window,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limited",
                headers={"Retry-After": str(window)},
            )

    return dep
