"""Smoke for C1: at-rest encryption of integration tokens.

Covers:
  - set_token / get_token round-trip with realistic OAuth-shaped values.
  - At-rest verification: the bytes in the ciphertext column do NOT
    contain the plaintext, and ARE the value Fernet decrypts back to.
  - Dual-read fallback: a row with only the legacy plaintext column
    populated decrypts back to that plaintext (so seeded-from-old-state
    rows still work) and emits the expected fallback warning.
  - Key rotation: ciphertext encrypted under an old key still decrypts
    after the operator prepends a new key and reloads the cipher.
    Then a re-write moves the row onto the new key.
  - Cleanup: the test row is deleted so re-runs are clean.

Run with: venv/bin/python tests/test_integration_tokens_smoke.py
"""

import logging
import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

# Ensure at least one Fernet key is configured for the smoke. The real
# .env already has one; if a tester runs this in a stripped env, mint
# one in-memory so the smoke is self-contained.
if not os.environ.get("INTEGRATION_TOKEN_KEYS"):
    from cryptography.fernet import Fernet as _F  # noqa: E402
    os.environ["INTEGRATION_TOKEN_KEYS"] = _F.generate_key().decode()

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import IntegrationToken  # noqa: E402
from services import integration_tokens as itok  # noqa: E402


_TEST_PROVIDER = f"c1-smoke-{uuid.uuid4().hex[:8]}"


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM integration_tokens WHERE provider LIKE 'c1-smoke-%'")
        )
        db.commit()
    finally:
        db.close()


_cleanup()

try:
    # ---------------------------------------------------------------------
    # 1. Round-trip via the service layer.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        row = itok.set_token(
            db,
            _TEST_PROVIDER,
            access_token="oauth-access-secret-AAA111",
            refresh_token="oauth-refresh-secret-RRR222",
            token_type="Bearer",
            owner_uri="https://example.com/owner/42",
        )
        db.commit()
        row_id = row.id

        got = itok.get_token(db, _TEST_PROVIDER)
        assert got is not None
        assert got["access_token"] == "oauth-access-secret-AAA111", got
        assert got["refresh_token"] == "oauth-refresh-secret-RRR222", got
        assert got["token_type"] == "Bearer"
        assert got["owner_uri"] == "https://example.com/owner/42"
    finally:
        db.close()
    print("round-trip via service layer ok")

    # ---------------------------------------------------------------------
    # 2. At-rest verification: the ciphertext column is bytes that do NOT
    # contain the plaintext, and the plaintext column was nulled on write
    # so we cannot accidentally fall back to a stale value.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        raw = db.execute(
            sql_text(
                "SELECT access_token, access_token_ciphertext, "
                "refresh_token, refresh_token_ciphertext "
                "FROM integration_tokens WHERE id = :i"
            ),
            {"i": row_id},
        ).one()
        assert raw.access_token is None, (
            f"set_token must NULL the plaintext column, got {raw.access_token!r}"
        )
        assert raw.refresh_token is None, (
            f"set_token must NULL the plaintext column, got {raw.refresh_token!r}"
        )
        assert isinstance(raw.access_token_ciphertext, (bytes, memoryview)), (
            type(raw.access_token_ciphertext)
        )
        assert isinstance(raw.refresh_token_ciphertext, (bytes, memoryview)), (
            type(raw.refresh_token_ciphertext)
        )
        access_bytes = bytes(raw.access_token_ciphertext)
        refresh_bytes = bytes(raw.refresh_token_ciphertext)
        assert b"oauth-access-secret-AAA111" not in access_bytes, (
            "plaintext leaked through ciphertext column"
        )
        assert b"oauth-refresh-secret-RRR222" not in refresh_bytes, (
            "plaintext leaked through ciphertext column"
        )
        # And the ciphertext decrypts back to the plaintext via the helper.
        assert itok.decrypt(access_bytes) == "oauth-access-secret-AAA111"
        assert itok.decrypt(refresh_bytes) == "oauth-refresh-secret-RRR222"
    finally:
        db.close()
    print("at-rest bytes opaque ok")

    # ---------------------------------------------------------------------
    # 3. Dual-read fallback: write a separate row with ONLY the legacy
    # plaintext column populated (simulating either an ad-hoc INSERT or
    # data seeded before C1 shipped). get_token must return the plaintext
    # and log a fallback warning so the row can be found and migrated.
    # ---------------------------------------------------------------------
    legacy_provider = f"c1-smoke-legacy-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO integration_tokens (provider, access_token, refresh_token) "
                "VALUES (:p, :a, :r)"
            ),
            {
                "p": legacy_provider,
                "a": "legacy-access-LLL333",
                "r": "legacy-refresh-LLL444",
            },
        )
        db.commit()

        logger = logging.getLogger("services.integration_tokens")
        captured: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        handler = _Capture(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            got_legacy = itok.get_token(db, legacy_provider)
        finally:
            logger.removeHandler(handler)

        assert got_legacy is not None
        assert got_legacy["access_token"] == "legacy-access-LLL333", got_legacy
        assert got_legacy["refresh_token"] == "legacy-refresh-LLL444", got_legacy
        fallback_msgs = [
            r for r in captured if r.getMessage() == "integration_tokens.plaintext_fallback"
        ]
        assert len(fallback_msgs) == 2, (
            f"expected 2 fallback warnings (one per field), got {len(fallback_msgs)}: "
            f"{[r.getMessage() for r in captured]}"
        )
    finally:
        db.close()
    print("dual-read fallback ok (with warning emission)")

    # ---------------------------------------------------------------------
    # 4. Key rotation: encrypt with old key, prepend a new key, the old
    # ciphertext still decrypts. Re-writing the row migrates the
    # at-rest bytes onto the new key.
    # ---------------------------------------------------------------------
    from cryptography.fernet import Fernet

    old_key = os.environ["INTEGRATION_TOKEN_KEYS"].split(",")[0].strip()
    new_key = Fernet.generate_key().decode()

    # Bytes encrypted under the old key BEFORE rotation.
    pre_rotation_ct = itok.encrypt("pre-rotation-secret")

    # Rotate: new key becomes the encrypter, old key stays in the
    # decrypter list. The settings module already cached
    # INTEGRATION_TOKEN_KEYS at import time, so we mutate it directly
    # to simulate a real env-var change + reload.
    from config import settings as cfg
    original_keys = list(cfg.INTEGRATION_TOKEN_KEYS)
    cfg.INTEGRATION_TOKEN_KEYS = [new_key, old_key]
    itok._reset_cipher_for_testing()

    try:
        # Old ciphertext still decrypts because old_key is still in the list.
        assert itok.decrypt(pre_rotation_ct) == "pre-rotation-secret"

        # New writes use the new key. Re-write the test row and confirm the
        # at-rest bytes change.
        db = SessionLocal()
        try:
            pre_rewrite = db.execute(
                sql_text(
                    "SELECT access_token_ciphertext FROM integration_tokens WHERE id = :i"
                ),
                {"i": row_id},
            ).scalar()
            itok.set_token(
                db,
                _TEST_PROVIDER,
                access_token="oauth-access-secret-AAA111",
                refresh_token="oauth-refresh-secret-RRR222",
            )
            db.commit()
            post_rewrite = db.execute(
                sql_text(
                    "SELECT access_token_ciphertext FROM integration_tokens WHERE id = :i"
                ),
                {"i": row_id},
            ).scalar()
            assert bytes(pre_rewrite) != bytes(post_rewrite), (
                "re-write under new key should change at-rest bytes"
            )
            # And the new ciphertext still decrypts back to the original
            # plaintext via the rotated MultiFernet.
            assert itok.decrypt(bytes(post_rewrite)) == "oauth-access-secret-AAA111"
        finally:
            db.close()

        # Drop the old key entirely: pre_rotation_ct should now fail.
        cfg.INTEGRATION_TOKEN_KEYS = [new_key]
        itok._reset_cipher_for_testing()
        try:
            itok.decrypt(pre_rotation_ct)
        except Exception as exc:
            assert exc.__class__.__name__ == "InvalidToken", exc
        else:
            raise AssertionError(
                "decrypt under new-key-only must reject ciphertext from the dropped old key"
            )
    finally:
        # Restore env so re-runs (and the rest of the process) stay sane.
        cfg.INTEGRATION_TOKEN_KEYS = original_keys
        itok._reset_cipher_for_testing()
    print("key rotation + retirement ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_integration_tokens_smoke OK")
