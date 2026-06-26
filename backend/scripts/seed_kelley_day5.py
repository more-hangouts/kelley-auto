"""Day 5 data unblock — seed Kelley Autoplex NAP + current vehicle inventory.

Source: a manually-assembled doc of public listing/NAP data
(`/home/deploy/Untitled document.docx`). No crawling. Prices and mileage
are unknown in the source ("Email for Price" / "Email for Mileage"), so
`unit_price_cents` and `mileage` are left NULL.

Idempotent:
  * NAP — `update_profile` is a plain UPSERT of the singleton; re-running
    just re-sets the same values.
  * Vehicles — each row is skipped if a CatalogItem already exists with the
    same VIN or stock_number (the source doc duplicates some blocks; we also
    dedupe the in-script list by VIN).

Goes through the SAME path the admin API uses: build a `CatalogItemCreate`
(runs `validate_create_shape`), call `.to_input()` (sets category='vehicle',
mirrors stock_number->internal_sku / exterior_color->color), then
`create_catalog_item`. One transaction; commit at the end.

Run:
    .venv/bin/python scripts/seed_kelley_day5.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from sqlalchemy import select  # noqa: E402

from api.routers.catalog import CatalogItemCreate  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from services import business_profile_service  # noqa: E402
from services.catalog_service import create_catalog_item  # noqa: E402


# ---------------------------------------------------------------------------
# NAP — name already "Kelley Autoplex"; this fills address/phone/email/site.
# NOTE: business_hours are NOT modeled on `business_profile` (no column, and
# the public NAP DTO has no hours key), so the doc's hours
# (Sun closed; Mon-Sat 9:00 AM - 7:00 PM CDT) are intentionally NOT seeded
# here — that storage remains an open gap. See the run summary.
# ---------------------------------------------------------------------------
NAP_PATCH = {
    "legal_name": "Kelley Autoplex",
    "display_name": "Kelley Autoplex",
    "address_line1": "5803 San Pedro Ave.",
    "address_line2": None,
    "city": "San Antonio",
    "state": "TX",
    "postal_code": "78023",
    "country": "US",
    "phone": "(210) 767-2408",
    "email": "chaserkelley@gmail.com",
    "website": "https://www.kelleyautoplex.com",
}


# ---------------------------------------------------------------------------
# Vehicles. `engine` text has no column on catalog_items, so it is preserved
# in `description_text` (an internal field). Prices/mileage stay NULL.
# ---------------------------------------------------------------------------
VEHICLES = [
    dict(
        year=2013, make="Ford", model="Edge", trim="SEL",
        body_type="SUV", condition="used",
        drivetrain="Front Wheel Drive", fuel_type="Gasoline",
        exterior_color="Ingot Silver Metallic", interior_color="Charcoal Black",
        vin="2FMDK3JC2DBC30676", stock_number="KEL-30676",
        vehicle_status="available",
        engine="3.5L V6 285hp 253ft. lbs.",
        features_json=[
            "Bluetooth", "Backup Camera", "Rear Parking Sensors",
            "Leather Steering Wheel", "Keyless Entry",
        ],
    ),
    dict(
        year=2011, make="Chrysler", model="Town and Country", trim="Touring-L",
        body_type="Van", condition="used",
        drivetrain="Front Wheel Drive", fuel_type="Flex Fuel",
        exterior_color="Black Clear Coat", interior_color="Black/Light Graystone",
        vin="2A4RR8DGXBR659483", stock_number="KEL-59483",
        vehicle_status="available",
        engine="Pentastar 3.6L Flex Fuel V6 283hp 260ft. lbs.",
        features_json=[
            "Leather Seats", "Heated Seats", "Backup Camera",
            "DVD Entertainment", "Power Sliding Doors", "Remote Start",
        ],
    ),
    dict(
        year=2008, make="Chrysler", model="Aspen", trim="Limited",
        body_type="SUV", condition="used",
        drivetrain="Rear Wheel Drive", fuel_type="Gasoline",
        exterior_color="Unknown", interior_color=None,
        vin="1A8HX58N08F106820", stock_number="KEL-06820",
        vehicle_status="available",
        engine="5.7L V8 335hp 370ft. lbs.",
        features_json=[
            "Third Row Seating", "Leather Trim", "Premium Audio",
            "Roof Rack", "Stability Control",
        ],
    ),
    dict(
        year=2004, make="Nissan", model="Armada", trim="LE",
        body_type="SUV", condition="used",
        drivetrain="Four Wheel Drive", fuel_type="Gasoline",
        exterior_color="Smoke", interior_color="Charcoal",
        vin="5N1AA08B14N719768", stock_number="KEL-19768",
        vehicle_status="available",
        engine="5L NA V8 double overhead cam (DOHC) 32V",
        features_json=[
            "Leather Seats", "Heated Seats", "Bose Audio",
            "Parking Sensors", "Roof Rack", "Trailer Hitch",
        ],
    ),
]


def _dedupe_by_vin(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        vin = r["vin"]
        if vin in seen:
            continue
        seen.add(vin)
        out.append(r)
    return out


def main() -> int:
    db = SessionLocal()
    try:
        # --- NAP ---
        before = business_profile_service.get_public_profile(db)
        business_profile_service.update_profile(db, patch=dict(NAP_PATCH))
        print(f"[nap] updated business_profile (was address={before['address']['line1']!r})")

        # --- Vehicles (idempotent by VIN or stock_number) ---
        created = 0
        skipped = 0
        for spec in _dedupe_by_vin(VEHICLES):
            vin = spec["vin"]
            stock = spec["stock_number"]
            existing = db.execute(
                select(CatalogItem.id, CatalogItem.public_code, CatalogItem.vin)
                .where((CatalogItem.vin == vin) | (CatalogItem.stock_number == stock))
            ).first()
            if existing:
                print(f"[skip] {spec['year']} {spec['make']} {spec['model']} "
                      f"vin={vin} already exists (id={existing[0]}, code={existing[1]})")
                skipped += 1
                continue

            payload = CatalogItemCreate(
                is_vehicle=True,
                year=spec["year"],
                make=spec["make"],
                model=spec["model"],
                trim=spec["trim"],
                body_type=spec["body_type"],
                condition=spec["condition"],
                drivetrain=spec["drivetrain"],
                fuel_type=spec["fuel_type"],
                exterior_color=spec["exterior_color"],
                interior_color=spec["interior_color"],
                vin=vin,
                stock_number=stock,
                vehicle_status=spec["vehicle_status"],
                description_text=f"Engine: {spec['engine']}",
                image_urls=[],
                features_json=spec["features_json"],
                # unit_price_cents / mileage intentionally omitted -> NULL.
            )
            item = create_catalog_item(db, payload.to_input())
            db.flush()
            print(f"[new]  {spec['year']} {spec['make']} {spec['model']} "
                  f"-> id={item.id} code={item.public_code} vin={vin} stock={stock}")
            created += 1

        db.commit()
        print(f"\nDone. vehicles created={created}, skipped(existing)={skipped}.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
