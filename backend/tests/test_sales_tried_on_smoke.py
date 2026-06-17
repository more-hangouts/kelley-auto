"""Smoke tests for the Sales Portal Phase 4 try-on log.

Covers:

  - Add (with size + without): both succeed; same dress + same size
    twice returns 409 (UNIQUE NULLS NOT DISTINCT); same dress + NULL
    size twice ALSO returns 409.
  - Add against an appointment with no linked event returns 409
    `event_required` (the user's directive: block + guide, do not
    auto-create the event from the try-on path).
  - List works without an event (returns empty + has_event=false).
  - Patch updates only set fields and writes one activity row.
  - Delete returns 204 and writes activity.
  - Activity payload omits internal_sku, designer, style_number,
    description_text per the SKU obfuscation policy.
  - Sales-only enforcement; admin token gets 403.
  - Catalog GET endpoints accept sales token (Phase 1 over-cautious
    lock revised in Phase 4).
"""

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import APP_TIMEZONE  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    Appointment,
    AppointmentTriedOnItem,
    CatalogItem,
    Contact,
    Event,
    EventParticipant,
    User,
)
from services import booking_service, catalog_service  # noqa: E402
from tests._attendance_helpers import (  # noqa: E402
    restore_gate,
    snapshot_and_disable_gate,
)

client = TestClient(app)

_user_ids: list[int] = []
_appt_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []
_catalog_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p4-{suffix}",
            email=f"{role}-p4-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P4 {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _token_for(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _seed_contact() -> int:
    db = SessionLocal()
    try:
        c = Contact(
            display_name="P4 Customer",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"p4-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _seed_event(contact_id: int, status: str = "consulted") -> int:
    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="P4 Test Event",
            event_date=date.today() + timedelta(days=200),
            status=status,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        _event_ids.append(e.id)
        db.add(
            EventParticipant(
                event_id=e.id,
                contact_id=contact_id,
                role="quinceanera",
                display_name="P4 Quince",
            )
        )
        db.commit()
        return e.id
    finally:
        db.close()


def _seed_appointment(*, contact_id: int, event_id: int | None) -> int:
    db = SessionLocal()
    try:
        tz = ZoneInfo(APP_TIMEZONE)
        slot_local = datetime.combine(
            date.today(), time(10, 0), tzinfo=tz
        ) + timedelta(minutes=len(_appt_ids))
        slot_utc = slot_local.astimezone(timezone.utc)
        a = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=60),
            slot_duration_minutes=60,
            timezone=APP_TIMEZONE,
            celebrant_first_name="P4",
            celebrant_last_name="Smoke",
            party_size_bucket="solo",
            phone="(210) 555-0123",
            email=f"p4-{uuid.uuid4().hex[:6]}@example.com",
            contact_id=contact_id,
            crm_event_id=event_id,
            status="confirmed",
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _appt_ids.append(a.id)
        return a.id
    finally:
        db.close()


def _seed_catalog_item() -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        item = catalog_service.create_catalog_item(
            db,
            catalog_service.CatalogItemInput(
                internal_sku=f"P4-PROBE-{suffix}",
                color="navy",
                category="quince_gown",
                designer="P4 Couture",
                style_number="P4-001",
                product_title="P4 Probe Dress",
            ),
        )
        db.commit()
        db.refresh(item)
        _catalog_ids.append(item.id)
        return item.id
    finally:
        db.close()


def _activity_for_event(event_id: int, activity_type: str) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(ActivityLog)
                .where(ActivityLog.event_id == event_id)
                .where(ActivityLog.activity_type == activity_type)
                .order_by(ActivityLog.id)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointment_tried_on_items "
                         "WHERE appointment_id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_status_change_events "
                         "WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_participants WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _appt_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _catalog_ids:
            db.execute(
                sql_text("DELETE FROM catalog_items WHERE id = ANY(:ids)"),
                {"ids": _catalog_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


_gate_snapshot: dict | None = None


def main() -> None:
    global _gate_snapshot
    _gate_snapshot = snapshot_and_disable_gate()

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_token = _token_for(sales_id, sales=True)
    admin_token = _token_for(admin_id, sales=False)
    sales_headers = {"Authorization": f"Bearer {sales_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    contact_id = _seed_contact()
    catalog_id_a = _seed_catalog_item()
    catalog_id_b = _seed_catalog_item()

    # ------------------------------------------------------------------
    # Catalog GET is dual-scope (Phase 4 revision). Sales token reads
    # the catalog list and an item by id.
    # ------------------------------------------------------------------
    resp = client.get("/api/catalog", headers=sales_headers)
    assert resp.status_code == 200, resp.text
    resp = client.get(f"/api/catalog/{catalog_id_a}", headers=sales_headers)
    assert resp.status_code == 200, resp.text

    # Catalog POST/PATCH stay admin-only.
    resp = client.post(
        "/api/catalog",
        headers=sales_headers,
        json={
            "internal_sku": "should-not-create",
            "color": "navy",
            "category": "quinceanera",
        },
    )
    assert resp.status_code == 403, resp.text

    # ------------------------------------------------------------------
    # Event-required guard: appointment with no event yet.
    # ------------------------------------------------------------------
    contact_pre = _seed_contact()
    appt_pre = _seed_appointment(contact_id=contact_pre, event_id=None)

    # GET works (no event). has_event=false, items empty.
    resp = client.get(
        f"/api/sales/appointments/{appt_pre}/tried-on",
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["appointment_id"] == appt_pre
    assert body["has_event"] is False
    assert body["items"] == []

    # POST is blocked with 409 event_required (NOT 422 / 400).
    resp = client.post(
        f"/api/sales/appointments/{appt_pre}/tried-on",
        headers=sales_headers,
        json={"catalog_item_id": catalog_id_a, "size_label": "10"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "event_required"

    # ------------------------------------------------------------------
    # Happy path: appointment with a linked event.
    # ------------------------------------------------------------------
    contact_main = _seed_contact()
    event_main = _seed_event(contact_main, status="consulted")
    appt_main = _seed_appointment(
        contact_id=contact_main, event_id=event_main
    )

    # Add size 10 in dress A.
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
        json={
            "catalog_item_id": catalog_id_a,
            "size_label": "10",
            "liked": True,
            "notes": "Loved the silhouette",
        },
    )
    assert resp.status_code == 201, resp.text
    row_a10 = resp.json()
    assert row_a10["size_label"] == "10"
    assert row_a10["liked"] is True
    assert row_a10["catalog_item"]["id"] == catalog_id_a
    # SKU obfuscation: the embedded catalog summary must NOT carry
    # internal_sku / designer / style_number / description_text.
    for forbidden in ("internal_sku", "designer", "style_number", "description_text"):
        assert forbidden not in row_a10["catalog_item"], forbidden

    # Activity row written, with payload referencing only catalog_item_id.
    rows = _activity_for_event(event_main, "appointment.tried_on_added")
    assert len(rows) == 1
    payload = rows[0].payload
    for forbidden in ("internal_sku", "designer", "style_number", "description_text"):
        assert forbidden not in payload, forbidden
    assert payload["catalog_item_id"] == catalog_id_a
    assert payload["size_label"] == "10"

    # Same dress + same size: 409 duplicate.
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
        json={"catalog_item_id": catalog_id_a, "size_label": "10"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "duplicate_tried_on"

    # Different size, same dress: 201 ok.
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
        json={"catalog_item_id": catalog_id_a, "size_label": "12"},
    )
    assert resp.status_code == 201, resp.text
    row_a12_id = resp.json()["id"]

    # Different dress, no size: 201 ok.
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
        json={"catalog_item_id": catalog_id_b},
    )
    assert resp.status_code == 201, resp.text

    # Different dress, no size again: 409 because of NULLS NOT DISTINCT.
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
        json={"catalog_item_id": catalog_id_b},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "duplicate_tried_on"

    # ------------------------------------------------------------------
    # List ordering (oldest first), shape includes catalog summary.
    # ------------------------------------------------------------------
    resp = client.get(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=sales_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_event"] is True
    assert len(body["items"]) == 3  # A/10, A/12, B/null
    sizes = [i["size_label"] for i in body["items"]]
    assert sizes == ["10", "12", None]

    # ------------------------------------------------------------------
    # PATCH: only the set fields are updated, activity row written.
    # ------------------------------------------------------------------
    resp = client.patch(
        f"/api/sales/tried-on/{row_a12_id}",
        headers=sales_headers,
        json={"liked": False, "notes": "Felt tight in the bodice"},
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["liked"] is False
    assert patched["size_label"] == "12"  # untouched
    assert patched["notes"] == "Felt tight in the bodice"

    upd_rows = _activity_for_event(event_main, "appointment.tried_on_updated")
    assert len(upd_rows) == 1
    assert set(upd_rows[0].payload["fields"]) == {"liked", "notes"}
    # Activity row records which fields changed — never the text.
    assert "Felt tight" not in str(upd_rows[0].payload)
    assert "notes" not in {
        k for k in upd_rows[0].payload.keys() if k != "fields"
    }

    # PATCH with no changes is a no-op (no second activity row).
    resp = client.patch(
        f"/api/sales/tried-on/{row_a12_id}",
        headers=sales_headers,
        json={"liked": False},
    )
    assert resp.status_code == 200, resp.text
    assert (
        len(_activity_for_event(event_main, "appointment.tried_on_updated")) == 1
    )

    # ------------------------------------------------------------------
    # DELETE: 204 and one tried_on_removed activity row.
    # ------------------------------------------------------------------
    resp = client.delete(
        f"/api/sales/tried-on/{row_a12_id}", headers=sales_headers
    )
    assert resp.status_code == 204, resp.text
    rm_rows = _activity_for_event(event_main, "appointment.tried_on_removed")
    assert len(rm_rows) == 1
    assert rm_rows[0].payload["catalog_item_id"] == catalog_id_a
    assert rm_rows[0].payload["size_label"] == "12"

    # ------------------------------------------------------------------
    # Catalog ON DELETE RESTRICT: trying to delete a catalog item with
    # tried-on rows fails. This is enforced by the FK; we verify the
    # 500/IntegrityError surfaces predictably even though admin would
    # soft-deactivate (active=false) instead of hard-delete in practice.
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        try:
            db.execute(
                sql_text("DELETE FROM catalog_items WHERE id = :cid"),
                {"cid": catalog_id_a},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError(
                "catalog_items DELETE was not blocked by RESTRICT"
            )
    finally:
        db.close()

    # ------------------------------------------------------------------
    # Scope rejection.
    # ------------------------------------------------------------------
    resp = client.get(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=admin_headers,
    )
    assert resp.status_code == 403, resp.text
    resp = client.post(
        f"/api/sales/appointments/{appt_main}/tried-on",
        headers=admin_headers,
        json={"catalog_item_id": catalog_id_b},
    )
    assert resp.status_code == 403, resp.text

    # ------------------------------------------------------------------
    # Unknown ids.
    # ------------------------------------------------------------------
    resp = client.get(
        "/api/sales/appointments/99999999/tried-on", headers=sales_headers
    )
    assert resp.status_code == 404, resp.text
    resp = client.delete(
        "/api/sales/tried-on/99999999", headers=sales_headers
    )
    assert resp.status_code == 404, resp.text

    print("sales_tried_on smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
        restore_gate(_gate_snapshot)
