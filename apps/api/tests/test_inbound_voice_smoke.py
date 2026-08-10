"""Inbound voice smoke (phase 1: forward to the published office line).

NO real Twilio traffic: requests are locally signed with a fake auth token and
posted straight at the webhook. Nothing places or receives an actual call.

Run as a script (writes/removes its own rows; points at whatever DATABASE_URL
is configured — CI/local use a migrated scratch clone, never prod):
    .venv/bin/python tests/test_inbound_voice_smoke.py

Covers:
  * a bad/absent Twilio signature routes NOTHING and logs NOTHING
  * a signed call is logged (from/to/sid/city/state) and forwarded to the
    configured office number, with the fallback message after the dial
  * caller ID is NOT overridden — whoever answers sees the CUSTOMER's number
  * the flag OFF still LOGS the call but speaks an apology instead of routing
  * a missing forward number is treated as unconfigured (never dials nowhere)
  * webhook retries are idempotent on CallSid (one row, not two)
  * a known caller is soft-linked to their contact; a stranger logs with NULL
  * status callbacks map Twilio's vocabulary and record duration
  * an unknown status / unknown CallSid is ignored rather than 500ing
  * the outbound softphone + bridge routes are unaffected
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
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, InboundCall  # noqa: E402
from modules.messaging.services.twilio_signature import compute_signature  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_BASE = "https://api.kelleyautoplex.com"
_INBOUND_PATH = "/api/webhooks/twilio/voice/inbound"
_STATUS_PATH = "/api/webhooks/twilio/voice/status"
_AUTH_TOKEN = "tok_test"
_BIZ_NUMBER = "+18302689308"
_OFFICE = "+12102513644"

_contact_ids: list[int] = []
_call_sids: list[str] = []
_phone_seq = [0]


def _assert(cond, label, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _sid() -> str:
    s = f"CA{_TAG}{uuid.uuid4().hex[:14]}"
    _call_sids.append(s)
    return s


def _make_contact() -> tuple[int, str]:
    db = SessionLocal()
    try:
        _phone_seq[0] += 1
        e164 = f"+1210558{_phone_seq[0]:04d}"
        c = Contact(display_name=f"Inbound Caller {_TAG}", phone=e164, phone_e164=e164)
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id, e164
    finally:
        db.close()


def _cleanup():
    db = SessionLocal()
    try:
        if _call_sids:
            db.execute(
                sql_text("DELETE FROM inbound_calls WHERE provider_call_sid = ANY(:s)"),
                {"s": _call_sids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM inbound_calls WHERE contact_id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"), {"ids": _contact_ids}
            )
        db.commit()
    finally:
        db.close()


def _inbound_on(*, forward=_OFFICE, enabled=True):
    return (
        mock.patch("config.settings.TWILIO_INBOUND_VOICE_ENABLED", enabled),
        mock.patch("config.settings.TWILIO_INBOUND_FORWARD_NUMBER", forward),
        mock.patch("config.settings.TWILIO_AUTH_TOKEN", _AUTH_TOKEN),
        mock.patch("config.settings.PUBLIC_API_BASE_URL", _BASE),
        mock.patch("config.settings.INBOUND_SMS_REQUIRE_SIGNATURE", True),
    )


def _post(path: str, form: dict, *, sign: bool = True):
    headers = {
        "X-Twilio-Signature": (
            compute_signature(_AUTH_TOKEN, f"{_BASE}{path}", form)
            if sign
            else "forged-signature"
        )
    }
    return client.post(path, headers=headers, data=form)


def _load(call_sid: str) -> InboundCall | None:
    db = SessionLocal()
    try:
        return (
            db.query(InboundCall)
            .filter(InboundCall.provider_call_sid == call_sid)
            .one_or_none()
        )
    finally:
        db.close()


def _count(call_sid: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(InboundCall)
            .filter(InboundCall.provider_call_sid == call_sid)
            .count()
        )
    finally:
        db.close()


# --- signature gate --------------------------------------------------------


def test_bad_signature_routes_and_logs_nothing():
    sid = _sid()
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        r = _post(
            _INBOUND_PATH,
            {"CallSid": sid, "From": "+12105551000", "To": _BIZ_NUMBER},
            sign=False,
        )
    _assert(r.status_code == 200, "bad sig 200", r.status_code)
    _assert("<Dial" not in r.text, "bad sig does not route", r.text)
    _assert(_OFFICE not in r.text, "bad sig leaks no office number", r.text)
    _assert(_count(sid) == 0, "bad sig logs nothing")
    print("inbound bad signature → no routing, no log ok")


# --- happy path ------------------------------------------------------------


def test_signed_call_is_logged_and_forwarded():
    sid = _sid()
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        r = _post(
            _INBOUND_PATH,
            {
                "CallSid": sid,
                "From": "+12105551001",
                "To": _BIZ_NUMBER,
                "FromCity": "SAN ANTONIO",
                "FromState": "TX",
            },
        )
    _assert(r.status_code == 200, "inbound 200", r.status_code)
    _assert(f"<Number>{_OFFICE}</Number>" in r.text, "forwards to office", r.text)
    _assert("<Dial" in r.text and 'timeout="25"' in r.text, "dial with timeout", r.text)
    # The fallback message must come AFTER </Dial> so Twilio falls through to it
    # only when the office does not answer.
    _assert(
        r.text.index("</Dial>") < r.text.index("<Say"), "say follows dial", r.text
    )
    # Caller ID deliberately NOT overridden — the shop sees the customer.
    _assert("callerId" not in r.text, "caller id not overridden", r.text)

    call = _load(sid)
    _assert(call is not None, "call logged")
    _assert(call.from_number == "+12105551001", "from recorded", call.from_number)
    _assert(call.to_number == _BIZ_NUMBER, "to recorded", call.to_number)
    _assert(call.status == "received", "born received", call.status)
    _assert(call.disposition == "forwarded", "disposition forwarded", call.disposition)
    _assert(call.forwarded_to == _OFFICE, "forward target snapshotted", call.forwarded_to)
    _assert(call.caller_city == "SAN ANTONIO", "city recorded", call.caller_city)
    _assert(call.caller_state == "TX", "state recorded", call.caller_state)
    print("inbound signed call logged + forwarded ok")


def test_known_caller_links_to_contact_stranger_does_not():
    contact_id, phone = _make_contact()
    ctxs = _inbound_on()
    known_sid, stranger_sid = _sid(), _sid()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        _post(_INBOUND_PATH, {"CallSid": known_sid, "From": phone, "To": _BIZ_NUMBER})
        _post(
            _INBOUND_PATH,
            {"CallSid": stranger_sid, "From": "+12105559123", "To": _BIZ_NUMBER},
        )
    _assert(_load(known_sid).contact_id == contact_id, "known caller linked")
    _assert(_load(stranger_sid).contact_id is None, "stranger logs unlinked")
    print("inbound contact matching ok")


def test_webhook_retry_is_idempotent():
    """Twilio retries deliver the same CallSid — one row, not two."""
    sid = _sid()
    ctxs = _inbound_on()
    form = {"CallSid": sid, "From": "+12105551002", "To": _BIZ_NUMBER}
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        a = _post(_INBOUND_PATH, form)
        b = _post(_INBOUND_PATH, form)
    _assert(a.status_code == 200 and b.status_code == 200, "both 200")
    _assert(a.text == b.text, "retry returns same routing", (a.text, b.text))
    _assert(_count(sid) == 1, "retry did not duplicate", _count(sid))
    print("inbound retry idempotent ok")


# --- flag off / misconfigured ----------------------------------------------


def test_flag_off_still_logs_but_speaks_instead_of_routing():
    sid = _sid()
    ctxs = _inbound_on(enabled=False)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        r = _post(
            _INBOUND_PATH, {"CallSid": sid, "From": "+12105551003", "To": _BIZ_NUMBER}
        )
    _assert(r.status_code == 200, "flag off 200", r.status_code)
    _assert("<Say" in r.text, "speaks an apology", r.text)
    _assert("<Dial" not in r.text, "does not route", r.text)
    _assert(_OFFICE not in r.text, "leaks no office number", r.text)
    call = _load(sid)
    _assert(call is not None, "still logged with flag off")
    _assert(call.disposition == "rejected", "disposition rejected", call.disposition)
    _assert(call.forwarded_to is None, "no forward target", call.forwarded_to)
    print("inbound flag-off logs + speaks ok")


def test_missing_forward_number_is_unconfigured():
    """Flag on but no destination → never dial nowhere; speak instead."""
    sid = _sid()
    ctxs = _inbound_on(forward=None)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        r = _post(
            _INBOUND_PATH, {"CallSid": sid, "From": "+12105551004", "To": _BIZ_NUMBER}
        )
    _assert("<Dial" not in r.text, "no dial without a number", r.text)
    _assert("<Say" in r.text, "speaks instead", r.text)
    _assert(_load(sid).disposition == "rejected", "logged as rejected")
    print("inbound missing forward number ok")


# --- status callbacks ------------------------------------------------------


def test_status_callback_records_outcome_and_duration():
    sid = _sid()
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551005", "To": _BIZ_NUMBER})
        r = _post(
            _STATUS_PATH,
            {"CallSid": sid, "CallStatus": "completed", "CallDuration": "137"},
        )
    _assert(r.status_code == 204, "status 204", r.status_code)
    call = _load(sid)
    _assert(call.status == "completed", "status recorded", call.status)
    _assert(call.duration_seconds == 137, "duration recorded", call.duration_seconds)
    print("inbound status callback ok")


def test_status_callback_maps_twilio_vocabulary():
    ctxs = _inbound_on()
    cases = [("no-answer", "no_answer"), ("busy", "busy"), ("in-progress", "in_progress")]
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        for raw, expected in cases:
            sid = _sid()
            _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551006", "To": _BIZ_NUMBER})
            _post(_STATUS_PATH, {"CallSid": sid, "CallStatus": raw})
            _assert(_load(sid).status == expected, f"{raw} → {expected}", _load(sid).status)
    print("inbound status vocabulary mapping ok")


def test_status_callback_tolerates_unknown_sid_and_status():
    """Neither an unknown call nor an unrecognised status may 500 or corrupt
    the row — an unknown status must not violate the CHECK constraint."""
    sid = _sid()
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        r = _post(_STATUS_PATH, {"CallSid": "CAneverseen", "CallStatus": "completed"})
        _assert(r.status_code == 204, "unknown sid 204", r.status_code)

        _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551007", "To": _BIZ_NUMBER})
        r = _post(_STATUS_PATH, {"CallSid": sid, "CallStatus": "teleported"})
        _assert(r.status_code == 204, "unknown status 204", r.status_code)
        _assert(_load(sid).status == "received", "unknown status ignored", _load(sid).status)

        r = _post(_STATUS_PATH, {"CallSid": sid, "CallStatus": "completed", "CallDuration": "abc"})
        _assert(r.status_code == 204, "bad duration 204", r.status_code)
        _assert(_load(sid).duration_seconds is None, "bad duration ignored")
    print("inbound status callback tolerates junk ok")


def test_status_callback_rejects_bad_signature():
    sid = _sid()
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551008", "To": _BIZ_NUMBER})
        r = _post(
            _STATUS_PATH, {"CallSid": sid, "CallStatus": "completed"}, sign=False
        )
    _assert(r.status_code == 204, "bad sig 204", r.status_code)
    _assert(_load(sid).status == "received", "forged status not applied", _load(sid).status)
    print("inbound status callback rejects bad signature ok")


# --- no collateral damage --------------------------------------------------


def test_outbound_paths_unaffected():
    """Inbound routing must not disturb the softphone/bridge TwiML routes."""
    from modules.messaging.services import voice_transport

    dial = "+12105554321"
    token = voice_transport.mint_dial_token(
        call_attempt_id=1, contact_id=2, user_id=3, dial_number=dial
    )
    ctxs = _inbound_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], mock.patch(
        "config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_NUMBER
    ):
        r = _post(
            "/api/webhooks/twilio/voice/outbound",
            {"DialToken": token, "CallSid": "CAoutbound"},
        )
    _assert(r.status_code == 200, "outbound still 200", r.status_code)
    _assert(dial in r.text, "outbound still dials its own token target", r.text)
    print("outbound paths unaffected ok")


if __name__ == "__main__":
    try:
        test_bad_signature_routes_and_logs_nothing()
        test_signed_call_is_logged_and_forwarded()
        test_known_caller_links_to_contact_stranger_does_not()
        test_webhook_retry_is_idempotent()
        test_flag_off_still_logs_but_speaks_instead_of_routing()
        test_missing_forward_number_is_unconfigured()
        test_status_callback_records_outcome_and_duration()
        test_status_callback_maps_twilio_vocabulary()
        test_status_callback_tolerates_unknown_sid_and_status()
        test_status_callback_rejects_bad_signature()
        test_outbound_paths_unaffected()
    finally:
        _cleanup()
    print("ALL INBOUND VOICE SMOKES PASSED")
