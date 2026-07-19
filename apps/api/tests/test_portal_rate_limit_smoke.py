"""Smoke for B4: Redis-backed portal token rate limits.

Covers:
  - Public invoice token lookups are capped per token key at 30/min.
  - A different key from the same IP still reaches the normal 404 path,
    proving the limiter is not just a coarse per-IP enumeration oracle.
  - A valid quote link and one signature submit still work when the
    Redis limiter is active.

Runs as a script:

    venv/bin/python tests/test_portal_rate_limit_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi.testclient import TestClient  # noqa: E402

from api import redis_rate_limit as rrl  # noqa: E402
from api.routers.portal import _reset_rate_limit_state  # noqa: E402
from api.server import app  # noqa: E402
from tests.test_portal_smoke import (  # noqa: E402
    _cleanup,
    _get_invitation_for_quote,
    _make_sent_quote,
    _seed_admin,
    _seed_event,
)

client = TestClient(app)


_B4_PATTERNS = [
    "rl:portal_ip:*",
    "rl:portal_key:*",
]


def _flush_test_keys() -> None:
    redis = rrl.get_client()
    for pattern in _B4_PATTERNS:
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor=cursor, match=pattern)
            if keys:
                redis.delete(*keys)
            if cursor == 0:
                break
    _reset_rate_limit_state()


def check_invalid_key_bucket_trips() -> None:
    ip = "10.60.0.1"
    key = f"missing-{uuid.uuid4().hex}"

    for i in range(1, 31):
        resp = client.get(
            f"/portal/invoice/{key}",
            headers={"X-Forwarded-For": ip},
        )
        assert resp.status_code == 404, (i, resp.status_code, resp.text)

    resp = client.get(
        f"/portal/invoice/{key}",
        headers={"X-Forwarded-For": ip},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == "rate_limited", resp.text
    assert resp.headers.get("Retry-After") == "60", resp.headers

    fresh_key = f"missing-{uuid.uuid4().hex}"
    fresh = client.get(
        f"/portal/invoice/{fresh_key}",
        headers={"X-Forwarded-For": ip},
    )
    assert fresh.status_code == 404, fresh.text
    print("portal per-token invalid-key bucket 429 ok")


def check_valid_quote_signature_still_works() -> None:
    user_ids = []
    contact_ids = []
    event_ids = []
    try:
        user_id, _email = _seed_admin()
        user_ids.append(user_id)
        contact_id, event_id = _seed_event("Portal Rate Limit")
        contact_ids.append(contact_id)
        event_ids.append(event_id)

        quote_id = _make_sent_quote(
            event_id=event_id,
            contact_id=contact_id,
            user_id=user_id,
        )
        public_key = _get_invitation_for_quote(quote_id).public_key
        headers = {"X-Forwarded-For": "10.60.0.2"}

        page = client.get(f"/portal/quote/{public_key}", headers=headers)
        assert page.status_code == 200, page.text
        assert "Read and sign" in page.text, page.text[:500]

        resp = client.post(
            f"/portal/quote/{public_key}/accept",
            headers=headers,
            json={
                "signature_name": "Maria Lopez",
                "signature_base64": (
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
                    "QVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
                ),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved", body
        assert body["signed_at"], body
        print("portal valid quote signature under limiter ok")
    finally:
        _cleanup(user_ids, contact_ids, event_ids)


def main() -> int:
    _flush_test_keys()
    try:
        check_invalid_key_bucket_trips()
        _flush_test_keys()
        check_valid_quote_signature_still_works()
        print("\ntest_portal_rate_limit_smoke OK")
        return 0
    finally:
        _flush_test_keys()
        try:
            rrl.get_client().close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
