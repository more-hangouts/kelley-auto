"""Customer-facing portal email rendering and dispatch.

Phase 7. Sends the email body that contains the portal link when staff
hits Send (or Resend) on an invoice or quote. Synchronous — no
``notification_jobs`` row. Two reasons:

  - The existing ``notification_jobs`` schema FKs ``appointment_id``
    NOT NULL-ish (the worker bails on missing appointments). Making it
    polymorphic for portal emails would mean a schema migration that
    isn't load-bearing for v1.
  - The portal URL doesn't change between "enqueue" and "send time", so
    the queue's render-at-send guarantee buys us nothing here. Staff
    triggers the action, gets immediate feedback if SMTP rejected.

Failures are logged and re-raised as ``PortalEmailError`` — the router
catches and returns a 502 so staff can retry without confusing the UI
state machine (the invoice IS sent; the email just didn't go out).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html import escape

from sqlalchemy.orm import Session

from config.settings import PORTAL_BASE_URL, SMTP_FROM_EMAIL
from database.models import (
    Contact,
    Invoice,
    InvoiceInvitation,
    Quote,
    QuoteInvitation,
)
from services.business_profile_service import (
    BusinessProfileError,
    BusinessProfileView,
    get_profile,
)
from services.email_transport import (
    EmailMessagePayload,
    EmailTransport,
    get_email_transport,
)

log = logging.getLogger(__name__)


class PortalEmailError(Exception):
    """SMTP or rendering failure. Surfaced as 502 by the router so
    staff knows the email didn't go out even though the invoice is
    flagged as sent."""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


@dataclass
class _RenderedEmail:
    subject: str
    text: str
    html: str


def _portal_url(kind: str, public_key: str) -> str:
    base = (PORTAL_BASE_URL or "").rstrip("/")
    return f"{base}/portal/{kind}/{public_key}"


def _format_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    dollars, c = divmod(cents, 100)
    return f"{sign}${dollars:,}.{c:02d}"


def _greeting(contact: Contact) -> str:
    name = (contact.first_name or contact.display_name or "").strip()
    return name.split(" ")[0] if name else "there"


def _contact_recipient(contact: Contact) -> str | None:
    return (contact.email or "").strip() or None


def _shop_name(business: BusinessProfileView | None) -> str:
    return (business.legal_name if business else None) or "Bella's XV"


def _shop_signoff(business: BusinessProfileView | None) -> str:
    """Plain-prose closing line. No em dashes per project copy rules."""
    return f"The {_shop_name(business)} team"


def _render_invoice_sent(
    *,
    invoice: Invoice,
    contact: Contact,
    business: BusinessProfileView | None,
    portal_url: str,
) -> _RenderedEmail:
    name = _greeting(contact)
    number = invoice.invoice_number or "(draft)"
    total = _format_money(invoice.total_cents)
    shop = _shop_name(business)
    signoff = _shop_signoff(business)

    subject = f"Your {shop} invoice {number}"
    text = (
        f"Hi {name},\n\n"
        f"Your invoice from {shop} is ready. The total is {total}.\n\n"
        f"You can review the full invoice and the payment schedule here:\n"
        f"  {portal_url}\n\n"
        f"If you have questions about anything on the invoice, please reply "
        f"to this email or call the boutique.\n\n"
        f"{signoff}\n"
    )
    html = (
        f"<!doctype html><html><body style=\"font-family:-apple-system,Segoe UI,sans-serif;color:#2A1B1F;\">"
        f"<p>Hi {escape(name)},</p>"
        f"<p>Your invoice from {escape(shop)} is ready. The total is "
        f"<strong>{escape(total)}</strong>.</p>"
        f"<p><a href=\"{escape(portal_url)}\" "
        f"style=\"display:inline-block;background:#A7616F;color:#fff;"
        f"padding:12px 18px;border-radius:8px;text-decoration:none;"
        f"font-weight:600;\">View invoice {escape(number)}</a></p>"
        f"<p>If you have questions about anything on the invoice, please reply "
        f"to this email or call the boutique.</p>"
        f"<p>{escape(signoff)}</p>"
        f"</body></html>"
    )
    return _RenderedEmail(subject=subject, text=text, html=html)


def _render_quote_sent(
    *,
    quote: Quote,
    contact: Contact,
    business: BusinessProfileView | None,
    portal_url: str,
) -> _RenderedEmail:
    name = _greeting(contact)
    number = quote.quote_number or "(draft)"
    total = _format_money(quote.total_cents)
    shop = _shop_name(business)
    signoff = _shop_signoff(business)

    subject = f"Your {shop} quote {number}"
    text = (
        f"Hi {name},\n\n"
        f"Here is the quote you asked for from {shop}. The total is {total}.\n\n"
        f"You can review the full quote and sign to accept it here:\n"
        f"  {portal_url}\n\n"
        f"Once you sign, the quote becomes your contract with us and we will "
        f"reach out to confirm next steps.\n\n"
        f"{signoff}\n"
    )
    html = (
        f"<!doctype html><html><body style=\"font-family:-apple-system,Segoe UI,sans-serif;color:#2A1B1F;\">"
        f"<p>Hi {escape(name)},</p>"
        f"<p>Here is the quote you asked for from {escape(shop)}. The total is "
        f"<strong>{escape(total)}</strong>.</p>"
        f"<p><a href=\"{escape(portal_url)}\" "
        f"style=\"display:inline-block;background:#A7616F;color:#fff;"
        f"padding:12px 18px;border-radius:8px;text-decoration:none;"
        f"font-weight:600;\">Review and sign quote {escape(number)}</a></p>"
        f"<p>Once you sign, the quote becomes your contract with us and we will "
        f"reach out to confirm next steps.</p>"
        f"<p>{escape(signoff)}</p>"
        f"</body></html>"
    )
    return _RenderedEmail(subject=subject, text=text, html=html)


def _render_invoice_reminder(
    *,
    invoice: Invoice,
    contact: Contact,
    business: BusinessProfileView | None,
    portal_url: str,
    installment_label: str,
    installment_amount_cents: int,
    due_date_text: str,
    reminder_index: int,
) -> _RenderedEmail:
    """Phase 11. Reminder body — copy bends with reminder_index so the
    third reminder lands harder than the first."""
    name = _greeting(contact)
    number = invoice.invoice_number or ""
    amount = _format_money(installment_amount_cents)
    shop = _shop_name(business)
    signoff = _shop_signoff(business)

    if reminder_index == 1:
        subject = f"Reminder: {installment_label.lower()} of {amount} due {due_date_text}"
        opener = (
            f"This is a friendly reminder that your {installment_label.lower()} "
            f"of {amount} is due {due_date_text}."
        )
    elif reminder_index == 2:
        subject = f"Second reminder: {installment_label.lower()} of {amount}"
        opener = (
            f"We wanted to follow up on your {installment_label.lower()} "
            f"of {amount}, due {due_date_text}."
        )
    else:
        subject = f"Final notice: {installment_label.lower()} of {amount} past due"
        opener = (
            f"Your {installment_label.lower()} of {amount} was due "
            f"{due_date_text} and is now past due. A late fee may apply."
        )

    text = (
        f"Hi {name},\n\n"
        f"{opener}\n\n"
        f"You can pay or get in touch with us through your invoice page:\n"
        f"  {portal_url}\n\n"
        f"If you've already sent the payment, please ignore this and "
        f"we'll get it reconciled on our end.\n\n"
        f"{signoff}\n"
    )
    html = (
        f"<!doctype html><html><body style=\"font-family:-apple-system,Segoe UI,sans-serif;color:#2A1B1F;\">"
        f"<p>Hi {escape(name)},</p>"
        f"<p>{escape(opener)}</p>"
        f"<p><a href=\"{escape(portal_url)}\" "
        f"style=\"display:inline-block;background:#A7616F;color:#fff;"
        f"padding:12px 18px;border-radius:8px;text-decoration:none;"
        f"font-weight:600;\">"
        f"Open invoice {escape(number) if number else ''}</a></p>"
        f"<p>If you've already sent the payment, please ignore this and "
        f"we'll get it reconciled on our end.</p>"
        f"<p>{escape(signoff)}</p>"
        f"</body></html>"
    )
    return _RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _resolve_business(db: Session) -> BusinessProfileView | None:
    try:
        return get_profile(db)
    except BusinessProfileError:
        return None


def _send_one(
    transport: EmailTransport,
    *,
    recipient: str,
    rendered: _RenderedEmail,
) -> None:
    msg = EmailMessagePayload(
        to=recipient,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
        reply_to=SMTP_FROM_EMAIL or None,
    )
    transport.send(msg)


def send_invoice_invitations(
    db: Session,
    *,
    invoice: Invoice,
    invitation_ids: list[int] | None = None,
) -> int:
    """Send the customer-facing email for every active invitation row.

    If ``invitation_ids`` is given, only those rows are emailed (used by
    resend on a specific contact set). Otherwise every active invitation
    on the invoice gets emailed. Returns the count of emails dispatched.
    Rows whose contact has no email address are skipped silently — the
    staff UI is responsible for showing that gap.
    """
    q = (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.invoice_id == invoice.id)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
    )
    if invitation_ids:
        q = q.filter(InvoiceInvitation.id.in_(invitation_ids))
    invitations = q.all()
    if not invitations:
        return 0

    business = _resolve_business(db)
    transport = get_email_transport()
    sent = 0
    for inv_row in invitations:
        contact = db.get(Contact, inv_row.contact_id)
        if contact is None:
            continue
        recipient = _contact_recipient(contact)
        if not recipient:
            log.info(
                "portal_email.invoice.skipped_no_email",
                extra={"invoice_id": invoice.id, "contact_id": contact.id},
            )
            continue
        try:
            rendered = _render_invoice_sent(
                invoice=invoice,
                contact=contact,
                business=business,
                portal_url=_portal_url("invoice", inv_row.public_key),
            )
            _send_one(transport, recipient=recipient, rendered=rendered)
            sent += 1
        except Exception as exc:
            log.exception(
                "portal_email.invoice.failed",
                extra={
                    "invoice_id": invoice.id,
                    "contact_id": contact.id,
                    "invitation_id": inv_row.id,
                },
            )
            raise PortalEmailError(
                f"failed to send invoice email: {exc!s}"
            ) from exc
    return sent


def send_quote_invitations(
    db: Session,
    *,
    quote: Quote,
    invitation_ids: list[int] | None = None,
) -> int:
    q = (
        db.query(QuoteInvitation)
        .filter(QuoteInvitation.quote_id == quote.id)
        .filter(QuoteInvitation.deleted_at.is_(None))
        .filter(QuoteInvitation.revoked_at.is_(None))
    )
    if invitation_ids:
        q = q.filter(QuoteInvitation.id.in_(invitation_ids))
    invitations = q.all()
    if not invitations:
        return 0

    business = _resolve_business(db)
    transport = get_email_transport()
    sent = 0
    for inv_row in invitations:
        contact = db.get(Contact, inv_row.contact_id)
        if contact is None:
            continue
        recipient = _contact_recipient(contact)
        if not recipient:
            log.info(
                "portal_email.quote.skipped_no_email",
                extra={"quote_id": quote.id, "contact_id": contact.id},
            )
            continue
        try:
            rendered = _render_quote_sent(
                quote=quote,
                contact=contact,
                business=business,
                portal_url=_portal_url("quote", inv_row.public_key),
            )
            _send_one(transport, recipient=recipient, rendered=rendered)
            sent += 1
        except Exception as exc:
            log.exception(
                "portal_email.quote.failed",
                extra={
                    "quote_id": quote.id,
                    "contact_id": contact.id,
                    "invitation_id": inv_row.id,
                },
            )
            raise PortalEmailError(
                f"failed to send quote email: {exc!s}"
            ) from exc
    return sent


def send_invoice_reminder(
    db: Session,
    *,
    invoice: Invoice,
    invitation: InvoiceInvitation,
    installment_label: str,
    installment_amount_cents: int,
    due_date_text: str,
    reminder_index: int,
) -> bool:
    """Phase 11. Send a single reminder email for one invitation row.

    Returns True if an email was actually dispatched, False if the
    contact has no email address (the caller should still stamp the
    `reminder*_sent_at` column so it doesn't keep retrying — the
    customer's email can be added later, but the missed-window state
    is already over). Raises ``PortalEmailError`` on SMTP failure so
    the caller can decide whether to back off and retry next pass.
    """
    contact = db.get(Contact, invitation.contact_id)
    if contact is None:
        return False
    recipient = _contact_recipient(contact)
    if not recipient:
        log.info(
            "portal_email.reminder.skipped_no_email",
            extra={
                "invoice_id": invoice.id,
                "contact_id": contact.id,
                "reminder_index": reminder_index,
            },
        )
        return False

    business = _resolve_business(db)
    portal_url = _portal_url("invoice", invitation.public_key)
    try:
        rendered = _render_invoice_reminder(
            invoice=invoice,
            contact=contact,
            business=business,
            portal_url=portal_url,
            installment_label=installment_label,
            installment_amount_cents=installment_amount_cents,
            due_date_text=due_date_text,
            reminder_index=reminder_index,
        )
        _send_one(get_email_transport(), recipient=recipient, rendered=rendered)
        return True
    except Exception as exc:
        log.exception(
            "portal_email.reminder.failed",
            extra={
                "invoice_id": invoice.id,
                "contact_id": contact.id,
                "reminder_index": reminder_index,
            },
        )
        raise PortalEmailError(
            f"failed to send reminder email: {exc!s}"
        ) from exc
