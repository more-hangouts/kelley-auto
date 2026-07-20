"""Phone-number normalization — a core primitive.

Extracted from booking_service (Phase 3) so it can be shared without creating a
cross-domain import cycle: contacts (contact_service), messaging (inbox_service),
and booking all normalize phone numbers, and none of them should have to import
the booking domain to do it. booking_service re-exports ``normalize_phone_e164``
for backwards compatibility, so existing ``booking_service.normalize_phone_e164``
callers are unaffected.
"""

from __future__ import annotations

import re


def normalize_phone_e164(raw: str) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.lstrip().startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    return None
