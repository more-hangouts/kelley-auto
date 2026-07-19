"""Smoke tests for the Phase 6 add-participant flow.

Covers:

  - find_or_create_contact returns (contact, was_new) — new phone is
    True, existing phone is False.
  - The canonical POST /api/events/{event_id}/participants route works
    for both admin and sales tokens.
  - The deprecated /api/sales/events/{event_id}/participants alias
    still works for both tokens (one rolling release of backwards
    compatibility).
  - Both routes write a single event_participants row with
    contact_id populated and an activity_log entry where
    actor_kind='staff' and payload reflects the right was_new_contact
    flag and party_size_bucket vocabulary.
  - Migration 055 invariants: NULL contact_id is rejected; ON DELETE
    RESTRICT blocks deleting a contact that still has participants.
  - PartySizeBucket Literal accepts only the canonical values the
    booking widget emits (`pair`, `3_4`, `5_plus`); legacy `solo` /
    `2_3` / `4_plus` are rejected with 422.
  - Participants without an event return 404. Participants with an
    unparseable phone return 422 (`phone_invalid`).
"""

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    Contact,
    Event,
    EventParticipant,
    User,
)
from services import contact_service  # noqa: E402
from tests._attendance_helpers import (  # noqa: E402
    restore_gate,
    snapshot_and_disable_gate,
)

client = TestClient(app)

_user_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []
_participant_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p6-{suffix}",
            email=f"{role}-p6-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P6 {role.title()}",
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


def _token_for(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _seed_contact() -> int:
    db = SessionLocal()
    try:
        c = Contact(
            display_name="P6 Customer",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"p6-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _seed_event(contact_id: int) -> int:
    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="P6 Test Event",
            event_date=date.today() + timedelta(days=200),
            status="consulted",
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        _event_ids.append(e.id)
        # Seed the canonical celebrant participant so we have something
        # to compare against.
        prim = EventParticipant(
            event_id=e.id,
            contact_id=contact_id,
            role="quinceanera",
            display_name="P6 Quince",
        )
        db.add(prim)
        db.commit()
        return e.id
    finally:
        db.close()


def _activity_for(event_id: int, activity_type: str) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return list(
            db.execute(
                select(ActivityLog)
                .where(ActivityLog.event_id == event_id)
                .where(ActivityLog.activity_type == activity_type)
                .order_by(ActivityLog.id)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _track_new_contacts(before_ids: set[int]) -> None:
    """After a request that may have created new contacts, sweep them
    into the cleanup pool."""
    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text("SELECT id FROM contacts WHERE id NOT IN :ids"),
            {"ids": tuple(before_ids) or (-1,)},
        ).all()
        for r in rows:
            if r[0] not in _contact_ids:
                _contact_ids.append(int(r[0]))
    finally:
        db.close()


def _all_contact_ids() -> set[int]:
    db = SessionLocal()
    try:
        return {int(r[0]) for r in db.execute(sql_text("SELECT id FROM contacts")).all()}
    finally:
        db.close()


_gate_snapshot: dict | None = None


def main() -> None:
    global _gate_snapshot
    _gate_snapshot = snapshot_and_disable_gate()

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_headers = {
        "Authorization": f"Bearer {_token_for(sales_id, sales=True)}"
    }
    admin_headers = {
        "Authorization": f"Bearer {_token_for(admin_id, sales=False)}"
    }

    primary_contact_id = _seed_contact()
    event_id = _seed_event(primary_contact_id)

    # ---- 1. find_or_create_contact returns (contact, was_new). ----
    db = SessionLocal()
    try:
        new_phone = f"+1210555{uuid.uuid4().int % 10_000:04d}"
        contact_a, was_new_a = contact_service.find_or_create_contact(
            db,
            phone_e164=new_phone,
            email=None,
            phone="(210) 555-0099",
            first_name="Tuple",
            last_name="Probe",
        )
        db.commit()
        _contact_ids.append(contact_a.id)
        assert was_new_a is True

        contact_b, was_new_b = contact_service.find_or_create_contact(
            db,
            phone_e164=new_phone,
            email=None,
            phone="(210) 555-0099",
            first_name="Tuple",
            last_name="Probe",
        )
        db.commit()
        assert was_new_b is False
        assert contact_b.id == contact_a.id
    finally:
        db.close()

    # ---- 2. Canonical route, sales token, brand-new phone. ----
    before = _all_contact_ids()
    new_phone_2 = f"(210) 555-{uuid.uuid4().int % 10_000:04d}"
    resp = client.post(
        f"/api/events/{event_id}/participants",
        headers=sales_headers,
        json={
            "parent_first_name": "María",
            "parent_last_name": "García",
            "celebrant_first_name": "Sofia",
            "phone": new_phone_2,
            "email": "p6-newphone@example.com",
            "party_size_bucket": "3_4",
            "role": "dama",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _track_new_contacts(before)
    _participant_ids.append(body["id"])
    assert body["was_new_contact"] is True
    assert body["contact"]["id"] not in {primary_contact_id}
    assert body["role"] == "dama"
    assert body["party_size_bucket"] == "3_4"

    # Activity row written as staff with was_new_contact True.
    rows = _activity_for(event_id, "event.participant_added")
    assert len(rows) == 1
    assert rows[0].actor_kind == "staff"
    assert rows[0].payload["was_new_contact"] is True
    assert rows[0].payload["party_size_bucket"] == "3_4"
    assert rows[0].subject_kind == "contact"

    # ---- 3. Canonical route, admin token, EXISTING phone (dedupes). ----
    # The contact we just created via the sales call is already in the
    # contacts table. Reusing the same phone here should NOT create a
    # second contact; it should reuse the existing one.
    resp = client.post(
        f"/api/events/{event_id}/participants",
        headers=admin_headers,
        json={
            "parent_first_name": "María",
            "parent_last_name": "García",
            "celebrant_first_name": "Lucia",
            "phone": new_phone_2,  # same phone
            "party_size_bucket": "pair",
            "role": "dama",
        },
    )
    assert resp.status_code == 201, resp.text
    second = resp.json()
    _participant_ids.append(second["id"])
    assert second["was_new_contact"] is False
    assert second["contact"]["id"] == body["contact"]["id"]

    rows = _activity_for(event_id, "event.participant_added")
    assert len(rows) == 2
    assert rows[1].payload["was_new_contact"] is False

    # ---- 4. Deprecated /api/sales/events/.../participants alias still works. ----
    new_phone_3 = f"(210) 555-{uuid.uuid4().int % 10_000:04d}"
    before = _all_contact_ids()
    resp = client.post(
        f"/api/sales/events/{event_id}/participants",
        headers=sales_headers,
        json={
            "parent_first_name": "Ana",
            "celebrant_first_name": "Vera",
            "phone": new_phone_3,
            "role": "other",
        },
    )
    assert resp.status_code == 201, resp.text
    aliased = resp.json()
    _track_new_contacts(before)
    _participant_ids.append(aliased["id"])
    assert aliased["was_new_contact"] is True

    # Same alias accepts an admin token too — important so the admin
    # event Overview keeps working through the rolling release.
    resp = client.post(
        f"/api/sales/events/{event_id}/participants",
        headers=admin_headers,
        json={
            "parent_first_name": "Bea",
            "celebrant_first_name": "Lola",
            "phone": new_phone_3,  # same phone — should dedupe
            "role": "other",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["was_new_contact"] is False
    _participant_ids.append(resp.json()["id"])

    # ---- 5. PartySizeBucket Literal rejects legacy values. ----
    for legacy_bucket in ("solo", "2_3", "4_plus"):
        resp = client.post(
            f"/api/events/{event_id}/participants",
            headers=sales_headers,
            json={
                "parent_first_name": "Vocab",
                "celebrant_first_name": "Probe",
                "phone": "(210) 555-9000",
                "party_size_bucket": legacy_bucket,
            },
        )
        assert resp.status_code == 422, (legacy_bucket, resp.text)

    # ---- 6. Unknown event → 404. ----
    resp = client.post(
        "/api/events/99999999/participants",
        headers=sales_headers,
        json={
            "parent_first_name": "X",
            "celebrant_first_name": "Y",
            "phone": "(210) 555-1212",
        },
    )
    assert resp.status_code == 404, resp.text

    # ---- 7. Unparseable phone → 422 phone_invalid. ----
    resp = client.post(
        f"/api/events/{event_id}/participants",
        headers=sales_headers,
        json={
            "parent_first_name": "X",
            "celebrant_first_name": "Y",
            "phone": "not a phone",
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "phone_invalid"

    # ---- 8. Migration 055 invariants. ----
    # NULL contact_id is rejected at the DB layer.
    db = SessionLocal()
    try:
        try:
            db.execute(
                sql_text(
                    "INSERT INTO event_participants "
                    "(event_id, contact_id, role, display_name) "
                    "VALUES (:eid, NULL, 'other', 'null probe')"
                ),
                {"eid": event_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError(
                "event_participants accepted a NULL contact_id"
            )
    finally:
        db.close()

    # ON DELETE RESTRICT blocks contact deletion while a participant
    # references the contact.
    target_contact_id = body["contact"]["id"]
    db = SessionLocal()
    try:
        try:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :cid"),
                {"cid": target_contact_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError(
                "contact DELETE was not blocked while event_participants "
                "rows still referenced it"
            )
    finally:
        db.close()

    print("event_participants smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
        restore_gate(_gate_snapshot)
