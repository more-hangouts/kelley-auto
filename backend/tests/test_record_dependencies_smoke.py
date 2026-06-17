"""Smoke for D1 of the CRM Record Deletion plan: dependency preview.

Exercises the read-only dependency report end-to-end:

  - Service-level: ``get_record_dependencies`` returns the right
    shape for ``contact`` and ``event``, including zero-dep,
    mixed active/deleted (via soft-deleting an invoice), and
    blocking financial cases.
  - Router-level: ``GET /api/admin/dependencies/{entity_type}/{id}``
    returns 200 with the expected JSON, 400 on bad ``entity_type``,
    404 on missing id, and 401/403 when called without admin auth.

Pre-D2 the four target tables (contacts, events, event_participants,
special_orders) have no ``deleted_at`` column, so ``deleted_count``
for those kinds is always 0 in this smoke. Once D2 ships and the
``_TARGET_TABLES_WITH_DELETED_AT`` flags flip, follow-up tests cover
the deleted-target case.

Runs serially per the project rule. Cleans up its seeded rows.

    venv/bin/python tests/test_record_dependencies_smoke.py
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
    Invoice,
    User,
)
from services import record_dependencies  # noqa: E402

client = TestClient(app)


# Name prefixes — must match cleanup_admin_smoke_pollution.sql entries.
_PREFIX = "D1 Dep Smoke"
_EMAIL_PREFIX = "d1-dep-smoke-"


_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d1-dep-smoke-admin-{suffix}",
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
        return u.id, create_access_token(u)
    finally:
        db.close()


def _seed_contact(label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        digits = f"55504{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"{_PREFIX} {label} {suffix}",
            email=f"{_EMAIL_PREFIX}{label.lower()}-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["d1-dep-smoke"],
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
            notes="d1-dep-smoke",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        _created_event_ids.append(event.id)
        return event.id
    finally:
        db.close()


def _seed_invoice(*, event_id: int, contact_id: int, label: str) -> int:
    db = SessionLocal()
    try:
        invoice = Invoice(
            event_id=event_id,
            contact_id=contact_id,
            invoice_number=f"D1DEP-{uuid.uuid4().hex[:10].upper()}",
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


def _soft_delete_invoice(invoice_id: int) -> None:
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        assert inv is not None
        inv.deleted_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_service_zero_deps() -> None:
    """A fresh contact with no events/invoices/quotes/payments has no
    blocking dependencies and is archivable."""
    contact_id = _seed_contact("Empty")
    db = SessionLocal()
    try:
        report = record_dependencies.get_record_dependencies(
            db, entity_type="contact", entity_id=contact_id
        )
    finally:
        db.close()

    assert report.entity_id == contact_id
    assert report.is_currently_deleted is False
    assert report.can_archive is True
    assert report.can_restore is False
    assert report.block_reasons == []
    kinds = {d.kind: d for d in report.dependencies}
    assert {"events", "event_participants", "appointments", "invoices",
            "quotes", "payments"} <= set(kinds)
    for d in report.dependencies:
        assert d.active_count == 0, f"{d.kind} active={d.active_count}"
        assert d.deleted_count == 0
        assert d.blocking is False


def check_service_blocking_financial() -> None:
    """A contact with an active draft invoice → blocked, not archivable.
    Soft-delete the invoice → blocking clears."""
    contact_id = _seed_contact("Block")
    event_id = _seed_event(contact_id, "Block")
    invoice_id = _seed_invoice(
        event_id=event_id, contact_id=contact_id, label="Block"
    )

    db = SessionLocal()
    try:
        report = record_dependencies.get_record_dependencies(
            db, entity_type="contact", entity_id=contact_id
        )
    finally:
        db.close()

    assert report.can_archive is False
    assert any("active invoice" in r for r in report.block_reasons), report.block_reasons
    assert any("linked event" in r for r in report.block_reasons), report.block_reasons

    invoices_dep = next(d for d in report.dependencies if d.kind == "invoices")
    assert invoices_dep.active_count == 1
    assert invoices_dep.deleted_count == 0
    assert invoices_dep.blocking is True

    # Mixed active/deleted: soft-delete the invoice; the deleted_count
    # bumps to 1, active drops to 0, the invoice no longer contributes
    # to block_reasons.
    _soft_delete_invoice(invoice_id)
    db = SessionLocal()
    try:
        report2 = record_dependencies.get_record_dependencies(
            db, entity_type="contact", entity_id=contact_id
        )
    finally:
        db.close()
    invoices_dep2 = next(d for d in report2.dependencies if d.kind == "invoices")
    assert invoices_dep2.active_count == 0
    assert invoices_dep2.deleted_count == 1
    assert invoices_dep2.blocking is False
    # event still blocks (the event has no deleted_at column yet)
    assert any("linked event" in r for r in report2.block_reasons)


def check_service_event_report() -> None:
    """Event report counts the invoice on it and lists sample titles."""
    contact_id = _seed_contact("Evt")
    event_id = _seed_event(contact_id, "Evt")
    invoice_id = _seed_invoice(event_id=event_id, contact_id=contact_id, label="Evt")

    db = SessionLocal()
    try:
        report = record_dependencies.get_record_dependencies(
            db, entity_type="event", entity_id=event_id
        )
    finally:
        db.close()

    invoices_dep = next(d for d in report.dependencies if d.kind == "invoices")
    assert invoices_dep.active_count == 1
    assert report.can_archive is False
    assert "invoices" in report.sample_titles
    assert len(report.sample_titles["invoices"]) == 1


def check_service_not_found() -> None:
    db = SessionLocal()
    try:
        try:
            record_dependencies.get_record_dependencies(
                db, entity_type="contact", entity_id=999_999_999
            )
        except record_dependencies.RecordNotFoundError as exc:
            assert exc.entity_type == "contact"
        else:
            raise AssertionError("expected RecordNotFoundError")
    finally:
        db.close()


def check_service_unsupported_type() -> None:
    db = SessionLocal()
    try:
        try:
            record_dependencies.get_record_dependencies(
                db, entity_type="invoice", entity_id=1
            )
        except record_dependencies.UnsupportedEntityTypeError as exc:
            assert exc.entity_type == "invoice"
        else:
            raise AssertionError("expected UnsupportedEntityTypeError")
    finally:
        db.close()


def check_endpoint_response_shape(token: str) -> None:
    """GET /api/admin/dependencies/contact/{id} returns the documented JSON."""
    contact_id = _seed_contact("Endpoint")
    event_id = _seed_event(contact_id, "Endpoint")
    _seed_invoice(event_id=event_id, contact_id=contact_id, label="Endpoint")

    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(
        f"/api/admin/dependencies/contact/{contact_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_keys = {
        "entity_type",
        "entity_id",
        "is_currently_deleted",
        "can_archive",
        "can_restore",
        "block_reasons",
        "dependencies",
        "sample_titles",
    }
    assert expected_keys <= set(body), body
    assert body["entity_type"] == "contact"
    assert body["entity_id"] == contact_id
    assert isinstance(body["dependencies"], list)
    assert all(
        {"kind", "active_count", "deleted_count", "blocking"} <= set(d)
        for d in body["dependencies"]
    )


def check_endpoint_bad_entity_type(token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/admin/dependencies/invoice/1", headers=headers)
    assert resp.status_code == 400, resp.text


def check_endpoint_not_found(token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get(
        "/api/admin/dependencies/contact/999999999", headers=headers
    )
    assert resp.status_code == 404, resp.text


def check_endpoint_requires_auth() -> None:
    resp = client.get("/api/admin/dependencies/contact/1")
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup() -> None:
    # Explicit per-layer commits so a partial-test failure can still
    # finish cleanup — events must clear before their parent contacts,
    # invoices before their parent events.
    for layer in (
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
        _, token = _make_admin()
        check_service_zero_deps()
        check_service_blocking_financial()
        check_service_event_report()
        check_service_not_found()
        check_service_unsupported_type()
        check_endpoint_response_shape(token)
        check_endpoint_bad_entity_type(token)
        check_endpoint_not_found(token)
        check_endpoint_requires_auth()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        import traceback

        traceback.print_exc()
        failed = True
    finally:
        cleanup()

    if failed:
        return 1
    print("D1 record-dependencies smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
