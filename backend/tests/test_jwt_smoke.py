"""Smoke for D5: python-jose → PyJWT swap.

This is a library-swap slice, not an auth redesign. The acceptance is
that every public failure mode emits the same 401/404 the router used
to emit under jose, and every happy path keeps working.

Covers the user-specified rejection cases against the live FastAPI
app via TestClient, plus a happy-path probe per scope:

  - happy path: admin token, sales token, booking signed token
  - expired token → 401
  - malformed token → 401
  - wrong signature (re-signed with a different secret) → 401
  - wrong algorithm at decode-time (e.g. HS512 token under our HS256
    decoder) → 401
  - alg=none token (no signature) → 401
  - token_version mismatch (bumped on the user row after minting) → 401
  - inactive user (is_active=False) → 401
  - booking signed token: expired → 404, wrong-purpose → 404, malformed
    → 404 (the public route collapses everything to a generic 404).

The smoke uses the real SECRET_KEY / RESCHEDULE_TOKEN_SECRET /
ENRICHMENT_TOKEN_SECRET from .env so a wire-compat regression would
surface here too.
"""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

import jwt as pyjwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import (  # noqa: E402
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ENRICHMENT_TOKEN_SECRET,
    RESCHEDULE_TOKEN_SECRET,
    SECRET_KEY,
)
from database.auth import (  # noqa: E402
    ADMIN_SCOPE,
    SALES_SCOPE,
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth as sales_auth_svc  # noqa: E402
from services.booking_tokens import (  # noqa: E402
    InvalidBookingToken,
    mint_token,
    verify_token,
)


client = TestClient(app)


_user_ids: list[int] = []


def _make_admin() -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d5-admin-{suffix}",
            email=f"d5-admin-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"D5 Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, u.email, "smoke-pass-12345"
    finally:
        db.close()


def _make_sales(pin: str = "424242") -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d5-sales-{suffix}",
            email=f"d5-sales-{suffix}@example.com",
            hashed_password=hash_password("unused"),
            full_name=f"D5 Sales {suffix}",
            is_active=True,
            role="sales",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.flush()
        sales_auth_svc.set_pin(db, u, pin, force_change=False)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, u.username
    finally:
        db.close()


def _bump_token_version(user_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE users SET token_version = token_version + 1 WHERE id = :i"),
            {"i": user_id},
        )
        db.commit()
    finally:
        db.close()


def _deactivate(user_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("UPDATE users SET is_active = FALSE WHERE id = :i"),
            {"i": user_id},
        )
        db.commit()
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.commit()
    finally:
        db.close()


try:
    admin_id, admin_email, admin_pw = _make_admin()
    sales_id, sales_username = _make_sales(pin="424242")
    admin_token = create_access_token(
        User(id=admin_id, role="admin", token_version=0)
    )
    sales_token = create_sales_token(
        User(id=sales_id, role="sales", token_version=0)
    )

    # ---------------------------------------------------------------------
    # 1. Happy paths under PyJWT-issued tokens
    # ---------------------------------------------------------------------
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == admin_email
    print("admin /api/auth/me happy path ok")

    resp = client.get("/api/sales/auth/me", headers={"Authorization": f"Bearer {sales_token}"})
    assert resp.status_code == 200, resp.text
    print("sales /api/sales/auth/me happy path ok")

    # ---------------------------------------------------------------------
    # 2. Expired token → 401
    # ---------------------------------------------------------------------
    now = datetime.now(timezone.utc)
    expired = pyjwt.encode(
        {
            "sub": str(admin_id),
            "tv": 0,
            "scope": ADMIN_SCOPE,
            "iat": now - timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES + 60),
            "exp": now - timedelta(minutes=1),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401, resp.text
    print("expired token → 401 ok")

    # ---------------------------------------------------------------------
    # 3. Malformed token → 401
    # ---------------------------------------------------------------------
    resp = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not.a.valid.token"}
    )
    assert resp.status_code == 401, resp.text
    print("malformed token → 401 ok")

    # ---------------------------------------------------------------------
    # 4. Wrong signature (token signed with a different key) → 401
    # ---------------------------------------------------------------------
    wrong_sig = pyjwt.encode(
        {
            "sub": str(admin_id),
            "tv": 0,
            "scope": ADMIN_SCOPE,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        "this-is-not-the-real-secret-and-should-be-long-enough-to-not-warn",
        algorithm="HS256",
    )
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {wrong_sig}"}
    )
    assert resp.status_code == 401, resp.text
    print("wrong-signature token → 401 ok")

    # ---------------------------------------------------------------------
    # 5. Wrong algorithm at decode-time → 401. The token is HS512-signed,
    # but our decoder pins algorithms=["HS256"]. PyJWT raises
    # InvalidAlgorithmError which is a subclass of InvalidTokenError, so
    # the handler 401s.
    # ---------------------------------------------------------------------
    hs512_tok = pyjwt.encode(
        {
            "sub": str(admin_id),
            "tv": 0,
            "scope": ADMIN_SCOPE,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        SECRET_KEY,
        algorithm="HS512",
    )
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {hs512_tok}"}
    )
    assert resp.status_code == 401, resp.text
    print("HS512 token under HS256 decoder → 401 ok")

    # ---------------------------------------------------------------------
    # 6. alg=none token → 401. PyJWT's algorithms=["HS256"] list
    # explicitly excludes `none`, so a none-signed token raises
    # InvalidAlgorithmError before the body is even read.
    # ---------------------------------------------------------------------
    none_tok = pyjwt.encode(
        {
            "sub": str(admin_id),
            "tv": 0,
            "scope": ADMIN_SCOPE,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        key="",
        algorithm="none",
    )
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {none_tok}"}
    )
    assert resp.status_code == 401, resp.text
    print("alg=none token → 401 ok")

    # ---------------------------------------------------------------------
    # 7. Token version mismatch → 401. Bump the user's token_version
    # AFTER minting the token, then re-use the now-stale token. The
    # decoder accepts the JWT (signature and exp are fine) but the
    # subsequent `user.token_version != tv` check fails.
    # ---------------------------------------------------------------------
    _bump_token_version(admin_id)
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 401, resp.text
    print("token_version mismatch → 401 ok")

    # Mint a fresh token under the new token_version so we can exercise
    # the inactive-user check on a token that would otherwise pass.
    fresh_admin = create_access_token(
        User(id=admin_id, role="admin", token_version=1)
    )
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {fresh_admin}"}
    )
    assert resp.status_code == 200, resp.text

    # ---------------------------------------------------------------------
    # 8. Inactive user → 401. Flip is_active to False and reuse the
    # known-good token.
    # ---------------------------------------------------------------------
    _deactivate(admin_id)
    resp = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {fresh_admin}"}
    )
    assert resp.status_code == 401, resp.text
    print("inactive user → 401 ok")

    # ---------------------------------------------------------------------
    # 9. Booking signed token: happy path, expired, wrong-purpose,
    # malformed. The public route surfaces every failure as a generic
    # 404 ("link is invalid or expired") — keep that contract intact.
    # ---------------------------------------------------------------------
    appt_id = 12345  # synthetic; verify_token is purely cryptographic, no DB hit

    # G1: mint_token takes an Appointment-shaped object (needs .id +
    # .slot_start_at for the slot-bound TTL cap). Build a synthetic stub
    # — this test exercises pure JWT crypto, not the live API path.
    class _StubAppt:
        def __init__(self, ident):
            self.id = ident
            self.slot_start_at = datetime.now(timezone.utc) + timedelta(days=30)

    stub_appt = _StubAppt(appt_id)
    reschedule_tok = mint_token(stub_appt, "reschedule")
    # verify_token now returns the full claims dict; the legacy int-return
    # contract was replaced so the verifier could surface `iat` for G1's
    # revocation comparison.
    claims = verify_token(reschedule_tok, "reschedule")
    assert claims["sub"] == str(appt_id)
    assert claims["purpose"] == "reschedule"
    print("booking reschedule token round-trip ok")

    # Expired reschedule token
    expired_resched = pyjwt.encode(
        {
            "sub": str(appt_id),
            "purpose": "reschedule",
            "iat": now - timedelta(days=120),
            "exp": now - timedelta(days=1),
        },
        RESCHEDULE_TOKEN_SECRET,
        algorithm="HS256",
    )
    try:
        verify_token(expired_resched, "reschedule")
    except InvalidBookingToken:
        pass
    else:
        raise AssertionError("expired reschedule token should raise InvalidBookingToken")
    print("booking expired token → InvalidBookingToken ok")

    # Wrong-purpose token: mint as 'cancel', verify as 'reschedule'.
    # `_secret_for("reschedule")` and `_secret_for("cancel")` are the SAME
    # secret today, so the signature actually verifies — the purpose-claim
    # mismatch is what catches it.
    cancel_tok = mint_token(stub_appt, "cancel")
    try:
        verify_token(cancel_tok, "reschedule")
    except InvalidBookingToken:
        pass
    else:
        raise AssertionError("wrong-purpose token should raise InvalidBookingToken")
    print("booking wrong-purpose token → InvalidBookingToken ok")

    # Enrichment token uses a DIFFERENT secret. Decoding it against the
    # reschedule secret should fail at the signature step.
    enrichment_tok = pyjwt.encode(
        {
            "sub": str(appt_id),
            "purpose": "enrichment",
            "iat": now,
            "exp": now + timedelta(days=7),
        },
        ENRICHMENT_TOKEN_SECRET,
        algorithm="HS256",
    )
    try:
        verify_token(enrichment_tok, "reschedule")
    except InvalidBookingToken:
        pass
    else:
        raise AssertionError("cross-secret token should raise InvalidBookingToken")
    print("booking cross-secret token → InvalidBookingToken ok")

    # Malformed booking token
    try:
        verify_token("garbage.not.a.token", "reschedule")
    except InvalidBookingToken:
        pass
    else:
        raise AssertionError("malformed booking token should raise InvalidBookingToken")
    print("booking malformed token → InvalidBookingToken ok")

    # End-to-end: hit the public booking reschedule GET route with each
    # failure mode and confirm it 404s ("link is invalid or expired").
    bad_token_responses = [
        ("garbage", "garbage.not.a.token"),
        ("expired", expired_resched),
        ("wrong-purpose", cancel_tok),
        ("wrong-secret", enrichment_tok),
    ]
    for label, tok in bad_token_responses:
        resp = client.get(f"/api/booking/reschedule/{tok}")
        assert resp.status_code == 404, (label, resp.status_code, resp.text)
    print("booking router 404s on every bad-token shape ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_jwt_smoke OK")
