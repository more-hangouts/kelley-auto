"""Contact identity: find-or-create from a booking submission.

Phone (E.164) is the canonical identity — it's the most reliable signal we
have at booking time. Email is a fallback only when phone normalization
failed. Matches the dedup logic used in migration 014 backfill.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Appointment, Contact, Event
from services import booking_service

log = logging.getLogger(__name__)


class ContactServiceError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "contact_error",
        conflict_contact_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.conflict_contact_id = conflict_contact_id


def find_or_create_contact(
    db: Session,
    *,
    phone_e164: str | None,
    email: str | None,
    phone: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> tuple[Contact, bool]:
    """Return ``(contact, was_new)`` for this booking. Creates a row if no match.

    `was_new` is True when this call inserted a fresh contact, False when an
    existing row was found by phone (or by email-when-phone-absent). The
    flag is the canonical "did we create this person just now?" signal —
    callers should not run a parallel pre-lookup to compute it themselves.

    Race-safe: if two requests insert the same phone_e164 concurrently, the
    loser catches the unique-violation in a savepoint and re-fetches the
    winner instead of bubbling a 500 to the customer.
    """

    existing = _lookup_contact(db, phone_e164=phone_e164, email=email)
    if existing is not None:
        return existing, False

    display_name = _compose_display_name(first_name, last_name)
    new_contact = Contact(
        first_name=first_name,
        last_name=last_name,
        display_name=display_name,
        email=email.lower() if email else None,
        phone=phone,
        phone_e164=phone_e164,
    )

    try:
        with db.begin_nested():  # SAVEPOINT
            db.add(new_contact)
            db.flush()
        return new_contact, True
    except IntegrityError:
        # Concurrent insert hit the phone_e164 unique index. Re-fetch the winner.
        winner = _lookup_contact(db, phone_e164=phone_e164, email=email)
        if winner is None:
            raise
        # We lost the race: the row existed by the time we re-fetched.
        # `was_new` is False because *this* call did not insert.
        return winner, False


def _lookup_contact(
    db: Session, *, phone_e164: str | None, email: str | None
) -> Contact | None:
    # D2: soft-deleted contacts are excluded from identity matches. A
    # returning customer whose old contact was archived gets a fresh
    # row; the partial unique on phone_e164 only constrains live rows.
    if phone_e164:
        c = (
            db.query(Contact)
            .filter(Contact.phone_e164 == phone_e164)
            .filter(Contact.deleted_at.is_(None))
            .first()
        )
        if c is not None:
            return c
        # Phone is the identity — don't fall back to email when phone is present.
        # Two people can share an inbox (parent + quince); they shouldn't merge.
        return None

    if email:
        return (
            db.query(Contact)
            .filter(func.lower(Contact.email) == email.lower())
            .filter(Contact.phone_e164.is_(None))
            .filter(Contact.deleted_at.is_(None))
            .first()
        )

    return None


def _compose_display_name(
    first_name: str | None, last_name: str | None
) -> str:
    parts = [p for p in (first_name, last_name) if p]
    if parts:
        return " ".join(parts).strip()
    return "Unknown"


def create_admin_contact(
    db: Session,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
) -> tuple[Contact, bool]:
    """Admin-driven contact insertion with the same phone/email dedup as
    the booking flow.

    Returns ``(contact, was_new)``. ``was_new=False`` means a contact
    matching the supplied phone (or email-when-phone-absent) already
    existed and is being returned untouched — the caller decides whether
    to PATCH or treat as a found-existing UX.
    """
    raw_phone = phone.strip() if phone else None
    phone_e164 = (
        booking_service.normalize_phone_e164(raw_phone) if raw_phone else None
    )
    normalized_email = email.strip().lower() if email else None

    existing = _lookup_contact(db, phone_e164=phone_e164, email=normalized_email)
    if existing is not None:
        return existing, False

    name = (display_name or "").strip() or _compose_display_name(
        first_name, last_name
    )
    new_contact = Contact(
        first_name=first_name or None,
        last_name=last_name or None,
        display_name=name,
        email=normalized_email,
        phone=raw_phone,
        phone_e164=phone_e164,
        notes=notes or None,
    )

    try:
        with db.begin_nested():
            db.add(new_contact)
            db.flush()
    except IntegrityError:
        winner = _lookup_contact(
            db, phone_e164=phone_e164, email=normalized_email
        )
        if winner is None:
            raise
        return winner, False

    db.commit()
    db.refresh(new_contact)
    return new_contact, True


# ---------------------------------------------------------------------------
# Edit + context (Phase B of the Contact UX plan)
# ---------------------------------------------------------------------------


_EDITABLE_FIELDS = {
    "first_name",
    "last_name",
    "display_name",
    "email",
    "phone",
    "notes",
    "tags",
}


def update_contact(
    db: Session, *, contact_id: int, patch: dict[str, Any]
) -> Contact:
    """Apply a partial update to a contact.

    `patch` carries only the fields the caller actually sent (router uses
    `model_dump(exclude_unset=True)`). Display-name precedence:
      - explicit `display_name` always wins
      - otherwise, if first/last changed, recompose from first+last
      - otherwise, leave display_name alone
    Phone changes re-normalize `phone_e164`. A unique-violation on
    `phone_e164` raises `ContactServiceError(code='phone_collision')` with
    the colliding contact id attached so the router can surface it as a 409
    that links straight into Phase C merge.
    """

    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise ContactServiceError("contact not found", code="contact_not_found")

    unknown = set(patch) - _EDITABLE_FIELDS
    if unknown:
        raise ContactServiceError(
            f"unknown fields: {sorted(unknown)}",
            code="unknown_fields",
        )

    # Pre-check phone uniqueness *before* mutating the ORM object. We tried
    # the savepoint+IntegrityError pattern used by `find_or_create_contact`,
    # but that pattern relies on the failing row being a brand-new INSERT
    # the savepoint can fully discard. Updating an existing row leaves the
    # dirty attributes in the identity map; the next autoflush re-raises
    # the violation outside the savepoint and the session is unusable. A
    # raw-SQL pre-check sidesteps that. Race window vs. a concurrent insert
    # is closed by the flush-time IntegrityError handler below, which returns
    # the same 409 shape with an unknown conflict id if another write wins.
    new_phone_e164: str | None = None
    if "phone" in patch:
        new_phone = patch["phone"] or None
        new_phone_e164 = (
            booking_service.normalize_phone_e164(new_phone) if new_phone else None
        )
        if new_phone_e164:
            row = db.execute(
                sql_text(
                    "SELECT id FROM contacts "
                    "WHERE phone_e164 = :p AND id != :cid "
                    "  AND deleted_at IS NULL LIMIT 1"
                ),
                {"p": new_phone_e164, "cid": contact_id},
            ).first()
            if row is not None:
                raise ContactServiceError(
                    "phone in use by another contact",
                    code="phone_collision",
                    conflict_contact_id=row[0],
                )

    explicit_display = "display_name" in patch
    name_components_changed = "first_name" in patch or "last_name" in patch

    if "first_name" in patch:
        contact.first_name = (patch["first_name"] or None)
    if "last_name" in patch:
        contact.last_name = (patch["last_name"] or None)

    if explicit_display:
        new_display = (patch["display_name"] or "").strip()
        if not new_display:
            raise ContactServiceError(
                "display_name cannot be empty",
                code="display_name_required",
            )
        contact.display_name = new_display
    elif name_components_changed:
        contact.display_name = _compose_display_name(
            contact.first_name, contact.last_name
        )

    if "email" in patch:
        contact.email = patch["email"].lower() if patch["email"] else None

    if "phone" in patch:
        contact.phone = patch["phone"] or None
        contact.phone_e164 = new_phone_e164

    if "notes" in patch:
        contact.notes = patch["notes"]
    if "tags" in patch:
        contact.tags = patch["tags"] or []

    try:
        db.flush()
    except IntegrityError as exc:
        # Belt-and-suspenders fallback: a concurrent insert could win the
        # phone_e164 between our pre-check and flush. Rare; surface as the
        # same 409 shape so the UI handles it the same way.
        db.rollback()
        raise ContactServiceError(
            "phone in use by another contact",
            code="phone_collision",
            conflict_contact_id=None,
        ) from exc

    return contact


def get_contact_context(
    db: Session, *, contact_id: int, max_alternates: int = 5
) -> dict[str, Any]:
    """Lightweight per-contact aggregates for the staff edit UI.

    Counts of linked events and appointments, plus up to `max_alternates`
    distinct celebrant names seen on this contact's appointments that
    differ from the contact's display_name. Powers the "Also booked under"
    chips in `ContactEditDialog`.
    """

    event_count = (
        db.query(func.count(Event.id))
        .filter(Event.primary_contact_id == contact_id)
        .filter(Event.deleted_at.is_(None))
        .scalar()
        or 0
    )
    appointment_count = (
        db.query(func.count(Appointment.id))
        .filter(Appointment.contact_id == contact_id)
        .scalar()
        or 0
    )

    contact = db.get(Contact, contact_id)
    contact_norm = (contact.display_name or "").strip().lower() if contact else ""

    rows = (
        db.query(
            Appointment.celebrant_first_name,
            Appointment.celebrant_last_name,
        )
        .filter(Appointment.contact_id == contact_id)
        .order_by(Appointment.created_at.desc())
        .limit(50)
        .all()
    )
    seen: set[str] = set()
    alternates: list[str] = []
    for first, last in rows:
        parts = [p.strip() for p in (first, last) if p and p.strip()]
        if not parts:
            continue
        full = " ".join(parts)
        norm = full.lower()
        if norm == contact_norm or norm in seen:
            continue
        seen.add(norm)
        alternates.append(full)
        if len(alternates) >= max_alternates:
            break

    return {
        "event_count": int(event_count),
        "appointment_count": int(appointment_count),
        "alternate_celebrants": alternates,
    }


# ---------------------------------------------------------------------------
# Archive / restore (D3 of docs/CRM_RECORD_DELETION_PLAN.md)
# ---------------------------------------------------------------------------


def _most_recent_event_id(db: Session, contact_id: int) -> int | None:
    """Per Gate 1, contact-level activity rows anchor to the contact's
    most recently created event (live OR deleted). Returns None when
    the contact has never had an event — the caller then skips the
    audit row."""
    row = (
        db.query(Event.id)
        .filter(Event.primary_contact_id == contact_id)
        .order_by(Event.created_at.desc())
        .first()
    )
    return int(row[0]) if row else None


def archive_contact(
    db: Session,
    *,
    contact_id: int,
    actor_user_id: int | None,
    reason: str,
    note: str | None = None,
) -> Contact:
    """Soft-delete a contact. Idempotent on already-archived rows.

    Refuses if the dependency report has any blocking active dependency
    (active linked event, active invoice/quote/payment, active
    participant). Writes an ``activity_log`` row anchored to the
    contact's most recent event when one exists; logs a warning and
    skips the audit row when the contact never had an event (per Gate
    1 of the CRM record deletion plan)."""
    from services import activity_log, record_dependencies  # local imports avoid cycles

    record_dependencies.validate_archive_reason(reason)

    contact = db.get(Contact, contact_id)
    if contact is None:
        raise ContactServiceError(
            "contact not found", code="contact_not_found"
        )
    if contact.deleted_at is not None:
        return contact  # idempotent

    report = record_dependencies.get_record_dependencies(
        db, entity_type="contact", entity_id=contact_id
    )
    if not report.can_archive:
        raise ContactServiceError(
            "archive blocked: " + "; ".join(report.block_reasons),
            code="archive_blocked",
        )

    now = datetime.now(timezone.utc)
    contact.deleted_at = now
    contact.updated_at = now
    db.flush()

    anchor_event_id = _most_recent_event_id(db, contact_id)
    if anchor_event_id is not None:
        activity_log.log_activity(
            db,
            event_id=anchor_event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.CONTACT_ARCHIVED,
            subject_kind="contact",
            subject_id=int(contact_id),
            payload={
                "reason": reason,
                "note": note,
                "anchor_event_id": anchor_event_id,
                "dependency_snapshot": record_dependencies.dependency_snapshot(
                    report
                ),
            },
        )
    else:
        log.warning(
            "contact.archived without audit anchor (no linked events)",
            extra={
                "contact_id": int(contact_id),
                "reason": reason,
                "actor_user_id": actor_user_id,
            },
        )
    return contact


def restore_contact(
    db: Session,
    *,
    contact_id: int,
    actor_user_id: int | None,
) -> Contact:
    """Lift the archive on a contact. Idempotent on live rows.

    Pre-flight checks the partial-unique ``uq_contacts_phone_e164``
    constraint: if another live contact already claims the
    ``phone_e164`` of this row, restore is blocked with
    ``restore_phone_collision``. Without this guard the flush would
    raise an opaque IntegrityError."""
    from services import activity_log, record_dependencies

    contact = db.get(Contact, contact_id)
    if contact is None:
        raise ContactServiceError(
            "contact not found", code="contact_not_found"
        )
    if contact.deleted_at is None:
        return contact  # idempotent

    if contact.phone_e164 is not None:
        collision = db.execute(
            sql_text(
                "SELECT id FROM contacts "
                "WHERE phone_e164 = :p AND deleted_at IS NULL "
                "  AND id != :cid LIMIT 1"
            ),
            {"p": contact.phone_e164, "cid": contact_id},
        ).first()
        if collision is not None:
            raise ContactServiceError(
                "another live contact already has this phone",
                code="restore_phone_collision",
                conflict_contact_id=int(collision[0]),
            )

    report = record_dependencies.get_record_dependencies(
        db, entity_type="contact", entity_id=contact_id
    )

    now = datetime.now(timezone.utc)
    contact.deleted_at = None
    contact.updated_at = now
    db.flush()

    anchor_event_id = _most_recent_event_id(db, contact_id)
    if anchor_event_id is not None:
        activity_log.log_activity(
            db,
            event_id=anchor_event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.CONTACT_RESTORED,
            subject_kind="contact",
            subject_id=int(contact_id),
            payload={
                "anchor_event_id": anchor_event_id,
                "dependency_snapshot": record_dependencies.dependency_snapshot(
                    report
                ),
            },
        )
    else:
        log.warning(
            "contact.restored without audit anchor (no linked events)",
            extra={
                "contact_id": int(contact_id),
                "actor_user_id": actor_user_id,
            },
        )
    return contact


def get_linked_events(
    db: Session, *, contact_id: int
) -> list[dict[str, Any]]:
    """Events where this contact is the primary celebrant.

    Ordered with future / dated events first (`event_date DESC`), then
    `created_at DESC` so a brand-new lead with no event_date floats above
    older undated rows. Each entry carries a server-computed `route`
    string so the frontend never builds the URL itself; that is the same
    pattern Global Search uses to keep navigation owned by the API.
    """

    rows = (
        db.query(
            Event.id,
            Event.event_name,
            Event.event_type,
            Event.status,
            Event.event_date,
            Event.created_at,
        )
        .filter(Event.primary_contact_id == contact_id)
        .filter(Event.deleted_at.is_(None))
        .order_by(
            Event.event_date.desc().nullslast(),
            Event.created_at.desc(),
        )
        .all()
    )

    return [
        {
            "id": int(row.id),
            "event_name": row.event_name,
            "event_type": row.event_type,
            "status": row.status,
            "event_date": row.event_date,
            "route": f"/events/{int(row.id)}",
        }
        for row in rows
    ]
