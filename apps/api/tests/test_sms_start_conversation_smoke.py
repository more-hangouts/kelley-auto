"""Start-SMS-conversation from CRM surfaces smoke (Phase 8).

Standalone script exercising the live app via TestClient against whatever
DATABASE_URL points at (a migrated scratch clone in local/CI so prod is never
touched). Transport is mocked — no real SMS is sent. Self-cleans.

Covers:
  * eligibility contract (no_phone / no_consent / opted_out / eligible).
  * idempotent create/reuse: two creates return the same conversation; an
    existing inbound thread is reused; multiple deals for one contact don't fork
    the SMS history.
  * create does NOT send a message.
  * consent bypass: hitting the send endpoint directly on a no-consent thread is
    blocked (recipient_no_sms_consent), and a no-valid-phone thread → 422.
  * authorization: sales can start + send; sales CANNOT list the inbox; admin
    can. Unauthenticated → 401.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Conversation, ConversationMessage, User  # noqa: E402
from modules.messaging.services import inbox_service  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_user_ids: list[int] = []
_contact_ids: list[int] = []
_phone_seq = [1000]


def _assert(cond, label, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_user(role: str) -> int:
    db = SessionLocal()
    try:
        u = User(
            username=f"{role}-p8-{_TAG}-{uuid.uuid4().hex[:4]}",
            email=f"{role}-p8-{_TAG}-{uuid.uuid4().hex[:4]}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"P8 {role.title()}",
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


def _make_contact(*, phone=True, consent=True, opted_out=False, archived=False) -> tuple[int, str | None]:
    db = SessionLocal()
    try:
        _phone_seq[0] += 1
        e164 = f"+1210777{_phone_seq[0]:04d}" if phone else None
        now = datetime.now(timezone.utc)
        c = Contact(
            display_name=f"P8 Cust {_TAG}",
            phone=e164,
            phone_e164=e164,
            sms_consent_at=now if consent else None,
            sms_consent_source="test" if consent else None,
            sms_opted_out_at=now if opted_out else None,
            deleted_at=now if archived else None,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id, e164
    finally:
        db.close()


def _token(uid: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _cleanup():
    db = SessionLocal()
    try:
        if _contact_ids:
            db.execute(
                sql_text(
                    "DELETE FROM conversation_messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE contact_id = ANY(:ids))"
                ),
                {"ids": _contact_ids},
            )
            db.execute(sql_text("DELETE FROM conversations WHERE contact_id = ANY(:ids)"), {"ids": _contact_ids})
            # Events reference contacts with ON DELETE RESTRICT — clear them first.
            db.execute(sql_text("DELETE FROM events WHERE primary_contact_id = ANY(:ids)"), {"ids": _contact_ids})
            db.execute(sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"), {"ids": _contact_ids})
        if _user_ids:
            db.execute(sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": _user_ids})
        db.commit()
    finally:
        db.close()


# Sending is enabled + transport configured for these tests (transport mocked so
# nothing is really sent).
def _sending_on():
    return (
        mock.patch("config.settings.SMS_SENDING_ENABLED", True),
        mock.patch("modules.core.services.sms_transport.sms_transport_configured", return_value=True),
    )


def test_eligibility_contract():
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    m1, m2 = _sending_on()
    with m1, m2:
        # no phone
        cid, _ = _make_contact(phone=False)
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
        _assert(r.status_code == 422 and r.json()["detail"]["code"] == "recipient_has_no_valid_phone", "no_phone 422", r.json())

        # no consent (and no inbound thread)
        cid, _ = _make_contact(consent=False)
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
        _assert(r.status_code == 200, "create still works no-consent", r.status_code)
        _assert(r.json()["eligibility"] == {"eligible": False, "reason": "no_consent"}, "no_consent reason", r.json())

        # opted out
        cid, _ = _make_contact(opted_out=True)
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
        _assert(r.json()["eligibility"]["reason"] == "opted_out", "opted_out reason", r.json())

        # eligible
        cid, _ = _make_contact()
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
        _assert(r.json()["eligibility"] == {"eligible": True, "reason": "eligible"}, "eligible", r.json())
    print("eligibility contract ok")


def test_archived_blocked():
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact(archived=True)
    r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r.status_code == 404 and r.json()["detail"]["code"] == "contact_not_found", "archived 404", r.json())
    print("archived blocked ok")


def test_reuse_and_no_fork():
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact()

    r1 = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid, "event_id": None})
    _assert(r1.json()["created"] is True, "first created", r1.json())
    conv1 = r1.json()["conversation_id"]

    # Second create for the SAME contact (a different deal) reuses the thread.
    r2 = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r2.json()["conversation_id"] == conv1, "reuse same conv", r2.json())
    _assert(r2.json()["created"] is False, "second not created", r2.json())

    # Exactly one SMS conversation exists for this contact (no fork).
    db = SessionLocal()
    try:
        n = db.query(Conversation).filter_by(contact_id=cid, channel="sms").count()
    finally:
        db.close()
    _assert(n == 1, "no fork — one conv", n)

    # Create did NOT send a message.
    db = SessionLocal()
    try:
        msgs = db.query(ConversationMessage).filter_by(conversation_id=conv1).count()
    finally:
        db.close()
    _assert(msgs == 0, "create sends nothing", msgs)
    print("reuse + no fork + no-send ok")


def test_reuse_existing_inbound_thread():
    """A thread the customer already started (inbound) is reused, and its
    inbound history means consent-by-reply holds."""
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, e164 = _make_contact(consent=False)  # no form consent

    # Simulate an existing inbound thread for this number.
    db = SessionLocal()
    try:
        conv = Conversation(
            provider="twilio", channel="sms", external_id=e164,
            status="open", last_inbound_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        db.commit()
        existing_id = conv.id
    finally:
        db.close()

    m1, m2 = _sending_on()
    with m1, m2:
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r.json()["conversation_id"] == existing_id, "reuse inbound thread", r.json())
    _assert(r.json()["created"] is False, "inbound not re-created", r.json())
    # Consent-by-reply: eligible despite no form consent, because inbound exists.
    _assert(r.json()["eligibility"]["eligible"] is True, "inbound → eligible", r.json())
    print("reuse existing inbound thread ok")


def test_send_bypass_blocked():
    """Directly hitting the send endpoint cannot bypass consent."""
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact(consent=False)  # no consent, no inbound

    m1, m2 = _sending_on()
    with m1, m2:
        conv_id = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid}).json()["conversation_id"]
        # Direct send → blocked by the consent guard in _send_sms_reply.
        r = client.post(
            f"/api/inbox/conversations/{conv_id}/messages",
            headers=ah,
            json={"body": "hi", "allow_quiet_hours": True},
        )
    _assert(r.status_code == 409 and r.json()["detail"]["code"] == "recipient_no_sms_consent", "bypass blocked", r.json())
    # No phantom message row.
    db = SessionLocal()
    try:
        n = db.query(ConversationMessage).filter_by(conversation_id=conv_id).count()
    finally:
        db.close()
    _assert(n == 0, "no phantom on blocked send", n)
    print("send bypass blocked ok")


def test_authorization():
    admin_id = _make_user("admin")
    sales_id = _make_user("sales")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    cid, _ = _make_contact()

    # Sales CAN start a conversation.
    m1, m2 = _sending_on()
    with m1, m2:
        r = client.post("/api/inbox/conversations/sms", headers=sh, json={"contact_id": cid})
    _assert(r.status_code == 200, "sales can start", r.status_code)

    # Sales CANNOT list the whole inbox (admin triage stays admin-only).
    r = client.get("/api/inbox/conversations", headers=sh)
    _assert(r.status_code == 403, "sales cannot list inbox", r.status_code)
    r = client.get("/api/inbox/conversations", headers=ah)
    _assert(r.status_code == 200, "admin can list inbox", r.status_code)

    # Unauthenticated start → 401.
    r = client.post("/api/inbox/conversations/sms", json={"contact_id": cid})
    _assert(r.status_code == 401, "unauth start 401", r.status_code)
    print("authorization ok")


def test_event_ownership_guard():
    """A client-supplied event_id belonging to a DIFFERENT contact must NOT be
    linked to this contact's thread (cross-customer context leak guard)."""
    from database.models import Event

    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact()
    other_cid, _ = _make_contact()

    # An event owned by OTHER contact.
    db = SessionLocal()
    try:
        ev = Event(event_type="vehicle_sale", event_name="Foreign deal", status="lead",
                   primary_contact_id=other_cid)
        db.add(ev)
        db.commit()
        foreign_event_id = ev.id
    finally:
        db.close()

    m1, m2 = _sending_on()
    with m1, m2:
        r = client.post(
            "/api/inbox/conversations/sms",
            headers=ah,
            json={"contact_id": cid, "event_id": foreign_event_id},
        )
    conv_id = r.json()["conversation_id"]
    # The foreign event must NOT have been linked.
    db = SessionLocal()
    try:
        conv = db.get(Conversation, conv_id)
        _assert(conv.event_id != foreign_event_id, "foreign event not linked", conv.event_id)
    finally:
        db.close()
    print("event ownership guard ok")


def test_eligibility_optout_beats_consent():
    """A contact who is opted out AND has no consent reports 'opted_out'
    (mirrors the send-path order), not 'no_consent'."""
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact(consent=False, opted_out=True)
    m1, m2 = _sending_on()
    with m1, m2:
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r.json()["eligibility"]["reason"] == "opted_out", "opt-out beats consent", r.json())
    print("eligibility opt-out beats consent ok")


def test_stop_then_start_eligibility():
    """STOP flips eligibility to opted_out; START (opt-in) restores it per the
    consent policy (the contact had form consent)."""
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, e164 = _make_contact(consent=True)  # consented

    db = SessionLocal()
    try:
        contact = db.get(Contact, cid)
        conv, _ = inbox_service.upsert_conversation(
            db, provider="twilio", channel="sms", external_id=e164
        )
        conv.contact_id = cid
        db.flush()
        # STOP.
        inbox_service._apply_opt_out(db, conv, source="sms_keyword")
        db.commit()
        elig_after_stop = inbox_service.sms_eligibility(db, db.get(Contact, cid))
        # START.
        inbox_service._apply_opt_in(db, conv)
        db.commit()
        elig_after_start = inbox_service.sms_eligibility(db, db.get(Contact, cid))
    finally:
        db.close()

    _assert(elig_after_stop["reason"] == "opted_out", "STOP → opted_out", elig_after_stop)
    m1, m2 = _sending_on()
    with m1, m2:
        # Re-evaluate with sending on so the terminal state is 'eligible'.
        db = SessionLocal()
        try:
            elig = inbox_service.sms_eligibility(db, db.get(Contact, cid))
        finally:
            db.close()
    _assert(elig["eligible"] is True, "START restores eligibility", elig)
    print("STOP then START eligibility ok")


def test_disabled_and_transport_reasons():
    """sms_disabled and transport_unavailable eligibility reasons are reachable."""
    admin_id = _make_user("admin")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    cid, _ = _make_contact(consent=True)

    # Sending OFF → sms_disabled.
    with mock.patch("config.settings.SMS_SENDING_ENABLED", False):
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r.json()["eligibility"]["reason"] == "sms_disabled", "sms_disabled", r.json())

    # Sending ON but transport NOT configured → transport_unavailable.
    with mock.patch("config.settings.SMS_SENDING_ENABLED", True), mock.patch(
        "modules.core.services.sms_transport.sms_transport_configured", return_value=False
    ):
        r = client.post("/api/inbox/conversations/sms", headers=ah, json={"contact_id": cid})
    _assert(r.json()["eligibility"]["reason"] == "transport_unavailable", "transport_unavailable", r.json())
    print("disabled + transport reasons ok")


def test_activity_message_feeds():
    """Contact + deal SMS message feeds surface the canonical ConversationMessage
    rows (read-only), scoped correctly, and are auth-gated."""
    admin_id = _make_user("admin")
    sales_id = _make_user("sales")
    ah = {"Authorization": f"Bearer {_token(admin_id, sales=False)}"}
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    cid, e164 = _make_contact(consent=True)

    from database.models import Event

    db = SessionLocal()
    try:
        ev = Event(event_type="vehicle_sale", event_name="Deal", status="lead",
                   primary_contact_id=cid)
        db.add(ev)
        db.flush()
        event_id = ev.id
        conv = Conversation(provider="twilio", channel="sms", external_id=e164,
                            status="open", contact_id=cid, event_id=event_id)
        db.add(conv)
        db.flush()
        # One outbound + one inbound SMS on this thread.
        db.add(ConversationMessage(conversation_id=conv.id, direction="outbound",
                                   channel="sms", provider="twilio", body="hi there",
                                   status="sent", sender_ref="business", recipient_ref=e164))
        db.add(ConversationMessage(conversation_id=conv.id, direction="inbound",
                                   channel="sms", provider="twilio", body="hello back",
                                   status="received", sender_ref=e164, recipient_ref="business"))
        db.commit()
    finally:
        db.close()

    # Contact feed.
    r = client.get(f"/api/inbox/contacts/{cid}/messages", headers=sh)
    _assert(r.status_code == 200, "contact feed 200 (sales)", r.status_code)
    msgs = r.json()["messages"]
    _assert(len(msgs) == 2, "contact feed count", msgs)
    _assert({m["direction"] for m in msgs} == {"outbound", "inbound"}, "both directions", msgs)

    # Deal feed.
    r = client.get(f"/api/inbox/events/{event_id}/messages", headers=ah)
    _assert(r.status_code == 200 and len(r.json()["messages"]) == 2, "deal feed", r.json())

    # Auth-gated.
    r = client.get(f"/api/inbox/contacts/{cid}/messages")
    _assert(r.status_code == 401, "contact feed unauth 401", r.status_code)
    print("activity message feeds ok")


if __name__ == "__main__":
    try:
        test_eligibility_contract()
        test_archived_blocked()
        test_reuse_and_no_fork()
        test_reuse_existing_inbound_thread()
        test_send_bypass_blocked()
        test_authorization()
        test_event_ownership_guard()
        test_eligibility_optout_beats_consent()
        test_stop_then_start_eligibility()
        test_disabled_and_transport_reasons()
        test_activity_message_feeds()
    finally:
        _cleanup()
    print("ALL START-SMS-CONVERSATION SMOKES PASSED")
