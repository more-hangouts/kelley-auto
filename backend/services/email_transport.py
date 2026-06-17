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

import logging
import smtplib
from dataclasses import dataclass, replace
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Protocol

from config.settings import (
    EMAIL_DEV_REDIRECT,
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
# header `<img src="cid:bellas-logo">` in services/notification_templates.py
# :_wrap_html resolves. Generated from marketing/assets/wordmark.svg.
EMAIL_LOGO_CID = "bellas-logo"
EMAIL_LOGO_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "email" / "bellas-wordmark.png"
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
        em = EmailMessage()
        from_header = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>" if SMTP_FROM_NAME else SMTP_FROM_EMAIL
        em["From"] = from_header
        em["To"] = msg.to
        em["Subject"] = msg.subject
        if msg.reply_to:
            em["Reply-To"] = msg.reply_to
        em.set_content(msg.text)
        if msg.html:
            em.add_alternative(msg.html, subtype="html")
            _attach_logo_to_html_part(em)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            if SMTP_USE_TLS:
                smtp.starttls()
                smtp.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(em)


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
    """Attach the wordmark to the HTML body so cid:bellas-logo resolves.

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


def send_rendered_safely(*, to: str, rendered, scope: str = "email") -> None:
    """Best-effort dispatch for transactional staff/admin emails. Wraps
    the ``RenderedEmail`` → ``EmailMessagePayload`` adaptation and swallows
    SMTP failures so the caller's primary action (schedule publish, role
    change, etc.) succeeds even if email is broken. ``scope`` is the
    namespace used in the exception log line so an operator can grep
    quickly. Mirrors the ``_send_email_safe`` pattern in
    ``services/time_off.py`` — that one stays for now to avoid touching
    working code; new call sites should prefer this shared helper.
    """
    if not to:
        return
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
    except Exception:  # noqa: BLE001
        log.exception("%s: email send failed for %s", scope, to)


def get_email_transport() -> EmailTransport:
    base: EmailTransport
    if SMTP_HOST and SMTP_FROM_EMAIL:
        base = SmtpEmailTransport()
    else:
        base = NullEmailTransport()
    if EMAIL_DEV_REDIRECT:
        return _RedirectingEmailTransport(base, EMAIL_DEV_REDIRECT)
    return base
