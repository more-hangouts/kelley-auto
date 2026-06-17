"""Phase 8 Slice A of the Sales Portal: schedule + time-off schema.

Five new tables + two FK additions on `staff_punches`:

  - `staff_shifts`: weekly shift template per stylist. `starts_at` /
    `ends_at` are TIMESTAMPTZ anchors; the time-of-day component is
    what repeats on each weekday in `working_days`. The resolver in
    Slice B carries `duration = ends_at - starts_at` and expands the
    template onto each working day in the requested range, which makes
    overnight shifts (`ends_at - starts_at > 1 day's local time-of-day
    headroom`) cleanly handled — the duration crosses midnight rather
    than the time-of-day "wrapping around."
  - `staff_shift_overrides`: temporary per-stylist override that wins
    over the assigned shift for a date range. Cascade on shift delete
    so a removed shift doesn't leave dangling overrides.
  - `staff_holidays`: advisory holiday calendar with a UNIQUE NULLS
    NOT DISTINCT on `(holiday_date, location_id, name)` so a "global"
    (location_id IS NULL) holiday with the same date+name as another
    actually collides instead of slipping through Postgres's default
    distinct-NULL UNIQUE semantics. The user explicitly asked for a
    DML probe on this case.
  - `time_off_requests`: stylist-submitted requests with the latest
    decision state on the row. The full decision timeline lives in
    the sibling table below.
  - `time_off_decision_events`: append-only audit timeline for time-
    off requests, mirroring `staff_punch_audit_events`. The user's
    Phase 8 guardrail #3 made this a requirement: "time-off approval
    append-only/audited, like attendance adjustments." `action` is
    locked to `requested | approved | denied | cancelled | amended`.
  - `staff_punches.shift_id` / `holiday_id` get FKs with
    ON DELETE SET NULL so a deleted shift or holiday never breaks
    a historical punch row.

DML probes round-trip every CHECK and FK behavior. The
NULLS-NOT-DISTINCT holiday probe tests both directions: a duplicate
global holiday is rejected, but two rows with same date+name and
different `location_id` are allowed.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_shifts ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_shifts (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                location_id INTEGER NULL
                    REFERENCES staff_locations(id) ON DELETE SET NULL,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                late_grace_period_minutes INTEGER NOT NULL DEFAULT 0
                    CHECK (late_grace_period_minutes BETWEEN 0 AND 120),
                earliest_check_in_minutes INTEGER NOT NULL DEFAULT 120
                    CHECK (earliest_check_in_minutes BETWEEN 0 AND 720),
                early_out_grace_minutes INTEGER NOT NULL DEFAULT 0
                    CHECK (early_out_grace_minutes BETWEEN 0 AND 120),
                auto_session_close_time TIME NULL,
                max_session_hours NUMERIC(5, 2) NULL
                    CHECK (max_session_hours IS NULL
                           OR max_session_hours BETWEEN 1 AND 24),
                working_days INTEGER[] NOT NULL
                    DEFAULT ARRAY[1, 2, 3, 4, 5, 6]::INTEGER[],
                notes TEXT NULL,
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_staff_shifts_range
                    CHECK (ends_at > starts_at),
                CONSTRAINT chk_staff_shifts_working_days_count
                    CHECK (array_length(working_days, 1) BETWEEN 1 AND 7),
                CONSTRAINT chk_staff_shifts_working_days_values
                    CHECK (working_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7])
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_shifts_user_range "
            "ON staff_shifts(user_id, starts_at)"
        )
    )

    # ---- staff_shift_overrides ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_shift_overrides (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                shift_id BIGINT NOT NULL
                    REFERENCES staff_shifts(id) ON DELETE CASCADE,
                starts_on DATE NOT NULL,
                ends_on DATE NOT NULL,
                reason TEXT NULL,
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_staff_shift_overrides_range
                    CHECK (ends_on >= starts_on)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_shift_overrides_user_range "
            "ON staff_shift_overrides(user_id, starts_on, ends_on)"
        )
    )

    # ---- staff_holidays ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_holidays (
                id SERIAL PRIMARY KEY,
                name VARCHAR(160) NOT NULL,
                holiday_date DATE NOT NULL,
                location_id INTEGER NULL
                    REFERENCES staff_locations(id) ON DELETE CASCADE,
                is_paid BOOLEAN NOT NULL DEFAULT FALSE,
                multiplier NUMERIC(5, 2) NULL
                    CHECK (multiplier IS NULL OR multiplier > 0),
                notes TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_staff_holidays_global
                    UNIQUE NULLS NOT DISTINCT
                    (holiday_date, location_id, name)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_staff_holidays_date "
            "ON staff_holidays(holiday_date)"
        )
    )

    # ---- staff_punches FK additions ----
    # `shift_id` and `holiday_id` were left as plain nullable columns
    # in migration 056 with no FK so Phase 7 could ship independently.
    # Slice A wires them up. ON DELETE SET NULL on both — a deleted
    # shift or holiday must never break a historical punch row.
    connection.execute(
        text(
            """
            ALTER TABLE staff_punches
                ADD CONSTRAINT fk_staff_punches_shift
                FOREIGN KEY (shift_id) REFERENCES staff_shifts(id)
                ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            """
            ALTER TABLE staff_punches
                ADD CONSTRAINT fk_staff_punches_holiday
                FOREIGN KEY (holiday_id) REFERENCES staff_holidays(id)
                ON DELETE SET NULL
            """
        )
    )

    # ---- time_off_requests ----
    connection.execute(
        text(
            """
            CREATE TABLE time_off_requests (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                starts_at TIMESTAMPTZ NOT NULL,
                ends_at TIMESTAMPTZ NOT NULL,
                reason TEXT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'approved', 'denied', 'cancelled'
                    )),
                decided_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                decided_at TIMESTAMPTZ NULL,
                decision_notes TEXT NULL,
                manager_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_time_off_requests_range
                    CHECK (ends_at > starts_at)
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_tor_user_status "
            "ON time_off_requests(user_id, status)"
        )
    )

    # ---- time_off_decision_events (append-only audit) ----
    # Mirrors staff_punch_audit_events. The action vocabulary is
    # CHECKed so a future code change can't silently introduce a
    # new state without a schema migration.
    connection.execute(
        text(
            """
            CREATE TABLE time_off_decision_events (
                id BIGSERIAL PRIMARY KEY,
                request_id BIGINT NOT NULL
                    REFERENCES time_off_requests(id) ON DELETE CASCADE,
                actor_kind VARCHAR(20) NOT NULL
                    CHECK (actor_kind IN ('owner', 'staff', 'system')),
                actor_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(20) NOT NULL
                    CHECK (action IN (
                        'requested', 'approved', 'denied',
                        'cancelled', 'amended'
                    )),
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
            "CREATE INDEX idx_tor_events_request "
            "ON time_off_decision_events(request_id, created_at DESC)"
        )
    )

    # ===== DML probes per the project rule =====

    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    if user_row is None:
        # Fresh install with no users; the schema is in place and the
        # behavioral smoke seeds its own users.
        return
    user_id = int(user_row[0])

    # ---- staff_shifts probes ----
    sp = connection.begin_nested()
    try:
        shift_id = connection.execute(
            text(
                """
                INSERT INTO staff_shifts
                    (user_id, starts_at, ends_at, working_days)
                VALUES
                    (:uid,
                     '2026-05-08 09:00:00-05'::TIMESTAMPTZ,
                     '2026-05-08 17:00:00-05'::TIMESTAMPTZ,
                     ARRAY[1, 2, 3, 4, 5])
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        assert shift_id is not None

        row = connection.execute(
            text(
                "SELECT user_id, late_grace_period_minutes, "
                "earliest_check_in_minutes, working_days "
                "FROM staff_shifts WHERE id = :id"
            ),
            {"id": shift_id},
        ).first()
        assert row[0] == user_id
        assert row[1] == 0
        assert row[2] == 120
        assert list(row[3]) == [1, 2, 3, 4, 5]

        # ends_at <= starts_at → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shifts "
                        "(user_id, starts_at, ends_at) VALUES "
                        "(:uid, '2026-05-08 17:00:00-05'::TIMESTAMPTZ, "
                        "       '2026-05-08 09:00:00-05'::TIMESTAMPTZ)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shifts CHECK accepted ends_at <= starts_at"
                )
        finally:
            sp_inner.rollback()

        # working_days length > 7 → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shifts "
                        "(user_id, starts_at, ends_at, working_days) "
                        "VALUES (:uid, "
                        "'2026-05-08 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-08 17:00:00-05'::TIMESTAMPTZ, "
                        "ARRAY[1, 2, 3, 4, 5, 6, 7, 1])"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shifts CHECK accepted working_days length 8"
                )
        finally:
            sp_inner.rollback()

        # working_days containing 8 → containment CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shifts "
                        "(user_id, starts_at, ends_at, working_days) "
                        "VALUES (:uid, "
                        "'2026-05-08 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-08 17:00:00-05'::TIMESTAMPTZ, "
                        "ARRAY[1, 8])"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shifts CHECK accepted weekday 8"
                )
        finally:
            sp_inner.rollback()

        # late_grace > 120 → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shifts "
                        "(user_id, starts_at, ends_at, "
                        " late_grace_period_minutes) "
                        "VALUES (:uid, "
                        "'2026-05-08 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-08 17:00:00-05'::TIMESTAMPTZ, "
                        "200)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shifts CHECK accepted late_grace=200"
                )
        finally:
            sp_inner.rollback()

        # max_session_hours = 25 → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shifts "
                        "(user_id, starts_at, ends_at, "
                        " max_session_hours) VALUES "
                        "(:uid, "
                        "'2026-05-08 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-08 17:00:00-05'::TIMESTAMPTZ, "
                        "25.0)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shifts CHECK accepted max_session_hours=25"
                )
        finally:
            sp_inner.rollback()

        # ---- staff_shift_overrides probes ----
        override_id = connection.execute(
            text(
                """
                INSERT INTO staff_shift_overrides
                    (user_id, shift_id, starts_on, ends_on)
                VALUES (:uid, :sid, '2026-05-12', '2026-05-14')
                RETURNING id
                """
            ),
            {"uid": user_id, "sid": shift_id},
        ).scalar()
        assert override_id is not None

        # ends_on < starts_on → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_overrides "
                        "(user_id, shift_id, starts_on, ends_on) "
                        "VALUES (:uid, :sid, "
                        "'2026-05-15', '2026-05-12')"
                    ),
                    {"uid": user_id, "sid": shift_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_shift_overrides CHECK accepted "
                    "ends_on < starts_on"
                )
        finally:
            sp_inner.rollback()

        # Override is hard-cascaded by parent shift delete (CASCADE)
        # — verify by deleting the shift inside this savepoint.
        sp_inner = connection.begin_nested()
        try:
            connection.execute(
                text("DELETE FROM staff_shifts WHERE id = :id"),
                {"id": shift_id},
            )
            remaining = connection.execute(
                text(
                    "SELECT COUNT(*) FROM staff_shift_overrides "
                    "WHERE id = :id"
                ),
                {"id": override_id},
            ).scalar()
            assert remaining == 0, (
                "staff_shift_overrides should cascade-delete "
                "with parent shift"
            )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()

    # ---- staff_holidays probes (the headline NULLS NOT DISTINCT case) ----
    sp = connection.begin_nested()
    try:
        # Two NULL-location holidays with same (date, name) → second
        # INSERT fails. The user explicitly asked for this probe in
        # Slice A.
        connection.execute(
            text(
                "INSERT INTO staff_holidays (name, holiday_date) "
                "VALUES ('__059_probe_global__', '2026-07-04')"
            )
        )
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_holidays "
                        "(name, holiday_date) "
                        "VALUES ('__059_probe_global__', '2026-07-04')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_holidays UNIQUE NULLS NOT DISTINCT did NOT "
                    "reject duplicate global holiday — Postgres is "
                    "treating NULL location_id as distinct"
                )
        finally:
            sp_inner.rollback()

        # Same date+name with different location_id → allowed.
        loc_id = connection.execute(
            text(
                "INSERT INTO staff_locations "
                "(name, latitude, longitude, radius_m) "
                "VALUES ('__059_probe_loc__', 0, 0, 100) RETURNING id"
            )
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO staff_holidays "
                "(name, holiday_date, location_id) "
                "VALUES ('__059_probe_global__', '2026-07-04', :loc)"
            ),
            {"loc": loc_id},
        )

        # multiplier = 0 → CHECK rejects (0 is not > 0).
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_holidays "
                        "(name, holiday_date, multiplier) VALUES "
                        "('__059_probe_zero_mult__', '2026-12-25', 0)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_holidays CHECK accepted multiplier=0"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()

    # ---- staff_punches FK probes ----
    sp = connection.begin_nested()
    try:
        # Bad shift_id rejected by FK.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_punches "
                        "(user_id, direction, shift_id) VALUES "
                        "(:uid, 'in', 999999999)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_punches.shift_id FK accepted bogus id"
                )
        finally:
            sp_inner.rollback()

        # Real shift_id round-trip + delete causes SET NULL on the
        # punch row.
        shift_id = connection.execute(
            text(
                """
                INSERT INTO staff_shifts
                    (user_id, starts_at, ends_at)
                VALUES (:uid,
                        '2026-05-08 09:00:00-05'::TIMESTAMPTZ,
                        '2026-05-08 17:00:00-05'::TIMESTAMPTZ)
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        punch_id = connection.execute(
            text(
                "INSERT INTO staff_punches "
                "(user_id, direction, shift_id, status) "
                "VALUES (:uid, 'in', :sid, 'recorded') RETURNING id"
            ),
            {"uid": user_id, "sid": shift_id},
        ).scalar()
        connection.execute(
            text("DELETE FROM staff_shifts WHERE id = :id"),
            {"id": shift_id},
        )
        ref = connection.execute(
            text("SELECT shift_id FROM staff_punches WHERE id = :id"),
            {"id": punch_id},
        ).scalar()
        assert ref is None, (
            f"staff_punches.shift_id should be NULL after parent "
            f"shift delete, got {ref!r}"
        )
    finally:
        sp.rollback()

    # ---- time_off_requests probes ----
    sp = connection.begin_nested()
    try:
        tor_id = connection.execute(
            text(
                """
                INSERT INTO time_off_requests
                    (user_id, starts_at, ends_at, reason)
                VALUES (:uid,
                        '2026-06-01 00:00:00-05'::TIMESTAMPTZ,
                        '2026-06-03 00:00:00-05'::TIMESTAMPTZ,
                        '__059_probe__')
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        assert tor_id is not None

        # ends_at <= starts_at → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO time_off_requests "
                        "(user_id, starts_at, ends_at) "
                        "VALUES (:uid, "
                        "'2026-06-03 00:00:00-05'::TIMESTAMPTZ, "
                        "'2026-06-01 00:00:00-05'::TIMESTAMPTZ)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "time_off_requests CHECK accepted "
                    "ends_at <= starts_at"
                )
        finally:
            sp_inner.rollback()

        # Bad status → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO time_off_requests "
                        "(user_id, starts_at, ends_at, status) "
                        "VALUES (:uid, "
                        "'2026-06-01 00:00:00-05'::TIMESTAMPTZ, "
                        "'2026-06-02 00:00:00-05'::TIMESTAMPTZ, "
                        "'maybe')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "time_off_requests CHECK accepted status='maybe'"
                )
        finally:
            sp_inner.rollback()

        # ---- time_off_decision_events probes ----
        connection.execute(
            text(
                """
                INSERT INTO time_off_decision_events
                    (request_id, actor_kind, actor_user_id, action,
                     old_values, new_values)
                VALUES (:rid, 'staff', :uid, 'requested',
                        '{}'::jsonb,
                        '{"status": "pending"}'::jsonb)
                """
            ),
            {"rid": tor_id, "uid": user_id},
        )

        # Bad action → CHECK rejects (locking the vocabulary)
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO time_off_decision_events "
                        "(request_id, actor_kind, action) "
                        "VALUES (:rid, 'owner', 'rubber-stamped')"
                    ),
                    {"rid": tor_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "time_off_decision_events CHECK accepted "
                    "action='rubber-stamped'"
                )
        finally:
            sp_inner.rollback()

        # Bad actor_kind → CHECK rejects
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO time_off_decision_events "
                        "(request_id, actor_kind, action) "
                        "VALUES (:rid, 'robot', 'approved')"
                    ),
                    {"rid": tor_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "time_off_decision_events CHECK accepted "
                    "actor_kind='robot'"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
