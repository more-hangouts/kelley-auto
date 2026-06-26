"""Smoke tests for the Kelley Autoplex vehicle-sale deal pipeline.

Day 3 / migration 086 adds a `vehicle_sale` workflow to the existing CRM
`events` table. This smoke proves a car deal can be created, walked through
every status, and that:

  - the workflow definition exposes the nine vehicle-sale columns with the
    right terminal semantics (`delivered`/`lost` terminal, `sold` not),
  - an out-of-workflow status is rejected (incl. a status that is valid for
    the quinceañera workflow but not for vehicle_sale),
  - audit rows are written for create + every transition,
  - moving a deal to `sold`/`delivered` drives the LINKED vehicle's
    `vehicle_status` to match,
  - the `is_vehicle` boundary holds: a deal linked to a non-vehicle
    (dress) catalog row never has its inventory status touched,
  - the legacy quinceañera workflow still creates + transitions normally.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_vehicle_sale_workflow_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
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
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8].upper()
_STOCK_PREFIX = f"DEALSTK-{_TAG}-"
_DRESS_SKU = f"DEALDRESS-{_TAG}"


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"vsale-{suffix}",
            email=f"vsale-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Vehicle Sale Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _make_contact(display_name: str) -> int:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                "INSERT INTO contacts (display_name, first_name, tags) "
                "VALUES (:dn, :fn, '[\"vsale-smoke\"]'::jsonb) RETURNING id"
            ),
            {"dn": display_name, "fn": display_name.split()[0]},
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


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


def _vehicle_status(catalog_item_id: int) -> tuple[str | None, bool]:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT vehicle_status, is_vehicle FROM catalog_items "
                "WHERE id = :id"
            ),
            {"id": catalog_item_id},
        ).first()
        return (row[0], row[1])
    finally:
        db.close()


def _status_history(event_id: int) -> list[tuple[str | None, str]]:
    db = SessionLocal()
    try:
        return [
            (r[0], r[1])
            for r in db.execute(
                sql_text(
                    "SELECT from_status, to_status FROM event_status_change_events "
                    "WHERE event_id = :eid ORDER BY changed_at, id"
                ),
                {"eid": event_id},
            ).all()
        ]
    finally:
        db.close()


def _participant_count(event_id: int) -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM event_participants WHERE event_id = :eid"
                ),
                {"eid": event_id},
            ).scalar()
        )
    finally:
        db.close()


def _vehicle_payload(stock_suffix: str) -> dict:
    return {
        "is_vehicle": True,
        "stock_number": _STOCK_PREFIX + stock_suffix,
        "make": "Honda",
        "model": "Accord",
        "year": 2020,
        "mileage": 41000,
        "exterior_color": "Silver",
        "vehicle_status": "available",
        "unit_price_cents": 2299500,
    }


def _cleanup(user_ids: list[int], contact_ids: list[int], baseline_seq: int) -> None:
    db = SessionLocal()
    try:
        if contact_ids:
            # Events RESTRICT on contact delete, so drop events first; they
            # cascade participants + status_change_events. The catalog FK is
            # ON DELETE SET NULL, so deleting cars doesn't need event order.
            db.execute(
                sql_text(
                    "DELETE FROM events WHERE primary_contact_id = ANY(:ids)"
                ),
                {"ids": contact_ids},
            )
        db.execute(
            sql_text(
                "DELETE FROM catalog_items "
                "WHERE internal_sku LIKE :stk OR internal_sku = :dress"
            ),
            {"stk": _STOCK_PREFIX + "%", "dress": _DRESS_SKU},
        )
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": baseline_seq},
        )
        db.commit()
    finally:
        db.close()


def main() -> int:  # noqa: C901 - linear smoke script
    baseline_seq = _get_seq()
    admin_id, admin_email = _make_admin()
    contact_ids: list[int] = []
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        print("login ok")

        # --- workflow definition ------------------------------------------
        resp = client.get("/api/events/workflow/vehicle_sale", headers=auth)
        _assert(resp.status_code == 200, "workflow def", resp.text)
        wf = resp.json()
        codes = [s["code"] for s in wf["statuses"]]
        _assert(
            codes
            == [
                "new_lead",
                "contacted",
                "appointment",
                "test_drive",
                "negotiation",
                "financing",
                "sold",
                "delivered",
                "lost",
            ],
            "vehicle_sale status order",
            codes,
        )
        terminals = [s["code"] for s in wf["statuses"] if s["is_terminal"]]
        _assert(terminals == ["delivered", "lost"], "terminals", terminals)
        sold = next(s for s in wf["statuses"] if s["code"] == "sold")
        _assert(sold["is_terminal"] is False, "sold non-terminal", sold)
        print("workflow definition ok")

        # --- create a vehicle + a deal linked to it -----------------------
        resp = client.post(
            "/api/catalog", headers=auth, json=_vehicle_payload("A")
        )
        _assert(resp.status_code == 201, "create vehicle", resp.text)
        vehicle = resp.json()
        veh_id = vehicle["id"]
        _assert(vehicle["vehicle_status"] == "available", "veh available", vehicle)

        buyer_id = _make_contact("Carlos Ramirez")
        contact_ids.append(buyer_id)

        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": buyer_id,
                "event_type": "vehicle_sale",
                "event_name": "2020 Honda Accord — Carlos",
                "vehicle_catalog_item_id": veh_id,
            },
        )
        _assert(resp.status_code == 201, "create deal", resp.text)
        deal = resp.json()
        deal_id = deal["id"]
        _assert(deal["event_type"] == "vehicle_sale", "deal type", deal)
        _assert(deal["status"] == "new_lead", "deal initial status", deal)
        _assert(
            deal["vehicle_catalog_item_id"] == veh_id, "deal vehicle link", deal
        )
        print(f"create vehicle_sale deal ok (id={deal_id})")

        # No quinceañera participant is seeded for a car deal.
        _assert(_participant_count(deal_id) == 0, "no participant on deal")
        # Initial audit row traces null -> new_lead.
        hist = _status_history(deal_id)
        _assert(hist == [(None, "new_lead")], "initial audit row", hist)
        print("deal seeding (no participant, initial audit) ok")

        # --- walk through every status ------------------------------------
        walk = [
            "contacted",
            "appointment",
            "test_drive",
            "negotiation",
            "financing",
            "sold",
            "delivered",
        ]
        for status in walk:
            resp = client.patch(
                f"/api/events/{deal_id}/status",
                headers=auth,
                json={"status": status},
            )
            _assert(resp.status_code == 200, f"patch -> {status}", resp.text)
            _assert(resp.json()["status"] == status, f"status now {status}", resp.json())
            if status == "sold":
                vs, isveh = _vehicle_status(veh_id)
                _assert(vs == "sold", "vehicle marked sold by deal", vs)
                _assert(isveh is True, "linked row is a vehicle", isveh)
        print("walk through every status ok")

        # delivered deal drove the vehicle to delivered
        vs, _ = _vehicle_status(veh_id)
        _assert(vs == "delivered", "vehicle marked delivered by deal", vs)
        print("sold/delivered propagation ok")

        # audit: initial + 7 transitions = 8 rows
        hist = _status_history(deal_id)
        _assert(len(hist) == 8, "audit row count", hist)
        _assert(hist[-1] == ("sold", "delivered"), "last transition", hist[-1])
        print("audit rows for every transition ok")

        # --- invalid status rejected --------------------------------------
        resp = client.patch(
            f"/api/events/{deal_id}/status", headers=auth, json={"status": "garbage"}
        )
        _assert(resp.status_code == 422, "reject garbage status", resp.text)
        # 'on_order' is valid for quinceañera but NOT vehicle_sale -> rejected
        # by the per-workflow gate even though the DB CHECK would accept it.
        resp = client.patch(
            f"/api/events/{deal_id}/status", headers=auth, json={"status": "on_order"}
        )
        _assert(
            resp.status_code == 422, "reject cross-workflow status", resp.text
        )
        print("invalid + cross-workflow status rejected ok")

        # --- is_vehicle boundary: deal linked to a dress row --------------
        # A dress catalog row (is_vehicle=false). A deal pointed at it that
        # reaches 'sold' must NOT have the dress's vehicle_status touched.
        dress = {
            "internal_sku": _DRESS_SKU,
            "color": "Blush",
            "category": "quince_gown",
            "designer": "Morilee",
            "style_number": "M-3001",
            "product_title": "Quince Gown Boundary Row",
        }
        resp = client.post("/api/catalog", headers=auth, json=dress)
        _assert(resp.status_code == 201, "create dress", resp.text)
        dress_row = resp.json()
        dress_id = dress_row["id"]
        _assert(dress_row["is_vehicle"] is False, "dress is_vehicle false", dress_row)
        dress_status_before, _ = _vehicle_status(dress_id)

        boundary_buyer = _make_contact("Boundary Buyer")
        contact_ids.append(boundary_buyer)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": boundary_buyer,
                "event_type": "vehicle_sale",
                "event_name": "Boundary deal (mislinked to a dress)",
                "vehicle_catalog_item_id": dress_id,
            },
        )
        _assert(resp.status_code == 201, "create boundary deal", resp.text)
        boundary_deal_id = resp.json()["id"]
        resp = client.patch(
            f"/api/events/{boundary_deal_id}/status",
            headers=auth,
            json={"status": "sold"},
        )
        _assert(resp.status_code == 200, "boundary deal -> sold", resp.text)
        dress_status_after, dress_isveh = _vehicle_status(dress_id)
        _assert(dress_isveh is False, "dress still not a vehicle", dress_isveh)
        _assert(
            dress_status_after == dress_status_before,
            "dress vehicle_status untouched by deal",
            (dress_status_before, dress_status_after),
        )
        print("is_vehicle boundary (dress untouched) ok")

        # --- board for vehicle_sale ---------------------------------------
        resp = client.get(
            "/api/events/board", headers=auth, params={"event_type": "vehicle_sale"}
        )
        _assert(resp.status_code == 200, "vehicle_sale board", resp.text)
        board = resp.json()
        _assert(board["event_type"] == "vehicle_sale", "board type", board)
        board_codes = [c["code"] for c in board["columns"]]
        _assert(board_codes == codes, "board columns match workflow", board_codes)
        delivered_col = next(c for c in board["columns"] if c["code"] == "delivered")
        _assert(
            any(card["id"] == deal_id for card in delivered_col["cards"]),
            "delivered deal on board",
            delivered_col,
        )
        print("vehicle_sale board ok")

        # --- legacy quinceañera workflow still works ----------------------
        quince_buyer = _make_contact("Sofia Quince")
        contact_ids.append(quince_buyer)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": quince_buyer,
                "event_name": "Sofia's Quince",
            },
        )
        _assert(resp.status_code == 201, "create quince", resp.text)
        quince = resp.json()
        quince_id = quince["id"]
        _assert(quince["event_type"] == "quinceanera", "quince type", quince)
        _assert(quince["status"] == "lead", "quince initial status", quince)
        _assert(
            quince["vehicle_catalog_item_id"] is None, "quince has no vehicle", quince
        )
        _assert(_participant_count(quince_id) == 1, "quince participant seeded")
        resp = client.patch(
            f"/api/events/{quince_id}/status",
            headers=auth,
            json={"status": "consulted"},
        )
        _assert(resp.status_code == 200, "quince patch", resp.text)
        _assert(resp.json()["status"] == "consulted", "quince consulted", resp.json())
        # a vehicle_sale status must NOT be accepted on a quinceañera event
        resp = client.patch(
            f"/api/events/{quince_id}/status",
            headers=auth,
            json={"status": "test_drive"},
        )
        _assert(resp.status_code == 422, "quince rejects vehicle status", resp.text)
        print("legacy quinceañera workflow still works ok")

        print()
        print("vehicle sale workflow smoke ok")
        return 0
    finally:
        _cleanup([admin_id], contact_ids, baseline_seq)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
