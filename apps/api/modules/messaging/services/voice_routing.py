"""Phase 2 inbound routing: ring the dashboard first, then a configurable number.

The shape of an answered call, and why it is a conference rather than a plain
bridge:

    caller --> <Conference kap-call-<sid>> <-- rep's browser (joined leg)

A plain ``<Dial><Client>`` bridge cannot be put on hold. Twilio has no hold verb
for it, and redirecting the caller's leg to hold music would end the ``<Dial>``
and tear down the rep's leg with it. Conference participants CAN be held
individually (a REST update with ``Hold=true``), so the conference is what makes
"put them on hold" possible at all — and it's also what will make transfer and
barge possible later without another re-architecture.

Ring flow:

  1. Caller is answered into a per-call conference with hold music, waiting.
  2. Every online rep's browser client is rung as a separate outbound leg
     (``client:userN``) that joins the same conference on answer.
  3. Each rep leg reports its outcome to a status callback. When ALL of them
     have reported without anyone joining, the PSTN fallback leg is originated
     into the conference. Counting — rather than reacting to the first decline —
     is what keeps one rep's "no thanks" from yanking the caller away from
     another rep's still-ringing phone.
  4. If nobody is online at all, the fallback is dialed directly and no
     conference is created; there is no browser leg to hold.

Every Twilio REST call here is best-effort and never raises into the webhook:
a failure to ring a browser must not cost the caller their call.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from config import settings
from database.models import InboundCall, VoicePresence, VoiceSettings

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
# A rep whose browser hasn't checked in within this window is treated as gone.
# Comfortably longer than the dashboard's heartbeat interval so one missed beat
# (a hiccup, a backgrounded tab) doesn't drop a rep out of the rotation.
PRESENCE_STALE_SECONDS = 75

_CONFERENCE_JOIN_PATH = "/api/webhooks/twilio/voice/conference/join"
_REP_STATUS_PATH = "/api/webhooks/twilio/voice/conference/rep-status"

# Twilio's stock hold music. Used both for the caller waiting for a rep to pick
# up and for an explicit hold.
HOLD_MUSIC_URL = "http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical"


def _base_url() -> str:
    return settings.PUBLIC_API_BASE_URL.rstrip("/")


def get_settings(db: Session) -> VoiceSettings:
    """The singleton routing config. Created on demand so a fresh database (or
    a restored backup that predates migration 106's seed) can never 500 an
    inbound call on a missing row."""
    row = db.get(VoiceSettings, 1)
    if row is None:
        row = VoiceSettings(id=1)
        db.add(row)
        db.flush()
    return row


def online_identities(db: Session) -> list[str]:
    """Twilio client identities for reps whose dashboard is registered, marked
    available, and heartbeating. Ordered for deterministic TwiML in tests."""
    rows = (
        db.query(VoicePresence.identity)
        .filter(
            VoicePresence.available.is_(True),
            VoicePresence.last_seen_at
            >= sql_text(f"NOW() - INTERVAL '{PRESENCE_STALE_SECONDS} seconds'"),
        )
        .order_by(VoicePresence.user_id.asc())
        .all()
    )
    return [r[0] for r in rows]


def touch_presence(db: Session, *, user_id: int, identity: str, available: bool = True):
    """Upsert this rep's heartbeat. Called on an interval by the dashboard while
    the softphone is registered."""
    row = db.get(VoicePresence, user_id)
    if row is None:
        row = VoicePresence(user_id=user_id, identity=identity, available=available)
        db.add(row)
    else:
        row.identity = identity
        row.available = available
        row.last_seen_at = sql_text("NOW()")
    return row


def clear_presence(db: Session, *, user_id: int) -> None:
    """Explicit sign-off (tab closed cleanly, softphone toggled off). Staleness
    already covers the messy cases; this just makes the clean case instant."""
    row = db.get(VoicePresence, user_id)
    if row is not None:
        db.delete(row)


def conference_name_for(call_sid: str) -> str:
    return f"kap-call-{call_sid}"


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --- TwiML builders --------------------------------------------------------


def build_conference_twiml(*, conference_name: str) -> str:
    """The CALLER's leg: park them in the conference with hold music.

    ``startConferenceOnEnter=false`` keeps them on hold music until a rep
    actually joins. ``endConferenceOnExit=true`` means the conference dies when
    the caller hangs up, so a rep is never left talking to an empty room.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Dial>"
        f'<Conference startConferenceOnEnter="false" endConferenceOnExit="true"'
        f' waitUrl="{_xml_escape(HOLD_MUSIC_URL)}"'
        f' beep="false">{_xml_escape(conference_name)}</Conference>'
        "</Dial>"
        "</Response>"
    )


def build_join_twiml(*, conference_name: str) -> str:
    """A REP's (or the fallback number's) leg: join and start the conference.

    ``endConferenceOnExit=false`` lets a rep hand off or drop without killing
    the caller's leg.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Dial>"
        f'<Conference startConferenceOnEnter="true" endConferenceOnExit="false"'
        f' beep="false">{_xml_escape(conference_name)}</Conference>'
        "</Dial>"
        "</Response>"
    )


# --- Twilio REST -----------------------------------------------------------


def _post_call(data: dict) -> str | None:
    """Originate one outbound leg. Returns the CallSid, or None on any failure —
    ringing a browser must never raise into the inbound webhook."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        return None
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Calls.json"
    )
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                data=data,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            )
    except httpx.HTTPError as exc:
        log.warning("voice_routing: leg to %s failed: %s", data.get("To"), exc)
        return None
    if resp.status_code not in (200, 201):
        log.warning(
            "voice_routing: leg to %s rejected http=%s body=%s",
            data.get("To"),
            resp.status_code,
            resp.text[:300],
        )
        return None
    try:
        return resp.json().get("sid")
    except ValueError:
        return None


def ring_browsers(*, conference_name: str, identities: list[str], timeout: int) -> int:
    """Ring every online rep's browser. Returns how many legs Twilio accepted —
    the denominator the fallback logic counts down from, so a leg Twilio refused
    is never waited on."""
    base = _base_url()
    join_url = f"{base}{_CONFERENCE_JOIN_PATH}?conference={conference_name}"
    status_url = f"{base}{_REP_STATUS_PATH}?conference={conference_name}"
    started = 0
    for identity in identities:
        sid = _post_call(
            {
                "To": f"client:{identity}",
                # Caller ID on a client leg is cosmetic; the browser shows the
                # real caller from the call parameters we pass separately.
                "From": settings.TWILIO_VOICE_FROM_NUMBER or "",
                "Url": join_url,
                "Method": "POST",
                "Timeout": str(timeout),
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": "completed",
            }
        )
        if sid:
            started += 1
    return started


def ring_fallback(*, conference_name: str, number: str, timeout: int) -> str | None:
    """Bring the fallback number into the conference. Used both when nobody is
    online and when the browsers all declined."""
    base = _base_url()
    return _post_call(
        {
            "To": number,
            "From": settings.TWILIO_VOICE_FROM_NUMBER or "",
            "Url": f"{base}{_CONFERENCE_JOIN_PATH}?conference={conference_name}",
            "Method": "POST",
            "Timeout": str(timeout),
        }
    )


def _conference_sid(conference_name: str) -> str | None:
    """Resolve a conference friendly-name to its SID. Hold is a participant
    update, and participant URLs are keyed by conference SID."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        return None
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Conferences.json"
        f"?FriendlyName={conference_name}&Status=in-progress"
    )
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.get(
                url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            )
        rooms = resp.json().get("conferences", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("voice_routing: conference lookup failed: %s", exc)
        return None
    return rooms[0]["sid"] if rooms else None


def set_hold(*, conference_name: str, participant_call_sid: str, on: bool) -> bool:
    """Hold or resume ONE participant — the caller, never the rep.

    This is the payoff for routing through a conference: the caller hears music
    while the rep keeps their own leg open, instead of the bridge collapsing.
    """
    room_sid = _conference_sid(conference_name)
    if not room_sid:
        return False
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.TWILIO_ACCOUNT_SID}/Conferences/{room_sid}"
        f"/Participants/{participant_call_sid}.json"
    )
    data = {"Hold": "true" if on else "false"}
    if on:
        data["HoldUrl"] = HOLD_MUSIC_URL
        data["HoldMethod"] = "GET"
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                data=data,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            )
    except httpx.HTTPError as exc:
        log.warning("voice_routing: hold update failed: %s", exc)
        return False
    if resp.status_code not in (200, 201):
        log.warning(
            "voice_routing: hold rejected http=%s body=%s",
            resp.status_code,
            resp.text[:300],
        )
        return False
    return True


# --- fallback arbitration --------------------------------------------------


def claim_fallback(db: Session, *, call: InboundCall) -> bool:
    """Record one finished rep leg and decide whether THIS caller should start
    the fallback.

    Returns True at most once per call. The guard is a conditional UPDATE
    rather than a read-then-write, because two rep legs can report at the same
    moment on two different uvicorn workers; the loser's UPDATE matches zero
    rows and it simply does nothing.
    """
    updated = db.execute(
        sql_text(
            """
            UPDATE inbound_calls
               SET rep_legs_done = rep_legs_done + 1,
                   updated_at = NOW()
             WHERE id = :id
         RETURNING rep_legs_done, rep_legs_total
            """
        ),
        {"id": call.id},
    ).one_or_none()
    if updated is None:
        return False
    done, total = updated

    # Someone is still ringing — do not pull the caller away from them.
    if done < total:
        return False

    claimed = db.execute(
        sql_text(
            """
            UPDATE inbound_calls
               SET fallback_started = TRUE,
                   updated_at = NOW()
             WHERE id = :id AND NOT fallback_started
         RETURNING id
            """
        ),
        {"id": call.id},
    ).one_or_none()
    return claimed is not None
