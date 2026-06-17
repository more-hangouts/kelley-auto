"""Time-off service (Phase 8 Slice C of the Sales Portal).

Service-layer for the four state transitions on `time_off_requests`:
`submit`, `cancel`, `decide` (approve/deny), and `amend` (owner edits
proposed times before approval). Every transition writes a paired
`time_off_decision_events` row so the timeline is complete; the
`time_off_requests` row keeps the latest decision for fast reads.

User's Slice C enforcement points the smoke probes against:

  - Sales users can only read/cancel their own requests.
  - Terminal status (`approved | denied | cancelled`) returns 409 on
    any second `decide` / `cancel` / `amend` call.
  - Every state transition writes a `time_off_decision_events` row.

The router maps `TimeOffServiceError.code` to HTTP statuses; codes are
stable so the frontend can render specific copy on each branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import SMTP_FROM_EMAIL
from database.models import (
    BusinessProfile,
    StaffScheduleEntry,
    TimeOffDecisionEvent,
    TimeOffRequest,
    User,
)
from services import email_transport, notification_templates
from services.business_time import to_business_local
from services.email_transport import EmailMessagePayload

log = logging.getLogger(__name__)


# Stable error codes mapped to HTTP statuses by the router.
class TimeOffServiceError(Exception):
    def __init__(
        self,
        code: str,
        *,
        http_status: int = 400,
        extra: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.extra = dict(extra or {})


_TERMINAL_STATUSES = frozenset({"approved", "denied", "cancelled"})


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------


def _owner_email_recipients(db: Session) -> list[str]:
    """Resolve who gets the "new request" notification.

    Preference: `business_profile.email`. Fallback: every active
    `users.role='admin'` row's email. Returning a list keeps the
    multi-owner future possible without another schema change.
    """
    profile = db.query(BusinessProfile).first()
    if profile is not None and profile.email:
        return [profile.email]
    rows = (
        db.query(User)
        .filter(User.role == "admin")
        .filter(User.is_active.is_(True))
        .all()
    )
    return [u.email for u in rows if u.email]


def _send_email_safe(*, to: str, rendered) -> None:
    """Best-effort send: SMTP failures are logged, never raised. The
    user's action (file/decide) succeeds even if email is broken."""
    if not to:
        return
    try:
        transport = email_transport.get_email_transport()
        transport.send(
            EmailMessagePayload(
                to=to,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
                reply_to=SMTP_FROM_EMAIL or None,
            )
        )
    except Exception:
        log.exception("time_off: email send failed for %s", to)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_aware(label: str, dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise TimeOffServiceError(f"naive_{label}", http_status=422)
    return dt.astimezone(timezone.utc)


def _audit(
    db: Session,
    *,
    request: TimeOffRequest,
    actor_user_id: int | None,
    actor_kind: Literal["staff", "owner", "system"],
    action: Literal[
        "requested", "approved", "denied", "cancelled", "amended"
    ],
    old_values: dict,
    new_values: dict,
    notes: str | None = None,
) -> TimeOffDecisionEvent:
    """Append one decision event. The CHECK on `action` and
    `actor_kind` from migration 059 enforces the vocabulary at the DB
    layer; this helper is the only writer in the service."""
    ev = TimeOffDecisionEvent(
        request_id=request.id,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        action=action,
        old_values=old_values,
        new_values=new_values,
        notes=notes,
    )
    db.add(ev)
    db.flush()
    return ev


def _to_dict(
    request: TimeOffRequest,
    *,
    user: User | None = None,
    decided_by: User | None = None,
) -> dict:
    def _iso(dt: datetime | None) -> str | None:
        return dt.astimezone(timezone.utc).isoformat() if dt else None

    def _local(dt: datetime | None) -> str | None:
        return to_business_local(dt).isoformat() if dt else None

    return {
        "id": request.id,
        "user_id": request.user_id,
        "user_full_name": user.full_name if user else None,
        "user_username": user.username if user else None,
        "starts_at": _iso(request.starts_at),
        "starts_at_local": _local(request.starts_at),
        "ends_at": _iso(request.ends_at),
        "ends_at_local": _local(request.ends_at),
        "reason": request.reason,
        "status": request.status,
        "decided_by_user_id": request.decided_by_user_id,
        "decided_by_full_name": decided_by.full_name if decided_by else None,
        "decided_at": _iso(request.decided_at),
        "decision_notes": request.decision_notes,
        "created_at": _iso(request.created_at),
        "updated_at": _iso(request.updated_at),
    }


def _hydrate(
    db: Session, requests: Iterable[TimeOffRequest]
) -> list[dict]:
    rows = list(requests)
    user_ids = {r.user_id for r in rows} | {
        r.decided_by_user_id for r in rows if r.decided_by_user_id is not None
    }
    user_map: dict[int, User] = {}
    if user_ids:
        users = (
            db.execute(select(User).where(User.id.in_(list(user_ids))))
            .scalars()
            .all()
        )
        user_map = {u.id: u for u in users}
    return [
        _to_dict(
            r,
            user=user_map.get(r.user_id),
            decided_by=(
                user_map.get(r.decided_by_user_id)
                if r.decided_by_user_id is not None
                else None
            ),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


def list_for_user(db: Session, *, user_id: int) -> list[dict]:
    """Stylist's own requests, newest first."""
    rows = (
        db.execute(
            select(TimeOffRequest)
            .where(TimeOffRequest.user_id == user_id)
            .order_by(
                TimeOffRequest.created_at.desc(), TimeOffRequest.id.desc()
            )
        )
        .scalars()
        .all()
    )
    return _hydrate(db, rows)


def list_admin(
    db: Session,
    *,
    from_date: datetime,
    to_date: datetime,
    user_id: int | None = None,
    statuses: Iterable[str] | None = None,
) -> list[dict]:
    """Admin-side filterable list. Per the user's Slice C enforcement
    point, admin reads are date-bounded. Both bounds required;
    intersect on `[starts_at < to, ends_at > from]` so a request that
    spans the window is included."""
    if to_date <= from_date:
        raise TimeOffServiceError("invalid_date_range", http_status=422)
    stmt = (
        select(TimeOffRequest)
        .where(TimeOffRequest.starts_at < to_date)
        .where(TimeOffRequest.ends_at > from_date)
        .order_by(
            TimeOffRequest.created_at.desc(), TimeOffRequest.id.desc()
        )
    )
    if user_id is not None:
        stmt = stmt.where(TimeOffRequest.user_id == user_id)
    if statuses:
        stmt = stmt.where(TimeOffRequest.status.in_(list(statuses)))
    rows = db.execute(stmt).scalars().all()
    return _hydrate(db, rows)


def get_for_user(
    db: Session, *, user_id: int, request_id: int
) -> dict | None:
    """Return a stylist's own request by id, or None.

    Sales users can only read their own — the user's enforcement
    point #3. The router maps None to 404 (and to 403 on a request
    owned by a different user — see `_load_for_user_or_raise`).
    """
    request = db.get(TimeOffRequest, request_id)
    if request is None or request.user_id != user_id:
        return None
    return _to_dict(request)


def _load_for_user_or_raise(
    db: Session, *, user_id: int, request_id: int
) -> TimeOffRequest:
    request = db.get(TimeOffRequest, request_id)
    if request is None:
        raise TimeOffServiceError(
            "time_off_request_not_found", http_status=404
        )
    if request.user_id != user_id:
        # Slice C enforcement: a sales user cannot touch another
        # stylist's request, even by id guess. Owner-side admin uses
        # `_load_admin_or_raise` which skips this check.
        raise TimeOffServiceError(
            "time_off_request_not_yours", http_status=403
        )
    return request


def _load_admin_or_raise(
    db: Session, *, request_id: int
) -> TimeOffRequest:
    request = db.get(TimeOffRequest, request_id)
    if request is None:
        raise TimeOffServiceError(
            "time_off_request_not_found", http_status=404
        )
    return request


# ---------------------------------------------------------------------------
# Stylist transitions: submit + cancel
# ---------------------------------------------------------------------------


def submit_request(
    db: Session,
    *,
    user: User,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
) -> dict:
    """Stylist files a new request. Status starts as `pending`. Writes
    one `time_off_decision_events` row with `action='requested'`."""
    starts_utc = _ensure_aware("starts_at", starts_at)
    ends_utc = _ensure_aware("ends_at", ends_at)
    if ends_utc <= starts_utc:
        raise TimeOffServiceError("invalid_date_range", http_status=422)

    cleaned_reason = (reason or "").strip() or None

    request = TimeOffRequest(
        user_id=user.id,
        starts_at=starts_utc,
        ends_at=ends_utc,
        reason=cleaned_reason,
        status="pending",
    )
    db.add(request)
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=user.id,
        actor_kind="staff",
        action="requested",
        old_values={},
        new_values={
            "status": "pending",
            "starts_at": starts_utc.isoformat(),
            "ends_at": ends_utc.isoformat(),
            "reason": cleaned_reason,
        },
    )

    # Notify owner. Best-effort — if SMTP is down the request still
    # lands and the owner sees it next time they open the queue.
    rendered = notification_templates.render_time_off_requested_to_owner(
        request=request, stylist=user
    )
    for to in _owner_email_recipients(db):
        _send_email_safe(to=to, rendered=rendered)

    return _to_dict(request, user=user)


def cancel_request(
    db: Session,
    *,
    user: User,
    request_id: int,
) -> dict:
    """Stylist cancels their own pending request. POST verb (not
    DELETE) so the row is preserved with `status='cancelled'` plus
    the decision-event audit trail — per the user's Slice C
    enforcement #1 ("matches the audit model and avoids implying the
    row disappears").

    Idempotent on a row that's already `cancelled`. Refuses on any
    other terminal status (409) so re-cancelling an approved request
    can't accidentally roll it back.
    """
    request = _load_for_user_or_raise(
        db, user_id=user.id, request_id=request_id
    )
    if request.status == "cancelled":
        return _to_dict(request, user=user)
    if request.status in _TERMINAL_STATUSES:
        raise TimeOffServiceError(
            "time_off_request_terminal", http_status=409
        )
    if request.status != "pending":
        raise TimeOffServiceError(
            "time_off_request_not_pending", http_status=409
        )

    old_status = request.status
    request.status = "cancelled"
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=user.id,
        actor_kind="staff",
        action="cancelled",
        old_values={"status": old_status},
        new_values={"status": "cancelled"},
    )
    return _to_dict(request, user=user)


# ---------------------------------------------------------------------------
# Owner transitions: amend + decide
# ---------------------------------------------------------------------------


def amend_request(
    db: Session,
    *,
    actor_user_id: int,
    request_id: int,
    starts_at: datetime | None,
    ends_at: datetime | None,
    decision_notes: str | None,
) -> dict:
    """Owner edits proposed times before approving.

    Leaves status at `pending` so the stylist still sees a pending
    request; the amendment shows up as an `amended` event in the
    audit timeline. Refuses on a terminal status (409) — once a
    request is approved/denied/cancelled, amending is the wrong tool
    (owner should ask the stylist to file a new request).

    Either `starts_at` OR `ends_at` (or both) must be set. Passing
    neither is a no-op-with-error so the caller can't accidentally
    write a phantom "amended" audit row that didn't change anything.
    """
    if starts_at is None and ends_at is None:
        raise TimeOffServiceError("nothing_to_amend", http_status=422)

    request = _load_admin_or_raise(db, request_id=request_id)
    if request.status in _TERMINAL_STATUSES:
        raise TimeOffServiceError(
            "time_off_request_terminal", http_status=409
        )
    if request.status != "pending":
        raise TimeOffServiceError(
            "time_off_request_not_pending", http_status=409
        )

    new_start = (
        _ensure_aware("starts_at", starts_at)
        if starts_at is not None
        else request.starts_at
    )
    new_end = (
        _ensure_aware("ends_at", ends_at)
        if ends_at is not None
        else request.ends_at
    )
    if new_end <= new_start:
        raise TimeOffServiceError("invalid_date_range", http_status=422)

    old_values = {
        "starts_at": request.starts_at.astimezone(timezone.utc).isoformat(),
        "ends_at": request.ends_at.astimezone(timezone.utc).isoformat(),
    }
    old_starts_at = request.starts_at
    old_ends_at = request.ends_at
    cleaned_notes = (decision_notes or "").strip() or None
    request.starts_at = new_start
    request.ends_at = new_end
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action="amended",
        old_values=old_values,
        new_values={
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
        },
        notes=cleaned_notes,
    )
    stylist = db.get(User, request.user_id)
    amended_by = db.get(User, actor_user_id)
    if stylist is not None and stylist.email:
        rendered = notification_templates.render_time_off_amended_to_staff(
            request=request,
            stylist=stylist,
            amended_by=amended_by,
            previous_starts_at=old_starts_at,
            previous_ends_at=old_ends_at,
            amendment_notes=cleaned_notes,
        )
        _send_email_safe(to=stylist.email, rendered=rendered)
    return _to_dict(request)


def _published_schedule_conflicts_locked(
    db: Session, *, request: TimeOffRequest
) -> list[dict]:
    """Return the list of published schedule entries whose interval
    intersects `request`, after acquiring SELECT ... FOR UPDATE on
    each. Used by `decide_request` when transitioning to 'approved'
    to close the second half of the publish/approve race.

    Why both halves matter:

      * publish-first race — publish commits, then approve. Without
        this lock, decide_request never notices the just-committed
        entry. With the lock, decide_request's SELECT FOR UPDATE on
        schedule_entries waits for publish's UPDATE to release;
        when it does, decide_request sees the new published row and
        raises `schedule_entry_conflict`.
      * approve-first race — approve commits, then publish.
        `services.staff_schedule._conflicting_time_off_locked`
        already locks pending+approved time-off rows and re-reads
        post-lock, so publish sees the now-approved request and
        skips that draft.

    Drafts are intentionally NOT included here: a draft that
    overlaps an approved time-off is the publish path's problem
    (publish_week's per-shift `skipped[]`). Only **published**
    entries are considered, because once published they're already
    visible to staff and need to be explicitly resolved by the
    manager rather than silently coexisting with an approved off.
    """
    rows = (
        db.execute(
            select(StaffScheduleEntry)
            .where(StaffScheduleEntry.user_id == request.user_id)
            .where(StaffScheduleEntry.status == "published")
            .where(StaffScheduleEntry.starts_at_local < request.ends_at)
            .where(StaffScheduleEntry.ends_at_local > request.starts_at)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    return [
        {
            "entry_id": e.id,
            "business_date": e.business_date.isoformat(),
            "starts_at_local": e.starts_at_local.isoformat(),
            "ends_at_local": e.ends_at_local.isoformat(),
        }
        for e in rows
    ]


def decide_request(
    db: Session,
    *,
    actor_user_id: int,
    request_id: int,
    decision: Literal["approved", "denied"],
    decision_notes: str | None,
) -> dict:
    """Owner approves or denies a pending request.

    Writes the decision audit row and stamps the request's
    `decided_by_user_id` / `decided_at` / `decision_notes` columns
    for fast reads. Refuses on any terminal status (409) — the user's
    Slice C enforcement #2.

    When `decision='approved'`, locks any overlapping PUBLISHED
    schedule entries (Slice-4 race fix). If any exist, raises
    `schedule_entry_conflict` with the offending entry ids so the
    manager moves or unpublishes the conflicting shift first.
    Denies do not run the schedule check — denying never creates a
    conflict.
    """
    if decision not in ("approved", "denied"):
        raise TimeOffServiceError("invalid_decision", http_status=422)

    request = _load_admin_or_raise(db, request_id=request_id)
    if request.status in _TERMINAL_STATUSES:
        raise TimeOffServiceError(
            "time_off_request_terminal", http_status=409
        )
    if request.status != "pending":
        raise TimeOffServiceError(
            "time_off_request_not_pending", http_status=409
        )

    if decision == "approved":
        conflicts = _published_schedule_conflicts_locked(
            db, request=request
        )
        if conflicts:
            raise TimeOffServiceError(
                "schedule_entry_conflict",
                http_status=409,
                extra={"entries": conflicts},
            )

    cleaned_notes = (decision_notes or "").strip() or None
    old_status = request.status
    request.status = decision
    request.decided_by_user_id = actor_user_id
    request.decided_at = datetime.now(timezone.utc)
    request.decision_notes = cleaned_notes
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action=decision,
        old_values={"status": old_status},
        new_values={
            "status": decision,
            "decided_by_user_id": actor_user_id,
        },
        notes=cleaned_notes,
    )

    # Notify the stylist of the decision. Best-effort.
    stylist = db.get(User, request.user_id)
    decided_by = db.get(User, actor_user_id)
    if stylist is not None and stylist.email:
        rendered = notification_templates.render_time_off_decided_to_staff(
            request=request, stylist=stylist, decided_by=decided_by
        )
        _send_email_safe(to=stylist.email, rendered=rendered)

    return _to_dict(request)


__all__ = [
    "TimeOffServiceError",
    "amend_request",
    "cancel_request",
    "decide_request",
    "get_for_user",
    "list_admin",
    "list_for_user",
    "submit_request",
]
