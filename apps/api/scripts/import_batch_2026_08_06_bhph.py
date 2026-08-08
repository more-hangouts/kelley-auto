"""Second 2026-08-06 batch from Luis — four buy-here-pay-here cars.

The list as sent was four VINs, and two of them were already on the lot:

  Frontier  1N6BD0CT0EN744241  -> KAP-00016, already available + bhph
  verano    1G4PP5SK3D4182986  -> KAP-00009, already available + bhph

Those two need no create. The Verano does get one edit: it has been sitting
with ``exterior_color = 'Unknown'`` since the CRM import, and the list
finally names it ("white verano"), so the placeholder is replaced.
Nothing names the Frontier's colour, so its placeholder stays.

That leaves two genuinely new cars, both bhph (the column default, so no
sale_type is set here — see migration 103):

  1FAFP3F21GL252801  2016 Ford Focus SE      "gray focus"
  1G6AA5RX4E0181046  2014 Cadillac ATS       "ATS"

**The Focus VIN does not pass its check digit** and is stored as sent. It
is structurally a valid VIN — 17 characters, no I/O/Q — and vPIC decodes it
to a 2016 Focus SE, but position 9 reads '1' where the checksum computes
'0'. That means exactly one character is wrong somewhere and this is NOT
guessable: 43 different single-character edits produce a valid check digit,
and about fifteen of them leave the year/make/model decode untouched, so
there is no unique correction the way there was for the Forester (whose
'b' in the check-digit slot was structurally impossible, leaving one
answer). Someone has to read it off the door jamb or the title. Stored
as-is deliberately, because a plausible guess would be worse than a known
question mark — a wrong VIN on a title application is a real problem.

No price or mileage was supplied for either car. Both are left NULL, which
matches every other bhph row (the storefront leads with the flat down
payment, not a sticker price).

Idempotent: skipped when the VIN or stock number already exists.

Run:
    .venv/bin/python scripts/import_batch_2026_08_06_bhph.py
    .venv/bin/python scripts/import_batch_2026_08_06_bhph.py --commit
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
from modules.inventory.services.catalog_service import (  # noqa: E402
    create_catalog_item,
    update_catalog_item,
)
from modules.inventory.services import vin as vin_util  # noqa: E402


# --- already on the lot: colour corrections only -------------------------
COLOUR_FIXES = {
    # "white verano" — replaces the 'Unknown' placeholder from the CRM import.
    "1G4PP5SK3D4182986": "White",
}

# --- new cars ------------------------------------------------------------
VEHICLES = [
    dict(
        year=2016, make="Ford", model="Focus", trim="SE",
        body_type="Sedan", condition="used",
        drivetrain="Front Wheel Drive", fuel_type="Gasoline",
        transmission="Automatic",
        exterior_color="Gray", interior_color=None,
        vin="1FAFP3F21GL252801",
        engine="2.0L I4 160hp",
    ),
    dict(
        year=2014, make="Cadillac", model="ATS", trim="Standard",
        body_type="Sedan", condition="used",
        drivetrain="Rear Wheel Drive", fuel_type="Gasoline",
        transmission="Automatic",
        exterior_color="Unknown", interior_color=None,
        vin="1G6AA5RX4E0181046",
        engine="2.0L Turbo I4",
    ),
]


def _stock_number(vin: str) -> str:
    """Match the convention already in the table: KEL- + last 6 of the VIN."""
    return f"KEL-{vin[-6:]}"


def main() -> int:
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        # --- colour corrections on rows that already exist ---------------
        fixed = 0
        for vin, colour in COLOUR_FIXES.items():
            row = db.execute(
                select(CatalogItem).where(CatalogItem.vin == vin)
            ).scalars().first()
            if row is None:
                print(f"[warn] colour fix skipped, no row for vin={vin}")
                continue
            if row.exterior_color == colour:
                print(f"[same] {row.public_code} already {colour!r}")
                continue
            was = row.exterior_color
            update_catalog_item(
                db, catalog_item_id=row.id, patch={"exterior_color": colour}
            )
            print(f"[edit] {row.public_code} {row.year} {row.make} {row.model}: "
                  f"exterior_color {was!r} -> {colour!r}")
            fixed += 1

        # --- new cars ----------------------------------------------------
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
                vehicle_status="available",
                description_text=f"Engine: {spec['engine']}",
                image_urls=[],
                features_json=[],
                # sale_type omitted -> column default 'bhph'.
                # unit_price_cents / mileage omitted -> NULL, as every
                # other bhph row.
            )
            item = create_catalog_item(db, payload.to_input())
            db.flush()
            warn = (
                "  ** CHECK DIGIT FAILS — verify against the door jamb **"
                if not vin_util.vin_check_digit_ok(vin) else ""
            )
            print(f"[new]  {label} -> id={item.id} code={item.public_code} "
                  f"vin={vin} stock={stock} sale_type={item.sale_type}{warn}")
            created += 1

        if commit:
            db.commit()
            print(f"\nCommitted. created={created}, skipped(existing)={skipped}, "
                  f"colours fixed={fixed}.")
        else:
            db.rollback()
            print(f"\nDRY RUN — nothing written. would create={created}, "
                  f"skip={skipped}, fix={fixed}. Re-run with --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
