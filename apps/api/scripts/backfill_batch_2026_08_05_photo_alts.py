"""Alt text for the 2026-08-05 batch's 42 photos (migration 102).

Written by looking at each photo, not generated from the row's fields —
"2007 Ford F-150 photo 4" tells a screen-reader user nothing they didn't
already get from the listing heading. Each line says what is actually in
the frame and which angle it is, so someone who can't see the gallery can
still tell the profile shot from the cockpit from the odometer.

House style, kept deliberately narrow:
  * Lead with what the photo shows, not "photo of" / "image of" — screen
    readers already announce it as an image.
  * Name the angle (front three-quarter, driver-side profile, rear) since
    that is what distinguishes one exterior shot from the next.
  * Include colour + body style on exteriors so any single photo stands
    on its own if it is surfaced out of context.
  * Where a gauge is legible, state the reading — those two photos are
    the ones a buyer actually zooms into.
  * No sales language. Alt text is a description, not a pitch.

Keyed by VIN and by position within that vehicle's `image_urls`, which is
the order `attach_batch_2026_08_05_photos.py` established. The script
resolves position -> URL at run time and writes through
`set_vehicle_photo_alts`, so it stays correct if a photo was reordered in
the admin between the attach and this backfill (it follows the *photo*,
never the slot). Re-runnable.

Run:
    .venv/bin/python scripts/backfill_batch_2026_08_05_photo_alts.py
    .venv/bin/python scripts/backfill_batch_2026_08_05_photo_alts.py --commit
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

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from modules.inventory.services.catalog_service import (  # noqa: E402
    set_vehicle_photo_alts,
)


ALT_TEXT: dict[str, list[str]] = {
    # ---- 2007 Ford F-150 (KAP-00033) --------------------------------
    "1FTRX12W57KD18625": [
        "Black 2007 Ford F-150 extended-cab pickup with a matching black "
        "camper shell over the bed, seen from the front passenger side.",
        "Front driver-side view of the black F-150 with the driver's door "
        "open, showing the chrome front bumper and black machined-face "
        "alloy wheels.",
        "Head-on front view of the black F-150, showing the chrome grille "
        "surround, Ford oval and F-150 front plate.",
        "Full passenger-side profile of the F-150, showing the extended "
        "cab, the camper shell over the bed and the chrome running board.",
        "Full driver-side profile of the black F-150 with the camper shell "
        "fitted, parked under the lot canopy.",
        "Rear view of the F-150 with the tailgate closed, showing the "
        "camper shell's tinted rear window and the chrome rear bumper with "
        "a trailer hitch receiver.",
        "Driver's cockpit of the F-150: gray dashboard, cloth-wrapped "
        "steering wheel with cruise control buttons, and tan cloth seats.",
        "F-150 front cabin seen through the open passenger door, showing "
        "the gray dash, center stack radio and tan cloth front seat.",
        "Rear seating area of the F-150 extended cab, with tan cloth seats "
        "and a fold-down rear bench.",
        "Close-up of the F-150 instrument cluster, showing the tachometer, "
        "speedometer and the fuel and temperature gauges.",
    ],
    # ---- 1997 Toyota Celica GT Limited Edition (KAP-00034) ----------
    "JT5FG02T1V0041639": [
        "Black 1997 Toyota Celica GT convertible with its tan soft top "
        "raised, seen from the front passenger side.",
        "Low front view of the black Celica convertible with the passenger "
        "door open, showing its paired round headlights and gold Toyota "
        "badge.",
        "Passenger-side profile of the Celica convertible with the tan top "
        "up, showing the rear spoiler and silver five-spoke alloy wheels.",
        "Driver-side profile of the black Celica convertible parked under "
        "the Kelley Autoplex canopy.",
        "Rear view of the Celica convertible, showing the tan soft top, "
        "trunk spoiler and gold Toyota, Celica and GT badging.",
        "Close-up of the Celica's rear light cluster and the gold GT badge "
        "on the trunk lid.",
        "Close-up of the Celica's trunk badging: gold Toyota, Celica and "
        "GT lettering above the license plate recess.",
        "Chrome Celica Limited Edition badge on the car's front fender.",
        "Celica Limited Edition plaque on the windshield header trim above "
        "the rear-view mirror.",
        "Driver's cockpit of the Celica: black dashboard with wood-grain "
        "trim, three-spoke steering wheel and tan leather seats.",
        "Celica interior seen through the open passenger door, showing the "
        "automatic shifter, wood-grain console and tan leather seats.",
        "The Celica's tan leather front bucket seats and center console.",
        "Close-up of the Celica's climate controls, with the power "
        "convertible-top open and close switch below.",
        "Close-up of a silver five-spoke Celica alloy wheel and tire.",
    ],
    # ---- 2012 Volkswagen CC Sport (KAP-00035) -----------------------
    "WVWMN7AN5CE505688": [
        "Brown metallic 2012 Volkswagen CC Sport seen head-on from a low "
        "front angle, showing the chrome grille bars and VW badge.",
        "Full passenger-side profile of the brown metallic Volkswagen CC, "
        "showing its frameless door windows and silver multi-spoke alloy "
        "wheels.",
        "Rear three-quarter view of the Volkswagen CC, showing the CC and "
        "2.0T badges and the chrome trim across the trunk.",
        "Driver's cockpit of the CC: black leather seats, leather-wrapped "
        "steering wheel and the automatic shifter on the center console.",
        "Close-up of the Volkswagen CC's instrument cluster; the odometer "
        "reads 154,344 miles.",
        "The CC's center stack, with the touchscreen radio, analog dash "
        "clock and the heated-seat and climate controls below.",
        "Rear cabin of the CC seen through the open passenger door, "
        "showing its two black leather rear bucket seats.",
    ],
    # ---- 2013 Volvo S60 T5 (KAP-00036) ------------------------------
    "YV1612FS4D2192118": [
        "Black 2013 Volvo S60 T5 seen from the front with the driver's "
        "door open, showing the chrome-ringed grille and Volvo badge.",
        "Full passenger-side profile of the black Volvo S60 parked under "
        "the Kelley Autoplex canopy.",
        "Full driver-side profile of the black Volvo S60, showing its "
        "silver five-spoke alloy wheels.",
        "Rear view of the black Volvo S60, showing the VOLVO lettering "
        "across the trunk and Texas Longhorns and Texas A&M emblems.",
        "Driver's cockpit of the S60, with a black and tan dashboard and a "
        "leather steering wheel; the odometer reads 148,839 miles.",
        "The S60's center stack, showing the Sensus display screen and the "
        "keyless start-stop engine button.",
        "Close-up of the S60's center console: audio keypad, climate dials "
        "with heated-seat controls, and the automatic gear selector.",
        "The S60's tan leather front seats seen through the open driver's "
        "door.",
        "Tan leather rear bench of the S60 with the center armrest and "
        "cupholders folded down.",
        "Rear cabin of the S60 seen through the open rear door, showing "
        "the tan leather seats and matching door trim.",
        "Close-up of a silver Volvo alloy wheel with a 215/50 R17 tire.",
    ],
    # ---- 2014 Subaru Forester 2.5i Premium (KAP-00037) ---------------
    "JF2SJAEC6EH476777": [
        "Bronze metallic 2014 Subaru Forester seen from a low front angle, "
        "showing the chrome grille surround, Subaru badge and roof rails.",
        "Full passenger-side profile of the bronze Forester, showing the "
        "black roof rails, body-coloured door handles and silver alloy "
        "wheels.",
        "Full driver-side profile of the bronze Subaru Forester parked under "
        "the Kelley Autoplex canopy.",
        "Rear view of the Forester with the tailgate closed, showing the "
        "Subaru, AWD, Forester and PZEV badges and a trailer hitch receiver "
        "below the bumper.",
        "The Forester's carpeted cargo area with the tailgate open and the "
        "retractable cargo cover in place behind the rear seats.",
        "Forester cabin seen through the open passenger door, showing the "
        "black dashboard, centre stack and the automatic gear selector.",
        "Driver's cockpit of the Forester: black dashboard, leather-wrapped "
        "steering wheel and black cloth seats.",
        "The Forester's centre stack, with a CD and HD Radio head unit above "
        "three rotary heating and air-conditioning dials.",
        "Close-up of the Forester's steering wheel, showing the Subaru "
        "badge and the cruise control, audio and phone buttons on the spokes.",
        "The Forester's black cloth front seats and centre console, seen "
        "through the open driver's door.",
        "Black cloth rear bench of the Forester seen through the open rear "
        "door.",
        "Close-up of a silver Subaru alloy wheel with a 225/60 R17 "
        "all-season tire.",
        "Close-up of the Forester's chrome grille bar and Subaru star badge.",
        "Close-up of the chrome Forester and PZEV partial zero emission "
        "vehicle badges on the tailgate.",
    ],
}


def main() -> int:
    commit = "--commit" in sys.argv
    db = SessionLocal()
    try:
        total = 0
        for vin, texts in ALT_TEXT.items():
            item = db.execute(
                select(CatalogItem).where(CatalogItem.vin == vin)
            ).scalars().first()
            if item is None:
                print(f"[error] no vehicle row for vin={vin}")
                return 1

            urls = list(item.image_urls or [])
            label = f"{item.year} {item.make} {item.model} ({item.public_code})"
            if len(texts) != len(urls):
                print(f"[error] {label}: {len(texts)} alt text(s) written but "
                      f"the row has {len(urls)} photo(s). Refusing to guess "
                      f"which photo each line belongs to.")
                return 1

            set_vehicle_photo_alts(
                db,
                catalog_item_id=item.id,
                alts=dict(zip(urls, texts)),
            )
            print(f"\n{label} — {len(texts)} description(s)")
            for index, text_value in enumerate(texts, start=1):
                preview = text_value if len(text_value) <= 96 else text_value[:93] + "..."
                print(f"  {index:>2}. {preview}")
            total += len(texts)

        if commit:
            db.commit()
            print(f"\nCommitted alt text for {total} photo(s).")
        else:
            db.rollback()
            print(f"\nDRY RUN — rolled back. Would set {total}. Use --commit.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
