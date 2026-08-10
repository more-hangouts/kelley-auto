"""Browser softphone smoke (call from the dashboard, audio in the tab).

NO real Twilio traffic: AccessTokens are signed with a fake API Key pair and
verified locally, and the browser — not the server — is what would place a
call, so there is no REST endpoint to mock at all.

Run as a script (writes/removes its own rows; points at whatever DATABASE_URL
is configured — CI/local use a migrated scratch clone, never prod):
    .venv/bin/python tests/test_softphone_smoke.py

Covers:
  * /api/voice/status + /api/voice/token require auth (401)
  * token 503 when the softphone is off or creds are absent
  * minted AccessToken: Twilio's JWT shape, identity derived from the
    AUTHENTICATED user (never the body), OUTGOING-only grant (no incoming)
  * browser call-attempt authorizes + logs (source=twilio_softphone) and hands
    back a dial token; unconfigured → 503 with nothing logged
  * contact without a phone → 422
  * TwiML outbound route dials ONLY what a valid dial token authorizes: absent,
    forged, expired, and wrong-purpose (bridge) tokens all dial nothing
  * a bad Twilio signature dials nothing
  * the bridge and native paths are untouched by any of this
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, ContactCallAttempt, User  # noqa: E402
from modules.messaging.services import voice_transport  # noqa: E402
from modules.messaging.services.twilio_signature import compute_signature  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_BIZ_FROM = "+12105550100"
_API_KEY_SID = "SK00000000000000000000000000000000"
_API_KEY_SECRET = "smoke-secret-not-real"
_APP_SID = "AP00000000000000000000000000000000"
_ACCOUNT_SID = "AC00000000000000000000000000000000"
_OUTBOUND_PATH = "/api/webhooks/twilio/voice/outbound"

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
            username=f"{role}-sp-{_TAG}-{uuid.uuid4().hex[:4]}",
            email=f"{role}-sp-{_TAG}-{uuid.uuid4().hex[:4]}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"Softphone {role.title()} {_TAG}",
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
        e164 = f"+1210557{_phone_seq[0]:04d}" if with_phone else None
        c = Contact(
            display_name=f"Softphone Cust {_TAG}",
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


def _softphone_on():
    """Patch context: softphone enabled with a fake API Key pair + TwiML App."""
    return (
        mock.patch("config.settings.TWILIO_SOFTPHONE_ENABLED", True),
        mock.patch("config.settings.TWILIO_ACCOUNT_SID", _ACCOUNT_SID),
        mock.patch("config.settings.TWILIO_AUTH_TOKEN", "tok_test"),
        mock.patch("config.settings.TWILIO_API_KEY_SID", _API_KEY_SID),
        mock.patch("config.settings.TWILIO_API_KEY_SECRET", _API_KEY_SECRET),
        mock.patch("config.settings.TWILIO_TWIML_APP_SID", _APP_SID),
        mock.patch("config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ_FROM),
    )


def _attempt_count(contact_id: int) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ContactCallAttempt)
            .filter(ContactCallAttempt.contact_id == contact_id)
            .count()
        )
    finally:
        db.close()


# --- auth ------------------------------------------------------------------


def test_voice_endpoints_require_auth():
    r = client.post("/api/voice/token")
    _assert(r.status_code == 401, "unauth token 401", r.status_code)
    r = client.get("/api/voice/status")
    _assert(r.status_code == 401, "unauth status 401", r.status_code)
    contact_id = _make_contact()
    r = client.post(f"/api/contacts/{contact_id}/call-attempts/browser", json={})
    _assert(r.status_code == 401, "unauth browser call 401", r.status_code)
    print("softphone auth required ok")


# --- access token ----------------------------------------------------------


def test_token_503_when_disabled():
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    with mock.patch("config.settings.TWILIO_SOFTPHONE_ENABLED", False):
        r = client.post("/api/voice/token", headers=sh)
        _assert(r.status_code == 503, "disabled token 503", (r.status_code, r.text))
        s = client.get("/api/voice/status", headers=sh)
        _assert(s.json()["enabled"] is False, "status reports disabled", s.json())
        _assert(s.json()["reason"] == "disabled", "status reason", s.json())
    print("token disabled → 503 ok")


def test_token_503_when_api_key_missing():
    """Switch on but no API Key pair → still refuses (can't sign a token)."""
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[5], ctxs[6], mock.patch(
        "config.settings.TWILIO_API_KEY_SID", None
    ), mock.patch("config.settings.TWILIO_API_KEY_SECRET", None):
        r = client.post("/api/voice/token", headers=sh)
        _assert(r.status_code == 503, "no api key 503", (r.status_code, r.text))
        s = client.get("/api/voice/status", headers=sh)
        _assert(s.json()["reason"] == "missing_api_key", "status reason", s.json())
    print("token missing-api-key → 503 ok")


def test_token_shape_and_grants():
    """The minted token is a Twilio AccessToken: signed by the API Key SECRET,
    issued by the API Key SID, scoped to the account, OUTGOING-only."""
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
        r = client.post("/api/voice/token", headers=sh)
    _assert(r.status_code == 200, "token 200", (r.status_code, r.text))
    body = r.json()
    _assert(body["from_number"] == _BIZ_FROM, "from_number surfaced", body)
    _assert(body["identity"] == f"user{sales_id}", "identity from token", body)

    hdr = jwt.get_unverified_header(body["token"])
    _assert(hdr.get("cty") == "twilio-fpa;v=1", "twilio content type", hdr)

    claims = jwt.decode(body["token"], _API_KEY_SECRET, algorithms=["HS256"])
    _assert(claims["iss"] == _API_KEY_SID, "issued by api key", claims)
    _assert(claims["sub"] == _ACCOUNT_SID, "scoped to account", claims)
    grants = claims["grants"]
    _assert(grants["identity"] == f"user{sales_id}", "grant identity", grants)
    _assert(
        grants["voice"]["outgoing"]["application_sid"] == _APP_SID,
        "outgoing app sid",
        grants,
    )
    # Outbound-only: a leaked token must not be usable to RECEIVE calls.
    _assert(grants["voice"]["incoming"]["allow"] is False, "no incoming grant", grants)
    print("token shape + grants ok")


def test_token_identity_ignores_body():
    """Identity comes from the authenticated user, never request input — a rep
    cannot register as another user's client."""
    sales_id = _make_user("sales")
    other_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
        r = client.post(
            "/api/voice/token",
            headers=sh,
            json={"identity": f"user{other_id}", "user_id": other_id},
        )
    _assert(r.status_code == 200, "token 200", (r.status_code, r.text))
    _assert(
        r.json()["identity"] == f"user{sales_id}",
        "identity NOT taken from body",
        r.json(),
    )
    print("token identity ignores body ok")


# --- browser call attempt --------------------------------------------------


def test_browser_call_logs_and_returns_dial_token():
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
        r = client.post(
            f"/api/contacts/{contact_id}/call-attempts/browser", headers=sh, json={}
        )
    _assert(r.status_code == 201, "browser call 201", (r.status_code, r.text))
    body = r.json()
    _assert(body["source"] == "twilio_softphone", "source stamped", body)
    _assert(body["outcome"] == "call_initiated", "born initiated", body)
    _assert(body.get("call_attempt_id"), "attempt id returned", body)
    _assert(body.get("dial_token"), "dial token returned", body)

    # The token authorizes exactly this contact's number, and nothing else.
    claims = voice_transport.verify_dial_token(body["dial_token"])
    db = SessionLocal()
    try:
        expected = db.get(Contact, contact_id).phone_e164
    finally:
        db.close()
    _assert(claims["dial"] == expected, "token dials the contact", (claims, expected))
    _assert(claims["cid"] == contact_id, "token bound to contact", claims)
    _assert(claims["uid"] == sales_id, "token bound to rep", claims)
    _assert(_attempt_count(contact_id) == 1, "exactly one attempt logged")
    print("browser call logs + returns dial token ok")


def test_browser_call_503_when_not_configured_and_logs_nothing():
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    with mock.patch("config.settings.TWILIO_SOFTPHONE_ENABLED", False):
        r = client.post(
            f"/api/contacts/{contact_id}/call-attempts/browser", headers=sh, json={}
        )
    _assert(r.status_code == 503, "unconfigured 503", (r.status_code, r.text))
    _assert(_attempt_count(contact_id) == 0, "nothing logged when unconfigured")
    print("browser call unconfigured → 503, nothing logged ok")


def test_browser_call_contact_not_found_and_missing_phone():
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6]:
        r = client.post("/api/contacts/999999999/call-attempts/browser", headers=sh, json={})
        _assert(r.status_code == 404, "missing contact 404", r.status_code)

        no_phone = _make_contact(with_phone=False)
        r = client.post(
            f"/api/contacts/{no_phone}/call-attempts/browser", headers=sh, json={}
        )
        _assert(r.status_code == 422, "no phone 422", (r.status_code, r.text))
        _assert(_attempt_count(no_phone) == 0, "nothing logged without a phone")
    print("browser call 404 / missing-phone ok")


# --- TwiML outbound route --------------------------------------------------


def _post_outbound(form: dict, *, sign: bool = True):
    url = f"https://api.kelleyautoplex.com{_OUTBOUND_PATH}"
    headers = {}
    if sign:
        headers["X-Twilio-Signature"] = compute_signature("tok_test", url, form)
    else:
        headers["X-Twilio-Signature"] = "obviously-wrong"
    return client.post(_OUTBOUND_PATH, headers=headers, data=form)


def test_outbound_twiml_dials_only_authorized_number():
    dial = "+12105558888"
    good = voice_transport.mint_dial_token(
        call_attempt_id=1, contact_id=2, user_id=3, dial_number=dial
    )
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6], mock.patch(
        "config.settings.PUBLIC_API_BASE_URL", "https://api.kelleyautoplex.com"
    ):
        r = _post_outbound({"DialToken": good, "CallSid": "CAtest"})
        _assert(r.status_code == 200, "valid token 200", r.status_code)
        _assert("<Dial" in r.text and dial in r.text, "dials authorized number", r.text)
        _assert(f'callerId="{_BIZ_FROM}"' in r.text, "business caller id", r.text)
        _assert('answerOnBridge="true"' in r.text, "answerOnBridge set", r.text)
    print("outbound twiml dials authorized number ok")


def test_outbound_twiml_rejects_bad_tokens():
    """Absent, forged, expired, and wrong-purpose tokens all dial NOTHING."""
    ctxs = _softphone_on()
    expired = jwt.encode(
        {
            "purpose": "voice_softphone_dial",
            "dial": "+12105559999",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=30),
        },
        voice_transport._token_secret(),
        algorithm="HS256",
    )
    forged = jwt.encode(
        {"purpose": "voice_softphone_dial", "dial": "+12105559999"},
        "not-the-real-secret",
        algorithm="HS256",
    )
    # A VALID bridge token must not work here — purposes are isolated.
    wrong_purpose = voice_transport.mint_bridge_token(
        call_attempt_id=1, contact_id=2, dial_number="+12105559999"
    )

    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6], mock.patch(
        "config.settings.PUBLIC_API_BASE_URL", "https://api.kelleyautoplex.com"
    ):
        for label, form in (
            ("absent", {"CallSid": "CAtest"}),
            ("empty", {"DialToken": "", "CallSid": "CAtest"}),
            ("garbage", {"DialToken": "not-a-jwt", "CallSid": "CAtest"}),
            ("expired", {"DialToken": expired, "CallSid": "CAtest"}),
            ("forged", {"DialToken": forged, "CallSid": "CAtest"}),
            ("wrong_purpose", {"DialToken": wrong_purpose, "CallSid": "CAtest"}),
        ):
            r = _post_outbound(form)
            _assert(r.status_code == 200, f"{label} still 200", r.status_code)
            _assert("<Dial" not in r.text, f"{label} dials nothing", (label, r.text))
            _assert("2105559999" not in r.text, f"{label} leaks no number", r.text)
    print("outbound twiml rejects bad tokens ok")


def test_outbound_twiml_rejects_bad_signature():
    """A valid token with a FORGED Twilio signature still dials nothing."""
    dial = "+12105557777"
    good = voice_transport.mint_dial_token(
        call_attempt_id=1, contact_id=2, user_id=3, dial_number=dial
    )
    ctxs = _softphone_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], ctxs[6], mock.patch(
        "config.settings.PUBLIC_API_BASE_URL", "https://api.kelleyautoplex.com"
    ), mock.patch("config.settings.INBOUND_SMS_REQUIRE_SIGNATURE", True):
        r = _post_outbound({"DialToken": good, "CallSid": "CAtest"}, sign=False)
    _assert(r.status_code == 200, "bad sig 200", r.status_code)
    _assert("<Dial" not in r.text, "bad signature dials nothing", r.text)
    _assert(dial not in r.text, "bad signature leaks no number", r.text)
    print("outbound twiml rejects bad signature ok")


def test_existing_call_paths_untouched():
    """Native tel: logging and the bridge route still behave as before."""
    contact_id = _make_contact()
    sales_id = _make_user("sales")
    sh = {"Authorization": f"Bearer {_token(sales_id, sales=True)}"}
    r = client.post(
        f"/api/contacts/{contact_id}/call-attempts",
        headers=sh,
        json={"phone": "+12105551212", "source": "contact_detail"},
    )
    _assert(r.status_code == 201, "native create 201", (r.status_code, r.text))
    _assert(r.json()["source"] == "contact_detail", "native source preserved", r.json())

    # Bridge with voice OFF still 503s — the softphone switch is independent.
    with mock.patch("config.settings.TWILIO_VOICE_ENABLED", False), mock.patch(
        "config.settings.TWILIO_SOFTPHONE_ENABLED", True
    ):
        r = client.post(
            f"/api/contacts/{contact_id}/call-attempts/bridge",
            headers=sh,
            json={"rep_phone": "+12105550199"},
        )
    _assert(r.status_code == 503, "bridge independent of softphone", (r.status_code, r.text))
    print("existing call paths untouched ok")


if __name__ == "__main__":
    try:
        test_voice_endpoints_require_auth()
        test_token_503_when_disabled()
        test_token_503_when_api_key_missing()
        test_token_shape_and_grants()
        test_token_identity_ignores_body()
        test_browser_call_logs_and_returns_dial_token()
        test_browser_call_503_when_not_configured_and_logs_nothing()
        test_browser_call_contact_not_found_and_missing_phone()
        test_outbound_twiml_dials_only_authorized_number()
        test_outbound_twiml_rejects_bad_tokens()
        test_outbound_twiml_rejects_bad_signature()
        test_existing_call_paths_untouched()
    finally:
        _cleanup()
    print("ALL SOFTPHONE SMOKES PASSED")
