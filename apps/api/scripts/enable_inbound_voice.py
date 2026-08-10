"""Point the Twilio business number at our inbound webhook — the last step.

Run AFTER `sudo systemctl restart kelley-backend`, because it refuses to
repoint the number until it has proved the live webhook actually answers with
forwarding TwiML. Repointing first would send real callers to a 404 and they'd
hear Twilio's "application error" recording.

What it does, in order:

  1. Signs a synthetic inbound request exactly as Twilio would and POSTs it at
     the PUBLIC url. Verifies the response forwards to the configured office
     number. Any failure aborts WITHOUT touching Twilio.
  2. Sets voice_url + voice_method on the number, and status_callback so call
     outcomes (answered / no-answer / duration) flow back.

Idempotent: re-running just re-asserts the same configuration.

    .venv/bin/python scripts/enable_inbound_voice.py           # verify + apply
    .venv/bin/python scripts/enable_inbound_voice.py --check   # verify only
    .venv/bin/python scripts/enable_inbound_voice.py --revert  # unset voice_url

`--revert` restores today's behaviour (inbound dead-ends) and is the rollback
if a live test call misbehaves.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from config import settings  # noqa: E402
from modules.messaging.services.twilio_signature import compute_signature  # noqa: E402

_INBOUND_PATH = "/api/webhooks/twilio/voice/inbound"
_STATUS_PATH = "/api/webhooks/twilio/voice/status"
# Obviously-synthetic CallSid so the verification row is easy to spot and drop.
_PROBE_SID = "CAverifyinboundwebhookprobe00001"


def _fail(msg: str) -> None:
    print(f"ABORT: {msg}")
    raise SystemExit(1)


def _twilio(method: str, path: str, data: dict | None = None) -> dict:
    sid, tok = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}{path}"
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _number_record() -> dict:
    target = settings.TWILIO_VOICE_FROM_NUMBER or settings.TWILIO_FROM_NUMBER
    if not target:
        _fail("no business voice number configured (TWILIO_VOICE_FROM_NUMBER)")
    listing = _twilio("GET", "/IncomingPhoneNumbers.json?PageSize=100")
    for n in listing.get("incoming_phone_numbers", []):
        if n["phone_number"] == target:
            return n
    _fail(f"{target} is not an IncomingPhoneNumber on this account")


def verify_live_webhook() -> None:
    """POST a Twilio-signed synthetic call at the PUBLIC url and require
    forwarding TwiML back. This is what makes repointing safe."""
    base = settings.PUBLIC_API_BASE_URL.rstrip("/")
    url = f"{base}{_INBOUND_PATH}"
    office = settings.TWILIO_INBOUND_FORWARD_NUMBER
    if not office:
        _fail("TWILIO_INBOUND_FORWARD_NUMBER is not set")
    if not settings.TWILIO_INBOUND_VOICE_ENABLED:
        _fail("TWILIO_INBOUND_VOICE_ENABLED is false — restart the backend with it true")

    form = {
        "CallSid": _PROBE_SID,
        "From": "+15005550006",  # Twilio's own test number, never a real caller
        "To": settings.TWILIO_VOICE_FROM_NUMBER or "",
        "CallStatus": "ringing",
    }
    sig = compute_signature(settings.TWILIO_AUTH_TOKEN, url, form)
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(), method="POST"
    )
    req.add_header("X-Twilio-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        _fail(f"webhook returned HTTP {exc.code} — is the backend restarted?")
    except Exception as exc:  # noqa: BLE001
        _fail(f"could not reach {url}: {exc}")

    if status != 200:
        _fail(f"webhook returned HTTP {status}")
    if "<Dial" not in text or office not in text:
        _fail(f"webhook did not return forwarding TwiML for {office}:\n{text}")
    print(f"  ✓ live webhook forwards to {office}")
    print(f"  note: probe row logged as {_PROBE_SID} (safe to delete)")


def apply_config() -> None:
    base = settings.PUBLIC_API_BASE_URL.rstrip("/")
    rec = _number_record()
    updated = _twilio(
        "POST",
        f"/IncomingPhoneNumbers/{rec['sid']}.json",
        {
            "VoiceUrl": f"{base}{_INBOUND_PATH}",
            "VoiceMethod": "POST",
            "StatusCallback": f"{base}{_STATUS_PATH}",
            "StatusCallbackMethod": "POST",
        },
    )
    print(f"  ✓ {updated['phone_number']} voice_url  = {updated.get('voice_url')}")
    print(f"  ✓ {updated['phone_number']} status_cb  = {updated.get('status_callback')}")


def revert_config() -> None:
    rec = _number_record()
    updated = _twilio(
        "POST",
        f"/IncomingPhoneNumbers/{rec['sid']}.json",
        {"VoiceUrl": "", "StatusCallback": ""},
    )
    print(f"  ✓ {updated['phone_number']} voice_url cleared — inbound dead-ends again")


def main() -> None:
    args = set(sys.argv[1:])
    if "--revert" in args:
        print("Reverting inbound voice routing…")
        revert_config()
        return

    print("Verifying the live inbound webhook…")
    verify_live_webhook()
    if "--check" in args:
        print("Check only — Twilio not modified.")
        return

    print("Pointing the business number at it…")
    apply_config()
    print("\nInbound voice is ON. Place a test call to the business number.")
    print("Rollback:  .venv/bin/python scripts/enable_inbound_voice.py --revert")


if __name__ == "__main__":
    main()
