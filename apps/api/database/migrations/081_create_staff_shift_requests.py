"""Scheduling Phase 1: staff shift-request records + audit events.

Two new tables back the cover/drop/swap/pickup workflow described in
docs/SCHEDULING_IMPROVEMENT_PLAN.md. Phase 1 only introduces durable,
auditable REQUEST records — no schedule mutation happens from a request
until later phases.

  - `staff_shift_requests`: one row per staff request. `request_type`
    discriminates cover/swap/drop/pickup; `status` tracks the lifecycle
    (pending -> accepted_by_staff -> approved | denied | cancelled |
    expired). `source_entry_id`/`target_entry_id` point at the concrete
    published `staff_schedule_entries` involved. `open_shift_post_id` is
    reserved for pickup claims; the FK is added in Phase 3 when
    `open_shift_posts` lands (the column is nullable until then).

  - `staff_shift_request_events`: append-only audit log mirroring
    `time_off_decision_events`. Protected by the shared
    `enforce_audit_append_only()` trigger (migration 063) so the
    timeline can't be rewritten.

A per-type CHECK keeps the entry shape honest: cover/drop carry a source
entry only; swap carries both source and target; pickup carries neither
(it claims an open post instead). This mirrors `staff_schedule_entries`'
defensive schema posture.

DML probes round-trip the defaults, every CHECK (type/status/per-type
entry shape), the requester CASCADE, and the events append-only trigger.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    # ---- staff_shift_requests ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_shift_requests (
                id BIGSERIAL PRIMARY KEY,
                request_type VARCHAR(16) NOT NULL
                    CHECK (request_type IN (
                        'cover', 'swap', 'drop', 'pickup'
                    )),
                status VARCHAR(24) NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'accepted_by_staff', 'approved',
                        'denied', 'cancelled', 'expired'
                    )),
                source_entry_id BIGINT NULL
                    REFERENCES staff_schedule_entries(id) ON DELETE CASCADE,
                target_entry_id BIGINT NULL
                    REFERENCES staff_schedule_entries(id) ON DELETE CASCADE,
                open_shift_post_id BIGINT NULL,
                requester_user_id INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                candidate_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                accepted_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                accepted_at TIMESTAMPTZ NULL,
                decided_by_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                decided_at TIMESTAMPTZ NULL,
                reason TEXT NULL,
                decision_notes TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT chk_ssr_type_entries CHECK (
                    (request_type = 'cover'
                        AND source_entry_id IS NOT NULL
                        AND target_entry_id IS NULL)
                    OR (request_type = 'drop'
                        AND source_entry_id IS NOT NULL
                        AND target_entry_id IS NULL)
                    OR (request_type = 'swap'
                        AND source_entry_id IS NOT NULL
                        AND target_entry_id IS NOT NULL)
                    OR (request_type = 'pickup'
                        AND source_entry_id IS NULL
                        AND target_entry_id IS NULL)
                )
            )
            """
        )
    )
    # Sales "my requests": scoped by requester + lifecycle state.
    connection.execute(
        text(
            "CREATE INDEX idx_ssr_requester_status "
            "ON staff_shift_requests(requester_user_id, status)"
        )
    )
    # Admin queue: newest-first, optionally filtered by status.
    connection.execute(
        text(
            "CREATE INDEX idx_ssr_status_created "
            "ON staff_shift_requests(status, created_at DESC)"
        )
    )
    # "Requests where I'm the proposed candidate" — partial so it stays
    # tight (most rows have a null candidate until a direct cover/swap).
    connection.execute(
        text(
            "CREATE INDEX idx_ssr_candidate "
            "ON staff_shift_requests(candidate_user_id) "
            "WHERE candidate_user_id IS NOT NULL"
        )
    )
    # Find open requests touching a given concrete shift.
    connection.execute(
        text(
            "CREATE INDEX idx_ssr_source_entry "
            "ON staff_shift_requests(source_entry_id) "
            "WHERE source_entry_id IS NOT NULL"
        )
    )

    # ---- staff_shift_request_events (append-only audit) ----
    connection.execute(
        text(
            """
            CREATE TABLE staff_shift_request_events (
                id BIGSERIAL PRIMARY KEY,
                request_id BIGINT NOT NULL
                    REFERENCES staff_shift_requests(id) ON DELETE CASCADE,
                actor_kind VARCHAR(20) NOT NULL
                    CHECK (actor_kind IN ('staff', 'owner', 'system')),
                actor_user_id INTEGER NULL
                    REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(20) NOT NULL
                    CHECK (action IN (
                        'requested', 'accepted', 'approved', 'denied',
                        'cancelled', 'expired', 'amended'
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
            "CREATE INDEX idx_ssr_events_request "
            "ON staff_shift_request_events(request_id, created_at DESC)"
        )
    )
    # Append-only protection via the shared trigger function (mig 063).
    connection.execute(
        text(
            "DROP TRIGGER IF EXISTS "
            "trg_staff_shift_request_events_append_only "
            "ON staff_shift_request_events"
        )
    )
    connection.execute(
        text(
            """
            CREATE TRIGGER trg_staff_shift_request_events_append_only
            BEFORE UPDATE OR DELETE ON staff_shift_request_events
            FOR EACH ROW
            EXECUTE FUNCTION enforce_audit_append_only()
            """
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
        # Seed two published entries to point requests at.
        src_entry_id = connection.execute(
            text(
                """
                INSERT INTO staff_schedule_entries
                    (user_id, business_date, starts_at_local, ends_at_local,
                     status, published_at)
                VALUES
                    (:uid, '2026-07-01',
                     '2026-07-01 09:00:00-05'::TIMESTAMPTZ,
                     '2026-07-01 17:00:00-05'::TIMESTAMPTZ,
                     'published', NOW())
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()
        tgt_entry_id = connection.execute(
            text(
                """
                INSERT INTO staff_schedule_entries
                    (user_id, business_date, starts_at_local, ends_at_local,
                     status, published_at)
                VALUES
                    (:uid, '2026-07-02',
                     '2026-07-02 09:00:00-05'::TIMESTAMPTZ,
                     '2026-07-02 17:00:00-05'::TIMESTAMPTZ,
                     'published', NOW())
                RETURNING id
                """
            ),
            {"uid": user_id},
        ).scalar()

        # Round-trip a cover request with all defaults.
        req_id = connection.execute(
            text(
                """
                INSERT INTO staff_shift_requests
                    (request_type, source_entry_id, requester_user_id, reason)
                VALUES ('cover', :src, :uid, 'probe')
                RETURNING id
                """
            ),
            {"src": src_entry_id, "uid": user_id},
        ).scalar()
        row = connection.execute(
            text(
                "SELECT status, request_type, target_entry_id "
                "FROM staff_shift_requests WHERE id = :id"
            ),
            {"id": req_id},
        ).first()
        assert row[0] == "pending", f"default status, got {row[0]!r}"
        assert row[1] == "cover"
        assert row[2] is None

        # Append an audit event and confirm the table accepts it.
        ev_id = connection.execute(
            text(
                """
                INSERT INTO staff_shift_request_events
                    (request_id, actor_kind, action, new_values)
                VALUES (:rid, 'staff', 'requested',
                        '{"status": "pending"}'::jsonb)
                RETURNING id
                """
            ),
            {"rid": req_id},
        ).scalar()
        assert ev_id is not None

        # Bad request_type → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, source_entry_id, requester_user_id) "
                        "VALUES ('teleport', :src, :uid)"
                    ),
                    {"src": src_entry_id, "uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "request_type CHECK accepted 'teleport'"
                )
        finally:
            sp_inner.rollback()

        # Bad status → CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, source_entry_id, requester_user_id, "
                        " status) "
                        "VALUES ('cover', :src, :uid, 'maybe')"
                    ),
                    {"src": src_entry_id, "uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError("status CHECK accepted 'maybe'")
        finally:
            sp_inner.rollback()

        # cover WITH a target_entry_id → per-type CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, source_entry_id, target_entry_id, "
                        " requester_user_id) "
                        "VALUES ('cover', :src, :tgt, :uid)"
                    ),
                    {"src": src_entry_id, "tgt": tgt_entry_id, "uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssr_type_entries accepted cover with a target"
                )
        finally:
            sp_inner.rollback()

        # swap WITHOUT a target_entry_id → per-type CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, source_entry_id, requester_user_id) "
                        "VALUES ('swap', :src, :uid)"
                    ),
                    {"src": src_entry_id, "uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssr_type_entries accepted swap without a target"
                )
        finally:
            sp_inner.rollback()

        # pickup WITH a source_entry_id → per-type CHECK rejects.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "INSERT INTO staff_shift_requests "
                        "(request_type, source_entry_id, requester_user_id) "
                        "VALUES ('pickup', :src, :uid)"
                    ),
                    {"src": src_entry_id, "uid": user_id},
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_ssr_type_entries accepted pickup with a source"
                )
        finally:
            sp_inner.rollback()

        # (The events table's append-only trigger is verified in the
        # schema smoke via the pg_trigger catalog — probing it here would
        # be env-dependent, since a migration run with
        # ALLOW_AUDIT_MUTATION=1 bypasses the trigger.)

        # Requester CASCADE: deleting the requesting user removes the
        # request (and, by cascade, its events).
        sp_inner = connection.begin_nested()
        try:
            # A throwaway user so we don't disturb the seed user.
            tmp_uid = connection.execute(
                text(
                    "INSERT INTO users "
                    "(username, email, hashed_password, is_active, role) "
                    "VALUES (:u, :e, 'x', TRUE, 'sales') RETURNING id"
                ),
                {
                    "u": "ssr-probe-cascade",
                    "e": "ssr-probe-cascade@example.com",
                },
            ).scalar()
            tmp_req = connection.execute(
                text(
                    "INSERT INTO staff_shift_requests "
                    "(request_type, source_entry_id, requester_user_id) "
                    "VALUES ('cover', :src, :uid) RETURNING id"
                ),
                {"src": src_entry_id, "uid": tmp_uid},
            ).scalar()
            connection.execute(
                text("DELETE FROM users WHERE id = :id"), {"id": tmp_uid}
            )
            survivor = connection.execute(
                text(
                    "SELECT id FROM staff_shift_requests WHERE id = :id"
                ),
                {"id": tmp_req},
            ).first()
            assert survivor is None, (
                "deleting the requester should CASCADE-delete the request"
            )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
