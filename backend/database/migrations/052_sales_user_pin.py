"""Phase 1 of the Sales Portal: PIN auth columns + role enum tightening.

Adds the columns required for stylist PIN login (`pin_hash`,
`pin_failed_count`, `pin_locked_until`, `last_pin_used_at`,
`force_pin_change`), tightens `users.role` to the closed set
(`admin`, `user`, `sales`) via a CHECK constraint, and adds a
partial index that the upcoming `/api/admin/sales-staff` listing
query will use.

This migration also bumps every existing user's `token_version` so
already-issued admin tokens are invalidated at deploy time. Phase 1
introduces a `scope` claim in JWTs; rather than ship a grace path
that quietly accepts unscoped tokens (extra code we'd remove in
Phase 9), we force re-login on cutover. The Bellas team is small
enough that a single re-login is cheaper than the dead-code burden.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # 1) Add PIN columns to `users`. All nullable / defaulted so the
    #    ALTER does not need to walk the row set.
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD COLUMN pin_hash VARCHAR(255) NULL,
                ADD COLUMN pin_failed_count INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN pin_locked_until TIMESTAMPTZ NULL,
                ADD COLUMN last_pin_used_at TIMESTAMPTZ NULL,
                ADD COLUMN force_pin_change BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )

    # 2) Tighten `users.role` to the closed set. Existing rows are
    #    `admin` or `user`; the new `sales` value joins the allowed list.
    connection.execute(
        text(
            """
            ALTER TABLE users
                ADD CONSTRAINT chk_users_role
                CHECK (role IN ('admin', 'user', 'sales'))
            """
        )
    )

    # 3) Partial index for the sales-staff listing screen.
    connection.execute(
        text(
            "CREATE INDEX idx_users_role_sales "
            "ON users(id) WHERE role = 'sales'"
        )
    )

    # 4) Bump `token_version` on every existing user. This invalidates
    #    every previously-issued JWT — the next request returns 401 and
    #    the user re-logs in. Phase 1's `create_access_token` mints
    #    tokens with a `scope` claim from this point forward.
    connection.execute(
        text("UPDATE users SET token_version = token_version + 1")
    )

    # ----- DML probe per the project rule -----
    # Insert a synthetic sales user row, set every new column, verify
    # round-trip; then verify the CHECK rejects an unsupported role and
    # an empty PIN column combination behaves as expected. Everything
    # runs inside a savepoint and rolls back so production data is
    # untouched.
    sp = connection.begin_nested()
    try:
        probe_username = "__052_probe_sales_user__"
        probe_email = "__052_probe_sales_user__@example.invalid"

        connection.execute(
            text(
                """
                INSERT INTO users
                    (username, email, hashed_password, full_name,
                     is_active, role, permissions,
                     pin_hash, pin_failed_count, force_pin_change)
                VALUES
                    (:username, :email, '__not_a_real_hash__', 'Probe Sales',
                     TRUE, 'sales', '[]'::jsonb,
                     '__not_a_real_pin_hash__', 0, TRUE)
                """
            ),
            {"username": probe_username, "email": probe_email},
        )

        row = connection.execute(
            text(
                "SELECT role, pin_hash, pin_failed_count, "
                "pin_locked_until, last_pin_used_at, force_pin_change "
                "FROM users WHERE username = :u"
            ),
            {"u": probe_username},
        ).first()
        assert row is not None, "probe sales user not inserted"
        assert row[0] == "sales", row
        assert row[1] == "__not_a_real_pin_hash__", row
        assert row[2] == 0, row
        assert row[3] is None, row
        assert row[4] is None, row
        assert row[5] is True, row
    finally:
        sp.rollback()

    # CHECK rejects unsupported role values.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO users
                        (username, email, hashed_password, role)
                    VALUES
                        ('__052_probe_bad_role__',
                         '__052_probe_bad_role__@example.invalid',
                         '__hash__', 'owner')
                    """
                )
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "chk_users_role did not reject role='owner'"
            )
    finally:
        sp.rollback()

    # CHECK rejects another bogus value just to be sure the constraint
    # isn't matching loosely.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO users
                        (username, email, hashed_password, role)
                    VALUES
                        ('__052_probe_bad_role_2__',
                         '__052_probe_bad_role_2__@example.invalid',
                         '__hash__', 'staff')
                    """
                )
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "chk_users_role did not reject role='staff'"
            )
    finally:
        sp.rollback()
