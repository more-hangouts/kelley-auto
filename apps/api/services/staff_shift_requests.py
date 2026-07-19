"""Staff shift-request service (Scheduling Phase 1).

Durable, auditable request records for the cover/swap/drop/pickup
workflow in docs/SCHEDULING_IMPROVEMENT_PLAN.md. Phase 1 implements the
read-only queue plus the two transitions staff can drive themselves:
`create` and `cancel`. NO schedule mutation happens here — approving a
request and transferring a shift land in Phase 2+.

Every transition writes a paired `staff_shift_request_events` row so the
timeline is complete; the `staff_shift_requests` row keeps the latest
state for fast reads. Stable error codes are mapped to HTTP statuses by
the routers:

    entry_not_found        404  source/target entry id doesn't exist
    entry_not_published    409  entry is a draft, not a published shift
    entry_not_yours        403  requester doesn't own the source shift
    entry_started          409  shift has started or has attendance
    request_not_found      404  request id doesn't exist / not visible
    request_terminal       409  request is already in a terminal state
    invalid_request_type   422  type unsupported in this phase / malformed
    invalid_candidate      422  proposed candidate isn't an eligible peer
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database.models import (
    OpenShiftPost,
    StaffScheduleEntry,
    StaffShiftRequest,
    StaffShiftRequestEvent,
    User,
)
from services import open_shifts, shift_request_notifications, staff_schedule
from services.business_time import to_business_local
from services.open_shifts import OpenShiftPostError
from services.staff_schedule import StaffScheduleError


# Stable error codes mapped to HTTP statuses by the routers.
class StaffShiftRequestError(Exception):
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


_TERMINAL_STATUSES = frozenset(
    {"approved", "denied", "cancelled", "expired"}
)

# Pickup needs `open_shift_posts`, which lands in Phase 3 — so only the
# entry-backed types are creatable in Phase 1.
_CREATABLE_TYPES = frozenset({"cover", "drop", "swap"})

# Gate 1 (resolved): staff may request up to 12h before the shift starts;
# inside that window only an admin can act, and only while no attendance
# exists (enforced separately by the started-shift guard).
_STAFF_REQUEST_CUTOFF = timedelta(hours=12)


# ---------------------------------------------------------------------------
# Audit + hydration helpers
# ---------------------------------------------------------------------------


def _audit(
    db: Session,
    *,
    request: StaffShiftRequest,
    actor_user_id: int | None,
    actor_kind: Literal["staff", "owner", "system"],
    action: Literal[
        "requested", "accepted", "approved", "denied",
        "cancelled", "expired", "amended",
    ],
    old_values: dict,
    new_values: dict,
    notes: str | None = None,
) -> StaffShiftRequestEvent:
    """Append one request event. The CHECKs on `action`/`actor_kind`
    (migration 081) enforce the vocabulary at the DB layer; this helper
    is the only writer in the service."""
    ev = StaffShiftRequestEvent(
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


def _entry_summary(entry: StaffScheduleEntry | None) -> dict | None:
    """Coworker-safe shift summary: names/times/status only. No
    `manager_notes`, attendance, or punch ids — those stay manager-only
    (the same boundary the sales team schedule enforces)."""
    if entry is None:
        return None
    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "business_date": entry.business_date.isoformat(),
        "starts_at_local": to_business_local(entry.starts_at_local).isoformat(),
        "ends_at_local": to_business_local(entry.ends_at_local).isoformat(),
        "status": entry.status,
    }


def _post_summary(post: "OpenShiftPost | None") -> dict | None:
    if post is None:
        return None
    return {
        "id": post.id,
        "business_date": post.business_date.isoformat(),
        "starts_at_local": to_business_local(post.starts_at_local).isoformat(),
        "ends_at_local": to_business_local(post.ends_at_local).isoformat(),
        "note": post.manager_notes,
        "status": post.status,
    }


def _to_dict(
    request: StaffShiftRequest,
    *,
    users: dict[int, User],
    entries: dict[int, StaffScheduleEntry],
    posts: dict[int, "OpenShiftPost"] | None = None,
) -> dict:
    posts = posts or {}
    def _iso(dt: datetime | None) -> str | None:
        return dt.astimezone(timezone.utc).isoformat() if dt else None

    def _name(user_id: int | None) -> str | None:
        u = users.get(user_id) if user_id is not None else None
        return u.full_name if u else None

    return {
        "id": request.id,
        "request_type": request.request_type,
        "status": request.status,
        "source_entry_id": request.source_entry_id,
        "target_entry_id": request.target_entry_id,
        "open_shift_post_id": request.open_shift_post_id,
        "requester_user_id": request.requester_user_id,
        "requester_full_name": _name(request.requester_user_id),
        "candidate_user_id": request.candidate_user_id,
        "candidate_full_name": _name(request.candidate_user_id),
        "accepted_by_user_id": request.accepted_by_user_id,
        "accepted_at": _iso(request.accepted_at),
        "decided_by_user_id": request.decided_by_user_id,
        "decided_by_full_name": _name(request.decided_by_user_id),
        "decided_at": _iso(request.decided_at),
        "reason": request.reason,
        "decision_notes": request.decision_notes,
        "created_at": _iso(request.created_at),
        "updated_at": _iso(request.updated_at),
        "source_entry": _entry_summary(entries.get(request.source_entry_id)),
        "target_entry": _entry_summary(entries.get(request.target_entry_id)),
        "open_shift_post": _post_summary(
            posts.get(request.open_shift_post_id)
        ),
    }


def _hydrate(
    db: Session, requests: Iterable[StaffShiftRequest]
) -> list[dict]:
    rows = list(requests)
    user_ids: set[int] = set()
    entry_ids: set[int] = set()
    for r in rows:
        for uid in (
            r.requester_user_id,
            r.candidate_user_id,
            r.accepted_by_user_id,
            r.decided_by_user_id,
        ):
            if uid is not None:
                user_ids.add(uid)
        for eid in (r.source_entry_id, r.target_entry_id):
            if eid is not None:
                entry_ids.add(eid)
    users: dict[int, User] = {}
    if user_ids:
        users = {
            u.id: u
            for u in db.execute(
                select(User).where(User.id.in_(list(user_ids)))
            )
            .scalars()
            .all()
        }
    entries: dict[int, StaffScheduleEntry] = {}
    if entry_ids:
        entries = {
            e.id: e
            for e in db.execute(
                select(StaffScheduleEntry).where(
                    StaffScheduleEntry.id.in_(list(entry_ids))
                )
            )
            .scalars()
            .all()
        }
    post_ids = {
        r.open_shift_post_id
        for r in rows
        if r.open_shift_post_id is not None
    }
    posts: dict[int, OpenShiftPost] = {}
    if post_ids:
        posts = {
            p.id: p
            for p in db.execute(
                select(OpenShiftPost).where(
                    OpenShiftPost.id.in_(list(post_ids))
                )
            )
            .scalars()
            .all()
        }
    return [
        _to_dict(r, users=users, entries=entries, posts=posts) for r in rows
    ]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _entry_started(entry: StaffScheduleEntry) -> bool:
    """A request can't mutate a shift that has begun or has any attendance
    stamped (the locked v1 decision). Single source of truth lives in
    ``staff_schedule.entry_has_started``."""
    return staff_schedule.entry_has_started(entry)


def _load_published_entry(
    db: Session, entry_id: int | None
) -> StaffScheduleEntry:
    if entry_id is None:
        raise StaffShiftRequestError("entry_not_found", http_status=404)
    entry = db.get(StaffScheduleEntry, entry_id)
    if entry is None:
        raise StaffShiftRequestError("entry_not_found", http_status=404)
    if entry.status != "published":
        raise StaffShiftRequestError("entry_not_published", http_status=409)
    return entry


def _validate_candidate(
    db: Session, *, candidate_user_id: int, requester_id: int
) -> None:
    if candidate_user_id == requester_id:
        raise StaffShiftRequestError("invalid_candidate", http_status=422)
    u = db.get(User, candidate_user_id)
    if u is None or not u.is_active or u.role != "sales":
        raise StaffShiftRequestError("invalid_candidate", http_status=422)


def _is_involved(request: StaffShiftRequest, user_id: int) -> bool:
    """Conservative visibility (Gate 3 default): a stylist sees a request
    only if they're the requester, the proposed candidate, or the staffer
    who accepted it."""
    return user_id in {
        request.requester_user_id,
        request.candidate_user_id,
        request.accepted_by_user_id,
    }


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


def list_for_user(db: Session, *, user_id: int) -> list[dict]:
    """Requests a stylist is involved in, newest first."""
    rows = (
        db.execute(
            select(StaffShiftRequest)
            .where(
                or_(
                    StaffShiftRequest.requester_user_id == user_id,
                    StaffShiftRequest.candidate_user_id == user_id,
                    StaffShiftRequest.accepted_by_user_id == user_id,
                )
            )
            .order_by(
                StaffShiftRequest.created_at.desc(),
                StaffShiftRequest.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    return _hydrate(db, rows)


def get_for_user(
    db: Session, *, user_id: int, request_id: int
) -> dict | None:
    """A stylist's view of one request, or None when it doesn't exist or
    they're not involved (the router maps None to 404 so a coworker can't
    probe for a private direct request's existence)."""
    request = db.get(StaffShiftRequest, request_id)
    if request is None or not _is_involved(request, user_id):
        return None
    return _hydrate(db, [request])[0]


def get_admin(db: Session, *, request_id: int) -> dict | None:
    """Owner's view of one request (sees every request), or None. Includes
    a `candidate_conflicts` preview so the approval UI can warn before the
    transfer hard-blocks."""
    request = db.get(StaffShiftRequest, request_id)
    if request is None:
        return None
    row = _hydrate(db, [request])[0]
    row["candidate_conflicts"] = candidate_conflicts(db, request_id=request_id)
    return row


def list_admin(
    db: Session,
    *,
    statuses: Iterable[str] | None = None,
    requester_user_id: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Owner queue: newest first, optionally filtered by status and/or
    requester. Bounded by `limit` so the queue read stays cheap."""
    stmt = select(StaffShiftRequest).order_by(
        StaffShiftRequest.created_at.desc(), StaffShiftRequest.id.desc()
    )
    if statuses:
        stmt = stmt.where(StaffShiftRequest.status.in_(list(statuses)))
    if requester_user_id is not None:
        stmt = stmt.where(
            StaffShiftRequest.requester_user_id == requester_user_id
        )
    stmt = stmt.limit(max(1, min(limit, 1000)))
    rows = db.execute(stmt).scalars().all()
    return _hydrate(db, rows)


# ---------------------------------------------------------------------------
# Stylist transitions: create + cancel
# ---------------------------------------------------------------------------


def create_request(
    db: Session,
    *,
    requester: User,
    request_type: str,
    source_entry_id: int | None,
    target_entry_id: int | None = None,
    candidate_user_id: int | None = None,
    reason: str | None = None,
) -> dict:
    """Stylist files a cover/drop/swap request against their own future
    published shift. Status starts `pending`; one `requested` event is
    written. No schedule mutation happens (Phase 2 handles approval)."""
    if request_type not in _CREATABLE_TYPES:
        raise StaffShiftRequestError(
            "invalid_request_type", http_status=422
        )

    # The source shift must be the requester's own, published, future.
    source = _load_published_entry(db, source_entry_id)
    if source.user_id != requester.id:
        raise StaffShiftRequestError("entry_not_yours", http_status=403)
    if _entry_started(source):
        raise StaffShiftRequestError("entry_started", http_status=409)
    # Gate 1: staff cutoff is 12h before start; inside that window the
    # admin handles it directly (still bounded by the started guard).
    if (
        source.starts_at_local.astimezone(timezone.utc)
        - datetime.now(timezone.utc)
    ) < _STAFF_REQUEST_CUTOFF:
        raise StaffShiftRequestError(
            "request_cutoff_passed", http_status=409
        )

    resolved_candidate: int | None = None

    if request_type == "swap":
        # Swap targets another stylist's published, future shift.
        target = _load_published_entry(db, target_entry_id)
        if target.user_id == requester.id:
            # Can't swap a shift with yourself.
            raise StaffShiftRequestError(
                "invalid_candidate", http_status=422
            )
        if _entry_started(target):
            raise StaffShiftRequestError("entry_started", http_status=409)
        _validate_candidate(
            db,
            candidate_user_id=target.user_id,
            requester_id=requester.id,
        )
        resolved_candidate = target.user_id
    else:
        # cover/drop must not carry a target entry.
        if target_entry_id is not None:
            raise StaffShiftRequestError(
                "invalid_request_type", http_status=422
            )
        if request_type == "cover" and candidate_user_id is not None:
            # Direct cover names a peer; an open cover leaves it null.
            _validate_candidate(
                db,
                candidate_user_id=candidate_user_id,
                requester_id=requester.id,
            )
            resolved_candidate = candidate_user_id
        # drop carries no candidate.

    cleaned_reason = (reason or "").strip() or None
    request = StaffShiftRequest(
        request_type=request_type,
        status="pending",
        source_entry_id=source.id,
        target_entry_id=target_entry_id if request_type == "swap" else None,
        requester_user_id=requester.id,
        candidate_user_id=resolved_candidate,
        reason=cleaned_reason,
    )
    db.add(request)
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=requester.id,
        actor_kind="staff",
        action="requested",
        old_values={},
        new_values={
            "status": "pending",
            "request_type": request_type,
            "source_entry_id": source.id,
            "target_entry_id": request.target_entry_id,
            "candidate_user_id": resolved_candidate,
        },
    )
    # A direct cover names a candidate up front — ask them to accept.
    # (Open cover with no candidate is visible on the board instead;
    # swap candidate notification arrives with the Phase 4 swap flow.)
    if request_type == "cover" and resolved_candidate is not None:
        candidate_user = db.get(User, resolved_candidate)
        if candidate_user is not None:
            shift_request_notifications.notify_cover_requested(
                db,
                request_id=request.id,
                candidate=candidate_user,
                requester=requester,
                source_entry=source,
            )
    elif request_type == "swap" and resolved_candidate is not None:
        candidate_user = db.get(User, resolved_candidate)
        target_entry = db.get(StaffScheduleEntry, target_entry_id)
        if candidate_user is not None and target_entry is not None:
            shift_request_notifications.notify_swap_requested(
                db,
                request_id=request.id,
                candidate=candidate_user,
                requester=requester,
                source_entry=source,
                target_entry=target_entry,
            )
    return _hydrate(db, [request])[0]


def claim_open_shift(db: Session, *, user: User, post_id: int) -> dict:
    """A staffer claims a posted open shift (Phase 3). Claiming IS the
    acceptance (Gate 2: open pickup skips the separate accept step), so the
    pickup request goes straight to `pending`, awaiting admin approval."""
    post = db.get(OpenShiftPost, post_id)
    if post is None:
        raise StaffShiftRequestError("post_not_found", http_status=404)
    if post.status != "open":
        raise StaffShiftRequestError("post_not_open", http_status=409)
    if (
        post.starts_at_local.astimezone(timezone.utc)
        - datetime.now(timezone.utc)
    ) < _STAFF_REQUEST_CUTOFF:
        raise StaffShiftRequestError(
            "request_cutoff_passed", http_status=409
        )
    # One active claim per staffer per post.
    existing = (
        db.execute(
            select(StaffShiftRequest.id)
            .where(StaffShiftRequest.open_shift_post_id == post_id)
            .where(StaffShiftRequest.requester_user_id == user.id)
            .where(
                StaffShiftRequest.status.in_(
                    ["pending", "accepted_by_staff"]
                )
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise StaffShiftRequestError("already_claimed", http_status=409)

    request = StaffShiftRequest(
        request_type="pickup",
        status="pending",
        requester_user_id=user.id,
        open_shift_post_id=post_id,
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
            "request_type": "pickup",
            "open_shift_post_id": post_id,
        },
    )
    return _hydrate(db, [request])[0]


def cancel_request(
    db: Session,
    *,
    user: User,
    request_id: int,
) -> dict:
    """Requester cancels their own non-terminal request. POST verb (not
    DELETE) so the row and its audit trail survive. Refuses on any
    terminal status (409) — a cancelled/approved/denied/expired request
    can't be cancelled again."""
    request = db.get(StaffShiftRequest, request_id)
    # Only the requester can cancel; hide existence from everyone else.
    if request is None or request.requester_user_id != user.id:
        raise StaffShiftRequestError("request_not_found", http_status=404)
    if request.status in _TERMINAL_STATUSES:
        raise StaffShiftRequestError("request_terminal", http_status=409)

    old_status = request.status
    request.status = "cancelled"
    request.updated_at = datetime.now(timezone.utc)
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
    return _hydrate(db, [request])[0]


def accept_request(db: Session, *, user: User, request_id: int) -> dict:
    """Candidate accepts a cover request (Gate 2: required before an admin
    can approve). The named candidate accepts a direct cover; an open
    cover (no candidate) can be accepted by any active sales staffer who
    isn't the requester, who then becomes the candidate."""
    request = db.get(StaffShiftRequest, request_id)
    if request is None:
        raise StaffShiftRequestError("request_not_found", http_status=404)
    if request.request_type not in ("cover", "swap"):
        # Drop has no accept step; pickup's claim is itself the accept.
        raise StaffShiftRequestError(
            "invalid_request_type", http_status=422
        )
    if request.status in _TERMINAL_STATUSES:
        raise StaffShiftRequestError("request_terminal", http_status=409)
    if request.status != "pending":
        raise StaffShiftRequestError("request_not_pending", http_status=409)

    if request.candidate_user_id is not None:
        # Direct cover: only the named candidate may accept. Hide from
        # everyone else (including the requester) to avoid leaking.
        if request.candidate_user_id != user.id:
            raise StaffShiftRequestError(
                "request_not_found", http_status=404
            )
    else:
        # Open cover: any active sales staffer except the requester.
        if user.id == request.requester_user_id or user.role != "sales" or (
            not user.is_active
        ):
            raise StaffShiftRequestError(
                "invalid_candidate", http_status=422
            )

    old_status = request.status
    now = datetime.now(timezone.utc)
    request.candidate_user_id = user.id
    request.accepted_by_user_id = user.id
    request.accepted_at = now
    request.status = "accepted_by_staff"
    request.updated_at = now
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=user.id,
        actor_kind="staff",
        action="accepted",
        old_values={"status": old_status},
        new_values={
            "status": "accepted_by_staff",
            "candidate_user_id": user.id,
        },
    )
    requester = db.get(User, request.requester_user_id)
    source = db.get(StaffScheduleEntry, request.source_entry_id)
    if requester is not None:
        if request.request_type == "swap":
            target = (
                db.get(StaffScheduleEntry, request.target_entry_id)
                if request.target_entry_id is not None
                else None
            )
            shift_request_notifications.notify_swap_accepted(
                db,
                request_id=request.id,
                requester=requester,
                candidate=user,
                source_entry=source,
                target_entry=target,
            )
        else:
            shift_request_notifications.notify_cover_accepted(
                db,
                request_id=request.id,
                requester=requester,
                candidate=user,
                source_entry=source,
            )
    return _hydrate(db, [request])[0]


def decline_request(db: Session, *, user: User, request_id: int) -> dict:
    """Named candidate / accepter declines a cover request. The request is
    cancelled (the requester can ask someone else)."""
    request = db.get(StaffShiftRequest, request_id)
    if request is None:
        raise StaffShiftRequestError("request_not_found", http_status=404)
    if request.status in _TERMINAL_STATUSES:
        raise StaffShiftRequestError("request_terminal", http_status=409)
    if user.id not in {
        request.candidate_user_id,
        request.accepted_by_user_id,
    }:
        raise StaffShiftRequestError("request_not_found", http_status=404)

    old_status = request.status
    now = datetime.now(timezone.utc)
    request.status = "cancelled"
    request.updated_at = now
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=user.id,
        actor_kind="staff",
        action="cancelled",
        old_values={"status": old_status},
        new_values={"status": "cancelled"},
        notes="declined_by_candidate",
    )
    requester = db.get(User, request.requester_user_id)
    source = db.get(StaffScheduleEntry, request.source_entry_id)
    if requester is not None:
        if request.request_type == "swap":
            shift_request_notifications.notify_swap_denied(
                db,
                request_id=request.id,
                actor_user_id=user.id,
                requester=requester,
                candidate=user,
                source_entry=source,
                declined=True,
            )
        else:
            shift_request_notifications.notify_cover_denied(
                db,
                request_id=request.id,
                actor_user_id=user.id,
                requester=requester,
                candidate=user,
                source_entry=source,
                declined=True,
            )
    return _hydrate(db, [request])[0]


def candidate_conflicts(
    db: Session, *, request_id: int
) -> list[dict] | None:
    """Preview the destination-user conflicts an admin would hit on
    approval. For cover: the candidate taking the source shift. For swap:
    each staffer taking the other's shift (tagged with `for_user_id`).
    None when not applicable."""
    request = db.get(StaffShiftRequest, request_id)
    if request is None:
        return None

    if request.request_type == "cover":
        if (
            request.candidate_user_id is None
            or request.source_entry_id is None
        ):
            return None
        source = db.get(StaffScheduleEntry, request.source_entry_id)
        if source is None:
            return None
        return staff_schedule.validate_staff_can_work_interval(
            db,
            user_id=request.candidate_user_id,
            starts_at_local=source.starts_at_local,
            ends_at_local=source.ends_at_local,
            exclude_entry_ids={source.id},
        )

    if request.request_type == "swap":
        if (
            request.candidate_user_id is None
            or request.source_entry_id is None
            or request.target_entry_id is None
        ):
            return None
        source = db.get(StaffScheduleEntry, request.source_entry_id)
        target = db.get(StaffScheduleEntry, request.target_entry_id)
        if source is None or target is None:
            return None
        exclude = {source.id, target.id}
        out: list[dict] = []
        # Requester moves into the target's slot.
        for c in staff_schedule.validate_staff_can_work_interval(
            db,
            user_id=request.requester_user_id,
            starts_at_local=target.starts_at_local,
            ends_at_local=target.ends_at_local,
            exclude_entry_ids=exclude,
        ):
            out.append({**c, "for_user_id": request.requester_user_id})
        # Candidate moves into the requester's slot.
        for c in staff_schedule.validate_staff_can_work_interval(
            db,
            user_id=request.candidate_user_id,
            starts_at_local=source.starts_at_local,
            ends_at_local=source.ends_at_local,
            exclude_entry_ids=exclude,
        ):
            out.append({**c, "for_user_id": request.candidate_user_id})
        return out

    return None


def decide_request(
    db: Session,
    *,
    actor_user_id: int,
    request_id: int,
    decision: Literal["approved", "denied"],
    decision_notes: str | None = None,
) -> dict:
    """Admin approves or denies a request. Approving a cover transfers the
    published entry to the accepted candidate (re-running conflict checks
    under a row lock); approving a drop retracts the entry to draft.
    Denying leaves the schedule untouched. Refuses any terminal request,
    and (for cover) requires the candidate to have accepted first."""
    if decision not in ("approved", "denied"):
        raise StaffShiftRequestError("invalid_decision", http_status=422)

    request = db.get(StaffShiftRequest, request_id)
    if request is None:
        raise StaffShiftRequestError("request_not_found", http_status=404)
    if request.status in _TERMINAL_STATUSES:
        raise StaffShiftRequestError("request_terminal", http_status=409)

    requester = db.get(User, request.requester_user_id)
    candidate = (
        db.get(User, request.candidate_user_id)
        if request.candidate_user_id is not None
        else None
    )
    source = (
        db.get(StaffScheduleEntry, request.source_entry_id)
        if request.source_entry_id is not None
        else None
    )
    cleaned_notes = (decision_notes or "").strip() or None
    old_status = request.status

    if decision == "approved":
        if request.request_type == "cover":
            if request.status != "accepted_by_staff":
                raise StaffShiftRequestError(
                    "request_not_accepted", http_status=409
                )
            if request.candidate_user_id is None:
                raise StaffShiftRequestError(
                    "invalid_candidate", http_status=422
                )
            try:
                staff_schedule.transfer_published_entry(
                    db,
                    entry_id=request.source_entry_id,
                    from_user_id=request.requester_user_id,
                    to_user_id=request.candidate_user_id,
                    actor_user_id=actor_user_id,
                    reason="shift_cover",
                    request_id=request.id,
                )
            except StaffScheduleError as exc:
                raise StaffShiftRequestError(
                    exc.code, http_status=exc.http_status, extra=exc.extra
                ) from exc
        elif request.request_type == "swap":
            if request.status != "accepted_by_staff":
                raise StaffShiftRequestError(
                    "request_not_accepted", http_status=409
                )
            if (
                request.candidate_user_id is None
                or request.target_entry_id is None
            ):
                raise StaffShiftRequestError(
                    "invalid_candidate", http_status=422
                )
            try:
                staff_schedule.swap_published_entries(
                    db,
                    entry_a_id=request.source_entry_id,
                    entry_b_id=request.target_entry_id,
                    user_a_id=request.requester_user_id,
                    user_b_id=request.candidate_user_id,
                    actor_user_id=actor_user_id,
                    request_id=request.id,
                )
            except StaffScheduleError as exc:
                raise StaffShiftRequestError(
                    exc.code, http_status=exc.http_status, extra=exc.extra
                ) from exc
        elif request.request_type == "drop":
            try:
                staff_schedule.retract_published_entry_to_draft(
                    db,
                    entry_id=request.source_entry_id,
                    expected_user_id=request.requester_user_id,
                    actor_user_id=actor_user_id,
                )
            except StaffScheduleError as exc:
                raise StaffShiftRequestError(
                    exc.code, http_status=exc.http_status, extra=exc.extra
                ) from exc
        elif request.request_type == "pickup":
            if request.open_shift_post_id is None:
                raise StaffShiftRequestError(
                    "invalid_request_type", http_status=422
                )
            # Lock the post and re-validate it's still open (close the
            # claim/approve race), then re-check the claimant can work it.
            try:
                post = open_shifts.lock_open_post(
                    db, request.open_shift_post_id
                )
            except OpenShiftPostError as exc:
                raise StaffShiftRequestError(
                    exc.code, http_status=exc.http_status, extra=exc.extra
                ) from exc
            conflicts = staff_schedule.validate_staff_can_work_interval(
                db,
                user_id=request.requester_user_id,
                starts_at_local=post.starts_at_local,
                ends_at_local=post.ends_at_local,
            )
            if conflicts:
                raise StaffShiftRequestError(
                    "candidate_conflict",
                    http_status=409,
                    extra={"conflicts": conflicts},
                )
            # Materialize the open post into a real published entry for
            # the claimant (this emits staff.shift_added to them).
            try:
                staff_schedule.create_entry(
                    db,
                    actor_user_id=actor_user_id,
                    user_id=request.requester_user_id,
                    business_date_=post.business_date,
                    starts_at_local=post.starts_at_local,
                    ends_at_local=post.ends_at_local,
                    late_grace_minutes=post.late_grace_minutes,
                    manager_notes=post.manager_notes,
                    source="manual",
                    publish=True,
                )
            except StaffScheduleError as exc:
                raise StaffShiftRequestError(
                    exc.code, http_status=exc.http_status, extra=exc.extra
                ) from exc
            open_shifts.mark_claimed(
                db,
                post=post,
                claimant_user_id=request.requester_user_id,
                request_id=request.id,
            )
            # Other staffers who claimed this post lose the race.
            siblings = (
                db.execute(
                    select(StaffShiftRequest)
                    .where(
                        StaffShiftRequest.open_shift_post_id == post.id
                    )
                    .where(StaffShiftRequest.id != request.id)
                    .where(
                        StaffShiftRequest.status.in_(
                            ["pending", "accepted_by_staff"]
                        )
                    )
                )
                .scalars()
                .all()
            )
            for sib in siblings:
                sib_old = sib.status
                sib.status = "expired"
                sib.updated_at = datetime.now(timezone.utc)
                _audit(
                    db,
                    request=sib,
                    actor_user_id=actor_user_id,
                    actor_kind="system",
                    action="expired",
                    old_values={"status": sib_old},
                    new_values={"status": "expired"},
                    notes="open_post_claimed_by_another",
                )

    now = datetime.now(timezone.utc)
    request.status = decision
    request.decided_by_user_id = actor_user_id
    request.decided_at = now
    request.decision_notes = cleaned_notes
    request.updated_at = now
    db.flush()
    _audit(
        db,
        request=request,
        actor_user_id=actor_user_id,
        actor_kind="owner",
        action=decision,
        old_values={"status": old_status},
        new_values={"status": decision, "decided_by_user_id": actor_user_id},
        notes=cleaned_notes,
    )

    if requester is not None:
        if request.request_type == "cover":
            if decision == "approved" and candidate is not None:
                shift_request_notifications.notify_cover_approved(
                    db,
                    request_id=request.id,
                    actor_user_id=actor_user_id,
                    requester=requester,
                    candidate=candidate,
                    source_entry=source,
                )
            elif decision == "denied":
                shift_request_notifications.notify_cover_denied(
                    db,
                    request_id=request.id,
                    actor_user_id=actor_user_id,
                    requester=requester,
                    candidate=candidate,
                    source_entry=source,
                    notes=cleaned_notes,
                )
        elif request.request_type == "swap":
            target = (
                db.get(StaffScheduleEntry, request.target_entry_id)
                if request.target_entry_id is not None
                else None
            )
            if decision == "approved" and candidate is not None:
                shift_request_notifications.notify_swap_approved(
                    db,
                    request_id=request.id,
                    actor_user_id=actor_user_id,
                    requester=requester,
                    candidate=candidate,
                    source_entry=source,
                    target_entry=target,
                )
            elif decision == "denied":
                shift_request_notifications.notify_swap_denied(
                    db,
                    request_id=request.id,
                    actor_user_id=actor_user_id,
                    requester=requester,
                    candidate=candidate,
                    source_entry=source,
                    notes=cleaned_notes,
                )
        elif request.request_type == "drop":
            shift_request_notifications.notify_drop_decided(
                db,
                request_id=request.id,
                actor_user_id=actor_user_id,
                requester=requester,
                source_entry=source,
                approved=(decision == "approved"),
                notes=cleaned_notes,
            )
        elif request.request_type == "pickup" and decision == "denied":
            # Approved pickups already notify the claimant via the
            # staff.shift_added event create_entry emits.
            shift_request_notifications.notify_pickup_denied(
                db,
                request_id=request.id,
                actor_user_id=actor_user_id,
                requester=requester,
                notes=cleaned_notes,
            )
    return _hydrate(db, [request])[0]


def _request_shift_started(
    db: Session, request: StaffShiftRequest, now: datetime
) -> bool:
    """Whether a still-open request can no longer be acted on because its
    underlying shift has started (or, for a pickup, its post is gone /
    no longer open). Used by the expiry cron."""
    if request.request_type == "pickup":
        if request.open_shift_post_id is None:
            return True
        post = db.get(OpenShiftPost, request.open_shift_post_id)
        if post is None or post.status != "open":
            return True
        return post.starts_at_local.astimezone(timezone.utc) <= now

    source = (
        db.get(StaffScheduleEntry, request.source_entry_id)
        if request.source_entry_id is not None
        else None
    )
    if source is None:
        return True
    if (
        source.actual_clock_in_punch_id is not None
        or source.actual_clock_out_punch_id is not None
        or source.attendance_status != "scheduled"
        or source.starts_at_local.astimezone(timezone.utc) <= now
    ):
        return True
    if request.request_type == "swap":
        target = (
            db.get(StaffScheduleEntry, request.target_entry_id)
            if request.target_entry_id is not None
            else None
        )
        if target is None:
            return True
        if (
            target.actual_clock_in_punch_id is not None
            or target.actual_clock_out_punch_id is not None
            or target.attendance_status != "scheduled"
            or target.starts_at_local.astimezone(timezone.utc) <= now
        ):
            return True
    return False


def expire_due(db: Session, *, now: datetime | None = None) -> dict:
    """Expire stale requests and open posts.

    Pending / accepted requests stay actionable until the underlying
    shift starts, because managers can still rescue them inside the
    staff-facing 12-hour cutoff. Open posts are different: staff cannot
    claim them once the cutoff is reached, so they leave the board at
    ``starts_at - _STAFF_REQUEST_CUTOFF``.

    Idempotent — terminal / expired rows are skipped. Caller commits.
    Returns ``{"scanned", "changed"}``.
    """
    now = now or datetime.now(timezone.utc)
    scanned = 0
    changed = 0

    open_requests = (
        db.execute(
            select(StaffShiftRequest).where(
                StaffShiftRequest.status.in_(
                    ["pending", "accepted_by_staff"]
                )
            )
        )
        .scalars()
        .all()
    )
    for request in open_requests:
        if not _request_shift_started(db, request, now):
            continue
        scanned += 1
        old_status = request.status
        request.status = "expired"
        request.updated_at = now
        _audit(
            db,
            request=request,
            actor_user_id=None,
            actor_kind="system",
            action="expired",
            old_values={"status": old_status},
            new_values={"status": "expired"},
            notes="shift_started",
        )
        changed += 1

    due_posts = (
        db.execute(
            select(OpenShiftPost)
            .where(OpenShiftPost.status == "open")
        )
        .scalars()
        .all()
    )
    for post in due_posts:
        cutoff = post.starts_at_local.astimezone(timezone.utc) - _STAFF_REQUEST_CUTOFF
        if cutoff > now:
            continue
        scanned += 1
        post.status = "expired"
        post.updated_at = now
        changed += 1

    return {"scanned": scanned, "changed": changed}


__all__ = [
    "StaffShiftRequestError",
    "accept_request",
    "cancel_request",
    "candidate_conflicts",
    "claim_open_shift",
    "create_request",
    "decide_request",
    "decline_request",
    "expire_due",
    "get_admin",
    "get_for_user",
    "list_admin",
    "list_for_user",
]
