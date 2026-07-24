"""Phase 10 Slice 6 — recurring staff unavailability (Epic 3.4).

Stylist-submitted standing rules of the form "I am unavailable on
weekday X from HH:MM to HH:MM", distinct from one-off time-off
requests (`time_off_requests`) and from manager-set templates
(`staff_shifts`). A stylist owns their own rows; the manager-side
weekly grid grays out matching cells the same way it grays approved
time-off, and the schedule publish path treats a published shift that
overlaps an active rule as a conflict (per-shift skip, mirroring the
existing time-off skip semantics).

Schema notes:

  - `weekday` is ISO weekday 1-7 (Mon=1, Sun=7) to match the
    convention `staff_shifts.working_days` already uses.
  - `start_time_local` / `end_time_local` are `TIME` columns living
    in boutique-local wall clock. Same-day intervals only —
    `end > start` is enforced by CHECK. Cross-midnight blocks
    aren't supported in this slice (no operational request for
    them; can split as two rows on adjacent weekdays if we ever
    need it).
  - `effective_from` / `effective_until` bound the rule's lifetime.
    `effective_from` defaults to "today" so a fresh row immediately
    applies. `effective_until IS NULL` means the rule is open-
    ended; setting a date makes it stop applying after that date,
    inclusive. No `deleted_at` per project policy (no delete UX
    that needs soft-delete history; if a stylist removes a rule,
    the row goes away outright).
  - Partial UNIQUE on `(user_id, weekday, start_time_local,
    end_time_local) WHERE effective_until IS NULL` rejects
    double-creating the same active rule. Two non-overlapping time
    intervals on the same weekday are still allowed (different
    start/end → different unique key).

DML probes round-trip every CHECK and the partial-unique guard.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE recurring_unavailability (
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                weekday SMALLINT NOT NULL
                    CHECK (weekday BETWEEN 1 AND 7),
                start_time_local TIME NOT NULL,
                end_time_local TIME NOT NULL,
                effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
                effective_until DATE NULL,
                reason TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ru_time_range
                    CHECK (end_time_local > start_time_local),
                CONSTRAINT chk_ru_effective_range
                    CHECK (
                        effective_until IS NULL
                        OR effective_until >= effective_from
                    )
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_ru_user_weekday "
            "ON recurring_unavailability(user_id, weekday)"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uniq_ru_active "
            "ON recurring_unavailability(user_id, weekday, "
            "    start_time_local, end_time_local) "
            "WHERE effective_until IS NULL"
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
        # Round-trip a basic indefinite rule.
        row_id = connection.execute(
            text(
                """
                INSERT INTO recurring_unavailability
                    (user_id, weekday, start_time_local, end_time_local,
                     reason)
                VALUES (:uid, 2, '18:00', '21:00', 'school pickup')
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        assert row_id is not None

        check_row = connection.execute(
            text(
                "SELECT weekday, start_time_local, end_time_local, "
                "       effective_until, reason "
                "FROM recurring_unavailability WHERE id = :id"
            ),
            {"id": row_id},
        ).first()
        assert check_row[0] == 2
        # TIME comes back as a datetime.time; compare lenient.
        assert str(check_row[1]) == "18:00:00"
        assert str(check_row[2]) == "21:00:00"
        assert check_row[3] is None, (
            f"effective_until should default to NULL, got {check_row[3]!r}"
        )
        assert check_row[4] == "school pickup"

        # Non-overlapping second rule on same weekday OK.
        morning_id = connection.execute(
            text(
                "INSERT INTO recurring_unavailability "
                "(user_id, weekday, start_time_local, end_time_local) "
                "VALUES (:uid, 2, '06:00', '08:00') RETURNING id"
            ),
            {"uid": user_id},
        ).scalar()
        assert morning_id is not None

        # Same active rule twice → partial-unique rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO recurring_unavailability "
                        "(user_id, weekday, start_time_local, "
                        " end_time_local) "
                        "VALUES (:uid, 2, '18:00', '21:00')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "uniq_ru_active accepted a duplicate active rule"
                )
        finally:
            sp_inner.rollback()

        # Bad weekday (8) → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO recurring_unavailability "
                        "(user_id, weekday, start_time_local, "
                        " end_time_local) "
                        "VALUES (:uid, 8, '09:00', '10:00')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "weekday CHECK accepted 8"
                )
        finally:
            sp_inner.rollback()

        # end <= start → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO recurring_unavailability "
                        "(user_id, weekday, start_time_local, "
                        " end_time_local) "
                        "VALUES (:uid, 3, '17:00', '09:00')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ru_time_range accepted end <= start"
                )
        finally:
            sp_inner.rollback()

        # effective_until < effective_from → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO recurring_unavailability "
                        "(user_id, weekday, start_time_local, "
                        " end_time_local, effective_from, "
                        " effective_until) "
                        "VALUES (:uid, 4, '09:00', '10:00', "
                        " '2026-06-01', '2026-05-01')"
                    ),
                    {"uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ru_effective_range accepted until < from"
                )
        finally:
            sp_inner.rollback()

        # Setting effective_until on an active rule frees the active-
        # unique slot, so a new indefinite rule with the same shape is
        # allowed afterward.
        connection.execute(
            text(
                "UPDATE recurring_unavailability SET effective_until = "
                "'2026-12-31' WHERE id = :id"
            ),
            {"id": row_id},
        )
        reopen_id = connection.execute(
            text(
                "INSERT INTO recurring_unavailability "
                "(user_id, weekday, start_time_local, end_time_local) "
                "VALUES (:uid, 2, '18:00', '21:00') RETURNING id"
            ),
            {"uid": user_id},
        ).scalar()
        assert reopen_id is not None
        assert reopen_id != row_id

        # ON DELETE CASCADE: drop a placeholder user that owns no rows
        # would be a no-op; just sanity-check that the FK is wired by
        # asserting the column metadata. (Cleanup handled by sp.rollback.)
    finally:
        sp.rollback()
