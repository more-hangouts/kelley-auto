"""Add the five vehicles texted in on 2026-08-05 by (210) 723-9550.

Source: a wholesaler's SMS listing five units with mileage, asking price and
VIN. Year/make/model/trim/body/drivetrain/engine here are NOT the seller's
words — every VIN was run through NHTSA vPIC (`services/vin_decode`) and the
decoded values win. Two corrections came out of that:

  * The "2005 Ford F-150" (1FTRX12W57KD18625) is a **2007** — position 10 is
    `7`. Confirmed back with the seller over text ("It's a 2007").
  * The Subaru VIN arrived as `JF2 SJAECb EH476777`. The `b` sits in position
    9, the check digit, which may only be 0-9 or X — so it is a transcription
    slip, not a VIN. The rest of the VIN determines the check digit uniquely:
    it is `6`, giving `JF2SJAEC6EH476777`, which vPIC decodes clean.

Colors: vPIC does not carry paint. The F-150, Celica and CC are read off the
photo sets Luis sent; the Volvo and the Forester stay "Unknown" until someone
eyeballs them on the lot (the same placeholder the Day-5 seed used).

The Celica's body style is worth noting: vPIC calls it a Convertible and the
photos back that up (soft top, up) — the text only said "1997 Toyota celica".

Vehicles are created **hidden**, not available: prices and mileage came from
a text message and four of the five have no photos or verified color yet.
Flip `vehicle_status` to `available` in the admin once they've been checked.

Idempotent: a row is skipped when its VIN or stock number already exists.
Goes through the same path the admin API uses (`CatalogItemCreate` ->
`.to_input()` -> `create_catalog_item`). One transaction, commit at the end.

Run:
    .venv/bin/python scripts/import_batch_2026_08_05.py            # dry-run
    .venv/bin/python scripts/import_batch_2026_08_05.py --commit
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

from modules.inventory.routers.catalog import CatalogItemCreate  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from modules.inventory.services.catalog_service import create_catalog_item  # noqa: E402


VEHICLES = [
    dict(
        # Seller said 2005; VIN position 10 = '7' -> 2007, seller confirmed.
        year=2007, make="Ford", model="F-150", trim="Styleside",
        body_type="Pickup", condition="used",
        drivetrain="Rear Wheel Drive", fuel_type="Gasoline",
        transmission="Automatic",
        exterior_color="Black", interior_color="Tan/Gray Cloth",
        vin="1FTRX12W57KD18625",
        mileage=150_000, unit_price_cents=590_000,
        engine="4.6L V8 231hp",
        features_json=[
            "Camper Shell", "Extended Cab", "Chrome Running Boards",
            "Aftermarket Alloy Wheels", "All-Terrain Tires",
            "Tow Hitch Receiver", "Cruise Control", "Cloth Seats",
        ],
    ),
    dict(
        year=1997, make="Toyota", model="Celica", trim=None,
        body_type="Convertible", condition="used",
        drivetrain=None, fuel_type="Gasoline",
        transmission=None,
        exterior_color="Black", interior_color=None,
        vin="JT5FG02T1V0041639",
        mileage=157_000, unit_price_cents=650_000,
        engine="2.2L 5S-FE I4 130hp",
        features_json=[],
    ),
    dict(
        year=2012, make="Volkswagen", model="CC", trim="Sport",
        body_type="Sedan", condition="used",
        drivetrain=None, fuel_type="Gasoline",
        transmission="Automatic",
        exterior_color="Brown Metallic", interior_color=None,
        vin="WVWMN7AN5CE505688",
        mileage=154_000, unit_price_cents=499_900,
        engine="2.0L Turbo I4 200hp",
        features_json=[],
    ),
    dict(
        year=2013, make="Volvo", model="S60", trim=None,
        body_type="Sedan", condition="used",
        drivetrain="Front Wheel Drive", fuel_type="Gasoline",
        transmission=None,
        exterior_color="Unknown", interior_color=None,
        vin="YV1612FS4D2192118",
        mileage=140_000, unit_price_cents=539_900,
        engine="2.5L B5254T12 I5 250hp",
        features_json=[],
    ),
    dict(
        # Texted as "JF2 SJAECb EH476777"; 'b' fell in the check-digit slot.
        # The computed check digit for this VIN is 6.
        year=2014, make="Subaru", model="Forester", trim="Premium",
        body_type="SUV", condition="used",
        drivetrain="All Wheel Drive", fuel_type="Gasoline",
        transmission="Automatic (CVT)",
        exterior_color="Unknown", interior_color=None,
        vin="JF2SJAEC6EH476777",
        mileage=170_000, unit_price_cents=599_900,
        engine="2.5L H4",
        features_json=[],
    ),
]


def _stock_number(vin: str) -> str:
    """Match the convention already in the table: KEL- + last 6 of the VIN."""
    return f"KEL-{vin[-6:]}"


def main() -> int:
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        created = skipped = 0
        for spec in VEHICLES:
            vin = spec["vin"]
            stock = _stock_number(vin)
            label = f"{spec['year']} {spec['make']} {spec['model']}"

            existing = db.execute(
                select(CatalogItem.id, CatalogItem.public_code)
                .where((CatalogItem.vin == vin) | (CatalogItem.stock_number == stock))
            ).first()
            if existing:
                print(f"[skip] {label} vin={vin} already exists "
                      f"(id={existing[0]}, code={existing[1]})")
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
                transmission=spec["transmission"],
                exterior_color=spec["exterior_color"],
                interior_color=spec["interior_color"],
                vin=vin,
                stock_number=stock,
                mileage=spec["mileage"],
                unit_price_cents=spec["unit_price_cents"],
                vehicle_status="hidden",
                description_text=f"Engine: {spec['engine']}",
                image_urls=[],
                features_json=spec["features_json"],
            )
            item = create_catalog_item(db, payload.to_input())
            db.flush()
            price = spec["unit_price_cents"] / 100
            print(f"[new]  {label} -> id={item.id} code={item.public_code} "
                  f"vin={vin} stock={stock} ${price:,.2f} "
                  f"{spec['mileage']:,} mi (hidden)")
            created += 1

        if commit:
            db.commit()
            print(f"\nCommitted. created={created}, skipped(existing)={skipped}.")
        else:
            db.rollback()
            print(f"\nDRY RUN — nothing written. would create={created}, "
                  f"skip={skipped}. Re-run with --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
