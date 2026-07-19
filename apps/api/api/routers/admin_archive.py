"""Admin archive / restore endpoints for the four CRM-core targets.

Phase D3-B of ``docs/CRM_RECORD_DELETION_PLAN.md``. Thin wrappers over
the service helpers added in D3-A; each route maps domain error codes
to HTTP statuses and validates the request body's ``reason`` enum.

Routes (all under ``/api/admin``):

  - ``POST /contacts/{contact_id}/archive``
  - ``POST /contacts/{contact_id}/restore``
  - ``POST /events/{event_id}/archive``
  - ``POST /events/{event_id}/restore``
  - ``POST /events/{event_id}/participants/{participant_id}/archive``
  - ``POST /events/{event_id}/participants/{participant_id}/restore``
  - ``POST /events/{event_id}/special-orders/{special_order_id}/archive``
  - ``POST /events/{event_id}/special-orders/{special_order_id}/restore``

Nested routes verify the ``event_id`` in the path matches the parent
``event_id`` on the child row before delegating — defends against a
malicious or buggy client substituting a sibling event in the URL.

Auth: ``require_admin_scope`` (no sales scope). Archive is an admin
action even when it touches a sales-facing row like a special order;
opening it to sales would let any floor stylist sweep records out of
the pipeline.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import (
    ActivityLog,
    CatalogItem,
    Contact,
    Event,
    EventParticipant,
    SpecialOrder,
    User,
)
from services import (
    activity_log as activity_log_service,
    contact_service,
    event_participants as event_participants_service,
    event_service,
    record_dependencies,
    special_order_service,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class ArchiveRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class ArchiveResponse(BaseModel):
    entity_type: str
    entity_id: int
    deleted_at: str | None  # isoformat; None on restore
    activity_logged: bool


# ---------------------------------------------------------------------------
# Error mappers
# ---------------------------------------------------------------------------


def _map_contact_error(exc: contact_service.ContactServiceError) -> HTTPException:
    status_map = {
        "contact_not_found": 404,
        "archive_blocked": 409,
        "restore_phone_collision": 409,
    }
    status = status_map.get(exc.code or "", 400)
    detail: dict = {"code": exc.code or "contact_error", "message": str(exc)}
    if exc.conflict_contact_id is not None:
        detail["conflict_contact_id"] = exc.conflict_contact_id
    return HTTPException(status_code=status, detail=detail)


def _map_event_error(exc: event_service.EventServiceError) -> HTTPException:
    status_map = {
        "event_not_found": 404,
        "archive_blocked": 409,
        "parent_archived": 409,
    }
    status = status_map.get(exc.code or "", 400)
    return HTTPException(
        status_code=status,
        detail={"code": exc.code or "event_error", "message": str(exc)},
    )


def _map_participant_error(
    exc: event_participants_service.EventParticipantError,
) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.code},
    )


def _map_special_order_error(
    exc: special_order_service.SpecialOrderError,
) -> HTTPException:
    status_map = {
        "special_order_not_found": 404,
        "archive_blocked": 409,
        "parent_archived": 409,
    }
    status = status_map.get(exc.code or "", 400)
    return HTTPException(
        status_code=status,
        detail={"code": exc.code or "special_order_error", "message": str(exc)},
    )


def _map_reason_error(
    exc: record_dependencies.ArchiveReasonError,
) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "invalid_reason", "message": str(exc)},
    )


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/archive",
    response_model=ArchiveResponse,
)
def archive_contact_route(
    contact_id: int,
    payload: ArchiveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    try:
        contact = contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=int(current_user.id),
            reason=payload.reason,
            note=payload.note,
        )
    except record_dependencies.ArchiveReasonError as exc:
        raise _map_reason_error(exc) from exc
    except contact_service.ContactServiceError as exc:
        raise _map_contact_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="contact",
        entity_id=contact_id,
        deleted_at=contact.deleted_at.isoformat() if contact.deleted_at else None,
        activity_logged=_has_event_anchor_for_contact(db, contact_id),
    )


@router.post(
    "/contacts/{contact_id}/restore",
    response_model=ArchiveResponse,
)
def restore_contact_route(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    try:
        contact = contact_service.restore_contact(
            db,
            contact_id=contact_id,
            actor_user_id=int(current_user.id),
        )
    except contact_service.ContactServiceError as exc:
        raise _map_contact_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="contact",
        entity_id=contact_id,
        deleted_at=None,
        activity_logged=_has_event_anchor_for_contact(db, contact_id),
    )


def _has_event_anchor_for_contact(db: Session, contact_id: int) -> bool:
    """True when the contact has at least one event (live or deleted),
    which means the archive/restore helper wrote an activity row."""
    return (
        contact_service._most_recent_event_id(db, contact_id) is not None
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.post(
    "/events/{event_id}/archive",
    response_model=ArchiveResponse,
)
def archive_event_route(
    event_id: int,
    payload: ArchiveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    try:
        event = event_service.archive_event(
            db,
            event_id=event_id,
            actor_user_id=int(current_user.id),
            reason=payload.reason,
            note=payload.note,
        )
    except record_dependencies.ArchiveReasonError as exc:
        raise _map_reason_error(exc) from exc
    except event_service.EventServiceError as exc:
        raise _map_event_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="event",
        entity_id=event_id,
        deleted_at=event.deleted_at.isoformat() if event.deleted_at else None,
        activity_logged=True,
    )


@router.post(
    "/events/{event_id}/restore",
    response_model=ArchiveResponse,
)
def restore_event_route(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    try:
        event_service.restore_event(
            db,
            event_id=event_id,
            actor_user_id=int(current_user.id),
        )
    except event_service.EventServiceError as exc:
        raise _map_event_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="event",
        entity_id=event_id,
        deleted_at=None,
        activity_logged=True,
    )


# ---------------------------------------------------------------------------
# Event participants
# ---------------------------------------------------------------------------


def _assert_participant_belongs_to_event(
    db: Session, *, event_id: int, participant_id: int
) -> EventParticipant:
    """Defend against URL-substitution attacks on nested routes: a
    participant_id that exists but belongs to a different event must
    return 404 rather than acting on the wrong event."""
    participant = db.get(EventParticipant, participant_id)
    if participant is None or int(participant.event_id) != int(event_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "participant_not_found",
                "message": "participant_not_found",
            },
        )
    return participant


@router.post(
    "/events/{event_id}/participants/{participant_id}/archive",
    response_model=ArchiveResponse,
)
def archive_participant_route(
    event_id: int,
    participant_id: int,
    payload: ArchiveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    _assert_participant_belongs_to_event(
        db, event_id=event_id, participant_id=participant_id
    )
    try:
        p = event_participants_service.archive_event_participant(
            db,
            participant_id=participant_id,
            actor_user_id=int(current_user.id),
            reason=payload.reason,
            note=payload.note,
        )
    except record_dependencies.ArchiveReasonError as exc:
        raise _map_reason_error(exc) from exc
    except event_participants_service.EventParticipantError as exc:
        raise _map_participant_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="event_participant",
        entity_id=participant_id,
        deleted_at=p.deleted_at.isoformat() if p.deleted_at else None,
        activity_logged=True,
    )


@router.post(
    "/events/{event_id}/participants/{participant_id}/restore",
    response_model=ArchiveResponse,
)
def restore_participant_route(
    event_id: int,
    participant_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    _assert_participant_belongs_to_event(
        db, event_id=event_id, participant_id=participant_id
    )
    try:
        event_participants_service.restore_event_participant(
            db,
            participant_id=participant_id,
            actor_user_id=int(current_user.id),
        )
    except event_participants_service.EventParticipantError as exc:
        raise _map_participant_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="event_participant",
        entity_id=participant_id,
        deleted_at=None,
        activity_logged=True,
    )


# ---------------------------------------------------------------------------
# Special orders
# ---------------------------------------------------------------------------


def _assert_special_order_belongs_to_event(
    db: Session, *, event_id: int, special_order_id: int
) -> SpecialOrder:
    so = db.get(SpecialOrder, special_order_id)
    if so is None or int(so.event_id) != int(event_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "special_order_not_found",
                "message": "special_order_not_found",
            },
        )
    return so


@router.post(
    "/events/{event_id}/special-orders/{special_order_id}/archive",
    response_model=ArchiveResponse,
)
def archive_special_order_route(
    event_id: int,
    special_order_id: int,
    payload: ArchiveRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    _assert_special_order_belongs_to_event(
        db, event_id=event_id, special_order_id=special_order_id
    )
    try:
        so = special_order_service.archive_special_order(
            db,
            special_order_id=special_order_id,
            actor_user_id=int(current_user.id),
            reason=payload.reason,
            note=payload.note,
        )
    except record_dependencies.ArchiveReasonError as exc:
        raise _map_reason_error(exc) from exc
    except special_order_service.SpecialOrderError as exc:
        raise _map_special_order_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="special_order",
        entity_id=special_order_id,
        deleted_at=so.deleted_at.isoformat() if so.deleted_at else None,
        activity_logged=True,
    )


@router.post(
    "/events/{event_id}/special-orders/{special_order_id}/restore",
    response_model=ArchiveResponse,
)
def restore_special_order_route(
    event_id: int,
    special_order_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin_scope)],
) -> ArchiveResponse:
    _assert_special_order_belongs_to_event(
        db, event_id=event_id, special_order_id=special_order_id
    )
    try:
        special_order_service.restore_special_order(
            db,
            special_order_id=special_order_id,
            actor_user_id=int(current_user.id),
        )
    except special_order_service.SpecialOrderError as exc:
        raise _map_special_order_error(exc) from exc
    db.commit()
    return ArchiveResponse(
        entity_type="special_order",
        entity_id=special_order_id,
        deleted_at=None,
        activity_logged=True,
    )


# ---------------------------------------------------------------------------
# Recycle Bin
# ---------------------------------------------------------------------------


from datetime import datetime  # noqa: E402  (local to avoid pyflakes)

from fastapi import Query  # noqa: E402


class RecycleBinItem(BaseModel):
    entity_type: str
    entity_id: int
    display_name: str
    secondary_label: str | None
    deleted_at: str
    deleted_by_user_id: int | None
    deleted_by_display_name: str | None
    reason: str | None
    # Parent event_id for the two nested restore routes
    # (event_participant, special_order). NULL for contact and event.
    parent_event_id: int | None


class RecycleBinResponse(BaseModel):
    entity_type: str
    items: list[RecycleBinItem]
    next_before_id: int | None


_ENTITY_MODELS = {
    "contact": Contact,
    "event": Event,
    "event_participant": EventParticipant,
    "special_order": SpecialOrder,
}


_ARCHIVE_ACTIVITY_TYPE = {
    "contact": activity_log_service.CONTACT_ARCHIVED,
    "event": activity_log_service.EVENT_ARCHIVED,
    "event_participant": activity_log_service.EVENT_PARTICIPANT_ARCHIVED,
    "special_order": activity_log_service.SPECIAL_ORDER_ARCHIVED,
}


@router.get(
    "/recycle-bin",
    response_model=RecycleBinResponse,
)
def list_recycle_bin(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
    entity_type: str = Query(..., min_length=1),
    page_size: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    deleted_by_user_id: int | None = Query(default=None, ge=1),
) -> RecycleBinResponse:
    """Paginate archived rows for one entity type.

    Keyset pagination via ``before_id`` (the smallest ``id`` from the
    previous page). The ``id DESC`` order is stable while archive
    happens because new archives carry larger ids than any earlier
    row in the bin.

    Optional filters: ``since`` / ``until`` clamp ``deleted_at``;
    ``deleted_by_user_id`` filters by the actor on the archive
    activity row. ``deleted_by_user_id`` cannot find contacts whose
    archive had no event anchor (Gate 1 fallback path) — those rows
    have no activity_log entry to filter by.
    """
    if entity_type not in _ENTITY_MODELS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_entity_type",
                "message": f"unsupported entity_type: {entity_type!r}",
            },
        )

    model = _ENTITY_MODELS[entity_type]

    q = db.query(model).filter(model.deleted_at.isnot(None))
    if before_id is not None:
        q = q.filter(model.id < before_id)
    if since is not None:
        q = q.filter(model.deleted_at >= since)
    if until is not None:
        q = q.filter(model.deleted_at <= until)
    rows = q.order_by(model.id.desc()).limit(page_size + 1).all()

    if not rows:
        return RecycleBinResponse(
            entity_type=entity_type, items=[], next_before_id=None
        )

    has_more = len(rows) > page_size
    rows = rows[:page_size]

    # Pull the matching archive activity rows in one query, then map.
    audit_by_subject = _load_audit_for(
        db, entity_type=entity_type, subject_ids=[int(r.id) for r in rows]
    )

    if deleted_by_user_id is not None:
        rows = [
            r
            for r in rows
            if audit_by_subject.get(int(r.id), {}).get("actor_user_id")
            == deleted_by_user_id
        ]

    items: list[RecycleBinItem] = []
    for row in rows:
        audit = audit_by_subject.get(int(row.id), {})
        items.append(_to_recycle_item(db, entity_type, row, audit))

    next_before_id = int(rows[-1].id) if has_more and rows else None
    return RecycleBinResponse(
        entity_type=entity_type,
        items=items,
        next_before_id=next_before_id,
    )


def _load_audit_for(
    db: Session, *, entity_type: str, subject_ids: list[int]
) -> dict[int, dict]:
    """Return ``{subject_id: {actor_user_id, actor_display_name, reason}}``
    for the archive activity_log rows of the given subjects. Missing
    subjects map to ``{}`` (e.g. the Gate 1 orphan-contact fallback)."""
    if not subject_ids:
        return {}
    archive_type = _ARCHIVE_ACTIVITY_TYPE[entity_type]
    rows = (
        db.query(ActivityLog)
        .filter(ActivityLog.subject_kind == entity_type)
        .filter(ActivityLog.subject_id.in_(subject_ids))
        .filter(ActivityLog.activity_type == archive_type)
        .order_by(ActivityLog.id.desc())
        .all()
    )
    out: dict[int, dict] = {}
    for row in rows:
        # Newest archive row per subject wins (a row that was archived,
        # restored, then archived again has two rows; the second
        # archive is the one that put it in the Recycle Bin today).
        sid = int(row.subject_id) if row.subject_id is not None else None
        if sid is None or sid in out:
            continue
        payload = row.payload or {}
        out[sid] = {
            "actor_user_id": row.actor_user_id,
            "actor_display_name": row.actor_display_name,
            "reason": payload.get("reason"),
        }
    return out


def _to_recycle_item(
    db: Session, entity_type: str, row, audit: dict
) -> RecycleBinItem:
    display_name, secondary_label = _entity_labels(db, entity_type, row)
    parent_event_id: int | None = None
    if entity_type in ("event_participant", "special_order"):
        parent_event_id = int(row.event_id)
    return RecycleBinItem(
        entity_type=entity_type,
        entity_id=int(row.id),
        display_name=display_name,
        secondary_label=secondary_label,
        deleted_at=row.deleted_at.isoformat(),
        deleted_by_user_id=audit.get("actor_user_id"),
        deleted_by_display_name=audit.get("actor_display_name"),
        reason=audit.get("reason"),
        parent_event_id=parent_event_id,
    )


def _entity_labels(
    db: Session, entity_type: str, row
) -> tuple[str, str | None]:
    """Per-entity (display_name, secondary_label). Secondary is a
    short context string so the Recycle Bin row is identifiable
    without round-tripping to the detail page."""
    if entity_type == "contact":
        bits: list[str] = []
        if row.phone_e164:
            bits.append(row.phone_e164)
        if row.email:
            bits.append(row.email)
        return row.display_name or "(unnamed contact)", " · ".join(bits) or None

    if entity_type == "event":
        date_str = (
            row.event_date.isoformat() if row.event_date is not None else None
        )
        secondary = date_str
        return row.event_name or "(unnamed event)", secondary

    if entity_type == "event_participant":
        parent_event = db.get(Event, row.event_id)
        parent_name = parent_event.event_name if parent_event else "(event?)"
        return row.display_name, f"{row.role} · {parent_name}"

    if entity_type == "special_order":
        catalog = db.get(CatalogItem, row.catalog_item_id)
        code = catalog.public_code if catalog else f"catalog#{row.catalog_item_id}"
        parent_event = db.get(Event, row.event_id)
        parent_name = parent_event.event_name if parent_event else "(event?)"
        return (
            f"{code} · size {row.size_label}",
            f"{row.status} · {parent_name}",
        )

    return f"#{row.id}", None
