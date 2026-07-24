"""Signed tokens for self-service appointment links.

Reschedule and cancel links carry a JWT bound to a single appointment ID
and a single purpose; both share the ``RESCHEDULE_TOKEN_SECRET`` (the
purpose claim differentiates them).

G1 hardening:

  - **Shorter, purpose-specific TTLs.** Previously 60d; now 30d **and**
    capped by the appointment's own `slot_start_at` so a token cannot
    outlive the appointment it points at. Specifically:

      reschedule: min(now + 30d, slot_start_at + 1d)  ← 1d grace for no-show
      cancel    : min(now + 30d, slot_start_at + 1d)  ← same

  - **Explicit revocation.** Each appointment carries
    `tokens_invalidated_at`. Cancel + reschedule call
    `revoke_appointment_tokens(appt)` which bumps the timestamp.
    Any token minted before that bump fails verification with the
    same generic `InvalidBookingToken` the router maps to 404 — so
    a leaked email link stops working the moment the customer
    cancels or reschedules.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from jwt.exceptions import InvalidTokenError

from config.settings import (
    PUBLIC_SITE_URL,
    RESCHEDULE_TOKEN_SECRET,
)

ALGORITHM = "HS256"

Purpose = Literal["reschedule", "cancel"]

# G1: tightened from 60 days. The slot-bound cap below usually
# fires first for real bookings (most appointments are within a few
# weeks), so these are the upper bound for far-future appointments only.
_DEFAULT_TTL_DAYS: dict[str, int] = {
    "reschedule": 30,
    "cancel": 30,
}

# G1: how far past `slot_start_at` each purpose remains valid.
# Reschedule/cancel get +1 day so an admin can still process a
# no-show via the customer-side link if needed.
_SLOT_BOUND_DAYS: dict[str, int] = {
    "reschedule": 1,
    "cancel": 1,
}


class InvalidBookingToken(Exception):
    """Raised when a self-service token fails to decode or validate."""


def _secret_for(purpose: Purpose) -> str:
    return RESCHEDULE_TOKEN_SECRET


def _exp_for(slot_start_at: datetime, purpose: Purpose, now: datetime) -> datetime:
    """Pick the tighter of (now + default TTL) and (slot_start_at + bound).

    The slot-bound cap is the meaningful one for real bookings — most
    appointments are within a few weeks, so the default ceiling rarely
    binds. Far-future appointments hit the default first.
    """
    ttl_days = _DEFAULT_TTL_DAYS[purpose]
    bound_days = _SLOT_BOUND_DAYS[purpose]

    default_exp = now + timedelta(days=ttl_days)
    slot_exp = slot_start_at + timedelta(days=bound_days)

    # If the appointment is already in the past beyond the slot bound,
    # the slot_exp will be < now; we still mint a token but it will be
    # immediately expired on decode — that's the correct behavior, no
    # extra special-case needed.
    return min(default_exp, slot_exp)


def mint_token(appointment, purpose: Purpose) -> str:
    """Mint a purpose-bound token for `appointment`.

    `appointment` must expose `.id` and `.slot_start_at` (the SQLAlchemy
    `Appointment` model, normally). Taking the row instead of the bare
    ID lets us cap TTL by `slot_start_at` without an extra DB hop.
    """
    now = datetime.now(timezone.utc)
    slot_start = appointment.slot_start_at
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=timezone.utc)

    claims = {
        "sub": str(appointment.id),
        "purpose": purpose,
        "iat": now,
        "exp": _exp_for(slot_start, purpose, now),
    }
    return jwt.encode(claims, _secret_for(purpose), algorithm=ALGORITHM)


def verify_token(token: str, expected_purpose: Purpose) -> dict:
    """Decode + validate a token. Returns the full claims dict so the
    caller can compare `iat` against `appointment.tokens_invalidated_at`.

    Raises `InvalidBookingToken` (always — every failure path collapses
    to the same exception so the router can't accidentally leak which
    check failed).
    """
    try:
        claims = jwt.decode(token, _secret_for(expected_purpose), algorithms=[ALGORITHM])
    except InvalidTokenError as exc:
        raise InvalidBookingToken("token decode failed") from exc

    if claims.get("purpose") != expected_purpose:
        raise InvalidBookingToken("token purpose mismatch")

    sub = claims.get("sub")
    try:
        int(sub)  # validate shape, caller uses claims["sub"] downstream
    except (TypeError, ValueError) as exc:
        raise InvalidBookingToken("token missing appointment id") from exc

    if "iat" not in claims:
        raise InvalidBookingToken("token missing iat")

    return claims


def ensure_not_revoked(claims: dict, appointment) -> None:
    """Compare the token's `iat` against `appointment.tokens_invalidated_at`.

    Raises `InvalidBookingToken` if the token was minted before the
    appointment's last revocation event.
    """
    invalidated = appointment.tokens_invalidated_at
    if invalidated is None:
        return

    iat = claims.get("iat")
    if iat is None:
        raise InvalidBookingToken("token missing iat")

    # PyJWT decodes `iat` to either an int (unix seconds) or a datetime
    # depending on version + options. Normalize to a tz-aware datetime
    # so the comparison with `invalidated` (which is TIMESTAMPTZ) works.
    if isinstance(iat, (int, float)):
        iat_dt = datetime.fromtimestamp(iat, tz=timezone.utc)
    else:
        iat_dt = iat
        if iat_dt.tzinfo is None:
            iat_dt = iat_dt.replace(tzinfo=timezone.utc)

    if invalidated.tzinfo is None:
        invalidated = invalidated.replace(tzinfo=timezone.utc)

    if iat_dt < invalidated:
        raise InvalidBookingToken("token revoked")


def revoke_appointment_tokens(appointment) -> None:
    """Bump `appointment.tokens_invalidated_at` to now.

    Call from cancel + reschedule routes BEFORE the commit so the
    invalidation lands in the same transaction as the status change.
    The caller is responsible for `db.commit()`.
    """
    appointment.tokens_invalidated_at = datetime.now(timezone.utc)


def reschedule_url(appointment) -> str:
    return f"{PUBLIC_SITE_URL.rstrip('/')}/reschedule/{mint_token(appointment, 'reschedule')}"


def cancel_url(appointment) -> str:
    return f"{PUBLIC_SITE_URL.rstrip('/')}/cancel/{mint_token(appointment, 'cancel')}"

