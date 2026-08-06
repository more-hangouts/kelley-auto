"""Follow-up reminder pass for deal notes.

A rep writes "called again, asked to call back Thursday" on a deal and
attaches a reminder. This pass finds the ones that have come due and
delivers them.

    run_note_reminder_pass(db, now=None) -> NoteReminderResult

Invoked by scripts/run_note_reminders.py, which deploy/systemd's
kelley-reminders.timer fires every few minutes. Unlike the invoice
reminder pass in workers/daily.py (once a day at 02:30, in-process), a
follow-up has a time-of-day the rep chose, so it needs a short tick — and
prod runs uvicorn with ``--workers 2``, which would give an in-process
loop two of everything.

Concurrency: each due row is claimed with ``SELECT ... FOR UPDATE SKIP
LOCKED`` before it is sent, so two overlapping passes (a slow tick still
running when the next fires, or someone running the script by hand) split
the work rather than double-sending. ``reminder_sent_at`` is the durable
guard on top of that: the claim query ignores anything already stamped.

Delivery failure leaves ``reminder_sent_at`` NULL, so the next tick
retries. That is deliberate — a reminder nobody received is worth
re-sending, and the transport is best-effort by design.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import ADMIN_BASE_URL
from database.models import Contact, Event, EventNote, User
from modules.core.services import cron_state
from modules.core.services.email_transport import send_rendered_safely
from modules.core.services.notification_templates import render_note_reminder

log = logging.getLogger(__name__)

# Safety valve: one tick never sends more than this. A backlog (timer
# disabled for a week, say) drains over successive ticks instead of
# hammering the mail transport in one burst.
MAX_PER_PASS = 100


@dataclass
class NoteReminderResult:
    scanned: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0


def _due_note_ids(db: Session, *, now: datetime, limit: int) -> list[int]:
    """Claim up to ``limit`` due reminders for THIS pass.

    Matches idx_event_notes_due exactly. ``skip_locked`` means a row another
    pass is already working is left to that pass rather than waited on.
    """
    return list(
        db.execute(
            select(EventNote.id)
            .where(
                EventNote.remind_at.is_not(None),
                EventNote.remind_at <= now,
                EventNote.reminder_sent_at.is_(None),
                EventNote.resolved_at.is_(None),
                EventNote.deleted_at.is_(None),
            )
            .order_by(EventNote.remind_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )


def _deal_url(event_id: int) -> str:
    return f"{ADMIN_BASE_URL}/deals/{event_id}/notes"


def _customer_name(db: Session, event: Event) -> str:
    contact = db.get(Contact, event.primary_contact_id)
    return (contact.display_name if contact else None) or event.event_name


def _send_one(db: Session, note: EventNote, *, now: datetime) -> str:
    """Deliver one reminder. Returns 'sent', 'failed', or 'skipped'."""
    event = db.get(Event, note.event_id)
    if event is None or event.deleted_at is not None:
        # The deal was archived after the reminder was set. Stamp it so the
        # pass stops reconsidering a row it can never usefully deliver.
        note.reminder_sent_at = now
        return "skipped"

    rep = db.get(User, note.remind_user_id) if note.remind_user_id else None
    if rep is None or not (rep.email or "").strip():
        log.warning(
            "note reminder %s has no deliverable recipient (user=%s)",
            note.id,
            note.remind_user_id,
        )
        note.reminder_sent_at = now
        return "skipped"

    if note.remind_channel != "email":
        # Schema allows 'sms'; nothing sends it yet (users have no phone
        # column). The service rejects it at write time, so this only
        # trips for rows written before a transport lands.
        log.warning(
            "note reminder %s wants channel %r which has no transport",
            note.id,
            note.remind_channel,
        )
        return "skipped"

    rendered = render_note_reminder(
        rep_name=rep.full_name or rep.username,
        customer_name=_customer_name(db, event),
        deal_name=event.event_name,
        note_body=note.body,
        note_written_at=note.created_at,
        admin_url=_deal_url(event.id),
    )
    ok = send_rendered_safely(
        to=rep.email, rendered=rendered, scope="deal_note_reminder"
    )
    if not ok:
        # Leave reminder_sent_at NULL — the next tick retries.
        return "failed"

    note.reminder_sent_at = now
    return "sent"


def run_note_reminder_pass(
    db: Session, *, now: datetime | None = None, limit: int = MAX_PER_PASS
) -> NoteReminderResult:
    """Deliver every follow-up reminder that has come due."""
    moment = now or datetime.now(timezone.utc)
    result = NoteReminderResult()

    with cron_state.record_run(cron_state.DEAL_NOTE_REMINDER) as run:
        note_ids = _due_note_ids(db, now=moment, limit=limit)
        result.scanned = len(note_ids)

        for note_id in note_ids:
            note = db.get(EventNote, note_id)
            if note is None:
                continue
            outcome = _send_one(db, note, now=moment)
            if outcome == "sent":
                result.sent += 1
            elif outcome == "failed":
                result.failed += 1
            else:
                result.skipped += 1

        db.commit()
        run.scanned = result.scanned
        run.changed = result.sent
        run.extra = {"failed": result.failed, "skipped": result.skipped}

    if result.scanned:
        log.info(
            "note reminder pass: scanned=%d sent=%d failed=%d skipped=%d",
            result.scanned,
            result.sent,
            result.failed,
            result.skipped,
        )
    return result
