"""Phase 10 Slice 4 (stability): add `missing_out_punch` to
`staff_schedule_entries.attendance_status`.

The new state covers the "36-hour shift" bug: a stylist clocked in,
forgot to clock out, and auto-close either didn't fire or didn't
link a paired out-punch into the schedule entry. A daily cron
(`services.missing_out_punch_cron`) flips eligible entries from
`scheduled` semantics through to this terminal-but-recoverable state
so the manager can resolve it from Attendance Review.

The existing CHECK constraint is dropped and re-created with the new
vocabulary. DML probes round-trip the new value and confirm that
old values still pass.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # The new value 'missing_out_punch' is 17 chars; the existing
    # VARCHAR(16) won't hold it. Widen first, then swap the CHECK.
    connection.execute(
        text(
            "ALTER TABLE staff_schedule_entries "
            "ALTER COLUMN attendance_status TYPE VARCHAR(24)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE staff_schedule_entries "
            "DROP CONSTRAINT staff_schedule_entries_attendance_status_check"
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE staff_schedule_entries
                ADD CONSTRAINT staff_schedule_entries_attendance_status_check
                CHECK (attendance_status IN (
                    'scheduled',
                    'present',
                    'late',
                    'no_show',
                    'excused',
                    'missing_out_punch'
                ))
            """
        )
    )

    # ===== DML probes =====
    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    if user_row is None:
        return
    user_id = int(user_row[0])

    sp = connection.begin_nested()
    try:
        # New value accepted.
        entry_id = connection.execute(
            text(
                """
                INSERT INTO staff_schedule_entries
                    (user_id, business_date,
                     starts_at_local, ends_at_local,
                     status, attendance_status,
                     published_at, published_by_user_id)
                VALUES
                    (:uid, '2026-06-15',
                     '2026-06-15 09:00:00-05'::TIMESTAMPTZ,
                     '2026-06-15 17:00:00-05'::TIMESTAMPTZ,
                     'published', 'missing_out_punch',
                     NOW(), :uid)
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        assert entry_id is not None

        # Pre-existing values still pass.
        for value in (
            "scheduled",
            "present",
            "late",
            "no_show",
            "excused",
        ):
            sp_inner = connection.begin_nested()
            try:
                connection.execute(
                    text(
                        "UPDATE staff_schedule_entries "
                        "SET attendance_status = :v WHERE id = :id"
                    ),
                    {"v": value, "id": entry_id},
                )
            finally:
                sp_inner.rollback()

        # Bogus value still rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE staff_schedule_entries "
                        "SET attendance_status = 'forgot_again' "
                        "WHERE id = :id"
                    ),
                    {"id": entry_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "attendance_status CHECK accepted bogus value "
                    "after migration 070"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
