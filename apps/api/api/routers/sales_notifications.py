"""Sales-portal notification preferences (B2.5).

Two endpoints under ``/api/sales/me/notifications``, both gated on
``require_sales_scope``:

    GET  /preferences   — list every event kind the user can toggle
                          with its current effective state.
    PUT  /preferences   — partial upsert of (kind, enabled) pairs.

A staff user can opt out of digests and opt INTO event kinds outside
their role's default bundle. Intrinsic-only kinds ("your shift was
edited") are NOT exposed here — see
``services/notification_preferences_service.py`` for the reasoning.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import notification_preferences_service as prefs_service

router = APIRouter()


class PreferenceRow(BaseModel):
    event_kind: str
    enabled: bool
    source: str  # 'role_default' | 'override'
    label: str
    category: str
    description: str


class PreferencesResponse(BaseModel):
    preferences: list[PreferenceRow]


class PreferenceUpdate(BaseModel):
    event_kind: str = Field(min_length=1, max_length=120)
    enabled: bool


class PreferenceUpdatesRequest(BaseModel):
    updates: list[PreferenceUpdate] = Field(default_factory=list)


@router.get("/preferences", response_model=PreferencesResponse)
def list_preferences(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_sales_scope)],
) -> PreferencesResponse:
    views = prefs_service.get_effective_preferences(db, user)
    return PreferencesResponse(
        preferences=[
            PreferenceRow(
                event_kind=v.event_kind,
                enabled=v.enabled,
                source=v.source,
                label=v.label,
                category=v.category,
                description=v.description,
            )
            for v in views
        ]
    )


@router.put("/preferences", response_model=PreferencesResponse)
def update_preferences(
    payload: PreferenceUpdatesRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_sales_scope)],
) -> PreferencesResponse:
    try:
        prefs_service.upsert_preferences(
            db,
            user,
            updates=[(u.event_kind, u.enabled) for u in payload.updates],
        )
        db.commit()
    except prefs_service.PreferenceError as exc:
        db.rollback()
        status = (
            422 if exc.code in ("kind_not_configurable", "kind_not_in_catalog") else 400
        )
        raise HTTPException(
            status_code=status, detail={"code": exc.code, "message": str(exc)}
        ) from exc

    # Re-read so the response carries the post-write effective state —
    # callers can render the new toggle positions without a second GET.
    views = prefs_service.get_effective_preferences(db, user)
    return PreferencesResponse(
        preferences=[
            PreferenceRow(
                event_kind=v.event_kind,
                enabled=v.enabled,
                source=v.source,
                label=v.label,
                category=v.category,
                description=v.description,
            )
            for v in views
        ]
    )
