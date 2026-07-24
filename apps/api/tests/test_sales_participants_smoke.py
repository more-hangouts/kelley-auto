"""Smoke test for the sales add-participant endpoint.

Creates one event, adds a participant with a brand-new phone, then adds
another participant using the primary contact's existing phone. Verifies the
contact identity behavior and the event_participants write.
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event, User  # noqa: E402


client = TestClient(app)


def _setup():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        primary_line = 1000 + (int(uuid.uuid4().hex[:6], 16) % 8000)
        new_line = 1000 + (int(uuid.uuid4().hex[:6], 16) % 8000)
        if new_line == primary_line:
            new_line += 1
        primary_phone = f"(210) 555-{primary_line:04d}"
        primary_phone_e164 = f"+1210555{primary_line:04d}"
        new_phone = f"(210) 555-{new_line:04d}"
        user = User(
            username=f"sales-participant-{suffix}",
            email=f"sales-participant-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Sales Participant Smoke",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.flush()

        contact = Contact(
            first_name="Primary",
            last_name="Parent",
            display_name="Primary Parent",
            email=f"primary-{suffix}@example.com",
            phone=primary_phone,
            phone_e164=primary_phone_e164,
        )
        db.add(contact)
        db.flush()

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name="Sales Participant Smoke Event",
            status="lead",
        )
        db.add(event)
        db.commit()
        return user.id, user.email, contact.id, contact.phone, event.id, new_phone
    finally:
        db.close()


def _cleanup(user_id: int, contact_ids: list[int], event_id: int) -> None:
    db = SessionLocal()
    try:
        db.execute(sql_text("DELETE FROM events WHERE id = :eid"), {"eid": event_id})
        for contact_id in contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :cid"), {"cid": contact_id}
            )
        db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.commit()
    finally:
        db.close()


user_id, user_email, primary_contact_id, primary_phone, event_id, new_phone = _setup()
created_contact_id: int | None = None

try:
    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        f"/api/sales/events/{event_id}/participants",
        headers=auth,
        json={
            "parent_first_name": "New",
            "parent_last_name": "Parent",
            "celebrant_first_name": "New",
            "celebrant_last_name": "Celebrant",
            "phone": new_phone,
            "email": "new-parent@example.com",
            "party_size_bucket": "3_4",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    created_contact_id = created["contact"]["id"]
    assert created["display_name"] == "New Celebrant"
    assert created["party_size_bucket"] == "3_4"
    assert created["was_new_contact"] is True
    assert created_contact_id != primary_contact_id
    print("new contact participant ok")

    resp = client.post(
        f"/api/sales/events/{event_id}/participants",
        headers=auth,
        json={
            "parent_first_name": "Primary",
            "parent_last_name": "Parent",
            "celebrant_first_name": "Existing",
            "celebrant_last_name": "Phone",
            "phone": primary_phone,
            "email": "should-not-merge-by-email@example.com",
            "party_size_bucket": "pair",
        },
    )
    assert resp.status_code == 201, resp.text
    reused = resp.json()
    assert reused["contact"]["id"] == primary_contact_id
    assert reused["display_name"] == "Existing Phone"
    assert reused["was_new_contact"] is False
    print("existing contact participant ok")

    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                "SELECT contact_id, display_name, measurements "
                "FROM event_participants WHERE event_id = :eid "
                "ORDER BY id"
            ),
            {"eid": event_id},
        ).all()
        assert len(rows) == 2, rows
        assert rows[0].contact_id == created_contact_id
        assert rows[0].measurements["party_size_bucket"] == "3_4"
        assert rows[1].contact_id == primary_contact_id
        assert rows[1].measurements["party_size_bucket"] == "pair"

        activities = db.execute(
            sql_text(
                "SELECT activity_type, payload FROM activity_log "
                "WHERE event_id = :eid ORDER BY id"
            ),
            {"eid": event_id},
        ).all()
        assert [a.activity_type for a in activities] == [
            "event.participant_added",
            "event.participant_added",
        ], activities
        assert activities[0].payload["was_new_contact"] is True
        assert activities[1].payload["was_new_contact"] is False
    finally:
        db.close()
    print("participant rows and activity log ok")
finally:
    contact_ids = [primary_contact_id]
    if created_contact_id is not None:
        contact_ids.append(created_contact_id)
    _cleanup(user_id, contact_ids, event_id)
