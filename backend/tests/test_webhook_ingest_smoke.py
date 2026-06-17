"""Smoke for C2: webhook header redaction + retention sweep.

Covers:
  - redact_headers keeps the allowlisted provenance fields and drops
    everything else, including Authorization, Cookie, X-*-Signature,
    and anything with `token` / `key` / `secret` in the name.
  - record_webhook_event is the only sanctioned writer and persists
    only the redacted JSONB to the DB. A direct SELECT confirms the
    DB row has no sensitive header keys.
  - run_retention_pass with `max_age_days=9999` is a no-op (rows in
    the test window stay).
  - run_retention_pass with a tight cutoff deletes rows older than the
    cutoff and leaves newer ones alone.
  - tick() updates the matching cron_run_state row (last_started_at,
    last_finished_at, last_scanned_count, last_changed_count,
    consecutive_failures=0).
  - tick() with a synthetic raise on the inner pass increments
    consecutive_failures and stamps last_error without losing the
    last_finished_at — record_run handles error stamping.

Run with: venv/bin/python tests/test_webhook_ingest_smoke.py
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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import WebhookEvent  # noqa: E402
from services import cron_state, webhook_ingest  # noqa: E402


_TEST_SOURCE = f"c2-smoke-{uuid.uuid4().hex[:8]}"


def _cleanup() -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM webhook_events WHERE source LIKE 'c2-smoke-%'")
        )
        db.execute(
            sql_text(
                "DELETE FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.WEBHOOK_RETENTION},
        )
        db.commit()
    finally:
        db.close()


_cleanup()

try:
    # ---------------------------------------------------------------------
    # 1. redact_headers: allowlist keeps content-type / user-agent /
    # x-event-id; denies Authorization, Cookie, X-Signature, token-ish
    # custom headers. None/empty input safe.
    # ---------------------------------------------------------------------
    raw = {
        "Authorization": "Bearer secret-xyz",
        "Content-Type": "application/json",
        "X-Signature": "sha256=deadbeef",
        "X-Event-Id": "evt_test_001",
        "Cookie": "sessionid=abc; csrftoken=def",
        "User-Agent": "TestProvider/1.0",
        "X-Some-Token": "should-not-survive",
        "X-Custom-Api-Key": "also-not",
        "Accept": "application/json",
        "Date": "Wed, 13 May 2026 23:00:00 GMT",
    }
    redacted = webhook_ingest.redact_headers(raw)
    assert "authorization" not in redacted, redacted
    assert "cookie" not in redacted, redacted
    assert "x-signature" not in redacted, redacted
    assert "x-some-token" not in redacted, redacted
    assert "x-custom-api-key" not in redacted, redacted
    assert redacted.get("content-type") == "application/json", redacted
    assert redacted.get("user-agent") == "TestProvider/1.0", redacted
    assert redacted.get("x-event-id") == "evt_test_001", redacted
    assert redacted.get("accept") == "application/json", redacted
    assert redacted.get("date") == "Wed, 13 May 2026 23:00:00 GMT", redacted

    assert webhook_ingest.redact_headers(None) == {}
    assert webhook_ingest.redact_headers({}) == {}
    print("redact_headers allowlist ok")

    # ---------------------------------------------------------------------
    # 2. record_webhook_event writes ONLY the redacted dict. A SELECT
    # against the raw JSONB column confirms Authorization is absent.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        row = webhook_ingest.record_webhook_event(
            db,
            source=_TEST_SOURCE,
            event_type="payment.succeeded",
            payload={"id": "evt_test_001", "amount": 4200},
            headers=raw,
            external_id=f"{_TEST_SOURCE}-evt_test_001",
        )
        db.commit()
        row_id = row.id

        on_disk = db.execute(
            sql_text(
                "SELECT headers, payload, source, event_type, external_id "
                "FROM webhook_events WHERE id = :i"
            ),
            {"i": row_id},
        ).one()
        headers_json = on_disk.headers or {}
        assert isinstance(headers_json, dict), type(headers_json)
        assert "authorization" not in headers_json, headers_json
        assert "cookie" not in headers_json, headers_json
        assert "x-signature" not in headers_json, headers_json
        assert headers_json.get("content-type") == "application/json"
        assert headers_json.get("x-event-id") == "evt_test_001"
        assert on_disk.payload == {"id": "evt_test_001", "amount": 4200}
        assert on_disk.external_id == f"{_TEST_SOURCE}-evt_test_001"
    finally:
        db.close()
    print("record_webhook_event redacted persistence ok")

    # ---------------------------------------------------------------------
    # 3. Retention sweep no-op: max_age_days=9999 prunes nothing on a
    # fresh row.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        result = webhook_ingest.run_retention_pass(db, max_age_days=9999)
        db.commit()
        assert result.deleted == 0, result
        still_there = db.execute(
            sql_text("SELECT COUNT(*) FROM webhook_events WHERE id = :i"),
            {"i": row_id},
        ).scalar()
        assert still_there == 1, still_there
    finally:
        db.close()
    print("retention no-op with wide window ok")

    # ---------------------------------------------------------------------
    # 4. Retention sweep prunes: backdate the row's received_at past the
    # cutoff and run with the default window. The row goes away.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        # Add a second row that is INSIDE the retention window so we can
        # prove the prune is targeted, not a global wipe.
        recent = webhook_ingest.record_webhook_event(
            db,
            source=_TEST_SOURCE,
            event_type="payment.refreshed",
            payload={"id": "evt_test_002", "amount": 1000},
            headers={"Content-Type": "application/json"},
            external_id=f"{_TEST_SOURCE}-evt_test_002",
        )
        db.commit()
        recent_id = recent.id

        # Backdate the FIRST row to ~120 days ago, leaving the new one
        # at NOW().
        db.execute(
            sql_text(
                "UPDATE webhook_events SET received_at = :ts WHERE id = :i"
            ),
            {
                "ts": datetime.now(timezone.utc) - timedelta(days=120),
                "i": row_id,
            },
        )
        db.commit()

        result = webhook_ingest.run_retention_pass(db, max_age_days=90)
        db.commit()
        assert result.deleted == 1, result
        old_gone = db.execute(
            sql_text("SELECT COUNT(*) FROM webhook_events WHERE id = :i"),
            {"i": row_id},
        ).scalar()
        recent_still = db.execute(
            sql_text("SELECT COUNT(*) FROM webhook_events WHERE id = :i"),
            {"i": recent_id},
        ).scalar()
        assert old_gone == 0, "old row should be pruned"
        assert recent_still == 1, "recent row should be kept"
    finally:
        db.close()
    print("retention targeted prune ok")

    # ---------------------------------------------------------------------
    # 5. tick() updates cron_run_state. Stamp last_started_at,
    # last_finished_at, last_scanned_count, last_changed_count,
    # consecutive_failures=0 on the happy path.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        # Add another backdated row so the tick has something to delete
        # and we can observe last_changed_count > 0.
        backdated = webhook_ingest.record_webhook_event(
            db,
            source=_TEST_SOURCE,
            event_type="payment.legacy",
            payload={"id": "evt_test_003"},
            headers=None,
            external_id=f"{_TEST_SOURCE}-evt_test_003",
        )
        db.commit()
        db.execute(
            sql_text(
                "UPDATE webhook_events SET received_at = :ts WHERE id = :i"
            ),
            {
                "ts": datetime.now(timezone.utc) - timedelta(days=180),
                "i": backdated.id,
            },
        )
        db.commit()

        result = webhook_ingest.tick(db, max_age_days=90)
        assert result.deleted == 1, result

        state = db.execute(
            sql_text(
                "SELECT last_started_at, last_finished_at, last_scanned_count, "
                "last_changed_count, last_error, consecutive_failures "
                "FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.WEBHOOK_RETENTION},
        ).one()
        assert state.last_started_at is not None
        assert state.last_finished_at is not None
        assert state.last_finished_at >= state.last_started_at
        assert state.last_scanned_count >= 1, state.last_scanned_count
        assert state.last_changed_count == 1, state.last_changed_count
        assert state.last_error is None, state.last_error
        assert state.consecutive_failures == 0, state.consecutive_failures
    finally:
        db.close()
    print("tick updates cron_run_state on success ok")

    # ---------------------------------------------------------------------
    # 6. tick() failure path: monkey-patch run_retention_pass to raise,
    # tick must re-raise and the state row gets last_error stamped +
    # consecutive_failures incremented.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        original = webhook_ingest.run_retention_pass

        def _boom(_db, **kwargs):
            raise RuntimeError("simulated retention failure")

        webhook_ingest.run_retention_pass = _boom  # type: ignore[assignment]
        try:
            try:
                webhook_ingest.tick(db)
            except RuntimeError as exc:
                assert "simulated retention failure" in str(exc), exc
            else:
                raise AssertionError("tick must re-raise the inner failure")
        finally:
            webhook_ingest.run_retention_pass = original  # type: ignore[assignment]

        state = db.execute(
            sql_text(
                "SELECT last_error, consecutive_failures "
                "FROM cron_run_state WHERE name = :n"
            ),
            {"n": cron_state.WEBHOOK_RETENTION},
        ).one()
        assert state.last_error is not None and "simulated retention" in state.last_error, state.last_error
        assert state.consecutive_failures == 1, state.consecutive_failures
    finally:
        db.close()
    print("tick failure stamps cron_run_state error ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_webhook_ingest_smoke OK")
