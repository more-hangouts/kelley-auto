"""Phase 7 Slice 1 of the Sales Portal: clock-in schema foundation.

Lays down the four attendance tables but ships zero disk-write code.
Selfie upload, owner attendance review UI, and the "punched-out" gate
on existing sales endpoints arrive in Slice 2 — that's the slice the
VPS `ReadWritePaths` check gates. This migration is pure DDL + DML
probes, so it ships without an ops change.

Tables:

  - `staff_locations`: per-boutique geofence center + radius.
  - `staff_punches`: one row per clock-in or clock-out event.
  - `staff_punch_audit_events`: append-only before/after audit rows
    for any system or human change to a punch.
  - `staff_punch_correction_requests`: stylist-submitted "I forgot to
    clock out, I actually left at X" requests for owner review.

`shift_id` and `holiday_id` on `staff_punches` are deliberately
plain nullable INTEGER/BIGINT columns without a foreign key — the
referenced tables (`staff_shifts`, `staff_holidays`) land in Phase
8's migration, which adds the FKs there. This keeps Phase 7 Slice 1
mergeable independently of Phase 8 sequencing.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_locations ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_locations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                latitude NUMERIC(10, 7) NOT NULL,
                longitude NUMERIC(10, 7) NOT NULL,
                radius_m INTEGER NOT NULL
                    CHECK (radius_m BETWEEN 25 AND 1000),
                grace_minutes INTEGER NOT NULL DEFAULT 0
                    CHECK (grace_minutes BETWEEN 0 AND 120),
                default_auto_session_close_time TIME NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_staff_locations_active "
            "ON staff_locations(active) WHERE active IS TRUE"
        )
    )

    # ---- staff_punches ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_punches (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE RESTRICT,
                direction VARCHAR(8) NOT NULL
                    CHECK (direction IN ('in', 'out')),
                punched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                status VARCHAR(20) NOT NULL DEFAULT 'recorded'
                    CHECK (status IN (
                        'recorded', 'late', 'early_out',
                        'unscheduled', 'manual_adjusted', 'void'
                    )),
                location_id INTEGER NULL
                    REFERENCES staff_locations(id) ON DELETE SET NULL,
                shift_id BIGINT NULL,
                holiday_id INTEGER NULL,
                client_latitude NUMERIC(10, 7) NULL,
                client_longitude NUMERIC(10, 7) NULL,
                client_accuracy_m NUMERIC(10, 2) NULL,
                distance_to_location_m NUMERIC(10, 2) NULL,
                selfie_storage_key VARCHAR(255) NULL,
                auto_closed BOOLEAN NOT NULL DEFAULT FALSE,
                auto_close_reason VARCHAR(24) NULL
                    CHECK (auto_close_reason IN (
                        'past_date', 'max_time_reached', 'max_session_hours'
                    )),
                auto_closed_at TIMESTAMPTZ NULL,
                hours_confirmation_status VARCHAR(20) NOT NULL
                    DEFAULT 'not_required'
                    CHECK (hours_confirmation_status IN (
                        'not_required', 'needs_review', 'confirmed', 'adjusted'
                    )),
                hours_confirmed_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                hours_confirmed_at TIMESTAMPTZ NULL,
                user_agent VARCHAR(255) NULL,
                ip INET NULL,
                notes TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_staff_punches_user_day "
            "ON staff_punches(user_id, punched_at)"
        )
    )
    # Owner attendance review queue: any non-finalized state. Indexed
    # together because the queue OR-filters across these states.
    connection.execute(
        text(
            """
            CREATE INDEX idx_staff_punches_review_queue
              ON staff_punches(punched_at DESC)
              WHERE auto_closed IS TRUE
                 OR status IN ('late', 'early_out', 'unscheduled',
                               'manual_adjusted', 'void')
                 OR hours_confirmation_status IN (
                       'needs_review', 'adjusted'
                    )
            """
        )
    )

    # ---- staff_punch_audit_events ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_punch_audit_events (
                id BIGSERIAL PRIMARY KEY,
                punch_id BIGINT NULL
                    REFERENCES staff_punches(id) ON DELETE SET NULL,
                actor_kind VARCHAR(20) NOT NULL
                    CHECK (actor_kind IN ('system', 'staff', 'owner')),
                actor_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(40) NOT NULL,
                reason_code VARCHAR(60) NULL,
                old_values JSONB NOT NULL DEFAULT '{}'::jsonb,
                new_values JSONB NOT NULL DEFAULT '{}'::jsonb,
                notes TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_staff_punch_audit_punch "
            "ON staff_punch_audit_events(punch_id, created_at DESC)"
        )
    )

    # ---- staff_punch_correction_requests ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_punch_correction_requests (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                punch_id BIGINT NULL
                    REFERENCES staff_punches(id) ON DELETE SET NULL,
                requested_check_in_at TIMESTAMPTZ NULL,
                requested_check_out_at TIMESTAMPTZ NULL,
                reason TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'approved', 'denied', 'cancelled'
                    )),
                decided_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                decided_at TIMESTAMPTZ NULL,
                decision_notes TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_staff_punch_corrections_status "
            "ON staff_punch_correction_requests(status, created_at DESC)"
        )
    )

    # ---- DML probes per the project rule ----

    # Insert a probe location and round-trip it.
    sp = connection.begin_nested()
    try:
        loc_id = connection.execute(
            text(
                """
                INSERT INTO staff_locations
                    (name, latitude, longitude, radius_m)
                VALUES
                    ('__056_probe_loc__', 29.4252, -98.4946, 100)
                RETURNING id
                """
            )
        ).scalar()
        row = connection.execute(
            text(
                "SELECT name, radius_m, active FROM staff_locations "
                "WHERE id = :id"
            ),
            {"id": loc_id},
        ).first()
        assert row is not None, "probe location did not insert"
        assert row[0] == "__056_probe_loc__"
        assert row[1] == 100
        assert row[2] is True

        # radius_m too small → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_locations "
                        "(name, latitude, longitude, radius_m) "
                        "VALUES ('__056_probe_loc_small__', 0, 0, 10)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_locations CHECK accepted radius_m=10 (below 25)"
                )
        finally:
            sp_inner.rollback()

        # radius_m too big → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_locations "
                        "(name, latitude, longitude, radius_m) "
                        "VALUES ('__056_probe_loc_big__', 0, 0, 5000)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_locations CHECK accepted radius_m=5000 (above 1000)"
                )
        finally:
            sp_inner.rollback()

        # Pick a real user to attach a probe punch to.
        user_row = connection.execute(
            text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).first()
        if user_row is not None:
            user_id = int(user_row[0])

            # Happy path: insert in + out punches.
            in_id = connection.execute(
                text(
                    """
                    INSERT INTO staff_punches
                        (user_id, direction, location_id,
                         client_latitude, client_longitude,
                         distance_to_location_m, status)
                    VALUES
                        (:uid, 'in', :loc, 29.4252, -98.4946, 12.5,
                         'unscheduled')
                    RETURNING id
                    """
                ),
                {"uid": user_id, "loc": loc_id},
            ).scalar()
            out_id = connection.execute(
                text(
                    """
                    INSERT INTO staff_punches
                        (user_id, direction, location_id, status)
                    VALUES (:uid, 'out', :loc, 'unscheduled')
                    RETURNING id
                    """
                ),
                {"uid": user_id, "loc": loc_id},
            ).scalar()
            assert in_id is not None and out_id is not None

            # Bad direction → CHECK rejects.
            sp_inner = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO staff_punches "
                            "(user_id, direction) "
                            "VALUES (:uid, 'around')"
                        ),
                        {"uid": user_id},
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "staff_punches CHECK accepted direction='around'"
                    )
            finally:
                sp_inner.rollback()

            # Bad status → CHECK rejects.
            sp_inner = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO staff_punches "
                            "(user_id, direction, status) "
                            "VALUES (:uid, 'in', 'bogus')"
                        ),
                        {"uid": user_id},
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "staff_punches CHECK accepted status='bogus'"
                    )
            finally:
                sp_inner.rollback()

            # Audit row + correction request both round-trip.
            connection.execute(
                text(
                    """
                    INSERT INTO staff_punch_audit_events
                        (punch_id, actor_kind, actor_user_id, action,
                         reason_code, old_values, new_values)
                    VALUES (:pid, 'staff', :uid, 'punch.created',
                            '__probe__',
                            '{}'::jsonb,
                            '{"direction": "in"}'::jsonb)
                    """
                ),
                {"pid": in_id, "uid": user_id},
            )

            connection.execute(
                text(
                    """
                    INSERT INTO staff_punch_correction_requests
                        (user_id, punch_id, requested_check_out_at, reason)
                    VALUES (:uid, :pid, NOW(), '__probe correction reason__')
                    """
                ),
                {"uid": user_id, "pid": in_id},
            )

            # Bad correction status → CHECK rejects.
            sp_inner = connection.begin_nested()
            try:
                try:
                    connection.execute(
                        text(
                            "INSERT INTO staff_punch_correction_requests "
                            "(user_id, reason, status) "
                            "VALUES (:uid, '__bad_status__', 'maybe')"
                        ),
                        {"uid": user_id},
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(
                        "staff_punch_correction_requests CHECK accepted "
                        "status='maybe'"
                    )
            finally:
                sp_inner.rollback()
    finally:
        sp.rollback()
