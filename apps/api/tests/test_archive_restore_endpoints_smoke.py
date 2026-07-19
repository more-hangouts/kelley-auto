"""Smoke for D3-B: admin archive / restore endpoints.

Exercises the eight new POST routes under ``/api/admin``:

  - Valid archive + restore round-trip per entity type.
  - 400 on invalid reason.
  - 404 on missing entity.
  - 404 on nested route parent-id mismatch (participant / special order
    URL claims a different event_id than the row).
  - 409 on dependency-blocked archive and parent-archived restore.
  - 401 / 403 without admin auth.

Runs serially. Cleans up.

    venv/bin/python tests/test_archive_restore_endpoints_smoke.py
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    CatalogItem,
    Contact,
    Event,
    EventParticipant,
    Invoice,
    SpecialOrder,
    User,
)
from services import special_order_service  # noqa: E402

client = TestClient(app)

_PREFIX = "D3B Arch Ep Smoke"
_EMAIL_PREFIX = "d3b-arch-ep-smoke-"

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
            username=f"d3b-arch-ep-smoke-admin-{suffix}",
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
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        digits = f"55507{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"{_PREFIX} {label} {suffix}",
            email=f"{_EMAIL_PREFIX}{label.lower()}-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
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
        )
        db.add(event)
        db.flush()
        # Seed a quinceanera and a dama so participant archive can
        # target the dama (sole-quince block doesn't fire).
        quince = EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="quinceanera",
            display_name=f"{_PREFIX} {label} Q",
            status="active",
        )
        dama = EventParticipant(
            event_id=event.id,
            contact_id=contact.id,
            role="dama",
            display_name=f"{_PREFIX} {label} D",
            status="active",
        )
        db.add_all([quince, dama])
        db.flush()
        ids = {
            "contact_id": contact.id,
            "event_id": event.id,
            "quince_id": quince.id,
            "dama_id": dama.id,
        }
        db.commit()
        _created_contact_ids.append(ids["contact_id"])
        _created_event_ids.append(ids["event_id"])
        return ids
    finally:
        db.close()


def _seed_invoice(*, event_id: int, contact_id: int) -> int:
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            invoice_number=f"D3BARCH-{uuid.uuid4().hex[:10].upper()}",
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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_archive_requires_auth() -> None:
    r = client.post(
        "/api/admin/contacts/1/archive",
        json={"reason": "duplicate"},
    )
    assert r.status_code in (401, 403), r.text


def check_invalid_reason(token: str) -> None:
    ids = _seed_chain("InvalidReason")
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/archive",
        json={"reason": "🤷"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "invalid_reason"


def check_event_archive_restore(token: str) -> None:
    ids = _seed_chain("EventRT")
    h = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/api/admin/events/{ids['event_id']}/archive",
        json={"reason": "duplicate", "note": "ep smoke"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "event"
    assert body["entity_id"] == ids["event_id"]
    assert body["deleted_at"] is not None
    assert body["activity_logged"] is True

    # Detail endpoint now 404s.
    r = client.get(f"/api/events/{ids['event_id']}", headers=h)
    assert r.status_code == 404, r.text

    # Restore.
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/restore",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted_at"] is None
    r = client.get(f"/api/events/{ids['event_id']}", headers=h)
    assert r.status_code == 200, r.text


def check_contact_archive_blocked_by_event(token: str) -> None:
    ids = _seed_chain("ContactBlock")
    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        f"/api/admin/contacts/{ids['contact_id']}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "archive_blocked"


def check_event_restore_blocked_by_archived_parent(token: str) -> None:
    ids = _seed_chain("EvtParentBlock")
    h = {"Authorization": f"Bearer {token}"}
    # Archive event then contact via API so the test exercises the
    # endpoints, not the service.
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/admin/contacts/{ids['contact_id']}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # Try to restore the event while contact is archived.
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/restore",
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "parent_archived"


def check_participant_archive_restore(token: str) -> None:
    ids = _seed_chain("PartRT")
    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/participants/{ids['dama_id']}/archive",
        json={"reason": "created_by_mistake"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["entity_type"] == "event_participant"

    r = client.post(
        f"/api/admin/events/{ids['event_id']}/participants/{ids['dama_id']}/restore",
        headers=h,
    )
    assert r.status_code == 200, r.text


def check_participant_parent_mismatch_404(token: str) -> None:
    """A participant URL pointing at the wrong event_id must 404, not
    act on a different event's participant."""
    ids_a = _seed_chain("Mis1")
    ids_b = _seed_chain("Mis2")
    h = {"Authorization": f"Bearer {token}"}

    # ids_a participant_id under ids_b event_id should 404.
    r = client.post(
        f"/api/admin/events/{ids_b['event_id']}/participants/{ids_a['dama_id']}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "participant_not_found"

    # The real participant should still be archivable under its own
    # event.
    r = client.post(
        f"/api/admin/events/{ids_a['event_id']}/participants/{ids_a['dama_id']}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 200, r.text


def check_special_order_archive_restore(token: str) -> None:
    ids = _seed_chain("SOEp")
    so_id = _seed_special_order(ids["event_id"])
    if so_id is None:
        return  # catalog empty in this env
    h = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/api/admin/events/{ids['event_id']}/special-orders/{so_id}/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # Wrong event in URL → 404.
    other_ids = _seed_chain("SOEpMis")
    r = client.post(
        f"/api/admin/events/{other_ids['event_id']}/special-orders/{so_id}/restore",
        headers=h,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "special_order_not_found"

    # Correct event → 200.
    r = client.post(
        f"/api/admin/events/{ids['event_id']}/special-orders/{so_id}/restore",
        headers=h,
    )
    assert r.status_code == 200, r.text


def check_contact_archive_restore_orphan(token: str) -> None:
    """A contact with no events archives + restores via the endpoints.
    activity_logged in the response is False (no event anchor)."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        contact = Contact(
            display_name=f"{_PREFIX} Orphan {suffix}",
            email=f"{_EMAIL_PREFIX}orphan-{suffix}@example.com",
        )
        db.add(contact)
        db.commit()
        _created_contact_ids.append(int(contact.id))
        cid = int(contact.id)
    finally:
        db.close()

    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        f"/api/admin/contacts/{cid}/archive",
        json={"reason": "created_by_mistake"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_at"] is not None
    assert body["activity_logged"] is False

    r = client.post(
        f"/api/admin/contacts/{cid}/restore",
        headers=h,
    )
    assert r.status_code == 200, r.text


def check_not_found_returns_404(token: str) -> None:
    h = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/api/admin/contacts/999999999/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "contact_not_found"

    r = client.post(
        "/api/admin/events/999999999/archive",
        json={"reason": "duplicate"},
        headers=h,
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup() -> None:
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
        check_archive_requires_auth()
        token = _make_admin()
        check_invalid_reason(token)
        check_event_archive_restore(token)
        check_contact_archive_blocked_by_event(token)
        check_event_restore_blocked_by_archived_parent(token)
        check_participant_archive_restore(token)
        check_participant_parent_mismatch_404(token)
        check_special_order_archive_restore(token)
        check_contact_archive_restore_orphan(token)
        check_not_found_returns_404(token)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        import traceback
        traceback.print_exc()
        failed = True
    finally:
        cleanup()
    if failed:
        return 1
    print("D3-B archive/restore endpoints smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
