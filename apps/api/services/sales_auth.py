"""PIN auth operations for the Sales Portal.

The PIN is a 6-digit numeric code, hashed with bcrypt at the same cost
factor as passwords. Lockout is row-level: after `MAX_FAILED_ATTEMPTS`
failures within `LOCK_WINDOW`, `pin_locked_until` is set to
`now() + LOCK_DURATION`. Nginx burst-rate limit on
`/api/sales/auth/pin` is additive, not a substitute.

Identifier privacy: callers must NOT reveal whether an identifier
maps to a real user. `find_pin_user_by_identifier` returns the user
or None; the caller raises a uniform 401 in either case.

This module deliberately knows nothing about JWTs or HTTP. Token
minting lives in `database.auth`; the router glues the two together.
"""

from __future__ import annotations

import re
import secrets
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.auth import hash_password, verify_password
from database.models import User
from services.email_transport import EmailMessagePayload

log = logging.getLogger(__name__)

PIN_LENGTH = 6
PIN_REGEX = re.compile(r"^\d{6}$")

MAX_FAILED_ATTEMPTS = 5
LOCK_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


class PinError(Exception):
    """Base error for PIN operations."""


class InvalidPinFormat(PinError):
    """PIN does not match the required format (6 digits)."""


class PinAccountLocked(PinError):
    """PIN account is locked. `retry_after_seconds` is set."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("locked")
        self.retry_after_seconds = retry_after_seconds


@dataclass
class PinVerifyResult:
    user: User
    force_change: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_pin_format(pin: str) -> None:
    """Raise InvalidPinFormat unless the PIN is exactly 6 digits."""
    if not isinstance(pin, str) or not PIN_REGEX.match(pin):
        raise InvalidPinFormat("pin_must_be_6_digits")


def generate_pin() -> str:
    """Mint a uniformly-random 6-digit PIN.

    Uses secrets.randbelow to avoid modulo bias. We deliberately allow
    leading zeros (e.g. "002317") because the keypad accepts them.
    Sequential-pattern rejection happens at the UI layer, not here —
    the owner sees the PIN once, hands it to the stylist, and the
    stylist immediately picks a new one.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def set_pin(db: Session, user: User, raw_pin: str, *, force_change: bool = False) -> None:
    """Hash and store a PIN, clearing failure/lock state.

    `force_change=True` is used by the owner reset path: the stylist
    enters this PIN once, then the change-pin flow forces them to pick
    their own.
    """
    validate_pin_format(raw_pin)
    user.pin_hash = hash_password(raw_pin)
    user.pin_failed_count = 0
    user.pin_locked_until = None
    user.last_pin_used_at = None
    user.force_pin_change = force_change
    db.flush()


def clear_pin(db: Session, user: User) -> None:
    """Wipe PIN auth state — the user can no longer PIN-login."""
    user.pin_hash = None
    user.pin_failed_count = 0
    user.pin_locked_until = None
    user.last_pin_used_at = None
    user.force_pin_change = False
    db.flush()


def is_locked(user: User) -> bool:
    if user.pin_locked_until is None:
        return False
    return user.pin_locked_until > _now()


def lock_retry_after_seconds(user: User) -> int:
    if user.pin_locked_until is None:
        return 0
    delta = (user.pin_locked_until - _now()).total_seconds()
    return max(0, int(delta))


def _send_account_locked_email(user: User) -> None:
    """Best-effort lockout notice. Login failure state must persist even
    if SMTP is unavailable, so dispatch errors are logged and swallowed."""
    if not user.email or user.pin_locked_until is None:
        return
    try:
        from services import email_transport
        from services.notification_templates import render_account_locked

        rendered = render_account_locked(
            staff_user=user,
            locked_until=user.pin_locked_until,
        )
        email_transport.get_email_transport().send(
            EmailMessagePayload(
                to=user.email,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("sales_auth.account_locked_email_failed user_id=%s", user.id)


def find_pin_user_by_identifier(db: Session, identifier: str) -> User | None:
    """Return the user matching `identifier`, or None.

    `identifier` is matched case-insensitively against `username` or
    `email`. Only users with `role = 'sales'` and a populated
    `pin_hash` can PIN-login; admin users keep `pin_hash = NULL` and
    are filtered out so a leaked admin email does not become a PIN
    target.
    """
    if not isinstance(identifier, str):
        return None
    needle = identifier.strip().lower()
    if not needle:
        return None
    return (
        db.query(User)
        .filter(User.role == "sales")
        .filter(User.pin_hash.isnot(None))
        .filter(
            (func.lower(User.username) == needle)
            | (func.lower(User.email) == needle)
        )
        .first()
    )


def verify_pin(db: Session, user: User, raw_pin: str) -> PinVerifyResult:
    """Verify a PIN attempt against `user`.

    Returns `PinVerifyResult` on success; raises `PinAccountLocked` if
    the user is currently locked; returns None on bad PIN. The caller
    is responsible for translating None into a uniform 401 response so
    the existence of the identifier is not leaked.

    On success we reset the failure counter and stamp `last_pin_used_at`.
    On failure we increment the failure counter; if the counter reaches
    MAX_FAILED_ATTEMPTS we set `pin_locked_until = now() + LOCK_DURATION`
    and reset the counter so the next window starts clean after the
    lockout expires.
    """
    if user.pin_hash is None:
        # Never authenticate a user who has not been issued a PIN.
        # The router maps this to the same 401 as a missing user.
        raise _PinAuthFailed()

    if is_locked(user):
        raise PinAccountLocked(lock_retry_after_seconds(user))

    if not verify_password(raw_pin, user.pin_hash):
        user.pin_failed_count = (user.pin_failed_count or 0) + 1
        just_locked = False
        if user.pin_failed_count >= MAX_FAILED_ATTEMPTS:
            user.pin_locked_until = _now() + LOCK_DURATION
            user.pin_failed_count = 0
            just_locked = True
        db.flush()
        if just_locked:
            _send_account_locked_email(user)
        raise _PinAuthFailed()

    user.pin_failed_count = 0
    user.pin_locked_until = None
    user.last_pin_used_at = _now()
    db.flush()
    return PinVerifyResult(user=user, force_change=bool(user.force_pin_change))


class _PinAuthFailed(PinError):
    """Raised internally on bad PIN. Router translates to a uniform 401."""
