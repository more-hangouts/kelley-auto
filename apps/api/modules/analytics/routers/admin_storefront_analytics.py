"""Admin storefront-analytics dashboard (Sprint 3 of the analytics plan).

Read-only aggregate reporting over the first-party event stream:

  GET /api/admin/storefront-analytics/summary?days=30
      Funnel counts, traffic/leads/revenue by channel (first-touch),
      shop-local daily series, and most-viewed vehicles.

The heavy lifting is ``storefront_analytics_service.summary()`` — the whole
dashboard is GROUP BY over one event stream. Admin scope only; sales tokens
get a 403. No PII leaves here: sources, counts, and vehicle labels only.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from modules.analytics.services import storefront_analytics_service

router = APIRouter()


@router.get("/summary")
def get_summary(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    return storefront_analytics_service.summary(db, days=days)
