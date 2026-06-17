"""Activity log service.

Phase 9. Single-purpose: append a row to ``activity_log`` whenever a
domain service flips a state worth telling staff about, and read it
back scoped by ``event_id`` for the timeline tab.

Design:

  - **Explicit calls, not decorators.** Each domain service calls
    ``log_activity`` after it mutates state. Decorators were on the
    table per the plan, but explicit calls grep cleanly and don't
    hide the side effect from a reader of the service code.
  - **No commit.** ``log_activity`` only ``db.add`` + ``db.flush``.
    The caller owns the transaction; if it rolls back, the activity
    row vanishes alongside the state change. That's the right
    coupling — staff should never see a "sent" timeline entry for an
    invoice that didn't actually transition.
  - **Vocabulary lives here.** The constants below are the canonical
    list of activity types. The router exposes them; the frontend
    label dictionary mirrors them. Adding a new type is a one-line
    addition here plus a label entry on the client.
  - **Subject pair invariant.** A row either has BOTH ``subject_kind``
    and ``subject_id`` or NEITHER. The DB enforces this with a CHECK;
    the helper raises rather than letting the insert fail late.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from database.models import ActivityLog, User

log = logging.getLogger(__name__)


ActorKind = Literal["staff", "customer", "system"]
SubjectKind = Literal[
    "invoice",
    "quote",
    "payment",
    "event",
    "contact",
    "appointment",
    # D3 of the CRM record deletion plan added archive/restore audit on
    # event_participants and special_orders; the two existing subject
    # kinds didn't cover them.
    "event_participant",
    "special_order",
]


# ---------------------------------------------------------------------------
# Activity-type vocabulary
# ---------------------------------------------------------------------------


# Invoice lifecycle
INVOICE_CREATED = "invoice.created"
INVOICE_UPDATED = "invoice.updated"
INVOICE_SENT = "invoice.sent"
INVOICE_RESENT = "invoice.resent"
INVOICE_VIEWED = "invoice.viewed"
INVOICE_PAID = "invoice.paid"
INVOICE_CANCELLED = "invoice.cancelled"
INVOICE_DELETED = "invoice.deleted"
INVOICE_PDF_RENDERED = "invoice.pdf_rendered"
INVOICE_REMINDER_SENT = "invoice.reminder_sent"

# Quote lifecycle
QUOTE_CREATED = "quote.created"
QUOTE_UPDATED = "quote.updated"
QUOTE_SENT = "quote.sent"
QUOTE_RESENT = "quote.resent"
QUOTE_VIEWED = "quote.viewed"
QUOTE_APPROVED = "quote.approved"
QUOTE_SIGNED = "quote.signed"
QUOTE_APPROVED_IN_STORE = "quote.approved_in_store"
QUOTE_REJECTED = "quote.rejected"
QUOTE_CANCELLED = "quote.cancelled"
QUOTE_CONVERTED = "quote.converted"
# Emitted when a quote that was previously 'converted' falls back to
# 'approved' because its linked draft invoice was deleted. The quote
# becomes re-convertible.
QUOTE_UNCONVERTED = "quote.unconverted"
QUOTE_DELETED = "quote.deleted"
QUOTE_EXPIRED = "quote.expired"

# Payment lifecycle
PAYMENT_CREATED = "payment.created"
PAYMENT_REFUNDED = "payment.refunded"
PAYMENT_VOIDED = "payment.voided"
PAYMENT_APPLIED = "payment.applied"
PAYMENT_UNAPPLIED = "payment.unapplied"

# Event lifecycle (mirrored from event_status_change_events)
EVENT_STATUS_CHANGED = "event.status_changed"
EVENT_PARTICIPANT_ADDED = "event.participant_added"
# Walk-in / in-store lead capture (services/walk_in_service.py). Anchored
# to the freshly-created event so the timeline tab shows a single
# "Captured as walk-in" row at the top of the audit trail.
EVENT_WALK_IN_CREATED = "event.walk_in_created"

# Appointment lifecycle (Phase 3 of the sales portal). These activity
# rows are still scoped by `event_id`; if the appointment has no
# linked event, we skip the activity row and rely on the appointment
# row's own columns (`status`, `attended_at`, `no_show_at`,
# `cancelled_at`, `internal_notes`) to record what happened.
APPOINTMENT_ARRIVED = "appointment.arrived"
APPOINTMENT_NO_SHOW = "appointment.no_show"
APPOINTMENT_CANCELLED = "appointment.cancelled"
APPOINTMENT_NOTES_EDITED = "appointment.notes_edited"
# Tried-on log (Phase 4 of the sales portal). Payload references
# `catalog_item_id` only — never `internal_sku`, `designer`, or
# `style_number`. The renderer resolves the public_code at read time.
APPOINTMENT_TRIED_ON_ADDED = "appointment.tried_on_added"
APPOINTMENT_TRIED_ON_UPDATED = "appointment.tried_on_updated"
APPOINTMENT_TRIED_ON_REMOVED = "appointment.tried_on_removed"
# Stylist reassignment (Phase 6 of the sales rep dashboard). Payload
# carries `{from_user_id, to_user_id, reason}` so the timeline shows
# who moved what to whom. Lead reassignments cascade to future-dated
# appointments and write one event-level parent row plus one
# appointment-level row per cascaded appointment, all anchored to the
# same event_id.
APPOINTMENT_REASSIGNED = "appointment.reassigned"
EVENT_REASSIGNED = "event.reassigned"

# Phase 10.3: tagging an appointment / quote / invoice to a specific
# event_participant — the buyer-journey link. Detach uses the same kind
# with to_participant = None so timelines can render "untagged" without
# a separate type.
APPOINTMENT_PARTICIPANT_ATTACHED = "appointment.participant_attached"
QUOTE_PARTICIPANT_ATTACHED = "quote.participant_attached"
INVOICE_PARTICIPANT_ATTACHED = "invoice.participant_attached"

# Portal-invitation lifecycle. These are staff actions but the customer
# acts on the keys, so they get their own bucket.
INVITATION_REVOKED = "invitation.revoked"
INVITATION_RESENT = "invitation.resent"

# CRM-record archive/restore. Added in D3 of the CRM record deletion
# plan. Anchor rules (see docs/CRM_RECORD_DELETION_PLAN.md Gate 1):
#   - event.* anchors to the event itself.
#   - event_participant.* anchors to the participant's parent event.
#   - special_order.* anchors to the special order's parent event.
#   - contact.* anchors to the contact's most recently created event
#     (live or deleted). If the contact has no events ever, the helper
#     skips the activity row and logs a warning — the row's own
#     deleted_at is the audit trail in that case.
# Payload shape:
#   {reason: str, note?: str, dependency_snapshot: dict,
#    anchor_event_id?: int}
CONTACT_ARCHIVED = "contact.archived"
CONTACT_RESTORED = "contact.restored"
EVENT_ARCHIVED = "event.archived"
EVENT_RESTORED = "event.restored"
EVENT_PARTICIPANT_ARCHIVED = "event_participant.archived"
EVENT_PARTICIPANT_RESTORED = "event_participant.restored"
SPECIAL_ORDER_ARCHIVED = "special_order.archived"
SPECIAL_ORDER_RESTORED = "special_order.restored"


_KNOWN_TYPES = frozenset(
    {
        INVOICE_CREATED,
        INVOICE_UPDATED,
        INVOICE_SENT,
        INVOICE_RESENT,
        INVOICE_VIEWED,
        INVOICE_PAID,
        INVOICE_CANCELLED,
        INVOICE_DELETED,
        INVOICE_PDF_RENDERED,
        INVOICE_REMINDER_SENT,
        QUOTE_CREATED,
        QUOTE_UPDATED,
        QUOTE_SENT,
        QUOTE_RESENT,
        QUOTE_VIEWED,
        QUOTE_APPROVED,
        QUOTE_SIGNED,
        QUOTE_APPROVED_IN_STORE,
        QUOTE_REJECTED,
        QUOTE_CANCELLED,
        QUOTE_CONVERTED,
        QUOTE_UNCONVERTED,
        QUOTE_DELETED,
        QUOTE_EXPIRED,
        PAYMENT_CREATED,
        PAYMENT_REFUNDED,
        PAYMENT_VOIDED,
        PAYMENT_APPLIED,
        PAYMENT_UNAPPLIED,
        EVENT_STATUS_CHANGED,
        EVENT_PARTICIPANT_ADDED,
        EVENT_WALK_IN_CREATED,
        APPOINTMENT_ARRIVED,
        APPOINTMENT_NO_SHOW,
        APPOINTMENT_CANCELLED,
        APPOINTMENT_NOTES_EDITED,
        APPOINTMENT_TRIED_ON_ADDED,
        APPOINTMENT_TRIED_ON_UPDATED,
        APPOINTMENT_TRIED_ON_REMOVED,
        APPOINTMENT_REASSIGNED,
        EVENT_REASSIGNED,
        APPOINTMENT_PARTICIPANT_ATTACHED,
        QUOTE_PARTICIPANT_ATTACHED,
        INVOICE_PARTICIPANT_ATTACHED,
        INVITATION_REVOKED,
        INVITATION_RESENT,
        CONTACT_ARCHIVED,
        CONTACT_RESTORED,
        EVENT_ARCHIVED,
        EVENT_RESTORED,
        EVENT_PARTICIPANT_ARCHIVED,
        EVENT_PARTICIPANT_RESTORED,
        SPECIAL_ORDER_ARCHIVED,
        SPECIAL_ORDER_RESTORED,
    }
)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def log_activity(
    db: Session,
    *,
    event_id: int,
    actor_kind: ActorKind,
    activity_type: str,
    actor_user_id: int | None = None,
    subject_kind: SubjectKind | None = None,
    subject_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> ActivityLog:
    """Append one row to ``activity_log``. No commit.

    ``actor_user_id`` is required for ``actor_kind='staff'``. The CHECK
    that used to enforce this at the DB layer was dropped in migration
    037 (it conflicted with the SET NULL FK on user deletion); the
    invariant is now service-level only and raised below. Callers
    should provide ``None`` for customer/system actions explicitly so
    the call site reads as "this is a customer action" instead of
    "I forgot to pass a user".
    """
    if activity_type not in _KNOWN_TYPES:
        # Don't crash the calling transaction over a typo'd activity
        # type — log loud and write the row anyway. The string column
        # is wide enough; the cost of a missed audit row would be
        # higher than the cost of a slightly off label.
        log.warning(
            "activity_log.unknown_type",
            extra={"activity_type": activity_type, "event_id": event_id},
        )
    if actor_kind == "staff" and actor_user_id is None:
        raise ValueError("staff actor_kind requires actor_user_id")
    if (subject_kind is None) != (subject_id is None):
        raise ValueError(
            "subject_kind and subject_id must both be set or both NULL"
        )

    # Snapshot the staff member's display name at write time. The FK
    # uses ON DELETE SET NULL so a user deletion would otherwise wipe
    # the audit row's actor identity entirely; this column survives
    # the FK being nulled. Customer / system rows have no name to
    # capture (we know who they are by actor_kind alone).
    actor_display_name: str | None = None
    if actor_kind == "staff" and actor_user_id is not None:
        user = db.get(User, actor_user_id)
        if user is not None:
            actor_display_name = (user.full_name or user.username or "")[:200] or None

    row = ActivityLog(
        event_id=event_id,
        actor_user_id=actor_user_id,
        actor_display_name=actor_display_name,
        actor_kind=actor_kind,
        activity_type=activity_type,
        subject_kind=subject_kind,
        subject_id=subject_id,
        payload=payload or {},
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@dataclass
class ActivityView:
    id: int
    event_id: int
    actor_user_id: int | None
    actor_kind: str
    actor_display_name: str | None
    activity_type: str
    subject_kind: str | None
    subject_id: int | None
    payload: dict[str, Any]
    created_at: datetime


def list_activities_for_event(
    db: Session,
    *,
    event_id: int,
    limit: int = 100,
    before_id: int | None = None,
) -> list[ActivityView]:
    """Reverse-chronological list of activities for one event.

    Keyset pagination via ``before_id``: the next page passes the
    smallest ``id`` from the previous page. The
    ``(event_id, id DESC)`` index makes this an index-only scan.

    Display name resolution: prefer the live join (so a renamed user
    shows the new name on every activity), then the column snapshot
    (so a deleted user still has an attribution), then NULL (the UI
    renders a generic label in that case).
    """
    limit = max(1, min(int(limit), 200))
    q = db.query(ActivityLog, User.full_name, User.username).outerjoin(
        User, User.id == ActivityLog.actor_user_id
    ).filter(ActivityLog.event_id == event_id)
    if before_id is not None:
        q = q.filter(ActivityLog.id < before_id)
    rows = q.order_by(ActivityLog.id.desc()).limit(limit).all()
    out: list[ActivityView] = []
    for row, full_name, username in rows:
        live = full_name or username
        display = live or row.actor_display_name
        out.append(
            ActivityView(
                id=int(row.id),
                event_id=int(row.event_id),
                actor_user_id=row.actor_user_id,
                actor_kind=row.actor_kind,
                actor_display_name=display,
                activity_type=row.activity_type,
                subject_kind=row.subject_kind,
                subject_id=row.subject_id,
                payload=dict(row.payload or {}),
                created_at=row.created_at,
            )
        )
    return out
