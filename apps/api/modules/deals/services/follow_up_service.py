"""The follow-up working queue — deals grouped by when they are next owed a call.

The Deals board answers "what stage is this deal in?". It does not answer the
question the floor actually asks every morning: *who do I call today?* A status
column is a static pile — 294 deals sat in `contacted` with nothing saying
which of them were due — so this module provides the other axis: the same live
deals, ordered by their next follow-up reminder.

There is deliberately NO new schema here. `event_notes.remind_at` (migration
100) already models "this deal is owed a callback on this date", and the deal
detail Timeline already writes it. This is a read model over that column.

**The next reminder for a deal** is the EARLIEST `remind_at` among its notes
that is not deleted and not resolved. Earliest, not latest: if a rep set a
reminder for Friday and later one for next month, the deal is due Friday.
`resolved_at` (the rep saying "handled") retires a reminder whether or not the
email ever fired, so a resolved note never holds a deal in a bucket.

**Buckets** are cut on the DEALERSHIP-LOCAL calendar date, not on UTC and not
on a 24-hour window from now. A reminder set for 9am today is "due today" at
5pm the same day, not "overdue" — the working day is the unit reps think in.

    overdue      next reminder's local date  <  today
    due_today    next reminder's local date  == today
    upcoming     next reminder's local date  >  today
    no_reminder  live deal with no open reminder at all

`no_reminder` is the important one and the reason this view exists: it is the
pile nobody has made a decision about. It is ordered STALEST FIRST (oldest
`status_changed_at`) so the most neglected deal is the first thing on screen,
and it is capped — the response carries the full count alongside the page so
the UI can say "showing 100 of 281" instead of implying the list is complete.

Terminal deals (sold / lost) are excluded everywhere: a closed deal is not
owed a call. That is what makes "mark it lost" a real way to clear the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, func, or_, select
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import CatalogItem, Contact, Event, EventNote, User
from modules.booking.services.event_workflow import all_statuses

# How many `no_reminder` rows to return. The bucket is unbounded by nature
# (every deal nobody has triaged lands in it); the count comes back separately
# so a capped page never reads as a complete one.
NO_REMINDER_LIMIT = 100

BUCKET_OVERDUE = "overdue"
BUCKET_DUE_TODAY = "due_today"
BUCKET_UPCOMING = "upcoming"
BUCKET_NO_REMINDER = "no_reminder"

BUCKETS = (BUCKET_OVERDUE, BUCKET_DUE_TODAY, BUCKET_UPCOMING, BUCKET_NO_REMINDER)


@dataclass(frozen=True)
class FollowUpItem:
    """One deal in the queue, carrying what a rep needs to make the call."""

    event_id: int
    event_name: str | None
    status: str
    status_changed_at: datetime | None
    bucket: str

    contact_id: int
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None

    owner_user_id: int | None
    owner_name: str | None

    vehicle_id: int | None
    vehicle_label: str | None
    vehicle_stock_number: str | None

    # The open reminder driving the bucket (all None for `no_reminder`).
    reminder_note_id: int | None
    remind_at: datetime | None
    reminder_sent_at: datetime | None
    remind_user_id: int | None

    # The most recent note on the deal — "what happened last time".
    last_note_id: int | None
    last_note_body: str | None
    last_note_author: str | None
    last_note_at: datetime | None

    # Days since the deal last moved. The queue's staleness signal.
    days_since_status_change: int | None


@dataclass(frozen=True)
class FollowUpQueue:
    event_type: str
    today: date
    timezone: str
    items: list[FollowUpItem]
    counts: dict[str, int]
    # Total rows in `no_reminder` before NO_REMINDER_LIMIT was applied.
    no_reminder_total: int


def _open_statuses(event_type: str) -> list[str]:
    return [s.code for s in all_statuses(event_type) if not s.is_terminal]


def _local_today() -> date:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).date()


def _bucket_for(local_due: date, today: date) -> str:
    if local_due < today:
        return BUCKET_OVERDUE
    if local_due == today:
        return BUCKET_DUE_TODAY
    return BUCKET_UPCOMING


def _vehicle_label(year, make, model, trim) -> str | None:
    parts = [str(p) for p in (year, make, model, trim) if p]
    return " ".join(parts) or None


def get_follow_up_queue(
    db: Session,
    *,
    event_type: str = "vehicle_sale",
    owner_user_id: int | None = None,
    include_unassigned: bool = True,
) -> FollowUpQueue:
    """Build the queue.

    ``owner_user_id`` filters to one rep's deals. Because 274 of the live deals
    have no owner at all, filtering by rep would hide most of the work — so
    ``include_unassigned`` keeps ownerless deals visible in a rep's queue by
    default. Pass False for a strict "only mine" view.
    """
    open_statuses = _open_statuses(event_type)
    today = _local_today()
    tz = ZoneInfo(APP_TIMEZONE)

    # --- Next open reminder per deal: earliest unresolved remind_at ---------
    # DISTINCT ON rather than MIN()+rejoin: it returns the WINNING NOTE ROW,
    # so the UI can link to and resolve that exact note. A MIN() subquery
    # would need a second join back on the timestamp, which fans out into
    # duplicate deals whenever two notes share a remind_at (a rep setting the
    # same round-number time on two notes is not hypothetical).
    next_remind_subq = (
        select(
            EventNote.event_id.label("event_id"),
            EventNote.id.label("note_id"),
            EventNote.remind_at.label("remind_at"),
            EventNote.reminder_sent_at.label("reminder_sent_at"),
            EventNote.remind_user_id.label("remind_user_id"),
        )
        .where(
            EventNote.deleted_at.is_(None),
            EventNote.remind_at.is_not(None),
            EventNote.resolved_at.is_(None),
        )
        .distinct(EventNote.event_id)
        .order_by(EventNote.event_id, EventNote.remind_at.asc(), EventNote.id.asc())
        .subquery()
    )

    # --- Most recent note per deal (reminder or not) ------------------------
    # DISTINCT ON is the cheap way to get the whole winning row rather than
    # just its timestamp; ordering matches the Timeline's newest-first.
    last_note_subq = (
        select(
            EventNote.event_id.label("event_id"),
            EventNote.id.label("note_id"),
            EventNote.body.label("body"),
            EventNote.author_display_name.label("author"),
            EventNote.created_at.label("created_at"),
        )
        .where(EventNote.deleted_at.is_(None))
        .distinct(EventNote.event_id)
        .order_by(EventNote.event_id, EventNote.created_at.desc(), EventNote.id.desc())
        .subquery()
    )

    stmt = (
        select(
            Event.id,
            Event.event_name,
            Event.status,
            Event.status_changed_at,
            Contact.id.label("contact_id"),
            Contact.display_name,
            Contact.phone,
            Contact.email,
            Event.owner_user_id,
            User.full_name.label("owner_name"),
            CatalogItem.id.label("vehicle_id"),
            CatalogItem.year,
            CatalogItem.make,
            CatalogItem.model,
            CatalogItem.trim,
            CatalogItem.stock_number,
            next_remind_subq.c.remind_at,
            next_remind_subq.c.note_id.label("remind_note_id"),
            next_remind_subq.c.reminder_sent_at,
            next_remind_subq.c.remind_user_id,
            last_note_subq.c.note_id.label("last_note_id"),
            last_note_subq.c.body.label("last_note_body"),
            last_note_subq.c.author.label("last_note_author"),
            last_note_subq.c.created_at.label("last_note_at"),
            func.extract(
                "day", func.now() - Event.status_changed_at
            ).cast(Integer).label("days_since_status_change"),
        )
        .join(Contact, Contact.id == Event.primary_contact_id)
        .outerjoin(User, User.id == Event.owner_user_id)
        .outerjoin(CatalogItem, CatalogItem.id == Event.vehicle_catalog_item_id)
        .outerjoin(next_remind_subq, next_remind_subq.c.event_id == Event.id)
        .outerjoin(last_note_subq, last_note_subq.c.event_id == Event.id)
        .where(
            Event.event_type == event_type,
            Event.deleted_at.is_(None),
            Event.status.in_(open_statuses),
        )
        # Due first, then the never-triaged pile stalest-first. NULLS LAST puts
        # `no_reminder` after everything with a date on it.
        .order_by(
            next_remind_subq.c.remind_at.asc().nulls_last(),
            Event.status_changed_at.asc(),
        )
    )

    if owner_user_id is not None:
        if include_unassigned:
            stmt = stmt.where(
                or_(
                    Event.owner_user_id == owner_user_id,
                    Event.owner_user_id.is_(None),
                )
            )
        else:
            stmt = stmt.where(Event.owner_user_id == owner_user_id)

    rows = db.execute(stmt).all()

    items: list[FollowUpItem] = []
    counts = {b: 0 for b in BUCKETS}
    no_reminder_total = 0

    for r in rows:
        if r.remind_at is None:
            bucket = BUCKET_NO_REMINDER
            no_reminder_total += 1
            # Cap the untriaged pile; `no_reminder_total` still reports the
            # true size so the UI never implies it showed everything.
            if counts[BUCKET_NO_REMINDER] >= NO_REMINDER_LIMIT:
                continue
        else:
            bucket = _bucket_for(r.remind_at.astimezone(tz).date(), today)

        counts[bucket] += 1
        items.append(
            FollowUpItem(
                event_id=r.id,
                event_name=r.event_name,
                status=r.status,
                status_changed_at=r.status_changed_at,
                bucket=bucket,
                contact_id=r.contact_id,
                contact_name=r.display_name,
                contact_phone=r.phone,
                contact_email=r.email,
                owner_user_id=r.owner_user_id,
                owner_name=r.owner_name,
                vehicle_id=r.vehicle_id,
                vehicle_label=_vehicle_label(r.year, r.make, r.model, r.trim),
                vehicle_stock_number=r.stock_number,
                reminder_note_id=r.remind_note_id,
                remind_at=r.remind_at,
                reminder_sent_at=r.reminder_sent_at,
                remind_user_id=r.remind_user_id,
                last_note_id=r.last_note_id,
                last_note_body=r.last_note_body,
                last_note_author=r.last_note_author,
                last_note_at=r.last_note_at,
                days_since_status_change=r.days_since_status_change,
            )
        )

    return FollowUpQueue(
        event_type=event_type,
        today=today,
        timezone=APP_TIMEZONE,
        items=items,
        counts=counts,
        no_reminder_total=no_reminder_total,
    )
