"""Smoke for BHPH date-of-birth normalization on the encrypted write path.

Intake takes the DOB as free text and most applicants fill it on a phone
keypad, which has no "/" — so the column accumulated bare-digit strings like
"08101995". Those are not parseable without guessing, and the CRM rendered
nonsense ages for them. `normalize_date_of_birth` canonicalizes to YYYY-MM-DD
at the single write path, so the storefront mask is a convenience and this is
the guarantee.

The smoke proves:

  1. Every shape prod has actually stored normalizes to YYYY-MM-DD.
  2. Impossible calendar dates (02/31), future dates, and absurd years are
     NOT coerced into a wrong-but-plausible date — they pass through verbatim
     for staff follow-up. A wrong DOB on a lender packet is worse than a
     visibly bad one.
  3. Ambiguous 6-digit entries ("071986": mmddyy or mm/yyyy?) are left alone
     rather than guessed.
  4. `_apply_to_row` — the only place a DOB is encrypted — routes through the
     normalizer, so no caller can bypass it.
  5. None / empty are preserved as-is (partial updates must stay partial).

Run with: .venv/bin/python tests/test_dob_normalization_smoke.py
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from modules.contacts.services import lead_application_service as svc  # noqa: E402


def _check_canonical() -> None:
    """Shapes seen in prod (and the obvious near-misses) → YYYY-MM-DD."""
    cases = {
        # bare digits — 26 of the 32 rows in prod on 2026-07-26
        "08101995": "1995-08-10",
        "03182004": "2004-03-18",
        "12051949": "1949-12-05",
        "01262009": "2009-01-26",
        # separated forms
        "06/22/1985": "1985-06-22",
        "4/5/1999": "1999-04-05",
        "10-17-1995": "1995-10-17",
        "05.30.1990": "1990-05-30",
        # already canonical (idempotence — re-saving a row must not drift)
        "1985-09-29": "1985-09-29",
        "1985-9-29": "1985-09-29",
        # yyyymmdd is unambiguous because a leading 4-digit month is impossible
        "19950810": "1995-08-10",
        # surrounding whitespace
        "  08101995  ": "1995-08-10",
    }
    for raw, expected in cases.items():
        got = svc.normalize_date_of_birth(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"
    print(f"  {len(cases)} canonical shapes ok")


def _check_preserved() -> None:
    """Unreadable or ambiguous input survives verbatim — never coerced."""
    next_year = date.today().year + 1
    future = date.today() + timedelta(days=30)
    cases = [
        "071986",  # 6 digits: mmddyy or mm/yyyy? unknowable
        "13451995",  # month 13, day 45
        "02/31/1990",  # impossible calendar date
        "08/08/95",  # 2-digit year: 1995 or 2095?
        f"01/01/{next_year}",  # future
        future.isoformat(),  # future, canonical shape
        "0810",  # incomplete
        "not a date",
        "1030-08-08",  # the year that produced "(age 995)" in the CRM
    ]
    for raw in cases:
        got = svc.normalize_date_of_birth(raw)
        assert got == raw.strip(), f"{raw!r} was rewritten to {got!r}"
    print(f"  {len(cases)} unparseable/ambiguous inputs preserved ok")


def _check_empty() -> None:
    assert svc.normalize_date_of_birth(None) is None
    assert svc.normalize_date_of_birth("") == ""
    assert svc.normalize_date_of_birth("   ") == ""
    print("  none/empty preserved ok")


def _check_write_path() -> None:
    """The encrypt call itself must normalize — a caller with a raw string
    (public intake, a staff edit, a future importer) cannot store a
    non-canonical DOB by going around the helper."""
    seen: list[str | None] = []

    class _FakeRow:
        """Records what the write path hands to the encryptor."""

        def __setattr__(self, name: str, value: object) -> None:
            object.__setattr__(self, name, value)

    real_encrypt = svc.lead_pii_crypto.encrypt_optional

    def _spy(value: str | None) -> bytes | None:
        seen.append(value)
        return real_encrypt(value)

    svc.lead_pii_crypto.encrypt_optional = _spy  # type: ignore[assignment]
    try:
        svc._apply_to_row(
            _FakeRow(),  # type: ignore[arg-type]
            svc.ApplicationInput(date_of_birth="08101995"),
        )
    finally:
        svc.lead_pii_crypto.encrypt_optional = real_encrypt  # type: ignore[assignment]

    assert seen == ["1995-08-10"], seen
    print("  _apply_to_row normalizes before encrypting ok")


def main() -> None:
    _check_canonical()
    _check_preserved()
    _check_empty()
    _check_write_path()
    print("dob_normalization smoke ok")


if __name__ == "__main__":
    main()
