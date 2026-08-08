"""Attach Luis's photo sets for the 2026-08-05 batch (F-150, Celica, CC).

Three folders of camera-original JPEGs (~6900x4600, 12-19 MB each) landed in
/home/deploy. Two things have to happen before they can go in:

1. **Size.** ``add_vehicle_photo`` rejects anything over VEHICLE_PHOTO_MAX_MB
   (10 MB), so the originals bounce as ``vehicle_photo_too_large``. We
   pre-scale each file to _VEHICLE_PHOTO_MAX_DIMENSION (2400 px long edge) —
   the exact size the service would have stored it at anyway — which lands
   every file around 0.5-1 MB. Nothing is lost that the pipeline wasn't
   already going to discard, and prod's size cap stays untouched.

2. **Order.** ``image_urls`` is an ordered list and index 0 is the thumbnail
   the storefront card and the CRM board use. The camera's filename order is
   not a listing order (the F-150 set opens on a low-angle shot with the
   driver door hanging open), so each set is explicitly sequenced below:
   hero 3/4 -> remaining exteriors -> cabin -> detail shots.

Idempotent: a vehicle that already has photos is skipped unless --force.
Dry-run by default.

Run:
    .venv/bin/python scripts/attach_batch_2026_08_05_photos.py
    .venv/bin/python scripts/attach_batch_2026_08_05_photos.py --commit
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

# Two of the Forester files arrived with their last bytes missing (an
# interrupted transfer): Pillow raises on them by default. Allow the partial
# decode and trim the damaged strip in _trim_decode_damage below, rather than
# dropping two otherwise-good photos.
ImageFile.LOAD_TRUNCATED_IMAGES = True
from sqlalchemy import select  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from modules.inventory.services.catalog_service import (  # noqa: E402
    _VEHICLE_PHOTO_MAX_DIMENSION,
    add_vehicle_photo,
)

# Note the trailing space in the F-150 directory name — it is real.
_F150_DIR = Path("/home/deploy/F150_camper ")
_CELICA_DIR = Path("/home/deploy/celica_aug")
_CC_DIR = Path("/home/deploy/vw cc pics")
_VOLVO_DIR = Path("/home/deploy/volvo 60")
_SUBARU_DIR = Path("/home/deploy/subaru forester ")

# vin -> ordered list of source files (index 0 becomes the thumbnail).
PHOTO_SETS: dict[str, list[Path]] = {
    # 2007 Ford F-150 — black SuperCab with a matching ATC camper shell.
    "1FTRX12W57KD18625": [
        _F150_DIR / "F150_camper -3.jpg",    # passenger front 3/4, shell in frame
        _F150_DIR / "F150_camper .jpg",      # driver front 3/4, door open
        _F150_DIR / "F150_camper -2.jpg",    # head-on front
        _F150_DIR / "F150_camper -4.jpg",    # passenger-side profile
        _F150_DIR / "F150_camper -6.jpg",    # driver-side profile
        _F150_DIR / "F150_camper -5.jpg",    # rear / tailgate
        _F150_DIR / "F150_camper -7.jpg",    # driver cockpit
        _F150_DIR / "F150_camper -8.jpg",    # cabin from passenger side
        _F150_DIR / "F150_camper -9.jpg",    # rear SuperCab seats
        _F150_DIR / "F150_camper -10.jpg",   # gauge cluster
    ],
    # 1997 Toyota Celica GT Limited Edition convertible — black over tan.
    "JT5FG02T1V0041639": [
        _CELICA_DIR / "celica_aug-3.jpg",    # passenger front 3/4, top up
        _CELICA_DIR / "celica_aug.jpg",      # front low angle, door open
        _CELICA_DIR / "celica_aug-4.jpg",    # passenger-side profile
        _CELICA_DIR / "celica_aug-8.jpg",    # driver-side profile
        _CELICA_DIR / "celica_aug-7.jpg",    # rear
        _CELICA_DIR / "celica_aug-6.jpg",    # taillight + GT badge
        _CELICA_DIR / "celica_aug-14.jpg",   # trunk badging
        _CELICA_DIR / "celica_aug-13.jpg",   # Limited Edition fender badge
        _CELICA_DIR / "celica_aug-12.jpg",   # Limited Edition header badge
        _CELICA_DIR / "celica_aug-2.jpg",    # driver cockpit
        _CELICA_DIR / "celica_aug-9.jpg",    # cabin from passenger side
        _CELICA_DIR / "celica_aug-10.jpg",   # front leather seats
        _CELICA_DIR / "celica_aug-11.jpg",   # climate + power-top switch
        _CELICA_DIR / "celica_aug-5.jpg",    # wheel / tire
    ],
    # 2012 Volkswagen CC Sport 2.0T — brown metallic over black leather.
    "WVWMN7AN5CE505688": [
        _CC_DIR / "Front.jpg",
        _CC_DIR / "passenger side.jpg",
        _CC_DIR / "back.jpg",
        _CC_DIR / "driver view.jpg",
        _CC_DIR / "dash.jpg",
        _CC_DIR / "infotainment.jpg",
        _CC_DIR / "passenger back seat.jpg",
    ],
    # 2013 Volvo S60 T5 — black over tan leather.
    "YV1612FS4D2192118": [
        _VOLVO_DIR / "volvo 60.jpg",         # front 3/4, driver door open
        _VOLVO_DIR / "volvo 60-2.jpg",       # passenger-side profile
        _VOLVO_DIR / "volvo 60-4.jpg",       # driver-side profile
        _VOLVO_DIR / "volvo 60-3.jpg",       # rear
        _VOLVO_DIR / "volvo 60-5.jpg",       # driver cockpit / dash
        _VOLVO_DIR / "volvo 60-6.jpg",       # centre stack, start/stop
        _VOLVO_DIR / "volvo 60-7.jpg",       # centre console controls
        _VOLVO_DIR / "volvo 60-10.jpg",      # front seats
        _VOLVO_DIR / "volvo 60-9.jpg",       # rear seats
        _VOLVO_DIR / "volvo 60-11.jpg",      # rear cabin, driver side
        _VOLVO_DIR / "volvo 60-8.jpg",       # wheel / tire
    ],
    # 2014 Subaru Forester 2.5i Premium — bronze metallic over black cloth.
    # Note the trailing space in the directory name, as with the F-150.
    "JF2SJAEC6EH476777": [
        _SUBARU_DIR / "subaru forester .jpg",    # front 3/4
        _SUBARU_DIR / "subaru forester -7.jpg",  # passenger-side profile
        _SUBARU_DIR / "subaru forester -9.jpg",  # driver-side profile
        _SUBARU_DIR / "subaru forester -8.jpg",  # rear
        _SUBARU_DIR / "subaru forester -10.jpg", # cargo area, hatch open
        _SUBARU_DIR / "subaru forester -11.jpg", # cabin from passenger door
        _SUBARU_DIR / "subaru forester -5.jpg",  # driver cockpit (truncated)
        _SUBARU_DIR / "subaru forester -3.jpg",  # centre stack
        _SUBARU_DIR / "subaru forester -4.jpg",  # steering wheel (truncated)
        _SUBARU_DIR / "subaru forester -12.jpg", # front seats
        _SUBARU_DIR / "subaru forester -13.jpg", # rear seats
        _SUBARU_DIR / "subaru forester -6.jpg",  # wheel / tire
        _SUBARU_DIR / "subaru forester -2.jpg",  # grille close-up
        _SUBARU_DIR / "subaru forester -14.jpg", # Forester / PZEV badging
    ],
}


def _trim_decode_damage(img: Image.Image) -> Image.Image:
    """Crop the flat grey band a truncated JPEG decodes to at its bottom.

    A JPEG that lost its tail decodes fine down to the last complete MCU row
    and then fills the remainder with uniform mid-grey. That band is not
    subject matter — it is corruption — and on a listing it reads as a
    photographer's mistake. Scan up from the bottom while rows stay flat and
    grey, then cut. Bounded at 15% so an genuinely grey-bottomed photo (an
    overcast sky, a concrete floor) can never be silently gutted; anything
    worse than that should be re-sent, not salvaged.
    """
    rgb = img.convert("RGB")
    width, height = rgb.size
    limit = int(height * 0.15)
    xs = range(0, width, max(1, width // 40))

    damaged = 0
    for y in range(height - 1, height - 1 - limit, -1):
        row = [rgb.getpixel((x, y)) for x in xs]
        # Flat = no channel spread (grey), and mid-tone rather than a real
        # blown-out or black edge.
        neutral = max(abs(p[0] - p[1]) + abs(p[1] - p[2]) for p in row)
        mean = sum(sum(p) for p in row) / (len(row) * 3)
        if neutral < 12 and 100 < mean < 160:
            damaged += 1
        else:
            break

    if damaged == 0:
        return img
    return img.crop((0, 0, width, height - damaged))


def _downscale(path: Path) -> bytes:
    """Return JPEG bytes scaled to the pipeline's own max dimension.

    Also applies the EXIF orientation up front so what we hand over is what
    gets stored (the service does the same, but doing it here means the
    dimension check below is honest).
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img = _trim_decode_damage(img)
        img.thumbnail(
            (_VEHICLE_PHOTO_MAX_DIMENSION, _VEHICLE_PHOTO_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        out = BytesIO()
        img.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
        return out.getvalue()


def main() -> int:
    commit = "--commit" in sys.argv
    force = "--force" in sys.argv

    missing = [p for paths in PHOTO_SETS.values() for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"[error] missing source file: {p}")
        return 1

    db = SessionLocal()
    try:
        attached = 0
        for vin, paths in PHOTO_SETS.items():
            item = db.execute(
                select(CatalogItem).where(CatalogItem.vin == vin)
            ).scalars().first()
            if item is None:
                print(f"[error] no vehicle row for vin={vin}")
                return 1

            label = f"{item.year} {item.make} {item.model} ({item.public_code})"
            if item.image_urls and not force:
                print(f"[skip] {label} already has {len(item.image_urls)} photo(s)")
                continue

            print(f"\n{label} — {len(paths)} photo(s)")
            for index, path in enumerate(paths):
                body = _downscale(path)
                original_mb = path.stat().st_size / 1024 / 1024
                add_vehicle_photo(
                    db,
                    catalog_item_id=item.id,
                    filename=f"{item.public_code}-{index + 1:02d}.jpg",
                    content_type="image/jpeg",
                    body=body,
                )
                slot = "thumbnail" if index == 0 else f"#{index + 1}"
                print(f"  [{slot:>9}] {path.name}  "
                      f"{original_mb:.1f}MB -> {len(body) / 1024:.0f}KB")
                attached += 1

        if commit:
            db.commit()
            print(f"\nCommitted. {attached} photo(s) stored.")
        else:
            db.rollback()
            print(f"\nDRY RUN — rolled back (files written to storage are "
                  f"orphaned, harmless). Would attach {attached}. Use --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
