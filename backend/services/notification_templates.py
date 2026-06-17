"""Render functions for booking-related emails and SMS.

Plain Python templates for v1 — simple enough that pulling in Jinja just
for these would be over-engineering. If templates grow more complex we
can swap to Jinja and keep the same render-at-send shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape

from sqlalchemy.orm.session import object_session

from config.settings import APP_TIMEZONE, SMTP_FROM_EMAIL, WIDGET_PUBLIC_BASE_URL
from database.models import Appointment, AppointmentEnrichmentResponse
from services.booking_service import format_confirmation_code
from services.booking_tokens import cancel_url, enrichment_url, reschedule_url
from services.email_transport import EmailMessagePayload
from services.sms_transport import SmsMessagePayload


_PARTY_LABEL = {
    # Legacy buckets (pre parent-capture flow). Kept for historical rows.
    "solo": "Just you",
    "2_3": "2-3 people",
    "4_plus": "4 or more",
    # Current buckets used by the booking widget.
    "pair": "Me and my quinceañera",
    "3_4": "3-4 of us",
    "5_plus": "5 or more",
}
_BOUTIQUE_ADDRESS = "7723 Guilbeau Rd #101, San Antonio, TX 78250"
_BOUTIQUE_PHONE = "(210) 670-5845"

_BE_INTRO = (
    "Help us prepare dresses in your size, style, and budget before you arrive."
)
_BE_CTA_LABEL = "Complete your Boutique Experience Profile"


def is_boutique_profile_attached(appt: Appointment) -> bool:
    """True iff any appointment on this lead has a submitted profile.

    A profile completed on the original appointment must keep counting as
    "this lead is done" after a reschedule, since Phase 1 keeps the
    profile attached to the original appointment as historical data
    rather than copying it forward. So when ``crm_event_id`` is set, the
    check spans every appointment tied to that CRM event.

    Falls back to a per-appointment check for legacy rows that predate
    auto-promotion. Falls back to ``False`` on a detached appointment so
    a template never errors on the absence of session context.
    """
    sess = object_session(appt)
    if sess is None:
        return False
    base = sess.query(AppointmentEnrichmentResponse.id).filter(
        AppointmentEnrichmentResponse.submitted_at.is_not(None),
    )
    if appt.crm_event_id is None:
        q = base.filter(AppointmentEnrichmentResponse.appointment_id == appt.id)
    else:
        q = base.join(
            Appointment,
            Appointment.id == AppointmentEnrichmentResponse.appointment_id,
        ).filter(Appointment.crm_event_id == appt.crm_event_id)
    return q.first() is not None




@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str


def _format_slot(appt: Appointment) -> str:
    # Show in shop timezone — appointments table stores TIMESTAMPTZ so we let
    # the system locale handle weekday/month names.
    try:
        from zoneinfo import ZoneInfo
        local = appt.slot_start_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = appt.slot_start_at
    return local.strftime("%A, %B %-d at %-I:%M %p")


def _format_event_date(appt: Appointment) -> str:
    if not appt.event_date:
        return "Not shared yet"
    return appt.event_date.strftime("%B %-d, %Y")


def _days_until_event(appt: Appointment) -> int | None:
    if not appt.event_date:
        return None
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(APP_TIMEZONE)).date()
    except Exception:  # pragma: no cover
        today = datetime.now().date()
    return (appt.event_date - today).days


def _customer_name(appt: Appointment) -> str:
    # The parent is the booker and the email recipient. Fall back to the
    # celebrant's first name for historical rows that predate parent
    # capture.
    parent = (appt.parent_first_name or "").strip()
    if parent:
        return parent
    return (appt.celebrant_first_name or "").strip() or "there"


def _full_name(appt: Appointment) -> str:
    # The celebrant's last name is the parent's last name in the new
    # flow; historical rows stored it directly on the appointment.
    last = (appt.parent_last_name or appt.celebrant_last_name or "").strip()
    return " ".join(
        filter(None, [appt.celebrant_first_name, last])
    ).strip() or "(no name)"


def _party_label(appt: Appointment) -> str:
    return _PARTY_LABEL.get(appt.party_size_bucket, appt.party_size_bucket)


def _format_money(cents: int | None) -> str:
    value = int(cents or 0)
    sign = "-" if value < 0 else ""
    dollars, remainder = divmod(abs(value), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def _payment_method_label(method: str | None) -> str:
    labels = {
        "cash": "Cash",
        "check": "Check",
        "card": "Card",
        "transfer": "Bank transfer",
        "zelle": "Zelle",
        "other": "Other",
    }
    return labels.get((method or "").lower(), (method or "Payment").title())


def _arrival_tips(appt: Appointment) -> list[str]:
    # Static list on purpose. The "Things to remember" section in the
    # booking confirmation slices index 0 (the inspiration-photos line is
    # covered by the Boutique Experience profile CTA above it); the
    # reminder template uses the full list. Dynamic per-party / per-event-
    # date variants were removed because they read more like reassurance
    # than reminders and stacked bullets undermined the brief feel.
    return [
        "Bring your favorite inspiration photos, color ideas, or theme details.",
        "Wear easy-to-change clothes and bring shoes with a similar heel height if you have them.",
        "Plan for your first visit to be relaxed, focused, and celebrant-led.",
    ]


def _appointment_details(appt: Appointment) -> list[tuple[str, str]]:
    return [
        ("When", f"{_format_slot(appt)} ({APP_TIMEZONE})"),
        ("Where", f"Bella's XV boutique, {_BOUTIQUE_ADDRESS}"),
        ("Party", _party_label(appt)),
        ("Event date", _format_event_date(appt)),
        ("Confirmation", format_confirmation_code(appt.confirmation_code)),
    ]


def _source_summary(appt: Appointment) -> str:
    source_bits = []
    if appt.utm_source:
        source_bits.append(f"source={appt.utm_source}")
    if appt.utm_medium:
        source_bits.append(f"medium={appt.utm_medium}")
    if appt.utm_campaign:
        source_bits.append(f"campaign={appt.utm_campaign}")
    if appt.utm_content:
        source_bits.append(f"content={appt.utm_content}")
    if appt.utm_term:
        source_bits.append(f"term={appt.utm_term}")
    if appt.fbclid:
        source_bits.append("fbclid=yes")
    if appt.gclid:
        source_bits.append("gclid=yes")
    if appt.msclkid:
        source_bits.append("msclkid=yes")
    return " · ".join(source_bits) if source_bits else "direct / organic"


def _engagement_summary(appt: Appointment) -> str:
    parts = []
    if appt.device_type:
        parts.append(appt.device_type)
    if appt.time_on_widget_ms is not None:
        parts.append(f"{round(appt.time_on_widget_ms / 1000)}s in widget")
    if appt.interaction_count is not None:
        parts.append(f"{appt.interaction_count} interactions")
    if appt.steps_completed is not None:
        parts.append(f"{appt.steps_completed} steps")
    if appt.bot_suspected:
        parts.append("bot suspected")
    return " · ".join(parts) if parts else "no engagement metadata"


def _journey_summary(appt: Appointment) -> str:
    journey = appt.user_journey if isinstance(appt.user_journey, list) else []
    labels = []
    for item in journey[-5:]:
        if not isinstance(item, dict):
            continue
        label = item.get("step") or item.get("event") or item.get("name")
        if label:
            labels.append(str(label))
    return " -> ".join(labels) if labels else "not captured"


def _html_button(label: str, href: str, *, secondary: bool = False) -> str:
    bg = "#FFFFFF" if secondary else "#A7616F"
    color = "#A7616F" if secondary else "#FFFFFF"
    border = "1px solid #E8D8DD" if secondary else "1px solid #A7616F"
    return (
        f"<a href=\"{escape(href)}\" style=\"display:inline-block; "
        f"background:{bg}; color:{color}; border:{border}; padding:12px 18px; "
        f"border-radius:8px; text-decoration:none; font-weight:700; "
        f"margin:0 8px 8px 0;\">{escape(label)}</a>"
    )


def _details_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<tr><td style=\"padding:10px 12px; color:#7A6A6F; font-size:13px; "
        f"border-bottom:1px solid #F1E7EA; width:34%;\">{escape(label)}</td>"
        f"<td style=\"padding:10px 12px; border-bottom:1px solid #F1E7EA;\">"
        f"{escape(value)}</td></tr>"
        for label, value in rows
    )
    return (
        "<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\" "
        "style=\"width:100%; border-collapse:collapse; background:#FFF8FA; "
        "border:1px solid #F1E1E5; border-radius:10px; overflow:hidden;\">"
        f"{body}</table>"
    )


def _bullet_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _wrap_html(body_html: str, *, preheader: str = "") -> str:
    hidden_preheader = (
        f"<div style=\"display:none; max-height:0; overflow:hidden; opacity:0;\">"
        f"{escape(preheader)}</div>"
        if preheader
        else ""
    )
    return f"""<!doctype html>
<html><body style="margin:0; padding:0; background:#FAF4F6; font-family:-apple-system, Segoe UI, sans-serif; color:#2A1B1F; line-height:1.5;">
{hidden_preheader}
<div style="max-width:620px; margin:0 auto; padding:28px 16px;">
<div style="background:#FFFFFF; border:1px solid #F0E2E6; border-radius:16px; overflow:hidden;">
<div style="background:#FFFFFF; padding:28px 24px 12px 24px; text-align:center; border-bottom:1px solid #F1E1E5;">
<img src="cid:bellas-logo" alt="Bella's XV" width="220" style="display:inline-block; max-width:220px; width:220px; height:auto; border:0; outline:none; text-decoration:none;">
</div>
<div style="padding:24px;">
{body_html}
</div>
</div>
<p style="color:#7A6A6F; font-size:12px; text-align:center;">Bella's XV · {_BOUTIQUE_ADDRESS} · {_BOUTIQUE_PHONE}</p>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Customer: booking confirmation
# ---------------------------------------------------------------------------


def render_booking_confirmation(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)
    party = _party_label(appt)
    resched = reschedule_url(appt)
    cancel = cancel_url(appt)
    profile_link = enrichment_url(appt)
    # Drop the first arrival tip ("Bring your inspiration photos…") — it's
    # superseded by the Boutique Experience Profile CTA that now sits right
    # below the details table. The remaining tips read as a quick reminder.
    tips = _arrival_tips(appt)[1:]
    event_date = _format_event_date(appt)
    needs_profile = not is_boutique_profile_attached(appt)

    profile_text = (
        f"{_BE_CTA_LABEL}.\n"
        f"{_BE_INTRO}\n"
        f"  {profile_link}\n\n"
        if needs_profile
        else ""
    )
    profile_html = (
        f"<h2 style=\"font-size:18px; margin-top:24px;\">{escape(_BE_CTA_LABEL)}</h2>"
        f"<p style=\"margin:0 0 12px 0;\">{escape(_BE_INTRO)}</p>"
        f"<p>{_html_button(_BE_CTA_LABEL, profile_link)}</p>"
        if needs_profile
        else ""
    )

    subject = f"You're booked at Bella's XV — {slot}"
    text = (
        f"Hi {name},\n\n"
        f"Your initial consultation is confirmed.\n\n"
        f"  When: {slot} ({APP_TIMEZONE})\n"
        f"  Where: Bella's XV boutique, {_BOUTIQUE_ADDRESS}\n"
        f"  Party: {party}\n"
        f"  Event date: {event_date}\n"
        f"  Confirmation: {format_confirmation_code(appt.confirmation_code)}\n\n"
        + profile_text
        + f"Things to remember:\n"
        + "".join(f"  - {tip}\n" for tip in tips)
        + "\n"
        f"Need to change your time? Reschedule: {resched}\n"
        f"Need to cancel? {cancel}\n\n"
        f"We can't wait to meet you.\n"
        f"The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">You're booked.</h1>"
        f"<p>Hi {escape(name)}, your initial consultation is confirmed.</p>"
        + _details_table(_appointment_details(appt))
        + profile_html
        + f"<h2 style=\"font-size:18px; margin-top:24px;\">Things to remember</h2>"
        + _bullet_list(tips)
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Reschedule", resched, secondary=True)
        + _html_button("Cancel", cancel, secondary=True)
        + "</p>"
        + f"<p>We can't wait to meet you.</p>"
        ,
        preheader=f"Your appointment is confirmed for {slot}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: thank-you after an attended appointment
# ---------------------------------------------------------------------------


def render_booking_thank_you(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    subject = "Thank you for visiting Bella's XV"
    value_line = (
        f"We noted today's visit at {_format_money(appt.purchase_value_cents)}."
        if appt.purchase_value_cents
        else "We loved helping you explore dresses and ideas for the celebration."
    )
    text = (
        f"Hi {name},\n\n"
        f"Thank you for spending time with us at Bella's XV.\n\n"
        f"{value_line}\n\n"
        f"If you have questions about anything you tried on, reply to this "
        f"email or call the boutique. We are happy to help with next steps.\n\n"
        f"The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Thank you for visiting</h1>"
        f"<p>Hi {escape(name)}, thank you for spending time with us at "
        f"Bella's XV.</p>"
        f"<p>{escape(value_line)}</p>"
        f"<p>If you have questions about anything you tried on, reply to "
        f"this email or call the boutique. We are happy to help with next "
        f"steps.</p>"
        f"<p>The Bella's XV team</p>",
        preheader="Thank you for visiting Bella's XV.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: no-show follow-up
# ---------------------------------------------------------------------------


def render_booking_no_show_followup(
    appt: Appointment,
    *,
    booking_url: str | None = None,
) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)
    link = (booking_url or WIDGET_PUBLIC_BASE_URL or "").rstrip("/")
    subject = "We missed you at Bella's XV"
    text = (
        f"Hi {name},\n\n"
        f"We missed you for your Bella's XV consultation on {slot}.\n\n"
        f"If life got busy, you can book a new time here:\n"
        f"    {link}\n\n"
        f"You can also reply to this email or call the boutique and we will "
        f"help find a better time.\n\n"
        f"The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">We missed you</h1>"
        f"<p>Hi {escape(name)}, we missed you for your Bella's XV "
        f"consultation on {escape(slot)}.</p>"
        f"<p>If life got busy, you can book a new time when you're ready.</p>"
        f"<p style=\"margin-top:22px;\">"
        + _html_button("Book a new time", link)
        + "</p>"
        f"<p>You can also reply to this email or call the boutique and we "
        f"will help find a better time.</p>"
        f"<p>The Bella's XV team</p>",
        preheader="We missed you at your Bella's XV consultation.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: payment receipt
# ---------------------------------------------------------------------------


def render_payment_receipt(
    *,
    contact,
    payment,
    receipt_url: str | None = None,
) -> RenderedEmail:
    name = (
        (getattr(contact, "first_name", None) or getattr(contact, "display_name", None) or "")
        .strip()
        .split(" ")[0]
        or "there"
    )
    number = payment.payment_number or f"payment #{payment.id}"
    amount = _format_money(payment.amount_cents)
    method = _payment_method_label(payment.method)
    paid_on = (
        payment.payment_date.strftime("%B %-d, %Y")
        if payment.payment_date is not None
        else "today"
    )
    applied = _format_money(payment.applied_cents)
    unapplied = _format_money(payment.unapplied_cents)
    subject = f"Payment received at Bella's XV: {amount}"

    allocation_line = (
        f"We applied {applied} to your invoice balance."
        if int(payment.applied_cents or 0) > 0
        else "We recorded this payment on your account."
    )
    unapplied_line = (
        f" The remaining {unapplied} is on your account as unapplied credit."
        if int(payment.unapplied_cents or 0) > 0
        else ""
    )
    reference_line = (
        f"\nReference: {payment.transaction_reference}\n"
        if payment.transaction_reference
        else ""
    )

    text = (
        f"Hi {name},\n\n"
        f"We received your {method.lower()} payment of {amount} on {paid_on}.\n\n"
        f"{allocation_line}{unapplied_line}\n"
        f"{reference_line}\n"
        + (
            f"You can view the receipt here:\n    {receipt_url}\n\n"
            if receipt_url
            else ""
        )
        + f"Thank you.\n"
        f"The Bella's XV team\n"
    )
    receipt_button = (
        f"<p style=\"margin-top:22px;\">{_html_button('View receipt', receipt_url)}</p>"
        if receipt_url
        else ""
    )
    reference_html = (
        f"<p style=\"color:#7A6A6F; font-size:13px;\">Reference: "
        f"{escape(payment.transaction_reference)}</p>"
        if payment.transaction_reference
        else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Payment received</h1>"
        f"<p>Hi {escape(name)}, we received your {escape(method.lower())} "
        f"payment of <strong>{escape(amount)}</strong> on {escape(paid_on)}.</p>"
        + _details_table(
            [
                ("Receipt", number),
                ("Amount", amount),
                ("Method", method),
                ("Applied", applied),
                ("Unapplied credit", unapplied),
            ]
        )
        + f"<p style=\"margin-top:22px;\">{escape(allocation_line + unapplied_line)}</p>"
        + reference_html
        + receipt_button
        + f"<p>Thank you.</p>",
        preheader=f"We received your Bella's XV payment of {amount}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Internal: new booking notification
# ---------------------------------------------------------------------------


def render_internal_new_booking(appt: Appointment) -> RenderedEmail:
    slot = _format_slot(appt)
    name = _full_name(appt)
    source = _source_summary(appt)
    engagement = _engagement_summary(appt)
    journey = _journey_summary(appt)

    subject = f"New booking: {name} — {slot}"
    text = (
        f"New appointment booked.\n\n"
        f"  Quinceañera: {name}\n"
        f"  When: {slot} ({APP_TIMEZONE})\n"
        f"  Party: {_party_label(appt)}\n"
        f"  Phone: {appt.phone_e164 or appt.phone}\n"
        f"  Email: {appt.email}\n"
        f"  Event date: {appt.event_date.isoformat() if appt.event_date else '(not provided)'}\n"
        f"  Source: {source}\n"
        f"  Engagement: {engagement}\n"
        f"  Recent journey: {journey}\n"
        f"  Page: {appt.page_url or '(not captured)'}\n"
        f"  Confirmation: {format_confirmation_code(appt.confirmation_code)}\n"
        f"  Customer note: {appt.customer_note or '(none)'}\n"
        f"  Bot suspected: {'yes' if appt.bot_suspected else 'no'}\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; margin-top:0;\">New booking</h1>"
        + _details_table(
            [
                ("Quinceanera", name),
                ("When", f"{slot} ({APP_TIMEZONE})"),
                ("Contact", f"{appt.phone_e164 or appt.phone} · {appt.email}"),
                ("Party", _party_label(appt)),
                ("Event date", _format_event_date(appt)),
                ("Source", source),
                ("Engagement", engagement),
                ("Recent journey", journey),
                ("Page", appt.page_url or "not captured"),
                ("Confirmation", format_confirmation_code(appt.confirmation_code)),
            ]
        )
        + (f"<p><em>{escape(appt.customer_note)}</em></p>" if appt.customer_note else "")
        + (
            "<p style=\"color:#B00020; font-weight:700;\">Bot or low-quality behavior suspected.</p>"
            if appt.bot_suspected
            else ""
        ),
        preheader=f"{name} booked {slot}. Source: {source}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: enrichment survey invitation (T+2min)
# ---------------------------------------------------------------------------


def render_enrichment_invitation(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)
    link = enrichment_url(appt)
    days = _days_until_event(appt)
    timing = (
        f"Your event date is {_format_event_date(appt)}, so there are {days} days to plan."
        if days is not None and days >= 0
        else "Your event date is not shared yet. You can add it inside your profile."
    )

    subject = _BE_CTA_LABEL
    text = (
        f"Hi {name},\n\n"
        f"We can't wait to meet you on {slot}.\n\n"
        f"{timing}\n\n"
        f"{_BE_INTRO}\n\n"
        f"  {link}\n\n"
        f"This is optional. Your appointment is already confirmed either way.\n\n"
        f"The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">{escape(_BE_CTA_LABEL)}</h1>"
        f"<p>Hi {escape(name)}, we can't wait to meet you on {escape(slot)}.</p>"
        f"<p>{escape(timing)}</p>"
        f"<p>{escape(_BE_INTRO)}</p>"
        f"<p>{_html_button(_BE_CTA_LABEL, link)}</p>"
        f"<p style=\"color:#7A6A6F; font-size:13px;\">Optional. Your appointment is already confirmed.</p>"
        ,
        preheader=f"{_BE_INTRO}",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: appointment reminder (T-24h)
# ---------------------------------------------------------------------------


def render_reminder(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)
    resched = reschedule_url(appt)
    cancel = cancel_url(appt)
    profile_link = enrichment_url(appt)
    tips = _arrival_tips(appt)[:3]
    needs_profile = not is_boutique_profile_attached(appt)

    subject = f"See you tomorrow at Bella's XV — {slot}"

    profile_text = (
        f"\nOne thing to do before you arrive:\n"
        f"{_BE_CTA_LABEL}.\n"
        f"{_BE_INTRO}\n"
        f"  {profile_link}\n"
        if needs_profile
        else ""
    )
    text = (
        f"Hi {name},\n\n"
        f"Quick reminder: your fitting is {slot} ({APP_TIMEZONE}).\n\n"
        f"  Bella's XV boutique, {_BOUTIQUE_ADDRESS}\n"
        f"  Confirmation: {format_confirmation_code(appt.confirmation_code)}\n\n"
        f"Quick prep:\n"
        + "".join(f"  - {tip}\n" for tip in tips)
        + profile_text
        + "\n"
        f"Need to change your time? {resched}\n"
        f"Can't make it? {cancel}\n\n"
        f"The Bella's XV team\n"
    )

    profile_html = (
        f"<h2 style=\"font-size:18px; margin-top:24px;\">{escape(_BE_CTA_LABEL)}</h2>"
        f"<p style=\"margin:0 0 12px 0;\">{escape(_BE_INTRO)}</p>"
        f"<p>{_html_button(_BE_CTA_LABEL, profile_link)}</p>"
        if needs_profile
        else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">See you tomorrow</h1>"
        f"<p>Hi {escape(name)}, just a reminder that your fitting is <strong>{escape(slot)}</strong>.</p>"
        + _details_table(_appointment_details(appt))
        + f"<h2 style=\"font-size:18px; margin-top:24px;\">Quick prep</h2>"
        + _bullet_list(tips)
        + profile_html
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Reschedule", resched, secondary=True)
        + _html_button("Cancel", cancel, secondary=True)
        + "</p>"
        ,
        preheader=f"Reminder: your Bella's XV fitting is {slot}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: cancellation confirmation
# ---------------------------------------------------------------------------


def render_cancellation_confirmation(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)

    subject = f"Your Bella's XV appointment is cancelled — {slot}"
    text = (
        f"Hi {name},\n\n"
        f"Your appointment for {slot} has been cancelled.\n\n"
        f"If you'd like to rebook, our calendar is at "
        f"https://shopbellasxv.com/#book.\n\n"
        f"— The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; margin-top:0;\">Cancelled</h1>"
        f"<p>Hi {escape(name)}, your appointment for <strong>{escape(slot)}</strong> has been cancelled.</p>"
        f"<p>{_html_button('Find another time', 'https://shopbellasxv.com/#book')}</p>"
        ,
        preheader=f"Your appointment for {slot} has been cancelled.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Customer: reschedule confirmation
# ---------------------------------------------------------------------------


def render_reschedule_confirmation(appt: Appointment) -> RenderedEmail:
    name = _customer_name(appt)
    slot = _format_slot(appt)
    resched = reschedule_url(appt)
    cancel = cancel_url(appt)

    subject = f"Your Bella's XV appointment is now {slot}"
    text = (
        f"Hi {name},\n\n"
        f"Your appointment has been rescheduled to {slot} ({APP_TIMEZONE}).\n\n"
        f"  Bella's XV boutique — 7723 Guilbeau Rd #101, San Antonio, TX 78250\n"
        f"  Confirmation: {format_confirmation_code(appt.confirmation_code)}\n\n"
        f"Need to change again? {resched}\n"
        f"Need to cancel? {cancel}\n\n"
        f"— The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">Rescheduled</h1>"
        f"<p>Hi {escape(name)}, your appointment is now <strong>{escape(slot)}</strong>.</p>"
        + _details_table(_appointment_details(appt))
        + f"<p>{_html_button('Reschedule again', resched)}{_html_button('Cancel', cancel, secondary=True)}</p>"
        ,
        preheader=f"Your Bella's XV appointment moved to {slot}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# SMS templates (interface stub for v1; real send arrives with Twilio)
# ---------------------------------------------------------------------------


def render_sms_confirmation(appt: Appointment) -> SmsMessagePayload:
    return SmsMessagePayload(
        to=appt.phone_e164 or appt.phone,
        body=(
            f"Bella's XV: You're booked for {_format_slot(appt)}. "
            f"Confirmation {format_confirmation_code(appt.confirmation_code)}. Reply STOP to opt out."
        ),
    )


def render_sms_reminder(appt: Appointment) -> SmsMessagePayload:
    return SmsMessagePayload(
        to=appt.phone_e164 or appt.phone,
        body=(
            f"Bella's XV: see you tomorrow at {_format_slot(appt)}. "
            f"7723 Guilbeau Rd #101. Reply STOP to opt out."
        ),
    )


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


EMAIL_RENDERERS = {
    "booking_confirmation": render_booking_confirmation,
    "internal_new_booking": render_internal_new_booking,
    "enrichment_invitation": render_enrichment_invitation,
    "reminder": render_reminder,
    "reschedule_confirmation": render_reschedule_confirmation,
    "cancellation_confirmation": render_cancellation_confirmation,
}

SMS_RENDERERS = {
    "sms_confirmation": render_sms_confirmation,
    "sms_reminder": render_sms_reminder,
}


def render_email(kind: str, appt: Appointment, recipient: str) -> EmailMessagePayload:
    renderer = EMAIL_RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"unknown email kind: {kind}")
    rendered = renderer(appt)
    reply_to = SMTP_FROM_EMAIL or None
    return EmailMessagePayload(
        to=recipient,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
        reply_to=reply_to,
    )


def render_sms(kind: str, appt: Appointment, recipient: str) -> SmsMessagePayload:
    renderer = SMS_RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"unknown sms kind: {kind}")
    msg = renderer(appt)
    # Recipient column wins (in case admin overrode the phone for testing).
    return SmsMessagePayload(to=recipient, body=msg.body)


# ---------------------------------------------------------------------------
# Staff schedule notifications.
# These are fixture-first renderers for the staff notification catalog.
# The later routing pass will decide recipients and preference handling.
# ---------------------------------------------------------------------------


def _schedule_local(dt: datetime) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        return dt


def _format_shift_window(starts_at: datetime, ends_at: datetime) -> str:
    start = _schedule_local(starts_at)
    end = _schedule_local(ends_at)
    if start.date() == end.date():
        return (
            start.strftime("%a, %b %-d, %-I:%M %p")
            + " to "
            + end.strftime("%-I:%M %p")
        )
    return (
        start.strftime("%a, %b %-d, %-I:%M %p")
        + " to "
        + end.strftime("%a, %b %-d, %-I:%M %p")
    )


def _format_schedule_week(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    return f"{week_start.strftime('%b %-d')} to {week_end.strftime('%b %-d')}"


def _shift_title(shift: dict) -> str:
    return (shift.get("title") or shift.get("role") or "Boutique shift").strip()


def _shift_location(shift: dict) -> str:
    return (shift.get("location") or "Bella's XV boutique").strip()


def _shift_notes(shift: dict) -> str:
    return (shift.get("notes") or "").strip()


def _shift_rows(shifts: list[dict]) -> list[tuple[str, str]]:
    rows = []
    for shift in shifts:
        rows.append(
            (
                _format_shift_window(shift["starts_at"], shift["ends_at"]),
                f"{_shift_title(shift)} at {_shift_location(shift)}",
            )
        )
    return rows


def render_schedule_published(
    *,
    staff_user,
    week_start: date,
    shifts: list[dict],
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    week = _format_schedule_week(week_start)
    count = len(shifts)
    shift_word = "shift" if count == 1 else "shifts"
    subject = f"Your Bella's XV schedule for {week}"
    shift_lines = "\n".join(
        f"  - {_format_shift_window(s['starts_at'], s['ends_at'])}: "
        f"{_shift_title(s)} at {_shift_location(s)}"
        + (f" ({_shift_notes(s)})" if _shift_notes(s) else "")
        for s in shifts
    )
    text = (
        f"Hi {name},\n\n"
        f"Your Bella's XV schedule for {week} was published with "
        f"{count} {shift_word}.\n\n"
        f"{shift_lines or '  - No shifts scheduled this week.'}\n\n"
        f"If something looks off, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your schedule was published</h1>"
        f"<p>Hi {escape(name)}, your Bella's XV schedule for "
        f"<strong>{escape(week)}</strong> was published with {count} "
        f"{escape(shift_word)}.</p>"
        + (
            _details_table(_shift_rows(shifts))
            if shifts
            else "<p>No shifts scheduled this week.</p>"
        )
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If something looks off, let the boutique owner know.</p>",
        preheader=f"Your schedule for {week} is ready.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_shift_added(*, staff_user, shift: dict) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    window = _format_shift_window(shift["starts_at"], shift["ends_at"])
    title = _shift_title(shift)
    location = _shift_location(shift)
    notes = _shift_notes(shift)
    subject = f"New Bella's XV shift: {window}"
    text = (
        f"Hi {name},\n\n"
        f"A shift was added to your Bella's XV schedule.\n\n"
        f"  When: {window}\n"
        f"  Shift: {title}\n"
        f"  Where: {location}\n"
        + (f"  Note: {notes}\n" if notes else "")
        + "\nIf this does not look right, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Shift added</h1>"
        f"<p>Hi {escape(name)}, a shift was added to your Bella's XV "
        f"schedule.</p>"
        + _details_table(
            [
                ("When", window),
                ("Shift", title),
                ("Where", location),
            ]
            + ([("Note", notes)] if notes else [])
        )
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If this does not look right, let the boutique owner know.</p>",
        preheader=f"New shift added for {window}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_shift_edited(
    *,
    staff_user,
    old_shift: dict,
    new_shift: dict,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    old_window = _format_shift_window(old_shift["starts_at"], old_shift["ends_at"])
    new_window = _format_shift_window(new_shift["starts_at"], new_shift["ends_at"])
    subject = f"Your Bella's XV shift was updated"
    text = (
        f"Hi {name},\n\n"
        f"One of your Bella's XV shifts was updated.\n\n"
        f"  Previous: {old_window}, {_shift_title(old_shift)} at "
        f"{_shift_location(old_shift)}\n"
        f"  Updated:  {new_window}, {_shift_title(new_shift)} at "
        f"{_shift_location(new_shift)}\n"
        + (
            f"  Note: {_shift_notes(new_shift)}\n"
            if _shift_notes(new_shift)
            else ""
        )
        + "\nIf this does not look right, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Shift updated</h1>"
        f"<p>Hi {escape(name)}, one of your Bella's XV shifts was updated.</p>"
        + _details_table(
            [
                ("Previous", f"{old_window}, {_shift_title(old_shift)}"),
                ("Updated", f"{new_window}, {_shift_title(new_shift)}"),
                ("Where", _shift_location(new_shift)),
            ]
            + ([("Note", _shift_notes(new_shift))] if _shift_notes(new_shift) else [])
        )
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If this does not look right, let the boutique owner know.</p>",
        preheader=f"Your shift changed to {new_window}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_shift_deleted(*, staff_user, shift: dict) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    window = _format_shift_window(shift["starts_at"], shift["ends_at"])
    title = _shift_title(shift)
    subject = f"Shift removed from your Bella's XV schedule"
    text = (
        f"Hi {name},\n\n"
        f"A shift was removed from your Bella's XV schedule.\n\n"
        f"  Removed: {window}, {title} at {_shift_location(shift)}\n\n"
        f"If you were expecting to work this shift, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Shift removed</h1>"
        f"<p>Hi {escape(name)}, a shift was removed from your Bella's XV "
        f"schedule.</p>"
        + _details_table(
            [
                ("Removed", window),
                ("Shift", title),
                ("Where", _shift_location(shift)),
            ]
        )
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If you were expecting to work this shift, let the boutique owner know.</p>",
        preheader=f"Shift removed: {window}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Generic staff notice (Scheduling Phase 2 shift requests).
# ---------------------------------------------------------------------------


def render_staff_simple_notice(
    *,
    staff_user,
    headline: str,
    message: str,
    details: list | None = None,
    preheader: str | None = None,
) -> RenderedEmail:
    """A lightweight staff email built from a headline + message + optional
    detail rows. The shift-request flow (cover/drop) puts the copy in the
    notification payload so the dispatcher renders without a per-kind
    template — copy lives at the emitting call site where the context is."""
    name = staff_user.full_name or staff_user.username
    detail_rows = [
        (str(label), str(value)) for label, value in (details or [])
    ]
    subject = headline
    text = (
        f"Hi {name},\n\n"
        f"{message}\n"
        + (
            "\n" + "".join(f"  {lbl}: {val}\n" for lbl, val in detail_rows)
            if detail_rows
            else ""
        )
        + "\nIf this does not look right, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">{escape(headline)}</h1>"
        f"<p>Hi {escape(name)}, {escape(message)}</p>"
        + (_details_table(detail_rows) if detail_rows else "")
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If this does not look right, let the boutique owner know.</p>",
        preheader=preheader or message,
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Staff attendance notifications.
# ---------------------------------------------------------------------------


def render_staff_missing_clock_out(
    *,
    staff_user,
    shift: dict,
    clocked_in_at: datetime,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    shift_window = _format_shift_window(shift["starts_at"], shift["ends_at"])
    clock_in = _schedule_local(clocked_in_at).strftime("%a, %b %-d at %-I:%M %p")
    subject = "Missing clock-out on your Bella's XV shift"
    text = (
        f"Hi {name},\n\n"
        f"We have you clocked in for your Bella's XV shift, but we do not "
        f"have a matching clock-out.\n\n"
        f"  Shift: {shift_window}\n"
        f"  Clocked in: {clock_in}\n\n"
        f"Please open the sales portal or talk with the boutique owner so "
        f"your hours can be corrected.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Missing clock-out</h1>"
        f"<p>Hi {escape(name)}, we have you clocked in for your Bella's XV "
        f"shift, but we do not have a matching clock-out.</p>"
        + _details_table(
            [
                ("Shift", shift_window),
                ("Clocked in", clock_in),
            ]
        )
        + f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"Please open the sales portal or talk with the boutique owner so "
        f"your hours can be corrected.</p>",
        preheader="Your shift is missing a clock-out.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_admin_missing_clock_out(
    *,
    staff_user,
    shift: dict,
    clocked_in_at: datetime,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    shift_window = _format_shift_window(shift["starts_at"], shift["ends_at"])
    clock_in = _schedule_local(clocked_in_at).strftime("%a, %b %-d at %-I:%M %p")
    subject = f"Missing clock-out: {name}"
    text = (
        f"{name} has a shift flagged for missing clock-out.\n\n"
        f"  Shift: {shift_window}\n"
        f"  Clocked in: {clock_in}\n\n"
        f"Open Attendance Review to confirm or correct the hours.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Missing clock-out</h1>"
        f"<p>{escape(name)} has a shift flagged for missing clock-out.</p>"
        + _details_table(
            [
                ("Staffer", name),
                ("Shift", shift_window),
                ("Clocked in", clock_in),
            ]
        )
        + "<p>Open Attendance Review to confirm or correct the hours.</p>",
        preheader=f"{name} is missing a clock-out.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Phase 8 Slice C: time-off notifications.
#
# Two emails:
#
#   - `render_time_off_requested_to_owner`: stylist filed a request,
#     owner needs to review.
#   - `render_time_off_decided_to_staff`: owner approved or denied,
#     stylist needs the verdict.
#
# These render functions take TimeOffRequest + User dataclasses
# directly (instead of going through the appointment-tied dispatch
# table) because time-off has no Appointment to anchor to. The
# Slice C routers call them and pass the result to
# `email_transport.get_email_transport().send(...)`.
# ---------------------------------------------------------------------------


def _format_time_off_window_text(
    request_starts_at: datetime, request_ends_at: datetime
) -> str:
    from services.business_time import to_business_local

    start_local = to_business_local(request_starts_at)
    end_local = to_business_local(request_ends_at)
    start_is_day_start = (
        start_local.hour == 0
        and start_local.minute == 0
        and start_local.second == 0
    )
    end_is_day_end = (
        end_local.hour == 23
        and end_local.minute == 59
        and end_local.second >= 0
    )
    if start_is_day_start and end_is_day_end:
        if start_local.date() == end_local.date():
            return start_local.strftime("All day on %a %b %-d")
        return (
            start_local.strftime("All day %a %b %-d")
            + " through "
            + end_local.strftime("%a %b %-d")
        )
    if start_local.date() == end_local.date():
        return (
            start_local.strftime("%a %b %-d, %-I:%M %p")
            + " to "
            + end_local.strftime("%-I:%M %p")
        )
    return (
        start_local.strftime("%a %b %-d, %-I:%M %p")
        + " through "
        + end_local.strftime("%a %b %-d, %-I:%M %p")
    )


def render_time_off_requested_to_owner(
    *,
    request,
    stylist,
) -> RenderedEmail:
    name = stylist.full_name or stylist.username
    window = _format_time_off_window_text(request.starts_at, request.ends_at)
    reason = (request.reason or "").strip()
    subject = f"Time-off request from {name} — {window}"
    text = (
        f"{name} just filed a time-off request.\n\n"
        f"  Window: {window}\n"
        f"  Reason: {reason or '(none provided)'}\n\n"
        "Open the admin time-off queue to approve or deny.\n"
    )
    safe_reason = (
        f"<p><strong>Reason:</strong> {escape(reason)}</p>"
        if reason
        else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">New time-off request</h1>"
        f"<p>{escape(name)} filed a time-off request for <strong>{escape(window)}</strong>.</p>"
        + safe_reason
        + "<p>Open the admin time-off queue to approve or deny.</p>",
        preheader=f"{name} requested time off",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_time_off_decided_to_staff(
    *,
    request,
    stylist,
    decided_by,
) -> RenderedEmail:
    name = stylist.full_name or stylist.username
    decided_by_name = (
        decided_by.full_name or decided_by.username if decided_by else "the owner"
    )
    window = _format_time_off_window_text(request.starts_at, request.ends_at)
    notes = (request.decision_notes or "").strip()

    if request.status == "approved":
        verb = "approved"
        body_text = (
            "Your time off is locked in — your schedule will reflect "
            "those days as off."
        )
    else:
        verb = "denied"
        body_text = (
            "Your time off was not approved. Reach out if you'd like "
            "to talk through it."
        )

    subject = f"Your time-off request — {verb}"
    text = (
        f"Hi {name},\n\n"
        f"{decided_by_name} {verb} your time-off request for {window}.\n\n"
        f"{body_text}\n"
        + (f"\nNote: {notes}\n" if notes else "")
    )
    safe_notes = (
        f"<p><strong>Note:</strong> {escape(notes)}</p>" if notes else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; color:#A7616F; margin-top:0;\">Time-off {escape(verb)}</h1>"
        f"<p>Hi {escape(name)}, {escape(decided_by_name)} {escape(verb)} your request for "
        f"<strong>{escape(window)}</strong>.</p>"
        f"<p>{escape(body_text)}</p>"
        + safe_notes,
        preheader=f"Your time-off was {verb}",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_time_off_amended_to_staff(
    *,
    request,
    stylist,
    amended_by,
    previous_starts_at: datetime,
    previous_ends_at: datetime,
    amendment_notes: str | None = None,
) -> RenderedEmail:
    name = stylist.full_name or stylist.username
    amended_by_name = (
        amended_by.full_name or amended_by.username if amended_by else "the owner"
    )
    old_window = _format_time_off_window_text(previous_starts_at, previous_ends_at)
    new_window = _format_time_off_window_text(request.starts_at, request.ends_at)
    notes = (amendment_notes or "").strip()

    subject = "Your time-off request was updated"
    text = (
        f"Hi {name},\n\n"
        f"{amended_by_name} updated the dates or times on your time-off "
        f"request.\n\n"
        f"  Previous: {old_window}\n"
        f"  Updated:  {new_window}\n"
        + (f"\nNote: {notes}\n" if notes else "")
        + "\nThe request is still pending until the boutique owner approves or denies it.\n"
    )
    safe_notes = (
        f"<p><strong>Note:</strong> {escape(notes)}</p>" if notes else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Time-off request updated</h1>"
        f"<p>Hi {escape(name)}, {escape(amended_by_name)} updated the "
        f"dates or times on your time-off request.</p>"
        + _details_table(
            [
                ("Previous", old_window),
                ("Updated", new_window),
            ]
        )
        + safe_notes
        + "<p>The request is still pending until the boutique owner approves "
        "or denies it.</p>",
        preheader="Your time-off request was updated.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Staff auth: PIN reset
# Sent to a sales-portal user after an admin resets their clock-in PIN.
# The set-PIN URL is minted by the wiring layer (see services/password_reset
# for the equivalent password-reset token shape we'll mirror); the renderer
# treats it as an opaque string so we can build copy ahead of the route.
# ---------------------------------------------------------------------------


def render_pin_reset(
    *,
    staff_user,
    set_pin_url: str,
    ttl_minutes: int = 30,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    subject = "Your Bella's XV PIN was reset"
    text = (
        f"Hi {name},\n\n"
        f"An admin just reset the PIN you use to clock in at "
        f"Bella's XV. To pick a new one, open this link within the "
        f"next {ttl_minutes} minutes:\n\n"
        f"    {set_pin_url}\n\n"
        f"If you didn't ask for this and weren't expecting it, "
        f"let the boutique owner know right away.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your PIN was reset</h1>"
        f"<p>Hi {escape(name)}, an admin just reset the PIN you use to "
        f"clock in. Pick a new one within the next {ttl_minutes} minutes.</p>"
        f"<p style=\"margin-top:22px;\">"
        + _html_button("Set a new PIN", set_pin_url)
        + "</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If you didn't ask for this and weren't expecting it, "
        f"let the boutique owner know right away.</p>",
        preheader="Your sales-portal PIN was reset.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Staff auth: password reset request
# Sent when an admin / staff user asks for a password reset from the admin app.
# ---------------------------------------------------------------------------


def render_password_reset_request(
    *,
    user,
    reset_url: str,
    ttl_minutes: int = 30,
) -> RenderedEmail:
    """Forgot-password email. Replaces the plain-text inline renderer
    that lived in services/password_reset.py so the email picks up the
    boutique chrome shared with every other transactional message.
    """
    name = user.full_name or user.username
    subject = "Reset your Bella's XV admin password"
    text = (
        f"Hi {name},\n\n"
        f"We received a request to reset the password for your "
        f"Bella's XV admin account ({user.email}).\n\n"
        f"To set a new password, open this link within the next "
        f"{ttl_minutes} minutes:\n\n"
        f"    {reset_url}\n\n"
        f"If you didn't request a reset, you can ignore this email. "
        f"Your password will stay the same.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Reset your password</h1>"
        f"<p>Hi {escape(name)}, we received a request to reset the "
        f"password for your Bella's XV admin account "
        f"(<code>{escape(user.email)}</code>).</p>"
        f"<p style=\"margin-top:22px;\">"
        + _html_button("Set a new password", reset_url)
        + "</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"This link is good for the next {ttl_minutes} minutes.</p>"
        f"<p style=\"color:#7A6A6F; font-size:13px;\">"
        f"If you didn't request a reset, you can ignore this email. "
        f"Your password will stay the same.</p>",
        preheader="Reset your Bella's XV admin password.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_password_changed(
    *,
    user,
    changed_at: datetime,
) -> RenderedEmail:
    """Post-reset confirmation. Sent immediately after a successful
    password change so the account holder has a tripwire if their
    reset email was compromised. The wiring layer
    (services/password_reset.confirm_reset) passes ``changed_at`` as the
    timestamp the row was committed at; the renderer formats it in shop
    timezone for readability.
    """
    name = user.full_name or user.username
    try:
        from zoneinfo import ZoneInfo
        local = changed_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = changed_at
    when = local.strftime("%A, %B %-d at %-I:%M %p")

    subject = "Your Bella's XV password was changed"
    text = (
        f"Hi {name},\n\n"
        f"The password for your Bella's XV admin account "
        f"({user.email}) was just changed on {when} ({APP_TIMEZONE}).\n\n"
        f"If you made this change, you can ignore this email.\n\n"
        f"If you didn't, contact the boutique owner right away to "
        f"reset your account.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Password changed</h1>"
        f"<p>Hi {escape(name)}, the password for your Bella's XV admin "
        f"account (<code>{escape(user.email)}</code>) was just "
        f"changed on <strong>{escape(when)}</strong> ({APP_TIMEZONE}).</p>"
        f"<p style=\"margin-top:22px;\">If you made this change, you "
        f"can ignore this email.</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If you didn't, contact the boutique owner right away to "
        f"reset your account.</p>",
        preheader="Confirmation that your Bella's XV password was just changed.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Staff auth: welcome / first-time setup
# Sent when an admin creates a new staff user. Role-aware: sales staff get
# a "set your PIN" link to the sales portal, admin / non-sales users get a
# "set your password" link to the admin app. The setup_url + portal_login_url
# are minted by the wiring layer so the same template covers both surfaces
# without baking subdomains into the renderer.
# ---------------------------------------------------------------------------


def render_welcome_new_user(
    *,
    staff_user,
    setup_url: str,
    portal_login_url: str,
    ttl_hours: int = 24,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    is_sales = (staff_user.role or "").lower() == "sales"
    credential = "PIN" if is_sales else "password"
    action_verb = "clock in" if is_sales else "log in"
    portal_label = "sales portal" if is_sales else "admin app"

    subject = "Welcome to Bella's XV"
    text = (
        f"Hi {name},\n\n"
        f"An account was created for you at Bella's XV. To finish "
        f"setting up, pick a {credential} you can use to {action_verb}:\n\n"
        f"    {setup_url}\n\n"
        f"This link is good for the next {ttl_hours} hours. After that, "
        f"ask the boutique owner to send a fresh one.\n\n"
        f"Once your {credential} is set, you can {action_verb} at:\n"
        f"    {portal_login_url}\n\n"
        f"Welcome to the team.\n"
        f"The Bella's XV team\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Welcome to Bella's XV</h1>"
        f"<p>Hi {escape(name)}, an account was created for you on the "
        f"{escape(portal_label)}. To finish setting up, pick a "
        f"{escape(credential)} you can use to {escape(action_verb)}.</p>"
        f"<p style=\"margin-top:22px;\">"
        + _html_button(f"Set your {credential}", setup_url)
        + "</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"This link is good for the next {ttl_hours} hours. After that, "
        f"ask the boutique owner to send a fresh one.</p>"
        f"<p style=\"margin-top:22px;\">Once your {escape(credential)} "
        f"is set, you can {escape(action_verb)} at "
        f"<a href=\"{escape(portal_login_url)}\" style=\"color:#A7616F;\">"
        f"{escape(portal_login_url)}</a>.</p>"
        f"<p style=\"margin-top:22px;\">Welcome to the team.</p>",
        preheader=f"Set your Bella's XV {credential} to get started.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_account_locked(
    *,
    staff_user,
    locked_until: datetime,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    try:
        from zoneinfo import ZoneInfo

        local = locked_until.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = locked_until
    unlock_time = local.strftime("%-I:%M %p")

    subject = "Your Bella's XV PIN is temporarily locked"
    text = (
        f"Hi {name},\n\n"
        f"Too many incorrect PIN attempts temporarily locked your "
        f"Bella's XV sales portal sign-in.\n\n"
        f"You can try again around {unlock_time} ({APP_TIMEZONE}), "
        f"or ask the boutique owner to unlock your account sooner.\n\n"
        f"If this wasn't you, let the boutique owner know right away.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your PIN is temporarily locked</h1>"
        f"<p>Hi {escape(name)}, too many incorrect PIN attempts temporarily "
        f"locked your Bella's XV sales portal sign-in.</p>"
        f"<p style=\"margin-top:22px;\">You can try again around "
        f"<strong>{escape(unlock_time)}</strong> ({APP_TIMEZONE}), or ask "
        f"the boutique owner to unlock your account sooner.</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If this wasn't you, let the boutique owner know right away.</p>",
        preheader="Your sales-portal PIN is temporarily locked.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_role_changed(
    *,
    staff_user,
    old_role: str,
    new_role: str,
    changed_by=None,
) -> RenderedEmail:
    name = staff_user.full_name or staff_user.username
    actor = (
        changed_by.full_name
        or changed_by.username
        if changed_by is not None
        else "An admin"
    )
    old_label = (old_role or "none").replace("_", " ").title()
    new_label = (new_role or "none").replace("_", " ").title()
    portal_label = "sales portal" if new_role == "sales" else "admin app"

    subject = "Your Bella's XV access was updated"
    text = (
        f"Hi {name},\n\n"
        f"{actor} updated your Bella's XV access from {old_label} "
        f"to {new_label}.\n\n"
        f"Use the {portal_label} the next time you sign in. If this "
        f"change doesn't look right, let the boutique owner know.\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your access was updated</h1>"
        f"<p>Hi {escape(name)}, {escape(actor)} updated your Bella's XV "
        f"access from <strong>{escape(old_label)}</strong> to "
        f"<strong>{escape(new_label)}</strong>.</p>"
        f"<p style=\"margin-top:22px;\">Use the {escape(portal_label)} "
        f"the next time you sign in.</p>"
        f"<p style=\"margin-top:22px; color:#7A6A6F; font-size:13px;\">"
        f"If this change doesn't look right, let the boutique owner know.</p>",
        preheader="Your Bella's XV access was updated.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


# ---------------------------------------------------------------------------
# Staff bookings: per-stylist alerts when a booking lands on / moves on /
# leaves their column, plus the admin-wide alerts for walk-in capture and
# any new booking (admin.new_booking just exposes the existing
# render_internal_new_booking under the catalog kind name).
# ---------------------------------------------------------------------------


def _staff_booking_details(appt: Appointment) -> list[tuple[str, str]]:
    """Compact appointment detail rows for staff-facing booking emails."""
    customer = _full_name(appt) or "Customer"
    return [
        ("When", f"{_format_slot(appt)} ({APP_TIMEZONE})"),
        ("Customer", customer),
        ("Party", _party_label(appt)),
        ("Phone", appt.phone_e164 or appt.phone or "(not provided)"),
        ("Event date", _format_event_date(appt)),
        ("Confirmation", format_confirmation_code(appt.confirmation_code)),
    ]


def render_staff_booking_assigned(
    *,
    staff_user,
    appointment,
    admin_url: str,
) -> RenderedEmail:
    """Sent to the assigned stylist when a booking lands on their column."""
    name = staff_user.full_name or staff_user.username
    slot = _format_slot(appointment)
    customer = _full_name(appointment) or "Customer"
    subject = f"New booking on your calendar — {slot}"
    text = (
        f"Hi {name},\n\n"
        f"A new booking landed on your column for {slot} ({APP_TIMEZONE}).\n\n"
        f"  Customer: {customer}\n"
        f"  Party: {_party_label(appointment)}\n"
        f"  Phone: {appointment.phone_e164 or appointment.phone or '(not provided)'}\n"
        f"  Event date: {_format_event_date(appointment)}\n"
        f"  Confirmation: {format_confirmation_code(appointment.confirmation_code)}\n\n"
        f"Open the appointment to add notes or prep:\n"
        f"    {admin_url}\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">New booking on your calendar</h1>"
        f"<p>Hi {escape(name)}, a new booking landed on your column.</p>"
        + _details_table(_staff_booking_details(appointment))
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the appointment", admin_url)
        + "</p>",
        preheader=f"New booking on your column for {slot}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_staff_booking_rescheduled(
    *,
    staff_user,
    appointment,
    previous_slot_start_at: datetime,
    admin_url: str,
) -> RenderedEmail:
    """Sent to a stylist when an appointment on their calendar moves. The
    appointment passed in is the NEW state; ``previous_slot_start_at`` is
    the slot it moved from. For a stylist who lost the assignment entirely
    the wiring layer should fire ``staff.booking_cancelled`` instead, since
    "moved off your calendar" reads the same as "no longer yours" to a
    stylist.
    """
    name = staff_user.full_name or staff_user.username
    new_slot = _format_slot(appointment)
    try:
        from zoneinfo import ZoneInfo
        prev_local = previous_slot_start_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        prev_local = previous_slot_start_at
    prev_slot = prev_local.strftime("%A, %B %-d at %-I:%M %p")
    customer = _full_name(appointment) or "Customer"
    subject = f"Appointment on your calendar moved — now {new_slot}"
    text = (
        f"Hi {name},\n\n"
        f"The {customer} appointment moved on your calendar.\n\n"
        f"  Previous: {prev_slot}\n"
        f"  New: {new_slot} ({APP_TIMEZONE})\n"
        f"  Customer: {customer}\n"
        f"  Phone: {appointment.phone_e164 or appointment.phone or '(not provided)'}\n"
        f"  Confirmation: {format_confirmation_code(appointment.confirmation_code)}\n\n"
        f"Open the appointment to update notes or prep:\n"
        f"    {admin_url}\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Appointment moved</h1>"
        f"<p>Hi {escape(name)}, the {escape(customer)} appointment moved "
        f"on your calendar.</p>"
        + _details_table(
            [
                ("Previous", prev_slot),
                ("New", f"{new_slot} ({APP_TIMEZONE})"),
                ("Customer", customer),
                ("Phone", appointment.phone_e164 or appointment.phone or "(not provided)"),
                ("Confirmation", format_confirmation_code(appointment.confirmation_code)),
            ]
        )
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the appointment", admin_url)
        + "</p>",
        preheader=f"Appointment moved to {new_slot}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_staff_booking_cancelled(
    *,
    staff_user,
    appointment,
    admin_url: str,
) -> RenderedEmail:
    """Sent to the assigned stylist when a booking on their column is
    cancelled. The slot is now free; the email signals that explicitly so
    a stylist seeing it knows their calendar opened up.
    """
    name = staff_user.full_name or staff_user.username
    slot = _format_slot(appointment)
    customer = _full_name(appointment) or "Customer"
    reason = (appointment.cancellation_reason or "").strip()
    subject = f"Appointment cancelled — {slot}"
    text = (
        f"Hi {name},\n\n"
        f"The {slot} appointment for {customer} was cancelled.\n"
        + (f"Reason: {reason}\n" if reason else "")
        + "\nThat slot is now open on your calendar.\n\n"
        f"  Customer: {customer}\n"
        f"  Phone: {appointment.phone_e164 or appointment.phone or '(not provided)'}\n"
        f"  Confirmation: {format_confirmation_code(appointment.confirmation_code)}\n\n"
        f"Open the appointment for context:\n"
        f"    {admin_url}\n"
    )
    reason_html = (
        f"<p style=\"margin-top:12px;\"><strong>Reason:</strong> {escape(reason)}</p>"
        if reason
        else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Appointment cancelled</h1>"
        f"<p>Hi {escape(name)}, the <strong>{escape(slot)}</strong> "
        f"appointment for {escape(customer)} was cancelled. That slot is "
        f"now open on your calendar.</p>"
        + reason_html
        + _details_table(
            [
                ("Customer", customer),
                ("Phone", appointment.phone_e164 or appointment.phone or "(not provided)"),
                ("Confirmation", format_confirmation_code(appointment.confirmation_code)),
            ]
        )
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the appointment", admin_url, secondary=True)
        + "</p>",
        preheader=f"Appointment for {customer} was cancelled.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def _format_appt_time(appt: Appointment) -> str:
    """Time-of-day only, in shop timezone. Used in digest lists where the
    date appears in the section header so each row stays compact."""
    try:
        from zoneinfo import ZoneInfo
        local = appt.slot_start_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = appt.slot_start_at
    return local.strftime("%-I:%M %p")


def render_staff_daily_digest(
    *,
    staff_user,
    digest_date: date,
    shift: dict | None,
    appointments: list[Appointment],
    admin_url: str,
) -> RenderedEmail:
    """Per-staffer summary of today's shift + appointments on their column.
    Sent each weekday morning to staff who have a shift today; the
    ``appointments`` list should already be filtered to the recipient's
    column by the digest runner.
    """
    name = staff_user.full_name or staff_user.username
    date_label = digest_date.strftime("%A, %B %-d")
    appt_count = len(appointments)
    appt_word = "appointment" if appt_count == 1 else "appointments"

    if shift:
        shift_line = _format_shift_window(shift["starts_at"], shift["ends_at"])
    else:
        shift_line = "No shift scheduled"

    subject = f"Your day at Bella's XV: {date_label}"

    appt_lines_text = "\n".join(
        f"  - {_format_appt_time(a)}: "
        f"{_full_name(a) or 'Customer'} ({_party_label(a)})"
        for a in appointments
    )
    text = (
        f"Hi {name},\n\n"
        f"Here's your day at Bella's XV for {date_label}.\n\n"
        f"  Shift: {shift_line}\n"
        f"  Appointments: {appt_count} {appt_word}\n"
        + (f"\n{appt_lines_text}\n" if appointments else "")
        + f"\nOpen your column:\n    {admin_url}\n"
    )

    appt_section_html = (
        f"<h2 style=\"font-size:18px; margin-top:24px;\">"
        f"Today's appointments</h2>"
        + _details_table(
            [
                (
                    _format_appt_time(a),
                    f"{_full_name(a) or 'Customer'} ({_party_label(a)})",
                )
                for a in appointments
            ]
        )
        if appointments
        else "<p style=\"margin-top:18px;\">"
        "No appointments on your column today.</p>"
    )

    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your day at the boutique</h1>"
        f"<p>Hi {escape(name)}, here's what's on your calendar for "
        f"<strong>{escape(date_label)}</strong>.</p>"
        + _details_table(
            [
                ("Shift", shift_line),
                ("Appointments", f"{appt_count} {appt_word}"),
            ]
        )
        + appt_section_html
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open your column", admin_url)
        + "</p>",
        preheader=f"Your day at Bella's XV: {appt_count} {appt_word}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_staff_weekly_digest(
    *,
    staff_user,
    week_start: date,
    shifts: list[dict],
    admin_url: str,
) -> RenderedEmail:
    """Per-staffer summary of upcoming week's shifts. Sent Sunday evening
    to staff with at least one shift in the next 7 days. Looks similar to
    ``render_schedule_published`` but framed as a weekly look-ahead rather
    than a publish event, with a CTA back to the personal schedule view.
    """
    name = staff_user.full_name or staff_user.username
    week = _format_schedule_week(week_start)
    count = len(shifts)
    shift_word = "shift" if count == 1 else "shifts"
    subject = f"Your week at Bella's XV: {week}"

    shift_lines = "\n".join(
        f"  - {_format_shift_window(s['starts_at'], s['ends_at'])}: "
        f"{_shift_title(s)} at {_shift_location(s)}"
        for s in shifts
    )
    text = (
        f"Hi {name},\n\n"
        f"Here's your week at Bella's XV ({week}). "
        f"You have {count} {shift_word} on the schedule.\n\n"
        f"{shift_lines or '  - No shifts scheduled this week.'}\n\n"
        f"Open your schedule:\n    {admin_url}\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Your week ahead</h1>"
        f"<p>Hi {escape(name)}, here's your week at Bella's XV "
        f"(<strong>{escape(week)}</strong>). You have {count} "
        f"{escape(shift_word)} on the schedule.</p>"
        + (
            _details_table(_shift_rows(shifts))
            if shifts
            else "<p>No shifts scheduled this week.</p>"
        )
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open your schedule", admin_url)
        + "</p>",
        preheader=f"Your Bella's XV week: {count} {shift_word}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_admin_daily_digest(
    *,
    admin_user,
    digest_date: date,
    new_bookings: list[Appointment],
    pending_time_off_rows: list[tuple[str, str]],
    missing_clock_out_rows: list[tuple[str, str]],
    abandoned_count: int = 0,
    in_store_approvals: list[tuple[str, str]] | None = None,
    admin_url: str,
) -> RenderedEmail:
    """Daily admin summary: new bookings, time-off awaiting decision,
    attendance exceptions, abandoned booking count, quotes signed in-store
    in the last 24h. ``pending_time_off_rows``, ``missing_clock_out_rows``,
    and ``in_store_approvals`` are ``(label, value)`` tuples — the digest
    runner pre-formats them so this renderer stays decoupled from the
    time-off, attendance, and quote models. ``new_bookings`` stays as
    Appointment instances so the slot/customer helpers do the work.
    """
    name = admin_user.full_name or admin_user.username
    date_label = digest_date.strftime("%A, %B %-d")
    in_store_rows = in_store_approvals or []
    new_count = len(new_bookings)
    time_off_count = len(pending_time_off_rows)
    missing_count = len(missing_clock_out_rows)
    in_store_count = len(in_store_rows)

    subject = f"Bella's XV daily digest: {date_label}"

    summary_rows = [
        ("New bookings", str(new_count)),
        ("Quotes signed in-store", str(in_store_count)),
        ("Time-off awaiting decision", str(time_off_count)),
        ("Missing clock-outs", str(missing_count)),
        ("Abandoned bookings", str(abandoned_count)),
    ]

    # Text sections — only render those with content so the email stays
    # tight on a quiet day.
    text_sections: list[str] = []
    if new_bookings:
        text_sections.append(
            "New bookings:\n"
            + "\n".join(
                f"  - {_format_slot(a)}: {_full_name(a) or 'Customer'} "
                f"({_party_label(a)})"
                for a in new_bookings
            )
        )
    if in_store_rows:
        text_sections.append(
            "Quotes signed in-store:\n"
            + "\n".join(
                f"  - {quote_number}: {detail}"
                for quote_number, detail in in_store_rows
            )
        )
    if pending_time_off_rows:
        text_sections.append(
            "Time-off awaiting decision:\n"
            + "\n".join(f"  - {who}: {window}" for who, window in pending_time_off_rows)
        )
    if missing_clock_out_rows:
        text_sections.append(
            "Missing clock-outs:\n"
            + "\n".join(f"  - {who}: {detail}" for who, detail in missing_clock_out_rows)
        )

    text = (
        f"Hi {name},\n\n"
        f"Here's the boutique on {date_label}.\n\n"
        f"  New bookings: {new_count}\n"
        f"  Quotes signed in-store: {in_store_count}\n"
        f"  Time-off awaiting decision: {time_off_count}\n"
        f"  Missing clock-outs: {missing_count}\n"
        f"  Abandoned bookings: {abandoned_count}\n\n"
        + ("\n\n".join(text_sections) + "\n\n" if text_sections else "")
        + f"Open admin:\n    {admin_url}\n"
    )

    # HTML sections
    html_sections: list[str] = []
    if new_bookings:
        html_sections.append(
            f"<h2 style=\"font-size:18px; margin-top:24px;\">"
            f"New bookings ({new_count})</h2>"
            + _details_table(
                [
                    (
                        _format_slot(a),
                        f"{_full_name(a) or 'Customer'} ({_party_label(a)})",
                    )
                    for a in new_bookings
                ]
            )
        )
    if in_store_rows:
        html_sections.append(
            f"<h2 style=\"font-size:18px; margin-top:24px;\">"
            f"Quotes signed in-store ({in_store_count})</h2>"
            + _details_table(in_store_rows)
        )
    if pending_time_off_rows:
        html_sections.append(
            f"<h2 style=\"font-size:18px; margin-top:24px;\">"
            f"Time-off awaiting decision ({time_off_count})</h2>"
            + _details_table(pending_time_off_rows)
        )
    if missing_clock_out_rows:
        html_sections.append(
            f"<h2 style=\"font-size:18px; margin-top:24px;\">"
            f"Missing clock-outs ({missing_count})</h2>"
            + _details_table(missing_clock_out_rows)
        )

    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"margin-top:0;\">Daily digest</h1>"
        f"<p>Hi {escape(name)}, here's the boutique on "
        f"<strong>{escape(date_label)}</strong>.</p>"
        + _details_table(summary_rows)
        + "".join(html_sections)
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open admin", admin_url)
        + "</p>",
        preheader=f"Bella's XV daily digest for {date_label}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_staff_quote_signed(
    *,
    staff_user,
    quote_number: str,
    customer_name: str,
    quote_total_cents: int,
    signed_at: datetime,
    admin_url: str,
) -> RenderedEmail:
    """Sent to the owner of a quote when the customer signs it through
    the portal. Decoupled from the Quote ORM model on purpose so the
    wiring layer can compose the email from whatever projection of the
    quote already exists at the call site.
    """
    name = staff_user.full_name or staff_user.username
    total = _format_money(quote_total_cents)
    try:
        from zoneinfo import ZoneInfo
        local = signed_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = signed_at
    when = local.strftime("%A, %B %-d at %-I:%M %p")
    subject = f"{customer_name} signed quote {quote_number}"
    text = (
        f"Hi {name},\n\n"
        f"{customer_name} just signed quote {quote_number}.\n\n"
        f"  Total: {total}\n"
        f"  Signed: {when} ({APP_TIMEZONE})\n\n"
        f"Open the quote in admin:\n    {admin_url}\n"
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Quote signed</h1>"
        f"<p>Hi {escape(name)}, <strong>{escape(customer_name)}</strong> "
        f"just signed quote <strong>{escape(quote_number)}</strong>.</p>"
        + _details_table(
            [
                ("Quote", quote_number),
                ("Customer", customer_name),
                ("Total", total),
                ("Signed", f"{when} ({APP_TIMEZONE})"),
            ]
        )
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the quote", admin_url)
        + "</p>",
        preheader=f"{customer_name} signed quote {quote_number}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_staff_payment_received(
    *,
    staff_user,
    payment_amount_cents: int,
    payment_method: str | None,
    customer_name: str,
    invoice_number: str | None,
    payment_number: str | None,
    received_at: datetime,
    admin_url: str,
) -> RenderedEmail:
    """Sent to the owner of an invoice when a payment is recorded
    against it. Decoupled from the Payment ORM model so the wiring
    layer can pass the small set of fields that actually appear in the
    email without hauling the whole row through."""
    name = staff_user.full_name or staff_user.username
    amount = _format_money(payment_amount_cents)
    method = _payment_method_label(payment_method)
    try:
        from zoneinfo import ZoneInfo
        local = received_at.astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:  # pragma: no cover
        local = received_at
    when = local.strftime("%A, %B %-d at %-I:%M %p")
    invoice_label = invoice_number or "(not linked to a specific invoice)"
    subject = f"Payment received: {amount} from {customer_name}"
    text = (
        f"Hi {name},\n\n"
        f"A payment of {amount} was just recorded from "
        f"{customer_name}.\n\n"
        f"  Amount: {amount}\n"
        f"  Method: {method}\n"
        f"  Customer: {customer_name}\n"
        f"  Invoice: {invoice_label}\n"
        + (f"  Receipt: {payment_number}\n" if payment_number else "")
        + f"  Received: {when} ({APP_TIMEZONE})\n\n"
        f"Open the payment in admin:\n    {admin_url}\n"
    )
    rows: list[tuple[str, str]] = [
        ("Amount", amount),
        ("Method", method),
        ("Customer", customer_name),
        ("Invoice", invoice_label),
    ]
    if payment_number:
        rows.append(("Receipt", payment_number))
    rows.append(("Received", f"{when} ({APP_TIMEZONE})"))
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"color:#A7616F; margin-top:0;\">Payment received</h1>"
        f"<p>Hi {escape(name)}, a payment of <strong>{escape(amount)}</strong> "
        f"was just recorded from <strong>{escape(customer_name)}</strong>.</p>"
        + _details_table(rows)
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the payment", admin_url)
        + "</p>",
        preheader=f"{amount} payment from {customer_name}.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)


def render_admin_walk_in_lead_created(
    *,
    captured_by,
    appointment,
    contact,
    notes: str | None = None,
    admin_url: str,
) -> RenderedEmail:
    """Admin-internal alert when a staff member logs a walk-in or phone
    lead via the dashboard. ``appointment`` is the placeholder
    appointment row that backs the lead; ``contact`` is the (possibly
    new) Contact the lead is attached to.
    """
    actor = captured_by.full_name or captured_by.username
    celebrant = (appointment.celebrant_first_name or "").strip()
    if appointment.celebrant_last_name:
        celebrant = f"{celebrant} {appointment.celebrant_last_name}".strip()
    celebrant = celebrant or contact.display_name or "(name unknown)"
    contact_label = (
        contact.display_name
        or " ".join(
            filter(None, [contact.first_name, contact.last_name])
        ).strip()
        or "(no name)"
    )
    notes_clean = (notes or "").strip()
    subject = f"New walk-in lead: {celebrant}"
    text = (
        f"{actor} just logged a walk-in lead.\n\n"
        f"  Celebrant: {celebrant}\n"
        f"  Contact: {contact_label}\n"
        f"  Phone: {contact.phone_e164 or contact.phone or '(not provided)'}\n"
        f"  Email: {contact.email or '(not provided)'}\n"
        f"  Event date: {_format_event_date(appointment)}\n"
        + (f"  Notes: {notes_clean}\n" if notes_clean else "")
        + f"\nOpen the lead in admin:\n    {admin_url}\n"
    )
    notes_html = (
        f"<p style=\"margin-top:12px;\"><strong>Notes:</strong> {escape(notes_clean)}</p>"
        if notes_clean
        else ""
    )
    html = _wrap_html(
        f"<h1 style=\"font-family:'Playfair Display', Georgia, serif; "
        f"margin-top:0;\">New walk-in lead</h1>"
        f"<p><strong>{escape(actor)}</strong> just logged a walk-in lead.</p>"
        + _details_table(
            [
                ("Celebrant", celebrant),
                ("Contact", contact_label),
                ("Phone", contact.phone_e164 or contact.phone or "(not provided)"),
                ("Email", contact.email or "(not provided)"),
                ("Event date", _format_event_date(appointment)),
            ]
        )
        + notes_html
        + f"<p style=\"margin-top:22px;\">"
        + _html_button("Open the lead", admin_url)
        + "</p>",
        preheader=f"{actor} logged a walk-in lead.",
    )
    return RenderedEmail(subject=subject, text=text, html=html)
