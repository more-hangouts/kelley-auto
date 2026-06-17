"""Smoke for D3-C: ``GET /api/admin/recycle-bin``.

Drives the read-side of the Recycle Bin:

  - Archived rows of the queried entity_type show up with the
    expected display_name + secondary_label + audit fields.
  - Keyset pagination via ``before_id`` returns disjoint pages.
  - ``deleted_by_user_id`` filter scopes to one actor.
  - 400 on unsupported entity_type; 401 without admin auth.
  - Orphan-contact archives (Gate 1 fallback, no activity row) still
    appear in the bin but with NULL ``deleted_by_user_id`` / reason.

Runs serially. Cleans up.

    venv/bin/python tests/test_recycle_bin_endpoint_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
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

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    EventParticipant,
    Invoice,
    SpecialOrder,
    User,
)

client = TestClient(app)

_PREFIX = "D3C Recycle Smoke"
_EMAIL_PREFIX = "d3c-recycle-smoke-"

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _make_admin(label: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"d3c-recycle-smoke-{label}-{suffix}",
            email=f"{_EMAIL_PREFIX}{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"{_PREFIX} {label} {suffix}",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return int(u.id), create_access_token(u)
    finally:
        db.close()


def _seed_event_with_contact(label: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        digits = f"55508{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"{_PREFIX} {label} {suffix}",
            email=f"{_EMAIL_PREFIX}{label.lower()}-{suffix}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{_PREFIX} {label} Event",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
        )
        db.add(event)
        db.commit()
        _created_contact_ids.append(int(contact.id))
        _created_event_ids.append(int(event.id))
        return int(contact.id), int(event.id)
    finally:
        db.close()


def _seed_orphan_contact(label: str) -> int:
    """Contact with NO events — exercises the Gate 1 fallback path
    (archive without an activity_log row)."""
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        contact = Contact(
            display_name=f"{_PREFIX} Orphan {label} {suffix}",
            email=f"{_EMAIL_PREFIX}orphan-{label}-{suffix}@example.com",
        )
        db.add(contact)
        db.commit()
        _created_contact_ids.append(int(contact.id))
        return int(contact.id)
    finally:
        db.close()


def _archive_event(token: str, event_id: int, reason: str) -> None:
    r = client.post(
        f"/api/admin/events/{event_id}/archive",
        json={"reason": reason},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


def _archive_contact(token: str, contact_id: int, reason: str) -> None:
    r = client.post(
        f"/api/admin/contacts/{contact_id}/archive",
        json={"reason": reason},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_auth_required() -> None:
    r = client.get("/api/admin/recycle-bin?entity_type=contact")
    assert r.status_code in (401, 403), r.text


def check_bad_entity_type(token: str) -> None:
    r = client.get(
        "/api/admin/recycle-bin?entity_type=garbage",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "unsupported_entity_type"


def check_events_in_bin(token: str, actor_id: int) -> None:
    contact_id, event_id = _seed_event_with_contact("EventBin")
    _archive_event(token, event_id, "duplicate")

    r = client.get(
        "/api/admin/recycle-bin?entity_type=event&page_size=200",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "event"
    match = next(
        (it for it in body["items"] if it["entity_id"] == event_id), None
    )
    assert match is not None, "event missing from Recycle Bin"
    assert match["reason"] == "duplicate"
    assert match["deleted_by_user_id"] == actor_id
    assert match["deleted_at"] is not None
    assert match["display_name"]


def check_contacts_in_bin_orphan(token: str) -> None:
    contact_id = _seed_orphan_contact("ContactBin")
    _archive_contact(token, contact_id, "created_by_mistake")

    r = client.get(
        "/api/admin/recycle-bin?entity_type=contact&page_size=200",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    match = next(
        (it for it in body["items"] if it["entity_id"] == contact_id), None
    )
    assert match is not None, "orphan contact missing from Recycle Bin"
    # Gate 1 fallback path: no activity row → no audit metadata.
    assert match["deleted_by_user_id"] is None
    assert match["reason"] is None


def check_pagination(token: str) -> None:
    """Seed three contacts that share the same archived state and walk
    the bin with page_size=1; pages must be disjoint."""
    cids = [_seed_orphan_contact(f"Page{i}") for i in range(3)]
    for cid in cids:
        _archive_contact(token, cid, "duplicate")

    seen: set[int] = set()
    before_id: int | None = None
    target = set(cids)
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(10):
        url = "/api/admin/recycle-bin?entity_type=contact&page_size=1"
        if before_id is not None:
            url += f"&before_id={before_id}"
        r = client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["items"]
        for it in items:
            if it["entity_id"] in target:
                assert it["entity_id"] not in seen, "duplicate page row"
                seen.add(it["entity_id"])
        if body["next_before_id"] is None:
            break
        before_id = body["next_before_id"]
    assert target <= seen, f"missing seeded ids: {target - seen}"


def check_filter_by_actor(token_a: str, actor_a: int, token_b: str, actor_b: int) -> None:
    # Each actor archives one orphan contact.
    cid_a = _seed_orphan_contact("ActorA")
    cid_b = _seed_orphan_contact("ActorB")
    _archive_contact(token_a, cid_a, "duplicate")
    _archive_contact(token_b, cid_b, "duplicate")

    # ...but orphan contacts write no activity row, so deleted_by
    # filter can't see them. Use events instead for this check.
    contact_id_a2, event_id_a2 = _seed_event_with_contact("ActorA2")
    contact_id_b2, event_id_b2 = _seed_event_with_contact("ActorB2")
    _archive_event(token_a, event_id_a2, "duplicate")
    _archive_event(token_b, event_id_b2, "duplicate")

    r = client.get(
        f"/api/admin/recycle-bin?entity_type=event&deleted_by_user_id={actor_a}&page_size=200",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 200, r.text
    ids = {it["entity_id"] for it in r.json()["items"]}
    assert event_id_a2 in ids
    assert event_id_b2 not in ids


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup() -> None:
    for layer in (
        (_created_invoice_ids, Invoice),
        (_created_event_ids, Event),
        (_created_contact_ids, Contact),
        (_created_user_ids, User),
    ):
        ids, model = layer
        db = SessionLocal()
        try:
            for row_id in ids:
                row = db.get(model, row_id)
                if row is not None:
                    db.delete(row)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"cleanup({model.__name__}) failed: {exc!r}")
        finally:
            db.close()


def main() -> int:
    failed = False
    try:
        check_auth_required()
        actor_a, token_a = _make_admin("a")
        actor_b, token_b = _make_admin("b")
        check_bad_entity_type(token_a)
        check_events_in_bin(token_a, actor_a)
        check_contacts_in_bin_orphan(token_a)
        check_pagination(token_a)
        check_filter_by_actor(token_a, actor_a, token_b, actor_b)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        import traceback
        traceback.print_exc()
        failed = True
    finally:
        cleanup()
    if failed:
        return 1
    print("D3-C recycle-bin smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
