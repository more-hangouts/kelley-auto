#!/usr/bin/env python
"""Entrypoint for the deal-note follow-up reminder pass.

Run by deploy/systemd/kelley-reminders.timer every 5 minutes:

    /opt/kelley/apps/api/.venv/bin/python scripts/run_note_reminders.py

Safe to run by hand at any time — the pass claims rows with FOR UPDATE
SKIP LOCKED and stamps reminder_sent_at, so a manual run alongside the
timer splits the work instead of double-sending.

Exits non-zero only on an unexpected error (systemd then marks the unit
failed and the next tick still fires). Individual delivery failures are
counted, logged, and retried on the next tick.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python scripts/run_note_reminders.py` from the api root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal  # noqa: E402
from modules.deals.services.note_reminder_runner import (  # noqa: E402
    run_note_reminder_pass,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db = SessionLocal()
    try:
        result = run_note_reminder_pass(db)
    except Exception:
        logging.exception("note reminder pass failed")
        db.rollback()
        return 1
    finally:
        db.close()

    print(
        f"note reminders: scanned={result.scanned} sent={result.sent} "
        f"failed={result.failed} skipped={result.skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
