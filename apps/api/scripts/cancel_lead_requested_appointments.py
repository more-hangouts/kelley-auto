"""One-time cleanup: retire the phantom appointments the storefront used to create.

Until 2026-08-16 the public lead form offered an hour-slot picker, and
`public_lead_service._create_requested_appointment` turned whatever hour the
customer clicked into a PENDING row in `appointments`. Nobody ever worked
them: at cleanup time production held 115 such rows, 113 already in the past,
and not a single one had ever been confirmed, attended, or cancelled. They
were a customer's wish, not a commitment anyone had made — and they made the
appointments calendar useless as a working surface.

The picker is gone (see the note at the top of public_lead_service.py). This
script clears the backlog it left behind.

TARGETED SET — deliberately narrow, all four conditions required:

    status  = 'pending'                     never confirmed or attended
    source  = 'public_booking'              migration 104's origin column
    raw_payload->>'source' = 'public_lead'  the storefront lead path, NOT the
                                            embeddable booking widget
    slot_start_at < now()                   the slot has already passed

Rows are CANCELLED, never deleted: the appointment keeps its confirmation
code, its link to the deal (`crm_event_id`) and its contact, so the history of
what the customer asked for stays readable on the deal timeline. Nothing about
the deal, the contact, or the lead itself is touched.

FUTURE-DATED rows are left alone on purpose. A slot that has not happened yet
may be a real person expecting a callback; the script prints them so a human
can decide, rather than quietly cancelling someone's expectation.

Idempotent — a second run finds nothing left to cancel.

Usage:
    .venv/bin/python scripts/cancel_lead_requested_appointments.py           # dry run
    .venv/bin/python scripts/cancel_lead_requested_appointments.py --apply
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from config.settings import APP_TIMEZONE  # noqa: E402
from database.connection import SessionLocal  # noqa: E402

_TZ = ZoneInfo(APP_TIMEZONE)


def _local(dt: datetime) -> str:
    """Render a stored timestamptz in DEALERSHIP-LOCAL time, zone labelled.

    Postgres hands these back in UTC. Printing them raw is how "10:00 AM CDT"
    reaches a human as "15:00" — which reads as 3pm to anyone standing in the
    dealership. Every appointment time this script prints is a time a person
    may act on, so it is always converted and always labelled.
    """
    return f"{dt.astimezone(_TZ):%Y-%m-%d %I:%M %p %Z}"

CANCELLATION_REASON = (
    "Auto-cleanup 2026-08-16: storefront requested-slot appointment, "
    "never confirmed. Web self-scheduling has been removed."
)

# The four-part predicate above, shared by the preview and the update so they
# can never drift apart.
_TARGET_WHERE = """
    status = 'pending'
    AND source = 'public_booking'
    AND raw_payload->>'source' = 'public_lead'
    AND slot_start_at < now()
"""

_FUTURE_WHERE = """
    status = 'pending'
    AND source = 'public_booking'
    AND raw_payload->>'source' = 'public_lead'
    AND slot_start_at >= now()
"""


def main() -> int:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        targets = db.execute(
            text(
                f"""
                SELECT id, slot_start_at, celebrant_first_name,
                       celebrant_last_name, crm_event_id
                  FROM appointments
                 WHERE {_TARGET_WHERE}
                 ORDER BY slot_start_at
                """
            )
        ).all()

        future = db.execute(
            text(
                f"""
                SELECT id, slot_start_at, celebrant_first_name,
                       celebrant_last_name, phone, email, crm_event_id
                  FROM appointments
                 WHERE {_FUTURE_WHERE}
                 ORDER BY slot_start_at
                """
            )
        ).all()

        print(
            f"{'APPLY' if apply else 'DRY RUN'} — "
            f"{_local(datetime.now(timezone.utc))}"
        )
        print(f"\npast pending storefront-requested slots to cancel: {len(targets)}")
        for r in targets[:10]:
            name = " ".join(x for x in (r[2], r[3]) if x)
            print(f"  #{r[0]:<6} {_local(r[1]):<24}  {name:<28} deal={r[4]}")
        if len(targets) > 10:
            print(f"  … and {len(targets) - 10} more")

        # Loud, because these are the ones a human still has to deal with.
        print(f"\nFUTURE slots LEFT ALONE (someone may be expecting a call): {len(future)}")
        for r in future:
            name = " ".join(x for x in (r[2], r[3]) if x)
            print(
                f"  #{r[0]:<6} {_local(r[1]):<24}  {name:<28} "
                f"{r[4] or '—':<14} {r[5] or '—':<32} deal={r[6]}"
            )

        if not apply:
            print("\nDry run — nothing written. Re-run with --apply to cancel.")
            return 0

        if not targets:
            print("\nNothing to do.")
            return 0

        result = db.execute(
            text(
                f"""
                UPDATE appointments
                   SET status = 'cancelled',
                       cancelled_at = now(),
                       cancellation_reason = :reason,
                       updated_at = now()
                 WHERE {_TARGET_WHERE}
                """
            ),
            {"reason": CANCELLATION_REASON},
        )
        db.commit()
        print(f"\ncancelled {result.rowcount} appointment(s).")

        remaining = db.execute(
            text(f"SELECT count(*) FROM appointments WHERE {_TARGET_WHERE}")
        ).scalar()
        print(f"remaining in target set (expect 0): {remaining}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
