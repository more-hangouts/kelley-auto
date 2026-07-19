"""Email transport with a Null fallback for dev.

When SMTP_HOST is unset (which is the default in dev .env), we route every
"send" to the NullEmailTransport which writes the rendered message through
the standard ``logging`` module instead of dispatching it. This keeps the
local feedback loop fast and prevents accidental mail to real customer
addresses while the booking widget is being exercised.

When EMAIL_DEV_REDIRECT is set, every outbound message is rewritten to
that address before it hits the underlying transport, with the original
recipient surfaced in the subject prefix and an in-body banner. This lets
us read every template in one inbox while we're still building copy,
without any per-template plumbing.
"""

from __future__ import annotations

import base64
import logging
import smtplib
from dataclasses import dataclass, replace
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Protocol

from config.settings import (
    EMAIL_DEV_REDIRECT,
    GMAIL_API_SENDER,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    GMAIL_OAUTH_REFRESH_TOKEN,
    SMTP_FROM_EMAIL,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_USE_TLS,
)

log = logging.getLogger(__name__)

# CID + on-disk path for the wordmark attached to every HTML email so the
# header `<img src="cid:kelley-logo">` in services/notification_templates.py
# :_wrap_html resolves. Sourced from the Kelley brand wordmark (logo-dark.png).
EMAIL_LOGO_CID = "kelley-logo"
EMAIL_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "email" / "kelley-wordmark.png"
)


@dataclass
class EmailMessagePayload:
    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None


class EmailTransport(Protocol):
    def send(self, msg: EmailMessagePayload) -> None: ...


def _build_email_message(msg: EmailMessagePayload, *, from_email: str) -> EmailMessage:
    """Render a payload into a MIME message shared by every real transport.

    Keeps the From display-name, text/HTML alternative structure, and the
    inline logo attachment identical whether we dispatch over SMTP or the
    Gmail API — so only the wire protocol differs between transports.
    """
    em = EmailMessage()
    em["From"] = f"{SMTP_FROM_NAME} <{from_email}>" if SMTP_FROM_NAME else from_email
    em["To"] = msg.to
    em["Subject"] = msg.subject
    if msg.reply_to:
        em["Reply-To"] = msg.reply_to
    em.set_content(msg.text)
    if msg.html:
        em.add_alternative(msg.html, subtype="html")
        _attach_logo_to_html_part(em)
    return em


class NullEmailTransport:
    """Logs the message instead of sending. The dev default."""

    def send(self, msg: EmailMessagePayload) -> None:
        log.info(
            "[email/null] to=%s subject=%r\n--text--\n%s",
            msg.to,
            msg.subject,
            msg.text,
        )


class SmtpEmailTransport:
    def __init__(self) -> None:
        if not SMTP_HOST:
            raise RuntimeError("SmtpEmailTransport requires SMTP_HOST")
        if not SMTP_FROM_EMAIL:
            raise RuntimeError("SmtpEmailTransport requires SMTP_FROM_EMAIL")

    def send(self, msg: EmailMessagePayload) -> None:
        em = _build_email_message(msg, from_email=SMTP_FROM_EMAIL)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            if SMTP_USE_TLS:
                smtp.starttls()
                smtp.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(em)


class GmailApiTransport:
    """Sends via the Gmail REST API using an OAuth2 refresh token.

    A one-time consent as GMAIL_API_SENDER mints a long-lived refresh token;
    the ``Credentials`` object exchanges it for short-lived access tokens on
    demand (auto-refreshing when they expire). Mail is sent *as* that
    mailbox and lands in its Sent folder. Google libraries are imported
    lazily inside ``__init__`` so the module still imports — and the other
    transports still work — on a box where ``google-auth`` isn't installed
    or the OAuth values are misconfigured.
    """

    _SCOPES = ("https://www.googleapis.com/auth/gmail.send",)
    _TOKEN_URI = "https://oauth2.googleapis.com/token"
    _SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    def __init__(self) -> None:
        if not (
            GMAIL_OAUTH_CLIENT_ID
            and GMAIL_OAUTH_CLIENT_SECRET
            and GMAIL_OAUTH_REFRESH_TOKEN
        ):
            raise RuntimeError("GmailApiTransport requires the GMAIL_OAUTH_* values")
        if not GMAIL_API_SENDER:
            raise RuntimeError("GmailApiTransport requires GMAIL_API_SENDER")

        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=GMAIL_OAUTH_REFRESH_TOKEN,
            client_id=GMAIL_OAUTH_CLIENT_ID,
            client_secret=GMAIL_OAUTH_CLIENT_SECRET,
            token_uri=self._TOKEN_URI,
            scopes=list(self._SCOPES),
        )
        self._session = AuthorizedSession(creds)

    def send(self, msg: EmailMessagePayload) -> None:
        em = _build_email_message(msg, from_email=GMAIL_API_SENDER)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode("ascii")
        resp = self._session.post(self._SEND_URL, json={"raw": raw}, timeout=20)
        if resp.status_code >= 400:
            # Surface the API's error body so an operator can tell an auth
            # problem (delegation not authorized) from a bad recipient.
            raise RuntimeError(
                f"Gmail API send failed ({resp.status_code}): {resp.text[:500]}"
            )


class _RedirectingEmailTransport:
    """Wraps another transport to redirect every send to a single address.

    The wrapping happens in ``get_email_transport`` when EMAIL_DEV_REDIRECT
    is set. The wrapper rewrites To:, prefixes the subject with the original
    recipient, and stamps an in-body banner on both text and HTML parts so a
    forwarded test email is unmistakable.
    """

    def __init__(self, inner: EmailTransport, redirect_to: str) -> None:
        self._inner = inner
        self._redirect_to = redirect_to

    def send(self, msg: EmailMessagePayload) -> None:
        original_to = msg.to
        text_banner = (
            f"[TEST EMAIL — would have gone to {original_to}]\n"
            f"--------------------------------------------------\n\n"
        )
        html_banner = (
            f"<div style=\"background:#fff3cd; border:1px solid #f0c674; "
            f"padding:10px 14px; margin:0 0 16px 0; font-family:"
            f"-apple-system, Segoe UI, sans-serif; font-size:13px; "
            f"color:#5a4400; border-radius:4px;\">"
            f"<strong>TEST EMAIL</strong> — would have gone to "
            f"<code>{escape(original_to)}</code>"
            f"</div>"
        )
        rewritten = replace(
            msg,
            to=self._redirect_to,
            subject=f"[TEST -> {original_to}] {msg.subject}",
            text=text_banner + msg.text,
            html=(html_banner + msg.html) if msg.html else None,
        )
        self._inner.send(rewritten)


def _attach_logo_to_html_part(em: EmailMessage) -> None:
    """Attach the wordmark to the HTML body so cid:kelley-logo resolves.

    Called after ``em.add_alternative(html, subtype='html')``. The Python
    email API restructures the html part into ``multipart/related`` so the
    image rides alongside the HTML. Missing logo file is logged and
    silently skipped — the email still delivers, just without the header
    image.
    """
    if not EMAIL_LOGO_PATH.exists():
        log.warning("[email] logo missing at %s; sending without inline image", EMAIL_LOGO_PATH)
        return
    html_part = em.get_body(preferencelist=("html",))
    if html_part is None:  # pragma: no cover - add_alternative just ran
        return
    html_part.add_related(
        EMAIL_LOGO_PATH.read_bytes(),
        maintype="image",
        subtype="png",
        cid=f"<{EMAIL_LOGO_CID}>",
        disposition="inline",
        filename=EMAIL_LOGO_PATH.name,
    )


def send_rendered_safely(*, to: str, rendered, scope: str = "email") -> bool:
    """Best-effort dispatch for transactional staff/admin emails. Wraps
    the ``RenderedEmail`` → ``EmailMessagePayload`` adaptation and swallows
    SMTP failures so the caller's primary action (schedule publish, role
    change, etc.) succeeds even if email is broken. ``scope`` is the
    namespace used in the exception log line so an operator can grep
    quickly. Mirrors the ``_send_email_safe`` pattern in
    ``services/time_off.py`` — that one stays for now to avoid touching
    working code; new call sites should prefer this shared helper.

    Returns True when the transport accepted the message, False when it was
    skipped (no recipient) or failed — so callers that care (e.g. lead
    alerts) can record the outcome instead of assuming success.
    """
    if not to:
        return False
    try:
        get_email_transport().send(
            EmailMessagePayload(
                to=to,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
                reply_to=SMTP_FROM_EMAIL or None,
            )
        )
        return True
    except Exception:  # noqa: BLE001
        log.exception("%s: email send failed for %s", scope, to)
        return False


def active_email_transport_kind() -> str:
    """The transport ``get_email_transport`` will select, as a short label
    (``"gmail_api"`` / ``"smtp"`` / ``"null"``) — the single source of truth
    for both the selector below and the health/startup checks. Pure: reads
    config only, constructs nothing.
    """
    if (
        GMAIL_OAUTH_CLIENT_ID
        and GMAIL_OAUTH_CLIENT_SECRET
        and GMAIL_OAUTH_REFRESH_TOKEN
        and GMAIL_API_SENDER
    ):
        return "gmail_api"
    if SMTP_HOST and SMTP_FROM_EMAIL:
        return "smtp"
    return "null"


def email_delivery_enabled() -> bool:
    """False when the resolved transport only logs (NullEmailTransport), i.e.
    no mail actually leaves the box. Callers use this to fail/warn loudly in
    production instead of silently dropping mail."""
    return active_email_transport_kind() != "null"


def get_email_transport() -> EmailTransport:
    kind = active_email_transport_kind()
    base: EmailTransport
    if kind == "gmail_api":
        base = GmailApiTransport()
    elif kind == "smtp":
        base = SmtpEmailTransport()
    else:
        base = NullEmailTransport()
    if EMAIL_DEV_REDIRECT:
        return _RedirectingEmailTransport(base, EMAIL_DEV_REDIRECT)
    return base
