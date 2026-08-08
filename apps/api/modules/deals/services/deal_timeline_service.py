"""One chronological story per deal, in the order it happened.

A rep opening a deal used to have to reconcile three surfaces to answer
"where did this come from and what happened next?": the Activity tab
(activity_log), the Notes tab (event_notes), and a separate text-messages
box pinned below the activity list. Calls were in a fourth place entirely
until they were mirrored into activity_log.

This service merges all of it into a single ordered list:

  * ``activity_log``          — lead submitted, calls, status changes, …
  * ``event_notes``           — staff notes and their follow-up reminders
  * ``conversation_messages`` — SMS / web chat / Meta, in and out

Each source keeps its own table (they have genuinely different shapes and
lifecycles); the merge happens on read. Items are normalized to a common
envelope — ``kind``, ``at``, ``actor``, plus a small typed payload — and
the CLIENT owns the wording, matching how the activity tab already works.

It also computes the ``summary`` a rep needs above the fold: where the
lead came from, what the last touch was, and any flags worth acting on.

Flags are deliberately few. A badge that fires on most deals teaches reps
to ignore badges, so this only raises things that are both precise and
actionable:

  * ``wrong_number``      — an INBOUND text said so, in the customer's words
  * ``needs_first_contact`` — a lead came in and nobody has called, texted,
    or written a note since
  * ``follow_up_due``     — a reminder is past due and unresolved

Deliberately NOT flagged: "no vehicle linked". Only 26 of 86 live deals
carry a vehicle link, so that badge would light up on two thirds of the
board and mean nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    ActivityLog,
    CatalogItem,
    Contact,
    Conversation,
    ConversationMessage,
    Event,
    EventNote,
    LeadAttribution,
)

# One page of story. Deals top out around a few dozen events; the cap
# exists so a pathological deal can't stream thousands of rows into the
# browser. When it trips, the response says so rather than silently
# truncating.
MAX_ITEMS = 300

ItemKind = Literal["activity", "note", "message"]

# What counts as a human on our side making contact. Used by
# ``needs_first_contact`` — a lead nobody has worked yet.
#
# Walk-ins and arrivals count: a rep stood in front of this person and
# typed them into the CRM. Flagging "no one has reached out" on a deal
# that only exists BECAUSE someone talked to them is exactly the kind of
# nonsense that teaches reps to ignore flags.
_STAFF_TOUCH_ACTIVITY = frozenset(
    {
        "call.initiated",
        "call.outcome_recorded",
        "event.walk_in_created",
        "appointment.arrived",
    }
)

# How a deal came into existence, in the order we prefer to report it.
# The value is the rep-facing phrase for "where did this come from".
_ORIGIN_ACTIVITY = {
    "event.walk_in_created": "Walk-in",
    "lead.public_submitted": "Website lead",
}

# "wrong number", "wrong #", "u have the wrong number". Anchored on the
# word so "wrongfully" and similar don't trip it.
_WRONG_NUMBER_RE = re.compile(r"\bwrong\s*(number|num\b|#)", re.IGNORECASE)


@dataclass
class TimelineItem:
    kind: ItemKind
    at: datetime
    id: int
    # Discriminator within the kind: activity_type, 'note', or direction.
    subtype: str
    actor_name: str | None = None
    actor_kind: str | None = None
    body: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DealSummary:
    created_via: str | None = None
    created_by_name: str | None = None
    created_at: datetime | None = None
    lead_source: str | None = None
    lead_source_page: str | None = None
    lead_message: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    vehicle_label: str | None = None
    last_touch_at: datetime | None = None
    last_touch_label: str | None = None
    flags: list[dict[str, str]] = field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    """Postgres hands back tz-aware datetimes, but a naive one would blow up
    the merge sort. Normalize defensively."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _activity_items(db: Session, event_id: int) -> list[TimelineItem]:
    rows = db.execute(
        select(ActivityLog)
        .where(ActivityLog.event_id == event_id)
        .order_by(ActivityLog.created_at.desc())
        .limit(MAX_ITEMS)
    ).scalars().all()
    return [
        TimelineItem(
            kind="activity",
            at=_aware(r.created_at),
            id=r.id,
            subtype=r.activity_type,
            actor_name=r.actor_display_name,
            actor_kind=r.actor_kind,
            payload=dict(r.payload or {}),
        )
        for r in rows
    ]


def _note_items(db: Session, event_id: int) -> list[TimelineItem]:
    rows = db.execute(
        select(EventNote)
        .where(EventNote.event_id == event_id, EventNote.deleted_at.is_(None))
        .order_by(EventNote.created_at.desc())
        .limit(MAX_ITEMS)
    ).scalars().all()
    return [
        TimelineItem(
            kind="note",
            at=_aware(r.created_at),
            id=r.id,
            subtype="note",
            actor_name=r.author_display_name,
            # An authorless note is a backfilled lead intake, not a person.
            actor_kind="staff" if r.author_user_id else "system",
            body=r.body,
            payload={
                "remind_at": _aware(r.remind_at),
                "reminder_sent_at": _aware(r.reminder_sent_at),
                "resolved_at": _aware(r.resolved_at),
                "edited_at": _aware(r.edited_at),
                "remind_channel": r.remind_channel,
                "imported": r.author_user_id is None,
            },
        )
        for r in rows
    ]


def _message_items(db: Session, event_id: int) -> list[TimelineItem]:
    """Texts and chats on conversations linked to this deal.

    Conversations carry ``event_id`` directly, so no contact-level fallback
    here — a message only belongs on a deal's story when it was actually
    tied to that deal.
    """
    rows = db.execute(
        select(ConversationMessage, Conversation.channel)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(Conversation.event_id == event_id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(MAX_ITEMS)
    ).all()
    items: list[TimelineItem] = []
    for msg, channel in rows:
        items.append(
            TimelineItem(
                kind="message",
                at=_aware(msg.sent_at or msg.created_at),
                id=msg.id,
                subtype=msg.direction,
                actor_kind="staff" if msg.direction == "outbound" else "customer",
                body=msg.body,
                payload={
                    "channel": msg.channel or channel,
                    "status": msg.status,
                    "sent_by_user_id": msg.sent_by_user_id,
                    "failed": msg.failed_at is not None,
                },
            )
        )
    return items


def _vehicle_label(db: Session, event: Event) -> str | None:
    """"2019 Toyota Camry" for the header. NULL on the ~70% of deals that
    carry no vehicle link (Event has no ORM relationship for it)."""
    if not event.vehicle_catalog_item_id:
        return None
    item = db.get(CatalogItem, event.vehicle_catalog_item_id)
    if item is None:
        return None
    bits = [str(item.year) if item.year else None, item.make, item.model]
    return " ".join(b for b in bits if b) or item.product_title


def _describe_last_touch(item: TimelineItem) -> str:
    if item.kind == "message":
        if item.subtype == "inbound":
            return "Customer replied by text"
        return "We texted the customer"
    if item.kind == "note":
        return "Staff note added"
    if item.subtype == "call.outcome_recorded":
        return f"Call result: {item.payload.get('outcome') or 'recorded'}"
    if item.subtype == "call.initiated":
        return "We called the customer"
    if item.subtype == "lead.public_submitted":
        return "Website lead submitted"
    if item.subtype == "event.status_changed":
        return f"Moved to {item.payload.get('to_status') or 'a new column'}"
    return item.subtype.replace(".", " ").replace("_", " ").capitalize()


def _build_flags(
    db: Session, event: Event, items: list[TimelineItem]
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []

    # --- wrong number: the customer told us, in an inbound text ----------
    wrong = next(
        (
            i
            for i in items
            if i.kind == "message"
            and i.subtype == "inbound"
            and i.body
            and _WRONG_NUMBER_RE.search(i.body)
        ),
        None,
    )
    if wrong is not None:
        contact = db.get(Contact, event.primary_contact_id)
        number = (contact.phone_e164 or contact.phone) if contact else None
        flags.append(
            {
                "code": "wrong_number",
                "severity": "warning",
                "label": "Possible wrong number",
                "detail": (
                    f"The person at {number} replied “{wrong.body.strip()}”."
                    if number
                    else f"The recipient replied “{wrong.body.strip()}”."
                ),
            }
        )

    # --- nobody has worked this lead yet --------------------------------
    touched = any(
        (i.kind == "note" and not i.payload.get("imported"))
        or (i.kind == "message" and i.subtype == "outbound")
        or (i.kind == "activity" and i.subtype in _STAFF_TOUCH_ACTIVITY)
        for i in items
    )
    if not touched and items:
        flags.append(
            {
                "code": "needs_first_contact",
                "severity": "warning",
                "label": "No one has reached out yet",
                "detail": "No call, text, or note on this deal since it came in.",
            }
        )

    # --- a promised follow-up has come and gone -------------------------
    now = datetime.now(timezone.utc)
    overdue = [
        i
        for i in items
        if i.kind == "note"
        and i.payload.get("remind_at")
        and not i.payload.get("resolved_at")
        and i.payload["remind_at"] <= now
    ]
    if overdue:
        flags.append(
            {
                "code": "follow_up_due",
                "severity": "warning",
                "label": (
                    "Follow-up due"
                    if len(overdue) == 1
                    else f"{len(overdue)} follow-ups due"
                ),
                "detail": overdue[0].body or "",
            }
        )

    return flags


def _creation_facts(db: Session, event: Event) -> tuple[str | None, str | None, datetime | None]:
    """Who put this deal in the CRM, and how.

    A rep looking at an unfamiliar deal asks "who took this?" first. The
    answer is already in the earliest activity row — walk-ins carry the
    staffer who logged them, website leads carry no actor because the
    customer did it themselves — it just wasn't being surfaced.
    """
    row = db.execute(
        select(ActivityLog)
        .where(
            ActivityLog.event_id == event.id,
            ActivityLog.activity_type.in_(tuple(_ORIGIN_ACTIVITY)),
        )
        .order_by(ActivityLog.created_at.asc())
        .limit(1)
    ).scalars().first()
    if row is None:
        return None, None, _aware(event.created_at)
    origin = _ORIGIN_ACTIVITY.get(row.activity_type)
    # Both shapes are logged as event.walk_in_created — the payload's
    # booking_context is what separates someone who arrived from someone
    # who called. Rows written before migration 104 carry no context and
    # keep the plain "Walk-in" phrasing, which was accurate for them.
    if (row.payload or {}).get("booking_context") == "phone_call":
        origin = "Phone lead"
    return (
        origin,
        row.actor_display_name,
        _aware(row.created_at),
    )


def _lead_summary(db: Session, event: Event, items: list[TimelineItem]) -> dict:
    attribution = db.execute(
        select(LeadAttribution)
        .where(LeadAttribution.event_id == event.id)
        .order_by(LeadAttribution.created_at.asc())
        .limit(1)
    ).scalars().first()

    # The customer's own words arrived as the deal's first (imported) note.
    imported = [i for i in items if i.kind == "note" and i.payload.get("imported")]
    message = imported[-1].body if imported else None

    source = None
    source_page = None
    if attribution is not None:
        source = attribution.source
        source_page = attribution.source_page
    # `source` is NULL on older rows; the page is the honest fallback and is
    # what a rep actually recognizes ("/contact-us").
    if not source and source_page:
        source = "website"
    return {"source": source, "source_page": source_page, "message": message}


def build_deal_timeline(db: Session, event_id: int) -> tuple[DealSummary, list[TimelineItem], bool]:
    """Return ``(summary, items_newest_first, truncated)``."""
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise LookupError("event_not_found")

    items = (
        _activity_items(db, event_id)
        + _note_items(db, event_id)
        + _message_items(db, event_id)
    )
    items.sort(key=lambda i: (i.at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    truncated = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]

    lead = _lead_summary(db, event, items)
    contact = db.get(Contact, event.primary_contact_id)
    last = items[0] if items else None

    created_via, created_by_name, created_at = _creation_facts(db, event)

    summary = DealSummary(
        created_via=created_via,
        created_by_name=created_by_name,
        created_at=created_at,
        lead_source=lead["source"],
        lead_source_page=lead["source_page"],
        lead_message=lead["message"],
        customer_name=contact.display_name if contact else None,
        customer_phone=(contact.phone_e164 or contact.phone) if contact else None,
        vehicle_label=_vehicle_label(db, event),
        last_touch_at=last.at if last else None,
        last_touch_label=_describe_last_touch(last) if last else None,
        flags=_build_flags(db, event, items),
    )
    return summary, items, truncated
