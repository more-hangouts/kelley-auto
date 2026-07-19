"""Record dependency preview for CRM-record archive/restore flows.

Phase D1 of ``docs/CRM_RECORD_DELETION_PLAN.md``. Single read-only
entry point used by the admin dependency endpoint and (post-D3) by the
archive helpers themselves. Returns a structured count of inbound
references to a target CRM record so the staff confirm modal can show
"this contact has 3 events, 1 invoice" before any destructive action
is offered.

Scope:

  - Supported entity types: ``contact``, ``event``, ``event_participant``,
    ``special_order``. These are the four targets of the D2 soft-delete
    migration.
  - Inbound FK map is captured in
    ``docs/CRM_RECORD_DELETION_PLAN.md`` (the Inbound FK Inventory
    table); this module is the executable mirror of that table.
  - Read-only. No mutation, no ``activity_log`` writes.

Note on ``deleted_at``:

  This module already understands soft-delete on tables that carry the
  column today — Tier 1 financials (``invoices``, ``quotes``,
  ``payments``, ``invoice_invitations``, ``quote_invitations``,
  ``event_documents``). The four target CRM tables gain ``deleted_at``
  only in D2; until then every row on those tables is treated as
  active. ``_TARGET_TABLES_WITH_DELETED_AT`` flips to ``True`` when D2
  ships, and the per-entity functions pick up the deleted-count split
  with no other change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    Contact,
    Event,
    EventDocument,
    EventParticipant,
    Invoice,
    InvoiceInvitation,
    Payment,
    PaymentAllocation,
    Quote,
    QuoteInvitation,
    SpecialOrder,
)

EntityType = Literal["contact", "event", "event_participant", "special_order"]

SUPPORTED_ENTITY_TYPES: tuple[EntityType, ...] = (
    "contact",
    "event",
    "event_participant",
    "special_order",
)

# D3 of the CRM record deletion plan: the enum the archive verb requires
# on every call. Kept here because the archive flow is intrinsically tied
# to the dependency preview — the same modal collects the reason that
# rides into ``activity_log`` payloads.
ARCHIVE_REASONS: frozenset[str] = frozenset(
    {
        "duplicate",
        "test_record",
        "created_by_mistake",
        "customer_requested",
        "other",
    }
)


class ArchiveReasonError(ValueError):
    """Caller passed a reason outside ``ARCHIVE_REASONS``. Router maps
    to 400."""


def validate_archive_reason(reason: str) -> str:
    if reason not in ARCHIVE_REASONS:
        raise ArchiveReasonError(f"unsupported archive reason: {reason!r}")
    return reason


def dependency_snapshot(report: "DependencyReport") -> dict:
    """Compact, JSON-safe view of a :class:`DependencyReport` for the
    archive/restore ``activity_log`` payload. Keeps just the counts +
    block reasons; sample titles are skipped because they can change
    over time and bloat the audit row."""
    return {
        "block_reasons": list(report.block_reasons),
        "dependencies": [
            {
                "kind": d.kind,
                "active_count": d.active_count,
                "deleted_count": d.deleted_count,
                "blocking": d.blocking,
            }
            for d in report.dependencies
        ],
    }

# Tier-1 soft-delete coverage on the four CRM targets. All True since
# migration 080 (D2) added ``deleted_at`` to every entry below. Kept as
# a registry so a future addition (e.g. ``appointments`` if status
# fields stop being enough) is a single-line change.
_TARGET_TABLES_WITH_DELETED_AT: dict[str, bool] = {
    "contacts": True,
    "events": True,
    "event_participants": True,
    "special_orders": True,
}


class RecordNotFoundError(Exception):
    """Entity does not exist (or, post-D2, was hard-deleted in defiance
    of policy). Router maps to 404."""

    def __init__(self, entity_type: str, entity_id: int) -> None:
        super().__init__(f"{entity_type} {entity_id} not found")
        self.entity_type = entity_type
        self.entity_id = entity_id


class UnsupportedEntityTypeError(Exception):
    """Caller asked for an entity type outside the D1 scope. Router
    maps to 400."""

    def __init__(self, entity_type: str) -> None:
        super().__init__(f"unsupported entity_type: {entity_type!r}")
        self.entity_type = entity_type


@dataclass(frozen=True)
class DependencyCount:
    """One row in the dependency report — one inbound relationship.

    ``kind`` is a stable string the frontend keys its label dictionary
    on (e.g. ``"events"``, ``"invoices"``). ``active_count`` is rows
    with ``deleted_at IS NULL`` (or all rows for target tables until
    D2 ships). ``deleted_count`` is the soft-deleted set. ``blocking``
    is set when the product rule says this relationship should prevent
    archive (regardless of FK behavior — soft-delete never fires FK
    cascades because the row physically remains).
    """

    kind: str
    active_count: int
    deleted_count: int
    blocking: bool


@dataclass(frozen=True)
class DependencyReport:
    entity_type: EntityType
    entity_id: int
    is_currently_deleted: bool
    can_archive: bool
    can_restore: bool
    block_reasons: list[str]
    dependencies: list[DependencyCount]
    sample_titles: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def get_record_dependencies(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
) -> DependencyReport:
    """Return a dependency report for one CRM record.

    Raises:
      - ``UnsupportedEntityTypeError`` if ``entity_type`` is not in
        ``SUPPORTED_ENTITY_TYPES``.
      - ``RecordNotFoundError`` if no row exists.
    """
    if entity_type not in SUPPORTED_ENTITY_TYPES:
        raise UnsupportedEntityTypeError(entity_type)

    if entity_type == "contact":
        return _contact_dependencies(db, entity_id)
    if entity_type == "event":
        return _event_dependencies(db, entity_id)
    if entity_type == "event_participant":
        return _event_participant_dependencies(db, entity_id)
    if entity_type == "special_order":
        return _special_order_dependencies(db, entity_id)
    raise UnsupportedEntityTypeError(entity_type)


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------


def _contact_dependencies(db: Session, contact_id: int) -> DependencyReport:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise RecordNotFoundError("contact", contact_id)

    events_active = _count(db, Event, Event.primary_contact_id == contact_id, target_table="events")
    events_deleted = _count_deleted(db, Event, Event.primary_contact_id == contact_id, target_table="events")
    # Participants on archived events are logically inert from the
    # contact's perspective — staff archived the parent event, so
    # those rows already hide from every list view. Only count live-
    # event participants as blockers so a contact whose events are all
    # in the Recycle Bin remains archivable.
    participants_active = (
        db.query(func.count(EventParticipant.id))
        .join(Event, Event.id == EventParticipant.event_id)
        .filter(EventParticipant.contact_id == contact_id)
        .filter(EventParticipant.deleted_at.is_(None))
        .filter(Event.deleted_at.is_(None))
        .scalar()
        or 0
    )
    participants_deleted = (
        db.query(func.count(EventParticipant.id))
        .filter(EventParticipant.contact_id == contact_id)
        .filter(EventParticipant.deleted_at.isnot(None))
        .scalar()
        or 0
    )
    participants_active = int(participants_active)
    participants_deleted = int(participants_deleted)
    # Appointments don't yet have soft-delete (out of D2 scope); count all
    # as active. cancelled/no_show are operational states already.
    appointments_active = (
        db.query(func.count(Appointment.id))
        .filter(Appointment.contact_id == contact_id)
        .scalar()
        or 0
    )
    invoices_active = _count(db, Invoice, Invoice.contact_id == contact_id)
    invoices_deleted = _count_deleted(db, Invoice, Invoice.contact_id == contact_id)
    quotes_active = _count(db, Quote, Quote.contact_id == contact_id)
    quotes_deleted = _count_deleted(db, Quote, Quote.contact_id == contact_id)
    payments_active = _count(db, Payment, Payment.contact_id == contact_id)
    payments_deleted = _count_deleted(db, Payment, Payment.contact_id == contact_id)

    deps = [
        DependencyCount("events", events_active, events_deleted, blocking=events_active > 0),
        DependencyCount(
            "event_participants",
            participants_active,
            participants_deleted,
            blocking=participants_active > 0,
        ),
        DependencyCount("appointments", int(appointments_active), 0, blocking=False),
        DependencyCount("invoices", invoices_active, invoices_deleted, blocking=invoices_active > 0),
        DependencyCount("quotes", quotes_active, quotes_deleted, blocking=quotes_active > 0),
        DependencyCount("payments", payments_active, payments_deleted, blocking=payments_active > 0),
    ]

    block_reasons = _block_reasons_from_deps(deps, kind_labels={
        "events": "linked event",
        "event_participants": "linked event participant",
        "invoices": "active invoice",
        "quotes": "active quote",
        "payments": "recorded payment",
    })

    sample_titles: dict[str, list[str]] = {}
    if events_active > 0:
        sample_titles["events"] = [
            row.event_name
            for row in (
                db.query(Event.event_name)
                .filter(Event.primary_contact_id == contact_id)
                .order_by(Event.event_date.desc().nullslast(), Event.created_at.desc())
                .limit(3)
                .all()
            )
        ]

    is_deleted = _is_soft_deleted(contact, "contacts")
    return DependencyReport(
        entity_type="contact",
        entity_id=contact_id,
        is_currently_deleted=is_deleted,
        can_archive=(not is_deleted) and not block_reasons,
        can_restore=is_deleted,
        block_reasons=block_reasons,
        dependencies=deps,
        sample_titles=sample_titles,
    )


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


def _event_dependencies(db: Session, event_id: int) -> DependencyReport:
    event = db.get(Event, event_id)
    if event is None:
        raise RecordNotFoundError("event", event_id)

    participants_active = _count(
        db,
        EventParticipant,
        EventParticipant.event_id == event_id,
        target_table="event_participants",
    )
    participants_deleted = _count_deleted(
        db,
        EventParticipant,
        EventParticipant.event_id == event_id,
        target_table="event_participants",
    )
    appointments_active = (
        db.query(func.count(Appointment.id))
        .filter(Appointment.crm_event_id == event_id)
        .scalar()
        or 0
    )
    invoices_active = _count(db, Invoice, Invoice.event_id == event_id)
    invoices_deleted = _count_deleted(db, Invoice, Invoice.event_id == event_id)
    quotes_active = _count(db, Quote, Quote.event_id == event_id)
    quotes_deleted = _count_deleted(db, Quote, Quote.event_id == event_id)
    payments_active = (
        db.query(func.count(func.distinct(Payment.id)))
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .filter(Invoice.event_id == event_id)
        .filter(Payment.deleted_at.is_(None))
        .scalar()
        or 0
    )
    so_active = _count(
        db, SpecialOrder, SpecialOrder.event_id == event_id, target_table="special_orders"
    )
    so_deleted = _count_deleted(
        db, SpecialOrder, SpecialOrder.event_id == event_id, target_table="special_orders"
    )
    docs_active = _count(db, EventDocument, EventDocument.event_id == event_id)
    docs_deleted = _count_deleted(db, EventDocument, EventDocument.event_id == event_id)

    deps = [
        DependencyCount(
            "participants", participants_active, participants_deleted, blocking=False
        ),
        DependencyCount("appointments", int(appointments_active), 0, blocking=False),
        DependencyCount("invoices", invoices_active, invoices_deleted, blocking=invoices_active > 0),
        DependencyCount("quotes", quotes_active, quotes_deleted, blocking=quotes_active > 0),
        DependencyCount(
            "payments", int(payments_active), 0, blocking=int(payments_active) > 0
        ),
        DependencyCount("special_orders", so_active, so_deleted, blocking=False),
        DependencyCount("event_documents", docs_active, docs_deleted, blocking=False),
    ]

    block_reasons = _block_reasons_from_deps(deps, kind_labels={
        "invoices": "active invoice",
        "quotes": "active quote",
        "payments": "recorded payment",
    })

    sample_titles: dict[str, list[str]] = {}
    if participants_active > 0:
        sample_titles["participants"] = [
            row.display_name
            for row in (
                db.query(EventParticipant.display_name)
                .filter(EventParticipant.event_id == event_id)
                .order_by(EventParticipant.id.asc())
                .limit(3)
                .all()
            )
        ]
    if invoices_active > 0:
        sample_titles["invoices"] = [
            row.invoice_number or f"#{row.id}"
            for row in (
                db.query(Invoice.id, Invoice.invoice_number)
                .filter(Invoice.event_id == event_id)
                .filter(Invoice.deleted_at.is_(None))
                .order_by(Invoice.id.desc())
                .limit(3)
                .all()
            )
        ]

    is_deleted = _is_soft_deleted(event, "events")
    return DependencyReport(
        entity_type="event",
        entity_id=event_id,
        is_currently_deleted=is_deleted,
        can_archive=(not is_deleted) and not block_reasons,
        can_restore=is_deleted,
        block_reasons=block_reasons,
        dependencies=deps,
        sample_titles=sample_titles,
    )


# ---------------------------------------------------------------------------
# Event participant
# ---------------------------------------------------------------------------


def _event_participant_dependencies(
    db: Session, participant_id: int
) -> DependencyReport:
    participant = db.get(EventParticipant, participant_id)
    if participant is None:
        raise RecordNotFoundError("event_participant", participant_id)

    invoices_active = _count(
        db, Invoice, Invoice.event_participant_id == participant_id
    )
    invoices_deleted = _count_deleted(
        db, Invoice, Invoice.event_participant_id == participant_id
    )
    quotes_active = _count(db, Quote, Quote.event_participant_id == participant_id)
    quotes_deleted = _count_deleted(
        db, Quote, Quote.event_participant_id == participant_id
    )

    deps = [
        DependencyCount("invoices", invoices_active, invoices_deleted, blocking=invoices_active > 0),
        DependencyCount("quotes", quotes_active, quotes_deleted, blocking=quotes_active > 0),
    ]

    block_reasons = _block_reasons_from_deps(deps, kind_labels={
        "invoices": "active invoice tied to this participant",
        "quotes": "active quote tied to this participant",
    })

    # Sole-quinceanera invariant: removing the only active quince on an
    # event would leave the event with no celebrant. Block at the
    # product layer.
    if participant.role == "quinceanera" and participant.status == "active":
        sibling_count = (
            db.query(func.count(EventParticipant.id))
            .filter(EventParticipant.event_id == participant.event_id)
            .filter(EventParticipant.id != participant_id)
            .filter(EventParticipant.role == "quinceanera")
            .filter(EventParticipant.status == "active")
            .scalar()
            or 0
        )
        if int(sibling_count) == 0:
            block_reasons.append(
                "sole active quinceañera on this event — promote a "
                "replacement first"
            )

    sample_titles: dict[str, list[str]] = {}
    if invoices_active > 0:
        sample_titles["invoices"] = [
            row.invoice_number or f"#{row.id}"
            for row in (
                db.query(Invoice.id, Invoice.invoice_number)
                .filter(Invoice.event_participant_id == participant_id)
                .filter(Invoice.deleted_at.is_(None))
                .order_by(Invoice.id.desc())
                .limit(3)
                .all()
            )
        ]

    is_deleted = _is_soft_deleted(participant, "event_participants")
    return DependencyReport(
        entity_type="event_participant",
        entity_id=participant_id,
        is_currently_deleted=is_deleted,
        can_archive=(not is_deleted) and not block_reasons,
        can_restore=is_deleted,
        block_reasons=block_reasons,
        dependencies=deps,
        sample_titles=sample_titles,
    )


# ---------------------------------------------------------------------------
# Special order
# ---------------------------------------------------------------------------


def _special_order_dependencies(
    db: Session, special_order_id: int
) -> DependencyReport:
    so = db.get(SpecialOrder, special_order_id)
    if so is None:
        raise RecordNotFoundError("special_order", special_order_id)

    # Special orders are a leaf table — nothing references them. The
    # only related row is the linked invoice line item, which is
    # informational (FK is SET NULL, so archive doesn't endanger
    # anything). Block on the product rule that an "in flight" order
    # should be cancelled-by-status, not archived.
    linked_invoice_line = so.invoice_line_item_id is not None

    deps = [
        DependencyCount(
            "linked_invoice_line",
            active_count=1 if linked_invoice_line else 0,
            deleted_count=0,
            blocking=False,
        ),
    ]

    block_reasons: list[str] = []
    if so.status in ("ordered", "received"):
        block_reasons.append(
            f"order status is {so.status!r} — cancel the order first, "
            "then archive"
        )

    is_deleted = _is_soft_deleted(so, "special_orders")
    return DependencyReport(
        entity_type="special_order",
        entity_id=special_order_id,
        is_currently_deleted=is_deleted,
        can_archive=(not is_deleted) and not block_reasons,
        can_restore=is_deleted,
        block_reasons=block_reasons,
        dependencies=deps,
        sample_titles={},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(db: Session, model, predicate, *, target_table: str | None = None) -> int:
    """Count active rows. For ``target_table`` (one of the four D2
    targets), skip the ``deleted_at`` filter until D2 flips the flag
    in ``_TARGET_TABLES_WITH_DELETED_AT``."""
    q = db.query(func.count(model.id)).filter(predicate)
    if target_table is not None:
        if _TARGET_TABLES_WITH_DELETED_AT.get(target_table):
            q = q.filter(model.deleted_at.is_(None))
    else:
        q = q.filter(model.deleted_at.is_(None))
    return int(q.scalar() or 0)


def _count_deleted(
    db: Session, model, predicate, *, target_table: str | None = None
) -> int:
    """Count soft-deleted rows. Pre-D2 target tables always return 0."""
    if target_table is not None and not _TARGET_TABLES_WITH_DELETED_AT.get(
        target_table
    ):
        return 0
    return int(
        db.query(func.count(model.id))
        .filter(predicate)
        .filter(model.deleted_at.isnot(None))
        .scalar()
        or 0
    )


def _is_soft_deleted(row, table: str) -> bool:
    if table in _TARGET_TABLES_WITH_DELETED_AT and not _TARGET_TABLES_WITH_DELETED_AT[
        table
    ]:
        return False
    return getattr(row, "deleted_at", None) is not None


def _block_reasons_from_deps(
    deps: list[DependencyCount], *, kind_labels: dict[str, str]
) -> list[str]:
    reasons: list[str] = []
    for dep in deps:
        if dep.blocking and dep.active_count > 0 and dep.kind in kind_labels:
            label = kind_labels[dep.kind]
            plural = "s" if dep.active_count != 1 else ""
            reasons.append(f"{dep.active_count} {label}{plural}")
    return reasons
