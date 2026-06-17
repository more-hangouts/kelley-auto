"""Backfill pipeline events for appointments that pre-date auto-promotion.

Now that POST /api/booking/appointments auto-promotes every booking onto the
pipeline board, historical appointments that were never manually promoted are
invisible to staff. Run this once to seed events for them.

Status mapping:
    pending, confirmed   -> Event.status = lead
    attended (no sale)   -> Event.status = consulted
    attended (sale)      -> Event.status = sold
    cancelled            -> skip (lead never materialized for staff)
    no_show, rescheduled -> skip (rescheduled is a tombstone, no_show needs
                            judgment we can't infer from data alone)

Skips appointments without a contact_id — those are legacy rows from before
contact linkage rolled out and need manual reconciliation.

Usage:
    python scripts/backfill_appointment_events.py --dry-run
    python scripts/backfill_appointment_events.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal
from database.models import Appointment
from services import event_service
from services.event_service import EventServiceError


_PROMOTABLE_STATUSES = {"pending", "confirmed", "attended"}
_SKIP_STATUSES = {"cancelled", "no_show", "rescheduled", "abandoned"}


def _target_status(appt: Appointment) -> str | None:
    if appt.status in ("pending", "confirmed"):
        return "lead"
    if appt.status == "attended":
        if appt.purchase_value_cents is not None and appt.purchase_value_cents > 0:
            return "sold"
        return "consulted"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    counts: Counter[str] = Counter()
    try:
        candidates = (
            db.query(Appointment)
            .filter(Appointment.crm_event_id.is_(None))
            .order_by(Appointment.created_at.asc())
            .all()
        )
        print(f"Found {len(candidates)} appointment(s) without crm_event_id.")

        for appt in candidates:
            if appt.status in _SKIP_STATUSES:
                counts[f"skip_{appt.status}"] += 1
                continue
            if appt.status not in _PROMOTABLE_STATUSES:
                counts[f"skip_unknown_status_{appt.status}"] += 1
                continue
            if appt.contact_id is None:
                counts["skip_no_contact"] += 1
                print(
                    f"  appt {appt.id} ({appt.confirmation_code}): no contact_id, skip"
                )
                continue

            target = _target_status(appt)
            if target is None:
                counts["skip_no_target"] += 1
                continue

            print(
                f"  appt {appt.id} ({appt.confirmation_code}, status={appt.status})"
                f" -> event status={target}"
            )
            if args.dry_run:
                counts[f"would_promote_to_{target}"] += 1
                continue

            try:
                event = event_service.promote_appointment_to_event(
                    db, appointment_id=appt.id, event_type="quinceanera"
                )
                if target != "lead":
                    event_service.change_event_status(
                        db,
                        event_id=event.id,
                        new_status=target,
                        notes="Backfilled from historical appointment.",
                    )
                db.commit()
                counts[f"promoted_to_{target}"] += 1
            except EventServiceError as exc:
                db.rollback()
                counts[f"error_{exc.code}"] += 1
                print(f"    ERROR: {exc.code}")
            except Exception as exc:
                db.rollback()
                counts["error_unhandled"] += 1
                print(f"    ERROR: {exc}")

        print("\nSummary:")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
        if args.dry_run:
            print("\n(dry-run — no writes performed)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
