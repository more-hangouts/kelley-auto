"""Smoke tests for the storefront web chat (migrations 094 + 097).

End-to-end over the public endpoints + the admin inbox service:
  - GET /script serves a valid tree,
  - honeypot / instant-submit on /start returns a fake session, writes nothing,
  - a real /start creates contact + conversation + intake message,
  - a repeat /start within 24h REUSES the thread (created=False, one alert),
  - a scripted answer writes the tap + the canned reply,
  - free text escalates: mints ONE vehicle_sale deal (idempotent on repeat),
  - staff reply via inbox_service.send_reply lands as an outbound 'sent' row
    with NO transport, and the visitor's cursor poll picks it up,
  - SMS threads still raise sms_sending_disabled,
  - poll with a bogus session id 404s (path pattern) and unknown session 404s.

Run as a script (writes/removes its own rows):
    .venv/bin/python tests/test_web_chat_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # lets cleanup delete its deals
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from modules.messaging.services import inbox_service  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_EMAIL = f"webchat-{_TAG}@example.com"
_UA = {
    "user-agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148"
    )
}

_state: dict = {}


def _assert(cond: bool, label: str, detail: object = "") -> None:
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _cleanup() -> None:
    """Remove EVERY artifact of this (and any prior crashed) smoke run,
    keyed on the ``webchat-*@example.com`` contact pattern. Notification
    jobs go first — the live backend's worker would otherwise email real
    staff about the smoke chat."""
    db = SessionLocal()
    try:
        contact_ids = [
            int(r[0])
            for r in db.execute(
                sql_text(
                    "SELECT id FROM contacts WHERE lower(email) LIKE "
                    "'webchat-%@example.com'"
                )
            ).all()
        ]
        for contact_id in contact_ids:
            conv_ids = [
                int(r[0])
                for r in db.execute(
                    sql_text("SELECT id FROM conversations WHERE contact_id = :c"),
                    {"c": contact_id},
                ).all()
            ]
            if conv_ids:
                for table in ("notification_jobs", "staff_notification_events"):
                    db.execute(
                        sql_text(
                            f"DELETE FROM {table} WHERE subject_kind = 'conversation' "
                            "AND subject_id = ANY(:ids)"
                        ),
                        {"ids": conv_ids},
                    )
                db.execute(
                    sql_text("DELETE FROM conversations WHERE id = ANY(:ids)"),
                    {"ids": conv_ids},
                )
            db.execute(
                sql_text(
                    "DELETE FROM storefront_events WHERE metadata->>'crm_event_id' IN "
                    "(SELECT id::text FROM events WHERE primary_contact_id = :c)"
                ),
                {"c": contact_id},
            )
            for table, col in (
                ("activity_log", "event_id"),
                ("lead_attribution", "event_id"),
            ):
                db.execute(
                    sql_text(
                        f"DELETE FROM {table} WHERE {col} IN "
                        "(SELECT id FROM events WHERE primary_contact_id = :c)"
                    ),
                    {"c": contact_id},
                )
            db.execute(
                sql_text("DELETE FROM events WHERE primary_contact_id = :c"),
                {"c": contact_id},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :c"), {"c": contact_id}
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def test_script() -> None:
    r = client.get("/api/web-chat/script")
    _assert(r.status_code == 200, "script 200", r.text)
    script = r.json()["script"]
    _assert(script.get("questions"), "script has questions")
    _state["script"] = script
    print("script ok")


def test_honeypot() -> None:
    r = client.post(
        "/api/web-chat/start",
        json={
            "email": f"bot-{_TAG}@example.com",
            "company_website": "http://spam.example",
            "elapsed_ms": 99999,
        },
        headers=_UA,
    )
    _assert(r.status_code == 200, "honeypot ack", r.text)
    sid = r.json()["session_id"]
    # The fake session doesn't exist — follow-ups 404 like an expired one.
    r2 = client.get(f"/api/web-chat/{sid}/messages", headers=_UA)
    _assert(r2.status_code == 404, "fake session 404s", r2.status_code)
    db = SessionLocal()
    try:
        n = db.execute(
            sql_text("SELECT COUNT(*) FROM contacts WHERE lower(email) = :e"),
            {"e": f"bot-{_TAG}@example.com"},
        ).scalar()
        _assert(int(n) == 0, "honeypot wrote nothing", n)
    finally:
        db.close()

    # Instant submit (elapsed_ms below the human floor) is also caught.
    r3 = client.post(
        "/api/web-chat/start",
        json={"email": f"bot2-{_TAG}@example.com", "elapsed_ms": 200},
        headers=_UA,
    )
    _assert(r3.status_code == 200, "instant-submit ack", r3.text)
    print("honeypot ok")


def test_start_and_reuse() -> None:
    payload = {
        "name": "Web Chat Smoke",
        "email": _EMAIL,
        "sms_opt_in": True,
        "page_url": "https://kelleyautoplex.com/inventory/KAP-00001",
        "intake": [
            {"question": "What can we help you with?", "answer": "I'm looking for a car"}
        ],
        "script_version": _state["script"].get("version"),
        "elapsed_ms": 4200,
    }
    r = client.post("/api/web-chat/start", json=payload, headers=_UA)
    _assert(r.status_code == 200, "start 200", r.text)
    data = r.json()
    _assert(data["created"] is True, "first start creates", data)
    _assert(data["messages"], "intake message present", data)
    _state["sid"] = data["session_id"]
    _state["last_id"] = max(m["id"] for m in data["messages"])

    r2 = client.post("/api/web-chat/start", json=payload, headers=_UA)
    _assert(r2.status_code == 200, "restart 200", r2.text)
    _assert(r2.json()["created"] is False, "24h window reuses thread", r2.json())
    _assert(
        r2.json()["session_id"] == _state["sid"], "same session id", r2.json()
    )
    print("start + reuse ok")


def test_scripted_answer() -> None:
    script = _state["script"]
    q = next(qq for qq in script["questions"] if qq["id"] == script.get("root", "start"))
    opt = next(o for o in q["options"] if o.get("answer"))
    r = client.post(
        f"/api/web-chat/{_state['sid']}/answer",
        json={"question_id": q["id"], "option_id": opt["id"]},
        headers=_UA,
    )
    _assert(r.status_code == 200, "answer 200", r.text)
    msgs = r.json()["messages"]
    kinds = [m["kind"] for m in msgs]
    _assert("visitor" in kinds and "auto" in kinds, "tap + canned reply", kinds)

    bad = client.post(
        f"/api/web-chat/{_state['sid']}/answer",
        json={"question_id": q["id"], "option_id": "nope"},
        headers=_UA,
    )
    _assert(bad.status_code == 422, "unknown option rejected", bad.status_code)
    print("scripted answer ok")


def test_escalation_once_only() -> None:
    r = client.post(
        f"/api/web-chat/{_state['sid']}/message",
        json={"body": "Do you have anything under $8k with third-row seats?"},
        headers=_UA,
    )
    _assert(r.status_code == 200, "message 200", r.text)
    r2 = client.post(
        f"/api/web-chat/{_state['sid']}/message",
        json={"body": "Also — do you take trades?"},
        headers=_UA,
    )
    _assert(r2.status_code == 200, "second message 200", r2.text)
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT c.event_id, c.visitor_sms_opt_in, ct.sms_consent_at IS NOT NULL "
                "FROM conversations c JOIN contacts ct ON ct.id = c.contact_id "
                "WHERE c.external_id = :sid"
            ),
            {"sid": _state["sid"]},
        ).first()
        _assert(row is not None and row[0] is not None, "deal linked", row)
        _assert(bool(row[1]) and bool(row[2]), "sms consent captured", tuple(row))
        deal_count = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM events WHERE primary_contact_id = "
                "(SELECT contact_id FROM conversations WHERE external_id = :sid)"
            ),
            {"sid": _state["sid"]},
        ).scalar()
        _assert(int(deal_count) == 1, "exactly one deal (once-only guard)", deal_count)
        _state["conversation_id"] = db.execute(
            sql_text("SELECT id FROM conversations WHERE external_id = :sid"),
            {"sid": _state["sid"]},
        ).scalar()
    finally:
        db.close()
    print("escalation once-only ok")


def test_staff_reply_and_poll() -> None:
    db = SessionLocal()
    try:
        admin_id = db.execute(
            sql_text("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        ).scalar()
        result = inbox_service.send_reply(
            db,
            int(_state["conversation_id"]),
            body="We do! Come by any time — ask for the lot manager.",
            user_id=int(admin_id),
        )
        db.commit()
        _assert(result["message"]["status"] == "sent", "reply sent w/o transport", result)
        _assert(
            result["conversation"]["status"] == "pending", "answered → pending", result
        )

        # SMS threads must still be gated.
        sms_conv = db.execute(
            sql_text("SELECT id FROM conversations WHERE channel = 'sms' LIMIT 1")
        ).scalar()
        if sms_conv:
            try:
                inbox_service.send_reply(
                    db, int(sms_conv), body="x", user_id=int(admin_id)
                )
                raise AssertionError("SMS reply should have raised")
            except inbox_service.InboxError as exc:
                _assert(exc.code == "sms_sending_disabled", "sms still gated", exc.code)
            db.rollback()
    finally:
        db.close()

    r = client.get(
        f"/api/web-chat/{_state['sid']}/messages?after_id={_state['last_id']}",
        headers=_UA,
    )
    _assert(r.status_code == 200, "poll 200", r.text)
    bodies = [m["body"] for m in r.json()["messages"] if m["kind"] == "staff"]
    _assert(any("lot manager" in (b or "") for b in bodies), "visitor sees reply", bodies)
    print("staff reply + poll ok")


def test_bad_sessions() -> None:
    r = client.get("/api/web-chat/wc_not-a-real-session/messages", headers=_UA)
    _assert(r.status_code in (404, 422), "malformed session rejected", r.status_code)
    r2 = client.get(f"/api/web-chat/wc_{uuid.uuid4().hex}/messages", headers=_UA)
    _assert(r2.status_code == 404, "unknown session 404", r2.status_code)
    print("bad sessions ok")


if __name__ == "__main__":
    try:
        test_script()
        test_honeypot()
        test_start_and_reuse()
        test_scripted_answer()
        test_escalation_once_only()
        test_staff_reply_and_poll()
        test_bad_sessions()
    finally:
        _cleanup()
    print("ALL WEB CHAT SMOKES PASSED")
