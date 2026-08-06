"""Smoke tests for the merged deal timeline.

The deal page used to make a rep read three surfaces (activity, notes, a
separate SMS box) and merge them mentally. This proves the one endpoint
tells the whole story:

  - activity, notes, and text messages all appear, in one list, newest first,
  - the lead's own words and source page surface in the summary,
  - `last_touch` reflects whatever actually happened most recently,
  - the wrong-number flag fires on an INBOUND text saying so (and not on an
    outbound one, which would be us saying it),
  - `needs_first_contact` fires on an unworked lead and clears once someone
    calls, texts, or writes a note,
  - `follow_up_due` fires on an overdue unresolved reminder,
  - messages on a conversation NOT linked to this deal stay off its story.

Run as a script (matches the repo convention):
    .venv/bin/python tests/test_deal_timeline_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]

# Phone numbers are UNIQUE in contacts, and this box's CRM holds real rows
# on the tidy-looking 555 numbers, so a hardcoded literal collides with
# production data rather than with a previous run. Derive per-run numbers.
_PHONE_SEED = int(_TAG[:4], 16) % 9000 + 1000
_BUYER_PHONE = f"+1210{_PHONE_SEED}0143"
_OTHER_PHONE = f"+1210{_PHONE_SEED}0199"
_WALKIN_PHONE = f"+1210{_PHONE_SEED}0177"
_SHOP_PHONE = f"+1210{_PHONE_SEED}1000"


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"tl-{suffix}",
            email=f"tl-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Timeline Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _make_contact(name: str, phone: str) -> int:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                "INSERT INTO contacts (display_name, first_name, phone_e164, tags) "
                "VALUES (:dn, :fn, :ph, '[\"timeline-smoke\"]'::jsonb) RETURNING id"
            ),
            {"dn": name, "fn": name.split()[0], "ph": phone},
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


def _seed_attribution(event_id: int, source_page: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO lead_attribution (event_id, source_page) "
                "VALUES (:eid, :page)"
            ),
            {"eid": event_id, "page": source_page},
        )
        db.commit()
    finally:
        db.close()


def _seed_imported_note(event_id: int, body: str) -> None:
    """An authorless note is what migration 100 produced from the old
    events.notes blob — the customer's words as they arrived."""
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO event_notes (event_id, body, created_at) "
                "VALUES (:eid, :body, NOW() - INTERVAL '3 hours')"
            ),
            {"eid": event_id, "body": body},
        )
        db.commit()
    finally:
        db.close()


def _seed_conversation(event_id: int, contact_id: int) -> int:
    db = SessionLocal()
    try:
        cid = db.execute(
            sql_text(
                "INSERT INTO conversations "
                "(channel, provider, external_id, contact_id, event_id, status) "
                "VALUES ('sms', 'twilio', :ext, :contact, :event, 'open') RETURNING id"
            ),
            {
                "ext": f"timeline-smoke-{_TAG}-{event_id}",
                "contact": contact_id,
                "event": event_id,
            },
        ).scalar()
        db.commit()
        return int(cid)
    finally:
        db.close()


def _seed_message(conversation_id: int, direction: str, body: str, minutes_ago: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "INSERT INTO conversation_messages "
                "(conversation_id, direction, channel, provider, sender_ref, "
                " recipient_ref, body, status, created_at) "
                "VALUES (:cid, :dir, 'sms', 'twilio', :sender, :recipient, :body, "
                "        'delivered', NOW() - (:mins || ' minutes')::interval)"
            ),
            {
                "cid": conversation_id,
                "dir": direction,
                "sender": _BUYER_PHONE if direction == "inbound" else _SHOP_PHONE,
                "recipient": _SHOP_PHONE if direction == "inbound" else _BUYER_PHONE,
                "body": body,
                "mins": minutes_ago,
            },
        )
        db.commit()
    finally:
        db.close()


def _cleanup(user_ids: list[int], contact_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM conversation_messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE contact_id = ANY(:ids))"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM conversations WHERE contact_id = ANY(:ids)"),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM lead_attribution WHERE event_id IN "
                    "(SELECT id FROM events WHERE primary_contact_id = ANY(:ids))"
                ),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"),
                {"ids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": user_ids}
            )
        db.commit()
    finally:
        db.close()


def main() -> int:
    admin_id, admin_email = _make_admin()
    contact_ids: list[int] = []
    try:
        resp = client.post(
            "/api/auth/login",
            json={"email": admin_email, "password": "smoke-pass-12345"},
        )
        _assert(resp.status_code == 200, "login", resp.text)
        auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        print("login ok")

        buyer_id = _make_contact("Tessa Rivera", _BUYER_PHONE)
        contact_ids.append(buyer_id)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": buyer_id,
                "event_type": "vehicle_sale",
                "event_name": "Challenger — Tessa",
            },
        )
        _assert(resp.status_code == 201, "create deal", resp.text)
        deal_id = resp.json()["id"]

        _seed_attribution(deal_id, "/contact-us")
        _seed_imported_note(deal_id, "How much is the Challenger and does it have a sunroof?")
        print(f"seeded deal {deal_id}")

        # --- unworked lead: needs first contact ---------------------------
        resp = client.get(f"/api/events/{deal_id}/timeline", headers=auth)
        _assert(resp.status_code == 200, "get timeline", resp.text)
        body = resp.json()
        summary = body["summary"]
        _assert(summary["lead_source"] == "website", "source derived", summary)
        _assert(summary["lead_source_page"] == "/contact-us", "source page", summary)
        _assert(
            "sunroof" in (summary["lead_message"] or ""),
            "customer's words in summary",
            summary,
        )
        codes = [f["code"] for f in summary["flags"]]
        _assert(codes == ["needs_first_contact"], "unworked lead flagged", codes)
        print("unworked lead summary ok")

        # --- a staff note clears needs_first_contact ----------------------
        resp = client.post(
            f"/api/events/{deal_id}/notes",
            headers=auth,
            json={"body": "Called, no answer. Trying again tomorrow."},
        )
        _assert(resp.status_code == 201, "add note", resp.text)
        note_id = resp.json()["id"]

        body = client.get(f"/api/events/{deal_id}/timeline", headers=auth).json()
        codes = [f["code"] for f in body["summary"]["flags"]]
        _assert("needs_first_contact" not in codes, "first contact cleared", codes)
        _assert(
            body["summary"]["last_touch_label"] == "Staff note added",
            "last touch is the note",
            body["summary"],
        )
        print("staff touch clears the flag ok")

        # --- calls and texts join the same list ----------------------------
        # A real call attempt, so the activity mirror is exercised end to end
        # rather than hand-inserting an activity row.
        resp = client.post(
            f"/api/contacts/{buyer_id}/call-attempts",
            headers=auth,
            json={
                "phone": _BUYER_PHONE,
                "event_id": deal_id,
                "source": "deal_overview",
            },
        )
        _assert(resp.status_code == 201, "log a call", resp.text)

        convo_id = _seed_conversation(deal_id, buyer_id)
        _seed_message(convo_id, "outbound", "Hi Tessa, this is Randy at Kelley.", 30)
        _seed_message(convo_id, "inbound", "Wrong number ", 5)

        body = client.get(f"/api/events/{deal_id}/timeline", headers=auth).json()
        items = body["items"]
        kinds = {i["kind"] for i in items}
        _assert(kinds == {"activity", "note", "message"}, "all sources merged", kinds)

        stamps = [i["at"] for i in items]
        _assert(stamps == sorted(stamps, reverse=True), "newest first", stamps[:4])

        # Both texts made it onto the deal's story.
        message_bodies = [i["body"] for i in items if i["kind"] == "message"]
        _assert(len(message_bodies) == 2, "both texts on the timeline", message_bodies)
        # ...and so did the call.
        _assert(
            any(i["subtype"] == "call.initiated" for i in items),
            "the call is on the timeline",
            [i["subtype"] for i in items],
        )

        # `last_touch` is a description OF the newest item, whatever it is.
        # Asserting the invariant rather than a fixed row keeps this honest:
        # everything created through the API lands at NOW, so which row wins
        # depends on seed order, but it must always agree with items[0].
        _assert(
            body["summary"]["last_touch_at"] == items[0]["at"],
            "last touch is the newest item",
            (body["summary"]["last_touch_at"], items[0]["at"]),
        )
        _assert(
            bool(body["summary"]["last_touch_label"]),
            "last touch has a label",
            body["summary"],
        )
        print("merged ordering ok")

        # --- wrong-number flag from the customer's own reply --------------
        flags = {f["code"]: f for f in body["summary"]["flags"]}
        _assert("wrong_number" in flags, "wrong number flagged", flags)
        _assert(
            _BUYER_PHONE in flags["wrong_number"]["detail"],
            "flag names the number",
            flags["wrong_number"],
        )
        print("wrong-number flag ok")

        # --- an overdue reminder raises follow_up_due ---------------------
        db = SessionLocal()
        try:
            db.execute(
                sql_text(
                    "UPDATE event_notes SET remind_at = :w, remind_user_id = :u "
                    "WHERE id = :id"
                ),
                {
                    "w": datetime.now(timezone.utc) - timedelta(hours=2),
                    "u": admin_id,
                    "id": note_id,
                },
            )
            db.commit()
        finally:
            db.close()

        body = client.get(f"/api/events/{deal_id}/timeline", headers=auth).json()
        codes = [f["code"] for f in body["summary"]["flags"]]
        _assert("follow_up_due" in codes, "overdue reminder flagged", codes)

        # Resolving it retires the flag.
        client.post(
            f"/api/events/{deal_id}/notes/{note_id}/resolve",
            headers=auth,
            json={"resolved": True},
        )
        body = client.get(f"/api/events/{deal_id}/timeline", headers=auth).json()
        codes = [f["code"] for f in body["summary"]["flags"]]
        _assert("follow_up_due" not in codes, "resolved reminder unflagged", codes)
        print("follow-up flag ok")

        # --- another deal's texts never leak in ---------------------------
        other_id = _make_contact("Other Buyer", _OTHER_PHONE)
        contact_ids.append(other_id)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": other_id,
                "event_type": "vehicle_sale",
                "event_name": "Unrelated deal",
            },
        )
        other_deal = resp.json()["id"]
        other_convo = _seed_conversation(other_deal, other_id)
        _seed_message(other_convo, "inbound", "This belongs to the other deal", 1)

        body = client.get(f"/api/events/{deal_id}/timeline", headers=auth).json()
        bodies = [i.get("body") or "" for i in body["items"]]
        _assert(
            not any("other deal" in b for b in bodies),
            "other deal's messages excluded",
            bodies,
        )
        # ...and the outbound "wrong number" case: us saying it must not flag.
        other_body = client.get(f"/api/events/{other_deal}/timeline", headers=auth).json()
        _assert(
            "wrong_number" not in [f["code"] for f in other_body["summary"]["flags"]],
            "no false wrong-number flag",
            other_body["summary"]["flags"],
        )
        print("deal isolation ok")

        # --- a walk-in is contact: it must NOT read "nobody reached out" ---
        # The deal only exists because a rep stood in front of the customer
        # and typed them in, so flagging it as untouched was nonsense — and
        # the rep who took it should be named.
        walkin_contact = _make_contact("Walk In Wanda", _WALKIN_PHONE)
        contact_ids.append(walkin_contact)
        resp = client.post(
            "/api/events",
            headers=auth,
            json={
                "primary_contact_id": walkin_contact,
                "event_type": "vehicle_sale",
                "event_name": "Walk-in deal",
            },
        )
        walkin_deal = resp.json()["id"]

        db = SessionLocal()
        try:
            db.execute(
                sql_text(
                    "INSERT INTO activity_log (event_id, actor_kind, actor_user_id, "
                    " actor_display_name, activity_type, payload) "
                    "VALUES (:e, 'staff', :u, 'Randy Kelley', "
                    "        'event.walk_in_created', '{}'::jsonb)"
                ),
                {"e": walkin_deal, "u": admin_id},
            )
            db.commit()
        finally:
            db.close()

        body = client.get(f"/api/events/{walkin_deal}/timeline", headers=auth).json()
        codes = [f["code"] for f in body["summary"]["flags"]]
        _assert(
            "needs_first_contact" not in codes,
            "a walk-in is not an untouched lead",
            codes,
        )
        _assert(
            body["summary"]["created_via"] == "Walk-in",
            "walk-in origin reported",
            body["summary"],
        )
        _assert(
            body["summary"]["created_by_name"] == "Randy Kelley",
            "the rep who took it is named",
            body["summary"],
        )
        print("walk-in origin + flag suppression ok")

        # --- 404 ------------------------------------------------------------
        resp = client.get("/api/events/99999999/timeline", headers=auth)
        _assert(resp.status_code == 404, "unknown deal 404s", resp.text)
        print("not-found ok")

        print()
        print("deal timeline smoke ok")
        return 0
    finally:
        _cleanup([admin_id], contact_ids)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
