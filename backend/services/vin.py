"""VIN normalization, structural validation, and check-digit.

Two distinct gates, on purpose:

* **Structural (hard gate)** — a VIN is exactly 17 characters drawn from
  the VIN alphabet: digits 0-9 and letters A-Z *excluding* I, O and Q
  (barred by the standard to avoid confusion with 1/0). We uppercase and
  strip surrounding whitespace plus interior spaces/dashes that staff
  paste off a sticker. A value that fails this is rejected on save.

* **Check digit (soft signal)** — position 9 is a checksum computed per
  the North American FMVSS algorithm. It is a typo tripwire, not a hard
  rule: not every VIN in the wild (pre-1981 cars, some imports) carries a
  conforming check digit, so a mismatch is surfaced as a *warning* rather
  than blocking the save. This is exactly what makes OCR-scanned VINs
  safe — a misread ``8``→``B`` usually breaks the checksum.
"""

from __future__ import annotations

import re

VIN_LENGTH = 17

# The 33-symbol VIN alphabet: 0-9 and A-Z minus I, O, Q.
_VIN_ALPHABET = frozenset("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")

# FMVSS transliteration: each letter maps to a digit; digits map to
# themselves. (I, O, Q are absent — they can never appear in a valid VIN.)
_TRANSLITERATION: dict[str, int] = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

# Positional weights; index 8 (the check digit itself) is weighted 0.
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

_SEPARATORS = re.compile(r"[\s\-]")


class VinError(ValueError):
    """Structural VIN rejection. Carries a `code` the API maps to a
    friendly message, matching the CatalogServiceError contract."""

    def __init__(self, message: str, *, code: str = "vehicle_vin_invalid") -> None:
        super().__init__(message)
        self.code = code


def clean_vin(raw: str | None) -> str:
    """Uppercase and strip whitespace + interior spaces/dashes. Does NOT
    validate — returns whatever is left (possibly the wrong length)."""
    if not raw:
        return ""
    return _SEPARATORS.sub("", str(raw)).strip().upper()


def normalize_vin(raw: str | None) -> str | None:
    """Return the cleaned, structurally-valid uppercase VIN.

    Empty / None input returns ``None`` (VIN is optional on a vehicle).
    A non-empty value that isn't a valid 17-char VIN raises ``VinError``.
    """
    cleaned = clean_vin(raw)
    if cleaned == "":
        return None
    if len(cleaned) != VIN_LENGTH:
        raise VinError(
            f"VIN must be {VIN_LENGTH} characters (got {len(cleaned)})."
        )
    bad = sorted(set(cleaned) - _VIN_ALPHABET)
    if bad:
        raise VinError(
            "VIN contains invalid characters: "
            f"{', '.join(bad)}. The letters I, O and Q are never used."
        )
    return cleaned


def vin_check_digit_ok(vin: str | None) -> bool:
    """True when the position-9 check digit matches the checksum.

    Cleans defensively and returns ``False`` (never raises) for anything
    not structurally valid, so callers can treat this purely as a soft
    warning signal.
    """
    v = clean_vin(vin)
    if len(v) != VIN_LENGTH or set(v) - _VIN_ALPHABET:
        return False
    total = sum(_TRANSLITERATION[ch] * w for ch, w in zip(v, _WEIGHTS))
    remainder = total % 11
    expected = "X" if remainder == 10 else str(remainder)
    return v[8] == expected
