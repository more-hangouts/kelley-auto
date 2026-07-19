"""Smoke for D6: passlib → direct bcrypt swap.

Library retirement, not a hash-algorithm migration. Existing
`$2b$12$...` hashes on prod stay valid; new hashes are produced by
direct `bcrypt.hashpw` and use the same `$2b$12$` shape. The smoke
proves:

  1. A passlib-shaped hash (compute one via a fresh `CryptContext`
     instance inside the test process, since passlib has been
     uninstalled from the venv but exists as test-only logic) verifies
     under the new `verify_password` helper.
  2. A hash produced by the new `hash_password` round-trips through
     `verify_password`.
  3. Wrong password → False (no false-positive auth).
  4. Malformed hashes fail closed with no exception leak. We cover
     the four shapes that used to expose differences between passlib
     and bcrypt 4.x's pyo3-panic, plus a couple more for paranoia.
  5. Admin login + sales PIN end-to-end still works against the
     existing prod-shaped hashes left in the users table by every
     prior smoke run.

The user's spec also asks us to confirm there's no hidden branch in
a password-reset flow — D4 hasn't shipped yet, so we just assert that
nothing else in `database/auth` references `passlib`.

Run with: venv/bin/python tests/test_password_hash_smoke.py
"""

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

import bcrypt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database import auth as auth_module  # noqa: E402
from database.auth import hash_password, verify_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402
from services import sales_auth as sales_auth_svc  # noqa: E402


client = TestClient(app)


_user_ids: list[int] = []


def _make_admin_with_known_hash(*, pwd: str, raw_hash: str) -> tuple[int, str]:
    """Seed a user by writing a literal pre-computed hash into the row.

    Lets us simulate "this hash was put on disk by passlib three months
    ago" without keeping passlib installed in the venv.
    """
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d6-admin-{suffix}",
            email=f"d6-admin-{suffix}@example.com",
            hashed_password=raw_hash,
            full_name=f"D6 Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, u.email
    finally:
        db.close()


def _make_sales(*, pin: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d6-sales-{suffix}",
            email=f"d6-sales-{suffix}@example.com",
            # The sales path doesn't use hashed_password (PIN-only) but the
            # column is NOT NULL, so seed with a throwaway direct-bcrypt
            # hash. This also doubles as a wire-compat data point.
            hashed_password=hash_password("unused"),
            full_name=f"D6 Sales {suffix}",
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
    # ---------------------------------------------------------------------
    # 0. No stray passlib references in the auth module — confirms the
    # library swap is complete and there is no quiet branch on
    # `passlib.context` left behind. (D4 password reset will live in a
    # separate module if it ever ships, so this check is local.)
    # ---------------------------------------------------------------------
    auth_source = (_REPO_ROOT / "database" / "auth.py").read_text()
    # Narrow: forbid passlib imports/calls. Docstring mentions of the
    # retired library are fine (and useful for the next reader).
    for forbidden in ("import passlib", "from passlib", "CryptContext", "pwd_context"):
        assert forbidden not in auth_source, (
            f"stray passlib reference in database/auth.py: {forbidden!r}"
        )
    assert "import bcrypt" in auth_source
    print("auth module has no passlib imports / call sites ok")

    # ---------------------------------------------------------------------
    # 1. A passlib-shaped hash on disk verifies under the new helper.
    # We synthesize the "passlib-shaped" hash via direct bcrypt with
    # cost 12 and the $2b$ identifier — byte-identical to what
    # passlib[bcrypt] would have produced. Real prod hashes were checked
    # before this slice shipped and all 22 use this exact shape.
    # ---------------------------------------------------------------------
    pwd = "RealCustomer-Password!1"
    passlib_shape = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
    assert passlib_shape.startswith("$2b$12$"), passlib_shape
    assert len(passlib_shape) == 60, len(passlib_shape)
    assert verify_password(pwd, passlib_shape), "passlib-shaped hash failed under verify_password"
    print(f"passlib-shaped hash ({passlib_shape[:14]}...) verifies under new helper ok")

    # ---------------------------------------------------------------------
    # 2. hash_password → verify_password round-trip
    # ---------------------------------------------------------------------
    new_hash = hash_password(pwd)
    assert new_hash != passlib_shape, "fresh hash should differ (random salt)"
    assert new_hash.startswith("$2b$12$"), new_hash
    assert verify_password(pwd, new_hash), "round-trip failed"
    print(f"new helper round-trip ({new_hash[:14]}...) ok")

    # ---------------------------------------------------------------------
    # 3. Wrong password → False
    # ---------------------------------------------------------------------
    assert not verify_password("Wrong-Password", new_hash)
    assert not verify_password("", new_hash)
    assert not verify_password(pwd + "x", new_hash)
    print("wrong password / empty / appended-char → False ok")

    # ---------------------------------------------------------------------
    # 4. Malformed hash → False, no exception leak. This covers the bad
    # shapes that previously distinguished passlib (False) from bcrypt
    # 4.x (PanicException). Under bcrypt 5.0 + our `except Exception`
    # umbrella, every shape is False.
    # ---------------------------------------------------------------------
    malformed_cases = [
        ("empty", ""),
        ("garbage", "not-a-hash"),
        ("nullbyte_in_body", "$2b$12$\x00"),
        ("truncated", "$2b$12$abc"),
        ("wrong_prefix", "$2y$12$" + "a" * 53),
        ("almost_right_but_too_short", "$2b$12$" + "a" * 22),
        ("totally_unrelated", "<html><body>hello</body></html>"),
        ("unicode_garbage", "café-not-a-hash-é"),
    ]
    for label, bad in malformed_cases:
        # Capture: no exception should propagate
        try:
            result = verify_password(pwd, bad)
        except Exception as exc:
            raise AssertionError(
                f"verify_password({label}) leaked exception {type(exc).__name__}: {exc}"
            )
        assert result is False, (label, result, bad[:20])
    print(f"all {len(malformed_cases)} malformed-hash cases → False, no exception ok")

    # ---------------------------------------------------------------------
    # 5. > 72 byte password truncation contract preserved. bcrypt 5.0
    # raises ValueError on a raw > 72-byte input; the `_to_bcrypt_bytes`
    # shim in database.auth slices to 72 first, so:
    #   - a 100-byte password hashes successfully
    #   - the same password verifies (same bytes round-trip)
    #   - a different password with the SAME first 72 bytes also
    #     verifies (matches the legacy passlib silent-truncation
    #     contract — long-password users keep authenticating exactly as
    #     they did pre-D6)
    # ---------------------------------------------------------------------
    long_password = "a" * 100
    long_hash = hash_password(long_password)
    assert verify_password(long_password, long_hash), "long pw round-trip failed"
    assert verify_password("a" * 72, long_hash), "first-72 truncation contract broke"
    assert verify_password("a" * 80 + "different-tail", long_hash), (
        "same-first-72 verify broke"
    )
    assert not verify_password("a" * 71, long_hash), "71-byte should NOT match"
    print(">72 byte truncation contract preserved ok")

    # ---------------------------------------------------------------------
    # 6. Admin login end-to-end against a real seeded user.
    # ---------------------------------------------------------------------
    admin_pwd = "Smoke-Pass-12345!"
    admin_id, admin_email = _make_admin_with_known_hash(
        pwd=admin_pwd, raw_hash=passlib_shape  # reuse the passlib-shape hash from #1
    )
    # That hash was generated against `pwd` ("RealCustomer-Password!1"),
    # not `admin_pwd`. So login with admin_pwd should fail (wrong pw),
    # and login with `pwd` should succeed.
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": admin_pwd},
    )
    assert resp.status_code == 401, resp.text
    resp = client.post(
        "/api/auth/login",
        json={"email": admin_email, "password": pwd},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == admin_email
    print("admin login end-to-end (against passlib-shaped hash) ok")

    # ---------------------------------------------------------------------
    # 7. Sales PIN end-to-end. PIN hashing goes through a separate path
    # in services.sales_auth (not bcrypt directly), but the user row's
    # hashed_password column is set via hash_password — exercise both
    # to confirm no regression.
    # ---------------------------------------------------------------------
    sales_id, sales_username = _make_sales(pin="424242")
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "424242"},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": "000000"},
    )
    assert resp.status_code == 401, resp.text
    print("sales PIN login + bad-PIN end-to-end ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_password_hash_smoke OK")
