"""Tried-on log endpoints (Phase 4 of the sales portal).

Routes:

  GET    /api/sales/appointments/{appointment_id}/tried-on
  POST   /api/sales/appointments/{appointment_id}/tried-on
  PATCH  /api/sales/tried-on/{tried_on_id}
  DELETE /api/sales/tried-on/{tried_on_id}

All require `require_sales_scope`. Add/patch/delete require the
appointment to have a linked CRM event; if not, the API returns
409 with `detail='event_required'` and the frontend renders a
"Mark arrived first" guide that opens the Phase 3 Arrived modal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import User
from services import sales_tried_on
from services.attendance_gate import require_floor_access
from services.sales_tried_on import TriedOnError

router = APIRouter()


_UNSET: Any = object()  # router-level sentinel for omitted PATCH fields


class TriedOnCatalogSummary(BaseModel):
    id: int
    public_code: str
    color: str
    category: str
    product_title: str | None
    house_name: str | None
    image_urls: list[Any] = []


class TriedOnItem(BaseModel):
    id: int
    appointment_id: int
    catalog_item_id: int
    size_label: str | None
    liked: bool | None
    notes: str | None
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    catalog_item: TriedOnCatalogSummary | None


class TriedOnList(BaseModel):
    appointment_id: int
    has_event: bool
    items: list[TriedOnItem]


class TriedOnAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_item_id: int
    size_label: str | None = Field(default=None, max_length=50)
    liked: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TriedOnPatchRequest(BaseModel):
    """Sentinel-aware patch. Use `model_fields_set` at call sites to
    distinguish "not in payload" from "explicit null"."""

    model_config = ConfigDict(extra="forbid")

    size_label: Optional[str] = Field(default=None, max_length=50)
    liked: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


def _raise_for(exc: TriedOnError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc


def _list_response(
    db: Session, *, appointment_id: int, items: list[dict]
) -> TriedOnList:
    from database.models import Appointment

    appt = db.get(Appointment, appointment_id)
    has_event = bool(appt and appt.crm_event_id)
    return TriedOnList(
        appointment_id=appointment_id,
        has_event=has_event,
        items=[TriedOnItem(**i) for i in items],
    )


@router.get(
    "/appointments/{appointment_id}/tried-on",
    response_model=TriedOnList,
)
def list_tried_on(
    appointment_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_sales_scope)],
) -> TriedOnList:
    """Read access does not require an event; the response carries
    `has_event` so the frontend can show the "Mark arrived first"
    guide alongside an empty list when the appointment hasn't been
    promoted yet."""
    try:
        items = sales_tried_on.list_for_appointment(
            db, appointment_id=appointment_id
        )
    except TriedOnError as exc:
        _raise_for(exc)
    return _list_response(db, appointment_id=appointment_id, items=items)


@router.post(
    "/appointments/{appointment_id}/tried-on",
    response_model=TriedOnItem,
    status_code=201,
)
def add_tried_on(
    appointment_id: int,
    payload: TriedOnAddRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> TriedOnItem:
    try:
        result = sales_tried_on.add_tried_on(
            db,
            appointment_id=appointment_id,
            catalog_item_id=payload.catalog_item_id,
            actor_user_id=current_user.id,
            size_label=payload.size_label,
            liked=payload.liked,
            notes=payload.notes,
        )
    except TriedOnError as exc:
        db.rollback()
        _raise_for(exc)
    db.commit()
    return TriedOnItem(**result)


@router.patch("/tried-on/{tried_on_id}", response_model=TriedOnItem)
def patch_tried_on(
    tried_on_id: int,
    payload: TriedOnPatchRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> TriedOnItem:
    fields_set = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "size_label" in fields_set:
        kwargs["size_label"] = payload.size_label
    if "liked" in fields_set:
        kwargs["liked"] = payload.liked
    if "notes" in fields_set:
        kwargs["notes"] = payload.notes

    try:
        result = sales_tried_on.update_tried_on(
            db,
            tried_on_id=tried_on_id,
            actor_user_id=current_user.id,
            **kwargs,
        )
    except TriedOnError as exc:
        db.rollback()
        _raise_for(exc)
    db.commit()
    return TriedOnItem(**result)


@router.delete(
    "/tried-on/{tried_on_id}",
    status_code=204,
    response_class=Response,
)
def delete_tried_on(
    tried_on_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_floor_access("sales"))],
) -> Response:
    try:
        sales_tried_on.remove_tried_on(
            db, tried_on_id=tried_on_id, actor_user_id=current_user.id
        )
    except TriedOnError as exc:
        db.rollback()
        _raise_for(exc)
    db.commit()
    return Response(status_code=204)
