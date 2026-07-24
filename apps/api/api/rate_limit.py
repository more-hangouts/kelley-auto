"""Per-user sliding-window rate limiter for money-changing endpoints.

Phase 13. The staff dashboard already requires authentication, so the
attack surface here is "buggy client retry loop" or "compromised
session," not anonymous probes — the portal rate limiter (per-IP)
already covers that. Staff routes share one bucket per user across
every rate-limited endpoint so a runaway loop on one verb burns into
the budget for every other money-changing verb on the same account.
That symptom (a 429 across the board) is the desired signal.

Why per-user, not per-IP: the shop's admins all NAT through one
office IP. A per-IP limit would either be too tight (one staffer
trips it for everyone) or so loose it doesn't bite. A per-user limit
is what you actually want for an authenticated surface.

State is in-process and protected by a lock. Single uvicorn worker
today; if the deploy ever scales to multiple workers, swap the
backing dict for Redis or similar.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, HTTPException, status

from database.auth import get_current_user
from database.models import User

log = logging.getLogger(__name__)


# 60 actions per minute per user is generous for a human clicker (one
# action per second sustained) and tight enough to catch a runaway
# retry loop within a few seconds of when it starts.
_LIMIT_PER_MIN = 60
_WINDOW_SEC = 60

_lock = threading.Lock()
_state: dict[int, deque] = defaultdict(deque)


def _check_user(user_id: int) -> None:
    """Trim the user's bucket and reject if it would overflow.

    Raises ``HTTPException(429)`` so the caller can fail fast without
    duplicating the response shape across routers.
    """
    now = time.monotonic()
    cutoff = now - _WINDOW_SEC
    with _lock:
        bucket = _state[user_id]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _LIMIT_PER_MIN:
            log.warning(
                "rate_limit.staff_blocked",
                extra={"user_id": user_id, "in_window": len(bucket)},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "rate_limited",
                    "message": (
                        "Too many money-changing requests. Wait a minute "
                        "and try again."
                    ),
                },
            )
        bucket.append(now)


def staff_money_rate_limit(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """FastAPI dependency: enforces the limit and returns the user.

    Routes that already pull ``get_current_user`` swap their
    ``Depends(get_current_user)`` for ``Depends(staff_money_rate_limit)``;
    the dependency does both jobs.
    """
    _check_user(user.id)
    return user


def _reset_state() -> None:
    """Test helper: drains the buckets so a smoke can re-run cleanly."""
    with _lock:
        _state.clear()
