"""Native-dialer call-attempt tracking smoke (Phase 7).

Standalone script (run: `python tests/test_call_attempts_smoke.py`) exercising
the live app via TestClient against whatever DATABASE_URL points at. In CI /
local this is pointed at a migrated scratch clone so prod is never touched; it
also self-cleans every row it creates.

Covers:
  * authorization (sales + admin allowed to log; unauthenticated 401) and
    contact-not-found (404).
  * idempotent double-submission (same idempotency_key → one row, created flag
    flips to false on replay).
  * authenticated-user attribution (salesperson_user_id comes from the token,
    NOT the body — a spoofed body field is ignored).
  * outcome transition validation (bad outcome 422; 'call_initiated' not
    reportable; idempotent re-record; notes-only patch; never infers connected).
  * manager aggregation by salesperson + business-local date.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, ContactCallAttempt, User  # noqa: E402
from modules.core.services import business_time  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_user_ids: list[int] = []
_contact_ids: list[int] = []
_event_ids: list[int] = []


def _assert(cond, label, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_user(role: str) -> int:
    db = SessionLocal()
    try:
        u = User(
            username=f"{role}-call-{_TAG}-{uuid.uuid4().hex[:4]}",
            email=f"{role}-call-{_TAG}-{uuid.uuid4().hex[:4]}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"Call {role.title()} {_TAG}",
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


_phone_seq = [0]


def _make_contact() -> tuple[int, str]:
    """Create a contact with a UNIQUE E.164 (contacts.phone_e164 is unique).
    Returns (contact_id, phone_e164)."""
    db = SessionLocal()
    try:
        _phone_seq[0] += 1
        # Deterministic-unique test number in the 555 exchange.
        e164 = f"+1210555{_phone_seq[0]:04d}"
        c = Contact(display_name=f"Call Cust {_TAG}", phone=e164, phone_e164=e164)
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id, e164
    finally:
        db.close()


def _token(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _cleanup():
    db = SessionLocal()
    try:
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contact_call_attempts WHERE contact_id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
            # activity_log rows cascade with their event; events RESTRICT on
            # contact delete, so the deal has to go first.
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM contact_call_attempts WHERE salesperson_user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": _user_ids})
        db.commit()
    finally:
        db.close()


def test_authz_and_not_found():
    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    # Unauthenticated → 401.
    r = client.post(f"/api/contacts/{contact_id}/call-attempts", json={"phone": "+12105551212"})
    _assert(r.status_code == 401, "unauth 401", r.status_code)

    # Sales token allowed.
    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "source": "contact_detail"},
    )
    _assert(r.status_code == 201, "sales create 201", r.status_code)

    # Contact not found → 404.
    r = client.post("/api/contacts/999999999/call-attempts", headers=sh, json={"phone": "+12105551212"})
    _assert(r.status_code == 404, "missing contact 404", r.status_code)
    print("authz + not-found ok")


def test_idempotent_double_submit():
    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    key = f"idem-{_TAG}-{uuid.uuid4().hex}"

    r1 = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "idempotency_key": key},
    )
    _assert(r1.status_code == 201, "first create 201", r1.status_code)
    _assert(r1.json()["created"] is True, "first created=true", r1.json())
    first_id = r1.json()["id"]

    # Same key again (double-tap) → same row, created=false, no dup.
    r2 = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "idempotency_key": key},
    )
    _assert(r2.json()["id"] == first_id, "idempotent same id", r2.json())
    _assert(r2.json()["created"] is False, "replay created=false", r2.json())

    db = SessionLocal()
    try:
        n = db.query(ContactCallAttempt).filter_by(idempotency_key=key).count()
    finally:
        db.close()
    _assert(n == 1, "exactly one row for key", n)
    print("idempotent double submit ok")


def test_user_attribution_from_token():
    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")
    other_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    # Attempt to SPOOF a different salesperson via the body — must be ignored.
    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "salesperson_user_id": other_id},
    )
    _assert(r.status_code == 201, "create 201", r.status_code)
    _assert(
        r.json()["salesperson_user_id"] == sales_id,
        "attribution from token not body",
        r.json()["salesperson_user_id"],
    )
    print("user attribution from token ok")


def test_outcome_transitions():
    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    r = client.post(f"/api/contacts/{contact_id}/call-attempts", headers=sh, json={"phone": "+12105551212"})
    attempt_id = r.json()["id"]
    _assert(r.json()["outcome"] == "call_initiated", "born initiated", r.json())
    _assert(r.json()["outcome_pending"] is True, "born pending", r.json())

    # Bad outcome → 422.
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}", headers=sh, json={"outcome": "banana"}
    )
    _assert(r.status_code == 422, "bad outcome 422", r.status_code)

    # 'call_initiated' is not reportable → 422 (can't walk back to un-started).
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}",
        headers=sh,
        json={"outcome": "call_initiated"},
    )
    _assert(r.status_code == 422, "initiated not reportable 422", r.status_code)

    # Valid outcome → clears pending.
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}",
        headers=sh,
        json={"outcome": "connected", "notes": "spoke with buyer"},
    )
    _assert(r.status_code == 200, "connected 200", r.status_code)
    _assert(r.json()["outcome"] == "connected", "outcome connected", r.json())
    _assert(r.json()["outcome_pending"] is False, "pending cleared", r.json())

    # Idempotent re-record of the same outcome → still 200, same state.
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}", headers=sh, json={"outcome": "connected"}
    )
    _assert(r.status_code == 200 and r.json()["outcome"] == "connected", "idempotent outcome", r.json())

    # Notes-only patch leaves outcome untouched.
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}", headers=sh, json={"notes": "left a card"}
    )
    _assert(r.json()["outcome"] == "connected", "notes-only keeps outcome", r.json())
    _assert(r.json()["notes"] == "left a card", "notes updated", r.json())

    # Empty patch → 422 nothing_to_update.
    r = client.patch(f"/api/contacts/{contact_id}/call-attempts/{attempt_id}", headers=sh, json={})
    _assert(r.status_code == 422, "empty patch 422", r.status_code)
    print("outcome transitions ok")


def test_aggregation_by_rep_and_local_date():
    contact_id, _phone = _make_contact()
    admin_id = _make_user("admin")
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    # Two calls today: one connected, one left as initiated (pending).
    a1 = client.post(f"/api/contacts/{contact_id}/call-attempts", headers=sh, json={"phone": "+12105551212"}).json()
    client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{a1['id']}", headers=sh, json={"outcome": "connected"}
    )
    client.post(f"/api/contacts/{contact_id}/call-attempts", headers=sh, json={"phone": "+12105551212"})

    # Sales token forbidden on the manager endpoint.
    r = client.get("/api/admin/call-activity/summary", headers=sh)
    _assert(r.status_code == 403, "sales on admin summary 403", r.status_code)

    today = business_time.business_date().isoformat()
    r = client.get(f"/api/admin/call-activity/summary?date={today}", headers=ah)
    _assert(r.status_code == 200, "summary 200", r.status_code)
    body = r.json()
    _assert(body["date"] == today, "summary date echoes", body)
    rep = next((x for x in body["reps"] if x["salesperson_user_id"] == sales_id), None)
    _assert(rep is not None, "rep present in summary", body)
    _assert(rep["initiated"] == 2, "initiated=2", rep)
    _assert(rep["connected"] == 1, "connected=1 (initiated NOT counted)", rep)
    _assert(rep["pending"] == 1, "pending=1", rep)
    _assert(body["calls_today"] >= 2, "calls_today total", body)

    # Recent list includes our contact.
    r = client.get("/api/admin/call-activity/recent?limit=50", headers=ah)
    _assert(r.status_code == 200, "recent 200", r.status_code)
    _assert(
        any(x["contact_id"] == contact_id for x in r.json()["recent"]),
        "recent includes contact",
        len(r.json()["recent"]),
    )
    print("aggregation by rep + local date ok")


def test_admin_can_write_and_read():
    """Admins (not only sales) can log + list call attempts on the contact
    routes (require_any_scope('admin','sales'))."""
    contact_id, _phone = _make_contact()
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    r = client.post(f"/api/contacts/{contact_id}/call-attempts", headers=ah, json={"phone": "+12105551212"})
    _assert(r.status_code == 201, "admin create 201", r.status_code)
    _assert(r.json()["salesperson_user_id"] == admin_id, "admin attributed", r.json())

    r = client.get(f"/api/contacts/{contact_id}/call-attempts", headers=ah)
    _assert(r.status_code == 200 and len(r.json()["call_attempts"]) == 1, "admin list", r.json())
    print("admin can write + read ok")


def test_cross_contact_and_missing_attempt_isolation():
    """PATCH is scoped to (contact_id, attempt_id): you cannot reach an attempt
    via a DIFFERENT contact's path (IDOR guard), and a missing attempt → 404."""
    c1, _p1 = _make_contact()
    c2, _p2 = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    a = client.post(f"/api/contacts/{c1}/call-attempts", headers=sh, json={"phone": "+12105551212"}).json()

    # Correct owner can PATCH.
    r = client.patch(f"/api/contacts/{c1}/call-attempts/{a['id']}", headers=sh, json={"outcome": "connected"})
    _assert(r.status_code == 200, "owner patch 200", r.status_code)

    # Same attempt id via a DIFFERENT contact path → 404 (not found under c2).
    r = client.patch(f"/api/contacts/{c2}/call-attempts/{a['id']}", headers=sh, json={"outcome": "no_answer"})
    _assert(r.status_code == 404, "cross-contact patch 404", r.status_code)

    # Missing attempt id → 404.
    r = client.patch(f"/api/contacts/{c1}/call-attempts/999999999", headers=sh, json={"outcome": "no_answer"})
    _assert(r.status_code == 404, "missing attempt 404", r.status_code)

    # Unauthenticated PATCH → 401.
    r = client.patch(f"/api/contacts/{c1}/call-attempts/{a['id']}", json={"outcome": "busy"})
    _assert(r.status_code == 401, "unauth patch 401", r.status_code)
    print("cross-contact + missing attempt isolation ok")


def test_business_local_date_boundary():
    """A call created just after UTC midnight but still 'yesterday' shop-local
    must bucket into the previous business day, not today. Proves the UTC→local
    day-bounds math (the flagged off-by-one risk)."""
    from datetime import datetime, timedelta, timezone

    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")

    # Pick a target LOCAL day = yesterday, and stamp a row at 00:30 local time —
    # which in America/Chicago is ~05:30/06:30 UTC the same calendar day, but the
    # point is to place it unambiguously inside yesterday's LOCAL window and
    # assert it lands there (and NOT in today).
    local_today = business_time.business_date()
    target_local_day = local_today - timedelta(days=1)
    tz = business_time.shop_tz()
    # 00:30 local on the target day → aware datetime → store as UTC.
    from datetime import time as _time

    local_dt = datetime.combine(target_local_day, _time(0, 30), tzinfo=tz)
    created_utc = local_dt.astimezone(timezone.utc)

    # Seed a row directly with the injected created_at (the API stamps 'now', so
    # we insert via the model to control the timestamp).
    db = SessionLocal()
    try:
        row = ContactCallAttempt(
            contact_id=contact_id,
            salesperson_user_id=sales_id,
            salesperson_display_name="Boundary Rep",
            phone_e164="+12105551212",
            outcome="connected",
            outcome_pending=False,
            created_at=created_utc,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}

    # It must appear in the TARGET (yesterday) summary...
    r = client.get(
        f"/api/admin/call-activity/summary?date={target_local_day.isoformat()}", headers=ah
    )
    rep = next((x for x in r.json()["reps"] if x["salesperson_user_id"] == sales_id), None)
    _assert(rep is not None and rep["connected"] == 1, "row in target local day", r.json())

    # ...and NOT in today's summary.
    r = client.get(
        f"/api/admin/call-activity/summary?date={local_today.isoformat()}", headers=ah
    )
    rep_today = next((x for x in r.json()["reps"] if x["salesperson_user_id"] == sales_id), None)
    _assert(rep_today is None, "row NOT in today", r.json())
    print("business-local date boundary ok")


def test_outcome_allowlist_in_sync():
    """The three independent copies of the outcome allowlist (migration CHECK,
    service, router literal) must agree — a drift silently breaks inserts or
    rejects valid outcomes."""
    import importlib.util

    from modules.analytics.services.call_attempts import CALL_OUTCOMES

    # Migration copy.
    spec = importlib.util.spec_from_file_location(
        "m098",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "database/migrations/098_create_contact_call_attempts.py",
        ),
    )
    m098 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m098)
    _assert(set(m098._OUTCOMES) == set(CALL_OUTCOMES), "migration allowlist matches service", m098._OUTCOMES)

    # Router literal copy (reportable = all except call_initiated).
    from modules.contacts.routers.call_attempts import OutcomeLiteral
    import typing

    literal_vals = set(typing.get_args(OutcomeLiteral))
    _assert(
        literal_vals == set(CALL_OUTCOMES) - {"call_initiated"},
        "router literal matches reportable outcomes",
        literal_vals,
    )
    print("outcome allowlist in sync ok")


def _make_deal(contact_id: int) -> int:
    """A vehicle_sale deal to hang call attempts on."""
    db = SessionLocal()
    try:
        eid = db.execute(
            sql_text(
                "INSERT INTO events "
                "(primary_contact_id, event_type, event_name, status) "
                "VALUES (:cid, 'vehicle_sale', 'Call Mirror Deal', 'contacted') "
                "RETURNING id"
            ),
            {"cid": contact_id},
        ).scalar()
        db.commit()
        _event_ids.append(int(eid))
        return int(eid)
    finally:
        db.close()


def _call_activity(event_id: int) -> list[tuple[str, int]]:
    db = SessionLocal()
    try:
        return [
            (r[0], r[1])
            for r in db.execute(
                sql_text(
                    "SELECT activity_type, subject_id FROM activity_log "
                    "WHERE event_id = :eid AND subject_kind = 'contact_call_attempt' "
                    "ORDER BY id"
                ),
                {"eid": event_id},
            ).all()
        ]
    finally:
        db.close()


def test_deal_linked_calls_mirror_to_activity():
    """A call on a deal shows up on that deal's Activity timeline.

    Calls live in their own table because outcomes transition in place;
    the timeline is append-only. Both milestones get mirrored so a rep
    reading a deal can see the phone rang and how it went.
    """
    contact_id, _phone = _make_contact()
    event_id = _make_deal(contact_id)
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "event_id": event_id, "source": "deal_overview"},
    )
    _assert(r.status_code == 201, "create on deal 201", r.text)
    attempt_id = r.json()["id"]

    rows = _call_activity(event_id)
    _assert(rows == [("call.initiated", attempt_id)], "initiated mirrored", rows)

    # Reporting an outcome adds the second milestone.
    r = client.patch(
        f"/api/contacts/{contact_id}/call-attempts/{attempt_id}",
        headers=sh,
        json={"outcome": "no_answer"},
    )
    _assert(r.status_code == 200, "record outcome 200", r.text)
    rows = _call_activity(event_id)
    _assert(
        rows == [("call.initiated", attempt_id), ("call.outcome_recorded", attempt_id)],
        "outcome mirrored",
        rows,
    )

    # Payload carries the milestone facts and NO PII — activity_log forbids
    # phone numbers and note bodies in metadata.
    db = SessionLocal()
    try:
        payload = db.execute(
            sql_text(
                "SELECT payload FROM activity_log WHERE subject_kind = "
                "'contact_call_attempt' AND subject_id = :sid "
                "AND activity_type = 'call.outcome_recorded'"
            ),
            {"sid": attempt_id},
        ).scalar()
    finally:
        db.close()
    _assert(payload.get("outcome") == "no_answer", "payload outcome", payload)
    _assert("phone" not in payload and "notes" not in payload, "no PII in payload", payload)
    print("deal-linked calls mirror to activity ok")


def test_contact_only_call_writes_no_activity():
    """A call with no deal has no timeline to write to and must not crash.

    activity_log.event_id is NOT NULL, so the mirror has to no-op rather
    than invent an anchor.
    """
    contact_id, _phone = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}

    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212"},
    )
    _assert(r.status_code == 201, "contact-only create 201", r.text)
    attempt_id = r.json()["id"]

    db = SessionLocal()
    try:
        n = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM activity_log WHERE subject_kind = "
                "'contact_call_attempt' AND subject_id = :sid"
            ),
            {"sid": attempt_id},
        ).scalar()
    finally:
        db.close()
    _assert(n == 0, "no activity row for contact-only call", n)
    print("contact-only call writes no activity ok")


if __name__ == "__main__":
    try:
        test_authz_and_not_found()
        test_idempotent_double_submit()
        test_user_attribution_from_token()
        test_outcome_transitions()
        test_aggregation_by_rep_and_local_date()
        test_admin_can_write_and_read()
        test_cross_contact_and_missing_attempt_isolation()
        test_business_local_date_boundary()
        test_outcome_allowlist_in_sync()
        test_deal_linked_calls_mirror_to_activity()
        test_contact_only_call_writes_no_activity()
    finally:
        _cleanup()
    print("ALL CALL ATTEMPT SMOKES PASSED")
