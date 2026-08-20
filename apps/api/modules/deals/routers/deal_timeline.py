"""One merged story per deal.

    GET /api/events/{event_id}/timeline

Returns the deal's summary (where the lead came from, last touch, flags)
plus every event on it — activity, staff notes, and text messages — in one
chronological list, newest first. Replaces the rep having to read the
Activity tab, the Notes tab, and a separate SMS box and merge them in
their head.

Wording lives in the client, matching the existing activity tab: the
payload carries facts, the UI carries English.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import User
from modules.deals.services import deal_timeline_service
from modules.scheduling.services.attendance_gate import require_floor_access

router = APIRouter()


class TimelineFlag(BaseModel):
    code: str
    severity: str
    label: str
    detail: str


class TimelineSummary(BaseModel):
    created_via: str | None
    created_by_name: str | None
    created_at: datetime | None
    lead_source: str | None
    lead_source_page: str | None
    lead_message: str | None
    customer_name: str | None
    customer_phone: str | None
    vehicle_label: str | None
    sold_vehicle_label: str | None = None
    last_touch_at: datetime | None
    last_touch_label: str | None
    flags: list[TimelineFlag]


class TimelineItemResponse(BaseModel):
    kind: str
    id: int
    at: datetime | None
    subtype: str
    actor_name: str | None
    actor_kind: str | None
    body: str | None
    payload: dict[str, Any]


class TimelineResponse(BaseModel):
    event_id: int
    summary: TimelineSummary
    items: list[TimelineItemResponse]
    # True when the deal has more history than one page holds — the client
    # says so rather than quietly showing a partial story.
    truncated: bool


@router.get("/{event_id}/timeline", response_model=TimelineResponse)
def get_deal_timeline(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> TimelineResponse:
    try:
        summary, items, truncated = deal_timeline_service.build_deal_timeline(
            db, event_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="event_not_found") from exc

    return TimelineResponse(
        event_id=event_id,
        summary=TimelineSummary(
            created_via=summary.created_via,
            created_by_name=summary.created_by_name,
            created_at=summary.created_at,
            lead_source=summary.lead_source,
            lead_source_page=summary.lead_source_page,
            lead_message=summary.lead_message,
            customer_name=summary.customer_name,
            customer_phone=summary.customer_phone,
            vehicle_label=summary.vehicle_label,
            sold_vehicle_label=summary.sold_vehicle_label,
            last_touch_at=summary.last_touch_at,
            last_touch_label=summary.last_touch_label,
            flags=[TimelineFlag(**f) for f in summary.flags],
        ),
        items=[
            TimelineItemResponse(
                kind=i.kind,
                id=i.id,
                at=i.at,
                subtype=i.subtype,
                actor_name=i.actor_name,
                actor_kind=i.actor_kind,
                body=i.body,
                payload=i.payload,
            )
            for i in items
        ],
        truncated=truncated,
    )
