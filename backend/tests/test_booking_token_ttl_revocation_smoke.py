"""G1 smoke: booking token TTL reduction + explicit revocation.

User-spec acceptance cases:

  1. Valid freshly-minted token works (round-trips through `verify_token`
     and the live API route).
  2. Expired token → 404 generic (forge an `exp` in the past).
  3. Wrong-purpose token → 404 (reschedule token presented to cancel route).
  4. Token after cancellation → 404 (`tokens_invalidated_at` bumped, the
     pre-cancel token's `iat` is older).
  5. Token after reschedule → 404 (original appointment's tokens are
     revoked when the original is marked rescheduled).
  6. Newly-issued token after re-issue works (the new appointment's
     reschedule/cancel/enrichment links are fully valid).

Plus a couple of nearby invariants worth pinning:

  - TTL `exp` for far-future appointments is capped by the 30/14-day
    default (the slot-bound is even further out).
  - TTL `exp` for near-term appointments is capped by `slot_start_at +
    bound_days` (slot bound is tighter than the default).
  - Generic 404 detail string is uniform across every failure mode so
    we don't leak which check tripped.

Run with: venv/bin/python tests/test_booking_token_ttl_revocation_smoke.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import RESCHEDULE_TOKEN_SECRET  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Appointment  # noqa: E402
from services.booking_tokens import (  # noqa: E402
    ALGORITHM,
    InvalidBookingToken,
    _DEFAULT_TTL_DAYS,
    _SLOT_BOUND_DAYS,
    _exp_for,
    cancel_url,
    enrichment_url,
    ensure_not_revoked,
    mint_token,
    reschedule_url,
    revoke_appointment_tokens,
    verify_token,
)

client = TestClient(app)


_appt_ids: list[int] = []


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _appt_ids:
            # The rescheduled-from FK is SET NULL on delete; explicit
            # ordering here keeps test DBs tidy.
            db.execute(
                sql_text(
                    "DELETE FROM appointment_session_events "
                    "WHERE event_id IN (SELECT event_id FROM appointments WHERE id = ANY(:ids))"
                ),
                {"ids": _appt_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM appointments WHERE rescheduled_from_id = ANY(:ids)"
                ),
                {"ids": _appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
            db.commit()
    finally:
        db.close()


def _make_appointment(*, slot_offset_days: int = 14) -> Appointment:
    """Insert a minimal Appointment row that satisfies the NOT NULLs.

    The smoke doesn't care about most of the booking ceremony — it just
    needs a real row to mint tokens for. `slot_offset_days` controls
    how far the slot is from now; tests use this to exercise the
    near-term-vs-far-future TTL bound.
    """
    db = SessionLocal()
    try:
        slot_start = datetime.now(timezone.utc) + timedelta(days=slot_offset_days)
        slot_end = slot_start + timedelta(hours=1)
        # Unique-ish confirmation code for this row. D1's canonical shape
        # is BX + 20 chars; we don't need to mimic the generator, just
        # have something the unique index won't reject.
        import uuid

        code = f"G1{uuid.uuid4().hex[:18].upper()}"
        appt = Appointment(
            confirmation_code=code,
            slot_start_at=slot_start,
            slot_end_at=slot_end,
            slot_duration_minutes=60,
            timezone="America/Chicago",
            celebrant_first_name="G1Test",
            party_size_bucket="2_3",
            phone="2105551212",
            email=f"g1-{code.lower()}@example.com",
            status="confirmed",
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _appt_ids.append(appt.id)
        return appt
    finally:
        db.close()


def _refresh(appt_id: int) -> Appointment:
    db = SessionLocal()
    try:
        return db.query(Appointment).filter(Appointment.id == appt_id).first()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Valid token works — round-trip and live API.
# ---------------------------------------------------------------------------
appt = _make_appointment(slot_offset_days=14)
token = mint_token(appt, "reschedule")
claims = verify_token(token, "reschedule")
assert claims["sub"] == str(appt.id), claims
assert claims["purpose"] == "reschedule"
# Live API: GET /reschedule/<token> returns 200 with the appointment summary.
resp = client.get(f"/api/booking/reschedule/{token}")
assert resp.status_code == 200, resp.text
print("valid reschedule token round-trips and live API returns 200 ok")


# ---------------------------------------------------------------------------
# 2. Expired token → 404.
# Forge a token with `exp` in the past, signed with the real secret +
# a real appointment id. The server should treat it as invalid_or_expired.
# ---------------------------------------------------------------------------
now = datetime.now(timezone.utc)
expired_claims = {
    "sub": str(appt.id),
    "purpose": "reschedule",
    "iat": now - timedelta(days=10),
    "exp": now - timedelta(days=1),  # expired 1 day ago
}
expired_token = jwt.encode(expired_claims, RESCHEDULE_TOKEN_SECRET, algorithm=ALGORITHM)
resp = client.get(f"/api/booking/reschedule/{expired_token}")
assert resp.status_code == 404, resp.text
assert resp.json()["detail"] == "link is invalid or expired", resp.json()
# Direct verify_token also rejects.
try:
    verify_token(expired_token, "reschedule")
    raise AssertionError("expired token should have raised InvalidBookingToken")
except InvalidBookingToken:
    pass
print("expired token → 404 with generic detail ok")


# ---------------------------------------------------------------------------
# 3. Wrong-purpose token → 404.
# A `cancel` token presented to the `reschedule` route fails decode
# (different secret usage check or purpose claim mismatch).
# ---------------------------------------------------------------------------
cancel_token = mint_token(appt, "cancel")
resp = client.get(f"/api/booking/reschedule/{cancel_token}")
assert resp.status_code == 404, resp.text
assert resp.json()["detail"] == "link is invalid or expired", resp.json()
# Direct verify_token also rejects (purpose mismatch).
try:
    verify_token(cancel_token, "reschedule")
    raise AssertionError("cross-purpose token should have raised")
except InvalidBookingToken:
    pass
print("wrong-purpose token → 404 with generic detail ok")


# ---------------------------------------------------------------------------
# 4. Token after cancellation → 404.
# Issue token, cancel the appointment, the pre-cancel token must fail.
# ---------------------------------------------------------------------------
appt2 = _make_appointment(slot_offset_days=14)
pre_cancel_resched_token = mint_token(appt2, "reschedule")
pre_cancel_enrich_token = mint_token(appt2, "enrichment")
# Sanity: token works before cancel.
assert client.get(f"/api/booking/reschedule/{pre_cancel_resched_token}").status_code == 200

# Cancel via the API (real flow — bumps `tokens_invalidated_at`).
cancel_tkn = mint_token(appt2, "cancel")
resp = client.post(
    f"/api/booking/cancel/{cancel_tkn}",
    json={"reason": "smoke test"},
)
assert resp.status_code == 200, resp.text

# Verify the column was bumped.
appt2_refreshed = _refresh(appt2.id)
assert appt2_refreshed.status == "cancelled"
assert appt2_refreshed.tokens_invalidated_at is not None
assert appt2_refreshed.cancelled_at is not None

# Pre-cancel reschedule token must now 404 (revoked).
resp = client.get(f"/api/booking/reschedule/{pre_cancel_resched_token}")
assert resp.status_code == 404, resp.text
assert resp.json()["detail"] == "link is invalid or expired", resp.json()

# Pre-cancel enrichment token must also 404.
resp = client.post(
    f"/api/booking/boutique-experience/{pre_cancel_enrich_token}",
    json={"summary": "minimum-meaningful payload to satisfy the validator"},
)
assert resp.status_code == 404, resp.text
print("token after cancellation → 404 (revoked via tokens_invalidated_at) ok")


# ---------------------------------------------------------------------------
# 5. Token after reschedule → 404.
# The reschedule API enforces availability rules on the NEW slot,
# which would force this smoke to pre-seed scheduling fixtures (out
# of scope for a revocation test). Instead, simulate the side effect
# at the DB layer — exactly what the reschedule handler does after
# `revoke_appointment_tokens(original)` — and assert the pre-reschedule
# token now 404s against the bumped `tokens_invalidated_at`.
# ---------------------------------------------------------------------------
appt3 = _make_appointment(slot_offset_days=14)
pre_resched_resched_token = mint_token(appt3, "reschedule")
pre_resched_enrich_token = mint_token(appt3, "enrichment")
# Sanity: token works before "reschedule".
assert client.get(f"/api/booking/reschedule/{pre_resched_resched_token}").status_code == 200

# Simulate what api/routers/booking.py does at the end of reschedule:
# mark status, bump tokens_invalidated_at, commit. Sleep 1s first so
# the bumped timestamp is strictly later than the token's iat (iat is
# 1-second resolution in JWT).
import time as _time
_time.sleep(1.2)
db = SessionLocal()
try:
    row = db.query(Appointment).filter(Appointment.id == appt3.id).one()
    row.status = "rescheduled"
    revoke_appointment_tokens(row)
    db.commit()
finally:
    db.close()

# Pre-reschedule reschedule token must now 404.
resp = client.get(f"/api/booking/reschedule/{pre_resched_resched_token}")
assert resp.status_code == 404, resp.text
assert resp.json()["detail"] == "link is invalid or expired", resp.json()

# Pre-reschedule enrichment token must also 404.
resp = client.post(
    f"/api/booking/boutique-experience/{pre_resched_enrich_token}",
    json={"summary": "minimum-meaningful payload to satisfy the validator"},
)
assert resp.status_code == 404, resp.text
print("token after reschedule → 404 (original's tokens revoked) ok")


# ---------------------------------------------------------------------------
# 6. Newly-issued token after re-issue works.
# Create a fresh appointment row (mimicking the NEW appointment that
# the reschedule handler would have inserted), confirm minting + verify
# rounds-trip cleanly even after the previous appt's tokens are revoked.
# ---------------------------------------------------------------------------
new_appt = _make_appointment(slot_offset_days=21)
fresh_resched_token = mint_token(new_appt, "reschedule")
resp = client.get(f"/api/booking/reschedule/{fresh_resched_token}")
assert resp.status_code == 200, resp.text
print("newly-issued token after re-issue works ok")


# ---------------------------------------------------------------------------
# 7. TTL bound: far-future appointment hits the default ceiling.
# ---------------------------------------------------------------------------
far_future = datetime.now(timezone.utc) + timedelta(days=365)
near_term = datetime.now(timezone.utc) + timedelta(days=5)
now = datetime.now(timezone.utc)

far_exp = _exp_for(far_future, "reschedule", now)
near_exp = _exp_for(near_term, "reschedule", now)

# Far-future: default 30d kicks in (slot bound is 365+1 days out).
expected_default = now + timedelta(days=_DEFAULT_TTL_DAYS["reschedule"])
assert abs((far_exp - expected_default).total_seconds()) < 1, (far_exp, expected_default)

# Near-term: slot bound (slot + 1d = ~6d) kicks in (tighter than 30d).
expected_slot = near_term + timedelta(days=_SLOT_BOUND_DAYS["reschedule"])
assert abs((near_exp - expected_slot).total_seconds()) < 1, (near_exp, expected_slot)
print("TTL bounds: far-future default ceiling + near-term slot ceiling ok")


# ---------------------------------------------------------------------------
# 8. Enrichment bound = slot_start exactly (no grace past slot).
# ---------------------------------------------------------------------------
enrich_exp = _exp_for(near_term, "enrichment", now)
# enrichment slot bound is 0, so exp = slot_start exactly.
assert abs((enrich_exp - near_term).total_seconds()) < 1, (enrich_exp, near_term)
print("enrichment exp = slot_start (no grace past slot) ok")


# ---------------------------------------------------------------------------
# 9. ensure_not_revoked() unit-tests against a synthetic appointment-shape.
# ---------------------------------------------------------------------------
class _FakeAppt:
    def __init__(self, ts):
        self.tokens_invalidated_at = ts


# No revocation set → passes.
ensure_not_revoked({"iat": int(now.timestamp())}, _FakeAppt(None))

# iat >= invalidated → passes.
ensure_not_revoked(
    {"iat": int(now.timestamp())},
    _FakeAppt(now - timedelta(seconds=1)),
)

# iat < invalidated → raises.
try:
    ensure_not_revoked(
        {"iat": int(now.timestamp())},
        _FakeAppt(now + timedelta(seconds=1)),
    )
    raise AssertionError("revoked check should have raised")
except InvalidBookingToken:
    pass
print("ensure_not_revoked unit cases ok")


# ---------------------------------------------------------------------------
# 10. revoke_appointment_tokens() bumps the column to roughly now.
# ---------------------------------------------------------------------------
class _MutableAppt:
    def __init__(self):
        self.tokens_invalidated_at = None


m = _MutableAppt()
before = datetime.now(timezone.utc)
revoke_appointment_tokens(m)
after = datetime.now(timezone.utc)
assert m.tokens_invalidated_at is not None
assert before <= m.tokens_invalidated_at <= after, m.tokens_invalidated_at
print("revoke_appointment_tokens bumps timestamp to now ok")


_cleanup()
print("\ntest_booking_token_ttl_revocation_smoke OK")
