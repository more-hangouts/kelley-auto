"""Test helpers for the Phase 7 Slice 2 attendance gate.

Most sales-mutation smokes were written before the punched-out gate
existed; they call mutation endpoints with a sales token without ever
running a punch-in. To keep those smokes focused on their own
behavior, each one snapshots `business_profile.attendance_gate_enabled`,
disables it, runs, and restores. New smokes that test the gate itself
(see `test_clock_selfie_and_gate_smoke.py`) do not use these helpers
— they need the gate live.
"""

from __future__ import annotations

from sqlalchemy import text as sql_text

from database.connection import SessionLocal


def snapshot_and_disable_gate() -> dict:
    """Capture the current gate setting, then disable it.

    Returns a snapshot dict the caller passes to `restore_gate` in
    its `finally` block.

    Safe to call from any test setup. Raises AssertionError if the
    business_profile singleton row doesn't exist; the suite already
    relies on it for other smokes.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT id, attendance_gate_enabled, selfie_policy "
                "FROM business_profile ORDER BY id LIMIT 1"
            )
        ).first()
        if row is None:
            raise AssertionError(
                "test prerequisite: business_profile row must exist"
            )
        snapshot = {
            "id": int(row[0]),
            "attendance_gate_enabled": bool(row[1]),
            "selfie_policy": row[2],
        }
        db.execute(
            sql_text(
                "UPDATE business_profile "
                "SET attendance_gate_enabled = FALSE WHERE id = :id"
            ),
            {"id": snapshot["id"]},
        )
        db.commit()
        return snapshot
    finally:
        db.close()


def restore_gate(snapshot: dict | None) -> None:
    """Restore prior gate state. No-op when snapshot is None (the
    snapshot call failed before capturing anything)."""
    if snapshot is None:
        return
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE business_profile "
                "SET attendance_gate_enabled = :enabled, "
                "    selfie_policy = :policy "
                "WHERE id = :id"
            ),
            {
                "id": snapshot["id"],
                "enabled": snapshot["attendance_gate_enabled"],
                "policy": snapshot["selfie_policy"],
            },
        )
        db.commit()
    finally:
        db.close()
