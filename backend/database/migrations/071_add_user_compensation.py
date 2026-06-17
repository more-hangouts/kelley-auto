"""Add `hourly_wage` and `commission_rate` to the `users` table.

Groundwork for the labor-analytics work Bella's wants on top of the
clock-in / schedule data. Both columns are nullable so the migration
is safe on the existing roster — the manager fills them in from the
admin Staff Profiles drawer as they hire / re-rate stylists.

Representation:

  * `hourly_wage` — NUMERIC(10, 2). Dollar amount. CHECK >= 0.
  * `commission_rate` — NUMERIC(5, 4), stored as a DECIMAL FRACTION
    (0.0750 == 7.5%). The UI presents/edits as a percent; the wire
    format and storage are always fractional so analytics don't have
    to guess which representation a row uses. CHECK BETWEEN 0 AND 1.

Both columns are admin-only on the wire — they're never serialized by
sales/portal/public endpoints. See `services/auto_scheduler.py` and
`api/routers/admin_sales_staff.py` for the exposed surface.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD COLUMN hourly_wage      NUMERIC(10, 2),
                ADD COLUMN commission_rate  NUMERIC(5, 4)
            """
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD CONSTRAINT users_hourly_wage_check
                CHECK (hourly_wage IS NULL OR hourly_wage >= 0)
            """
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD CONSTRAINT users_commission_rate_check
                CHECK (commission_rate IS NULL
                       OR (commission_rate >= 0
                           AND commission_rate <= 1))
            """
        )
    )

    # ===== DML probes =====
    # The new columns default to NULL on existing rows; confirm by
    # picking any existing user and round-tripping a couple of values.
    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    if user_row is None:
        return
    user_id = int(user_row[0])

    sp = connection.begin_nested()
    try:
        # NULLs accepted (default state on existing rows).
        connection.execute(
            text(
                "UPDATE users "
                "SET hourly_wage = NULL, commission_rate = NULL "
                "WHERE id = :id"
            ),
            {"id": user_id},
        )

        # Valid values round-trip.
        connection.execute(
            text(
                "UPDATE users "
                "SET hourly_wage = 18.50, commission_rate = 0.0750 "
                "WHERE id = :id"
            ),
            {"id": user_id},
        )
        row = connection.execute(
            text(
                "SELECT hourly_wage, commission_rate "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        ).first()
        assert row is not None
        assert float(row[0]) == 18.50, row
        assert float(row[1]) == 0.0750, row

        # Boundary values: 0 and 1 for commission_rate.
        connection.execute(
            text(
                "UPDATE users SET hourly_wage = 0, commission_rate = 0 "
                "WHERE id = :id"
            ),
            {"id": user_id},
        )
        connection.execute(
            text(
                "UPDATE users SET commission_rate = 1 WHERE id = :id"
            ),
            {"id": user_id},
        )

        # Negative wage rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE users SET hourly_wage = -1 "
                        "WHERE id = :id"
                    ),
                    {"id": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "users_hourly_wage_check accepted negative wage"
                )
        finally:
            sp_inner.rollback()

        # Commission > 1 rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE users SET commission_rate = 1.5 "
                        "WHERE id = :id"
                    ),
                    {"id": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "users_commission_rate_check accepted commission > 1"
                )
        finally:
            sp_inner.rollback()

        # Commission < 0 rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE users SET commission_rate = -0.01 "
                        "WHERE id = :id"
                    ),
                    {"id": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "users_commission_rate_check accepted commission < 0"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
