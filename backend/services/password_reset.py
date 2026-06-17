"""Password reset request + confirm flow.

Phase D4 of SECURITY_REMEDIATION_PLAN.md. The schema (migration 002)
has carried `password_reset_tokens(token_hash, expires_at, used_at)`
since the very first commit, waiting for a flow to land on top of it.
This module is that flow.

Token shape: a 256-bit random value from `secrets.token_urlsafe(32)`
goes into the reset URL emailed to the customer. **Only the
SHA-256 hex digest is stored.** A DB leak therefore exposes hashes
that can't be used to consume any reset link — the attacker would
need both the leaked row AND the matching plaintext from the user's
inbox.

Single use: a successful confirm marks `used_at`, swaps the user's
`hashed_password` (via D6's helper), and bumps `users.token_version`
(D2's revocation primitive) so every existing JWT for that user dies.
A compromise that uses a reset link auto-evicts every session the
real owner had open.

Anti-enumeration: `request_reset` always returns silently regardless
of whether the email maps to a user. The caller-visible response is
the same `204 No Content` on the existing-email path and the
non-existent-email path. Timing leakage is minimised by routing the
email send through FastAPI's `BackgroundTasks` so the request returns
before any SMTP I/O. A per-email rate limit makes whatever timing
signal remains far too slow to scrape.

Operator handoff when SMTP is not wired: `email_transport` falls back
to a `NullEmailTransport` that logs the rendered message body
through `logging`. The reset URL ends up in journalctl — an admin
who needs to manually deliver a reset can pull it out without
exposing the link in code or storing the plaintext.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from config.settings import PUBLIC_SITE_URL
from database.auth import hash_password
from database.models import PasswordResetToken, User
from services.email_transport import EmailMessagePayload, get_email_transport

log = logging.getLogger(__name__)


# Token lifetime — short on purpose. Long enough that a customer
# checking email during dinner still works, short enough that a
# leaked email surfacing later is harmless.
TOKEN_TTL_MINUTES = 30


def _hash_token(plaintext: str) -> str:
    """Return the SHA-256 hex digest stored in `password_reset_tokens`."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _render_email(user: User, reset_url: str) -> EmailMessagePayload:
    """Delegate to services/notification_templates so the email picks up
    the boutique chrome (logo header, HTML body) shared by every other
    transactional message. The canonical renderer is
    ``render_password_reset_request``; this thin wrapper exists so the
    request flow above keeps its narrow internal contract.
    """
    from services.notification_templates import render_password_reset_request

    rendered = render_password_reset_request(
        user=user, reset_url=reset_url, ttl_minutes=TOKEN_TTL_MINUTES
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _build_reset_url(plaintext_token: str) -> str:
    base = (PUBLIC_SITE_URL or "").rstrip("/")
    return f"{base}/auth/password-reset/confirm?token={plaintext_token}"


def _send_reset_email(user: User, reset_url: str) -> None:
    """Best-effort dispatch. Any exception is logged and swallowed so a
    transient SMTP failure does not leak "this email exists" through
    a differing response status. The reset row was already written;
    an admin can re-invite the customer if mail is wedged."""
    try:
        get_email_transport().send(_render_email(user, reset_url))
    except Exception:  # noqa: BLE001
        log.exception("password_reset.email_dispatch_failed user_id=%s", user.id)


def notify_password_changed(
    user: User, *, changed_at: datetime | None = None
) -> None:
    """Public tripwire: dispatch the 'your password just changed' email.

    Used both internally by ``confirm_reset`` and externally by the
    self-service admin change-password handler so both password
    mutation paths leave the user an out-of-band notification. The
    direct-SMTP pattern (no ``record_event``) is intentional — the
    reset path established the convention that password emails ship
    on a best-effort, no-audit-row path, and the self-service path
    follows it for consistency.
    """
    _send_password_changed_email(
        user, changed_at or datetime.now(timezone.utc)
    )


def _send_password_changed_email(user: User, changed_at: datetime) -> None:
    """Confirmation dispatched after a successful reset. Same best-effort
    pattern as ``_send_reset_email`` — the password change already
    committed; an SMTP hiccup shouldn't poison a successful security
    operation. The exception trace lands in logs so an operator can
    re-notify out-of-band if it matters."""
    try:
        from services.notification_templates import render_password_changed

        rendered = render_password_changed(user=user, changed_at=changed_at)
        get_email_transport().send(
            EmailMessagePayload(
                to=user.email,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "password_reset.confirm_email_dispatch_failed user_id=%s", user.id
        )


def _mint_reset_for_user(db: Session, *, user: User) -> str:
    """Mint a reset token row for an already-authorized user object.

    Shared by the public forgot-password flow and authenticated admin
    reset triggers so both paths produce the same token shape, TTL,
    invalidation semantics, and email template.
    """
    now = datetime.now(timezone.utc)
    # Invalidate any prior unused tokens for this user so a fresh
    # request always replaces stale ones — important for the "I lost
    # the email, send another" UX and to keep token lookup unique
    # per active reset.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).update({"used_at": now}, synchronize_session=False)

    plaintext = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    db.add(row)
    db.commit()
    return plaintext


def request_reset_for_user(db: Session, *, user: User) -> bool:
    """Generate and send a reset link for a specific active user.

    This is the authenticated sibling of ``request_reset``. It keeps
    the same best-effort email dispatch and returns False only when
    the supplied user is not eligible for password reset.
    """
    if user is None or not user.is_active:
        return False
    plaintext = _mint_reset_for_user(db, user=user)
    _send_reset_email(user, _build_reset_url(plaintext))
    return True


def request_reset(db: Session, *, email: str) -> bool:
    """Generate a single-use reset token for the user matching `email`.

    Returns True if a token was minted (i.e. the email matched an
    active user) and False otherwise. The caller MUST NOT surface
    this boolean to the network — it exists for the test smoke and
    for future internal callers. The HTTP route returns 204 in both
    branches so the client cannot enumerate accounts.

    Side effects on the True branch:
      - any previous unused / unexpired token rows for the user are
        marked `used_at` so this becomes the only valid link
      - new row written with `token_hash = sha256(plaintext)`,
        `expires_at = now() + TOKEN_TTL_MINUTES`
      - reset email rendered and sent via the configured transport
    """
    user = (
        db.query(User)
        .filter(func.lower(User.email) == email.strip().lower())
        .first()
    )
    if user is None or not user.is_active:
        return False
    return request_reset_for_user(db, user=user)


class ConfirmResetFailure(Exception):
    """Raised when a confirm call cannot complete. Always maps to the
    same generic 400 at the HTTP layer — the route handler does not
    expose which specific check failed so a caller cannot probe the
    token state."""


def confirm_reset(db: Session, *, token: str, new_password: str) -> User:
    """Consume a reset token, set the user's new password, and revoke
    every existing session for that user.

    Raises `ConfirmResetFailure` if the token is missing, malformed,
    already used, expired, or belongs to a deactivated user. The HTTP
    layer collapses the exception to a single 400 status with a
    fixed detail string so token state cannot be probed by the
    response shape.

    On success: writes the new bcrypt hash, stamps `used_at` on the
    token row, and bumps `users.token_version` so every JWT minted
    for this user before the reset is now stale.
    """
    if not token:
        raise ConfirmResetFailure("missing token")
    token_hash = _hash_token(token)
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None:
        raise ConfirmResetFailure("token not found")
    if row.used_at is not None:
        raise ConfirmResetFailure("token already used")
    if row.expires_at <= now:
        raise ConfirmResetFailure("token expired")

    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or not user.is_active:
        raise ConfirmResetFailure("user not eligible")

    user.hashed_password = hash_password(new_password)
    # D2 revocation primitive: every JWT issued before this commit
    # dies. The new login mints a token against token_version + 1.
    user.token_version = (user.token_version or 0) + 1
    row.used_at = now
    db.add(user)
    db.add(row)
    db.commit()

    # Security tripwire: the account holder gets a confirmation that the
    # password just changed. If they receive it without expecting it,
    # the reset email was compromised and they can re-secure the account
    # before the attacker establishes a new pattern.
    _send_password_changed_email(user, now)
    return user
