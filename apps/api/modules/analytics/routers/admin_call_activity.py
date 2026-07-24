"""Manager/admin view of native-dialer call activity (Phase 7).

Read-only reporting over ``contact_call_attempts`` — "how many calls did each
rep make today, and how did they land?" Business-local dates so "today" is the
dealership's day, not raw UTC. Admin scope only; sales tokens get 403 (a rep
sees the floor's rollup only through the manager view, consistent with
sales-activity reporting).

  GET /api/admin/call-activity/summary?date=YYYY-MM-DD
      Per-rep counts (initiated/connected/voicemail/no_answer/pending) for the
      day, plus a floor-wide calls_today total.

  GET /api/admin/call-activity/recent?limit=
      Most-recent attempts across the floor with contact + rep + outcome, for
      the recent-activity list.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_sales_scope
from database.connection import get_db
from database.models import User
from modules.analytics.services import call_attempts as svc

router = APIRouter()

# Sales-scoped router (mounted at /api/sales/call-activity): a rep sees only
# their OWN counts. Admin reporting stays on the admin router above.
sales_router = APIRouter()


@sales_router.get("/today")
def my_calls_today(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_sales_scope)],
) -> dict:
    """The authenticated rep's own call count for the business-local day."""
    return {"calls_today": svc.calls_today_count(db, user_id=user.id)}


@router.get("/summary")
def call_summary(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    date: str | None = Query(default=None, description="YYYY-MM-DD, business-local"),
) -> dict:
    day: date_type | None = None
    if date:
        try:
            day = date_type.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_date") from exc

    resolved_day, rows = svc.summary_by_rep(db, day=day)
    total = sum(r["initiated"] for r in rows)
    return {
        "date": resolved_day.isoformat(),
        "calls_today": total,
        "reps": rows,
    }


@router.get("/recent")
def recent(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    limit: int = Query(default=25, ge=1, le=200),
) -> dict:
    return {"recent": svc.recent_calls(db, limit=limit)}
