"""SMS transport interface.

v1 ships only the noop transport — Twilio integration arrives once the
10DLC registration clears. The interface lives here so booking-side code
can enqueue SMS notifications today without changing when SMS goes live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from config.settings import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
    TWILIO_MESSAGING_SERVICE_SID,
)

log = logging.getLogger(__name__)


@dataclass
class SmsMessagePayload:
    to: str
    body: str


class SmsTransport(Protocol):
    def send(self, msg: SmsMessagePayload) -> None: ...


class NoopSmsTransport:
    def send(self, msg: SmsMessagePayload) -> None:
        log.info("[sms/noop] to=%s body=%r", msg.to, msg.body)


def get_sms_transport() -> SmsTransport:
    # Twilio will land here once 10DLC + creds are in place.
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and (
        TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID
    ):
        log.warning("Twilio transport not yet implemented; using NoopSmsTransport")
    return NoopSmsTransport()
