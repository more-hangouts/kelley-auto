"""Confirmation-code canonicalization + display — core primitives.

Extracted from booking_service (Phase 3). The *display* side of appointment
confirmation codes is needed by core notification rendering
(notification_templates) and by the sales-appointments surface, so it must not
live in the booking domain or those callers would import booking. Code
*generation* (the random body, alphabet, length) stays in booking_service since
that is booking behavior. booking_service re-exports these two functions so
existing ``booking_service.format_confirmation_code`` /
``normalize_confirmation_code`` callers are unaffected.

Storage form is hyphen-free canonical (e.g. ``BXABCDEFGHJK...``); the display
layer inserts hyphens.
"""

from __future__ import annotations

import re

_CODE_PREFIX = "BX"  # No hyphen in stored canonical form; display layer adds it.
_DISPLAY_GROUP_SIZE = 5  # `BX-XXXXX-XXXXX-XXXXX-XXXXX` for human reading.
_NON_ALPHANUMERIC_RE = re.compile(r"[^A-Za-z0-9]+")


def normalize_confirmation_code(raw: str | None) -> str:
    """Canonicalize a confirmation code for storage and lookup.

    Strips every non-alphanumeric character and uppercases. This makes
    `BX-ABCDE-FGHJK-MNPQR-STUVW`, `bx abcde fghjk mnpqr stuvw`, and
    `bxabcdefghjkmnpqrstuvw` all match a single canonical stored form.
    Also handles legacy `BX-ABCDEF` codes from before D1 (their hyphen
    is stripped to `BXABCDEF`, matching the post-D1 canonical column).

    Returns an empty string for None or whitespace-only input — the
    caller should treat that the same as a not-found lookup.
    """
    if not raw:
        return ""
    return _NON_ALPHANUMERIC_RE.sub("", str(raw)).upper()


def format_confirmation_code(stored: str | None) -> str:
    """Render a stored canonical code for human display.

    For D1-era bodies (≥ 2 groups' worth of characters) inserts hyphens
    every `_DISPLAY_GROUP_SIZE` chars: `BX-ABCDE-FGHJK-MNPQR-STUVW`.
    For legacy short bodies (≤ one full group, e.g. backfilled
    pre-D1 codes) the body is rendered as a single segment so the
    display matches what the original customer email showed:
    `BXABCDEF` → `BX-ABCDEF`, not `BX-ABCDE-F`.
    Storage stays hyphen-free.
    """
    if not stored:
        return ""
    canon = normalize_confirmation_code(stored)
    if not canon.startswith(_CODE_PREFIX):
        return stored  # Unrecognized shape — return as-is rather than mangle.
    body = canon[len(_CODE_PREFIX):]
    if not body:
        return canon
    if len(body) <= _DISPLAY_GROUP_SIZE + 2:
        # Pre-D1 codes (6-7 char body). Single-group display is friendlier
        # than `BX-ABCDE-F` for the trailing-one-char case.
        return f"{_CODE_PREFIX}-{body}"
    groups = [
        body[i : i + _DISPLAY_GROUP_SIZE]
        for i in range(0, len(body), _DISPLAY_GROUP_SIZE)
    ]
    return f"{_CODE_PREFIX}-{'-'.join(groups)}"
