"""HMAC-SHA256 stamping for signed quotes.

Phase C3 of SECURITY_REMEDIATION_PLAN.md. The schema-level immutability
trigger (migration 062) blocks tampering with the signature columns
after a quote is signed; the HMAC stamp lets a downstream verifier
prove that the signature, signer identity, and stable business terms
have not been silently rewritten by a code path that pre-dates the
trigger (or by direct ad-hoc SQL in an environment without the trigger).

Canonical payload format — version-prefixed so we can extend without
invalidating old stamps. The signature image itself is large (20-40KB of
base64) so we include its SHA-256 hex rather than the raw bytes; the
hash binds the image to the HMAC just as effectively and keeps the
canonical string short enough to be useful in admin queries.

```
v=1|quote_id=:id|quote_number=:qn|event_id=:eid|contact_id=:cid
  |subtotal_cents=:s|discount_cents=:d|tax_cents=:t|total_cents=:tot
  |signature_signed_at=:iso|signature_name=:name|signature_ip=:ip
  |signature_user_agent=:ua|signature_sha256=:sha
```

Single key on purpose: rotation would invalidate every prior stamp on
an evidentiary record. If rotation is ever needed, treat it as its
own slice with a `signature_hmac_kid` column so old verifications
keep working.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from config import settings as _settings

log = logging.getLogger(__name__)


CANONICAL_VERSION = "1"


class QuoteSignatureHMACUnconfigured(RuntimeError):
    """Raised when stamp/verify is called without `QUOTE_SIGNATURE_KEY`.

    Stays on use rather than import so the app still boots in
    environments that haven't yet generated a key — only the actual
    signing paths fail loudly when the key is missing.
    """


class SignedQuoteFields(Protocol):
    """Structural typing of the quote columns the canonical payload reads.

    Using a Protocol instead of `Quote` lets the migration's backfill
    pass plain row records into the same canonicalisation routine,
    without dragging the SQLAlchemy ORM into a connection-level
    migration context.
    """

    id: int
    quote_number: str | None
    event_id: int
    contact_id: int
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    signature_base64: str | None
    signature_signed_at: datetime | None
    signature_name: str | None
    signature_ip: Any
    signature_user_agent: str | None


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _sig_sha256(signature_base64: str | None) -> str:
    """SHA-256 hex of the base64 string the customer's pen produced.

    Hashing the base64 (not the decoded image bytes) keeps the
    canonicalisation purely text-based and identical to what the
    migration sees when it reads the column directly from Postgres.
    """
    raw = (signature_base64 or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_payload(quote: SignedQuoteFields) -> bytes:
    """Build the bytes that the HMAC is computed over.

    Field order is fixed — adding a new field requires bumping
    CANONICAL_VERSION and writing migration 0XX_quote_signature_hmac_v2
    that re-stamps every row.
    """
    fields = [
        ("v", CANONICAL_VERSION),
        ("quote_id", str(quote.id)),
        ("quote_number", quote.quote_number or ""),
        ("event_id", str(quote.event_id)),
        ("contact_id", str(quote.contact_id)),
        ("subtotal_cents", str(quote.subtotal_cents or 0)),
        ("discount_cents", str(quote.discount_cents or 0)),
        ("tax_cents", str(quote.tax_cents or 0)),
        ("total_cents", str(quote.total_cents or 0)),
        ("signature_signed_at", _iso(quote.signature_signed_at)),
        ("signature_name", quote.signature_name or ""),
        ("signature_ip", str(quote.signature_ip) if quote.signature_ip else ""),
        ("signature_user_agent", quote.signature_user_agent or ""),
        ("signature_sha256", _sig_sha256(quote.signature_base64)),
    ]
    return "|".join(f"{k}={v}" for k, v in fields).encode("utf-8")


def compute_hmac(quote: SignedQuoteFields) -> str:
    """Return the hex HMAC-SHA256 for `quote` under the configured key.

    Raises QuoteSignatureHMACUnconfigured if the key is missing — this
    is a hard error on the signing path because an unstamped row would
    instantly trip the schema CHECK constraint added in migration 062.
    """
    key = _settings.QUOTE_SIGNATURE_KEY
    if not key:
        raise QuoteSignatureHMACUnconfigured(
            "QUOTE_SIGNATURE_KEY is not set — generate one and add it "
            "to .env before signing quotes."
        )
    digest = hmac.new(
        key.encode("utf-8"),
        canonical_payload(quote),
        hashlib.sha256,
    )
    return digest.hexdigest()


def stamp(quote) -> str:
    """Compute and assign `signature_hmac` on the SQLAlchemy quote row.

    Idempotent: re-stamping the same set of inputs yields the same
    hex string, so calling this twice in the same transaction is safe.
    The immutability trigger blocks any subsequent change to a non-null
    `signature_hmac`, so callers should only invoke this on the first
    transition into the signed state.
    """
    value = compute_hmac(quote)
    quote.signature_hmac = value
    return value


def verify(quote: SignedQuoteFields, expected: str | None = None) -> bool:
    """Constant-time verify `quote.signature_hmac` against a fresh compute.

    Pass `expected` to compare against a value loaded from somewhere
    other than the row itself (e.g. a forensic copy). When `expected`
    is `None` the method compares against the `signature_hmac` column
    on the row — this is the common verifier pattern.
    """
    current = expected if expected is not None else getattr(quote, "signature_hmac", None)
    if not current:
        return False
    computed = compute_hmac(quote)
    return hmac.compare_digest(computed, current)
