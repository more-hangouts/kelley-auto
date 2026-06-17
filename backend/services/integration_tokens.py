"""Fernet-encrypted at-rest storage for third-party integration tokens.

Phase C1 of SECURITY_REMEDIATION_PLAN.md. The `integration_tokens` table
will hold OAuth access/refresh pairs for whatever provider integrations
ship next (CRM sync, calendar, payment-gateway extras). Storing those
in plaintext is the HIGH finding this slice closes.

Key strategy: `INTEGRATION_TOKEN_KEYS` in the environment is a
comma-separated list of Fernet keys, NEWEST FIRST. We pass that list to
`MultiFernet`, which:
  - encrypts with the first key (rotation lands new traffic on the
    new key automatically), and
  - decrypts with whichever key in the list produced a given
    ciphertext (so the old key stays readable until traffic has
    rewritten every row).

To rotate: generate a new Fernet key, prepend it to the env var, deploy.
Let traffic naturally rewrite stored tokens for one cycle. Drop the
trailing old key from the env var. No service downtime.

Dual-read transition (this slice): writes always go to the
`*_ciphertext` BYTEA columns. Reads prefer ciphertext; if the row has
only the legacy plaintext column populated (e.g. an ad-hoc INSERT
that bypassed `set_token`), the service returns the plaintext and
emits a warning so we can find and migrate it. A follow-up slice will
remove the fallback once production has had a window with the new
write path live.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy.orm import Session

from config import settings as _settings
from database.models import IntegrationToken

log = logging.getLogger(__name__)


class IntegrationTokenCryptoUnconfigured(RuntimeError):
    """Raised when encrypt/decrypt is called without any keys configured.

    Settings load `INTEGRATION_TOKEN_KEYS` as an empty list when the env
    var is missing or blank. We raise on use rather than on import so
    the app still boots in environments that don't have an integration
    wired yet — only callers that actually touch tokens see the error.
    """


_cipher: MultiFernet | None = None


def _get_cipher() -> MultiFernet:
    """Lazily build a MultiFernet from the configured keys.

    Reads `_settings.INTEGRATION_TOKEN_KEYS` via attribute access (not a
    bare import) so a key-rotation reload — or a test that swaps keys
    mid-run — is observable without restarting the process. The cipher
    itself is cached; callers that mutate the keys list must call
    `_reset_cipher_for_testing()` to drop the cache.
    """
    global _cipher
    if _cipher is None:
        keys = _settings.INTEGRATION_TOKEN_KEYS
        if not keys:
            raise IntegrationTokenCryptoUnconfigured(
                "INTEGRATION_TOKEN_KEYS is empty — set at least one Fernet "
                "key in the environment before reading or writing "
                "integration tokens"
            )
        fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        _cipher = MultiFernet(fernets)
    return _cipher


def _reset_cipher_for_testing() -> None:
    """Drop the cached MultiFernet so a smoke can swap keys mid-run."""
    global _cipher
    _cipher = None


def encrypt(plaintext: str) -> bytes:
    """Encrypt a string token with the newest configured key.

    Returns the Fernet ciphertext as bytes, suitable for direct insert
    into a BYTEA column. Fernet output is base64-encoded and includes
    its own version byte, so the raw bytes are safe to round-trip.
    """
    if not isinstance(plaintext, str):
        raise TypeError(
            f"encrypt expected str, got {type(plaintext).__name__}"
        )
    return _get_cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt Fernet ciphertext back to the original string.

    Tries each key in `INTEGRATION_TOKEN_KEYS` in order; the first
    one that validates wins. Raises `InvalidToken` if every key fails,
    which is what we want — silently returning a partial value would
    let stale ciphertext leak through after a botched rotation.
    """
    return _get_cipher().decrypt(ciphertext).decode("utf-8")


def encrypt_optional(plaintext: str | None) -> bytes | None:
    """Convenience: encrypt or pass-through `None`."""
    if plaintext is None:
        return None
    return encrypt(plaintext)


def decrypt_optional(ciphertext: bytes | None) -> str | None:
    """Convenience: decrypt or pass-through `None`."""
    if ciphertext is None:
        return None
    return decrypt(bytes(ciphertext))


def _resolve_token_field(
    row: IntegrationToken,
    *,
    ciphertext_attr: str,
    plaintext_attr: str,
    provider: str,
) -> str | None:
    """Return the decrypted value for one of the two paired columns.

    Preference order:
      1. ciphertext column populated → decrypt and return.
      2. plaintext column populated → return plaintext, warn so the row
         can be migrated. This is the dual-read fallback for the C1
         transition window; it will be removed when the follow-up slice
         scrubs the plaintext columns.
      3. neither → return None.
    """
    ciphertext = getattr(row, ciphertext_attr)
    if ciphertext is not None:
        return decrypt(bytes(ciphertext))
    plaintext = getattr(row, plaintext_attr)
    if plaintext is not None:
        log.warning(
            "integration_tokens.plaintext_fallback",
            extra={
                "provider": provider,
                "field": plaintext_attr,
                "row_id": row.id,
            },
        )
        return plaintext
    return None


def get_token(db: Session, provider: str) -> dict[str, Any] | None:
    """Read the integration token row for `provider`, decrypted.

    Returns `None` if no row exists. Otherwise returns a dict with the
    decrypted access/refresh tokens plus the non-secret metadata
    columns. Callers should treat the dict as read-only.
    """
    row = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.provider == provider)
        .first()
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "provider": row.provider,
        "access_token": _resolve_token_field(
            row,
            ciphertext_attr="access_token_ciphertext",
            plaintext_attr="access_token",
            provider=provider,
        ),
        "refresh_token": _resolve_token_field(
            row,
            ciphertext_attr="refresh_token_ciphertext",
            plaintext_attr="refresh_token",
            provider=provider,
        ),
        "token_type": row.token_type,
        "expires_at": row.expires_at,
        "owner_uri": row.owner_uri,
        "organization_uri": row.organization_uri,
        "metadata": row.extra_metadata,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def set_token(
    db: Session,
    provider: str,
    *,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_type: str | None = None,
    expires_at: datetime | None = None,
    owner_uri: str | None = None,
    organization_uri: str | None = None,
    metadata: dict | None = None,
) -> IntegrationToken:
    """Upsert the integration token row for `provider`.

    Writes encrypt to the `*_ciphertext` columns. Any legacy plaintext
    column on an existing row is NULLed in the same write so a future
    decrypt failure can't quietly fall back to a stale plaintext value
    we thought we had migrated. The dual-read fallback only covers
    rows that have never been written through this helper.

    Pass `access_token=None` to leave the existing access token
    untouched; pass `""` to explicitly clear it.
    """
    row = (
        db.query(IntegrationToken)
        .filter(IntegrationToken.provider == provider)
        .first()
    )
    if row is None:
        row = IntegrationToken(provider=provider)
        db.add(row)

    if access_token is not None:
        row.access_token_ciphertext = encrypt(access_token) if access_token else None
        row.access_token = None
    if refresh_token is not None:
        row.refresh_token_ciphertext = encrypt(refresh_token) if refresh_token else None
        row.refresh_token = None
    if token_type is not None:
        row.token_type = token_type
    if expires_at is not None:
        row.expires_at = expires_at
    if owner_uri is not None:
        row.owner_uri = owner_uri
    if organization_uri is not None:
        row.organization_uri = organization_uri
    if metadata is not None:
        row.extra_metadata = metadata

    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row
