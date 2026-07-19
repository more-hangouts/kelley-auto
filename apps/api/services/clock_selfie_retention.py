"""Selfie retention sweep (Phase 7 Slice 2B-3 of the Sales Portal).

Daily pass that deletes selfie files older than the owner's
`business_profile.selfie_retention_days` setting while preserving
every other punch column. Implements the lock-in: "A retention job
deletes old selfie files according to the owner setting while
preserving punch metadata."

Design notes:

  - **Files only**, never punch rows. Punch rows carry coordinates,
    timestamps, audit attribution, and the geofence audit; the
    selfie is the only artifact that decays. Deletion sets
    `staff_punches.selfie_storage_key = NULL` so a future read
    doesn't dangle a key pointing at a missing file.
  - **NULL retention means "keep forever"** — the cron exits with a
    zero-row no-op. The owner setting allows this explicitly.
  - **Uses the captured VPS path**: per the deploy notes, the systemd
    unit's `ReadWritePaths` covers `/var/lib/bellas-xv/uploads`. The
    selfie key is `clockin/<user_id>/<punch_id>.webp`, which lives
    under that path; `document_storage.delete_object` is the same
    helper Slice 2A used to write the file, so the path discipline
    matches end-to-end.
  - **Idempotent**: re-running on the same day is a no-op past the
    first sweep because every match is followed by a NULL of the
    storage key. A row whose key has already been NULLed is invisible
    to the next pass.
  - **Audit** trail per delete: an audit row with action
    `selfie.retention_deleted` and the prior storage key in
    `old_values` lands in `staff_punch_audit_events`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from database.models import (
    BusinessProfile,
    StaffPunch,
    StaffPunchAuditEvent,
)
from services import cron_state, document_storage

log = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    scanned: int
    deleted_files: int
    cleared_keys: int
    skipped_missing_files: int


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

    `now_override` is for the smoke; production calls let the helper
    compute its own "now" so a cron that runs slightly late doesn't
    use a stale cutoff.
    """
    retention_days = _resolve_retention_days(db)
    if retention_days is None:
        # NULL = forever. The cron is a no-op and the cron-state row
        # records that we ran cleanly.
        return RetentionResult(0, 0, 0, 0)

    if now_override is not None:
        cutoff = now_override - timedelta(days=retention_days)
    else:
        cutoff = _cutoff(retention_days)

    candidates = (
        db.query(StaffPunch)
        .filter(StaffPunch.selfie_storage_key.isnot(None))
        .filter(StaffPunch.punched_at < cutoff)
        .order_by(StaffPunch.id)
        .all()
    )

    scanned = len(candidates)
    deleted_files = 0
    cleared_keys = 0
    skipped_missing = 0

    for punch in candidates:
        prior_key = punch.selfie_storage_key
        existed = document_storage.object_exists(prior_key)
        try:
            document_storage.delete_object(prior_key)
        except Exception:
            # Best-effort delete — a permission error here would
            # typically be the same `ReadWritePaths` failure the Slice
            # 2A smoke covers. Log and move on so one bad row doesn't
            # block the whole sweep.
            log.exception("retention: delete_object failed for %s", prior_key)
            continue
        if existed:
            deleted_files += 1
        else:
            skipped_missing += 1
        punch.selfie_storage_key = None
        cleared_keys += 1
        db.add(
            StaffPunchAuditEvent(
                punch_id=punch.id,
                actor_kind="system",
                actor_user_id=None,
                action="selfie.retention_deleted",
                reason_code="retention_policy",
                old_values={"selfie_storage_key": prior_key},
                new_values={"selfie_storage_key": None},
                notes=(
                    f"retention {retention_days}d: removed selfie file"
                ),
            )
        )

    db.flush()
    return RetentionResult(
        scanned=scanned,
        deleted_files=deleted_files,
        cleared_keys=cleared_keys,
        skipped_missing_files=skipped_missing,
    )


def tick(db: Session) -> RetentionResult:
    """Worker entrypoint. Wraps `run_retention_pass` in a cron-state
    record_run so the admin status surface knows when this last
    completed."""
    with cron_state.record_run(cron_state.SELFIE_RETENTION) as run:
        result = run_retention_pass(db)
        run.scanned = result.scanned
        run.changed = result.cleared_keys
        db.commit()
    return result
