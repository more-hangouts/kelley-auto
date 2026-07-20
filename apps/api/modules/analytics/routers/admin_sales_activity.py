"""Admin view of commission-mode sales activity (Phase 14.4).

Read-only reporting over ``sales_activity_events``. Answers the owner's
question — "are my reps actually reviewing leads and contacts?" — without
mixing into payroll/attendance reporting.

  GET /api/admin/sales-activity/summary?range=today|yesterday|week&user_id=
      Per-rep counts (leads/appointments/contacts viewed, searches) + last
      seen, most-recently-active first.

  GET /api/admin/sales-activity/rep/{user_id}/recent?limit=&before_id=
      Keyset-paginated recent activity rows for one rep, for drilldown.

Admin scope only; sales tokens get a 403. Ranges are computed in
business-local time (``services/business_time.py``) so "today" is the
dealership's day, not raw UTC.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import business_time
from modules.analytics.services import sales_activity

router = APIRouter()

RangeKey = Literal["today", "yesterday", "week"]


def _resolve_range(
    range_key: RangeKey | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[datetime, datetime | None]:
    """Return ``(since, until)`` in business-local aware datetimes.

    Explicit ``since``/``until`` win (custom range). Otherwise a named range
    is anchored to local midnight so day boundaries match the shop's clock.
    """
    if since is not None:
        return since, until

    tz = business_time.shop_tz()
    today = business_time.business_date()  # local date
    start_today = datetime.combine(today, time.min, tzinfo=tz)

    if range_key == "yesterday":
        return start_today - timedelta(days=1), start_today
    if range_key == "week":
        # Trailing 7 days including today.
        return start_today - timedelta(days=6), None
    # Default: today.
    return start_today, None


class RepSummaryModel(BaseModel):
    actor_user_id: int
    full_name: str | None
    username: str | None
    leads_viewed: int
    appointments_viewed: int
    contacts_viewed: int
    searches: int
    last_activity_at: datetime | None


class SummaryResponse(BaseModel):
    since: datetime
    until: datetime | None
    reps: list[RepSummaryModel]


@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    range: Annotated[RangeKey, Query(description="today|yesterday|week")] = "today",
    user_id: Annotated[int | None, Query(description="Filter to one rep")] = None,
    since: Annotated[
        datetime | None, Query(description="Custom-range start (overrides range)")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Custom-range end (exclusive)")
    ] = None,
) -> SummaryResponse:
    resolved_since, resolved_until = _resolve_range(range, since, until)
    summaries = sales_activity.summary_by_rep(
        db,
        since=resolved_since,
        until=resolved_until,
        actor_user_id=user_id,
    )
    return SummaryResponse(
        since=resolved_since,
        until=resolved_until,
        reps=[
            RepSummaryModel(
                actor_user_id=s.actor_user_id,
                full_name=s.full_name,
                username=s.username,
                leads_viewed=s.leads_viewed,
                appointments_viewed=s.appointments_viewed,
                contacts_viewed=s.contacts_viewed,
                searches=s.searches,
                last_activity_at=s.last_activity_at,
            )
            for s in summaries
        ],
    )


class ActivityRowModel(BaseModel):
    id: int
    actor_user_id: int
    activity_type: str
    subject_kind: str | None
    subject_id: int | None
    route: str | None
    source: str | None
    metadata: dict
    created_at: datetime


class RecentResponse(BaseModel):
    actor_user_id: int
    rows: list[ActivityRowModel]
    next_before_id: int | None


@router.get("/rep/{user_id}/recent", response_model=RecentResponse)
def get_rep_recent(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query()] = None,
) -> RecentResponse:
    rows = sales_activity.recent_for_rep(
        db, actor_user_id=user_id, limit=limit, before_id=before_id
    )
    return RecentResponse(
        actor_user_id=user_id,
        rows=[
            ActivityRowModel(
                id=r.id,
                actor_user_id=r.actor_user_id,
                activity_type=r.activity_type,
                subject_kind=r.subject_kind,
                subject_id=r.subject_id,
                route=r.route,
                source=r.source,
                metadata=r.metadata,
                created_at=r.created_at,
            )
            for r in rows
        ],
        # Keyset cursor: the smallest id on this page. Null when the page
        # wasn't full (no more rows).
        next_before_id=rows[-1].id if len(rows) == limit else None,
    )
