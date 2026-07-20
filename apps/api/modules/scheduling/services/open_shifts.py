"""Open-shift post service (Scheduling Phase 3).

CRUD for `open_shift_posts` — shifts a manager posts without an assignee
for staff to claim (the pickup board). Claiming and approval live in
`services.staff_shift_requests` (a pickup is a `staff_shift_requests` row
of type 'pickup'); this module owns only the post lifecycle and the
sanitized board read.

Stable error codes (mapped to HTTP by the routers):

    invalid_date_range      422  end <= start
    business_date_mismatch  422  business_date != local date of start
    late_grace_out_of_range 422  grace not in [0, 120]
    post_not_found          404
    post_not_open           409  post already claimed/cancelled/expired
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import OpenShiftPost, User
from services.business_time import shop_tz, to_business_local


class OpenShiftPostError(Exception):
    def __init__(
        self, code: str, *, http_status: int = 400, extra: dict | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.extra = dict(extra or {})


def _ensure_aware(dt: datetime, *, field: str) -> datetime:
    if dt.tzinfo is None:
        raise OpenShiftPostError(f"naive_{field}", http_status=422)
    return dt


def _serialize_admin(post: OpenShiftPost, *, users=None) -> dict:
    def _iso(dt: datetime | None) -> str | None:
        return dt.astimezone(timezone.utc).isoformat() if dt else None

    name = None
    if users is not None and post.claimed_by_user_id is not None:
        u = users.get(post.claimed_by_user_id)
        name = u.full_name if u else None
    return {
        "id": post.id,
        "business_date": post.business_date.isoformat(),
        "starts_at_local": to_business_local(post.starts_at_local).isoformat(),
        "ends_at_local": to_business_local(post.ends_at_local).isoformat(),
        "late_grace_minutes": post.late_grace_minutes,
        "source": post.source,
        "manager_notes": post.manager_notes,
        "status": post.status,
        "created_by_user_id": post.created_by_user_id,
        "claimed_by_user_id": post.claimed_by_user_id,
        "claimed_by_full_name": name,
        "claimed_request_id": post.claimed_request_id,
        "created_at": _iso(post.created_at),
        "updated_at": _iso(post.updated_at),
    }


# Allowlist for the staff-facing board. Open posts have no assignee, so
# there is nothing personal here; we still drop manager/audit plumbing
# (created_by, claim fields, source, timestamps) and surface the note as
# display copy. A future field can't widen this by accident.
def _serialize_public(post: OpenShiftPost) -> dict:
    return {
        "id": post.id,
        "business_date": post.business_date.isoformat(),
        "starts_at_local": to_business_local(post.starts_at_local).isoformat(),
        "ends_at_local": to_business_local(post.ends_at_local).isoformat(),
        "late_grace_minutes": post.late_grace_minutes,
        "note": post.manager_notes,
    }


def create_post(
    db: Session,
    *,
    actor_user_id: int,
    business_date_: date,
    starts_at_local: datetime,
    ends_at_local: datetime,
    late_grace_minutes: int | None = None,
    manager_notes: str | None = None,
) -> dict:
    starts = _ensure_aware(starts_at_local, field="starts_at_local")
    ends = _ensure_aware(ends_at_local, field="ends_at_local")
    if ends <= starts:
        raise OpenShiftPostError("invalid_date_range", http_status=422)
    if business_date_ != starts.astimezone(shop_tz()).date():
        raise OpenShiftPostError(
            "business_date_mismatch",
            http_status=422,
            extra={"expected": starts.astimezone(shop_tz()).date().isoformat()},
        )
    grace = 30 if late_grace_minutes is None else int(late_grace_minutes)
    if not (0 <= grace <= 120):
        raise OpenShiftPostError("late_grace_out_of_range", http_status=422)

    post = OpenShiftPost(
        business_date=business_date_,
        starts_at_local=starts,
        ends_at_local=ends,
        late_grace_minutes=grace,
        source="manual",
        manager_notes=(manager_notes or "").strip() or None,
        status="open",
        created_by_user_id=actor_user_id,
    )
    db.add(post)
    db.flush()
    return _serialize_admin(post)


def cancel_post(db: Session, *, post_id: int) -> dict:
    post = db.get(OpenShiftPost, post_id)
    if post is None:
        raise OpenShiftPostError("post_not_found", http_status=404)
    if post.status != "open":
        raise OpenShiftPostError("post_not_open", http_status=409)
    post.status = "cancelled"
    post.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _serialize_admin(post)


def get_post(db: Session, post_id: int) -> OpenShiftPost | None:
    return db.get(OpenShiftPost, post_id)


def lock_open_post(db: Session, post_id: int) -> OpenShiftPost:
    """SELECT ... FOR UPDATE on a post, requiring it still be open. Used by
    the pickup-approval path to close the claim/approve race."""
    post = (
        db.execute(
            select(OpenShiftPost)
            .where(OpenShiftPost.id == post_id)
            .with_for_update()
        )
        .scalars()
        .first()
    )
    if post is None:
        raise OpenShiftPostError("post_not_found", http_status=404)
    if post.status != "open":
        raise OpenShiftPostError("post_not_open", http_status=409)
    return post


def mark_claimed(
    db: Session,
    *,
    post: OpenShiftPost,
    claimant_user_id: int,
    request_id: int,
) -> None:
    post.status = "claimed"
    post.claimed_by_user_id = claimant_user_id
    post.claimed_request_id = request_id
    post.updated_at = datetime.now(timezone.utc)
    db.flush()


def list_admin(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    statuses: Iterable[str] | None = None,
) -> list[dict]:
    if to_date < from_date:
        raise OpenShiftPostError("invalid_date_range", http_status=422)
    stmt = (
        select(OpenShiftPost)
        .where(OpenShiftPost.business_date >= from_date)
        .where(OpenShiftPost.business_date <= to_date)
        .order_by(
            OpenShiftPost.business_date,
            OpenShiftPost.starts_at_local,
            OpenShiftPost.id,
        )
    )
    if statuses:
        stmt = stmt.where(OpenShiftPost.status.in_(list(statuses)))
    rows = db.execute(stmt).scalars().all()
    claimer_ids = {
        r.claimed_by_user_id for r in rows if r.claimed_by_user_id is not None
    }
    users = {}
    if claimer_ids:
        users = {
            u.id: u
            for u in db.execute(
                select(User).where(User.id.in_(list(claimer_ids)))
            )
            .scalars()
            .all()
        }
    return [_serialize_admin(r, users=users) for r in rows]


def list_open_for_sales(
    db: Session, *, from_date: date, to_date: date
) -> list[dict]:
    """The staff-facing board: only `open` posts, sanitized."""
    if to_date < from_date:
        raise OpenShiftPostError("invalid_date_range", http_status=422)
    rows = (
        db.execute(
            select(OpenShiftPost)
            .where(OpenShiftPost.status == "open")
            .where(OpenShiftPost.business_date >= from_date)
            .where(OpenShiftPost.business_date <= to_date)
            .order_by(
                OpenShiftPost.business_date,
                OpenShiftPost.starts_at_local,
                OpenShiftPost.id,
            )
        )
        .scalars()
        .all()
    )
    return [_serialize_public(r) for r in rows]


__all__ = [
    "OpenShiftPostError",
    "cancel_post",
    "create_post",
    "get_post",
    "list_admin",
    "list_open_for_sales",
    "lock_open_post",
    "mark_claimed",
]
