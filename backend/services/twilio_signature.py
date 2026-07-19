"""Twilio webhook signature verification (stdlib only — no SDK dependency).

Twilio signs each webhook with HMAC-SHA1 over the full request URL plus the
POST parameters, keyed by the account Auth Token, base64-encoded, in the
``X-Twilio-Signature`` header. We recompute and compare in constant time.

    signature = base64(hmac_sha1(auth_token, url + "".join(k + v for k,v in
                sorted(post_params))))

The load-bearing subtlety in production: Twilio signs against the **public**
URL it posted to (``https://api.kelleyautoplex.com/...``). Behind Caddy the
app sees an internal ``http://127.0.0.1:8000`` request, so verifying against
``request.url`` fails every time. The router must build the URL from
``PUBLIC_API_BASE_URL`` + the path. This module just takes the already-correct
URL string.

Docs: https://www.twilio.com/docs/usage/security#validating-requests
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Return the expected base64 X-Twilio-Signature for a POST request."""
    data = url
    for key in sorted(params):
        data += key + (params[key] if params[key] is not None else "")
    digest = hmac.new(
        auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str | None
) -> bool:
    """Constant-time check of the provided X-Twilio-Signature."""
    if not signature or not auth_token:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature)
