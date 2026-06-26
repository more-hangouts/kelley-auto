"""Smoke tests for the Kelley Autoplex vehicle inventory overlay.

Day 1 / migration 085 reuses `catalog_items` for vehicles. This smoke
proves a car can be created, patched, listed, and searched through the
staff API; that the uniqueness/validation guards fire; that the
`is_vehicle` discriminator keeps a (backfilled) non-vehicle row out of the
vehicle list/search; and that the public DTO leaks no internal field.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_vehicle_inventory_smoke.py
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
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from datetime import datetime, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem, User  # noqa: E402
import base64  # noqa: E402

from services import document_storage  # noqa: E402
from services.catalog_service import public_vehicle_dto  # noqa: E402

client = TestClient(app)

# Unique run tag so cleanup only deletes this run's rows. Stock numbers
# and VINs embed it; internal_sku derives from stock_number for vehicles.
_TAG = uuid.uuid4().hex[:8].upper()
_STOCK_PREFIX = f"KAXSTK-{_TAG}-"
_DRESS_SKU = f"KAXDRESS-{_TAG}"
# A syntactically valid 17-char VIN base; we vary the last chars per row.
_VIN_A = f"1HGCM82633A0{_TAG[:5]}"  # 17 chars
_VIN_DUP = _VIN_A


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_user(role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"veh-{role}-{suffix}",
            email=f"veh-{role}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Vehicle {role}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id, user.email
    finally:
        db.close()


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    _assert(resp.status_code == 200, "login", resp.text)
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


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


def _cleanup(user_ids: list[int], baseline_seq: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM catalog_items "
                "WHERE internal_sku LIKE :stk OR internal_sku = :dress"
            ),
            {"stk": _STOCK_PREFIX + "%", "dress": _DRESS_SKU},
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


def _vehicle_payload(stock_suffix: str, *, vin: str | None) -> dict:
    payload = {
        "is_vehicle": True,
        "stock_number": _STOCK_PREFIX + stock_suffix,
        "make": "Toyota",
        "model": "Camry",
        "trim": "LE",
        "year": 2019,
        "mileage": 82214,
        "transmission": "Automatic",
        "fuel_type": "Gas",
        "exterior_color": "White",
        "interior_color": "Black",
        "body_type": "Sedan",
        "drivetrain": "FWD",
        "condition": "used",
        "vehicle_status": "available",
        "unit_price_cents": 1499500,
        "carfax_url": "https://carfax.example/x",
        "video_url": "https://video.example/x",
        "features_json": ["Bluetooth", "Backup Camera"],
        "image_urls": ["https://example.com/car-front.jpg"],
    }
    if vin is not None:
        payload["vin"] = vin
    return payload


def main() -> int:  # noqa: C901 - linear smoke script
    baseline_seq = _get_seq()
    admin_id, admin_email = _make_user("admin")
    try:
        admin = _login(admin_email)
        print("login ok")

        # --- 1. Create a vehicle -------------------------------------
        resp = client.post(
            "/api/catalog", headers=admin, json=_vehicle_payload("A", vin=_VIN_A)
        )
        _assert(resp.status_code == 201, "create vehicle", resp.text)
        veh = resp.json()
        _assert(veh["is_vehicle"] is True, "is_vehicle true", veh)
        _assert(veh["category"] == "vehicle", "category=vehicle", veh)
        _assert(veh["vin"] == _VIN_A, "vin round-trip", veh)
        _assert(
            veh["stock_number"] == _STOCK_PREFIX + "A", "stock round-trip", veh
        )
        # compat mirroring: internal_sku<-stock_number, color<-exterior_color,
        # designer<-make, style_number<-model
        _assert(
            veh["internal_sku"] == _STOCK_PREFIX + "A", "internal_sku<-stock", veh
        )
        _assert(veh["color"] == "White", "color<-exterior_color", veh)
        _assert(veh["designer"] == "Toyota", "designer<-make", veh)
        _assert(veh["style_number"] == "Camry", "style_number<-model", veh)
        _assert(veh["public_code"].startswith("BVX-"), "public_code minted", veh)
        veh_id = veh["id"]
        print("create vehicle ok")

        # --- 1b. Photo upload (local storage) ------------------------
        _png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4"
            "2mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        resp = client.post(
            f"/api/catalog/{veh_id}/photos",
            headers=admin,
            files={"file": ("front.png", _png, "image/png")},
        )
        _assert(resp.status_code == 201, "photo upload", resp.text)
        _imgs = resp.json()["image_urls"]
        _assert(
            _imgs[-1].startswith(f"/api/public/media/vehicles/{veh_id}/")
            and _imgs[-1].endswith(".png"),
            "uploaded photo appended to image_urls as relative path",
            _imgs,
        )
        _key = _imgs[-1].replace("/api/public/media/", "")
        # public detail exposes the uploaded photo as an ABSOLUTE url
        pdto = client.get(f"/api/public/inventory/{veh['public_code']}").json()
        _assert(
            pdto["photos"]
            and pdto["photos"][-1].startswith("http")
            and pdto["photos"][-1].endswith(_key.rsplit("/", 1)[-1]),
            "public DTO resolves uploaded photo to absolute url",
            pdto.get("photos"),
        )
        # media route serves it; logo and traversal are NOT public
        _m = client.get(f"/api/public/media/{_key}")
        _assert(_m.status_code == 200, "public media serves photo", _m.status_code)
        _assert(
            _m.headers.get("content-type") == "image/png",
            "media content-type",
            _m.headers.get("content-type"),
        )
        _assert(
            client.get("/api/public/media/business/logo.png").status_code == 404,
            "public media is scoped to vehicles/ (logo not exposed)",
        )
        # non-image is rejected
        resp = client.post(
            f"/api/catalog/{veh_id}/photos",
            headers=admin,
            files={"file": ("notes.txt", b"not an image", "text/plain")},
        )
        _assert(resp.status_code == 415, "non-image photo rejected", resp.text)
        # reorder/clear via PATCH image_urls still works; clear + delete file
        resp = client.patch(
            f"/api/catalog/{veh_id}", headers=admin, json={"image_urls": []}
        )
        _assert(resp.status_code == 200, "clear image_urls", resp.text)
        document_storage.delete_object(_key)
        print("photo upload + public serve ok")

        # --- 2. Patch the vehicle ------------------------------------
        resp = client.patch(
            f"/api/catalog/{veh_id}",
            headers=admin,
            json={
                "unit_price_cents": 1399500,
                "mileage": 83000,
                "vehicle_status": "hidden",  # hidden without being sold
                "features_json": ["Bluetooth", "Backup Camera", "Sunroof"],
            },
        )
        _assert(resp.status_code == 200, "patch vehicle", resp.text)
        patched = resp.json()
        _assert(patched["unit_price_cents"] == 1399500, "patch price", patched)
        _assert(patched["vehicle_status"] == "hidden", "patch status", patched)
        _assert(len(patched["features_json"]) == 3, "patch features", patched)
        # back to available so it shows in listings below
        resp = client.patch(
            f"/api/catalog/{veh_id}",
            headers=admin,
            json={"vehicle_status": "available"},
        )
        _assert(resp.status_code == 200, "patch back to available", resp.text)
        print("patch vehicle ok")

        # --- 3. List vehicles (group=vehicle) ------------------------
        resp = client.get(
            "/api/catalog",
            headers=admin,
            params={"group": "vehicle", "limit": 200},
        )
        _assert(resp.status_code == 200, "list vehicles", resp.text)
        ids = {row["id"] for row in resp.json()}
        _assert(veh_id in ids, "vehicle in group=vehicle list", ids)
        _assert(
            all(row["category"] == "vehicle" for row in resp.json()),
            "group=vehicle returns only vehicles",
            resp.json(),
        )
        print("list vehicles ok")

        # --- 4. Search by make / model / VIN / stock_number ----------
        for term, label in (
            ("Toyota", "make"),
            ("Camry", "model"),
            (_VIN_A, "VIN"),
            (_STOCK_PREFIX + "A", "stock_number"),
        ):
            resp = client.get(
                "/api/catalog",
                headers=admin,
                params={"q": term, "limit": 200},
            )
            _assert(resp.status_code == 200, f"search by {label}", resp.text)
            found = {row["id"] for row in resp.json()}
            _assert(veh_id in found, f"search by {label} finds vehicle", found)
        print("search by make/model/VIN/stock_number ok")

        # --- 5. Duplicate non-empty VIN blocked ----------------------
        resp = client.post(
            "/api/catalog",
            headers=admin,
            json=_vehicle_payload("B", vin=_VIN_DUP),
        )
        _assert(resp.status_code == 409, "duplicate VIN blocked", resp.text)
        print("duplicate non-empty VIN blocked ok")

        # --- 5b. Empty VIN allowed twice (manual early entry) --------
        for suffix in ("EMPTY1", "EMPTY2"):
            resp = client.post(
                "/api/catalog",
                headers=admin,
                json=_vehicle_payload(suffix, vin=""),
            )
            _assert(
                resp.status_code == 201,
                f"empty VIN allowed ({suffix})",
                resp.text,
            )
        print("empty VIN allowed (two rows) ok")

        # --- 6. Duplicate stock_number blocked -----------------------
        dup_stock = _vehicle_payload("A", vin="")  # same stock as row A
        resp = client.post("/api/catalog", headers=admin, json=dup_stock)
        _assert(resp.status_code == 409, "duplicate stock_number blocked", resp.text)
        print("duplicate stock_number blocked ok")

        # --- 7. Validation rejections (422) --------------------------
        bad_cases = {
            "bad status": {"vehicle_status": "for_sale"},
            "negative mileage": {"mileage": -5},
            "out-of-range year": {"year": 1979},
            "future year": {"year": datetime.now(timezone.utc).year + 2},
            "short VIN": {"vin": "TOOSHORT"},
            "non-list features": {"features_json": {"k": "v"}},
        }
        for label, override in bad_cases.items():
            body = _vehicle_payload("BAD", vin="")
            body.update(override)
            resp = client.post("/api/catalog", headers=admin, json=body)
            _assert(
                resp.status_code == 422,
                f"reject {label} -> 422",
                f"got {resp.status_code}: {resp.text}",
            )
        print("validation rejections ok")

        # --- 8. is_vehicle boundary: a non-vehicle dress row ---------
        # Created with vehicle_status='available' to mimic the 085 backfill,
        # but is_vehicle stays false so it must NOT surface as a car.
        dress = {
            "internal_sku": _DRESS_SKU,
            "designer": "Morilee",
            "style_number": "M-2080",
            "color": "Blush",
            "category": "quince_gown",
            "product_title": "Quince Gown Smoke Row",
            "image_urls": ["https://example.com/gown.jpg"],
        }
        resp = client.post("/api/catalog", headers=admin, json=dress)
        _assert(resp.status_code == 201, "create dress row", resp.text)
        dress_row = resp.json()
        _assert(dress_row["is_vehicle"] is False, "dress is_vehicle false", dress_row)
        dress_id = dress_row["id"]
        # Simulate the backfill having set vehicle_status='available' on it.
        db = SessionLocal()
        try:
            db.execute(
                sql_text(
                    "UPDATE catalog_items SET vehicle_status='available' "
                    "WHERE id = :id"
                ),
                {"id": dress_id},
            )
            db.commit()
        finally:
            db.close()
        # group=vehicle must exclude it
        resp = client.get(
            "/api/catalog",
            headers=admin,
            params={"group": "vehicle", "limit": 200},
        )
        veh_ids = {row["id"] for row in resp.json()}
        _assert(
            dress_id not in veh_ids,
            "backfilled dress excluded from vehicle list",
            veh_ids,
        )
        # search by its gown identifiers still finds it (catalog not broken),
        # but it is not a vehicle row
        resp = client.get(
            "/api/catalog", headers=admin, params={"q": "M-2080", "limit": 50}
        )
        gown_hits = {row["id"]: row for row in resp.json()}
        _assert(dress_id in gown_hits, "dress still searchable", gown_hits)
        _assert(
            gown_hits[dress_id]["is_vehicle"] is False,
            "dress hit is not a vehicle",
            gown_hits[dress_id],
        )
        # and it can be patched with NULL vehicle fields intact
        resp = client.patch(
            f"/api/catalog/{dress_id}",
            headers=admin,
            json={"product_title": "Quince Gown Renamed"},
        )
        _assert(resp.status_code == 200, "patch dress row", resp.text)
        print("is_vehicle boundary (backfilled dress excluded) ok")

        # --- 9. Public DTO leaks no internal field -------------------
        db = SessionLocal()
        try:
            item = db.get(CatalogItem, veh_id)
            dto = public_vehicle_dto(item)
        finally:
            db.close()
        forbidden = {
            "internal_sku",
            "stock_number",
            "wholesale_cents",
            "wholesale_as_of",
            "wholesale_source",
            "designer",
            "style_number",
            "color",
            "source_platform",
            "source_product_id",
            "source_product_url",
        }
        leaked = forbidden & set(dto.keys())
        _assert(not leaked, "public DTO leaks internal keys", leaked)
        # values: the internal_sku/stock string must not appear in any value
        flat = " ".join(str(v) for v in dto.values())
        _assert(_STOCK_PREFIX not in flat, "stock value leaked into DTO", flat)
        _assert(dto["listingCode"].startswith("BVX-"), "listingCode present", dto)
        _assert(dto["make"] == "Toyota", "DTO make", dto)
        print("public DTO clean ok")

        print()
        print("vehicle inventory smoke ok")
        return 0
    finally:
        _cleanup([admin_id], baseline_seq)


if __name__ == "__main__":
    sys.exit(main())
