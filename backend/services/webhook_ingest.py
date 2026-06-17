"""Webhook ingest helpers: header redaction + retention sweep.

Phase C2 of SECURITY_REMEDIATION_PLAN.md. The `webhook_events` table
is currently a stub (zero rows, no callers in services/api) — the
table will fill up as soon as a provider integration lands and starts
posting events. C2 makes sure that when it does, two things hold:

  1. Sensitive request headers never reach the DB. `record_webhook_event`
     is the only sanctioned writer; it filters incoming headers through
     `redact_headers` which keeps a tight allowlist of provenance /
     debugging fields and drops everything else, including the obvious
     credential carriers (`authorization`, `cookie`, `x-*-signature`,
     `*-key`, `*-token`).

  2. Old rows do not accumulate. A daily retention tick prunes anything
     older than `WEBHOOK_EVENTS_RETENTION_DAYS` (default 90) and records
     the run in `cron_run_state` so the admin status surface can see
     "last pruned, deleted N" without trawling logs.

The redaction strategy is pure allowlist, not denylist. A denylist
silently leaks any new sensitive header a provider invents; an
allowlist fails closed. The cost is that adding a new debugging
header means editing this file, which is the right cost.

Callers should always go through `record_webhook_event`. A raw
`db.add(WebhookEvent(headers=request.headers))` bypasses redaction —
the dedicated writer exists precisely so that footgun is hard to
trigger by accident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config.settings import WEBHOOK_EVENTS_RETENTION_DAYS
from database.models import WebhookEvent
from services import cron_state

log = logging.getLogger(__name__)


# Header allowlist — kept tight on purpose. Anything outside this set
# is dropped before insert. Lowercase comparison.
#
# Rationale per entry:
#   content-type / content-length / accept / accept-encoding — payload
#     framing; useful for "did the provider send what we expected?"
#   user-agent — provenance / version of the calling integration.
#   x-request-id / x-event-id / x-message-id — generic provider
#     correlation IDs. Provider-specific message IDs that don't fit
#     these names should be promoted to the dedicated `external_id`
#     column instead of leaking through the headers JSONB.
#   date — clock-skew debugging.
_ALLOWED_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "user-agent",
        "accept",
        "accept-encoding",
        "x-request-id",
        "x-event-id",
        "x-message-id",
        "date",
    }
)


def redact_headers(raw: dict | None) -> dict:
    """Return a redacted copy of `raw`, keeping only allowlisted keys.

    Header names are lowercased before comparison so a provider's
    `X-Event-Id` and `x-event-id` collapse to the same key. Values are
    coerced to strings — JSONB cannot store the variety of objects
    starlette / requests sometimes hands in (e.g. Headers proxies).
    """
    if not raw:
        return {}
    redacted: dict[str, str] = {}
    for key, value in raw.items():
        if key is None:
            continue
        norm = str(key).lower().strip()
        if norm in _ALLOWED_HEADERS:
            redacted[norm] = str(value) if value is not None else ""
    return redacted


def record_webhook_event(
    db: Session,
    *,
    source: str,
    event_type: str,
    payload: dict,
    headers: dict | None = None,
    external_id: str | None = None,
) -> WebhookEvent:
    """Insert a webhook event row with redacted headers.

    Callers should funnel ALL webhook inserts through here so the
    allowlist redaction can't be bypassed. `external_id` populates the
    dedup unique index — pass the provider's stable message id where
    one exists.
    """
    row = WebhookEvent(
        source=source,
        event_type=event_type,
        external_id=external_id,
        payload=payload,
        headers=redact_headers(headers),
    )
    db.add(row)
    db.flush()
    return row


@dataclass
class RetentionResult:
    scanned: int
    deleted: int
    cutoff_at: datetime


def run_retention_pass(
    db: Session, *, max_age_days: int | None = None
) -> RetentionResult:
    """Delete `webhook_events` rows older than the cutoff.

    Default cutoff is `WEBHOOK_EVENTS_RETENTION_DAYS`. Pass an explicit
    `max_age_days` from a manual `tick(db, max_age_days=...)` call to
    test the prune against a tighter window (or 9999 to confirm a no-op
    leaves the table untouched).

    The existing `idx_webhook_events_received_at` covers
    `received_at < cutoff` so the prune is index-supported even on a
    multi-million-row table.
    """
    days = int(max_age_days) if max_age_days is not None else WEBHOOK_EVENTS_RETENTION_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    scanned = db.execute(
        sql_text("SELECT COUNT(*) FROM webhook_events")
    ).scalar() or 0

    result = db.execute(
        sql_text(
            "DELETE FROM webhook_events WHERE received_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    deleted = int(result.rowcount or 0)
    db.flush()
    return RetentionResult(scanned=int(scanned), deleted=deleted, cutoff_at=cutoff)


def tick(db: Session, *, max_age_days: int | None = None) -> RetentionResult:
    """Worker entrypoint. Wraps `run_retention_pass` in a cron-state
    record_run so admin can see when the prune last ran and how many
    rows it deleted."""
    with cron_state.record_run(cron_state.WEBHOOK_RETENTION) as run:
        result = run_retention_pass(db, max_age_days=max_age_days)
        run.scanned = result.scanned
        run.changed = result.deleted
        db.commit()
    return result
