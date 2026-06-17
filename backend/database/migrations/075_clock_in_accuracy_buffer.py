"""Clock-in reliability — accuracy-aware geofence + acceptance audit.

Slice A of the geofence reliability work. Backend rule layer only —
trusted-network detection lands in slice C (its own migration), the
frontend best-of-N capture and admin UI live in their own slices.

What lands here:

  - `staff_punches.accepted_by`: stable vocabulary recording **why** the
    punch was accepted, so the owner can tell a strict GPS pass from a
    "we widened the gate because the phone reported ±40m" pass without
    re-running the math. Default `'gps'` so every historical row keeps
    a meaningful value.
  - `staff_punches.accepted_buffer_m`: when the accuracy buffer was used,
    how much slack was applied (NULL otherwise). Recorded as the cap we
    used — `min(client_accuracy_m, accuracy_buffer_max_m)` — not the
    overshoot, so the same gate decision is reproducible from the row.
  - `business_profile.gps_accuracy_buffer_max_m`: owner-configurable cap
    on how much accuracy slack the gate is willing to grant. Default 50
    matches the user's spec ("for example `min(client_accuracy_m, 50)`").
    CHECK 0-200 keeps fat-fingers from creating a 1000m gate.

The vocabulary on `accepted_by` already reserves the `'trusted_network'`
literal so slice C can ship without a second ALTER on the column.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_punches: how was this row accepted? ----
    connection.execute(
        text(
            "ALTER TABLE staff_punches "
            "ADD COLUMN accepted_by VARCHAR(32) NOT NULL DEFAULT 'gps'"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE staff_punches "
            "ADD CONSTRAINT chk_staff_punches_accepted_by "
            "CHECK (accepted_by IN "
            "('gps', 'gps_with_accuracy_buffer', 'trusted_network'))"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE staff_punches "
            "ADD COLUMN accepted_buffer_m NUMERIC(10, 2) NULL"
        )
    )

    # ---- business_profile: owner-tunable cap on accuracy slack ----
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD COLUMN gps_accuracy_buffer_max_m INTEGER NOT NULL DEFAULT 50"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD CONSTRAINT chk_bp_gps_accuracy_buffer_max_m "
            "CHECK (gps_accuracy_buffer_max_m BETWEEN 0 AND 200)"
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        # Default lands as 50 on the existing singleton.
        got = connection.execute(
            text(
                "SELECT gps_accuracy_buffer_max_m "
                "FROM business_profile WHERE id = 1"
            )
        ).scalar()
        assert got == 50, f"expected default 50, got {got!r}"

        # 75 round-trips.
        connection.execute(
            text(
                "UPDATE business_profile "
                "SET gps_accuracy_buffer_max_m = 75 WHERE id = 1"
            )
        )
        assert (
            connection.execute(
                text(
                    "SELECT gps_accuracy_buffer_max_m "
                    "FROM business_profile WHERE id = 1"
                )
            ).scalar()
            == 75
        )

        # CHECK rejects -1.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE business_profile "
                        "SET gps_accuracy_buffer_max_m = -1 WHERE id = 1"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_bp_gps_accuracy_buffer_max_m accepted -1"
                )
        finally:
            sp_inner.rollback()

        # CHECK rejects 201.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE business_profile "
                        "SET gps_accuracy_buffer_max_m = 201 WHERE id = 1"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_bp_gps_accuracy_buffer_max_m accepted 201"
                )
        finally:
            sp_inner.rollback()

        # staff_punches: probe row with each accepted_by value.
        user_row = connection.execute(
            text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).first()
        if user_row is not None:
            user_id = int(user_row[0])

            for accepted in ("gps", "gps_with_accuracy_buffer", "trusted_network"):
                pid = connection.execute(
                    text(
                        """
                        INSERT INTO staff_punches
                            (user_id, direction, status,
                             accepted_by, accepted_buffer_m)
                        VALUES
                            (:uid, 'in', 'unscheduled', :acc, :buf)
                        RETURNING id
                        """
                    ),
                    {
                        "uid": user_id,
                        "acc": accepted,
                        "buf": (
                            None if accepted == "gps" else 40.0
                        ),
                    },
                ).scalar()
                row = connection.execute(
                    text(
                        "SELECT accepted_by, accepted_buffer_m "
                        "FROM staff_punches WHERE id = :pid"
                    ),
                    {"pid": pid},
                ).first()
                assert row is not None
                assert row[0] == accepted

            # CHECK rejects an unknown accepted_by.
            sp_inner = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO staff_punches "
                            "(user_id, direction, accepted_by) "
                            "VALUES (:uid, 'in', 'magic')"
                        ),
                        {"uid": user_id},
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "chk_staff_punches_accepted_by accepted 'magic'"
                    )
            finally:
                sp_inner.rollback()
    finally:
        sp.rollback()
