"""Clock-in reliability — trusted-network fallback (log-only first).

Slice C of the geofence reliability work. Schema lands now; the
acceptance bypass stays gated behind `trusted_network_enabled` (default
FALSE) so the owner can verify the shop's public IP is stable for a
few days before flipping the toggle. Detection runs regardless of the
toggle so the audit row tells you "this punch came from the boutique
Wi-Fi" even on a GPS-accepted clock-in.

What lands here:

  - `business_profile.trusted_network_enabled`: master switch. FALSE
    means detection-only; TRUE means a request from a listed IP can
    accept a clock-in that would otherwise have failed the geofence.
  - `business_profile.trusted_clock_in_ips`: JSONB array of strings.
    Each string is either a single IP (`203.0.113.5`) or a CIDR
    (`203.0.113.0/24`). The router resolves the request's real client
    IP via X-Forwarded-For (we sit behind nginx) and tests membership
    in `ipaddress` so v4/v6 + CIDR all work uniformly.
  - `staff_punches.trusted_network_detected`: per-row evidence flag.
    Stamped TRUE whenever the request IP matches the trusted list,
    independent of `accepted_by`. So `accepted_by='gps'` + `detected
    =TRUE` is the audit shape during the log-only window.

`accepted_by='trusted_network'` was reserved by migration 075, so this
slice does not need to touch the CHECK constraint.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD COLUMN trusted_network_enabled BOOLEAN NOT NULL "
            "DEFAULT FALSE"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD COLUMN trusted_clock_in_ips JSONB NOT NULL "
            "DEFAULT '[]'::jsonb"
        )
    )
    # JSONB array shape: ["203.0.113.5", "198.51.100.0/24"]. We do not
    # CHECK the contents at the DB level — `ipaddress.ip_network` in the
    # service layer is the source of truth, and a malformed entry just
    # never matches anything. The shape check guards against the obvious
    # "someone PATCHed an object instead of an array" mistake.
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD CONSTRAINT chk_bp_trusted_ips_is_array "
            "CHECK (jsonb_typeof(trusted_clock_in_ips) = 'array')"
        )
    )

    connection.execute(
        text(
            "ALTER TABLE staff_punches "
            "ADD COLUMN trusted_network_detected BOOLEAN NOT NULL "
            "DEFAULT FALSE"
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        # Defaults land cleanly.
        row = connection.execute(
            text(
                "SELECT trusted_network_enabled, trusted_clock_in_ips "
                "FROM business_profile WHERE id = 1"
            )
        ).first()
        assert row is not None
        assert row[0] is False, f"expected default FALSE, got {row[0]!r}"
        assert row[1] == [], f"expected default [], got {row[1]!r}"

        # Set a realistic value, round-trip.
        connection.execute(
            text(
                "UPDATE business_profile "
                "SET trusted_network_enabled = TRUE, "
                "    trusted_clock_in_ips = '[\"203.0.113.5\", "
                "                            \"198.51.100.0/24\"]'::jsonb "
                "WHERE id = 1"
            )
        )
        row = connection.execute(
            text(
                "SELECT trusted_network_enabled, trusted_clock_in_ips "
                "FROM business_profile WHERE id = 1"
            )
        ).first()
        assert row[0] is True
        assert "203.0.113.5" in row[1]

        # Non-array shape is rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE business_profile "
                        "SET trusted_clock_in_ips = '{}'::jsonb "
                        "WHERE id = 1"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_bp_trusted_ips_is_array accepted an object"
                )
        finally:
            sp_inner.rollback()

        # staff_punches: detected flag round-trips.
        user_row = connection.execute(
            text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).first()
        if user_row is not None:
            uid = int(user_row[0])
            pid = connection.execute(
                text(
                    "INSERT INTO staff_punches "
                    "(user_id, direction, status, "
                    " accepted_by, trusted_network_detected) "
                    "VALUES (:uid, 'in', 'unscheduled', 'gps', TRUE) "
                    "RETURNING id"
                ),
                {"uid": uid},
            ).scalar()
            got = connection.execute(
                text(
                    "SELECT trusted_network_detected "
                    "FROM staff_punches WHERE id = :pid"
                ),
                {"pid": pid},
            ).scalar()
            assert got is True

            # The default lands as FALSE.
            pid2 = connection.execute(
                text(
                    "INSERT INTO staff_punches "
                    "(user_id, direction, status) "
                    "VALUES (:uid, 'in', 'unscheduled') "
                    "RETURNING id"
                ),
                {"uid": uid},
            ).scalar()
            got = connection.execute(
                text(
                    "SELECT trusted_network_detected "
                    "FROM staff_punches WHERE id = :pid"
                ),
                {"pid": pid2},
            ).scalar()
            assert got is False
    finally:
        sp.rollback()
