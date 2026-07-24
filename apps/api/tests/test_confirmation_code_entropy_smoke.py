"""Smoke for D1: confirmation-code entropy boost + display/lookup separation.

Covers the user-specified acceptance plus a couple of nearby checks:

  1. Generator entropy/length: new codes are `BX` + 20 chars from the
     unambiguous 31-symbol alphabet (log2(31^20) ≈ 99 bits). 200 draws
     produce 200 distinct values, the alphabet is respected, and no
     hyphen appears in storage.

  2. format_confirmation_code inserts hyphens for display.
     `normalize_confirmation_code` strips every non-alphanumeric and
     uppercases, so messy customer input (spaces, mixed case, partial
     hyphens) collapses to the canonical stored form.

  3. Legacy short-code rows post-backfill: a row created with the
     pre-D1 generator (simulated by writing `BXOLDCDE` directly) is
     found via canonicalised lookup when the customer types
     `BX-OLDCDE` (with the legacy hyphen). Old emails in the wild
     still resolve.

  4. New-code lookup tolerates every reasonable input shape:
     canonical, hyphen-grouped, all-spaces, lowercase. A wrong code
     resolves to no row.

  (The Boutique Experience confirm endpoint that originally exercised
  cases 3-6 over HTTP was removed with the dress-shop enrichment flow;
  lookup canonicalisation is now pinned at the service layer, which is
  what any future confirmation-code entry point must call.)

Run with: venv/bin/python tests/test_confirmation_code_entropy_smoke.py
"""

import math
import os
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import Appointment  # noqa: E402
from modules.booking.services.booking_service import (  # noqa: E402
    _CODE_ALPHABET,
    _CODE_LENGTH,
    _CODE_PREFIX,
    _generate_code,
    format_confirmation_code,
    normalize_confirmation_code,
)


_appt_ids: list[int] = []


def _seed_appointment(*, code: str, email: str, status: str = "confirmed") -> int:
    """Insert an appointment with a pre-chosen confirmation_code so the
    smoke can simulate both legacy and new-format rows without going
    through the full booking widget flow."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        a = Appointment(
            confirmation_code=code,
            slot_start_at=now + timedelta(days=7),
            slot_end_at=now + timedelta(days=7, hours=1),
            slot_duration_minutes=60,
            timezone="America/Chicago",
            celebrant_first_name="D1 Celebrant",
            party_size_bucket="3_4",
            phone="(210) 555-0001",
            phone_e164="+12105550001",
            email=email,
            status=status,
            event_date=date.today() + timedelta(days=180),
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _appt_ids.append(a.id)
        return a.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointment_session_events WHERE event_id IN ("
                         "SELECT event_id FROM appointments WHERE id = ANY(:ids))"),
                {"ids": _appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        db.commit()
    finally:
        db.close()


def _lookup(raw_input: str) -> int | None:
    """Resolve customer-typed input to an appointment id the same way the
    (removed) confirm endpoint did: canonicalise, then exact-match the
    stored code."""
    canonical = normalize_confirmation_code(raw_input)
    db = SessionLocal()
    try:
        row = (
            db.query(Appointment.id)
            .filter(Appointment.confirmation_code == canonical)
            .first()
        )
        return row[0] if row else None
    finally:
        db.close()


try:
    # ---------------------------------------------------------------------
    # 1. Generator entropy + length + alphabet
    # ---------------------------------------------------------------------
    assert _CODE_LENGTH == 20, f"expected 20-char body, got {_CODE_LENGTH}"
    assert _CODE_PREFIX == "BX"
    assert len(_CODE_ALPHABET) == 31, len(_CODE_ALPHABET)

    codes = {_generate_code() for _ in range(200)}
    assert len(codes) == 200, "draws collided in 200 samples — impossible at 99 bits"
    for c in codes:
        assert len(c) == 22, (c, len(c))  # BX + 20 body
        assert c.startswith("BX"), c
        assert all(ch in _CODE_ALPHABET for ch in c[2:]), c
        assert "-" not in c, "stored canonical form must not contain hyphens"

    # Stat sanity: each body char position should see most of the alphabet
    # across 200 draws. Strict count varies, but variance should be wide
    # enough that >= 20 distinct chars per position is comfortable.
    for position in range(2, 22):
        chars_at_pos = Counter(c[position] for c in codes)
        assert len(chars_at_pos) >= 20, (
            f"position {position} only saw {len(chars_at_pos)} distinct chars"
        )

    entropy_bits = _CODE_LENGTH * math.log2(len(_CODE_ALPHABET))
    assert entropy_bits >= 96, f"entropy {entropy_bits:.1f} bits below 96-bit target"
    print(f"generator: 200 unique codes, ≈{entropy_bits:.1f} bits ok")

    # ---------------------------------------------------------------------
    # 2. normalize_confirmation_code + format_confirmation_code round-trip
    # ---------------------------------------------------------------------
    sample = "BX" + "ABCDE" * 4  # canonical 22-char
    rendered = format_confirmation_code(sample)
    assert rendered == "BX-ABCDE-ABCDE-ABCDE-ABCDE", rendered
    assert normalize_confirmation_code(rendered) == sample
    assert normalize_confirmation_code("  bx abcde abcde abcde abcde  ") == sample
    assert normalize_confirmation_code("BX-AB CD-E.AB---CDE_ABCDE/ABCDE") == sample
    assert normalize_confirmation_code(None) == ""
    assert normalize_confirmation_code("") == ""
    print("normalize/format round-trip ok")

    # ---------------------------------------------------------------------
    # 3. Legacy short code still works post-backfill.
    # ---------------------------------------------------------------------
    legacy_email = f"d1-legacy-{uuid.uuid4().hex[:6]}@example.com"
    legacy_code_canon = "BXOLDCDE"  # 8 chars, post-backfill canonical
    legacy_appt_id = _seed_appointment(code=legacy_code_canon, email=legacy_email)

    # Customer types the pre-D1 display form (`BX-OLDCDE`); canonicalised
    # lookup must resolve it to the legacy_code_canon stored row.
    assert _lookup("BX-OLDCDE") == legacy_appt_id
    print("legacy BX-XXXXXX input still resolves post-backfill ok")

    # ---------------------------------------------------------------------
    # 4. New-code lookup tolerates every reasonable input shape.
    # ---------------------------------------------------------------------
    new_code = _generate_code()
    new_email = f"d1-new-{uuid.uuid4().hex[:6]}@example.com"
    new_appt_id = _seed_appointment(code=new_code, email=new_email)

    display = format_confirmation_code(new_code)
    spaced = display.replace("-", " ")
    lower = display.lower()

    for variant in (new_code, display, spaced, lower):
        assert _lookup(variant) == new_appt_id, variant
    print("new code resolves via canonical / hyphenated / spaced / lower ok")

    # Wrong code resolves to nothing.
    assert _lookup("BX-WRONGWRONGWRONGWRONGWRONG") is None
    print("wrong-code resolves to no row ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_confirmation_code_entropy_smoke OK")
