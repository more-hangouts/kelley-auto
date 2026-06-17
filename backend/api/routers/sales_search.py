"""Sales-portal lead search endpoint.

`GET /api/sales/search/leads?q=&limit=` — drives the dashboard search
box. Sales-scope only; admin tokens are rejected.

Deliberately a separate router from `api/routers/search.py`. The admin
global search composes invoice and quote results that include monetary
sublabels ("Balance: $1,200"); the sales portal must never surface
those. A flag on the admin path would be fragile (one careless field
addition leaks money values onto the floor); a parallel surface with
its own service and response shape is the safer cut.

No attendance gate on read: a punched-out stylist can still search to
prep for their shift. Mutations (walk-in create, assignment) live on
other routers and stay behind `require_floor_access`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import sales_search_service
from services.sales_search_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_QUERY_LENGTH,
)

router = APIRouter()


class SalesSearchResultModel(BaseModel):
    type: str
    id: int
    label: str
    sublabel: str
    contact_id: int | None
    assigned_user_id: int | None
    route: str


class SalesSearchResponse(BaseModel):
    query: str
    results: list[SalesSearchResultModel]


@router.get("/leads", response_model=SalesSearchResponse)
def search_leads(
    db: Annotated[Session, Depends(get_db)],
    _sales: Annotated[User, Depends(require_sales_scope)],
    q: Annotated[str, Query(min_length=MIN_QUERY_LENGTH, max_length=200)],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_LIMIT, description="Per-type cap."),
    ] = DEFAULT_LIMIT,
) -> SalesSearchResponse:
    results = sales_search_service.search_leads(db, q=q, limit=limit)
    return SalesSearchResponse(
        query=q,
        results=[
            SalesSearchResultModel(
                type=r.type,
                id=r.id,
                label=r.label,
                sublabel=r.sublabel,
                contact_id=r.contact_id,
                assigned_user_id=r.assigned_user_id,
                route=r.route,
            )
            for r in results
        ],
    )
