"""Meta webhook signature verification (X-Hub-Signature-256).

Meta signs each webhook POST with HMAC-SHA256 over the **raw request body**,
keyed by the app secret, sent as ``X-Hub-Signature-256: sha256=<hexdigest>``.
Unlike Twilio (URL + sorted params, SHA1), this is a straight body HMAC, so we
must hash the exact bytes received — not a re-serialized dict.

Docs: https://developers.facebook.com/docs/messenger-platform/webhooks#validate-payloads
"""

from __future__ import annotations

import hashlib
import hmac


def compute_signature(app_secret: str, raw_body: bytes) -> str:
    """Return the expected 'sha256=<hex>' header value for a raw body."""
    digest = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Constant-time check of the X-Hub-Signature-256 header."""
    if not header or not app_secret:
        return False
    expected = compute_signature(app_secret, raw_body)
    return hmac.compare_digest(expected, header)
