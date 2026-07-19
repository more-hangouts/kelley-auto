"""Smoke for B3: booking widget + confirmation-code rate limits.

Covers:
  - /api/booking/appointments: per-IP bucket trips at 6th submission
    (the limit is 5/min — sized to "real customer books once", with
    a few retries' worth of headroom for flaky-network reload).
  - /api/booking/events: 30 telemetry events from one IP stay well
    under the generous 240/min bucket so legitimate noisy sessions
    are not 429-throttled.
  - /api/booking/abandon shares the telemetry bucket — still records
    to appointment_session_events.
  - /api/booking/boutique-experience/confirm: 5 wrong codes for one
    email from one IP returns 404; 6th returns 429 (per-email bucket
    trips before the row lookup, layered with D1's pending entropy
    boost).
  - Per-email is scoped per email: after burning victim@'s bucket, a
    different email from the same IP still gets 404 (not 429),
    proving the limit isn't a per-IP leak that would enable
    enumeration via response timing.

Uses unique X-Forwarded-For per scenario so per-IP buckets do not
cross-pollute. Flushes the B3 buckets on entry and exit.
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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("RATE_LIMIT_FAIL_OPEN", "true")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api import redis_rate_limit as rrl  # noqa: E402
from api.server import app  # noqa: E402
from database.connection import SessionLocal  # noqa: E402


client = TestClient(app)


_B3_PATTERNS = [
    "rl:booking_create_ip:*",
    "rl:booking_telemetry_ip:*",
    "rl:booking_profile_ip:*",
    "rl:booking_confirm_ip:*",
    "rl:booking_confirm_email:*",
    "rl:booking_token_ip:*",
]


def _flush_test_keys() -> None:
    """Drop every B3 bucket so a re-run starts fresh."""
    redis = rrl.get_client()
    for pattern in _B3_PATTERNS:
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor=cursor, match=pattern)
            if keys:
                redis.delete(*keys)
            if cursor == 0:
                break


def _cleanup_telemetry(event_id_prefix: str) -> None:
    """Drop any session-event rows we wrote, keyed on the test prefix."""
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM appointment_session_events "
                "WHERE event_id LIKE :p OR session_id LIKE :p"
            ),
            {"p": f"{event_id_prefix}%"},
        )
        db.commit()
    finally:
        db.close()


_flush_test_keys()

try:
    # ---------------------------------------------------------------------
    # /appointments per-IP: 5 attempts allowed, 6th is 429.
    # Send {} so body validation 422s — the rate-limit dep runs first
    # and counts every request regardless of downstream status.
    # ---------------------------------------------------------------------
    ip1 = "10.40.0.1"
    for i in range(1, 6):
        resp = client.post(
            "/api/booking/appointments",
            json={},
            headers={"X-Forwarded-For": ip1},
        )
        assert resp.status_code == 422, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/booking/appointments",
        json={},
        headers={"X-Forwarded-For": ip1},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == "rate_limited", resp.text
    assert resp.headers.get("Retry-After") == "60", resp.headers
    print("appointments per-ip 429 ok")

    # ---------------------------------------------------------------------
    # /events telemetry: 30 events from one IP — all 200.
    # The bucket is 240/min so a legitimate noisy session has headroom.
    # ---------------------------------------------------------------------
    telemetry_prefix = f"b3-telemetry-{uuid.uuid4().hex[:8]}"
    ip2 = "10.40.0.2"
    for i in range(1, 31):
        resp = client.post(
            "/api/booking/events",
            json={
                "event_id": f"{telemetry_prefix}-{i}",
                "session_id": f"{telemetry_prefix}-session",
                "event_name": "step_view",
                "step": "date",
            },
            headers={"X-Forwarded-For": ip2},
        )
        assert resp.status_code == 200, (i, resp.status_code, resp.text)
    print("telemetry 30 events under limit ok")

    # ---------------------------------------------------------------------
    # /abandon shares the telemetry bucket and continues to record.
    # Reuse ip2 to confirm the shared bucket has not been blown by the
    # 30 events above (well under 240/min).
    # ---------------------------------------------------------------------
    abandon_event_id = f"{telemetry_prefix}-abandon"
    resp = client.post(
        "/api/booking/abandon",
        json={
            "event_id": abandon_event_id,
            "session_id": f"{telemetry_prefix}-session",
            "step": "step_2",
            "partial": {"celebrant_first_name": "B3"},
            "behavior": {
                "time_on_widget_ms": 12000,
                "interaction_count": 4,
                "steps_completed": 1,
            },
        },
        headers={"X-Forwarded-For": ip2},
    )
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT event_name FROM appointment_session_events "
                "WHERE event_id = :eid"
            ),
            {"eid": abandon_event_id},
        ).first()
        assert row is not None, "abandon did not persist a session event"
        assert row[0] == "abandoned", row[0]
    finally:
        db.close()
    print("abandon shared bucket records ok")

    # ---------------------------------------------------------------------
    # /boutique-experience/confirm per-email: 5 wrong codes for one
    # email from one IP returns 404; 6th returns 429.
    # The per-email bucket trips before the row lookup so a brute-force
    # search over codes for one known email gets shut down.
    # ---------------------------------------------------------------------
    ip3 = "10.40.0.3"
    victim_email = f"victim-{uuid.uuid4().hex[:6]}@example.com"
    profile_payload = {
        "summary": "rate-limit smoke payload — any meaningful field works"
    }
    for i in range(1, 6):
        resp = client.post(
            "/api/booking/boutique-experience/confirm",
            json={
                "confirmation_code": f"BX-WRG{i:02d}",
                "email": victim_email,
                "profile": profile_payload,
            },
            headers={"X-Forwarded-For": ip3},
        )
        assert resp.status_code == 404, (i, resp.status_code, resp.text)
    resp = client.post(
        "/api/booking/boutique-experience/confirm",
        json={
            "confirmation_code": "BX-WRG99",
            "email": victim_email,
            "profile": profile_payload,
        },
        headers={"X-Forwarded-For": ip3},
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == "rate_limited", resp.text
    print("confirm per-email 429 ok")

    # ---------------------------------------------------------------------
    # Per-email is scoped per email: victim_email's bucket is burned,
    # but a different email from the same IP still gets 404. This
    # proves the 429 cannot be used to enumerate which emails are
    # under attack — only the specific email being brute-forced sees
    # the lockout.
    # ---------------------------------------------------------------------
    other_email = f"other-{uuid.uuid4().hex[:6]}@example.com"
    resp = client.post(
        "/api/booking/boutique-experience/confirm",
        json={
            "confirmation_code": "BX-WRG01",
            "email": other_email,
            "profile": profile_payload,
        },
        headers={"X-Forwarded-For": ip3},
    )
    assert resp.status_code == 404, resp.text
    print("confirm per-email scoped ok (no enumeration leak)")

finally:
    _cleanup_telemetry(telemetry_prefix)
    _flush_test_keys()
    rrl.get_client().close()
    print("cleanup done")

print("\ntest_booking_rate_limit_smoke OK")
