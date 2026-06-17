"""Thin helpers for the staff.shift_* event surfaces (#18, #19, #20).

Wraps ``services.notification_routing.record_event`` for the three
schedule-mutation kinds:

  - ``staff.shift_added``     — fires when an out-of-publish single-cell
    publish or create-and-publish writes a new published row. The bulk
    ``publish_week`` path uses ``staff.schedule_published`` (#17) instead
    so a manager publishing 20 shifts doesn't generate 20 separate
    "shift added" emails.

  - ``staff.shift_edited``    — fires when a published row's
    ``starts_at_local`` / ``ends_at_local`` / ``business_date`` /
    ``manager_notes`` is mutated through the published-edit path.
    Payload carries ``old_shift`` and ``new_shift`` so the renderer can
    show a before/after diff without a second DB round-trip — necessary
    because by the time the worker dispatches, the row only reflects
    the new state.

  - ``staff.shift_deleted``   — fires when a published row is retracted
    back to draft (the "delete a published shift" UX). Payload carries
    the ``shift`` snapshot because the entry still exists post-retract
    but as a draft, and the renderer needs the published-shift fields
    to render "this shift was removed from your schedule."

All three serialize datetimes via ``.isoformat()`` into payload so the
shift dict survives the JSONB round-trip. The dispatcher's
``_normalize_staff_payload`` (services/notification_service) coerces
``starts_at`` / ``ends_at`` back to ``datetime`` for the renderer.

Recipient resolution piggy-backs on the existing intrinsic-targeting
function ``_staffer_of_schedule_entry`` (services/notification_routing)
keyed by ``subject_kind='shift'`` + ``subject_id=entry.id``. The entry
must still exist in the DB at ``record_event`` time so the lookup can
resolve to the affected staffer; retract preserves the row so this
holds. Hard deletes would break this and need an explicit recipient
fallback in payload — out of scope for this slice.

No-op when the entry's user is inactive or has no email — the same
guardrail ``recipients_for`` applies to every event kind.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import StaffScheduleEntry


def _serialize_shift(shift: dict) -> dict:
    """Coerce a shift dict's datetimes to ISO strings for JSONB storage.

    Mirrors the shape produced by ``staff_schedule._entry_to_shift_dict``
    but with ``starts_at`` / ``ends_at`` as ISO strings so SQLAlchemy can
    write the payload column without a custom serializer. The dispatcher
    coerces them back at render time via the ``_at``-suffix heuristic.
    """
    return {
        "starts_at": shift["starts_at"].isoformat(),
        "ends_at": shift["ends_at"].isoformat(),
        "title": shift.get("title") or "Boutique shift",
        "location": shift.get("location") or "Bella's XV boutique",
        "notes": shift.get("notes"),
    }


def notify_shift_added(
    db: Session,
    *,
    entry: StaffScheduleEntry,
    shift: dict,
    actor_user_id: int | None,
) -> None:
    """Fire ``staff.shift_added`` for the affected staffer.

    Caller passes the freshly-built ``shift`` dict (via
    ``staff_schedule._entry_to_shift_dict``) so the payload carries
    the snapshot of what was published. ``entry`` is used only for
    ``subject_id`` routing — the renderer reads from payload, not
    from the entry row.
    """
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.shift_added",
        subject_kind="shift",
        subject_id=entry.id,
        actor_user_id=actor_user_id,
        payload={"shift": _serialize_shift(shift)},
    )


def notify_shift_edited(
    db: Session,
    *,
    entry: StaffScheduleEntry,
    old_shift: dict,
    new_shift: dict,
    actor_user_id: int | None,
) -> None:
    """Fire ``staff.shift_edited`` for the affected staffer.

    Caller snapshots ``old_shift`` BEFORE mutating the entry and
    ``new_shift`` AFTER, then calls this helper. The payload carries
    both so the renderer can show "Previous: … / Updated: …" without
    a second DB lookup — by the time the worker dispatches, the row
    only reflects the new state.
    """
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.shift_edited",
        subject_kind="shift",
        subject_id=entry.id,
        actor_user_id=actor_user_id,
        payload={
            "old_shift": _serialize_shift(old_shift),
            "new_shift": _serialize_shift(new_shift),
        },
    )


def notify_shift_deleted(
    db: Session,
    *,
    entry: StaffScheduleEntry,
    shift: dict,
    actor_user_id: int | None,
) -> None:
    """Fire ``staff.shift_deleted`` for the affected staffer.

    Call this BEFORE flipping ``status`` back to draft (or before any
    other mutation that would change what the staffer was originally
    notified about). Intrinsic targeting via ``_staffer_of_schedule_entry``
    requires the entry row to exist; retract preserves it. Payload
    carries the published-shift snapshot for the renderer.
    """
    from services import notification_routing

    notification_routing.record_event(
        db,
        kind="staff.shift_deleted",
        subject_kind="shift",
        subject_id=entry.id,
        actor_user_id=actor_user_id,
        payload={"shift": _serialize_shift(shift)},
    )
