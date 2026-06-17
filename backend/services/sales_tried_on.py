"""Tried-on log for the Sales Portal (Phase 4).

The user's directive: try-on rows are anchored on the appointment but
written against the event (so they live in the CRM history). If the
appointment has no linked event, the service refuses with
`event_required` and the UI guides the stylist back to the Phase 3
"Mark arrived" action. We do not auto-create events here — Phase 3
already gives staff a clean Arrived button that does the right thing
(promote + transition to consulted).

Activity-log payloads carry only `tried_on_item_id` and
`catalog_item_id`. Per the SKU obfuscation policy in Phase 9 of the
phase plan, `internal_sku`, `designer`, `style_number`, and
`description_text` never appear in the payload.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    AppointmentTriedOnItem,
    CatalogItem,
    User,
)
from services import activity_log


class TriedOnError(Exception):
    """Stable error codes the router maps to HTTP statuses."""

    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _require_event(appt: Appointment) -> int:
    if appt.crm_event_id is None:
        raise TriedOnError("event_required", http_status=409)
    return appt.crm_event_id


def _serialize(row: AppointmentTriedOnItem, item: CatalogItem | None) -> dict:
    """Render a tried-on row with enough catalog context for the UI.

    `internal_sku`, `designer`, `style_number`, and `description_text`
    are intentionally omitted from the embedded `catalog_item` shape;
    the picker in admin land surfaces them, but the on-screen card on
    the sales floor only needs identification + image.
    """
    return {
        "id": row.id,
        "appointment_id": row.appointment_id,
        "catalog_item_id": row.catalog_item_id,
        "size_label": row.size_label,
        "liked": row.liked,
        "notes": row.notes,
        "created_by_user_id": row.created_by_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "catalog_item": (
            None
            if item is None
            else {
                "id": item.id,
                "public_code": item.public_code,
                "color": item.color,
                "category": item.category,
                "product_title": item.product_title,
                "house_name": item.house_name,
                "image_urls": list(item.image_urls or []),
            }
        ),
    }


def list_for_appointment(db: Session, *, appointment_id: int) -> list[dict]:
    """Return the tried-on rows for an appointment, oldest first.

    Read access does not require an event — empty list is fine when the
    appointment has not been promoted yet, so the section can render
    the "Mark arrived first" guide alongside an empty list.
    """
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise TriedOnError("appointment_not_found", http_status=404)

    rows = (
        db.execute(
            select(AppointmentTriedOnItem)
            .where(AppointmentTriedOnItem.appointment_id == appointment_id)
            .order_by(AppointmentTriedOnItem.created_at, AppointmentTriedOnItem.id)
        )
        .scalars()
        .all()
    )

    catalog_ids = {r.catalog_item_id for r in rows}
    items: dict[int, CatalogItem] = {}
    if catalog_ids:
        items = {
            ci.id: ci
            for ci in db.execute(
                select(CatalogItem).where(CatalogItem.id.in_(catalog_ids))
            )
            .scalars()
            .all()
        }

    return [_serialize(r, items.get(r.catalog_item_id)) for r in rows]


def add_tried_on(
    db: Session,
    *,
    appointment_id: int,
    catalog_item_id: int,
    actor_user_id: int,
    size_label: str | None = None,
    liked: bool | None = None,
    notes: str | None = None,
) -> dict:
    appt = db.get(Appointment, appointment_id)
    if appt is None:
        raise TriedOnError("appointment_not_found", http_status=404)
    event_id = _require_event(appt)

    catalog_item = db.get(CatalogItem, catalog_item_id)
    if catalog_item is None:
        raise TriedOnError("catalog_item_not_found", http_status=404)
    if not catalog_item.active:
        raise TriedOnError("catalog_item_inactive", http_status=409)

    row = AppointmentTriedOnItem(
        appointment_id=appointment_id,
        catalog_item_id=catalog_item_id,
        size_label=size_label or None,
        liked=liked,
        notes=notes,
        created_by_user_id=actor_user_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # The unique constraint is the only IntegrityError shape this
        # path can produce in practice; FK violations on
        # `appointment_id` / `catalog_item_id` are caught by the get
        # checks above.
        raise TriedOnError("duplicate_tried_on", http_status=409) from exc

    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPOINTMENT_TRIED_ON_ADDED,
        subject_kind="appointment",
        subject_id=appt.id,
        payload={
            "tried_on_item_id": row.id,
            "catalog_item_id": row.catalog_item_id,
            "size_label": row.size_label,
        },
    )
    db.flush()
    return _serialize(row, catalog_item)


def update_tried_on(
    db: Session,
    *,
    tried_on_id: int,
    actor_user_id: int,
    size_label: str | None = ...,  # type: ignore[assignment]
    liked: bool | None = ...,  # type: ignore[assignment]
    notes: str | None = ...,  # type: ignore[assignment]
) -> dict:
    """Patch one of `size_label`, `liked`, `notes`. Sentinel `...` means
    "leave alone"; explicit None is allowed (clears the field)."""
    row = db.get(AppointmentTriedOnItem, tried_on_id)
    if row is None:
        raise TriedOnError("tried_on_not_found", http_status=404)
    appt = db.get(Appointment, row.appointment_id)
    if appt is None:
        # FK is CASCADE on appointment delete, so this is a defensive
        # check rather than an expected branch.
        raise TriedOnError("appointment_not_found", http_status=404)
    event_id = _require_event(appt)

    changed_fields: list[str] = []
    if size_label is not ...:
        new_size = size_label or None
        if new_size != row.size_label:
            row.size_label = new_size
            changed_fields.append("size_label")
    if liked is not ...:
        if liked != row.liked:
            row.liked = liked
            changed_fields.append("liked")
    if notes is not ...:
        new_notes = notes if notes is not None else None
        if new_notes != row.notes:
            row.notes = new_notes
            # Field name only — we never log the text, just the
            # acknowledgment that notes changed.
            changed_fields.append("notes")

    if not changed_fields:
        # Nothing to update; do not bump updated_at or write a noise audit row.
        catalog_item = db.get(CatalogItem, row.catalog_item_id)
        return _serialize(row, catalog_item)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise TriedOnError("duplicate_tried_on", http_status=409) from exc

    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPOINTMENT_TRIED_ON_UPDATED,
        subject_kind="appointment",
        subject_id=appt.id,
        payload={
            "tried_on_item_id": row.id,
            "catalog_item_id": row.catalog_item_id,
            "fields": sorted(changed_fields),
        },
    )
    db.flush()
    catalog_item = db.get(CatalogItem, row.catalog_item_id)
    return _serialize(row, catalog_item)


def remove_tried_on(
    db: Session, *, tried_on_id: int, actor_user_id: int
) -> None:
    row = db.get(AppointmentTriedOnItem, tried_on_id)
    if row is None:
        raise TriedOnError("tried_on_not_found", http_status=404)
    appt = db.get(Appointment, row.appointment_id)
    if appt is None:
        raise TriedOnError("appointment_not_found", http_status=404)
    event_id = _require_event(appt)

    payload = {
        "tried_on_item_id": row.id,
        "catalog_item_id": row.catalog_item_id,
        "size_label": row.size_label,
    }

    db.delete(row)
    db.flush()

    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPOINTMENT_TRIED_ON_REMOVED,
        subject_kind="appointment",
        subject_id=appt.id,
        payload=payload,
    )
    db.flush()
