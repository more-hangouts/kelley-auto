"""Import Kelley Autoplex's active inventory (26 cars) from the dealer's own
Carsforsale dashboard export (pasted by the owner — not scraped).

Idempotent upsert keyed on VIN (or stock_number when VIN is unavailable):
  * new vehicle  -> create via the admin CatalogItemCreate path
  * existing one -> patch in the now-known mileage + transmission (without
    clobbering richer fields the Day-5 docx seed already set, e.g. colors)

Data available from the dashboard LIST view: year/make/model/trim/body,
mileage, transmission, engine, VIN. NOT available here (follow-ups):
  - price  -> all "Email for Price"  -> unit_price_cents stays NULL
  - exterior/interior color -> NEW rows get "Unknown" (required field)
  - photos -> none in the list view  -> image_urls empty (import later)

One row (2018 Nissan Altima) has a masked VIN (…XXXXXX) in the export, so it
is imported VIN-less and deduped on a synthetic stock number instead.

Run:
    .venv/bin/python scripts/import_active_inventory.py
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
from services.catalog_service import (  # noqa: E402
    create_catalog_item,
    update_catalog_item,
)


def _flex(engine: str) -> str:
    return "Flex Fuel" if "flex fuel" in engine.lower() else "Gasoline"


# year, make, model, trim, body, vin, mileage, transmission, engine[, drivetrain]
_RAW = [
    (2013, "Ford", "Edge", "SEL", "SUV", "2FMDK3JC2DBC30676", 124223, "6-Speed Shiftable Automatic", "3.5L V6 285hp 253ft. lbs."),
    (2011, "Chrysler", "Town and Country", "Touring-L", "Van", "2A4RR8DGXBR659483", 191013, "6-Speed Shiftable Automatic", "Pentastar 3.6L Flex Fuel V6 283hp 260ft. lbs."),
    (2010, "Chevrolet", "Camaro", "LT", "Coupe", "2G1FC1EV5A9123305", 132516, "6-Speed Manual", "3.6L V6 304hp 273ft. lbs."),
    (2008, "Chrysler", "Aspen", "Limited", "SUV", "1A8HX58N08F106820", 121436, "5-Speed Automatic", "5.7L V8 335hp 370ft. lbs."),
    (2004, "Nissan", "Armada", "LE", "SUV", "5N1AA08B14N719768", 166107, "5-Speed Automatic", "5L NA V8 double overhead cam (DOHC) 32V"),
    (2016, "GMC", "Acadia", "SLT-1", "SUV", "1GKKRRKD9GJ116521", 164674, "6-Speed Shiftable Automatic", "3.6L V6 281hp 266ft. lbs."),
    (2012, "Dodge", "Challenger", "R/T", "Coupe", "2C3CDYBT3CH293923", 156305, "6-Speed Manual", "HEMI 5.7L V8 372hp 401ft. lbs."),
    (2015, "Nissan", "Rogue", "S", "Crossover", "KNMAT2MT7FP551425", 118258, "CVT", "2.5L I4 170hp 175ft. lbs."),
    (2013, "Buick", "Verano", "Base", "Sedan", "1G4PP5SK3D4182986", 103582, "6-Speed Shiftable Automatic w/Overdrive", "Ecotec 2.4L Flex Fuel I4 180hp 171ft. lbs."),
    (2016, "Mazda", "Mazda3", "i Touring", "Sedan", "3MZBM1V76GM245155", 177049, "6-Speed Shiftable Automatic", "SKYACTIV-G 2.0L I4 155hp 150ft. lbs. PZEV"),
    (2006, "HUMMER", "H2", "Base", "SUV", "5GRGN23U56H112876", 154220, "4-Speed Automatic", "6.0L NA V8 overhead valves (OHV) 16V"),
    (2011, "Audi", "Q5", "3.2 quattro Prestige", "SUV", "WA1WKAFP3BA029837", 155391, "6-speed Automatic with Tiptronic", "3.2L V6 270hp 243ft. lbs.", "All Wheel Drive"),
    (2015, "Volvo", "XC60", "T5 Premier", "SUV", "YV4612RK7F2643124", 123055, "6-Speed Shiftable Automatic", "2.5L Turbo I5 250hp 266ft. lbs. ULEV"),
    (2016, "Nissan", "Altima", "2.5 SR", "Sedan", "1N4AL3AP8GN323904", 143469, "CVT", "2.5L I4 182hp 180ft. lbs."),
    (2016, "Ford", "Escape", "S", "SUV", "1FMCU0F73GUC41796", 129159, "6-Speed Shiftable Automatic", "Duratec 2.5L I4 168hp 170ft. lbs."),
    (2014, "Nissan", "Frontier", "S", "Truck", "1N6BD0CT0EN744241", 167430, "5-Speed Automatic", "2.5L I4 152hp 171ft. lbs."),
    (2014, "Ford", "Escape", "SE", "SUV", "1FMCU0GX4EUB97299", 104675, "6-Speed Shiftable Automatic", "EcoBoost 2.0L Turbo I4 240hp 270ft. lbs."),
    (2015, "Buick", "Encore", "Base", "SUV", "KL4CJASBXFB245474", 160429, "6-Speed Automatic", "Ecotec 1.4L Turbo I4 138hp 148ft. lbs."),
    (2010, "INFINITI", "G37 Sedan", "Base", "Sedan", "JN1CV6AP8AM408286", 174220, "7-Speed Shiftable Automatic", "3.7L V6 328hp 269ft. lbs."),
    (2018, "Dodge", "Journey", "SE", "SUV", "3C4PDCAB1JT385870", 121820, "4-Speed Shiftable Automatic", "2.4L I4 173hp 166ft. lbs."),
    (2015, "INFINITI", "Q70L", "3.7", "Sedan", "JN1BY1PP6FM600150", 138972, "7-Speed Shiftable Automatic", "3.7L V6 330hp 270ft. lbs."),
    (2014, "Kia", "Sportage", "LX", "SUV", "KNDPB3AC0E7549585", 153160, "6-Speed Automatic Sportmatic", "2.4L I4 182hp 177ft. lbs."),
    (2013, "Cadillac", "XTS", "Premium Collection", "Sedan", "2G61S5S3XD9143801", 149180, "6-Speed Shiftable Automatic", "3.6L V6 304hp 264ft. lbs."),
    (2019, "Chevrolet", "Trax", "LS", "Crossover", "3GNCJKSB6KL374226", 133880, "6-Speed Shiftable Automatic", "Ecotec 1.4L Turbo I4 138hp 148ft. lbs."),
    (2016, "Mazda", "Mazda3", "i Grand Touring", "Sedan", "JM1BM1X7XG1343862", 72027, "6-Speed Shiftable Automatic", "SKYACTIV-G 2.0L I4 155hp 150ft. lbs. PZEV"),
    # Masked VIN in the export -> import VIN-less, dedupe on stock.
    (2018, "Nissan", "Altima", "2.5 SR", "Sedan", None, 78000, "CVT", "2.5L I4 179hp 177ft. lbs."),
]


def _records():
    out = []
    for row in _RAW:
        year, make, model, trim, body, vin, mileage, trans, engine = row[:9]
        drivetrain = row[9] if len(row) > 9 else None
        stock = f"KEL-{vin[-6:].upper()}" if vin else "KEL-2018ALTSR-NOVIN"
        out.append(
            dict(
                year=year, make=make, model=model, trim=trim, body_type=body,
                vin=vin, stock_number=stock, mileage=mileage,
                transmission=trans, engine=engine, drivetrain=drivetrain,
                fuel_type=_flex(engine),
            )
        )
    return out


def main() -> int:
    db = SessionLocal()
    created = updated = 0
    try:
        for spec in _records():
            vin, stock = spec["vin"], spec["stock_number"]
            cond = CatalogItem.vin == vin if vin else CatalogItem.stock_number == stock
            existing = db.execute(
                select(CatalogItem).where(cond)
            ).scalars().first()

            label = f"{spec['year']} {spec['make']} {spec['model']}"
            if existing is not None:
                # Backfill the now-known fields; don't clobber colors/features.
                patch: dict = {}
                if existing.mileage is None and spec["mileage"] is not None:
                    patch["mileage"] = spec["mileage"]
                if not existing.transmission and spec["transmission"]:
                    patch["transmission"] = spec["transmission"]
                if not existing.fuel_type and spec["fuel_type"]:
                    patch["fuel_type"] = spec["fuel_type"]
                if patch:
                    update_catalog_item(db, catalog_item_id=existing.id, patch=patch)
                    print(f"[upd]  {label} id={existing.id} {list(patch)}")
                    updated += 1
                else:
                    print(f"[skip] {label} id={existing.id} (already complete)")
                continue

            payload = CatalogItemCreate(
                is_vehicle=True,
                year=spec["year"], make=spec["make"], model=spec["model"],
                trim=spec["trim"], body_type=spec["body_type"],
                condition="used", drivetrain=spec["drivetrain"],
                fuel_type=spec["fuel_type"],
                exterior_color="Unknown", interior_color=None,
                transmission=spec["transmission"],
                vin=vin, stock_number=stock, vehicle_status="available",
                mileage=spec["mileage"],
                description_text=f"Engine: {spec['engine']}",
                image_urls=[], features_json=[],
                # unit_price_cents omitted -> NULL ("Email for Price")
            )
            item = create_catalog_item(db, payload.to_input())
            db.flush()
            print(f"[new]  {label} -> id={item.id} {item.public_code} "
                  f"vin={vin or '(masked)'} {spec['mileage']:,} mi")
            created += 1

        db.commit()
        total = db.execute(
            select(CatalogItem.id).where(
                CatalogItem.is_vehicle.is_(True),
                CatalogItem.vehicle_status == "available",
            )
        ).all()
        print(f"\nDone. created={created} updated={updated} "
              f"| available vehicles now={len(total)}")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
