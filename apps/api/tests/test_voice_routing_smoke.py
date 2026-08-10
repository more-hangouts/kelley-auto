"""Inbound voice phase 2 smoke: ring the dashboard first, hold, configurable fallback.

NO real Twilio traffic: every REST call the router would make (ringing a
browser, ringing the fallback, holding a participant) is mocked. Nothing dials.

Run as a script (writes/removes its own rows; points at whatever DATABASE_URL
is configured — CI/local use a migrated scratch clone, never prod):
    .venv/bin/python tests/test_voice_routing_smoke.py

Covers:
  * presence: heartbeat registers, staleness drops a rep out, explicit offline
    is immediate, and only 'available' reps are rung
  * routing: reps online → conference TwiML + browsers rung; nobody online →
    straight to the fallback number; browser_only → message, never a number
  * a browser leg Twilio REFUSES is not counted, so the fallback isn't
    stranded waiting on a leg that was never placed
  * rep-status arbitration: the fallback fires ONCE, and only after EVERY rung
    browser has reported — one rep declining must not yank the caller off
    another rep's still-ringing phone
  * conference join TwiML requires a signature and refuses a foreign room name
  * hold: 404 unknown call, 409 for a non-conference call, success path
  * settings: admin-only writes, E.164 normalization, and the guard against
    selecting fallback_only with no number
  * the AccessToken incoming grant follows its flag
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

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, create_sales_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import InboundCall, User, VoicePresence, VoiceSettings  # noqa: E402
from modules.messaging.services import voice_routing  # noqa: E402
from modules.messaging.services.twilio_signature import compute_signature  # noqa: E402

client = TestClient(app)

_TAG = uuid.uuid4().hex[:8]
_BASE = "https://api.kelleyautoplex.com"
_AUTH_TOKEN = "tok_test"
_BIZ = "+18302689308"
_FALLBACK = "+12105550142"
_INBOUND_PATH = "/api/webhooks/twilio/voice/inbound"
_REP_STATUS_PATH = "/api/webhooks/twilio/voice/conference/rep-status"
_JOIN_PATH = "/api/webhooks/twilio/voice/conference/join"

_user_ids: list[int] = []
_call_sids: list[str] = []


def _assert(cond, label, detail=""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")


def _sid() -> str:
    s = f"CA{_TAG}{uuid.uuid4().hex[:14]}"
    _call_sids.append(s)
    return s


def _make_user(role: str = "sales") -> int:
    db = SessionLocal()
    try:
        u = User(
            username=f"{role}-vr-{_TAG}-{uuid.uuid4().hex[:4]}",
            email=f"{role}-vr-{_TAG}-{uuid.uuid4().hex[:4]}@example.com",
            hashed_password=hash_password("x"),
            full_name=f"Routing {role.title()} {_TAG}",
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


def _hdr(user_id: int, *, sales: bool = True) -> dict:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        tok = create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()
    return {"Authorization": f"Bearer {tok}"}


_ORIGINAL_SETTINGS: dict = {}


def _snapshot_settings():
    db = SessionLocal()
    try:
        cfg = db.get(VoiceSettings, 1)
        if cfg:
            _ORIGINAL_SETTINGS.update(
                {
                    "inbound_mode": cfg.inbound_mode,
                    "fallback_number": cfg.fallback_number,
                    "ring_timeout_seconds": cfg.ring_timeout_seconds,
                    "fallback_timeout_seconds": cfg.fallback_timeout_seconds,
                }
            )
    finally:
        db.close()


def _set_cfg(**fields):
    db = SessionLocal()
    try:
        cfg = db.get(VoiceSettings, 1) or VoiceSettings(id=1)
        for k, v in fields.items():
            setattr(cfg, k, v)
        db.add(cfg)
        db.commit()
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
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM voice_presence WHERE user_id = ANY(:ids)"),
                {"ids": _user_ids},
            )
            db.execute(sql_text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": _user_ids})
        db.commit()
    finally:
        db.close()
    # Restore whatever routing config the box had before the smoke ran.
    if _ORIGINAL_SETTINGS:
        _set_cfg(**_ORIGINAL_SETTINGS)


def _voice_on():
    return (
        mock.patch("config.settings.TWILIO_INBOUND_VOICE_ENABLED", True),
        mock.patch("config.settings.TWILIO_AUTH_TOKEN", _AUTH_TOKEN),
        mock.patch("config.settings.PUBLIC_API_BASE_URL", _BASE),
        mock.patch("config.settings.INBOUND_SMS_REQUIRE_SIGNATURE", True),
        mock.patch("config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ),
        mock.patch("config.settings.TWILIO_INBOUND_FORWARD_NUMBER", _FALLBACK),
    )


def _online(identities: list[str]):
    """Pin exactly who is 'online' for a routing test.

    Routing assertions must not depend on presence rows left behind by an
    earlier test — or by a real rep signed in on this box. Presence itself is
    covered directly by the presence tests above.
    """
    return mock.patch(
        "modules.messaging.services.voice_routing.online_identities",
        mock.Mock(return_value=identities),
    )


def _post(path: str, form: dict, *, sign: bool = True, query: str = ""):
    url = f"{_BASE}{path}" + (f"?{query}" if query else "")
    headers = {
        "X-Twilio-Signature": (
            compute_signature(_AUTH_TOKEN, url, form) if sign else "forged"
        )
    }
    return client.post(path + (f"?{query}" if query else ""), headers=headers, data=form)


# --- presence --------------------------------------------------------------


def test_presence_heartbeat_and_staleness():
    uid = _make_user()
    h = _hdr(uid)
    r = client.post("/api/voice/presence", headers=h, json={"available": True})
    _assert(r.status_code == 200, "heartbeat 200", (r.status_code, r.text))

    db = SessionLocal()
    try:
        _assert(f"user{uid}" in voice_routing.online_identities(db), "rep is online")

        # Age the heartbeat past the staleness window: a closed laptop must drop
        # out of the rotation without a clean logout.
        db.execute(
            sql_text(
                "UPDATE voice_presence SET last_seen_at = NOW() - INTERVAL '10 minutes' "
                "WHERE user_id = :u"
            ),
            {"u": uid},
        )
        db.commit()
        _assert(
            f"user{uid}" not in voice_routing.online_identities(db),
            "stale rep is not rung",
        )
    finally:
        db.close()

    # Fresh beat brings them back.
    client.post("/api/voice/presence", headers=h, json={"available": True})
    db = SessionLocal()
    try:
        _assert(f"user{uid}" in voice_routing.online_identities(db), "rep back online")
    finally:
        db.close()
    print("presence heartbeat + staleness ok")


def test_presence_unavailable_and_offline():
    uid = _make_user()
    h = _hdr(uid)
    client.post("/api/voice/presence", headers=h, json={"available": False})
    db = SessionLocal()
    try:
        _assert(
            f"user{uid}" not in voice_routing.online_identities(db),
            "unavailable rep is not rung",
        )
    finally:
        db.close()

    client.post("/api/voice/presence", headers=h, json={"available": True})
    r = client.delete("/api/voice/presence", headers=h)
    _assert(r.status_code == 200, "offline 200", r.status_code)
    db = SessionLocal()
    try:
        _assert(db.get(VoicePresence, uid) is None, "presence row removed")
    finally:
        db.close()
    print("presence unavailable + explicit offline ok")


# --- routing ---------------------------------------------------------------


def test_routes_to_browser_when_reps_online():
    uid = _make_user()
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)

    sid = _sid()
    ctxs = _voice_on()
    ring = mock.Mock(return_value=1)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], _online([f"user{uid}"]), mock.patch(
        "modules.messaging.services.voice_routing.ring_browsers", ring
    ):
        r = _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551200", "To": _BIZ})

    _assert(r.status_code == 200, "inbound 200", r.status_code)
    _assert("<Conference" in r.text, "caller parked in a conference", r.text)
    _assert(f"kap-call-{sid}" in r.text, "per-call room name", r.text)
    _assert("waitUrl" in r.text, "hold music while waiting", r.text)
    _assert(_FALLBACK not in r.text, "fallback not dialed yet", r.text)
    _assert(ring.called, "browsers were rung")
    _assert(f"user{uid}" in ring.call_args.kwargs["identities"], "this rep rung")

    db = SessionLocal()
    try:
        row = (
            db.query(InboundCall)
            .filter(InboundCall.provider_call_sid == sid)
            .one_or_none()
        )
        _assert(row.conference_name == f"kap-call-{sid}", "room recorded")
        _assert(row.rep_legs_total == 1, "leg count recorded", row.rep_legs_total)
        _assert(row.disposition == "browser", "disposition browser", row.disposition)
    finally:
        db.close()
    print("routes to browser when reps online ok")


def test_routes_to_fallback_when_nobody_online():
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)
    sid = _sid()
    ctxs = _voice_on()
    ring = mock.Mock(return_value=0)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], _online([]), mock.patch(
        "modules.messaging.services.voice_routing.ring_browsers", ring
    ):
        r = _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551201", "To": _BIZ})
    _assert("<Conference" not in r.text, "no conference with nobody online", r.text)
    _assert(f"<Number>{_FALLBACK}</Number>" in r.text, "dials the fallback", r.text)
    _assert(not ring.called, "no browsers rung when none online")
    print("routes to fallback when nobody online ok")


def test_refused_browser_legs_fall_back_immediately():
    """Twilio refusing every browser leg must behave exactly like nobody online —
    not leave the caller waiting on legs that were never placed."""
    uid = _make_user()
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)

    sid = _sid()
    ctxs = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], _online([f"user{uid}"]), mock.patch(
        "modules.messaging.services.voice_routing.ring_browsers",
        mock.Mock(return_value=0),
    ):
        r = _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551202", "To": _BIZ})
    _assert("<Conference" not in r.text, "no conference when legs refused", r.text)
    _assert(f"<Number>{_FALLBACK}</Number>" in r.text, "falls back", r.text)
    print("refused browser legs fall back ok")


def test_browser_only_never_dials_a_number():
    uid = _make_user()
    _set_cfg(inbound_mode="browser_only", fallback_number=_FALLBACK)

    sid = _sid()
    ctxs = _voice_on()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], _online([f"user{uid}"]), mock.patch(
        "modules.messaging.services.voice_routing.ring_browsers",
        mock.Mock(return_value=0),
    ):
        r = _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551203", "To": _BIZ})
    _assert("<Say" in r.text, "speaks the unavailable message", r.text)
    _assert(_FALLBACK not in r.text, "browser_only never dials a number", r.text)
    print("browser_only never dials a number ok")


def test_fallback_only_skips_browsers():
    uid = _make_user()
    _set_cfg(inbound_mode="fallback_only", fallback_number=_FALLBACK)

    sid = _sid()
    ctxs = _voice_on()
    ring = mock.Mock(return_value=1)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], _online([f"user{uid}"]), mock.patch(
        "modules.messaging.services.voice_routing.ring_browsers", ring
    ):
        r = _post(_INBOUND_PATH, {"CallSid": sid, "From": "+12105551204", "To": _BIZ})
    _assert(not ring.called, "fallback_only skips browsers even when online")
    _assert(f"<Number>{_FALLBACK}</Number>" in r.text, "dials fallback", r.text)
    print("fallback_only skips browsers ok")


# --- rep-status arbitration ------------------------------------------------


def _seed_ringing_call(*, legs: int) -> tuple[str, str]:
    sid = _sid()
    room = f"kap-call-{sid}"
    db = SessionLocal()
    try:
        db.add(
            InboundCall(
                provider_call_sid=sid,
                from_number="+12105551300",
                to_number=_BIZ,
                status="received",
                conference_name=room,
                rep_legs_total=legs,
                disposition="browser",
            )
        )
        db.commit()
    finally:
        db.close()
    return sid, room


def test_fallback_waits_for_every_browser_leg():
    """With two reps rung, the first decline must NOT pull the caller away."""
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)
    _sid_, room = _seed_ringing_call(legs=2)
    ctxs = _voice_on()
    fb = mock.Mock(return_value="CAfallback")
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], mock.patch(
        "modules.messaging.services.voice_routing.ring_fallback", fb
    ):
        _post(
            _REP_STATUS_PATH,
            {"CallSid": "CAleg1", "CallStatus": "no-answer"},
            query=f"conference={room}",
        )
        _assert(not fb.called, "first decline does not trigger fallback")

        _post(
            _REP_STATUS_PATH,
            {"CallSid": "CAleg2", "CallStatus": "no-answer"},
            query=f"conference={room}",
        )
        _assert(fb.called, "fallback fires once the last leg reports")
        _assert(fb.call_args.kwargs["number"] == _FALLBACK, "dials configured number")
    print("fallback waits for every browser leg ok")


def test_fallback_fires_only_once():
    """Duplicate/retried callbacks must not start a second fallback leg."""
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)
    _sid_, room = _seed_ringing_call(legs=1)
    ctxs = _voice_on()
    fb = mock.Mock(return_value="CAfallback")
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], mock.patch(
        "modules.messaging.services.voice_routing.ring_fallback", fb
    ):
        for _ in range(4):
            _post(
                _REP_STATUS_PATH,
                {"CallSid": "CAleg1", "CallStatus": "busy"},
                query=f"conference={room}",
            )
    _assert(fb.call_count == 1, "exactly one fallback leg", fb.call_count)
    print("fallback fires only once ok")


def test_answered_leg_never_triggers_fallback():
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)
    _sid_, room = _seed_ringing_call(legs=1)
    ctxs = _voice_on()
    fb = mock.Mock()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], mock.patch(
        "modules.messaging.services.voice_routing.ring_fallback", fb
    ):
        _post(
            _REP_STATUS_PATH,
            {"CallSid": "CAleg1", "CallStatus": "in-progress"},
            query=f"conference={room}",
        )
    _assert(not fb.called, "an answered leg never triggers fallback")
    print("answered leg never triggers fallback ok")


def test_rep_status_requires_signature():
    _set_cfg(inbound_mode="browser_then_fallback", fallback_number=_FALLBACK)
    _sid_, room = _seed_ringing_call(legs=1)
    ctxs = _voice_on()
    fb = mock.Mock()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5], mock.patch(
        "modules.messaging.services.voice_routing.ring_fallback", fb
    ):
        _post(
            _REP_STATUS_PATH,
            {"CallSid": "CAleg1", "CallStatus": "no-answer"},
            sign=False,
            query=f"conference={room}",
        )
    _assert(not fb.called, "forged callback cannot trigger a fallback call")
    print("rep-status requires signature ok")


# --- conference join -------------------------------------------------------


def test_conference_join_twiml():
    ctxs = _voice_on()
    room = "kap-call-CAsomething"
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], ctxs[5]:
        r = _post(_JOIN_PATH, {"CallSid": "CAleg"}, query=f"conference={room}")
        _assert("<Conference" in r.text and room in r.text, "joins the room", r.text)
        _assert('startConferenceOnEnter="true"' in r.text, "rep starts the room", r.text)

        # A foreign room name must not be joinable through our endpoint.
        r = _post(_JOIN_PATH, {"CallSid": "CAleg"}, query="conference=someone-elses-room")
        _assert("<Conference" not in r.text, "refuses foreign room", r.text)

        r = _post(_JOIN_PATH, {"CallSid": "CAleg"}, sign=False, query=f"conference={room}")
        _assert("<Conference" not in r.text, "refuses bad signature", r.text)
    print("conference join twiml ok")


# --- hold ------------------------------------------------------------------


def test_hold_requires_conference_and_works():
    uid = _make_user()
    h = _hdr(uid)

    r = client.post("/api/voice/calls/CAdoesnotexist/hold", headers=h, json={"on": True})
    _assert(r.status_code == 404, "unknown call 404", r.status_code)

    # A call with no conference (fallback-routed) cannot be held.
    plain_sid = _sid()
    db = SessionLocal()
    try:
        db.add(
            InboundCall(
                provider_call_sid=plain_sid,
                from_number="+12105551400",
                to_number=_BIZ,
                status="received",
            )
        )
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/voice/calls/{plain_sid}/hold", headers=h, json={"on": True})
    _assert(r.status_code == 409, "non-conference call 409", (r.status_code, r.text))

    conf_sid, _room = _seed_ringing_call(legs=1)
    with mock.patch(
        "modules.messaging.services.voice_routing.set_hold", mock.Mock(return_value=True)
    ):
        r = client.post(f"/api/voice/calls/{conf_sid}/hold", headers=h, json={"on": True})
    _assert(r.status_code == 200, "hold 200", (r.status_code, r.text))
    _assert(r.json()["on_hold"] is True, "hold reported", r.json())

    with mock.patch(
        "modules.messaging.services.voice_routing.set_hold",
        mock.Mock(return_value=False),
    ):
        r = client.post(f"/api/voice/calls/{conf_sid}/hold", headers=h, json={"on": True})
    _assert(r.status_code == 502, "provider failure surfaces 502", r.status_code)
    print("hold gating + success ok")


# --- settings --------------------------------------------------------------


def test_settings_admin_only_and_normalizes():
    sales_id = _make_user("sales")
    admin_id = _make_user("admin")

    r = client.get("/api/voice/settings", headers=_hdr(sales_id))
    _assert(r.status_code == 200, "sales can read settings", r.status_code)
    _assert("online_reps" in r.json(), "reports who is online", r.json())

    r = client.put(
        "/api/voice/settings", headers=_hdr(sales_id), json={"inbound_mode": "fallback_only"}
    )
    _assert(r.status_code == 403, "sales cannot write settings", r.status_code)

    ah = _hdr(admin_id, sales=False)
    r = client.put(
        "/api/voice/settings",
        headers=ah,
        json={"fallback_number": "(210) 555-0142", "inbound_mode": "browser_then_fallback"},
    )
    _assert(r.status_code == 200, "admin writes settings", (r.status_code, r.text))
    _assert(
        r.json()["fallback_number"] == "+12105550142",
        "typed number normalized to E.164",
        r.json(),
    )

    r = client.put("/api/voice/settings", headers=ah, json={"fallback_number": "nope"})
    _assert(r.status_code == 422, "garbage number rejected", r.status_code)

    # fallback_only with no destination would be a silent dead end.
    r = client.put(
        "/api/voice/settings",
        headers=ah,
        json={"fallback_number": None, "inbound_mode": "fallback_only"},
    )
    _assert(r.status_code == 422, "fallback_only needs a number", (r.status_code, r.text))
    print("settings admin-only + normalization ok")


# --- token grant -----------------------------------------------------------


def test_incoming_grant_follows_flag():
    uid = _make_user()
    h = _hdr(uid)
    base = (
        mock.patch("config.settings.TWILIO_SOFTPHONE_ENABLED", True),
        mock.patch("config.settings.TWILIO_ACCOUNT_SID", "AC_test"),
        mock.patch("config.settings.TWILIO_API_KEY_SID", "SK_test"),
        mock.patch("config.settings.TWILIO_API_KEY_SECRET", "secret-not-real"),
        mock.patch("config.settings.TWILIO_TWIML_APP_SID", "AP_test"),
        mock.patch("config.settings.TWILIO_VOICE_FROM_NUMBER", _BIZ),
    )
    for flag in (False, True):
        with base[0], base[1], base[2], base[3], base[4], base[5], mock.patch(
            "config.settings.TWILIO_INBOUND_TO_BROWSER_ENABLED", flag
        ):
            r = client.post("/api/voice/token", headers=h)
        _assert(r.status_code == 200, "token 200", r.status_code)
        claims = jwt.decode(r.json()["token"], "secret-not-real", algorithms=["HS256"])
        allow = claims["grants"]["voice"]["incoming"]["allow"]
        _assert(allow is flag, f"incoming grant follows flag ({flag})", claims)
        _assert(r.json()["can_receive"] is flag, "can_receive mirrors the grant")
    print("incoming grant follows flag ok")


if __name__ == "__main__":
    _snapshot_settings()
    try:
        test_presence_heartbeat_and_staleness()
        test_presence_unavailable_and_offline()
        test_routes_to_browser_when_reps_online()
        test_routes_to_fallback_when_nobody_online()
        test_refused_browser_legs_fall_back_immediately()
        test_browser_only_never_dials_a_number()
        test_fallback_only_skips_browsers()
        test_fallback_waits_for_every_browser_leg()
        test_fallback_fires_only_once()
        test_answered_leg_never_triggers_fallback()
        test_rep_status_requires_signature()
        test_conference_join_twiml()
        test_hold_requires_conference_and_works()
        test_settings_admin_only_and_normalizes()
        test_incoming_grant_follows_flag()
    finally:
        _cleanup()
    print("ALL VOICE ROUTING SMOKES PASSED")
