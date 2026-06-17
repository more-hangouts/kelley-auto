"""Admin record-dependency preview endpoint.

Phase D1 of ``docs/CRM_RECORD_DELETION_PLAN.md``. Single GET endpoint
that powers the future archive/restore confirm modal. Read-only and
admin-gated. No archive or restore behavior is exposed here — that
arrives in D3.

Endpoint:

  - ``GET /api/admin/dependencies/{entity_type}/{entity_id}`` returns a
    :class:`DependencyReportResponse` with active/deleted counts per
    inbound relationship, block reasons, and short sample titles.
    ``entity_type`` must be one of ``contact``, ``event``,
    ``event_participant``, ``special_order``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import record_dependencies
from services.record_dependencies import (
    RecordNotFoundError,
    UnsupportedEntityTypeError,
)

router = APIRouter()


class DependencyCountResponse(BaseModel):
    kind: str
    active_count: int
    deleted_count: int
    blocking: bool


class DependencyReportResponse(BaseModel):
    entity_type: str
    entity_id: int
    is_currently_deleted: bool
    can_archive: bool
    can_restore: bool
    block_reasons: list[str]
    dependencies: list[DependencyCountResponse]
    sample_titles: dict[str, list[str]]


@router.get(
    "/{entity_type}/{entity_id}",
    response_model=DependencyReportResponse,
)
def get_dependencies(
    entity_type: str,
    entity_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> DependencyReportResponse:
    try:
        report = record_dependencies.get_record_dependencies(
            db, entity_type=entity_type, entity_id=entity_id
        )
    except UnsupportedEntityTypeError as exc:
        raise HTTPException(
            status_code=400, detail=f"unsupported entity_type: {exc.entity_type!r}"
        ) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"{exc.entity_type} {exc.entity_id} not found",
        ) from exc

    return DependencyReportResponse(
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        is_currently_deleted=report.is_currently_deleted,
        can_archive=report.can_archive,
        can_restore=report.can_restore,
        block_reasons=list(report.block_reasons),
        dependencies=[
            DependencyCountResponse(
                kind=d.kind,
                active_count=d.active_count,
                deleted_count=d.deleted_count,
                blocking=d.blocking,
            )
            for d in report.dependencies
        ],
        sample_titles=dict(report.sample_titles),
    )
