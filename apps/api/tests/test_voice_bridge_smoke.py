"""Twilio Voice click-to-call bridge smoke (business-number call path).

The Twilio Calls REST API is MOCKED throughout — this test NEVER places a real
call and never depends on live creds. It exercises the bridge endpoint, the
signed-token TwiML callback, and confirms the native call-attempt logging path
is untouched.

Run as a script (writes/removes its own rows; points at whatever DATABASE_URL
is configured — CI/local use a migrated scratch clone, never prod):
    .venv/bin/python tests/test_voice_bridge_smoke.py

Covers:
  * auth required (unauthenticated bridge → 401)
  * contact not found (404)
  * missing rep callback phone/config (422 when no rep_phone + no fallback)
  * voice not configured → 503 (master switch / creds absent)
  * successful bridge initiation LOGS a call attempt (source=twilio_bridge) and
    returns call_attempt_id + provider_call_sid
  * a Twilio rejection rolls back — no phantom logged attempt survives (502)
  * TwiML callback ONLY dials with a valid signed token; a forged/expired/absent
    token yields empty TwiML (dials nothing), and a valid token dials exactly
    the authorized contact number with the business caller ID
  * native call logging still works (unchanged)
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, ContactCallAttempt, User  # noqa: E402
from modules.messaging.services import voice_transport  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_BIZ_FROM = "+12105550100"
_REP_FALLBACK = "+12105550199"
_user_ids: list[int] = []
_contact_ids: list[int] = []
_phone_seq = [0]


def _assert(cond, label, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _make_user(role: str) -> int:
    db = SessionLocal()
    try:
        u = User(
            username=f"{role}-voice-{_TAG}-{uuid.uuid4().hex[:4]}",
            email=f"{role}-voice-{_TAG}-{uuid.uuid4().hex[:4]}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"Voice {role.title()} {_TAG}",
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


def _make_contact(*, with_phone: bool = True) -> int:
    db = SessionLocal()
    try:
        _phone_seq[0] += 1
        e164 = f"+1210556{_phone_seq[0]:04d}" if with_phone else None
        c = Contact(
            display_name=f"Voice Cust {_TAG}",
            phone=e164,
            phone_e164=e164,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
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
            db.execute(sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"), {"ids": _contact_ids})
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM contact_call_attempts WHERE salesperson_user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": _user_ids})
        db.commit()
    finally:
        db.close()


def _voice_on():
    """Patch context: voice enabled, creds + business number present, and the
    Twilio REST call MOCKED to succeed. Returns (context_managers, mock)."""
    ok = voice_transport.VoiceCallResult(
        ok=True, provider_call_sid=f"CA{_TAG}", status="queued"
    )
    initiate = mock.Mock(return_value=ok)
    ctxs = (
        mock.patch("config.settings.TWILIO_VOICE_ENABLED", True),
        mock.patch("config.settings.TWILIO_ACCOUNT_SID", "AC_test"),
        mock.patch("config.settings.TWILIO_AUTH_TOKEN", "tok_test"),
        mock.patch("config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_FROM),
        mock.patch("config.settings.TWILIO_VOICE_REP_FALLBACK_NUMBER", _REP_FALLBACK),
        mock.patch(
            "modules.messaging.services.voice_transport.initiate_bridge_call",
            initiate,
        ),
    )
    return ctxs, initiate


def test_bridge_auth_required():
    contact_id = _make_contact()
    r = client.post(f"/api/contacts/{contact_id}/call-attempts/bridge", json={})
    _assert(r.status_code == 401, "unauth bridge 401", r.status_code)
    print("bridge auth required ok")


def test_bridge_contact_not_found():
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs, _ = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
        r = client.post("/api/contacts/999999999/call-attempts/bridge", headers=sh, json={})
    _assert(r.status_code == 404, "missing contact 404", r.status_code)
    print("bridge contact-not-found ok")


def test_bridge_missing_rep_phone():
    """No rep_phone in the body AND no fallback configured → 422."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    with mock.patch("config.settings.TWILIO_VOICE_ENABLED", True), mock.patch(
        "config.settings.TWILIO_ACCOUNT_SID", "AC_test"
    ), mock.patch("config.settings.TWILIO_AUTH_TOKEN", "tok_test"), mock.patch(
        "config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_FROM
    ), mock.patch(
        "config.settings.TWILIO_VOICE_REP_FALLBACK_NUMBER", None
    ):
        r = client.post(f"/api/contacts/{contact_id}/call-attempts/bridge", headers=sh, json={})
    _assert(r.status_code == 422, "missing rep phone 422", (r.status_code, r.text))
    print("bridge missing-rep-phone ok")


def test_bridge_voice_not_configured():
    """Master switch off (or creds absent) → 503, and NOTHING is logged."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    with mock.patch("config.settings.TWILIO_VOICE_ENABLED", False), mock.patch(
        "config.settings.TWILIO_VOICE_REP_FALLBACK_NUMBER", _REP_FALLBACK
    ):
        r = client.post(f"/api/contacts/{contact_id}/call-attempts/bridge", headers=sh, json={})
    _assert(r.status_code == 503, "voice not configured 503", (r.status_code, r.text))
    db = SessionLocal()
    try:
        n = db.query(ContactCallAttempt).filter_by(contact_id=contact_id).count()
    finally:
        db.close()
    _assert(n == 0, "nothing logged when unconfigured", n)
    print("bridge voice-not-configured ok")


def test_bridge_success_logs_attempt():
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs, initiate = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
        r = client.post(
            f"/api/contacts/{contact_id}/call-attempts/bridge",
            headers=sh,
            json={"rep_phone": "+12105557788"},
        )
    _assert(r.status_code == 201, "bridge 201", (r.status_code, r.text))
    body = r.json()
    _assert(body["call_attempt_id"] > 0, "returns call_attempt_id", body)
    _assert(body["provider_call_sid"] == f"CA{_TAG}", "returns provider sid", body)
    _assert(body["source"] == "twilio_bridge", "source tagged", body)
    _assert(body["salesperson_user_id"] == sales_id, "attributed to token user", body)

    # Twilio was asked to ring the REP first (To = rep number).
    _assert(initiate.call_count == 1, "initiate called once", initiate.call_count)
    kwargs = initiate.call_args.kwargs
    _assert(kwargs["rep_number"] == "+12105557788", "rings rep number", kwargs)
    _assert("token=" in kwargs["callback_url"], "callback carries token", kwargs)

    # An attempt row persists (managers see it alongside native calls).
    db = SessionLocal()
    try:
        row = db.query(ContactCallAttempt).filter_by(contact_id=contact_id).one()
        _assert(row.source == "twilio_bridge", "row source persisted", row.source)
        _assert(row.outcome == "call_initiated", "row born initiated", row.outcome)
    finally:
        db.close()
    print("bridge success logs attempt ok")


def test_bridge_uses_fallback_number():
    """Omitting rep_phone falls back to TWILIO_VOICE_REP_FALLBACK_NUMBER."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs, initiate = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
        r = client.post(f"/api/contacts/{contact_id}/call-attempts/bridge", headers=sh, json={})
    _assert(r.status_code == 201, "fallback bridge 201", (r.status_code, r.text))
    _assert(
        initiate.call_args.kwargs["rep_number"] == _REP_FALLBACK,
        "used fallback rep number",
        initiate.call_args.kwargs,
    )
    print("bridge uses fallback number ok")


def test_bridge_provider_failure_rolls_back():
    """Twilio rejects the outbound call → 502 and NO phantom attempt survives."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    fail = voice_transport.VoiceCallResult(
        ok=False, error_code="21205", error_message="Url is not a valid URL"
    )
    with mock.patch("config.settings.TWILIO_VOICE_ENABLED", True), mock.patch(
        "config.settings.TWILIO_ACCOUNT_SID", "AC_test"
    ), mock.patch("config.settings.TWILIO_AUTH_TOKEN", "tok_test"), mock.patch(
        "config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_FROM
    ), mock.patch(
        "config.settings.TWILIO_VOICE_REP_FALLBACK_NUMBER", _REP_FALLBACK
    ), mock.patch(
        "modules.messaging.services.voice_transport.initiate_bridge_call",
        return_value=fail,
    ):
        r = client.post(f"/api/contacts/{contact_id}/call-attempts/bridge", headers=sh, json={})
    _assert(r.status_code == 502, "provider failure 502", (r.status_code, r.text))
    _assert(
        (r.json().get("detail") or {}).get("code") == "bridge_call_failed",
        "failure code surfaced",
        r.json(),
    )
    db = SessionLocal()
    try:
        n = db.query(ContactCallAttempt).filter_by(contact_id=contact_id).count()
    finally:
        db.close()
    _assert(n == 0, "no phantom attempt after rollback", n)
    print("bridge provider-failure rolls back ok")


def test_bridge_missing_contact_phone():
    """A contact with no phone → 422 contact_phone_missing (before dialing)."""
    contact_id = _make_contact(with_phone=False)
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs, initiate = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
        r = client.post(
            f"/api/contacts/{contact_id}/call-attempts/bridge",
            headers=sh,
            json={"rep_phone": "+12105557788"},
        )
    _assert(r.status_code == 422, "missing contact phone 422", (r.status_code, r.text))
    _assert(initiate.call_count == 0, "never dialed without contact phone", initiate.call_count)
    print("bridge missing-contact-phone ok")


def test_twiml_callback_token_gate():
    """The TwiML callback dials ONLY with a valid signed token; a forged /
    absent token yields empty TwiML (dials nothing). Signature verification is
    bypassed here (INBOUND_SMS_REQUIRE_SIGNATURE off) to isolate the token gate.
    """
    dial = "+12105550123"
    good = voice_transport.mint_bridge_token(
        call_attempt_id=1, contact_id=2, dial_number=dial
    )
    with mock.patch("config.settings.INBOUND_SMS_REQUIRE_SIGNATURE", False), mock.patch(
        "config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_FROM
    ):
        # Valid token → dials the authorized number with business caller ID.
        r = client.post(f"/api/webhooks/twilio/voice/bridge?token={good}")
        _assert(r.status_code == 200, "twiml 200", r.status_code)
        _assert("<Dial" in r.text and dial in r.text, "dials authorized number", r.text)
        _assert(f'callerId="{_BIZ_FROM}"' in r.text, "business caller id", r.text)

        # Forged token → empty response, dials NOTHING.
        r = client.post("/api/webhooks/twilio/voice/bridge?token=not-a-real-token")
        _assert(r.status_code == 200, "forged twiml 200", r.status_code)
        _assert("<Dial" not in r.text, "forged token dials nothing", r.text)

        # Absent token → empty response.
        r = client.post("/api/webhooks/twilio/voice/bridge")
        _assert("<Dial" not in r.text, "absent token dials nothing", r.text)
    print("twiml callback token gate ok")


def test_twiml_callback_rejects_bad_signature():
    """With signature verification REQUIRED, a request without a valid
    X-Twilio-Signature dials nothing even if the token is valid."""
    good = voice_transport.mint_bridge_token(
        call_attempt_id=1, contact_id=2, dial_number="+12105550123"
    )
    with mock.patch("config.settings.INBOUND_SMS_REQUIRE_SIGNATURE", True), mock.patch(
        "config.settings.TWILIO_AUTH_TOKEN", "tok_test"
    ):
        r = client.post(f"/api/webhooks/twilio/voice/bridge?token={good}")
    _assert(r.status_code == 200, "bad-sig twiml 200", r.status_code)
    _assert("<Dial" not in r.text, "unsigned request dials nothing", r.text)
    print("twiml callback rejects bad signature ok")


def test_native_call_logging_still_works():
    """The pre-existing native tel: call-attempt logging path is unchanged."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "source": "contact_detail"},
    )
    _assert(r.status_code == 201, "native create 201", (r.status_code, r.text))
    _assert(r.json()["outcome"] == "call_initiated", "native born initiated", r.json())
    _assert(r.json()["source"] == "contact_detail", "native source preserved", r.json())
    print("native call logging still works ok")


if __name__ == "__main__":
    try:
        test_bridge_auth_required()
        test_bridge_contact_not_found()
        test_bridge_missing_rep_phone()
        test_bridge_voice_not_configured()
        test_bridge_success_logs_attempt()
        test_bridge_uses_fallback_number()
        test_bridge_provider_failure_rolls_back()
        test_bridge_missing_contact_phone()
        test_twiml_callback_token_gate()
        test_twiml_callback_rejects_bad_signature()
        test_native_call_logging_still_works()
    finally:
        _cleanup()
    print("ALL VOICE BRIDGE SMOKES PASSED")
