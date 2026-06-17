"""Smoke for Phase 9 sub-slice 1, Priority 1: staff-locations admin
extensions (default_auto_session_close_time round-trip and the
read-only test-geofence probe).

Covers the three things shipped in steps 1-2 of the sub-slice:

  - Create/patch/response carries `default_auto_session_close_time`.
  - PATCH with explicit null clears the cutoff.
  - POST `/{id}/test-geofence` returns inside/outside + distance using
    the same haversine the punch gate uses.

Runs serially per the project rule on shared singleton state.
"""

import math
import os
import sys
import uuid
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
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import StaffLocation, User  # noqa: E402
from services.clock_in import EARTH_RADIUS_M  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []

# Synthetic San Antonio-area coords; never collides with prod data.
PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000
PROBE_RADIUS_M = 100


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p9sl-{suffix}",
            email=f"{role}-p9sl-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P9SL {role.title()}",
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


def _coords_offset(lat: float, lng: float, north_m: float, east_m: float):
    delta_lat = (north_m / EARTH_RADIUS_M) * (180 / math.pi)
    delta_lng = (
        (east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
        * (180 / math.pi)
    )
    return lat + delta_lat, lng + delta_lng


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _location_ids:
            db.execute(
                sql_text("DELETE FROM staff_locations WHERE id = ANY(:ids)"),
                {"ids": _location_ids},
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
    admin_id = _make_user(role="admin")
    sales_id = _make_user(role="sales")
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}
    sales_headers = {"Authorization": f"Bearer {_token_for(sales_id, sales=True)}"}

    # ---- 1. Create carries default_auto_session_close_time on the wire. ----
    resp = client.post(
        "/api/admin/staff-locations",
        headers=admin_headers,
        json={
            "name": "P9 probe boutique",
            "latitude": PROBE_LAT,
            "longitude": PROBE_LNG,
            "radius_m": PROBE_RADIUS_M,
            "grace_minutes": 5,
            "default_auto_session_close_time": "21:30:00",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    location_id = body["id"]
    _location_ids.append(location_id)
    assert body["default_auto_session_close_time"] == "21:30:00", body
    assert body["grace_minutes"] == 5
    assert body["radius_m"] == PROBE_RADIUS_M

    # ---- 2. Real-row check: the value actually landed in Postgres. ----
    db = SessionLocal()
    try:
        row = db.get(StaffLocation, location_id)
        assert row is not None
        assert row.default_auto_session_close_time is not None
        assert row.default_auto_session_close_time.strftime("%H:%M:%S") == "21:30:00"
    finally:
        db.close()

    # ---- 3. PATCH to a new time round-trips. ----
    resp = client.patch(
        f"/api/admin/staff-locations/{location_id}",
        headers=admin_headers,
        json={"default_auto_session_close_time": "20:00:00"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_auto_session_close_time"] == "20:00:00"

    # ---- 4. PATCH with explicit null clears the column. The membership-
    #         only check in patch_location() is what makes this work; a
    #         `is not None` guard would have silently dropped the clear. ----
    resp = client.patch(
        f"/api/admin/staff-locations/{location_id}",
        headers=admin_headers,
        json={"default_auto_session_close_time": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_auto_session_close_time"] is None

    db = SessionLocal()
    try:
        row = db.get(StaffLocation, location_id)
        assert row.default_auto_session_close_time is None
    finally:
        db.close()

    # Restore a value so subsequent geofence tests are easier to read.
    resp = client.patch(
        f"/api/admin/staff-locations/{location_id}",
        headers=admin_headers,
        json={"default_auto_session_close_time": "22:00:00"},
    )
    assert resp.status_code == 200, resp.text

    # ---- 5. test-geofence: inside-radius coords come back inside=true. ----
    inside_lat, inside_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 50.0, 0.0)
    resp = client.post(
        f"/api/admin/staff-locations/{location_id}/test-geofence",
        headers=admin_headers,
        json={"latitude": inside_lat, "longitude": inside_lng},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inside"] is True, body
    assert 45.0 <= body["distance_m"] <= 55.0, body
    assert body["radius_m"] == PROBE_RADIUS_M

    # ---- 6. test-geofence: outside-radius coords come back inside=false
    #         with a distance well past the radius. ----
    far_lat, far_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 5_000.0, 0.0)
    resp = client.post(
        f"/api/admin/staff-locations/{location_id}/test-geofence",
        headers=admin_headers,
        json={"latitude": far_lat, "longitude": far_lng},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inside"] is False, body
    assert body["distance_m"] >= 4_500, body
    assert body["radius_m"] == PROBE_RADIUS_M

    # ---- 7. test-geofence on unknown id is 404, not 200/false. ----
    resp = client.post(
        "/api/admin/staff-locations/999999/test-geofence",
        headers=admin_headers,
        json={"latitude": PROBE_LAT, "longitude": PROBE_LNG},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "staff_location_not_found"

    # ---- 8. Soft-deleting the location does NOT block test-geofence —
    #         the owner needs to validate coords before reactivating. ----
    resp = client.delete(
        f"/api/admin/staff-locations/{location_id}", headers=admin_headers
    )
    assert resp.status_code == 204, resp.text

    resp = client.post(
        f"/api/admin/staff-locations/{location_id}/test-geofence",
        headers=admin_headers,
        json={"latitude": inside_lat, "longitude": inside_lng},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["inside"] is True

    # ---- 9. Sales token gets 403 on every staff-locations route. ----
    resp = client.get("/api/admin/staff-locations", headers=sales_headers)
    assert resp.status_code == 403, resp.text

    resp = client.post(
        "/api/admin/staff-locations",
        headers=sales_headers,
        json={
            "name": "Sales-token attempt",
            "latitude": PROBE_LAT,
            "longitude": PROBE_LNG,
            "radius_m": PROBE_RADIUS_M,
        },
    )
    assert resp.status_code == 403, resp.text

    resp = client.post(
        f"/api/admin/staff-locations/{location_id}/test-geofence",
        headers=sales_headers,
        json={"latitude": inside_lat, "longitude": inside_lng},
    )
    assert resp.status_code == 403, resp.text

    # ---- 10. Out-of-range lat/lng is rejected at the Pydantic boundary. ----
    resp = client.post(
        f"/api/admin/staff-locations/{location_id}/test-geofence",
        headers=admin_headers,
        json={"latitude": 999.0, "longitude": 0.0},
    )
    assert resp.status_code == 422, resp.text

    print("phase9 staff_locations smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
