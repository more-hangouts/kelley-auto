"""Photos + alt text for the 2026-08-06 bhph batch (Focus, ATS, Verano, Frontier).

Four folders landed in /home/deploy. Their names are unreliable and are NOT
what maps a folder to a car — the VIN is:

    ford focus/       ->  1FAFP3F21GL252801   2016 Ford Focus SE
    caddillac/        ->  1G6AA5RX4E0181046   2014 Cadillac ATS   (sic)
    buic verano/      ->  1G4PP5SK3D4182986   2013 Buick Verano   (sic)
    toyota frontier/  ->  1N6BD0CT0EN744241   2014 NISSAN Frontier

That last one is not a Toyota. The photos show Nissan badges front, rear
and on the wheel caps, and the VIN's 1N6 WMI is Nissan North America.

**The Verano and Frontier already had photos, and these REPLACE them.**
Both carried nine images from the 2026-06-30 CRM scrape at 1280x720 /
1170x644 and ~150 KB — fine as a placeholder, visibly poor next to a 2400px
shoot, and covering the same angles. Luis chose replacement over keeping
both. The old rows' URLs are cleared through ``update_catalog_item`` so the
service prunes their alt-text keys and reports the orphaned media keys,
which are then deleted from disk. This is destructive and deliberate: after
this runs the scrape images are gone.

Alt text is written per photo from looking at each frame — same house style
as the 2026-08-05 batch (lead with the subject, name the angle, colour and
body style on exteriors, no sales language).

Two condition notes are stated plainly in the alt text rather than
airbrushed, because the photos show them and a shopper will see them:
the Focus's cracked rear bumper, and the Frontier's chipped wheel paint.

Idempotent: a car whose photo count already matches its new set is skipped.
Dry-run by default.

Run:
    .venv/bin/python scripts/attach_batch_2026_08_06_photos.py
    .venv/bin/python scripts/attach_batch_2026_08_06_photos.py --commit
"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")
os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from PIL import Image, ImageFile, ImageOps  # noqa: E402
from sqlalchemy import select  # noqa: E402

ImageFile.LOAD_TRUNCATED_IMAGES = True

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from modules.inventory.services.catalog_service import (  # noqa: E402
    _VEHICLE_PHOTO_MAX_DIMENSION,
    add_vehicle_photo,
    delete_vehicle_media_keys,
    set_vehicle_photo_alts,
    update_catalog_item,
)

_FOCUS = Path("/home/deploy/ford focus")
_ATS = Path("/home/deploy/caddillac")
_VERANO = Path("/home/deploy/buic verano")
_FRONTIER = Path("/home/deploy/toyota frontier")


# vin -> ordered [(source file, alt text)]. Index 0 is the cover photo.
SETS: dict[str, list[tuple[Path, str]]] = {
    # ---- 2016 Ford Focus SE (KAP-00038) -----------------------------
    "1FAFP3F21GL252801": [
        (_FOCUS / "ford focus.jpg",
         "Gray 2016 Ford Focus SE sedan seen from a low front driver-side "
         "angle, showing the black mesh grille and silver alloy wheels."),
        (_FOCUS / "ford focus-3.jpg",
         "Full passenger-side profile of the gray Ford Focus parked under "
         "the Kelley Autoplex canopy."),
        (_FOCUS / "ford focus-5.jpg",
         "Full driver-side profile of the gray Ford Focus, showing its "
         "five-spoke silver alloy wheels."),
        (_FOCUS / "ford focus-4.jpg",
         "Rear view of the gray Ford Focus showing the Focus and Flex Fuel "
         "badges. The rear bumper cover is cracked and separated from the "
         "body on the driver's side, with scuffing across the bumper."),
        (_FOCUS / "ford focus-2.jpg",
         "Driver's cockpit of the Focus, with the instrument cluster behind "
         "a steering wheel wearing an aftermarket cover."),
        (_FOCUS / "ford focus-8.jpg",
         "The Focus's dashboard seen through the open driver's door, with "
         "the centre stack radio, climate dials and automatic shifter."),
        (_FOCUS / "ford focus-6.jpg",
         "The Focus's black cloth front seats and centre console."),
        (_FOCUS / "ford focus-7.jpg",
         "Black cloth rear bench of the Ford Focus seen through the open "
         "rear door."),
    ],
    # ---- 2014 Cadillac ATS (KAP-00039) ------------------------------
    "1G6AA5RX4E0181046": [
        (_ATS / "caddillac.jpg",
         "Dark gray metallic 2014 Cadillac ATS sedan seen from a low front "
         "angle, showing the chrome grille and Cadillac crest badge."),
        (_ATS / "caddillac-4.jpg",
         "Full passenger-side profile of the dark gray Cadillac ATS, "
         "showing its silver multi-spoke alloy wheels."),
        (_ATS / "caddillac-5.jpg",
         "Rear view of the Cadillac ATS, showing the badging and dual "
         "exhaust outlets."),
        (_ATS / "caddillac-2.jpg",
         "Close-up of the Cadillac ATS's chrome grille and the coloured "
         "Cadillac crest badge."),
        (_ATS / "caddillac-3.jpg",
         "Driver's cockpit of the ATS: black leather seats, leather-wrapped "
         "steering wheel and the CUE touchscreen in the centre stack."),
        (_ATS / "caddillac-9.jpg",
         "ATS cabin seen through the open passenger door, showing the CUE "
         "touchscreen, black leather seats and a Bose speaker in the door."),
        (_ATS / "caddillac-6.jpg",
         "Close-up of the ATS steering wheel and its Cadillac crest, with "
         "the centre console shifter behind it."),
        (_ATS / "caddillac-7.jpg",
         "Black leather rear seats of the Cadillac ATS seen through the "
         "open driver-side rear door."),
        (_ATS / "caddillac-8.jpg",
         "The ATS's black leather rear bench seen from the passenger side, "
         "with the rear parcel shelf above."),
    ],
    # ---- 2013 Buick Verano (KAP-00009) — REPLACES scrape photos -----
    "1G4PP5SK3D4182986": [
        (_VERANO / "buic verano.jpg",
         "White 2013 Buick Verano seen head-on from a low front angle, "
         "showing the chrome waterfall grille and Buick badge."),
        (_VERANO / "buic verano-2.jpg",
         "Full passenger-side profile of the white Buick Verano, showing "
         "its chrome window trim and silver multi-spoke alloy wheels."),
        (_VERANO / "buic verano-4.jpg",
         "Full driver-side profile of the white Buick Verano parked under "
         "the Kelley Autoplex canopy."),
        (_VERANO / "buic verano-3.jpg",
         "Rear view of the white Verano, showing the chrome trim strip, "
         "Buick badge and Verano lettering on the trunk."),
        (_VERANO / "buic verano-6.jpg",
         "Driver's cockpit of the Verano: light grey leather seats, "
         "wood-grain console trim and a leather-wrapped steering wheel."),
        (_VERANO / "buic verano-7.jpg",
         "Verano interior seen through the open passenger door, showing "
         "the two-tone dashboard, centre stack and light grey seats."),
        (_VERANO / "buic verano-9.jpg",
         "The Verano's centre stack, with the Buick IntelliLink display "
         "screen above the radio controls."),
        (_VERANO / "buic verano-11.jpg",
         "Close-up of the Verano's dual-zone automatic climate controls, "
         "with heated-seat and heated-steering-wheel buttons above."),
        (_VERANO / "buic verano-10.jpg",
         "Close-up of the Verano's steering wheel, showing the Buick badge "
         "and the cruise control and audio buttons on the spokes."),
        (_VERANO / "buic verano-8.jpg",
         "Light grey leather rear bench of the Buick Verano seen through "
         "the open rear door."),
        (_VERANO / "buic verano-5.jpg",
         "Close-up of a silver Buick alloy wheel and tire on the white "
         "Verano."),
    ],
    # ---- 2014 Nissan Frontier S (KAP-00016) — REPLACES scrape photos -
    "1N6BD0CT0EN744241": [
        (_FRONTIER / "toyota frontier.jpg",
         "Silver 2014 Nissan Frontier extended-cab pickup seen from a low "
         "front angle, showing the chrome grille and Nissan badge."),
        (_FRONTIER / "toyota frontier-3.jpg",
         "Full passenger-side profile of the silver Nissan Frontier, "
         "showing the extended cab, side step bar and steel wheels."),
        (_FRONTIER / "toyota frontier-5.jpg",
         "Full driver-side profile of the silver Frontier parked under the "
         "Kelley Autoplex canopy."),
        (_FRONTIER / "toyota frontier-4.jpg",
         "Rear view of the Frontier with the tailgate closed, showing the "
         "Nissan-stamped bed liner and chrome step bumper."),
        (_FRONTIER / "toyota frontier-2.jpg",
         "Close-up of the Frontier's chrome grille surround and Nissan "
         "badge."),
        (_FRONTIER / "toyota frontier-7.jpg",
         "Driver's cockpit of the Frontier: grey dashboard, CD radio and "
         "the automatic shifter on the centre console."),
        (_FRONTIER / "toyota frontier-8.jpg",
         "The Frontier's dashboard seen from the passenger side, showing "
         "the centre stack radio and climate dials."),
        (_FRONTIER / "toyota frontier-9.jpg",
         "Grey cloth front seats of the Frontier extended cab, with the "
         "jump-seat area behind them."),
        (_FRONTIER / "toyota frontier-6.jpg",
         "Close-up of a silver Frontier steel wheel with an all-terrain "
         "tire; the wheel's paint is chipped around the centre cap."),
    ],
}

# Photo-derived corrections applied alongside the upload.
DETAIL_FIXES: dict[str, dict] = {
    "1FAFP3F21GL252801": dict(
        interior_color="Black Cloth",
        features_json=["Bluetooth", "Cruise Control", "Alloy Wheels",
                       "Cloth Seats", "Automatic Transmission"],
    ),
    "1G6AA5RX4E0181046": dict(
        exterior_color="Gray Metallic",
        interior_color="Black Leather",
        features_json=["Leather Seats", "Bose Audio", "CUE Touchscreen",
                       "Power Front Seats", "Alloy Wheels",
                       "Rear-Wheel Drive"],
    ),
    "1G4PP5SK3D4182986": dict(
        interior_color="Light Gray Leather",
        features_json=["Leather Seats", "Heated Front Seats",
                       "Heated Steering Wheel", "IntelliLink Touchscreen",
                       "Dual-Zone Climate Control", "Alloy Wheels",
                       "Rear Spoiler", "Cruise Control"],
    ),
    "1N6BD0CT0EN744241": dict(
        exterior_color="Silver",
        interior_color="Gray Cloth",
        features_json=["Extended Cab", "Bed Liner", "Side Step Bars",
                       "Chrome Rear Step Bumper", "Cruise Control",
                       "Cloth Seats"],
    ),
}


def _downscale(path: Path) -> bytes:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail(
            (_VEHICLE_PHOTO_MAX_DIMENSION, _VEHICLE_PHOTO_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        out = BytesIO()
        img.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
        return out.getvalue()


def main() -> int:
    commit = "--commit" in sys.argv

    missing = [p for entries in SETS.values() for p, _ in entries if not p.is_file()]
    if missing:
        for p in missing:
            print(f"[error] missing source file: {p}")
        return 1

    db = SessionLocal()
    stale_media_keys: list[str] = []
    try:
        attached = 0
        for vin, entries in SETS.items():
            item = db.execute(
                select(CatalogItem).where(CatalogItem.vin == vin)
            ).scalars().first()
            if item is None:
                print(f"[error] no vehicle row for vin={vin}")
                return 1

            label = f"{item.year} {item.make} {item.model} ({item.public_code})"
            if len(item.image_urls or []) == len(entries) and item.image_alts:
                print(f"[skip] {label} already has {len(entries)} described photo(s)")
                continue

            old_count = len(item.image_urls or [])
            if old_count:
                row = update_catalog_item(
                    db, catalog_item_id=item.id, patch={"image_urls": []}
                )
                keys = getattr(row, "_removed_vehicle_media_keys", []) or []
                stale_media_keys.extend(keys)
                print(f"\n{label} — REPLACING {old_count} old photo(s) "
                      f"({len(keys)} file(s) to delete)")
            else:
                print(f"\n{label} — {len(entries)} photo(s)")

            for index, (path, _alt) in enumerate(entries):
                body = _downscale(path)
                add_vehicle_photo(
                    db,
                    catalog_item_id=item.id,
                    filename=f"{item.public_code}-{index + 1:02d}.jpg",
                    content_type="image/jpeg",
                    body=body,
                )
                slot = "cover" if index == 0 else f"#{index + 1}"
                print(f"  [{slot:>6}] {path.name}  "
                      f"{path.stat().st_size / 1024 / 1024:.1f}MB -> "
                      f"{len(body) / 1024:.0f}KB")
                attached += 1

            db.flush()
            db.refresh(item)
            set_vehicle_photo_alts(
                db,
                catalog_item_id=item.id,
                alts=dict(zip(item.image_urls, [alt for _, alt in entries])),
            )

            fix = DETAIL_FIXES.get(vin)
            if fix:
                row = update_catalog_item(db, catalog_item_id=item.id, patch=dict(fix))
                print(f"  [detail] {row.exterior_color} / {row.interior_color}, "
                      f"{len(row.features_json)} features")

        if commit:
            db.commit()
            if stale_media_keys:
                delete_vehicle_media_keys(stale_media_keys)
                print(f"\nDeleted {len(stale_media_keys)} replaced photo file(s).")
            print(f"Committed. {attached} photo(s) stored and described.")
        else:
            db.rollback()
            print(f"\nDRY RUN — rolled back. Would attach {attached} and delete "
                  f"{len(stale_media_keys)} old file(s). Use --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
