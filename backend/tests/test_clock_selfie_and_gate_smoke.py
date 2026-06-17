"""Smoke tests for Phase 7 Slice 2 (selfie storage + attendance gate).

Two surfaces:

  1. Selfie storage on the multipart `POST /api/sales/clock/in` and
     `/out` endpoints. Covers the happy path (Pillow round-trip,
     EXIF strip, 1024px cap, WebP re-encode), the size cap (>1MB),
     wrong content-type, malformed image bytes, and — critically —
     the deployment-failure path: when the on-disk write fails
     (e.g. the systemd `ReadWritePaths` line is missing in
     production), the endpoint must surface a stable
     `selfie_storage_unavailable` 503 rather than a generic 500.
     The smoke triggers that path by monkeypatching
     `document_storage.put_object` to raise `PermissionError`.
  2. The server-enforced punched-out attendance gate. Covers:
     sales token punched out → 403 attendance_gate on appointment
     mutation; sales token punched in → 200; admin token bypasses
     regardless; flipping `business_profile.attendance_gate_enabled`
     to false makes the gate a no-op.

Run after `test_clock_in_smoke.py`; both touch `staff_locations` and
`staff_punches`. Neither hard-deletes user-level data outside its
own seed pool.
"""

import io
import os
import sys
import uuid
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

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    BusinessProfile,
    StaffPunch,
    User,
)
from services import clock_selfie, document_storage  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []
_appointment_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []
_selfie_keys_to_remove: list[str] = []

PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000
PROBE_RADIUS_M = 100


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p7s2-{suffix}",
            email=f"{role}-p7s2-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P7S2 {role.title()}",
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


def _seed_location(*, active: bool = True) -> int:
    db = SessionLocal()
    try:
        from database.models import StaffLocation
        loc = StaffLocation(
            name="P7S2 Probe",
            latitude=PROBE_LAT,
            longitude=PROBE_LNG,
            radius_m=PROBE_RADIUS_M,
            active=active,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        _location_ids.append(loc.id)
        return loc.id
    finally:
        db.close()


def _seed_event_with_appointment(contact_id: int) -> tuple[int, int]:
    """Returns (event_id, appointment_id)."""
    from datetime import date, datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    from config.settings import APP_TIMEZONE
    from database.models import Appointment, Event, EventParticipant
    from services import booking_service

    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="P7S2 Test",
            event_date=date.today() + timedelta(days=200),
            status="consulted",
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
                display_name="P7S2 Quince",
            )
        )

        tz = ZoneInfo(APP_TIMEZONE)
        slot_local = datetime.combine(
            date.today(), time(11, 0), tzinfo=tz
        )
        slot_utc = slot_local.astimezone(timezone.utc)
        a = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=60),
            slot_duration_minutes=60,
            timezone=APP_TIMEZONE,
            celebrant_first_name="P7S2",
            celebrant_last_name="Sample",
            party_size_bucket="solo",
            phone="(210) 555-0100",
            email=f"p7s2-{uuid.uuid4().hex[:6]}@example.com",
            contact_id=contact_id,
            crm_event_id=e.id,
            status="confirmed",
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        _appointment_ids.append(a.id)
        return e.id, a.id
    finally:
        db.close()


def _seed_contact() -> int:
    from database.models import Contact

    db = SessionLocal()
    try:
        c = Contact(
            display_name="P7S2 Customer",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"p7s2-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _set_attendance_gate(*, enabled: bool, selfie_policy: str = "optional") -> None:
    """Tweak the singleton business_profile row for the duration of the
    smoke. We capture the prior values so the smoke restores them."""
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is None:
            # Tests rely on a profile existing. We do not create one
            # here because the production singleton has many fields
            # the smoke shouldn't be in the business of populating.
            raise AssertionError(
                "test prerequisite: business_profile row must exist; run "
                "the business profile smoke first."
            )
        row.attendance_gate_enabled = enabled
        row.selfie_policy = selfie_policy
        db.commit()
    finally:
        db.close()


def _capture_business_profile_settings() -> dict:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        return {
            "attendance_gate_enabled": row.attendance_gate_enabled if row else True,
            "selfie_policy": row.selfie_policy if row else "optional",
        }
    finally:
        db.close()


def _restore_business_profile_settings(snapshot: dict) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is None:
            return
        row.attendance_gate_enabled = snapshot["attendance_gate_enabled"]
        row.selfie_policy = snapshot["selfie_policy"]
        db.commit()
    finally:
        db.close()


def _make_jpeg_bytes(size_px: int = 200, color=(170, 50, 90)) -> bytes:
    img = Image.new("RGB", (size_px, size_px), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _coords_offset(lat: float, lng: float, north_m: float, east_m: float):
    import math
    delta_lat = (north_m / 6_371_000) * (180 / math.pi)
    delta_lng = (
        (east_m / (6_371_000 * math.cos(math.radians(lat))))
        * (180 / math.pi)
    )
    return lat + delta_lat, lng + delta_lng


def _cleanup() -> None:
    db = SessionLocal()
    try:
        # Selfie files (best-effort): the smoke happy-path writes one.
        for key in list(_selfie_keys_to_remove):
            document_storage.delete_object(key)

        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_punch_audit_events "
                    "WHERE actor_user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_punches WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
        if _location_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_locations WHERE id = ANY(:ids)"
                ),
                {"ids": _location_ids},
            )
        if _appointment_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointments WHERE id = ANY(:ids)"
                ),
                {"ids": _appointment_ids},
            )
        if _event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM activity_log WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    profile_snapshot = _capture_business_profile_settings()

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_headers = {"Authorization": f"Bearer {_token_for(sales_id, sales=True)}"}
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}

    location_id = _seed_location()  # noqa: F841
    inside_lat, inside_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 30.0, 0.0)

    # ===== SELFIE STORAGE =====

    # Default policy at start: ensure 'optional' so we can test absent
    # selfie too.
    _set_attendance_gate(enabled=True, selfie_policy="optional")

    # ---- Happy path: punch in WITH selfie. ----
    selfie_bytes = _make_jpeg_bytes()
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
        files={"selfie": ("face.jpg", selfie_bytes, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["selfie_storage_key"] is not None
    assert body["selfie_storage_key"].startswith(
        f"clockin/{sales_id}/"
    ) and body["selfie_storage_key"].endswith(".webp")
    _selfie_keys_to_remove.append(body["selfie_storage_key"])

    # The on-disk file exists and decodes as WebP under the cap.
    db = SessionLocal()
    try:
        punch = (
            db.execute(
                select(StaffPunch).where(StaffPunch.id == body["id"])
            ).scalars().first()
        )
        assert punch.selfie_storage_key == body["selfie_storage_key"]
    finally:
        db.close()
    assert document_storage.object_exists(body["selfie_storage_key"])
    on_disk = document_storage.open_object(body["selfie_storage_key"]).read()
    with Image.open(io.BytesIO(on_disk)) as img:
        assert img.format == "WEBP", img.format
        assert max(img.size) <= 1024
    # File well under 1MB.
    assert len(on_disk) < clock_selfie.SELFIE_MAX_BYTES

    # ---- Punch out (no selfie this time, optional policy). ----
    resp = client.post(
        "/api/sales/clock/out",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["selfie_storage_key"] is None

    # ---- Wrong content type rejected. ----
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
        files={"selfie": ("note.txt", b"not an image", "text/plain")},
    )
    assert resp.status_code == 415, resp.text
    assert resp.json()["detail"]["code"] == "selfie_unsupported_type"

    # ---- Oversized payload rejected. ----
    big = b"\x00" * (clock_selfie.SELFIE_MAX_BYTES + 1)
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
        files={"selfie": ("big.jpg", big, "image/jpeg")},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "selfie_too_large"

    # ---- Bytes that claim to be JPEG but aren't a real image. ----
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
        files={"selfie": ("fake.jpg", b"X" * 5_000, "image/jpeg")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "selfie_invalid"

    # ---- Storage-unavailable failure mode (the deploy-gate smoke). ----
    # Monkeypatch document_storage.put_object to raise PermissionError.
    # In production this is what happens when the systemd unit's
    # `ReadWritePaths` line does not cover the upload root: the open()
    # call fails, the OSError propagates, clock_selfie maps it to a
    # stable 503. The smoke's whole point is to prove the failure
    # surfaces as an obvious server error and not a generic 500.
    real_put = document_storage.put_object

    def _failing_put(key, fileobj):
        raise PermissionError(
            "[smoke] simulated systemd ReadWritePaths block"
        )

    document_storage.put_object = _failing_put
    try:
        resp = client.post(
            "/api/sales/clock/in",
            headers=sales_headers,
            data={
                "client_latitude": inside_lat,
                "client_longitude": inside_lng,
            },
            files={"selfie": ("face.jpg", selfie_bytes, "image/jpeg")},
        )
    finally:
        document_storage.put_object = real_put

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "selfie_storage_unavailable"

    # The transaction was rolled back: no half-committed punch row.
    db = SessionLocal()
    try:
        rows_after_failure = (
            db.execute(
                select(StaffPunch).where(StaffPunch.user_id == sales_id)
            )
            .scalars()
            .all()
        )
        # We had one in (with selfie) and one out (no selfie) before.
        # The failed in attempt should NOT have added a row.
        assert len(rows_after_failure) == 2, len(rows_after_failure)
        assert {r.direction for r in rows_after_failure} == {"in", "out"}
    finally:
        db.close()

    # ---- selfie_policy='required' rejects an absent selfie. ----
    _set_attendance_gate(enabled=True, selfie_policy="required")
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "selfie_required"

    # ---- selfie_policy='disabled' rejects a present selfie. ----
    _set_attendance_gate(enabled=True, selfie_policy="disabled")
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
        files={"selfie": ("face.jpg", selfie_bytes, "image/jpeg")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "selfie_disabled"

    # ===== ATTENDANCE GATE =====

    # Reset to optional so policy doesn't leak into gate tests.
    _set_attendance_gate(enabled=True, selfie_policy="optional")

    # The sales user is currently OUT (we punched them in then out).
    # Seed a contact + event + appointment so the gate has a target.
    contact_id = _seed_contact()
    _event_id, appt_id = _seed_event_with_appointment(contact_id)

    # ---- Sales token, gate enabled, punched OUT → 403 attendance_gate. ----
    resp = client.post(
        f"/api/sales/appointments/{appt_id}/status",
        headers=sales_headers,
        json={"action": "no_show"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "attendance_gate"

    resp = client.patch(
        f"/api/sales/appointments/{appt_id}/notes",
        headers=sales_headers,
        json={"internal_notes": "trying to write while clocked out"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "attendance_gate"

    # ---- Admin token bypasses regardless. ----
    resp = client.post(
        f"/api/events/{_event_id}/participants",
        headers=admin_headers,
        json={
            "parent_first_name": "AdminBypass",
            "celebrant_first_name": "Probe",
            "phone": f"(210) 555-{uuid.uuid4().int % 10_000:04d}",
        },
    )
    assert resp.status_code == 201, resp.text
    # The participants endpoint find-or-creates a Contact as a side
    # effect. The event_participants join row is cleaned via _event_ids,
    # but the standalone Contact lingers and eats a slot in the
    # +1210555XXXX phone_e164 space — over many runs that causes
    # uq_contacts_phone_e164 collisions in other smokes that randomize
    # in the same range. Track the new contact so _cleanup deletes it.
    body = resp.json()
    if body.get("was_new_contact"):
        _contact_ids.append(body["contact"]["id"])

    # ---- Sales token, gate ENABLED, after punching IN → 200. ----
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 200, resp.text

    resp = client.patch(
        f"/api/sales/appointments/{appt_id}/notes",
        headers=sales_headers,
        json={"internal_notes": "first floor note while clocked in"},
    )
    assert resp.status_code == 200, resp.text

    # Punch the user back out so the next test exercises the gate-
    # disabled branch independently.
    resp = client.post(
        "/api/sales/clock/out",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 200, resp.text

    # ---- Owner disables the gate → sales token works while OUT. ----
    _set_attendance_gate(enabled=False, selfie_policy="optional")
    resp = client.patch(
        f"/api/sales/appointments/{appt_id}/notes",
        headers=sales_headers,
        json={"internal_notes": "gate disabled, this should write"},
    )
    assert resp.status_code == 200, resp.text

    # ---- The gate also covers tried-on add. ----
    _set_attendance_gate(enabled=True, selfie_policy="optional")
    # User is still out from the punch-out earlier. Seed a catalog
    # item by hitting the admin endpoint.
    from services import catalog_service
    db = SessionLocal()
    try:
        item = catalog_service.create_catalog_item(
            db,
            catalog_service.CatalogItemInput(
                internal_sku=f"P7S2-PROBE-{uuid.uuid4().hex[:6]}",
                color="ivory",
                category="quince_gown",
                designer="Probe House",
                style_number="P7S2-001",
                product_title="Probe Gown",
            ),
        )
        db.commit()
        catalog_id = item.id
    finally:
        db.close()
    try:
        resp = client.post(
            f"/api/sales/appointments/{appt_id}/tried-on",
            headers=sales_headers,
            json={"catalog_item_id": catalog_id, "size_label": "10"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "attendance_gate"
    finally:
        # Best-effort catalog cleanup so the smoke leaves no debris.
        db = SessionLocal()
        try:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_tried_on_items "
                    "WHERE catalog_item_id = :cid"
                ),
                {"cid": catalog_id},
            )
            db.execute(
                sql_text("DELETE FROM catalog_items WHERE id = :cid"),
                {"cid": catalog_id},
            )
            db.commit()
        finally:
            db.close()

    # ---- The gate also covers event-document upload (Phase 9 audit). ----
    # Sales user is currently punched out (we punched out earlier).
    _set_attendance_gate(enabled=True, selfie_policy="optional")
    files = {
        "file": (
            "p9-gate.txt",
            b"document body for gate audit",
            "text/plain",
        ),
    }
    resp = client.post(
        f"/api/events/{_event_id}/documents",
        headers=sales_headers,
        data={"kind": "document"},
        files=files,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "attendance_gate"

    print("clock_selfie_and_gate smoke ok")
    _restore_business_profile_settings(profile_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
