"""Sales activity monitoring service (Phase 14).

Records the meaningful *reads* a commission-mode sales rep performs so the
owner can see whether reps are actually reviewing leads and contacts — not
just whether they wrote notes. Writes to ``sales_activity_events``
(migration 091). Reads roll the stream up per rep for the admin panel.

Design (mirrors the reasoning in ``services/activity_log.py``, with two
deliberate differences for a *monitoring* stream on *read* endpoints):

  - **No FastAPI import.** This is a plain service; routers pass the
    ``Session`` and the resolved ``actor_user_id``.
  - **Best-effort, never raises.** A monitoring write must never turn a
    rep's search or appointment view into a 500. ``record`` catches every
    exception, rolls back its own row, logs a warning, and returns ``None``.
  - **Self-committing.** Unlike ``activity_log`` (which rides the caller's
    mutating transaction), these fire on GET reads that otherwise commit
    nothing. ``record`` commits its own row so the read still persists the
    audit trail. Safe because the read endpoints have no other pending
    writes at the point of call.
  - **Throttled views.** Refreshing an appointment/lead/contact shouldn't
    spam the timeline, so a view of the same subject by the same rep within
    ``THROTTLE_MINUTES`` collapses to the first. Searches are never
    throttled (each distinct search is signal) — enforced naturally because
    search rows carry no subject.
  - **Privacy by construction.** Callers pass normalized ``metadata`` only.
    Never pass note bodies, financial fields, document keys, portal tokens,
    or raw search text. Search rows carry ``{query_length, result_count}``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import SalesActivityEvent, User

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Activity-type vocabulary (Phase 14.2)
# ---------------------------------------------------------------------------

SALES_LEAD_VIEWED = "sales.lead_viewed"
SALES_APPOINTMENT_VIEWED = "sales.appointment_viewed"
SALES_CONTACT_VIEWED = "sales.contact_viewed"
SALES_SEARCH_PERFORMED = "sales.search_performed"

_KNOWN_TYPES = frozenset(
    {
        SALES_LEAD_VIEWED,
        SALES_APPOINTMENT_VIEWED,
        SALES_CONTACT_VIEWED,
        SALES_SEARCH_PERFORMED,
    }
)

SubjectKind = Literal["event", "appointment", "contact"]

# A repeat view of the same subject by the same rep within this window is
# not re-recorded. Matches the plan's 5-minute suggestion.
THROTTLE_MINUTES = 5


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def record(
    db: Session,
    *,
    actor_user_id: int,
    activity_type: str,
    subject_kind: SubjectKind | None = None,
    subject_id: int | None = None,
    route: str | None = None,
    source: str | None = None,
    metadata: dict[str, Any] | None = None,
    throttle_minutes: int = THROTTLE_MINUTES,
) -> SalesActivityEvent | None:
    """Best-effort record of one sales read. Never raises; commits its own row.

    Returns the written row, or ``None`` if it was throttled or if the write
    failed (a failure is logged, not raised — monitoring must not break the
    read it is observing).

    Throttling applies only to subject-bearing views (event/appointment/
    contact). Search rows have no subject and are always recorded.
    """
    try:
        if activity_type not in _KNOWN_TYPES:
            # Don't drop the row over a typo, but make the anomaly visible.
            log.warning(
                "sales_activity.unknown_type",
                extra={"activity_type": activity_type},
            )
        if (subject_kind is None) != (subject_id is None):
            # Enforced by a DB CHECK too; fail fast here rather than let the
            # insert blow up mid-transaction.
            raise ValueError(
                "subject_kind and subject_id must both be set or both NULL"
            )

        if subject_id is not None and throttle_minutes > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=throttle_minutes)
            exists = (
                db.query(SalesActivityEvent.id)
                .filter(
                    SalesActivityEvent.actor_user_id == actor_user_id,
                    SalesActivityEvent.activity_type == activity_type,
                    SalesActivityEvent.subject_kind == subject_kind,
                    SalesActivityEvent.subject_id == subject_id,
                    SalesActivityEvent.created_at >= cutoff,
                )
                .first()
            )
            if exists is not None:
                return None

        row = SalesActivityEvent(
            actor_user_id=actor_user_id,
            activity_type=activity_type,
            subject_kind=subject_kind,
            subject_id=subject_id,
            route=route,
            source=source,
            activity_metadata=metadata or {},
        )
        db.add(row)
        db.commit()
        return row
    except Exception as exc:  # noqa: BLE001 — monitoring must never break the read
        log.warning(
            "sales_activity.record_failed",
            extra={
                "activity_type": activity_type,
                "actor_user_id": actor_user_id,
                "error_type": type(exc).__name__,
            },
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def normalized_search_metadata(query: str, result_count: int) -> dict[str, Any]:
    """Build a privacy-safe metadata dict for a search row.

    Stores the query *length* and result count — never the raw text, which
    may be a phone number or email. Keep this the single place search
    metadata is shaped so the no-raw-text rule can't drift.
    """
    return {
        "query_length": len(query or ""),
        "result_count": int(result_count),
    }


# ---------------------------------------------------------------------------
# Read (admin reporting)
# ---------------------------------------------------------------------------


@dataclass
class RepActivitySummary:
    actor_user_id: int
    full_name: str | None
    username: str | None
    leads_viewed: int
    appointments_viewed: int
    contacts_viewed: int
    searches: int
    last_activity_at: datetime | None


_TYPE_TO_SUMMARY_FIELD = {
    SALES_LEAD_VIEWED: "leads_viewed",
    SALES_APPOINTMENT_VIEWED: "appointments_viewed",
    SALES_CONTACT_VIEWED: "contacts_viewed",
    SALES_SEARCH_PERFORMED: "searches",
}


def summary_by_rep(
    db: Session,
    *,
    since: datetime,
    until: datetime | None = None,
    actor_user_id: int | None = None,
) -> list[RepActivitySummary]:
    """Per-rep counts of each activity type over a window.

    One row per rep who has any activity in range, plus the rep's last-seen
    timestamp. Ordered by most-recently-active first so the admin panel
    surfaces live reps at the top.
    """
    q = (
        db.query(
            SalesActivityEvent.actor_user_id,
            SalesActivityEvent.activity_type,
            func.count().label("n"),
            func.max(SalesActivityEvent.created_at).label("last_at"),
        )
        .filter(SalesActivityEvent.created_at >= since)
    )
    if until is not None:
        q = q.filter(SalesActivityEvent.created_at < until)
    if actor_user_id is not None:
        q = q.filter(SalesActivityEvent.actor_user_id == actor_user_id)
    q = q.group_by(
        SalesActivityEvent.actor_user_id, SalesActivityEvent.activity_type
    )

    # Fold the (rep, type) rows into one summary per rep.
    summaries: dict[int, RepActivitySummary] = {}
    for uid, atype, n, last_at in q.all():
        s = summaries.get(uid)
        if s is None:
            s = RepActivitySummary(
                actor_user_id=uid,
                full_name=None,
                username=None,
                leads_viewed=0,
                appointments_viewed=0,
                contacts_viewed=0,
                searches=0,
                last_activity_at=None,
            )
            summaries[uid] = s
        field = _TYPE_TO_SUMMARY_FIELD.get(atype)
        if field is not None:
            setattr(s, field, getattr(s, field) + int(n))
        if last_at is not None and (
            s.last_activity_at is None or last_at > s.last_activity_at
        ):
            s.last_activity_at = last_at

    # Attach display identity in one query.
    if summaries:
        users = (
            db.query(User.id, User.full_name, User.username)
            .filter(User.id.in_(summaries.keys()))
            .all()
        )
        for uid, full_name, username in users:
            s = summaries.get(uid)
            if s is not None:
                s.full_name = full_name
                s.username = username

    return sorted(
        summaries.values(),
        key=lambda s: (s.last_activity_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )


@dataclass
class ActivityRow:
    id: int
    actor_user_id: int
    activity_type: str
    subject_kind: str | None
    subject_id: int | None
    route: str | None
    source: str | None
    metadata: dict[str, Any]
    created_at: datetime


def recent_for_rep(
    db: Session,
    *,
    actor_user_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> list[ActivityRow]:
    """Reverse-chronological recent activity for one rep (keyset paginated)."""
    limit = max(1, min(int(limit), 200))
    q = db.query(SalesActivityEvent).filter(
        SalesActivityEvent.actor_user_id == actor_user_id
    )
    if before_id is not None:
        q = q.filter(SalesActivityEvent.id < before_id)
    rows = q.order_by(SalesActivityEvent.id.desc()).limit(limit).all()
    return [
        ActivityRow(
            id=int(r.id),
            actor_user_id=int(r.actor_user_id),
            activity_type=r.activity_type,
            subject_kind=r.subject_kind,
            subject_id=r.subject_id,
            route=r.route,
            source=r.source,
            metadata=dict(r.activity_metadata or {}),
            created_at=r.created_at,
        )
        for r in rows
    ]
