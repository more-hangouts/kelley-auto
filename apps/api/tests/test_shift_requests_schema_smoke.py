"""Schema smoke for Scheduling Phase 1: staff_shift_requests + events.

Validates the migration-081 schema with real DML against the live dev DB
(per the project rule: prove constraints with concrete INSERTs):

  1. request_type CHECK rejects an unknown type.
  2. status CHECK rejects an unknown status.
  3. Per-type CHECK (chk_ssr_type_entries):
       - cover with a target_entry_id is rejected
       - swap without a target_entry_id is rejected
       - pickup with a source_entry_id is rejected
  4. Requester FK CASCADE: deleting the requester removes the request.
  5. Events FK CASCADE: deleting a request removes its events.
  6. The events table carries the append-only trigger (pg_trigger).
"""

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

_user_ids: list[int] = []


def _make_user(*, role: str = "sales") -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p0sched-{suffix}",
            email=f"{role}-p0sched-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P0Sched {role.title()} {suffix}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _published_entry(db, user_id: int, day: str) -> int:
    return db.execute(
        sql_text(
            """
            INSERT INTO staff_schedule_entries
                (user_id, business_date, starts_at_local, ends_at_local,
                 status, published_at)
            VALUES
                (:uid, :d,
                 (:d || ' 09:00:00-05')::TIMESTAMPTZ,
                 (:d || ' 17:00:00-05')::TIMESTAMPTZ,
                 'published', NOW())
            RETURNING id
            """
        ),
        {"uid": user_id, "d": day},
    ).scalar()


def _expect_integrity(db, label: str, stmt: str, params: dict) -> None:
    sp = db.begin_nested()
    try:
        db.execute(sql_text(stmt), params)
    except IntegrityError:
        sp.rollback()
        return
    sp.rollback()
    raise AssertionError(f"{label}: constraint did not reject the row")


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_request_events "
                    "WHERE request_id IN ("
                    "SELECT id FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids))"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_shift_requests "
                    "WHERE requester_user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM staff_schedule_entries "
                    "WHERE user_id = ANY(:uids)"
                ),
                {"uids": _user_ids},
            )
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    requester = _make_user(role="sales")
    other = _make_user(role="sales")

    db = SessionLocal()
    try:
        src = _published_entry(db, requester, "2026-08-03")
        tgt = _published_entry(db, other, "2026-08-04")
        db.commit()

        # ---- CHECK constraints ----
        print("===== CHECK constraints =====")
        _expect_integrity(
            db,
            "request_type",
            "INSERT INTO staff_shift_requests "
            "(request_type, source_entry_id, requester_user_id) "
            "VALUES ('teleport', :src, :uid)",
            {"src": src, "uid": requester},
        )
        _expect_integrity(
            db,
            "status",
            "INSERT INTO staff_shift_requests "
            "(request_type, source_entry_id, requester_user_id, status) "
            "VALUES ('cover', :src, :uid, 'maybe')",
            {"src": src, "uid": requester},
        )
        _expect_integrity(
            db,
            "cover_with_target",
            "INSERT INTO staff_shift_requests "
            "(request_type, source_entry_id, target_entry_id, "
            " requester_user_id) VALUES ('cover', :src, :tgt, :uid)",
            {"src": src, "tgt": tgt, "uid": requester},
        )
        _expect_integrity(
            db,
            "swap_without_target",
            "INSERT INTO staff_shift_requests "
            "(request_type, source_entry_id, requester_user_id) "
            "VALUES ('swap', :src, :uid)",
            {"src": src, "uid": requester},
        )
        _expect_integrity(
            db,
            "pickup_with_source",
            "INSERT INTO staff_shift_requests "
            "(request_type, source_entry_id, requester_user_id) "
            "VALUES ('pickup', :src, :uid)",
            {"src": src, "uid": requester},
        )

        # ---- Valid rows round-trip ----
        print("===== valid round-trip + defaults =====")
        req_id = db.execute(
            sql_text(
                "INSERT INTO staff_shift_requests "
                "(request_type, source_entry_id, requester_user_id) "
                "VALUES ('cover', :src, :uid) RETURNING id"
            ),
            {"src": src, "uid": requester},
        ).scalar()
        row = db.execute(
            sql_text(
                "SELECT status, request_type FROM staff_shift_requests "
                "WHERE id = :id"
            ),
            {"id": req_id},
        ).first()
        assert row[0] == "pending", f"default status, got {row[0]!r}"
        assert row[1] == "cover"

        ev_id = db.execute(
            sql_text(
                "INSERT INTO staff_shift_request_events "
                "(request_id, actor_kind, action) "
                "VALUES (:rid, 'staff', 'requested') RETURNING id"
            ),
            {"rid": req_id},
        ).scalar()
        assert ev_id is not None
        db.commit()

        # ---- Events FK CASCADE on request delete ----
        print("===== events cascade on request delete =====")
        db.execute(
            sql_text("DELETE FROM staff_shift_requests WHERE id = :id"),
            {"id": req_id},
        )
        db.commit()
        remaining = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_shift_request_events "
                "WHERE id = :id"
            ),
            {"id": ev_id},
        ).scalar()
        assert remaining == 0, "events should cascade-delete with the request"

        # ---- Append-only trigger present ----
        print("===== append-only trigger present =====")
        trig = db.execute(
            sql_text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = "
                "'trg_staff_shift_request_events_append_only' "
                "AND NOT tgisinternal"
            )
        ).first()
        assert trig is not None, (
            "append-only trigger missing on staff_shift_request_events"
        )
    finally:
        db.close()

    # ---- Requester CASCADE (delete user removes request) ----
    print("===== requester cascade =====")
    db = SessionLocal()
    try:
        src2 = _published_entry(db, requester, "2026-08-05")
        req2 = db.execute(
            sql_text(
                "INSERT INTO staff_shift_requests "
                "(request_type, source_entry_id, requester_user_id) "
                "VALUES ('cover', :src, :uid) RETURNING id"
            ),
            {"src": src2, "uid": requester},
        ).scalar()
        db.commit()
        # Deleting the source entry also cascades the request; instead
        # delete a throwaway requester to isolate the requester FK.
        throwaway = _make_user(role="sales")
        src3 = _published_entry(db, throwaway, "2026-08-06")
        req3 = db.execute(
            sql_text(
                "INSERT INTO staff_shift_requests "
                "(request_type, source_entry_id, requester_user_id) "
                "VALUES ('cover', :src, :uid) RETURNING id"
            ),
            {"src": src3, "uid": throwaway},
        ).scalar()
        db.commit()
        db.execute(
            sql_text("DELETE FROM users WHERE id = :id"), {"id": throwaway}
        )
        db.commit()
        gone = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM staff_shift_requests WHERE id = :id"
            ),
            {"id": req3},
        ).scalar()
        assert gone == 0, "deleting the requester should cascade the request"
        # req2 (still-present requester) survives.
        assert (
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM staff_shift_requests WHERE id = :id"
                ),
                {"id": req2},
            ).scalar()
            == 1
        )
    finally:
        db.close()

    print("shift_requests_schema smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
