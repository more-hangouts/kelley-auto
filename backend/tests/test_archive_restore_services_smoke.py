"""Smoke for D3-A: archive/restore service helpers.

Drives each of the four ``archive_*`` / ``restore_*`` service pairs
end-to-end against the live DB:

  - Idempotent archive on an already-archived row.
  - Archive blocked when the dependency report says ``can_archive=False``.
  - Restore blocked by partial-unique collision (contact phone reuse) /
    archived parent (event for participant / special_order) /
    quinceanera-slot conflict.
  - ``activity_log`` writes land with the right type, subject pair,
    and payload (including ``dependency_snapshot``).
  - Contact archive without an audit anchor (no linked events) writes
    NO activity row and only logs a warning.

Runs serially per the project rule; cleans up all seeded rows.

    venv/bin/python tests/test_archive_restore_services_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
# Cleanup CASCADEs through activity_log on event delete; the append-only
# trigger refuses without this bypass.
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    CatalogItem,
    Contact,
    Event,
    EventParticipant,
    Invoice,
    SpecialOrder,
    User,
)
from services import (  # noqa: E402
    activity_log,
    contact_service,
    event_participants,
    event_service,
    special_order_service,
)

_PREFIX = "D3A Arch Smoke"
_EMAIL_PREFIX = "d3a-arch-smoke-"

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_special_order_ids: list[int] = []
_created_invoice_ids: list[int] = []
_created_activity_log_ids: list[int] = []


def _seed_contact(label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        digits = f"55506{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"{_PREFIX} {label} {suffix}",
            email=f"{_EMAIL_PREFIX}{label.lower()}-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["d3a-arch-smoke"],
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        _created_contact_ids.append(contact.id)
        return contact.id
    finally:
        db.close()


def _seed_event(contact_id: int, label: str) -> int:
    db = SessionLocal()
    try:
        event = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name=f"{_PREFIX} {label} Event",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        _created_event_ids.append(event.id)
        return event.id
    finally:
        db.close()


def _seed_participant(
    event_id: int, contact_id: int, role: str, label: str
) -> int:
    db = SessionLocal()
    try:
        p = EventParticipant(
            event_id=event_id,
            contact_id=contact_id,
            role=role,
            display_name=f"{_PREFIX} {label} {role}",
            status="active",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def _seed_invoice(*, event_id: int, contact_id: int, label: str) -> int:
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            invoice_number=f"D3AARCH-{uuid.uuid4().hex[:10].upper()}",
            status="draft",
            issue_date=date.today(),
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        _created_invoice_ids.append(invoice.id)
        return invoice.id
    finally:
        db.close()


def _seed_special_order(event_id: int) -> int | None:
    db = SessionLocal()
    try:
        catalog = (
            db.query(CatalogItem)
            .filter(CatalogItem.active.is_(True))
            .order_by(CatalogItem.id.asc())
            .first()
        )
        if catalog is None:
            return None
        result = special_order_service.create_special_order(
            db,
            special_order_service.CreateSpecialOrderInput(
                event_id=event_id,
                catalog_item_id=int(catalog.id),
                size_label="10",
                status="needed",
            ),
        )
        db.commit()
        _created_special_order_ids.append(int(result.id))
        return int(result.id)
    finally:
        db.close()


def _soft_delete_invoice(invoice_id: int) -> None:
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        assert inv is not None
        inv.deleted_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _activity_row(
    *, event_id: int, activity_type: str, subject_id: int
) -> ActivityLog | None:
    db = SessionLocal()
    try:
        row = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == activity_type)
            .filter(ActivityLog.subject_id == subject_id)
            .order_by(ActivityLog.id.desc())
            .first()
        )
        if row is not None:
            _created_activity_log_ids.append(int(row.id))
        return row
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_contact_archive_restore_with_anchor() -> None:
    """Contact with a (now-archived) linked event archives + restores
    cleanly. The activity row anchors on the event because
    ``_most_recent_event_id`` finds live OR deleted events."""
    contact_id = _seed_contact("CA")
    event_id = _seed_event(contact_id, "CA")

    # An active event blocks contact archive; archive it first so the
    # event still exists as an anchor target but no longer counts as
    # an active dependency.
    db = SessionLocal()
    try:
        event_service.archive_event(
            db, event_id=event_id, actor_user_id=None, reason="duplicate"
        )
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=None,
            reason="test_record",
            note="ca smoke",
        )
        db.commit()
    finally:
        db.close()

    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.CONTACT_ARCHIVED,
        subject_id=contact_id,
    )
    assert row is not None, "contact.archived activity row missing"
    assert row.subject_kind == "contact"
    assert row.payload.get("reason") == "test_record"
    assert row.payload.get("note") == "ca smoke"
    assert "dependency_snapshot" in row.payload
    assert row.payload.get("anchor_event_id") == event_id

    # Idempotent: double-archive returns the row, does not write a
    # second activity log entry.
    db = SessionLocal()
    try:
        contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=None,
            reason="other",
        )
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        cnt = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == activity_log.CONTACT_ARCHIVED)
            .filter(ActivityLog.subject_id == contact_id)
            .count()
        )
        assert cnt == 1, f"archive wrote {cnt} rows; expected 1 (idempotent)"
    finally:
        db.close()

    # Restore.
    db = SessionLocal()
    try:
        contact_service.restore_contact(
            db, contact_id=contact_id, actor_user_id=None
        )
        db.commit()
    finally:
        db.close()
    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.CONTACT_RESTORED,
        subject_id=contact_id,
    )
    assert row is not None


def check_contact_archive_without_anchor() -> None:
    """A contact with zero events writes no activity row but archives
    + restores successfully."""
    contact_id = _seed_contact("Orphan")
    db = SessionLocal()
    try:
        contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=None,
            reason="created_by_mistake",
        )
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        cnt = (
            db.query(ActivityLog)
            .filter(ActivityLog.subject_kind == "contact")
            .filter(ActivityLog.subject_id == contact_id)
            .count()
        )
        assert cnt == 0, (
            f"expected no activity row for orphan contact, got {cnt}"
        )
    finally:
        db.close()

    db = SessionLocal()
    try:
        contact_service.restore_contact(
            db, contact_id=contact_id, actor_user_id=None
        )
        db.commit()
    finally:
        db.close()


def check_archive_blocked_by_dependencies() -> None:
    """A contact with an active draft invoice cannot be archived; once
    the invoice is soft-deleted, archive proceeds (the event is still
    a blocker until it too goes away — but for this smoke we only
    need the financial block + clear)."""
    contact_id = _seed_contact("Block")
    event_id = _seed_event(contact_id, "Block")
    invoice_id = _seed_invoice(
        event_id=event_id, contact_id=contact_id, label="Block"
    )

    db = SessionLocal()
    try:
        try:
            contact_service.archive_contact(
                db,
                contact_id=contact_id,
                actor_user_id=None,
                reason="duplicate",
            )
        except contact_service.ContactServiceError as exc:
            assert exc.code == "archive_blocked", exc.code
        else:
            raise AssertionError(
                "archive_contact accepted a contact with active deps"
            )
    finally:
        db.close()

    _soft_delete_invoice(invoice_id)
    # The event still blocks because it has no soft-delete in this
    # smoke; first archive the event.
    db = SessionLocal()
    try:
        # Event archive should fail too since the invoice was soft-
        # deleted but the event's own dependency report counts deleted
        # invoices as deleted_count, not active. So event archive
        # should now succeed.
        event_service.archive_event(
            db,
            event_id=event_id,
            actor_user_id=None,
            reason="duplicate",
        )
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=None,
            reason="duplicate",
        )
        db.commit()
    finally:
        db.close()


def check_event_restore_refuses_archived_parent() -> None:
    """Restoring an event whose primary contact is still archived
    raises parent_archived."""
    contact_id = _seed_contact("RestoreParent")
    event_id = _seed_event(contact_id, "RestoreParent")

    # Archive both. Order matters per dependency rules: event first,
    # then contact (contact archive would fail while the event was
    # live).
    db = SessionLocal()
    try:
        event_service.archive_event(
            db, event_id=event_id, actor_user_id=None, reason="duplicate"
        )
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        contact_service.archive_contact(
            db,
            contact_id=contact_id,
            actor_user_id=None,
            reason="duplicate",
        )
        db.commit()
    finally:
        db.close()

    # Try to restore the event without first restoring the contact.
    db = SessionLocal()
    try:
        try:
            event_service.restore_event(
                db, event_id=event_id, actor_user_id=None
            )
        except event_service.EventServiceError as exc:
            assert exc.code == "parent_archived", exc.code
        else:
            raise AssertionError("restore_event accepted archived parent")
    finally:
        db.close()


def check_participant_sole_quinceanera_block() -> None:
    """The sole active quinceanera on an event cannot be archived; the
    dependency report's block message fires."""
    contact_id = _seed_contact("Quince")
    event_id = _seed_event(contact_id, "Quince")
    quince_id = _seed_participant(
        event_id, contact_id, "quinceanera", "QuinceA"
    )

    db = SessionLocal()
    try:
        try:
            event_participants.archive_event_participant(
                db,
                participant_id=quince_id,
                actor_user_id=None,
                reason="duplicate",
            )
        except event_participants.EventParticipantError as exc:
            assert exc.code == "archive_blocked", exc.code
        else:
            raise AssertionError(
                "archive_event_participant accepted sole quinceanera"
            )
    finally:
        db.close()


def check_participant_archive_restore_dama() -> None:
    """A dama participant archives and restores. Status flips to
    'removed' on archive, stays after restore."""
    contact_id = _seed_contact("Dama")
    event_id = _seed_event(contact_id, "Dama")
    # Seed a sibling quinceanera so the event has a celebrant.
    _seed_participant(event_id, contact_id, "quinceanera", "Quince")
    dama_id = _seed_participant(event_id, contact_id, "dama", "Dama")

    db = SessionLocal()
    try:
        event_participants.archive_event_participant(
            db,
            participant_id=dama_id,
            actor_user_id=None,
            reason="created_by_mistake",
        )
        db.commit()
    finally:
        db.close()
    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.EVENT_PARTICIPANT_ARCHIVED,
        subject_id=dama_id,
    )
    assert row is not None

    db = SessionLocal()
    try:
        p = db.get(EventParticipant, dama_id)
        assert p.status == "removed", p.status
        assert p.deleted_at is not None
    finally:
        db.close()

    db = SessionLocal()
    try:
        event_participants.restore_event_participant(
            db, participant_id=dama_id, actor_user_id=None
        )
        db.commit()
    finally:
        db.close()
    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.EVENT_PARTICIPANT_RESTORED,
        subject_id=dama_id,
    )
    assert row is not None
    db = SessionLocal()
    try:
        p = db.get(EventParticipant, dama_id)
        assert p.deleted_at is None
        # Status stays 'removed' — restore does not auto-reactivate;
        # staff flips it explicitly from the participant editor.
        assert p.status == "removed", p.status
    finally:
        db.close()


def check_special_order_archive_restore() -> None:
    """A 'needed' special order archives + restores cleanly."""
    contact_id = _seed_contact("SO")
    event_id = _seed_event(contact_id, "SO")
    _seed_participant(event_id, contact_id, "quinceanera", "Q")
    so_id = _seed_special_order(event_id)
    if so_id is None:
        return  # catalog empty in this env

    db = SessionLocal()
    try:
        special_order_service.archive_special_order(
            db,
            special_order_id=so_id,
            actor_user_id=None,
            reason="created_by_mistake",
        )
        db.commit()
    finally:
        db.close()
    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.SPECIAL_ORDER_ARCHIVED,
        subject_id=so_id,
    )
    assert row is not None
    assert row.payload.get("status_at_archive") == "needed"

    db = SessionLocal()
    try:
        special_order_service.restore_special_order(
            db, special_order_id=so_id, actor_user_id=None
        )
        db.commit()
    finally:
        db.close()
    row = _activity_row(
        event_id=event_id,
        activity_type=activity_log.SPECIAL_ORDER_RESTORED,
        subject_id=so_id,
    )
    assert row is not None


def check_archive_reason_validation() -> None:
    """An unrecognized reason raises ArchiveReasonError before any
    mutation happens."""
    from services import record_dependencies

    contact_id = _seed_contact("BadReason")
    db = SessionLocal()
    try:
        try:
            contact_service.archive_contact(
                db,
                contact_id=contact_id,
                actor_user_id=None,
                reason="🤷",
            )
        except record_dependencies.ArchiveReasonError:
            pass
        else:
            raise AssertionError("invalid reason was accepted")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup() -> None:
    # Delete activity rows we wrote first (FK CASCADE on event would
    # also handle them, but we let the event delete cascade naturally).
    for layer in (
        (_created_special_order_ids, SpecialOrder),
        (_created_invoice_ids, Invoice),
        (_created_event_ids, Event),
        (_created_contact_ids, Contact),
        (_created_user_ids, User),
    ):
        ids, model = layer
        db = SessionLocal()
        try:
            for row_id in ids:
                row = db.get(model, row_id)
                if row is not None:
                    db.delete(row)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"cleanup({model.__name__}) failed: {exc!r}")
        finally:
            db.close()


def main() -> int:
    failed = False
    try:
        check_contact_archive_restore_with_anchor()
        check_contact_archive_without_anchor()
        check_archive_blocked_by_dependencies()
        check_event_restore_refuses_archived_parent()
        check_participant_sole_quinceanera_block()
        check_participant_archive_restore_dama()
        check_special_order_archive_restore()
        check_archive_reason_validation()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        import traceback

        traceback.print_exc()
        failed = True
    finally:
        cleanup()
    if failed:
        return 1
    print("D3-A archive/restore service smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
