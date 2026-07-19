"""SMS transport interface + Twilio implementation.

The A2P 10DLC brand + campaign are approved, so ``TwilioSmsTransport`` is the
live sender: a thin httpx POST to the Twilio Messages API (no SDK dependency,
matching the stdlib inbound verifier and the Meta CAPI sender). Until the
account creds + a sender are configured, ``get_sms_transport()`` returns the
noop so booking-side code that enqueues SMS keeps working unchanged.

Two call shapes intentionally coexist:
  * ``send(payload) -> None`` — the fire-and-forget interface booking
    reminders use (``notification_service``); a failure just logs.
  * ``send_result(to, body) -> SmsSendResult`` — the richer path the CRM
    inbox uses so it can persist the Twilio SID (for the delivery-status
    callback) or the error code on a ``failed`` row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from config.settings import (
    PUBLIC_API_BASE_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
    TWILIO_MESSAGING_SERVICE_SID,
)

log = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 10.0
_STATUS_CALLBACK_PATH = "/api/webhooks/twilio/status"


@dataclass
class SmsMessagePayload:
    to: str
    body: str


@dataclass
class SmsSendResult:
    ok: bool
    provider_message_id: str | None = None
    status: str | None = None  # Twilio's initial status (queued/accepted/…)
    error_code: str | None = None
    error_message: str | None = None


class SmsTransport(Protocol):
    def send(self, msg: SmsMessagePayload) -> None: ...

    def send_result(self, *, to: str, body: str) -> SmsSendResult: ...


def sms_transport_configured() -> bool:
    """True when account creds AND at least one sender are present. The inbox
    checks this before writing a queued row so a misconfiguration is a clean
    503, not a half-sent message."""
    return bool(
        TWILIO_ACCOUNT_SID
        and TWILIO_AUTH_TOKEN
        and (TWILIO_MESSAGING_SERVICE_SID or TWILIO_FROM_NUMBER)
    )


def status_callback_url() -> str:
    return PUBLIC_API_BASE_URL.rstrip("/") + _STATUS_CALLBACK_PATH


class NoopSmsTransport:
    def send(self, msg: SmsMessagePayload) -> None:
        log.info("[sms/noop] to=%s body=%r", msg.to, msg.body)

    def send_result(self, *, to: str, body: str) -> SmsSendResult:
        log.info("[sms/noop] send_result to=%s body=%r", to, body)
        return SmsSendResult(
            ok=False,
            error_code="not_configured",
            error_message="SMS transport not configured (noop)",
        )


class TwilioSmsTransport:
    """Live Twilio Messages API sender. Prefers the Messaging Service SID
    (Kelley's number lives in a Messaging Service ``MGxxxx`` where the A2P
    campaign + sender pool are bound); falls back to a bare From number."""

    def send(self, msg: SmsMessagePayload) -> None:
        # Fire-and-forget interface for booking reminders — a failure logs
        # and is swallowed (the caller has no row to update).
        result = self.send_result(to=msg.to, body=msg.body)
        if not result.ok:
            log.warning(
                "twilio send failed to=%s code=%s: %s",
                msg.to,
                result.error_code,
                result.error_message,
            )

    def send_result(self, *, to: str, body: str) -> SmsSendResult:
        """Send one SMS. NEVER raises — every failure becomes ``ok=False`` so
        the caller can persist a ``failed`` row without a transaction blowing
        up. Registers a status callback for async delivered/failed updates."""
        data: dict[str, str] = {
            "To": to,
            "Body": body,
            "StatusCallback": status_callback_url(),
        }
        if TWILIO_MESSAGING_SERVICE_SID:
            data["MessagingServiceSid"] = TWILIO_MESSAGING_SERVICE_SID
        else:
            data["From"] = TWILIO_FROM_NUMBER  # type: ignore[assignment]

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{TWILIO_ACCOUNT_SID}/Messages.json"
        )
        try:
            with httpx.Client(timeout=_SEND_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                )
        except httpx.HTTPError as exc:
            log.warning("twilio send transport error to=%s: %s", to, exc)
            return SmsSendResult(
                ok=False,
                error_code="transport_error",
                error_message=f"{type(exc).__name__}: {exc}"[:500],
            )

        try:
            payload = resp.json()
        except ValueError:
            payload = {}

        if resp.status_code in (200, 201):
            return SmsSendResult(
                ok=True,
                provider_message_id=payload.get("sid"),
                status=payload.get("status"),
            )

        log.warning(
            "twilio send rejected to=%s http=%s code=%s: %s",
            to,
            resp.status_code,
            payload.get("code"),
            payload.get("message"),
        )
        return SmsSendResult(
            ok=False,
            error_code=str(payload.get("code") or f"http_{resp.status_code}"),
            error_message=str(payload.get("message") or resp.text)[:500],
        )


def get_sms_transport() -> SmsTransport:
    if sms_transport_configured():
        return TwilioSmsTransport()
    return NoopSmsTransport()
