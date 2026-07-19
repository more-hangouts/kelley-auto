"""Attach the 301 ORIGINAL downloaded vehicle photos to the 26 catalog rows.

Source: var/crm_import_package/inventory_photo_manifest.csv plus the actual
JPG files under var/crm_import_package/inventory_photos/<VIN>/NN.jpg.

These are the real downloaded image bytes (not carsforsale hot-links). Each
file is stored on the CRM via ``catalog_service.add_vehicle_photo`` — the
same path the admin "upload photo" button uses — which writes the bytes
under DOCUMENT_STORAGE_ROOT and appends a self-hosted ``/api/public/media/…``
URL to the row's ``image_urls`` (first = thumbnail).

Matching: by VIN. The one masked-VIN Altima (folder 1N4AL3APJCXXXXXX) has no
VIN in the catalog, so it falls back to its synthetic stock number.

Idempotent: a vehicle that already has >= its manifest photo count is
skipped, so re-runs don't duplicate. Use --force to append anyway.

Dry-run by default. Pass --commit to actually store files + update rows.

Run:
    .venv/bin/python scripts/import_inventory_photos.py            # dry-run
    .venv/bin/python scripts/import_inventory_photos.py --commit   # for real
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")
os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from sqlalchemy import select  # noqa: E402

from database.connection import SessionLocal  # noqa: E402
from database.models import CatalogItem  # noqa: E402
from services.catalog_service import CatalogServiceError, add_vehicle_photo  # noqa: E402

_PKG = _REPO_ROOT / "var" / "crm_import_package"
_MANIFEST = _PKG / "inventory_photo_manifest.csv"

# The masked-VIN Altima was imported VIN-less under this synthetic stock by
# scripts/import_active_inventory.py — map its photo folder to that row.
_MASKED_VIN = "1N4AL3APJCXXXXXX"
_MASKED_STOCK = "KEL-2018ALTSR-NOVIN"


def _find_vehicle(db, vin: str) -> CatalogItem | None:
    item = db.execute(
        select(CatalogItem).where(CatalogItem.vin == vin)
    ).scalars().first()
    if item is None and vin == _MASKED_VIN:
        item = db.execute(
            select(CatalogItem).where(CatalogItem.stock_number == _MASKED_STOCK)
        ).scalars().first()
    return item


def main() -> int:
    commit = "--commit" in sys.argv[1:]
    force = "--force" in sys.argv[1:]
    if not _MANIFEST.exists():
        print(f"ERROR: missing {_MANIFEST}", file=sys.stderr)
        return 2

    # Group photo files by VIN, ordered by photo_number.
    by_vin: dict[str, list[tuple[int, str]]] = defaultdict(list)
    label: dict[str, str] = {}
    with _MANIFEST.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            vin = (row.get("vin") or "").strip()
            n = int(row.get("photo_number") or 0)
            by_vin[vin].append((n, row["local_photo_file"]))
            label[vin] = f"{row.get('year')} {row.get('make')} {row.get('model')}"
    for vin in by_vin:
        by_vin[vin].sort(key=lambda t: t[0])

    print(f"Manifest: {sum(len(v) for v in by_vin.values())} photos across "
          f"{len(by_vin)} vehicles")
    print(f"Mode: {'COMMIT' if commit else 'DRY-RUN (no writes)'}"
          f"{' +force' if force else ''}\n")

    db = SessionLocal()
    stored = skipped_vehicles = missing_vehicles = missing_files = 0
    try:
        for vin, photos in sorted(by_vin.items()):
            item = _find_vehicle(db, vin)
            name = label.get(vin, vin)
            if item is None:
                missing_vehicles += 1
                print(f"  [no-match] {name} vin={vin} — no catalog row")
                continue

            have = len(item.image_urls or [])
            if have >= len(photos) and not force:
                skipped_vehicles += 1
                print(f"  [skip] {name} id={item.id} already has {have} photos")
                continue

            files = []
            for _n, rel in photos:
                p = _PKG / rel
                if not p.exists() or p.stat().st_size == 0:
                    missing_files += 1
                    print(f"    ! missing/empty file: {rel}")
                    continue
                files.append(p)

            if not commit:
                print(f"  [would-add] {name} id={item.id}: +{len(files)} photos "
                      f"(currently {have})")
                stored += len(files)
                continue

            added = 0
            for p in files:
                try:
                    add_vehicle_photo(
                        db,
                        catalog_item_id=item.id,
                        filename=p.name,
                        content_type="image/jpeg",
                        body=p.read_bytes(),
                    )
                    added += 1
                except CatalogServiceError as exc:
                    print(f"    ! {p.name} rejected: {exc.code}")
            db.commit()
            stored += added
            print(f"  [added] {name} id={item.id}: +{added} photos "
                  f"(now {len((db.get(CatalogItem, item.id).image_urls) or [])})")

        print(
            f"\nDone. {'would store' if not commit else 'stored'} photos={stored} "
            f"| vehicles-skipped={skipped_vehicles} "
            f"| no-catalog-match={missing_vehicles} "
            f"| missing-files={missing_files}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
