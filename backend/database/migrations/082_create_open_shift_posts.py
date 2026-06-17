"""Scheduling Phase 3: manager-posted open shifts (pickup board).

`open_shift_posts` holds shifts a manager posts WITHOUT an assignee, for
staff to claim from their portal (see docs/SCHEDULING_IMPROVEMENT_PLAN.md
— "Open Shifts Are Not Schedule Entries Yet"). When a pickup is approved
a normal published `staff_schedule_entries` row is created for the
claimant and the post closes as `claimed`.

This migration also wires the FK that migration 081 deferred:
`staff_shift_requests.open_shift_post_id -> open_shift_posts(id)`. The two
tables reference each other (a request points at the post it claims; the
post points at the winning request), so the column existed first and the
constraint is added here once both tables are present.

DML probes round-trip the defaults, the range/status/claim CHECKs, the
creator SET NULL, and the new request->post FK behavior.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE open_shift_posts (
                id BIGSERIAL PRIMARY KEY,
                business_date DATE NOT NULL,
                starts_at_local TIMESTAMPTZ NOT NULL,
                ends_at_local TIMESTAMPTZ NOT NULL,
                late_grace_minutes INTEGER NOT NULL DEFAULT 30
                    CHECK (late_grace_minutes BETWEEN 0 AND 120),
                source VARCHAR(16) NOT NULL DEFAULT 'manual'
                    CHECK (source IN (
                        'manual', 'template_clone', 'override_clone'
                    )),
                manager_notes TEXT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'open'
                    CHECK (status IN (
                        'open', 'claimed', 'cancelled', 'expired'
                    )),
                created_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                claimed_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                claimed_request_id BIGINT NULL
                    REFERENCES staff_shift_requests(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_osp_range
                    CHECK (ends_at_local > starts_at_local),
                CONSTRAINT chk_osp_claimed_has_claimer CHECK (
                    status <> 'claimed' OR claimed_by_user_id IS NOT NULL
                )
            )
            """
        )
    )
    # Board read path: the open posts for a date range.
    connection.execute(
        text(
            "CREATE INDEX idx_osp_open_date "
            "ON open_shift_posts(business_date) WHERE status = 'open'"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_osp_status_date "
            "ON open_shift_posts(status, business_date)"
        )
    )

    # Wire the deferred FK from migration 081.
    connection.execute(
        text(
            """
            ALTER TABLE staff_shift_requests
                ADD CONSTRAINT fk_ssr_open_shift_post
                FOREIGN KEY (open_shift_post_id)
                REFERENCES open_shift_posts(id) ON DELETE SET NULL
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_ssr_open_shift_post "
            "ON staff_shift_requests(open_shift_post_id) "
            "WHERE open_shift_post_id IS NOT NULL"
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
        post_id = connection.execute(
            text(
                """
                INSERT INTO open_shift_posts
                    (business_date, starts_at_local, ends_at_local,
                     created_by_user_id)
                VALUES
                    ('2026-07-10',
                     '2026-07-10 09:00:00-05'::TIMESTAMPTZ,
                     '2026-07-10 17:00:00-05'::TIMESTAMPTZ,
                     :uid)
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        row = connection.execute(
            text(
                "SELECT status, late_grace_minutes, source "
                "FROM open_shift_posts WHERE id = :id"
            ),
            {"id": post_id},
        ).first()
        assert row[0] == "open", f"default status, got {row[0]!r}"
        assert row[1] == 30
        assert row[2] == "manual"

        # range CHECK
        sp_i = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO open_shift_posts "
                        "(business_date, starts_at_local, ends_at_local) "
                        "VALUES ('2026-07-10', "
                        "'2026-07-10 17:00:00-05'::TIMESTAMPTZ, "
                        "'2026-07-10 09:00:00-05'::TIMESTAMPTZ)"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError("chk_osp_range accepted end<=start")
        finally:
            sp_i.rollback()

        # status CHECK
        sp_i = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO open_shift_posts "
                        "(business_date, starts_at_local, ends_at_local, "
                        " status) VALUES ('2026-07-10', "
                        "'2026-07-10 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-07-10 17:00:00-05'::TIMESTAMPTZ, 'taken')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError("status CHECK accepted 'taken'")
        finally:
            sp_i.rollback()

        # claimed-without-claimer CHECK
        sp_i = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO open_shift_posts "
                        "(business_date, starts_at_local, ends_at_local, "
                        " status) VALUES ('2026-07-10', "
                        "'2026-07-10 09:00:00-05'::TIMESTAMPTZ, "
                        "'2026-07-10 17:00:00-05'::TIMESTAMPTZ, 'claimed')"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_osp_claimed_has_claimer accepted claimed w/o claimer"
                )
        finally:
            sp_i.rollback()

        # request -> post FK SET NULL on post delete
        sp_i = connection.begin_nested()
        try:
            req_id = connection.execute(
                text(
                    "INSERT INTO staff_shift_requests "
                    "(request_type, requester_user_id, open_shift_post_id) "
                    "VALUES ('pickup', :uid, :pid) RETURNING id"
                ),
                {"uid": user_id, "pid": post_id},
            ).scalar()
            connection.execute(
                text("DELETE FROM open_shift_posts WHERE id = :id"),
                {"id": post_id},
            )
            linked = connection.execute(
                text(
                    "SELECT open_shift_post_id FROM staff_shift_requests "
                    "WHERE id = :id"
                ),
                {"id": req_id},
            ).scalar()
            assert linked is None, (
                "deleting a post should NULL the request's open_shift_post_id"
            )
        finally:
            sp_i.rollback()

        # pickup with a source entry is still rejected by 081's CHECK
        sp_i = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, requester_user_id, open_shift_post_id) "
                        "VALUES ('pickup', :uid, :pid)"
                    ),
                    {"uid": user_id, "pid": post_id},
                )
            except Exception as exc:  # pragma: no cover - sanity
                raise AssertionError(
                    f"pickup with only a post should be allowed: {exc}"
                )
            else:
                pass
        finally:
            sp_i.rollback()
    finally:
        sp.rollback()
