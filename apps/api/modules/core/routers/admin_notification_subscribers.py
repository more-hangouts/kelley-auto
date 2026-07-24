"""Admin router for the notification subscriber registry — the "who gets
what" surface (Omnichannel Inbox Plan Part 1; migration 093).

Owner-side CRUD under ``/api/admin/notification-subscribers``. Lets an admin
add/remove people — including external, login-less email recipients — and
toggle which event kinds each one receives. Thin per the repo convention:
validation + auth + HTTP shapes here, all logic in
``services.notification_subscriber_service``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from modules.core.services import notification_subscriber_service as svc
from modules.core.services.notification_subscriber_service import SubscriberError

router = APIRouter()


def _raise(exc: SubscriberError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


class SubscriberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    user_id: int | None = None
    phone_e164: str | None = Field(default=None, max_length=20)


class SubscriptionToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=128)
    enabled: bool


class SubscriptionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions: list[SubscriptionToggle] = Field(min_length=1)


class SubscriberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


@router.get("")
def list_subscribers(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    return {
        "subscribers": svc.list_subscribers(db),
        "catalog": svc.catalog(),
    }


@router.post("", status_code=201)
def create_subscriber(
    payload: SubscriberCreate,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = svc.create_subscriber(
            db,
            display_name=payload.display_name,
            email=payload.email,
            user_id=payload.user_id,
            phone_e164=payload.phone_e164,
        )
    except SubscriberError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.put("/{subscriber_id}/subscriptions")
def update_subscriptions(
    subscriber_id: int,
    payload: SubscriptionsUpdate,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = svc.update_subscriptions(
            db,
            subscriber_id,
            [(t.kind, t.enabled) for t in payload.subscriptions],
        )
    except SubscriberError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.patch("/{subscriber_id}")
def patch_subscriber(
    subscriber_id: int,
    payload: SubscriberPatch,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = svc.set_active(
            db, subscriber_id, is_active=payload.is_active
        )
    except SubscriberError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.delete("/{subscriber_id}", status_code=204)
def delete_subscriber(
    subscriber_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        svc.delete_subscriber(db, subscriber_id)
    except SubscriberError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=204)
