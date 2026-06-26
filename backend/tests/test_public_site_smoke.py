"""Smoke tests for the public vehicle inventory API (Day 4).

Proves the unauthenticated /api/public/inventory list + detail endpoints:
  - require NO staff auth,
  - show only for-sale cars (available by default; pending on request) and
    hide sold/delivered/hidden/wholesale/inactive/non-vehicle rows in the list,
  - serve detail for available/pending/sold/delivered but 404 hidden/
    wholesale/inactive/non-vehicle/unknown,
  - resolve detail by numeric id AND by listingCode (public_code),
  - never leak an internal field (internal_sku/stock_number/wholesale/
    designer/style_number/color/source_*) in any response,
  - honor make/price/year filters, sort, and pagination.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_public_site_smoke.py
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8].upper()
_STOCK_PREFIX = f"PUBSTK-{_TAG}-"
_DRESS_SKU = f"PUBDRESS-{_TAG}"

# Internal keys that must NEVER appear in a public DTO (snake_case storage
# names + the compat columns + the discriminator/category).
_FORBIDDEN_KEYS = {
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
    "is_vehicle",
    "active",
    "category",
}


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _assert_clean(dto: dict, label: str) -> None:
    leaked = _FORBIDDEN_KEYS & set(dto.keys())
    _assert(not leaked, f"{label}: forbidden keys leaked", leaked)
    flat = " ".join(str(v) for v in dto.values())
    _assert(_STOCK_PREFIX not in flat, f"{label}: stock value leaked", flat)


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"pub-{suffix}",
            email=f"pub-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Public Smoke Admin",
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


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _cleanup(user_id: int, baseline_seq: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM catalog_items "
                "WHERE internal_sku LIKE :stk OR internal_sku = :dress"
            ),
            {"stk": _STOCK_PREFIX + "%", "dress": _DRESS_SKU},
        )
        db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s WHERE id = 1"
            ),
            {"s": baseline_seq},
        )
        db.commit()
    finally:
        db.close()


def _vehicle(suffix, *, make, model, year, price_cents, status, active=True):
    return {
        "is_vehicle": True,
        "stock_number": _STOCK_PREFIX + suffix,
        "make": make,
        "model": model,
        "year": year,
        "mileage": 50000,
        "exterior_color": "Blue",
        "vehicle_status": status,
        "active": active,
        "unit_price_cents": price_cents,
    }


def main() -> int:  # noqa: C901 - linear smoke script
    baseline_seq = _get_seq()
    admin_id, admin_email = _make_admin()
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        # --- seed inventory across every status ---------------------------
        seeds = {
            "avail1": _vehicle("AV1", make="Toyota", model="Corolla", year=2018,
                               price_cents=1_500_000, status="available"),
            "avail2": _vehicle("AV2", make="Honda", model="Civic", year=2021,
                               price_cents=2_200_000, status="available"),
            "pending1": _vehicle("PEN", make="Toyota", model="Camry", year=2020,
                                 price_cents=1_999_500, status="pending"),
            "sold1": _vehicle("SOLD", make="Ford", model="F150", year=2019,
                              price_cents=3_000_000, status="sold"),
            "hidden1": _vehicle("HID", make="Kia", model="Soul", year=2017,
                                price_cents=900_000, status="hidden"),
            "wholesale1": _vehicle("WHS", make="Nissan", model="Versa", year=2016,
                                   price_cents=700_000, status="wholesale"),
            "inactive1": _vehicle("INA", make="Mazda", model="3", year=2019,
                                  price_cents=1_400_000, status="available",
                                  active=False),
        }
        ids: dict[str, int] = {}
        codes: dict[str, str] = {}
        for key, payload in seeds.items():
            resp = client.post("/api/catalog", headers=auth, json=payload)
            _assert(resp.status_code == 201, f"seed {key}", resp.text)
            ids[key] = resp.json()["id"]
            codes[key] = resp.json()["public_code"]

        # a non-vehicle dress row (is_vehicle boundary)
        resp = client.post(
            "/api/catalog",
            headers=auth,
            json={
                "internal_sku": _DRESS_SKU,
                "color": "Blush",
                "category": "quince_gown",
                "designer": "Morilee",
                "style_number": "M-9001",
                "product_title": "Public Boundary Gown",
            },
        )
        _assert(resp.status_code == 201, "seed dress", resp.text)
        dress_id = resp.json()["id"]
        print("seeded inventory ok")

        # --- list requires NO auth ---------------------------------------
        resp = client.get("/api/public/inventory")
        _assert(resp.status_code == 200, "public list unauth", resp.text)
        body = resp.json()
        listed_ids = {item["id"] for item in body["items"]}
        # available cars show; everything else is hidden from the list.
        _assert(ids["avail1"] in listed_ids, "avail1 listed", listed_ids)
        _assert(ids["avail2"] in listed_ids, "avail2 listed", listed_ids)
        for hidden_key in ("pending1", "sold1", "hidden1", "wholesale1", "inactive1"):
            _assert(
                ids[hidden_key] not in listed_ids,
                f"{hidden_key} must NOT list",
                listed_ids,
            )
        _assert(dress_id not in listed_ids, "dress must NOT list", listed_ids)
        print("list defaults to available + hides the rest ok")

        # --- list leaks no private field ---------------------------------
        for item in body["items"]:
            _assert_clean(item, "list item")
            _assert("listingCode" in item, "listingCode present", item)
            _assert("make" in item, "make present", item)
        print("list hides private fields ok")

        # --- status=pending surfaces pending, not available --------------
        resp = client.get("/api/public/inventory", params={"status": "pending"})
        _assert(resp.status_code == 200, "list pending", resp.text)
        pend_ids = {i["id"] for i in resp.json()["items"]}
        _assert(ids["pending1"] in pend_ids, "pending listed on request", pend_ids)
        _assert(ids["avail1"] not in pend_ids, "available excluded under pending", pend_ids)
        print("status=pending filter ok")

        # --- make filter (within default available) ----------------------
        resp = client.get("/api/public/inventory", params={"make": "toyota"})
        mk_ids = {i["id"] for i in resp.json()["items"]}
        _assert(ids["avail1"] in mk_ids, "Toyota Corolla in make filter", mk_ids)
        _assert(ids["avail2"] not in mk_ids, "Honda excluded by make filter", mk_ids)
        print("make filter ok")

        # --- price + year filters ----------------------------------------
        resp = client.get("/api/public/inventory", params={"min_price": 20000})
        pr_ids = {i["id"] for i in resp.json()["items"]}
        _assert(ids["avail2"] in pr_ids, "avail2 >= $20k", pr_ids)
        _assert(ids["avail1"] not in pr_ids, "avail1 < $20k excluded", pr_ids)

        resp = client.get("/api/public/inventory", params={"min_year": 2020})
        yr_ids = {i["id"] for i in resp.json()["items"]}
        _assert(ids["avail2"] in yr_ids, "avail2 year>=2020", yr_ids)
        _assert(ids["avail1"] not in yr_ids, "avail1 year<2020 excluded", yr_ids)
        print("price + year filters ok")

        # --- sort price_asc orders cheaper first -------------------------
        resp = client.get("/api/public/inventory", params={"sort": "price_asc"})
        order = [i["id"] for i in resp.json()["items"]]
        _assert(
            order.index(ids["avail1"]) < order.index(ids["avail2"]),
            "price_asc puts cheaper avail1 first",
            order,
        )
        print("sort ok")

        # --- pagination ---------------------------------------------------
        resp = client.get("/api/public/inventory", params={"limit": 1})
        page = resp.json()
        _assert(len(page["items"]) == 1, "limit=1 returns one row", page)
        _assert(page["total"] >= 2, "total counts all available", page)
        print("pagination ok")

        # --- detail by id + by listingCode -------------------------------
        resp = client.get(f"/api/public/inventory/{ids['avail1']}")
        _assert(resp.status_code == 200, "detail by id", resp.text)
        d = resp.json()
        _assert_clean(d, "detail by id")
        _assert(d["id"] == ids["avail1"], "detail id match", d)
        _assert(d["listingCode"] == codes["avail1"], "detail listingCode", d)

        resp = client.get(f"/api/public/inventory/{codes['avail1']}")
        _assert(resp.status_code == 200, "detail by listingCode", resp.text)
        _assert(resp.json()["id"] == ids["avail1"], "detail-by-code id match", resp.json())
        print("detail by id + listingCode ok")

        # --- sold detail still resolves ----------------------------------
        resp = client.get(f"/api/public/inventory/{ids['sold1']}")
        _assert(resp.status_code == 200, "sold detail resolves", resp.text)
        _assert_clean(resp.json(), "sold detail")
        print("sold detail resolves ok")

        # --- 404s: hidden / wholesale / inactive / dress / unknown -------
        for key in ("hidden1", "wholesale1", "inactive1"):
            resp = client.get(f"/api/public/inventory/{ids[key]}")
            _assert(resp.status_code == 404, f"{key} detail 404", resp.text)
        resp = client.get(f"/api/public/inventory/{dress_id}")
        _assert(resp.status_code == 404, "dress detail 404", resp.text)
        resp = client.get("/api/public/inventory/999999999")
        _assert(resp.status_code == 404, "unknown id 404", resp.text)
        resp = client.get(f"/api/public/inventory/{codes['hidden1']}")
        _assert(resp.status_code == 404, "hidden by code 404", resp.text)
        print("404 gating ok")

        # --- public business profile (NAP only, no operational fields) ----
        resp = client.get("/api/public/business-profile")
        _assert(resp.status_code == 200, "public business-profile", resp.text)
        prof = resp.json()
        for k in ("name", "address", "phone", "email", "website"):
            _assert(k in prof, f"profile has {k}", prof)
        _assert(isinstance(prof["address"], dict), "address is object", prof)
        # Operational/financial fields must NEVER appear.
        forbidden_profile = {
            "default_tax_rate",
            "default_tax_name",
            "default_invoice_terms",
            "default_invoice_footer",
            "default_payment_instructions",
            "reminder1_enabled",
            "attendance_gate_enabled",
            "trusted_clock_in_ips",
            "target_labor_pct",
            "updated_by_user_id",
        }
        leaked = forbidden_profile & set(prof.keys())
        _assert(not leaked, "business-profile leaks operational fields", leaked)
        print("public business-profile ok")

        print()
        print("public site smoke ok")
        return 0
    finally:
        _cleanup(admin_id, baseline_seq)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
