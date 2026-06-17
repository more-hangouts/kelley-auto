"""Phase 10 Slice 1 of the Sales Portal: per-day published schedule.

One new table — `staff_schedule_entries` — that backs the manager's
weekly grid view. Where `staff_shifts` is a recurring template and
`staff_shift_overrides` is a date-range exception, this table holds
*concrete per-day shift instances*: rows the manager publishes
through the grid UI that the resolver treats as authoritative.

Precedence the resolver (updated in this slice) will respect:

    published entry > override > base template

The table also carries the per-day attendance lifecycle
(`attendance_status`) and a `manager_notes` text field. Phase 10
Slice 2 will wire `actual_clock_in_punch_id` / `actual_clock_out_punch_id`
from `services/clock_in.py` and add a no-show cron; for Slice 1 those
columns sit ready but stay NULL.

Design notes:

  - `starts_at_local` / `ends_at_local` are TIMESTAMPTZ. They carry the
    boutique-local wall time the manager picked, with the boutique tz
    attached. Storing the offset keeps DST transitions explicit and
    matches the convention already used by `staff_shifts.starts_at`.
  - `late_grace_minutes` is copied onto the row at create/publish time.
    For template-cloned entries the source shift's
    `late_grace_period_minutes` is copied through; manual entries default
    to 30. This keeps Slice 2's no-show cron from having to walk back
    to the template for a value that may have changed since publish.
  - `source` discriminates 'manual', 'template_clone', 'override_clone'
    so reporting can attribute a publish-week run later.
  - `attendance_status` is on this row only. We are intentionally NOT
    mutating the existing `staff_punches.status` semantics — punches
    keep their late/early_out/unscheduled vocabulary; the schedule layer
    tracks "did this scheduled shift happen" separately.
  - No UNIQUE on (user_id, business_date): split shifts and same-day
    coverage handoffs are real cases. The service layer rejects exact
    (user_id, starts_at_local, ends_at_local) duplicates.

DML probes round-trip every CHECK and the FK SET NULL on
`source_shift_id` (a deleted template must not orphan the entry's
attendance history).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_schedule_entries ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_schedule_entries (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                business_date DATE NOT NULL,
                starts_at_local TIMESTAMPTZ NOT NULL,
                ends_at_local TIMESTAMPTZ NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'published')),
                attendance_status VARCHAR(16) NOT NULL DEFAULT 'scheduled'
                    CHECK (attendance_status IN (
                        'scheduled', 'present', 'late', 'no_show', 'excused'
                    )),
                late_grace_minutes INTEGER NOT NULL DEFAULT 30
                    CHECK (late_grace_minutes BETWEEN 0 AND 120),
                source VARCHAR(16) NOT NULL DEFAULT 'manual'
                    CHECK (source IN (
                        'manual', 'template_clone', 'override_clone'
                    )),
                source_shift_id BIGINT NULL
                    REFERENCES staff_shifts(id) ON DELETE SET NULL,
                manager_notes TEXT NULL,
                actual_clock_in_punch_id BIGINT NULL
                    REFERENCES staff_punches(id) ON DELETE SET NULL,
                actual_clock_out_punch_id BIGINT NULL
                    REFERENCES staff_punches(id) ON DELETE SET NULL,
                published_at TIMESTAMPTZ NULL,
                published_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_sse_range
                    CHECK (ends_at_local > starts_at_local),
                CONSTRAINT chk_sse_publish_stamp
                    CHECK (
                        (status = 'draft' AND published_at IS NULL
                            AND published_by_user_id IS NULL)
                        OR (status = 'published' AND published_at IS NOT NULL)
                    )
            )
            """
        )
    )
    # Grid read path: scoped by (user_id, business_date) for the week.
    connection.execute(
        text(
            "CREATE INDEX idx_sse_user_date "
            "ON staff_schedule_entries(user_id, business_date)"
        )
    )
    # Resolver path: scoped by (user_id, business_date, status='published')
    # via a partial index so the hot read stays narrow.
    connection.execute(
        text(
            "CREATE INDEX idx_sse_user_date_published "
            "ON staff_schedule_entries(user_id, business_date) "
            "WHERE status = 'published'"
        )
    )
    # Phase 10 Slice 2's no-show cron: walks published entries whose
    # start has passed with no actual_clock_in. A partial index keeps
    # the scan tight even as historic rows pile up.
    connection.execute(
        text(
            "CREATE INDEX idx_sse_no_show_scan "
            "ON staff_schedule_entries(starts_at_local) "
            "WHERE status = 'published' "
            "  AND attendance_status = 'scheduled' "
            "  AND actual_clock_in_punch_id IS NULL"
        )
    )

    # ===== DML probes per the project rule =====

    user_row = connection.execute(
        text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).first()
    if user_row is None:
        # Fresh install — schema is in place; the behavioral smoke
        # seeds its own users.
        return
    user_id = int(user_row[0])

    sp = connection.begin_nested()
    try:
        # Seed a template shift the entry can reference.
        shift_id = connection.execute(
            text(
                """
                INSERT INTO staff_shifts
                    (user_id, starts_at, ends_at, working_days,
                     late_grace_period_minutes)
                VALUES
                    (:uid,
                     '2026-05-18 09:00:00-05'::TIMESTAMPTZ,
                     '2026-05-18 17:00:00-05'::TIMESTAMPTZ,
                     ARRAY[1, 2, 3, 4, 5],
                     15)
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        assert shift_id is not None

        # Round-trip a draft entry with all defaults.
        entry_id = connection.execute(
            text(
                """
                INSERT INTO staff_schedule_entries
                    (user_id, business_date,
                     starts_at_local, ends_at_local,
                     source, source_shift_id, late_grace_minutes,
                     created_by_user_id)
                VALUES
                    (:uid, '2026-05-18',
                     '2026-05-18 09:00:00-05'::TIMESTAMPTZ,
                     '2026-05-18 17:00:00-05'::TIMESTAMPTZ,
                     'template_clone', :sid, 15,
                     :uid)
                RETURNING id
                """
            ),
            {"uid": user_id, "sid": shift_id},
        ).scalar()
        assert entry_id is not None

        row = connection.execute(
            text(
                "SELECT status, attendance_status, late_grace_minutes, "
                "       source, source_shift_id "
                "FROM staff_schedule_entries WHERE id = :id"
            ),
            {"id": entry_id},
        ).first()
        assert row[0] == "draft", f"expected default status='draft', got {row[0]!r}"
        assert row[1] == "scheduled"
        assert row[2] == 15
        assert row[3] == "template_clone"
        assert row[4] == shift_id

        # ends_at_local <= starts_at_local → range CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local) "
                        "VALUES (:uid, '2026-05-18', "
                        "'2026-05-18 17:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-18 09:00:00-05'::TIMESTAMPTZ)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_schedule_entries chk_sse_range accepted "
                    "ends_at <= starts_at"
                )
        finally:
            sp_inner.rollback()

        # Bad status → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local, status) "
                        "VALUES (:uid, '2026-05-19', "
                        "'2026-05-19 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-19 17:00:00-05'::TIMESTAMPTZ, "
                        "'archived')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_schedule_entries status CHECK accepted 'archived'"
                )
        finally:
            sp_inner.rollback()

        # Bad attendance_status → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local, "
                        " attendance_status) "
                        "VALUES (:uid, '2026-05-19', "
                        "'2026-05-19 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-19 17:00:00-05'::TIMESTAMPTZ, "
                        "'partially-there')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_schedule_entries attendance_status CHECK "
                    "accepted bogus value"
                )
        finally:
            sp_inner.rollback()

        # late_grace_minutes > 120 → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local, "
                        " late_grace_minutes) "
                        "VALUES (:uid, '2026-05-19', "
                        "'2026-05-19 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-19 17:00:00-05'::TIMESTAMPTZ, "
                        "200)"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "staff_schedule_entries late_grace_minutes CHECK "
                    "accepted 200"
                )
        finally:
            sp_inner.rollback()

        # status='published' without published_at → publish-stamp CHECK
        # rejects (a published row MUST have a timestamp).
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local, status) "
                        "VALUES (:uid, '2026-05-19', "
                        "'2026-05-19 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-19 17:00:00-05'::TIMESTAMPTZ, "
                        "'published')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_sse_publish_stamp accepted status=published "
                    "with NULL published_at"
                )
        finally:
            sp_inner.rollback()

        # status='draft' with a published_at stamp → publish-stamp CHECK
        # rejects (draft must be unstamped — otherwise reporting drifts).
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_entries "
                        "(user_id, business_date, "
                        " starts_at_local, ends_at_local, "
                        " status, published_at) "
                        "VALUES (:uid, '2026-05-19', "
                        "'2026-05-19 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-05-19 17:00:00-05'::TIMESTAMPTZ, "
                        "'draft', NOW())"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_sse_publish_stamp accepted status=draft "
                    "with non-NULL published_at"
                )
        finally:
            sp_inner.rollback()

        # Source-shift SET NULL on parent shift delete: drop the template
        # and verify the entry's `source_shift_id` becomes NULL without
        # losing the row (attendance history must survive a template
        # cleanup).
        sp_inner = connection.begin_nested()
        try:
            connection.execute(
                text("DELETE FROM staff_shifts WHERE id = :id"),
                {"id": shift_id},
            )
            survivor = connection.execute(
                text(
                    "SELECT source_shift_id "
                    "FROM staff_schedule_entries WHERE id = :id"
                ),
                {"id": entry_id},
            ).scalar()
            assert survivor is None, (
                f"staff_schedule_entries.source_shift_id should be NULL "
                f"after parent shift delete, got {survivor!r}"
            )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
