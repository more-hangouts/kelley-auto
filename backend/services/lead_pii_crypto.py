"""Fernet-encrypted at-rest storage for BHPH lead-application PII.

Mirrors the proven ``services/integration_tokens.py`` crypto (Phase C1 of
SECURITY_REMEDIATION_PLAN.md) but on a SEPARATE key so a compromise of one
does not expose the other. The sensitive fields of a buy-here-pay-here
application — date of birth, driver's license number, SSN (future), and the
home address — are stored as Fernet ciphertext in BYTEA columns and only
decrypted inside the permission-gated, audited application endpoint.

Key strategy: ``LEAD_PII_KEYS`` is a comma-separated list of Fernet keys,
NEWEST FIRST, handed to ``MultiFernet`` which encrypts with the first key
and decrypts with whichever key produced a given ciphertext. Rotate by
prepending a new key, letting traffic rewrite rows, then dropping the old
one. There is NO plaintext fallback here — unlike the integration-token
transition, this table is greenfield, so a decrypt failure is a hard error
rather than a silent plaintext leak.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from cryptography.fernet import Fernet, MultiFernet

from config import settings as _settings

log = logging.getLogger(__name__)


class LeadPiiCryptoUnconfigured(RuntimeError):
    """Raised when encrypt/decrypt is called with no ``LEAD_PII_KEYS`` set.

    Raised on use, not import, so the app still boots in environments that
    haven't provisioned a PII key yet — only code paths that actually touch
    application PII see the error.
    """


_cipher: MultiFernet | None = None


def _get_cipher() -> MultiFernet:
    """Lazily build a MultiFernet from ``LEAD_PII_KEYS`` (read via attribute
    access so a rotation/test key-swap is observable without a restart)."""
    global _cipher
    if _cipher is None:
        keys = _settings.LEAD_PII_KEYS
        if not keys:
            raise LeadPiiCryptoUnconfigured(
                "LEAD_PII_KEYS is empty — set at least one Fernet key in the "
                "environment before reading or writing lead-application PII"
            )
        _cipher = MultiFernet(
            [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        )
    return _cipher


def _reset_cipher_for_testing() -> None:
    """Drop the cached MultiFernet so a smoke can swap keys mid-run."""
    global _cipher
    _cipher = None


def encrypt(plaintext: str) -> bytes:
    """Encrypt a string with the newest configured key → BYTEA-ready bytes."""
    if not isinstance(plaintext, str):
        raise TypeError(f"encrypt expected str, got {type(plaintext).__name__}")
    return _get_cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt Fernet ciphertext back to the original string. Raises
    ``InvalidToken`` if no configured key validates it."""
    return _get_cipher().decrypt(bytes(ciphertext)).decode("utf-8")


def encrypt_optional(plaintext: str | None) -> bytes | None:
    """Encrypt, or pass through None/empty as None (so a blank field stores
    NULL rather than encrypted empty-string noise)."""
    if plaintext is None or plaintext == "":
        return None
    return encrypt(plaintext)


def decrypt_optional(ciphertext: bytes | None) -> str | None:
    """Decrypt, or pass through None."""
    if ciphertext is None:
        return None
    return decrypt(ciphertext)


def encrypt_json(value: Any | None) -> bytes | None:
    """Encrypt a JSON-serializable structure (e.g. the address dict). None or
    an empty dict stores NULL."""
    if value is None or value == {} or value == []:
        return None
    return encrypt(json.dumps(value, separators=(",", ":"), sort_keys=True))


def decrypt_json(ciphertext: bytes | None) -> Any | None:
    """Decrypt and JSON-parse, or pass through None."""
    if ciphertext is None:
        return None
    return json.loads(decrypt(ciphertext))
