"""Import the 250 MyAccountCenter CRM customers into ``contacts``.

Source: var/crm_import_package/customers_import_ready.csv (the vetted,
import-ready file from the scrape package — 250 rows, 0 needing review).

Idempotent: goes through ``contact_service.create_admin_contact``, which
de-dupes on phone_e164 then email and is transactional, so re-running this
never double-inserts. ``was_new=False`` rows are left untouched.

The CRM extras with no native column (lead_status, salesperson, message /
interested-vehicle counts, and the MyAccountCenter external_id) are folded
into ``notes`` for provenance and a couple of ``tags`` for filtering — but
only on freshly-created rows, so a re-run never clobbers staff edits.

Dry-run by default (no DB writes). Pass --commit to actually insert.

Run:
    .venv/bin/python scripts/import_crm_customers.py            # dry-run
    .venv/bin/python scripts/import_crm_customers.py --commit   # for real
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")
os.environ.setdefault("APP_TIMEZONE", "America/Chicago")

from database.connection import SessionLocal  # noqa: E402
from services import booking_service, contact_service  # noqa: E402

_CSV = _REPO_ROOT / "var" / "crm_import_package" / "customers_import_ready.csv"
_IMPORT_TAG = "mac-import"


def _notes(row: dict) -> str:
    lines = ["Imported from MyAccountCenter CRM (2026-06-30)."]
    if row.get("lead_status"):
        lines.append(f"Lead status: {row['lead_status']}")
    if row.get("salesperson"):
        lines.append(f"Salesperson: {row['salesperson']}")
    msgs = row.get("message_count") or "0"
    veh = row.get("interested_vehicle_count") or "0"
    lines.append(f"Messages: {msgs} · Interested vehicles: {veh}")
    if row.get("source"):
        lines.append(f"Source: {row['source']}")
    if row.get("external_id"):
        lines.append(f"Ext ID: {row['external_id']}")
    return "\n".join(lines)


def _tags(row: dict) -> list[str]:
    tags = [_IMPORT_TAG]
    if row.get("lead_status"):
        tags.append(row["lead_status"])
    return tags


def main() -> int:
    commit = "--commit" in sys.argv[1:]
    if not _CSV.exists():
        print(f"ERROR: missing {_CSV}", file=sys.stderr)
        return 2

    with _CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"Loaded {len(rows)} customer rows from {_CSV.name}")
    print(f"Mode: {'COMMIT' if commit else 'DRY-RUN (no writes)'}\n")

    db = SessionLocal()
    created = existing = no_contactable = 0
    try:
        for i, row in enumerate(rows, 1):
            phone = (row.get("phone") or "").strip()
            email = (row.get("email") or "").strip() or None
            phone_e164 = booking_service.normalize_phone_e164(phone)
            name = (row.get("full_name") or "").strip() or _compose(row)
            if not phone_e164 and not email:
                # Package guarantees 0 of these, but guard anyway.
                no_contactable += 1
                print(f"  [skip:{i}] {name!r} has no phone or email")
                continue

            if not commit:
                pre = contact_service._lookup_contact(
                    db, phone_e164=phone_e164, email=(email.lower() if email else None)
                )
                tag = "exists" if pre else "new"
                if pre:
                    existing += 1
                else:
                    created += 1
                if i <= 5 or tag == "exists":
                    print(f"  [{tag}:{i}] {name} | {phone_e164 or '(no phone)'} | {email or '(no email)'}")
                continue

            contact, was_new = contact_service.create_admin_contact(
                db,
                first_name=(row.get("first_name") or None),
                last_name=(row.get("last_name") or None),
                display_name=name,
                email=email,
                phone=phone or None,
                notes=_notes(row),
            )
            if was_new:
                contact.tags = _tags(row)
                db.commit()
                created += 1
            else:
                existing += 1
                print(f"  [dedup:{i}] {name} -> existing contact id={contact.id}")

        print(
            f"\nDone. {'would create' if not commit else 'created'}={created} "
            f"| {'already-present' if not commit else 'deduped-existing'}={existing} "
            f"| uncontactable-skipped={no_contactable}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _compose(row: dict) -> str:
    parts = [(row.get("first_name") or "").strip(), (row.get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or "(unnamed)"


if __name__ == "__main__":
    raise SystemExit(main())
