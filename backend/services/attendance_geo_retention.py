"""Attendance geo / IP / UA retention sweep (G2 of SECURITY_REMEDIATION_PLAN.md).

Daily pass that NULLs the privacy-sensitive `staff_punches` columns
once a punch is older than the configured retention window. The
columns scrubbed:

    client_latitude
    client_longitude
    client_accuracy_m
    user_agent
    ip

These are the PII vectors — exact device coordinates, accuracy
beacon, browser fingerprint, and source IP. Once a punch is closed
and any disputes have been resolved (the operator's
`selfie_retention_days` setting defines that horizon), there is no
operational reason to keep them.

Preserved on every row:

    location_id              — which boutique, anonymous after the geo
                               coords are gone
    distance_to_location_m   — was-inside-geofence audit, anonymous
    status                   — recorded / auto_closed / etc.
    direction, punched_at,   — the actual punch event
    user_id, shift_id, ...

Design choices:

  - **Reuses `business_profile.selfie_retention_days`** instead of
    introducing a separate `attendance_geo_retention_days` setting.
    Same domain, same privacy intent, fewer knobs for operators to
    keep in sync. If a future split is needed (e.g. keep selfies
    for HR longer than coords), it's a one-line schema change away.

  - **NULL retention = keep forever.** Mirrors `clock_selfie_retention`
    so the two retention surfaces obey the same operator-controlled
    toggle.

  - **Audit row per redacted punch.** A `staff_punch_audit_events`
    row with `action='geo.retention_scrubbed'` lands for every punch
    that is touched. The `old_values` field carries the redacted
    column names (not their values — the audit trail is for "did
    this happen?" not "what was the value?"). Append-only triggers
    from C4 protect the audit row afterwards.

  - **Idempotent.** The candidate query filters
    `client_latitude IS NOT NULL OR ip IS NOT NULL OR user_agent IS NOT NULL`
    so once a row is scrubbed, the next sweep skips it.

  - **Selfie file behavior unaffected.** The selfie retention cron
    runs independently; both can run in any order on the same day
    without conflict because each touches its own column set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import (
    BusinessProfile,
    StaffPunch,
    StaffPunchAuditEvent,
)
from services import cron_state

log = logging.getLogger(__name__)


# Column names scrubbed by this sweep. Listed here so the audit row's
# `old_values` records exactly which fields were redacted on each pass
# — useful if the column set ever changes and an operator wants to
# audit historical retention events.
SCRUBBED_FIELDS: tuple[str, ...] = (
    "client_latitude",
    "client_longitude",
    "client_accuracy_m",
    "user_agent",
    "ip",
)


@dataclass
class RetentionResult:
    scanned: int
    scrubbed: int


def _resolve_retention_days(db: Session) -> int | None:
    profile = db.query(BusinessProfile).first()
    if profile is None:
        return None
    return profile.selfie_retention_days


def _cutoff(retention_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=retention_days)


def run_retention_pass(
    db: Session,
    *,
    now_override: datetime | None = None,
) -> RetentionResult:
    """One retention tick.

    `now_override` is for the smoke; production lets the helper compute
    its own "now" so a cron that runs slightly late doesn't use a stale
    cutoff.
    """
    retention_days = _resolve_retention_days(db)
    if retention_days is None:
        # NULL = forever. No-op, cron-state row records a clean run.
        return RetentionResult(scanned=0, scrubbed=0)

    if now_override is not None:
        cutoff = now_override - timedelta(days=retention_days)
    else:
        cutoff = _cutoff(retention_days)

    # Idempotency filter: only pick up rows that still carry any of the
    # PII columns. Once scrubbed they're invisible to the next pass.
    candidates = (
        db.query(StaffPunch)
        .filter(StaffPunch.punched_at < cutoff)
        .filter(
            or_(
                StaffPunch.client_latitude.isnot(None),
                StaffPunch.client_longitude.isnot(None),
                StaffPunch.client_accuracy_m.isnot(None),
                StaffPunch.user_agent.isnot(None),
                StaffPunch.ip.isnot(None),
            )
        )
        .order_by(StaffPunch.id)
        .all()
    )

    scanned = len(candidates)
    scrubbed = 0

    for punch in candidates:
        # Record which fields were non-null at the moment of scrubbing
        # — the audit trail is "we cleared these"; we don't persist the
        # values themselves (defeats the point of retention).
        cleared_names = []
        for name in SCRUBBED_FIELDS:
            if getattr(punch, name) is not None:
                cleared_names.append(name)
                setattr(punch, name, None)

        if not cleared_names:
            # Defensive — the candidate filter should have ruled this
            # out, but a concurrent write could theoretically race.
            continue

        db.add(
            StaffPunchAuditEvent(
                punch_id=punch.id,
                actor_kind="system",
                actor_user_id=None,
                action="geo.retention_scrubbed",
                reason_code="retention_policy",
                old_values={"cleared_fields": cleared_names},
                new_values={"cleared_fields": []},
                notes=(
                    f"retention {retention_days}d: scrubbed "
                    f"{len(cleared_names)} PII field(s)"
                ),
            )
        )
        scrubbed += 1

    db.flush()
    return RetentionResult(scanned=scanned, scrubbed=scrubbed)


def tick(db: Session) -> RetentionResult:
    """Worker entrypoint. Wraps `run_retention_pass` in a cron-state
    `record_run` so the admin status surface sees the last successful
    pass."""
    with cron_state.record_run(cron_state.ATTENDANCE_GEO_RETENTION) as run:
        result = run_retention_pass(db)
        run.scanned = result.scanned
        run.changed = result.scrubbed
        db.commit()
    return result
