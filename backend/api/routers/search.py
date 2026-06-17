"""Global search API.

Single endpoint behind the command-palette UI. Returns a discriminated-
union list of results across requested entity types. Phase 1 covers
events + contacts; future phases extend to invoices, quotes, and
special orders without changing this router's shape.

Auth: admin-only via the local `require_admin` dependency. The plan
locks this surface as staff/admin and there is no per-row scoping
because Bellas is single-tenant; the gate exists so a non-admin user
account on the system cannot enumerate the customer base by typing
fragments.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import search_service
from services.search_service import (
    ALLOWED_TYPES,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_QUERY_LENGTH,
    SearchServiceError,
)

router = APIRouter()


class SearchResultModel(BaseModel):
    type: str
    id: int
    label: str
    sublabel: str
    score: float
    route: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultModel]


@router.get("", response_model=SearchResponse)
def get_search(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    q: Annotated[str, Query(min_length=MIN_QUERY_LENGTH, max_length=200)],
    types: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated entity types. Defaults to all enabled "
                "types. Unknown types return 400."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_LIMIT, description="Per-type cap."),
    ] = DEFAULT_LIMIT,
) -> SearchResponse:
    selected: frozenset[str] | None = None
    if types is not None:
        parsed = {t.strip() for t in types.split(",") if t.strip()}
        if not parsed:
            raise HTTPException(status_code=400, detail="empty_types")
        unknown = parsed - ALLOWED_TYPES
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_types", "unknown": sorted(unknown)},
            )
        selected = frozenset(parsed)

    try:
        results = search_service.search(
            db, q=q, types=selected, limit=limit
        )
    except SearchServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc

    return SearchResponse(
        query=q,
        results=[
            SearchResultModel(
                type=r.type,
                id=r.id,
                label=r.label,
                sublabel=r.sublabel,
                score=r.score,
                route=r.route,
            )
            for r in results
        ],
    )
