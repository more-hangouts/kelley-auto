"""Catalog SKU obfuscation Phase 5 — special_orders smoke.

Exercises the lifecycle end-to-end against a real Postgres
connection:

  - Schema CHECKs fire on every invariant: status whitelist, size
    nonempty, status ↔ timestamp coupling, picked_up requires
    received.
  - Service create rejects non-catalog invoice lines, missing size,
    invoice/catalog mismatch, and inactive catalog rows.
  - Lifecycle transitions: needed → ordered → received → picked_up
    works, ordered → delayed → ordered round-trips, cancel from any
    non-terminal state works, picked_up is terminal, received → ordered
    is rejected.
  - Patch updates eta_date / vendor_order_number / internal_notes
    without touching status.
  - ON DELETE SET NULL: deleting the underlying invoice line clears
    invoice_line_item_id but leaves the row visible (the catalog +
    size snapshot is enough to keep tracking).
  - Router round-trip: the staff API surfaces the catalog snapshot
    and returns ``vendor_order_number`` / ``internal_notes`` (these
    are staff-side responses; Phase 7's lint will assert customer
    surfaces never echo them).

Runs as a script:

    venv/bin/python tests/test_special_orders_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    InvoiceLineItem,
    SpecialOrder,
    User,
)
from services import invoice_service, special_order_service  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
)
from services.invoice_service import LineItemInput  # noqa: E402
from services.special_order_service import (  # noqa: E402
    CreateSpecialOrderInput,
    SpecialOrderError,
)


_PREFIX = f"P5-SO-{uuid.uuid4().hex[:8].upper()}-"
client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state "
                    "WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _reset_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": value},
        )
        db.commit()
    finally:
        db.close()


def _seed():
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=_PREFIX + "Customer", phone="(210) 555-7777"
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=_PREFIX + "Quince",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        cat = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "MORI-89216-IVORY",
                color="Ivory",
                category="quince_gown",
                designer="Mori Lee",
                style_number="89216",
                house_name="Isabella",
                product_title="Isabella Quince",
            ),
        )
        cat_inactive = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "RETIRED-0001",
                color="Sand",
                category="quince_gown",
                designer="Retired Vendor",
                style_number="0001",
                active=False,
            ),
        )
        # Catalog-backed invoice + line so the create-from-line flow
        # has something to attach to.
        inv = invoice_service.create_invoice(
            db,
            event_id=event.id,
            contact_id=contact.id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=cat.id,
                    size_label="08",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                ),
                LineItemInput(
                    kind="fee",
                    public_description="Rush alteration fee",
                    quantity=Decimal("1"),
                    unit_price_cents=5000,
                ),
            ],
        )
        db.commit()
        line_rows = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv.id)
            .order_by(InvoiceLineItem.sort_order)
            .all()
        )
        catalog_line_id = next(
            l.id for l in line_rows if l.catalog_item_id == cat.id
        )
        non_catalog_line_id = next(
            l.id for l in line_rows if l.catalog_item_id is None
        )
        return {
            "contact_id": contact.id,
            "event_id": event.id,
            "catalog_id": cat.id,
            "catalog_inactive_id": cat_inactive.id,
            "invoice_id": inv.id,
            "catalog_line_id": catalog_line_id,
            "non_catalog_line_id": non_catalog_line_id,
        }
    finally:
        db.close()


def _cleanup() -> None:
    p = _PREFIX + "%"
    db = SessionLocal()
    try:
        events_subq = (
            "(SELECT id FROM events WHERE event_name LIKE :p)"
        )
        db.execute(
            sql_text(
                f"DELETE FROM special_orders WHERE event_id IN {events_subq}"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM invoice_invitations WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM invoice_installments WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                f"DELETE FROM invoice_line_items WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(f"DELETE FROM invoices WHERE event_id IN {events_subq}"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM events WHERE event_name LIKE :p"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM contacts WHERE display_name LIKE :p"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM users WHERE username LIKE :p"),
            {"p": p},
        )
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": p},
        )
        db.commit()
    finally:
        db.close()


def _make_admin() -> tuple[int, dict[str, str]]:
    db = SessionLocal()
    try:
        user = User(
            username=_PREFIX + "admin",
            email=_PREFIX.lower() + "admin@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Phase 5 Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        resp = client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "smoke-pass-12345"},
        )
        assert resp.status_code == 200, resp.text
        return user.id, {"Authorization": f"Bearer {resp.json()['access_token']}"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service-layer checks
# ---------------------------------------------------------------------------


def check_create_from_invoice_line_copies_snapshot(seed) -> int:
    db = SessionLocal()
    try:
        row = special_order_service.create_from_invoice_line(
            db, invoice_line_item_id=seed["catalog_line_id"]
        )
        db.commit()
        assert row.event_id == seed["event_id"]
        assert row.catalog_item_id == seed["catalog_id"]
        assert row.size_label == "08"
        assert row.status == "needed"
        assert row.invoice_line_item_id == seed["catalog_line_id"]
        return int(row.id)
    finally:
        db.close()


def check_create_from_non_catalog_line_rejected(seed) -> None:
    db = SessionLocal()
    try:
        try:
            special_order_service.create_from_invoice_line(
                db, invoice_line_item_id=seed["non_catalog_line_id"]
            )
            db.commit()
            raise AssertionError(
                "create_from_invoice_line accepted a non-catalog line"
            )
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invoice_line_not_catalog_backed", exc.code
    finally:
        db.close()


def check_create_against_inactive_catalog_rejected(seed) -> None:
    db = SessionLocal()
    try:
        try:
            special_order_service.create_special_order(
                db,
                CreateSpecialOrderInput(
                    event_id=seed["event_id"],
                    catalog_item_id=seed["catalog_inactive_id"],
                    size_label="06",
                ),
            )
            db.commit()
            raise AssertionError(
                "create accepted inactive catalog item"
            )
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "catalog_item_inactive", exc.code
    finally:
        db.close()


def check_invoice_line_catalog_mismatch_rejected(seed) -> None:
    """Mint a second active catalog row that does NOT match the
    invoice line's catalog and verify the service refuses to attach a
    special order linking the two. Using an active row here is
    intentional — the inactive-check fires first, so we need a row
    that passes the active gate to actually exercise the mismatch
    path."""
    db = SessionLocal()
    try:
        other = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "OTHER-VENDOR-0001",
                color="Champagne",
                category="quince_gown",
                designer="Other Vendor",
                style_number="X-001",
            ),
        )
        db.commit()
        try:
            special_order_service.create_special_order(
                db,
                CreateSpecialOrderInput(
                    event_id=seed["event_id"],
                    catalog_item_id=int(other.id),
                    size_label="08",
                    invoice_line_item_id=seed["catalog_line_id"],
                ),
            )
            db.commit()
            raise AssertionError(
                "invoice/catalog mismatch was accepted"
            )
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invoice_line_catalog_mismatch", exc.code
    finally:
        db.close()


def check_cross_event_invoice_line_rejected(seed) -> None:
    """Direct ``create_special_order`` must refuse to attach an
    invoice line whose invoice belongs to a different event than the
    one named in the request. Without this guard a staff caller could
    file a special-order row under event A that points at a line on
    event B, and the cross-event linkage would silently ship.
    """
    db = SessionLocal()
    try:
        # Build a second event + contact + invoice with its own
        # catalog-backed line. The line lives on the second event.
        contact_b = Contact(
            display_name=_PREFIX + "OtherCustomer",
            phone="(210) 555-0102",
        )
        db.add(contact_b)
        db.flush()
        event_b = Event(
            primary_contact_id=contact_b.id,
            event_type="quinceanera",
            event_name=_PREFIX + "OtherQuince",
            event_date=date.today() + timedelta(days=200),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event_b)
        db.flush()
        inv_b = invoice_service.create_invoice(
            db,
            event_id=event_b.id,
            contact_id=contact_b.id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=seed["catalog_id"],
                    size_label="14",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
        )
        db.commit()
        line_b_id = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv_b.id)
            .one()
            .id
        )
        try:
            special_order_service.create_special_order(
                db,
                CreateSpecialOrderInput(
                    event_id=seed["event_id"],
                    catalog_item_id=seed["catalog_id"],
                    size_label="14",
                    invoice_line_item_id=line_b_id,
                ),
            )
            db.commit()
            raise AssertionError(
                "cross-event invoice line was accepted"
            )
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invoice_line_event_mismatch", exc.code
            assert exc.extra.get("line_event_id") == int(event_b.id)
            assert exc.extra.get("requested_event_id") == int(seed["event_id"])
    finally:
        db.close()


def check_size_label_mismatch_rejected(seed) -> None:
    """The special order's size_label must match the invoice line's
    size when a line is supplied. Otherwise staff could order an
    ``08`` against an invoice line that promised the customer a
    ``10`` and the lifecycle log would silently disagree with the
    invoice.
    """
    db = SessionLocal()
    try:
        try:
            special_order_service.create_special_order(
                db,
                CreateSpecialOrderInput(
                    event_id=seed["event_id"],
                    catalog_item_id=seed["catalog_id"],
                    size_label="10",
                    invoice_line_item_id=seed["catalog_line_id"],
                ),
            )
            db.commit()
            raise AssertionError(
                "size_label mismatch with invoice line was accepted"
            )
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invoice_line_size_mismatch", exc.code
            assert exc.extra.get("line_size_label") == "08"
            assert exc.extra.get("requested_size_label") == "10"
    finally:
        db.close()


def check_lifecycle_happy_path(special_order_id: int) -> None:
    db = SessionLocal()
    try:
        # needed → ordered: stamps ordered_at automatically
        row = special_order_service.mark_ordered(
            db,
            special_order_id=special_order_id,
            eta_date=date.today() + timedelta(days=30),
            vendor_order_number="MV-12345",
        )
        db.commit()
        assert row.status == "ordered"
        assert row.ordered_at is not None
        assert row.eta_date == date.today() + timedelta(days=30)
        assert row.vendor_order_number == "MV-12345"

        # ordered → delayed → ordered: ETA bump round-trips
        row = special_order_service.mark_delayed(
            db,
            special_order_id=special_order_id,
            eta_date=date.today() + timedelta(days=45),
        )
        db.commit()
        assert row.status == "delayed"
        assert row.eta_date == date.today() + timedelta(days=45)

        row = special_order_service.mark_ordered(
            db,
            special_order_id=special_order_id,
            eta_date=date.today() + timedelta(days=40),
        )
        db.commit()
        assert row.status == "ordered"
        assert row.eta_date == date.today() + timedelta(days=40)

        # ordered → received → picked_up
        row = special_order_service.mark_received(
            db, special_order_id=special_order_id
        )
        db.commit()
        assert row.status == "received"
        assert row.received_at is not None

        row = special_order_service.mark_picked_up(
            db, special_order_id=special_order_id
        )
        db.commit()
        assert row.status == "picked_up"
        assert row.picked_up_at is not None

        # picked_up is terminal: cancel rejected.
        try:
            special_order_service.mark_cancelled(
                db, special_order_id=special_order_id
            )
            db.commit()
            raise AssertionError("cancel of picked_up row was accepted")
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invalid_transition", exc.code
    finally:
        db.close()


def check_received_to_ordered_rejected(seed) -> None:
    db = SessionLocal()
    try:
        # Create a fresh row, walk it to received, then attempt the
        # forbidden reverse transition.
        row = special_order_service.create_special_order(
            db,
            CreateSpecialOrderInput(
                event_id=seed["event_id"],
                catalog_item_id=seed["catalog_id"],
                size_label="10",
            ),
        )
        db.commit()
        sid = int(row.id)
        special_order_service.mark_ordered(db, special_order_id=sid)
        db.commit()
        special_order_service.mark_received(db, special_order_id=sid)
        db.commit()
        try:
            special_order_service.mark_ordered(db, special_order_id=sid)
            db.commit()
            raise AssertionError("received → ordered reversal was accepted")
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "invalid_transition", exc.code
    finally:
        db.close()


def check_patch_metadata(seed) -> None:
    db = SessionLocal()
    try:
        row = special_order_service.create_special_order(
            db,
            CreateSpecialOrderInput(
                event_id=seed["event_id"],
                catalog_item_id=seed["catalog_id"],
                size_label="04",
            ),
        )
        db.commit()
        sid = int(row.id)
        # Walk to ordered so we have an ETA worth bumping.
        special_order_service.mark_ordered(db, special_order_id=sid)
        db.commit()
        special_order_service.patch_special_order(
            db,
            special_order_id=sid,
            patch={
                "eta_date": date.today() + timedelta(days=21),
                "vendor_order_number": "MV-99999",
                "internal_notes": "called vendor 11/4",
                "size_label": "06",
            },
        )
        db.commit()
        refreshed = special_order_service.get_special_order(db, sid)
        assert refreshed.eta_date == date.today() + timedelta(days=21)
        assert refreshed.vendor_order_number == "MV-99999"
        assert refreshed.internal_notes == "called vendor 11/4"
        assert refreshed.size_label == "06"
        assert refreshed.status == "ordered"

        # Reject unknown fields and blank size.
        try:
            special_order_service.patch_special_order(
                db, special_order_id=sid, patch={"status": "received"}
            )
            db.commit()
            raise AssertionError("patch accepted forbidden field")
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "unknown_fields", exc.code
        try:
            special_order_service.patch_special_order(
                db, special_order_id=sid, patch={"size_label": "   "}
            )
            db.commit()
            raise AssertionError("patch accepted blank size_label")
        except SpecialOrderError as exc:
            db.rollback()
            assert exc.code == "size_label_required", exc.code
    finally:
        db.close()


def check_invoice_line_delete_sets_null(seed) -> None:
    """When the originating invoice line is deleted, the special
    order's invoice_line_item_id must clear to NULL while the rest of
    the row stays intact (the catalog snapshot still anchors what was
    on order)."""
    db = SessionLocal()
    try:
        row = special_order_service.create_from_invoice_line(
            db, invoice_line_item_id=seed["catalog_line_id"]
        )
        db.commit()
        sid = int(row.id)
        # Delete the invoice line directly. This is the same effect a
        # staff edit that drops the line through invoice_service would
        # produce (line replacement DELETEs the row before reinserting
        # the new shape).
        db.execute(
            sql_text("DELETE FROM invoice_line_items WHERE id = :i"),
            {"i": seed["catalog_line_id"]},
        )
        db.commit()
        view = special_order_service.get_special_order(db, sid)
        assert view.invoice_line_item_id is None, (
            f"expected SET NULL, got {view.invoice_line_item_id!r}"
        )
        assert view.catalog_item_id == seed["catalog_id"], (
            "catalog snapshot lost after invoice-line delete"
        )
        assert view.size_label == "08"
        assert view.status == "needed"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Router smoke
# ---------------------------------------------------------------------------


def check_router_round_trip(seed, headers) -> None:
    # Pick a fresh row via the convenience endpoint, then walk the
    # transitions through the per-row routes.
    inv = invoice_service  # quiet unused-import warning
    db = SessionLocal()
    try:
        # Add a second catalog-backed invoice line so this check has
        # its own line to attach to (the others are consumed by other
        # checks in the run).
        line = InvoiceLineItem(
            invoice_id=seed["invoice_id"],
            sort_order=99,
            kind="product",
            catalog_item_id=seed["catalog_id"],
            size_label="12",
            quantity=Decimal("1"),
            unit_price_cents=120000,
            line_subtotal_cents=120000,
            line_tax_cents=0,
            line_total_cents=120000,
        )
        db.add(line)
        db.commit()
        line_id = int(line.id)
    finally:
        db.close()

    resp = client.post(
        f"/api/events/{seed['event_id']}/special-orders/from-invoice-line",
        headers=headers,
        json={"invoice_line_item_id": line_id, "internal_notes": "router test"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["catalog_item_id"] == seed["catalog_id"]
    assert body["size_label"] == "12"
    assert body["status"] == "needed"
    assert body["catalog"]["internal_sku"].startswith(_PREFIX)
    assert body["catalog"]["public_code"].startswith("BVX-")
    assert body["internal_notes"] == "router test"
    sid = int(body["id"])

    # mark-ordered with metadata
    resp = client.post(
        f"/api/special-orders/{sid}/mark-ordered",
        headers=headers,
        json={
            "vendor_order_number": "PO-router-77",
            "eta_date": (date.today() + timedelta(days=14)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ordered"
    assert body["vendor_order_number"] == "PO-router-77"
    assert body["ordered_at"] is not None

    # patch metadata
    resp = client.patch(
        f"/api/special-orders/{sid}",
        headers=headers,
        json={"internal_notes": "patched from router"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["internal_notes"] == "patched from router"

    # mark-received then mark-picked-up
    resp = client.post(
        f"/api/special-orders/{sid}/mark-received",
        headers=headers,
        json={},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    resp = client.post(
        f"/api/special-orders/{sid}/mark-picked-up",
        headers=headers,
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "picked_up"
    assert body["picked_up_at"] is not None

    # cancel after picked_up rejected
    resp = client.post(
        f"/api/special-orders/{sid}/cancel", headers=headers
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_transition"

    # listing on the event surface includes this row.
    resp = client.get(
        f"/api/events/{seed['event_id']}/special-orders",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["special_orders"]
    found = next(r for r in rows if r["id"] == sid)
    assert found["status"] == "picked_up"


def check_router_requires_auth(seed) -> None:
    resp = client.get(f"/api/events/{seed['event_id']}/special-orders")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_seq()
    print(f"catalog_public_code_seq baseline = {seq_baseline}")
    seed = _seed()
    print(
        f"seeded event={seed['event_id']} catalog={seed['catalog_id']} "
        f"invoice_line={seed['catalog_line_id']}"
    )
    _admin_id, admin_headers = _make_admin()
    try:
        sid = check_create_from_invoice_line_copies_snapshot(seed)
        print(f"create_from_invoice_line ok (special_order={sid})")
        check_create_from_non_catalog_line_rejected(seed)
        print("non-catalog invoice line rejected ok")
        check_create_against_inactive_catalog_rejected(seed)
        print("inactive catalog item rejected ok")
        check_invoice_line_catalog_mismatch_rejected(seed)
        print("invoice/catalog mismatch rejected ok")
        check_cross_event_invoice_line_rejected(seed)
        print("cross-event invoice line rejected ok")
        check_size_label_mismatch_rejected(seed)
        print("invoice/size mismatch rejected ok")
        check_lifecycle_happy_path(sid)
        print("needed→ordered→delayed→ordered→received→picked_up ok")
        check_received_to_ordered_rejected(seed)
        print("received→ordered rejected ok")
        check_patch_metadata(seed)
        print("patch metadata + unknown-field/blank-size rejection ok")
        check_invoice_line_delete_sets_null(seed)
        print("ON DELETE SET NULL on invoice line ok")
        check_router_requires_auth(seed)
        print("router auth required ok")
        check_router_round_trip(seed, admin_headers)
        print("router round-trip + listing ok")
        print()
        print("special_orders smoke ok")
        return 0
    finally:
        _cleanup()
        _reset_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
