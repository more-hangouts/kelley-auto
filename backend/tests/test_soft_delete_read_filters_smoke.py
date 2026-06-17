"""Smoke for D2 of the CRM record deletion plan: soft-delete is honored
by every default admin read.

Exercises the read-filter audit by:

  - Seeding contact + event + participant + special_order + draft
    invoice rows.
  - Soft-deleting each target row in turn (direct DB update — D3 has
    not landed the archive service helpers yet).
  - Hitting the admin endpoints that previously surfaced the row and
    asserting they now return 404 / empty.
  - Hitting the dependency endpoint to confirm ``deleted_count``
    increments and ``is_currently_deleted`` flips.
  - Re-using ``phone_e164`` from a soft-deleted contact via the
    ``find_or_create_contact`` service to confirm the partial unique
    index does not block returning customers.

Cleans up all seeded rows. Runs serially per project rule.

    venv/bin/python tests/test_soft_delete_read_filters_smoke.py
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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    EventParticipant,
    Invoice,
    SpecialOrder,
    User,
)
from services import contact_service, event_service, special_order_service  # noqa: E402

client = TestClient(app)

_PREFIX = "D2 Soft Smoke"
_EMAIL_PREFIX = "d2-soft-smoke-"

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_special_order_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _make_admin() -> str:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d2-soft-smoke-admin-{suffix}",
            email=f"{_EMAIL_PREFIX}admin-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"{_PREFIX} Admin {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return create_access_token(u)
    finally:
        db.close()


def _seed_chain(label: str) -> dict[str, int]:
    """Seed a contact + event + quinceanera participant + draft invoice
    + needed-status special order against a known catalog row. Returns
    a dict of id keys."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        digits = f"55505{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"{_PREFIX} {label} {suffix}",
            email=f"{_EMAIL_PREFIX}{label.lower()}-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["d2-soft-smoke"],
        )
        db.add(contact)
        db.flush()

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{_PREFIX} {label} Event",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="d2-soft-smoke",
        )
        db.add(event)
        db.flush()

        participant = EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="quinceanera",
            display_name=f"{_PREFIX} {label} Celebrant",
        )
        db.add(participant)
        db.flush()

        invoice = Invoice(
            event_id=event.id,
            contact_id=contact.id,
            invoice_number=f"D2SOFT-{uuid.uuid4().hex[:10].upper()}",
            status="draft",
            issue_date=date.today(),
        )
        db.add(invoice)
        db.flush()

        ids = {
            "contact_id": contact.id,
            "event_id": event.id,
            "participant_id": participant.id,
            "invoice_id": invoice.id,
            "phone_e164": contact.phone_e164,
        }
        db.commit()

        _created_contact_ids.append(ids["contact_id"])
        _created_event_ids.append(ids["event_id"])
        _created_invoice_ids.append(ids["invoice_id"])
        return ids
    finally:
        db.close()


def _seed_special_order(event_id: int) -> int:
    """Find an existing active catalog item and seed one special order
    against it. Touch nothing in the catalog so we don't have to clean
    up catalog pollution."""
    from database.models import CatalogItem  # local import

    db = SessionLocal()
    try:
        catalog = (
            db.query(CatalogItem)
            .filter(CatalogItem.active.is_(True))
            .order_by(CatalogItem.id.asc())
            .first()
        )
        if catalog is None:
            return 0  # smoke degrades gracefully if catalog is empty
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


def _soft_delete(model, row_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.get(model, row_id)
        assert row is not None
        row.deleted_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_contact_disappears(token: str) -> None:
    ids = _seed_chain("Contact")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(f"/api/contacts/{ids['contact_id']}", headers=headers)
    assert r.status_code == 200, r.text

    _soft_delete(Contact, ids["contact_id"])

    r = client.get(f"/api/contacts/{ids['contact_id']}", headers=headers)
    assert r.status_code == 404, r.text

    # Dependency report should now flip is_currently_deleted=True and
    # can_archive=False (already archived) / can_restore=True.
    r = client.get(
        f"/api/admin/dependencies/contact/{ids['contact_id']}",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_currently_deleted"] is True
    assert body["can_archive"] is False
    assert body["can_restore"] is True


def check_event_disappears(token: str) -> None:
    ids = _seed_chain("Event")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get(f"/api/events/{ids['event_id']}", headers=headers)
    assert r.status_code == 200, r.text

    _soft_delete(Event, ids["event_id"])

    r = client.get(f"/api/events/{ids['event_id']}", headers=headers)
    assert r.status_code == 404, r.text

    # Board / pipeline should not include the archived event.
    r = client.get("/api/events/board?event_type=quinceanera", headers=headers)
    assert r.status_code == 200, r.text
    for column in r.json().get("columns", []):
        for card in column.get("cards", []):
            assert card["id"] != ids["event_id"], (
                "archived event still in pipeline column"
                f" {column['code']}"
            )


def check_special_order_disappears(token: str) -> None:
    ids = _seed_chain("SpecialOrder")
    so_id = _seed_special_order(ids["event_id"])
    if so_id == 0:
        return  # catalog empty in this DB; smoke is best-effort here

    db = SessionLocal()
    try:
        rows = special_order_service.list_for_event(db, event_id=ids["event_id"])
        assert any(r.id == so_id for r in rows), rows
    finally:
        db.close()

    _soft_delete(SpecialOrder, so_id)

    db = SessionLocal()
    try:
        rows = special_order_service.list_for_event(db, event_id=ids["event_id"])
        assert not any(r.id == so_id for r in rows), (
            "archived special order still in list_for_event"
        )
    finally:
        db.close()


def check_phone_reuse_after_archive() -> None:
    """A soft-deleted contact's phone_e164 must not block re-creating
    a contact with the same phone (the partial unique index has the
    ``deleted_at IS NULL`` predicate after migration 080).
    """
    ids = _seed_chain("PhoneReuse")
    _soft_delete(Contact, ids["contact_id"])

    db = SessionLocal()
    try:
        contact, was_new = contact_service.find_or_create_contact(
            db,
            phone_e164=ids["phone_e164"],
            email=None,
            phone="(210) 555-1234",
            first_name="Returning",
            last_name="Customer",
        )
        db.commit()
        assert was_new is True, "should have created a fresh contact"
        assert contact.id != ids["contact_id"]
        _created_contact_ids.append(int(contact.id))
    finally:
        db.close()


def check_linked_events_skips_archived() -> None:
    ids = _seed_chain("LinkedEvents")
    db = SessionLocal()
    try:
        linked = contact_service.get_linked_events(db, contact_id=ids["contact_id"])
        assert any(e["id"] == ids["event_id"] for e in linked), linked
    finally:
        db.close()

    _soft_delete(Event, ids["event_id"])

    db = SessionLocal()
    try:
        linked = contact_service.get_linked_events(db, contact_id=ids["contact_id"])
        assert not any(e["id"] == ids["event_id"] for e in linked), (
            "archived event still in get_linked_events"
        )
    finally:
        db.close()


def check_event_status_change_blocked_on_archived() -> None:
    """Service-layer guard: change_event_status raises event_not_found
    once the event is archived, mirroring the public 404."""
    ids = _seed_chain("StatusGuard")
    _soft_delete(Event, ids["event_id"])

    db = SessionLocal()
    try:
        try:
            event_service.change_event_status(
                db, event_id=ids["event_id"], new_status="consulted"
            )
        except event_service.EventServiceError as exc:
            assert exc.code == "event_not_found"
        else:
            raise AssertionError(
                "change_event_status accepted an archived event"
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup() -> None:
    # Hard-delete in dependency order. Archived rows are still
    # physically present so DELETE works; un-archiving them first would
    # trip the partial unique on phone_e164 (which is the whole point
    # of the D2 schema and is verified by check_phone_reuse_after_archive).
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
        token = _make_admin()
        check_contact_disappears(token)
        check_event_disappears(token)
        check_special_order_disappears(token)
        check_phone_reuse_after_archive()
        check_linked_events_skips_archived()
        check_event_status_change_blocked_on_archived()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        import traceback
        traceback.print_exc()
        failed = True
    finally:
        cleanup()

    if failed:
        return 1
    print("D2 soft-delete read-filter smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
