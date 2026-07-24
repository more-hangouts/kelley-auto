"""Phase 10 Slice 3: admin-configurable schedule shift presets.

One new table — `staff_schedule_presets` — that backs the "Preset"
dropdown in the manager weekly grid (`AdminScheduleGrid.jsx`).
Slice 2 hard-coded three presets in the frontend; this slice moves
that list into the DB so the client can edit / add / archive presets
from the admin UI without a code change.

Design notes:

  - `start_time` / `end_time` are plain `TIME` columns. A preset is
    a time-of-day pair only; the concrete `staff_schedule_entries`
    row still carries the full TIMESTAMPTZ for the picked business
    date — the grid is what combines preset + cell-date into a real
    interval. Keeping the preset itself time-of-day avoids a
    DST/timezone-drift trap where a "9am-5pm" preset stored as a
    TIMESTAMPTZ silently rolls past a DST flip.
  - `active=FALSE` is soft-delete. Archived rows stay around so old
    audit references (a future audit row that pointed at the preset
    by id) survive a UI delete.
  - UNIQUE on `label` is *partial on active=TRUE` so an archived
    preset's label can be reused by a new active row. Postgres
    `NULLS NOT DISTINCT` doesn't apply here — `active` is NOT NULL
    — but the partial-index form is the right pattern for soft-delete
    uniqueness.
  - `sort_order` is a plain INTEGER (no default-fill trick); the
    seed migration assigns 100/200/300 so future hand-edits have
    room. The service layer's `update_preset` doesn't try to
    re-pack the sort gaps — that's a UX problem we'd solve with
    a drag-handle reorder verb, not a schema constraint.

DML probes round-trip every CHECK and the partial-unique index.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_schedule_presets ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_schedule_presets (
                id BIGSERIAL PRIMARY KEY,
                label VARCHAR(80) NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                late_grace_minutes INTEGER NOT NULL DEFAULT 30
                    CHECK (late_grace_minutes BETWEEN 0 AND 120),
                sort_order INTEGER NOT NULL DEFAULT 100
                    CHECK (sort_order >= 0),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ssp_range
                    CHECK (end_time > start_time),
                CONSTRAINT chk_ssp_label_nonblank
                    CHECK (length(btrim(label)) > 0)
            )
            """
        )
    )
    # Partial unique: an archived preset's label can be reused by a new
    # active row. Two active presets cannot share a label.
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_ssp_active_label "
            "ON staff_schedule_presets(label) WHERE active = TRUE"
        )
    )
    # The grid fetches active presets sorted by sort_order; index it.
    connection.execute(
        text(
            "CREATE INDEX idx_ssp_active_sort "
            "ON staff_schedule_presets(sort_order, label) "
            "WHERE active = TRUE"
        )
    )

    # ---- Seed the three Slice 2 hardcoded presets so the grid keeps
    # its current behavior on first deploy of Slice 3. ----
    connection.execute(
        text(
            """
            INSERT INTO staff_schedule_presets
                (label, start_time, end_time, late_grace_minutes, sort_order)
            VALUES
                ('Opening (9am - 5pm)', '09:00', '17:00', 30, 100),
                ('Mid (11am - 7pm)',    '11:00', '19:00', 30, 200),
                ('Closing (1pm - 9pm)', '13:00', '21:00', 30, 300)
            """
        )
    )

    # ===== DML probes per the project rule =====

    sp = connection.begin_nested()
    try:
        # Seed insert above already proved the happy path. Verify the
        # three rows landed and are sorted correctly.
        rows = connection.execute(
            text(
                "SELECT label, start_time, end_time, sort_order "
                "FROM staff_schedule_presets "
                "WHERE active = TRUE "
                "ORDER BY sort_order"
            )
        ).all()
        assert len(rows) == 3, f"expected 3 seed presets, got {len(rows)}"
        assert rows[0][0] == "Opening (9am - 5pm)"
        assert rows[2][0] == "Closing (1pm - 9pm)"

        # end_time <= start_time → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time) VALUES "
                        "('__069_probe_bad_range__', '17:00', '09:00')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssp_range accepted end_time <= start_time"
                )
        finally:
            sp_inner.rollback()

        # Equal start/end → also rejected (the CHECK is strict >).
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time) VALUES "
                        "('__069_probe_zero_range__', '09:00', '09:00')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssp_range accepted end_time == start_time"
                )
        finally:
            sp_inner.rollback()

        # Blank label → CHECK rejects (length-after-trim > 0).
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time) VALUES "
                        "('   ', '09:00', '17:00')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssp_label_nonblank accepted whitespace-only label"
                )
        finally:
            sp_inner.rollback()

        # late_grace_minutes > 120 → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time, "
                        " late_grace_minutes) VALUES "
                        "('__069_probe_grace__', '09:00', '17:00', 200)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "late_grace_minutes CHECK accepted 200"
                )
        finally:
            sp_inner.rollback()

        # sort_order < 0 → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time, sort_order) "
                        "VALUES ('__069_probe_sort__', '09:00', '17:00', -1)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError("sort_order CHECK accepted -1")
        finally:
            sp_inner.rollback()

        # Two active presets cannot share a label.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_schedule_presets "
                        "(label, start_time, end_time) VALUES "
                        "('Opening (9am - 5pm)', '08:00', '16:00')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "uq_ssp_active_label accepted duplicate active label"
                )
        finally:
            sp_inner.rollback()

        # Archive one of the seeds; then a new active row with the
        # same label should be allowed (the partial unique excludes
        # the archived row).
        sp_inner = connection.begin_nested()
        try:
            connection.execute(
                text(
                    "UPDATE staff_schedule_presets SET active = FALSE "
                    "WHERE label = 'Opening (9am - 5pm)'"
                )
            )
            # Re-insert with same label — should succeed.
            new_id = connection.execute(
                text(
                    "INSERT INTO staff_schedule_presets "
                    "(label, start_time, end_time) VALUES "
                    "('Opening (9am - 5pm)', '08:00', '16:00') "
                    "RETURNING id"
                )
            ).scalar()
            assert new_id is not None, (
                "partial unique blocked re-use of an archived label"
            )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
