"""Smoke tests for Phase 7 Slice 1 (clock-in foundation).

Covers the haversine math, the geofence accept/reject decision, the
punch state machine, the today's-punches read, and the admin
staff-locations seed endpoint. No selfie code is exercised — that
arrives in Slice 2.

The smoke seeds its own staff_location row for the boutique and
cleans it up; it does not touch any pre-existing seeded location
data on the dev DB.
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
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
from database.models import StaffLocation, StaffPunch, User  # noqa: E402
from services.clock_in import EARTH_RADIUS_M, haversine_m  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_location_ids: list[int] = []
_punch_ids: list[int] = []
# Any pre-existing active staff_locations in the DB (e.g. the real
# `bellasXV` row on the VPS) get parked as inactive for the duration
# of the smoke so the "no active locations" assertion in step 10 is
# meaningful. _cleanup re-activates them.
_parked_location_ids: list[int] = []

# Boutique-ish coordinates for the smoke. Real Bellas geofence will be
# seeded by the owner via the admin endpoint at deploy time; the
# smoke uses synthetic San Antonio-area coords so it never collides
# with production data.
PROBE_LAT = 29.4252000
PROBE_LNG = -98.4946000
PROBE_RADIUS_M = 100


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p7s1-{suffix}",
            email=f"{role}-p7s1-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P7S1 {role.title()}",
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


def _set_trusted_network(*, enabled: bool, ips: list[str]) -> None:
    """Slam the business_profile trusted-network columns directly.

    The patch endpoint validates entries and is exercised by the
    business_profile smoke; here we want to put the singleton into a
    specific state with one round-trip so the test stays focused on
    the clock-in surface.
    """
    import json as _json

    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE business_profile "
                "SET trusted_network_enabled = :enabled, "
                "    trusted_clock_in_ips = CAST(:ips AS JSONB) "
                "WHERE id = 1"
            ),
            {"enabled": enabled, "ips": _json.dumps(ips)},
        )
        db.commit()
    finally:
        db.close()


def _coords_offset(lat: float, lng: float, north_m: float, east_m: float):
    """Return (lat', lng') offset by `north_m` meters north and
    `east_m` meters east. Approximate; good enough for smoke."""
    delta_lat = (north_m / EARTH_RADIUS_M) * (180 / math.pi)
    delta_lng = (
        (east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
        * (180 / math.pi)
    )
    return lat + delta_lat, lng + delta_lng


def _track_punches() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT id FROM staff_punches WHERE user_id = ANY(:uids)"
            ),
            {"uids": _user_ids or [-1]},
        ).all()
        for r in rows:
            if r[0] not in _punch_ids:
                _punch_ids.append(int(r[0]))
    finally:
        db.close()


def _cleanup() -> None:
    # Belt-and-suspenders: if the test panicked mid-way through the
    # trusted-network step, the singleton row could be left enabled
    # with a leftover IP list. Snap back to default-deny here too.
    try:
        _set_trusted_network(enabled=False, ips=[])
    except Exception:
        pass

    db = SessionLocal()
    try:
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
                    "DELETE FROM staff_punch_correction_requests "
                    "WHERE user_id = ANY(:uids)"
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
        if _parked_location_ids:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = TRUE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _parked_location_ids},
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
    # ---- 1. Haversine reference values. ----
    # Same point: zero distance.
    assert (
        haversine_m(PROBE_LAT, PROBE_LNG, PROBE_LAT, PROBE_LNG) < 0.01
    ), "haversine of identical points should be zero"

    # 1° latitude difference is ~111.32 km on the WGS-84 mean radius.
    one_deg_lat_m = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert abs(one_deg_lat_m - 111_195) < 200, (
        f"1deg latitude haversine off: {one_deg_lat_m:.1f}"
    )

    # 100m offset round-trips within ~1m.
    lat2, lng2 = _coords_offset(PROBE_LAT, PROBE_LNG, 100.0, 0.0)
    measured = haversine_m(PROBE_LAT, PROBE_LNG, lat2, lng2)
    assert abs(measured - 100.0) < 1.0, (
        f"100m offset measured as {measured:.2f}m"
    )

    # ---- 2. Admin seeds the geofence via the endpoint, not raw SQL. ----
    # On the VPS this smoke runs against a live DB that already has the
    # real `bellasXV` location row active. Step 10 needs "zero active
    # locations" to assert closest_location_name is None, so park any
    # existing active rows here and restore them in _cleanup.
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text("SELECT id FROM staff_locations WHERE active = TRUE")
        ).all()
        for row in rows:
            _parked_location_ids.append(int(row[0]))
        if _parked_location_ids:
            db.execute(
                sql_text(
                    "UPDATE staff_locations SET active = FALSE "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _parked_location_ids},
            )
            db.commit()
    finally:
        db.close()

    admin_id = _make_user(role="admin")
    sales_id = _make_user(role="sales")
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}
    sales_headers = {"Authorization": f"Bearer {_token_for(sales_id, sales=True)}"}

    # Sales token cannot create a location.
    resp = client.post(
        "/api/admin/staff-locations",
        headers=sales_headers,
        json={
            "name": "Probe boutique",
            "latitude": PROBE_LAT,
            "longitude": PROBE_LNG,
            "radius_m": PROBE_RADIUS_M,
        },
    )
    assert resp.status_code == 403, resp.text

    # Admin can.
    resp = client.post(
        "/api/admin/staff-locations",
        headers=admin_headers,
        json={
            "name": "Probe boutique",
            "latitude": PROBE_LAT,
            "longitude": PROBE_LNG,
            "radius_m": PROBE_RADIUS_M,
        },
    )
    assert resp.status_code == 201, resp.text
    loc_body = resp.json()
    location_id = loc_body["id"]
    _location_ids.append(location_id)
    assert loc_body["active"] is True
    assert loc_body["radius_m"] == PROBE_RADIUS_M

    # radius_m=10 violates the Pydantic Field(ge=25) before it even
    # reaches the DB CHECK; verify 422 from the API surface.
    resp = client.post(
        "/api/admin/staff-locations",
        headers=admin_headers,
        json={
            "name": "Too tight",
            "latitude": PROBE_LAT,
            "longitude": PROBE_LNG,
            "radius_m": 10,
        },
    )
    assert resp.status_code == 422, resp.text

    # ---- 3. Status before any punch: state='out', no last, no today. ----
    resp = client.get("/api/sales/clock/status", headers=sales_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "out"
    assert body["last_punch"] is None
    assert body["today_punches"] == []

    # Admin token rejected from sales-only endpoints.
    resp = client.get("/api/sales/clock/status", headers=admin_headers)
    assert resp.status_code == 403, resp.text

    # ---- 4. Punch in inside the radius (50m offset). ----
    inside_lat, inside_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 50.0, 0.0)
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
            "client_accuracy_m": 12.5,
        },
    )
    assert resp.status_code == 200, resp.text
    in_body = resp.json()
    _punch_ids.append(in_body["id"])
    assert in_body["direction"] == "in"
    assert in_body["status"] == "unscheduled"
    assert in_body["location_id"] == location_id
    # Distance is roughly 50m; accept 45-55 to absorb the offset
    # function's spherical approximation noise.
    assert 45 <= in_body["distance_to_location_m"] <= 55, in_body
    # Slice A: strict-radius pass records `accepted_by='gps'` with no
    # buffer slack. The accuracy buffer is configured but only fires
    # when the strict check fails.
    assert in_body["accepted_by"] == "gps", in_body
    assert in_body["accepted_buffer_m"] is None, in_body
    # Slice C: default install has trusted-network disabled and no IPs
    # configured, so a strict-GPS pass records `detected=False`.
    assert in_body["trusted_network_detected"] is False, in_body

    # ---- 5. Status now reflects the punch. ----
    resp = client.get("/api/sales/clock/status", headers=sales_headers)
    body = resp.json()
    assert body["state"] == "in"
    assert body["last_punch"]["id"] == in_body["id"]
    assert len(body["today_punches"]) == 1
    assert body["business_date"] is not None

    # ---- 6. Double punch-in within the Slice-4 idempotency window
    # (60s) returns the existing punch (200), NOT 409. This is the
    # shared-iPad double-tap fix: a second tap is the same punch.
    # The state-machine guard (409 'already_punched_in') still fires
    # for late re-taps after the window — covered separately in
    # test_schedule_stability_smoke. ----
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == in_body["id"], (
        "double-tap within 60s should return the original punch id"
    )

    # ---- 7. Punch out from outside the radius — accepted (we don't
    # block clock-out by geofence). Distance is recorded for audit. ----
    far_lat, far_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 5_000.0, 0.0)
    resp = client.post(
        "/api/sales/clock/out",
        headers=sales_headers,
        data={
            "client_latitude": far_lat,
            "client_longitude": far_lng,
            "client_accuracy_m": 30.0,
        },
    )
    assert resp.status_code == 200, resp.text
    out_body = resp.json()
    _punch_ids.append(out_body["id"])
    assert out_body["direction"] == "out"
    # ~5km away, but still records the closest active location.
    assert out_body["location_id"] == location_id
    assert out_body["distance_to_location_m"] >= 4_500

    # ---- 8. Second clock-out within the Slice-4 idempotency window
    # also debounces (same shape as #6 on punch_in). The state-machine
    # 409 'not_punched_in' only fires after the window expires AND
    # the user is actually in 'out' state. ----
    resp = client.post(
        "/api/sales/clock/out",
        headers=sales_headers,
        data={"client_latitude": inside_lat, "client_longitude": inside_lng},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == out_body["id"], (
        "second clock-out within 60s should return the original punch id"
    )

    # ---- 8.5 Accuracy buffer (Slice A). Just outside the strict
    # radius but reporting accuracy that matches the configured cap
    # widens the gate by exactly that much. The audit row tells the
    # owner which mechanism let the punch through. ----
    buf_lat, buf_lng = _coords_offset(PROBE_LAT, PROBE_LNG, 130.0, 0.0)

    # Insufficient reported accuracy → buffer = min(10, 50) = 10m of
    # slack, distance 130m exceeds 100m + 10m, still rejected. The
    # rejection payload echoes the buffer we tried to apply so the UI
    # can render "we already widened the gate by 10m".
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": buf_lat,
            "client_longitude": buf_lng,
            "client_accuracy_m": 10.0,
        },
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "outside_geofence"
    assert detail["accuracy_buffer_m"] == 10.0, detail

    # Honest ±50m reading → buffer caps at the configured 50m, distance
    # 130m fits inside 100m + 50m. Accepted, recorded as buffer-assisted.
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={
            "client_latitude": buf_lat,
            "client_longitude": buf_lng,
            "client_accuracy_m": 50.0,
        },
    )
    assert resp.status_code == 200, resp.text
    buf_body = resp.json()
    _punch_ids.append(buf_body["id"])
    assert buf_body["accepted_by"] == "gps_with_accuracy_buffer", buf_body
    assert abs(buf_body["accepted_buffer_m"] - 50.0) < 0.01, buf_body

    # Restore state='out' for the geofence-rejection assertion in step 9.
    # The latest in-punch breaks the out-direction idempotency chain so
    # this lands as a fresh out, not a debounced repeat.
    resp = client.post(
        "/api/sales/clock/out",
        headers=sales_headers,
        data={
            "client_latitude": inside_lat,
            "client_longitude": inside_lng,
        },
    )
    assert resp.status_code == 200, resp.text
    _punch_ids.append(resp.json()["id"])

    # ---- 8.6 Trusted-network detection (Slice C). Log-only first:
    # detection always runs, but acceptance only bypasses GPS when the
    # owner has flipped `trusted_network_enabled`. Uses an explicit
    # X-Forwarded-For so the TestClient (which fakes `client.host` to
    # the literal string "testclient") can present as a real IP. ----
    _set_trusted_network(enabled=False, ips=["203.0.113.5"])
    try:
        trusted_h = {**sales_headers, "X-Forwarded-For": "203.0.113.5"}
        untrusted_h = {**sales_headers, "X-Forwarded-For": "198.51.100.42"}

        # 8.6a: GPS-inside from a trusted IP. accepted_by stays 'gps'
        # (the strict radius did the work) but the audit flag captures
        # the network match so the owner sees the trusted list is
        # hitting reliably before flipping the bypass on.
        resp = client.post(
            "/api/sales/clock/in",
            headers=trusted_h,
            data={
                "client_latitude": inside_lat,
                "client_longitude": inside_lng,
                "client_accuracy_m": 12.5,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        _punch_ids.append(body["id"])
        assert body["accepted_by"] == "gps", body
        assert body["trusted_network_detected"] is True, body

        # Restore state='out' for the next assertion.
        resp = client.post(
            "/api/sales/clock/out",
            headers=trusted_h,
            data={
                "client_latitude": inside_lat,
                "client_longitude": inside_lng,
            },
        )
        assert resp.status_code == 200, resp.text
        _punch_ids.append(resp.json()["id"])
        # Out punches stamp `trusted_network_detected` too.
        assert resp.json()["trusted_network_detected"] is True

        # 8.6b: GPS-outside from a trusted IP while the toggle is OFF.
        # Detection-only: the punch is still rejected.
        resp = client.post(
            "/api/sales/clock/in",
            headers=trusted_h,
            data={
                "client_latitude": far_lat,
                "client_longitude": far_lng,
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "outside_geofence"

        # 8.6c: Flip the toggle on. Same outside-GPS punch from the
        # trusted IP now accepts via the bypass, recording the
        # `trusted_network` acceptance vocabulary.
        _set_trusted_network(enabled=True, ips=["203.0.113.5"])

        resp = client.post(
            "/api/sales/clock/in",
            headers=trusted_h,
            data={
                "client_latitude": far_lat,
                "client_longitude": far_lng,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        _punch_ids.append(body["id"])
        assert body["accepted_by"] == "trusted_network", body
        assert body["accepted_buffer_m"] is None, body
        assert body["trusted_network_detected"] is True, body
        # Real GPS distance is still recorded for audit even though the
        # bypass made the geofence non-load-bearing for acceptance.
        assert body["distance_to_location_m"] >= 4_500, body

        # Restore state='out' before continuing.
        resp = client.post(
            "/api/sales/clock/out",
            headers=trusted_h,
            data={
                "client_latitude": inside_lat,
                "client_longitude": inside_lng,
            },
        )
        assert resp.status_code == 200, resp.text
        _punch_ids.append(resp.json()["id"])

        # 8.6d: Untrusted IP, toggle ON, GPS outside — no bypass, the
        # punch is rejected. The trusted-network gate is the IP list,
        # not the toggle.
        resp = client.post(
            "/api/sales/clock/in",
            headers=untrusted_h,
            data={
                "client_latitude": far_lat,
                "client_longitude": far_lng,
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "outside_geofence"

        # 8.6e: Status endpoint reports per-request trusted-network
        # state so the UI can show "Connected through boutique
        # network" before the user has tapped anything.
        resp = client.get("/api/sales/clock/status", headers=trusted_h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trusted_network_enabled"] is True
        assert body["trusted_network_detected"] is True

        resp = client.get("/api/sales/clock/status", headers=untrusted_h)
        body = resp.json()
        assert body["trusted_network_enabled"] is True
        assert body["trusted_network_detected"] is False
    finally:
        # Restore singleton BP defaults so subsequent steps (and any
        # follow-up smokes) see the default-deny state.
        _set_trusted_network(enabled=False, ips=[])

    # ---- 9. Punch in OUTSIDE the geofence is rejected with the
    # closest-distance metadata. ----
    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={"client_latitude": far_lat, "client_longitude": far_lng},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "outside_geofence"
    assert detail["closest_location_name"] == "Probe boutique"
    assert detail["closest_location_radius_m"] == PROBE_RADIUS_M
    assert detail["distance_m"] >= 4_500

    # ---- 10. Deactivating the location prevents future clock-ins. ----
    resp = client.delete(
        f"/api/admin/staff-locations/{location_id}", headers=admin_headers
    )
    assert resp.status_code == 204, resp.text

    resp = client.post(
        "/api/sales/clock/in",
        headers=sales_headers,
        data={"client_latitude": inside_lat, "client_longitude": inside_lng},
    )
    # No active locations → outside_geofence with NULL closest_*
    # because the helper returns None when no active rows exist.
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "outside_geofence"
    assert detail["closest_location_name"] is None
    assert detail["distance_m"] is None

    # ---- 11. Sanity: ON DELETE RESTRICT on staff_punches.user_id
    # blocks user deletion while punches reference the user. ----
    db = SessionLocal()
    try:
        # We need a user with at least one punch.
        punch_user = (
            db.execute(
                select(StaffPunch.user_id)
                .where(StaffPunch.user_id == sales_id)
                .limit(1)
            )
            .scalar()
        )
        assert punch_user == sales_id

        try:
            db.execute(
                sql_text("DELETE FROM users WHERE id = :uid"),
                {"uid": sales_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError(
                "users DELETE was not blocked by RESTRICT while punches "
                "referenced the user"
            )
    finally:
        db.close()

    _track_punches()
    print("clock_in smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
