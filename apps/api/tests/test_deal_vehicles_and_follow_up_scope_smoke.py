"""Smoke tests for the deal's two vehicle links (migration 111) and for the
follow-up queue's appointment carve-out.

Two changes that share a subject — which car a deal is about, and which deals
are actually owed a phone call:

  - `PATCH /api/events/{id}/vehicles` sets the inquiry link, the sold link, or
    both; an OMITTED key leaves that link alone while an explicit `null`
    clears it. That distinction is the whole point of the endpoint: before it,
    recording the car someone bought meant overwriting the car they asked
    about, which destroyed the lead's origin.
  - closing a deal marks the SOLD car sold, not the inquiry car, and
    correcting the sold car on an already-closed deal re-propagates.
  - a non-vehicle catalog row is refused (422, not 404 — the row exists).
  - the follow-up queue EXCLUDES deals with an upcoming booked appointment and
    reports how many it held back, while a deal whose appointment is in the
    PAST stays in the queue: the visit already happened, so that deal is owed
    exactly the call the queue exists to prompt.
  - buckets stay ordered oldest-first.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_deal_vehicles_and_follow_up_scope_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        u = User(
            username=f"dealveh-{_TAG}",
            email=f"dealveh-{_TAG}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Deal Vehicle Smoke Admin",
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
                "VALUES (:dn, :fn, '[\"dealveh-smoke\"]'::jsonb) RETURNING id"
            ),
            {"dn": display_name, "fn": display_name.split()[0]},
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


def _reserve_public_code() -> str:
    """An unused KAP-NNNNN.

    `public_code` is CHECK-constrained to exactly five digits (migration 041),
    so the run's hex tag cannot be embedded in it the way it is in the SKU.
    Counting down from 99999 keeps these fixtures far away from the sequence
    the catalog service actually mints from.
    """
    db = SessionLocal()
    try:
        for n in range(99999, 90000, -1):
            code = f"KAP-{n:05d}"
            taken = db.execute(
                sql_text("SELECT 1 FROM catalog_items WHERE public_code = :c"),
                {"c": code},
            ).first()
            if not taken:
                return code
        raise RuntimeError("no free smoke public_code in the 9xxxx band")
    finally:
        db.close()


def _make_vehicle(model: str, seq: int) -> int:
    """A minimal `is_vehicle` catalog row. `color` is NOT NULL on the table."""
    db = SessionLocal()
    try:
        vid = db.execute(
            sql_text(
                """
                INSERT INTO catalog_items
                  (internal_sku, public_code, category, color, is_vehicle,
                   unit_price_cents, year, make, model, stock_number,
                   vehicle_status)
                VALUES
                  (:sku, :code, 'vehicle', 'Silver', TRUE,
                   1500000, 2019, 'Toyota', :model, :stock, 'available')
                RETURNING id
                """
            ),
            {
                "sku": f"SMOKE-{_TAG}-{seq}",
                "code": _reserve_public_code(),
                "model": model,
                "stock": f"ST-{_TAG}-{seq}",
            },
        ).scalar()
        db.commit()
        return int(vid)
    finally:
        db.close()


def _make_non_vehicle(seq: int) -> int:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                """
                INSERT INTO catalog_items
                  (internal_sku, public_code, category, color, is_vehicle,
                   unit_price_cents)
                VALUES (:sku, :code, 'accessory', 'n/a', FALSE, 1000)
                RETURNING id
                """
            ),
            {
                "sku": f"SMOKE-NV-{_TAG}-{seq}",
                "code": _reserve_public_code(),
            },
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


def _vehicle_status(vehicle_id: int) -> str | None:
    db = SessionLocal()
    try:
        return db.execute(
            sql_text("SELECT vehicle_status FROM catalog_items WHERE id = :id"),
            {"id": vehicle_id},
        ).scalar()
    finally:
        db.close()


def _make_appointment(event_id: int, contact_id: int, start: datetime, status: str) -> int:
    db = SessionLocal()
    try:
        aid = db.execute(
            sql_text(
                """
                INSERT INTO appointments
                  (confirmation_code, slot_start_at, slot_end_at,
                   slot_duration_minutes, timezone, celebrant_first_name,
                   party_size_bucket, phone, email, status, contact_id,
                   crm_event_id)
                VALUES
                  (:code, :start, :end, 60, :tz, 'Smoke', 'solo',
                   '2105550100', :email, :status, :cid, :eid)
                RETURNING id
                """
            ),
            {
                "code": f"SMK{_TAG}{event_id}"[:32].upper(),
                "start": start,
                "end": start + timedelta(hours=1),
                "tz": APP_TIMEZONE,
                "email": f"appt-{_TAG}@example.com",
                "status": status,
                "cid": contact_id,
                "eid": event_id,
            },
        ).scalar()
        db.commit()
        return int(aid)
    finally:
        db.close()


def _cleanup(user_ids, contact_ids, catalog_ids) -> None:
    db = SessionLocal()
    try:
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointments WHERE contact_id = ANY(:ids)"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if catalog_ids:
            db.execute(
                sql_text("DELETE FROM catalog_items WHERE id = ANY(:ids)"),
                {"ids": catalog_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> int:
    admin_id, admin_email = _make_admin()
    contact_ids: list[int] = []
    catalog_ids: list[int] = []
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        print("login ok")

        inquiry_id = _make_vehicle("Camry", 1)
        bought_id = _make_vehicle("RAV4", 2)
        corrected_id = _make_vehicle("Highlander", 3)
        not_a_car_id = _make_non_vehicle(4)
        catalog_ids += [inquiry_id, bought_id, corrected_id, not_a_car_id]

        buyer_id = _make_contact("Ramona Vela")
        contact_ids.append(buyer_id)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": buyer_id,
                "event_type": "vehicle_sale",
                "event_name": f"Vehicle switch smoke {_TAG}",
                "vehicle_catalog_item_id": inquiry_id,
            },
        )
        _assert(resp.status_code == 201, "create deal", resp.text)
        deal_id = resp.json()["id"]
        print(f"create deal ok (id={deal_id})")

        # --- the deal serves both links ------------------------------------
        body = client.get(f"/api/events/{deal_id}", headers=auth).json()
        _assert(
            body["vehicle_catalog_item_id"] == inquiry_id,
            "inquiry link on create",
            body,
        )
        _assert(
            body["sold_vehicle_catalog_item_id"] is None,
            "sold link starts null",
            body,
        )
        _assert(body["vehicle"]["label"] == "2019 Toyota Camry", "vehicle ref", body)
        _assert(body["sold_vehicle"] is None, "sold ref null", body)
        print("initial links ok")

        # --- setting the SOLD car leaves the inquiry car alone --------------
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            headers=auth,
            json={"sold_vehicle_catalog_item_id": bought_id},
        )
        _assert(resp.status_code == 200, "patch sold vehicle", resp.text)
        body = resp.json()
        _assert(
            body["vehicle_catalog_item_id"] == inquiry_id,
            "inquiry link PRESERVED when only the sold link is sent",
            body,
        )
        _assert(
            body["sold_vehicle_catalog_item_id"] == bought_id, "sold link set", body
        )
        _assert(body["sold_vehicle"]["label"] == "2019 Toyota RAV4", "sold ref", body)
        print("set sold vehicle ok (inquiry preserved)")

        # --- closing marks the SOLD car, not the inquiry car ----------------
        resp = client.patch(
            f"/api/events/{deal_id}/status", headers=auth, json={"status": "sold"}
        )
        _assert(resp.status_code == 200, "close deal", resp.text)
        _assert(
            _vehicle_status(bought_id) == "sold",
            "the car they BOUGHT is marked sold",
            _vehicle_status(bought_id),
        )
        _assert(
            _vehicle_status(inquiry_id) == "available",
            "the car they only ASKED about stays available",
            _vehicle_status(inquiry_id),
        )
        print("sold propagates to the bought car only ok")

        # --- correcting the sold car re-propagates --------------------------
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            headers=auth,
            json={"sold_vehicle_catalog_item_id": corrected_id},
        )
        _assert(resp.status_code == 200, "correct sold vehicle", resp.text)
        _assert(
            _vehicle_status(corrected_id) == "sold",
            "corrected car marked sold",
            _vehicle_status(corrected_id),
        )
        print("correcting the sold car re-propagates ok")

        # --- explicit null clears; omission does not ------------------------
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            headers=auth,
            json={"sold_vehicle_catalog_item_id": None},
        )
        body = resp.json()
        _assert(
            body["sold_vehicle_catalog_item_id"] is None, "explicit null clears", body
        )
        _assert(
            body["vehicle_catalog_item_id"] == inquiry_id,
            "inquiry link untouched by the clear",
            body,
        )
        resp = client.patch(f"/api/events/{deal_id}/vehicles", headers=auth, json={})
        body = resp.json()
        _assert(
            body["vehicle_catalog_item_id"] == inquiry_id,
            "empty payload changes nothing",
            body,
        )
        print("null-vs-omitted semantics ok")

        # --- guards ---------------------------------------------------------
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            headers=auth,
            json={"vehicle_catalog_item_id": 99999999},
        )
        _assert(resp.status_code == 404, "unknown vehicle 404s", resp.text)
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            headers=auth,
            json={"sold_vehicle_catalog_item_id": not_a_car_id},
        )
        _assert(resp.status_code == 422, "non-vehicle catalog row 422s", resp.text)
        resp = client.patch(
            "/api/events/99999999/vehicles",
            headers=auth,
            json={"vehicle_catalog_item_id": None},
        )
        _assert(resp.status_code == 404, "unknown deal 404s", resp.text)
        resp = client.patch(
            f"/api/events/{deal_id}/vehicles",
            json={"vehicle_catalog_item_id": None},
        )
        _assert(resp.status_code in (401, 403), "auth required", resp.status_code)
        print("guards ok")

        # --- follow-up queue: appointments carve-out ------------------------
        tz = ZoneInfo(APP_TIMEZONE)
        now_local = datetime.now(tz)

        past_contact = _make_contact("Past Appointment Buyer")
        soon_contact = _make_contact("Upcoming Appointment Buyer")
        contact_ids += [past_contact, soon_contact]

        deal_ids = {}
        for label, cid in (("past", past_contact), ("soon", soon_contact)):
            resp = client.post(
                "/api/events",
                headers=auth,
                json={
                    "primary_contact_id": cid,
                    "event_type": "vehicle_sale",
                    "event_name": f"{label} appt smoke {_TAG}",
                },
            )
            _assert(resp.status_code == 201, f"create {label} deal", resp.text)
            deal_ids[label] = resp.json()["id"]
            # Give each one an OVERDUE reminder. Without it a fresh deal lands
            # in `no_reminder`, which is capped at 100 and ordered stalest
            # first — so the newest row in the system is exactly the one the
            # cap cuts, and the test would fail against real data for reasons
            # that have nothing to do with appointments. It also sharpens the
            # carve-out claim: the upcoming-appointment deal is held back even
            # though it has a reminder that is past due.
            resp = client.post(
                f"/api/events/{deal_ids[label]}/notes",
                headers=auth,
                json={
                    "body": f"{label} appt smoke reminder",
                    "remind_at": (
                        datetime.now(timezone.utc) - timedelta(days=1)
                    ).isoformat(),
                },
            )
            _assert(resp.status_code in (200, 201), f"{label} reminder", resp.text)

        _make_appointment(
            deal_ids["past"], past_contact, now_local - timedelta(days=3), "confirmed"
        )
        _make_appointment(
            deal_ids["soon"], soon_contact, now_local + timedelta(days=2), "confirmed"
        )

        resp = client.get("/api/events/follow-ups", headers=auth)
        _assert(resp.status_code == 200, "follow-up queue", resp.text)
        queue = resp.json()
        listed = {i["event_id"] for i in queue["items"]}

        _assert(
            deal_ids["soon"] not in listed,
            "deal with an UPCOMING appointment is held out of the call queue",
            deal_ids["soon"],
        )
        _assert(
            deal_ids["past"] in listed,
            "deal whose appointment already passed is still owed a call",
            deal_ids["past"],
        )
        _assert(
            next(
                i["bucket"] for i in queue["items"] if i["event_id"] == deal_ids["past"]
            )
            == "overdue",
            "the past-appointment deal lands in overdue, not some other bucket",
            deal_ids["past"],
        )
        _assert(
            queue["scheduled_total"] >= 1,
            "held-back deals are counted, not silently dropped",
            queue["scheduled_total"],
        )
        print(
            f"appointment carve-out ok (scheduled_total={queue['scheduled_total']})"
        )

        # A cancelled appointment is not a commitment — it must not shield.
        db = SessionLocal()
        try:
            db.execute(
                sql_text(
                    "UPDATE appointments SET status = 'cancelled' "
                    "WHERE crm_event_id = :eid"
                ),
                {"eid": deal_ids["soon"]},
            )
            db.commit()
        finally:
            db.close()
        queue = client.get("/api/events/follow-ups", headers=auth).json()
        _assert(
            deal_ids["soon"] in {i["event_id"] for i in queue["items"]},
            "a CANCELLED appointment does not shield a deal from the queue",
            deal_ids["soon"],
        )
        print("cancelled appointment does not shield ok")

        # --- ordering: oldest first inside every dated bucket ---------------
        for bucket in ("overdue", "due_today", "upcoming"):
            due = [
                i["remind_at"] for i in queue["items"] if i["bucket"] == bucket
            ]
            _assert(
                all(a <= b for a, b in zip(due, due[1:])),
                f"{bucket} is ordered oldest-first",
                due[:5],
            )
        stale = [
            i["status_changed_at"]
            for i in queue["items"]
            if i["bucket"] == "no_reminder"
        ]
        _assert(
            all(a <= b for a, b in zip(stale, stale[1:])),
            "no_reminder is ordered stalest-first",
            stale[:5],
        )
        print("bucket ordering ok (oldest first)")

        print()
        print("deal vehicles + follow-up scope smoke ok")
        return 0
    finally:
        _cleanup([admin_id], contact_ids, catalog_ids)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
