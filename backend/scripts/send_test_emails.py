"""Fire test renders of any registered email template.

The script builds synthesized fixtures (no DB roundtrip) and hands them
to the same renderer + transport stack production uses. When
EMAIL_DEV_REDIRECT is set, every send lands at that inbox with the
original recipient surfaced in the subject and an in-body banner.

Usage:
    python scripts/send_test_emails.py --list
    python scripts/send_test_emails.py --kind booking.confirmation
    python scripts/send_test_emails.py --kind all

The intended-recipient address shown in the subject prefix is the one
configured per-kind in TEST_RECIPIENT below — it's the address the
template would have gone to in production, not the address the email
actually lands at. Override EMAIL_DEV_REDIRECT in .env to control where
it lands.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import EMAIL_DEV_REDIRECT
from database.models import Appointment, Contact, Payment, TimeOffRequest, User
from services.email_transport import (
    EmailMessagePayload,
    get_email_transport,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────
#
# Build SQLAlchemy model instances WITHOUT a session attached. The renderers
# read attributes directly, and any helpers that call ``object_session(obj)``
# already handle the None case (see services/notification_templates.py
# :is_boutique_profile_attached). Stable values across runs so the email
# copy diffs cleanly from one render to the next.


def _fake_appointment() -> Appointment:
    appt = Appointment()
    appt.id = 999_999
    appt.confirmation_code = "DEMOAAAA1111BBBB2222"
    # ~14 days out, 10:00 AM in the shop timezone, 60-minute consultation.
    base_start = datetime.now(timezone.utc) + timedelta(days=14)
    appt.slot_start_at = base_start.replace(hour=15, minute=0, second=0, microsecond=0)
    appt.slot_end_at = appt.slot_start_at + timedelta(minutes=60)
    appt.slot_duration_minutes = 60
    appt.timezone = "America/Chicago"

    appt.celebrant_first_name = "Sofia"
    appt.celebrant_last_name = "Garcia"
    appt.parent_first_name = "Maria"
    appt.parent_last_name = "Garcia"
    appt.event_date = (date.today() + timedelta(days=180))
    appt.party_size_bucket = "3_4"
    appt.phone = "(210) 555-0142"
    appt.phone_e164 = "+12105550142"
    appt.email = "maria.garcia@example.com"
    appt.customer_note = None

    appt.status = "confirmed"
    appt.assigned_user_id = None
    appt.internal_notes = None
    appt.cancelled_at = None
    appt.cancellation_reason = None
    appt.rescheduled_from_id = None
    appt.tokens_invalidated_at = None
    appt.attended_at = None
    appt.no_show_at = None
    appt.purchase_at = None
    appt.purchase_value_cents = None

    appt.contact_id = None
    appt.crm_event_id = None

    appt.visitor_id = None
    appt.session_id = None
    appt.event_id = "test-event-id-000001"
    appt.page_url = None
    appt.referrer_url = None
    appt.utm_source = None
    appt.utm_medium = None
    appt.utm_campaign = None
    appt.utm_content = None
    appt.utm_term = None
    appt.utm_id = None
    appt.fbclid = None
    appt.gclid = None
    appt.msclkid = None
    appt.fbp_cookie = None
    appt.fbc_cookie = None

    appt.device_type = None
    appt.user_agent = None
    appt.screen = None
    appt.viewport = None
    appt.browser_language = None
    appt.platform = None
    appt.browser_timezone = None

    appt.time_on_widget_ms = None
    appt.interaction_count = None
    appt.steps_completed = None
    appt.user_journey = []
    appt.behavior_score = None
    appt.bot_suspected = False

    appt.raw_payload = {}
    return appt


def _fake_staff_user() -> User:
    user = User()
    user.id = 42
    user.username = "sofia"
    user.email = "sofia.garcia@example.com"
    user.full_name = "Sofia Garcia"
    user.is_active = True
    user.role = "sales"
    user.permissions = []
    return user


def _fake_contact() -> Contact:
    contact = Contact()
    contact.id = 7_777
    contact.first_name = "Maria"
    contact.last_name = "Garcia"
    contact.display_name = "Maria Garcia"
    contact.email = "maria.garcia@example.com"
    contact.phone = "(210) 555-0142"
    contact.phone_e164 = "+12105550142"
    return contact


def _fake_payment() -> Payment:
    payment = Payment()
    payment.id = 888_888
    payment.contact_id = 7_777
    payment.payment_number = "PMT-2026-000123"
    payment.amount_cents = 50000
    payment.applied_cents = 45000
    payment.unapplied_cents = 5000
    payment.refunded_cents = 0
    payment.payment_date = date.today()
    payment.method = "zelle"
    payment.transaction_reference = "ZELLE-4451"
    payment.status = "completed"
    payment.notes = None
    return payment


def _fake_shift(*, day_offset: int = 1, hour: int = 15) -> dict:
    start = datetime.now(timezone.utc) + timedelta(days=day_offset)
    start = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    return {
        "starts_at": start,
        "ends_at": start + timedelta(hours=5),
        "title": "Stylist floor shift",
        "location": "Bella's XV boutique",
        "notes": "Focus on consultation prep and floor coverage.",
    }


def _fake_time_off_request(
    *,
    status: str = "pending",
    partial: bool = True,
) -> TimeOffRequest:
    req = TimeOffRequest()
    req.id = 333_333
    req.user_id = 42
    tz = ZoneInfo("America/Chicago")
    if partial:
        start = datetime.now(tz) + timedelta(days=10)
        req.starts_at = start.replace(hour=12, minute=0, second=0, microsecond=0)
        req.ends_at = start.replace(hour=16, minute=0, second=0, microsecond=0)
    else:
        start = datetime.now(tz) + timedelta(days=10)
        end = start + timedelta(days=1)
        req.starts_at = start.replace(hour=0, minute=0, second=0, microsecond=0)
        req.ends_at = end.replace(hour=23, minute=59, second=59, microsecond=0)
    req.reason = "Family appointment"
    req.status = status
    req.decided_by_user_id = 99 if status in ("approved", "denied") else None
    req.decision_notes = (
        "Covered on the floor schedule." if status == "approved" else None
    )
    return req


# ─── Renderer adapters ─────────────────────────────────────────────────────
#
# Each adapter returns an EmailMessagePayload ready to hand to the transport.
# The `recipient` is the production address the template would target; the
# redirect wrapper will rewrite it before send.


def _render_booking_confirmation() -> EmailMessagePayload:
    from services.notification_templates import render_booking_confirmation

    appt = _fake_appointment()
    rendered = render_booking_confirmation(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_thank_you() -> EmailMessagePayload:
    from services.notification_templates import render_booking_thank_you

    appt = _fake_appointment()
    appt.status = "attended"
    appt.attended_at = datetime.now(timezone.utc)
    appt.purchase_at = datetime.now(timezone.utc)
    appt.purchase_value_cents = 189900
    rendered = render_booking_thank_you(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_no_show_followup() -> EmailMessagePayload:
    from services.notification_templates import render_booking_no_show_followup

    appt = _fake_appointment()
    appt.status = "no_show"
    appt.no_show_at = datetime.now(timezone.utc)
    rendered = render_booking_no_show_followup(
        appt,
        booking_url="https://shopbellasxv.com/book",
    )
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_reminder() -> EmailMessagePayload:
    from services.notification_templates import render_reminder

    appt = _fake_appointment()
    # Reminder fires ~24h before the slot, so move the fixture closer.
    appt.slot_start_at = datetime.now(timezone.utc) + timedelta(hours=24)
    appt.slot_end_at = appt.slot_start_at + timedelta(minutes=60)
    rendered = render_reminder(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_enrichment_invitation() -> EmailMessagePayload:
    from services.notification_templates import render_enrichment_invitation

    appt = _fake_appointment()
    rendered = render_enrichment_invitation(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_reschedule_confirmation() -> EmailMessagePayload:
    from services.notification_templates import render_reschedule_confirmation

    appt = _fake_appointment()
    # The fixture represents the NEW slot the customer rescheduled to.
    # Push it out a few extra days so it's visibly distinct from the
    # original-booking timestamp in tests that fire both back-to-back.
    appt.slot_start_at = datetime.now(timezone.utc) + timedelta(days=21, hours=2)
    appt.slot_end_at = appt.slot_start_at + timedelta(minutes=60)
    appt.rescheduled_from_id = 999_998
    rendered = render_reschedule_confirmation(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_booking_cancellation_confirmation() -> EmailMessagePayload:
    from services.notification_templates import render_cancellation_confirmation

    appt = _fake_appointment()
    appt.status = "cancelled"
    appt.cancelled_at = datetime.now(timezone.utc)
    appt.cancellation_reason = "Schedule conflict"
    rendered = render_cancellation_confirmation(appt)
    return EmailMessagePayload(
        to=appt.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_booking_assigned() -> EmailMessagePayload:
    from services.notification_templates import render_staff_booking_assigned

    user = _fake_staff_user()
    appt = _fake_appointment()
    appt.assigned_user_id = user.id
    rendered = render_staff_booking_assigned(
        staff_user=user,
        appointment=appt,
        admin_url=f"https://admin.shopbellasxv.com/appointments/{appt.id}",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_booking_rescheduled() -> EmailMessagePayload:
    from services.notification_templates import render_staff_booking_rescheduled

    user = _fake_staff_user()
    appt = _fake_appointment()
    appt.assigned_user_id = user.id
    appt.slot_start_at = datetime.now(timezone.utc) + timedelta(days=18, hours=2)
    appt.slot_end_at = appt.slot_start_at + timedelta(minutes=60)
    previous_slot_start = datetime.now(timezone.utc) + timedelta(days=14, hours=4)
    rendered = render_staff_booking_rescheduled(
        staff_user=user,
        appointment=appt,
        previous_slot_start_at=previous_slot_start,
        admin_url=f"https://admin.shopbellasxv.com/appointments/{appt.id}",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_booking_cancelled() -> EmailMessagePayload:
    from services.notification_templates import render_staff_booking_cancelled

    user = _fake_staff_user()
    appt = _fake_appointment()
    appt.assigned_user_id = user.id
    appt.status = "cancelled"
    appt.cancelled_at = datetime.now(timezone.utc)
    appt.cancellation_reason = "Family schedule conflict"
    rendered = render_staff_booking_cancelled(
        staff_user=user,
        appointment=appt,
        admin_url=f"https://admin.shopbellasxv.com/appointments/{appt.id}",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_walk_in_lead_created() -> EmailMessagePayload:
    from services.notification_templates import render_admin_walk_in_lead_created

    captured_by = _fake_staff_user()
    captured_by.role = "admin"
    captured_by.full_name = "Alex Rivera"
    captured_by.email = "alex.rivera@example.com"
    captured_by.username = "alex"

    contact = _fake_contact()
    appt = _fake_appointment()
    rendered = render_admin_walk_in_lead_created(
        captured_by=captured_by,
        appointment=appt,
        contact=contact,
        notes="Looking for a princess silhouette in coral. Budget under $1500.",
        admin_url=f"https://admin.shopbellasxv.com/contacts/{contact.id}",
    )
    return EmailMessagePayload(
        to="bookings@shopbellasxv.com",
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_new_booking() -> EmailMessagePayload:
    from services.notification_templates import render_internal_new_booking

    appt = _fake_appointment()
    rendered = render_internal_new_booking(appt)
    return EmailMessagePayload(
        to="bookings@shopbellasxv.com",
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_quote_signed() -> EmailMessagePayload:
    from services.notification_templates import render_staff_quote_signed

    user = _fake_staff_user()
    user.full_name = "Alex Rivera"
    user.email = "alex.rivera@example.com"
    user.username = "alex"
    rendered = render_staff_quote_signed(
        staff_user=user,
        quote_number="Q-2026-000087",
        customer_name="Maria Garcia",
        quote_total_cents=289500,
        signed_at=datetime.now(timezone.utc),
        admin_url="https://admin.shopbellasxv.com/quotes/Q-2026-000087",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_digest_staff_daily() -> EmailMessagePayload:
    from services.notification_templates import render_staff_daily_digest

    user = _fake_staff_user()
    today = date.today()
    shift = _fake_shift(day_offset=0, hour=10)

    appts: list[Appointment] = []
    for hour, party, first, last in [
        (10, "3_4", "Maria", "Garcia"),
        (12, "pair", "Lupita", "Mendoza"),
        (14, "5_plus", "Camila", "Reyes"),
    ]:
        a = _fake_appointment()
        a.id = 800_000 + hour
        a.confirmation_code = f"DEMO{hour:02d}TODAY000000000000"
        local_tz = ZoneInfo("America/Chicago")
        local_start = datetime.combine(
            today, datetime.min.time(), tzinfo=local_tz
        ).replace(hour=hour, minute=0)
        a.slot_start_at = local_start.astimezone(timezone.utc)
        a.slot_end_at = a.slot_start_at + timedelta(minutes=60)
        a.celebrant_first_name = first
        a.celebrant_last_name = last
        a.parent_first_name = first
        a.parent_last_name = last
        a.party_size_bucket = party
        appts.append(a)

    rendered = render_staff_daily_digest(
        staff_user=user,
        digest_date=today,
        shift=shift,
        appointments=appts,
        admin_url="https://admin.shopbellasxv.com/schedule/today",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_digest_staff_weekly() -> EmailMessagePayload:
    from services.notification_templates import render_staff_weekly_digest

    user = _fake_staff_user()
    today = date.today()
    # Pick the upcoming Monday as week_start for the look-ahead.
    days_until_monday = (7 - today.weekday()) % 7 or 7
    week_start = today + timedelta(days=days_until_monday)

    shifts = [
        _fake_shift(day_offset=days_until_monday + 0, hour=10),    # Mon morning
        _fake_shift(day_offset=days_until_monday + 2, hour=13),    # Wed afternoon
        _fake_shift(day_offset=days_until_monday + 4, hour=10),    # Fri morning
        _fake_shift(day_offset=days_until_monday + 5, hour=11),    # Sat morning
    ]

    rendered = render_staff_weekly_digest(
        staff_user=user,
        week_start=week_start,
        shifts=shifts,
        admin_url="https://admin.shopbellasxv.com/schedule",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_digest_admin_daily() -> EmailMessagePayload:
    from services.notification_templates import render_admin_daily_digest

    admin = _fake_staff_user()
    admin.role = "admin"
    admin.full_name = "Alex Rivera"
    admin.email = "alex.rivera@example.com"
    admin.username = "alex"

    today = date.today()

    new_bookings: list[Appointment] = []
    for offset_days, first, last, party in [
        (10, "Maria", "Garcia", "3_4"),
        (14, "Camila", "Reyes", "5_plus"),
        (21, "Lupita", "Mendoza", "pair"),
    ]:
        a = _fake_appointment()
        a.id = 700_000 + offset_days
        a.confirmation_code = f"NEW{offset_days:02d}DEMO000000000000"
        a.slot_start_at = datetime.now(timezone.utc) + timedelta(
            days=offset_days, hours=2
        )
        a.slot_end_at = a.slot_start_at + timedelta(minutes=60)
        a.celebrant_first_name = first
        a.celebrant_last_name = last
        a.parent_first_name = first
        a.parent_last_name = last
        a.party_size_bucket = party
        new_bookings.append(a)

    pending_time_off_rows = [
        ("Sofia Garcia", "Wed May 21, 12:00 PM to 4:00 PM"),
        ("Lucia Hernandez", "Sat May 24 (all day)"),
    ]
    missing_clock_out_rows = [
        ("Sofia Garcia", "Clocked in 9:30 AM on Sat May 16, never out"),
    ]

    rendered = render_admin_daily_digest(
        admin_user=admin,
        digest_date=today,
        new_bookings=new_bookings,
        pending_time_off_rows=pending_time_off_rows,
        missing_clock_out_rows=missing_clock_out_rows,
        abandoned_count=4,
        admin_url="https://admin.shopbellasxv.com",
    )
    return EmailMessagePayload(
        to=admin.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_manual_resend_schedule() -> EmailMessagePayload:
    """#38 — admin clicks 'Resend this week's schedule' for a single
    recipient. Same template as #17 staff.schedule_published; the kind
    name documents that the trigger is manual, not the publish event.
    """
    from services.notification_templates import render_schedule_published

    user = _fake_staff_user()
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    week_start = today + timedelta(days=days_until_monday)
    shifts = [
        _fake_shift(day_offset=days_until_monday + 0, hour=10),
        _fake_shift(day_offset=days_until_monday + 2, hour=13),
        _fake_shift(day_offset=days_until_monday + 4, hour=10),
    ]
    rendered = render_schedule_published(
        staff_user=user,
        week_start=week_start,
        shifts=shifts,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_payment_received() -> EmailMessagePayload:
    from services.notification_templates import render_staff_payment_received

    user = _fake_staff_user()
    user.full_name = "Alex Rivera"
    user.email = "alex.rivera@example.com"
    user.username = "alex"
    rendered = render_staff_payment_received(
        staff_user=user,
        payment_amount_cents=50000,
        payment_method="zelle",
        customer_name="Maria Garcia",
        invoice_number="INV-2026-000123",
        payment_number="PMT-2026-000123",
        received_at=datetime.now(timezone.utc),
        admin_url="https://admin.shopbellasxv.com/payments/PMT-2026-000123",
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_pin_reset() -> EmailMessagePayload:
    from services.notification_templates import render_pin_reset

    user = _fake_staff_user()
    set_pin_url = (
        "https://sales.shopbellasxv.com/set-pin/"
        "eyJhbGciOiJIUzI1NiJ9.demo-token-for-template-review"
    )
    rendered = render_pin_reset(staff_user=user, set_pin_url=set_pin_url)
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_payment_receipt() -> EmailMessagePayload:
    from services.notification_templates import render_payment_receipt

    contact = _fake_contact()
    payment = _fake_payment()
    rendered = render_payment_receipt(
        contact=contact,
        payment=payment,
        receipt_url="https://shopbellasxv.com/portal/payment/PMT-2026-000123",
    )
    return EmailMessagePayload(
        to=contact.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_schedule_published() -> EmailMessagePayload:
    from services.notification_templates import render_schedule_published

    user = _fake_staff_user()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    shifts = [
        _fake_shift(day_offset=1, hour=15),
        _fake_shift(day_offset=3, hour=16),
        _fake_shift(day_offset=5, hour=14),
    ]
    rendered = render_schedule_published(
        staff_user=user,
        week_start=week_start,
        shifts=shifts,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_shift_added() -> EmailMessagePayload:
    from services.notification_templates import render_shift_added

    user = _fake_staff_user()
    rendered = render_shift_added(staff_user=user, shift=_fake_shift(day_offset=2))
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_shift_edited() -> EmailMessagePayload:
    from services.notification_templates import render_shift_edited

    user = _fake_staff_user()
    old_shift = _fake_shift(day_offset=4, hour=15)
    new_shift = dict(old_shift)
    new_shift["starts_at"] = old_shift["starts_at"] + timedelta(hours=1)
    new_shift["ends_at"] = old_shift["ends_at"] + timedelta(hours=1)
    new_shift["notes"] = "Updated for later floor coverage."
    rendered = render_shift_edited(
        staff_user=user,
        old_shift=old_shift,
        new_shift=new_shift,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_shift_deleted() -> EmailMessagePayload:
    from services.notification_templates import render_shift_deleted

    user = _fake_staff_user()
    rendered = render_shift_deleted(staff_user=user, shift=_fake_shift(day_offset=6))
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_time_off_requested() -> EmailMessagePayload:
    from services.notification_templates import render_time_off_requested_to_owner

    stylist = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.email = "alex.rivera@example.com"
    req = _fake_time_off_request(status="pending", partial=True)
    rendered = render_time_off_requested_to_owner(request=req, stylist=stylist)
    return EmailMessagePayload(
        to=admin.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_time_off_approved() -> EmailMessagePayload:
    from services.notification_templates import render_time_off_decided_to_staff

    stylist = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.full_name = "Alex Rivera"
    req = _fake_time_off_request(status="approved", partial=False)
    rendered = render_time_off_decided_to_staff(
        request=req,
        stylist=stylist,
        decided_by=admin,
    )
    return EmailMessagePayload(
        to=stylist.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_time_off_denied() -> EmailMessagePayload:
    from services.notification_templates import render_time_off_decided_to_staff

    stylist = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.full_name = "Alex Rivera"
    req = _fake_time_off_request(status="denied", partial=True)
    req.decision_notes = "We already have two stylists out that afternoon."
    rendered = render_time_off_decided_to_staff(
        request=req,
        stylist=stylist,
        decided_by=admin,
    )
    return EmailMessagePayload(
        to=stylist.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_time_off_amended() -> EmailMessagePayload:
    from services.notification_templates import render_time_off_amended_to_staff

    stylist = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.full_name = "Alex Rivera"
    req = _fake_time_off_request(status="pending", partial=True)
    previous_start = req.starts_at
    previous_end = req.ends_at
    req.starts_at = req.starts_at + timedelta(hours=1)
    req.ends_at = req.ends_at + timedelta(hours=1)
    rendered = render_time_off_amended_to_staff(
        request=req,
        stylist=stylist,
        amended_by=admin,
        previous_starts_at=previous_start,
        previous_ends_at=previous_end,
        amendment_notes="Shifted by one hour to match coverage.",
    )
    return EmailMessagePayload(
        to=stylist.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_missing_clock_out() -> EmailMessagePayload:
    from services.notification_templates import render_staff_missing_clock_out

    user = _fake_staff_user()
    shift = _fake_shift(day_offset=-1, hour=15)
    clocked_in_at = shift["starts_at"] + timedelta(minutes=3)
    rendered = render_staff_missing_clock_out(
        staff_user=user,
        shift=shift,
        clocked_in_at=clocked_in_at,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_missing_clock_out() -> EmailMessagePayload:
    from services.notification_templates import render_admin_missing_clock_out

    user = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.email = "alex.rivera@example.com"
    shift = _fake_shift(day_offset=-1, hour=15)
    clocked_in_at = shift["starts_at"] + timedelta(minutes=3)
    rendered = render_admin_missing_clock_out(
        staff_user=user,
        shift=shift,
        clocked_in_at=clocked_in_at,
    )
    return EmailMessagePayload(
        to=admin.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_welcome_sales() -> EmailMessagePayload:
    from services.notification_templates import render_welcome_new_user

    user = _fake_staff_user()  # role="sales"
    setup_url = (
        "https://sales.shopbellasxv.com/set-pin/"
        "eyJhbGciOiJIUzI1NiJ9.demo-welcome-token-sales"
    )
    portal_login_url = "https://sales.shopbellasxv.com"
    rendered = render_welcome_new_user(
        staff_user=user,
        setup_url=setup_url,
        portal_login_url=portal_login_url,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_password_changed() -> EmailMessagePayload:
    from services.notification_templates import render_password_changed

    user = _fake_staff_user()
    user.role = "admin"
    user.full_name = "Alex Rivera"
    user.email = "alex.rivera@example.com"
    user.username = "alex"
    changed_at = datetime.now(timezone.utc)
    rendered = render_password_changed(user=user, changed_at=changed_at)
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_admin_password_reset_request() -> EmailMessagePayload:
    from services.notification_templates import render_password_reset_request

    user = _fake_staff_user()
    user.role = "admin"
    user.full_name = "Alex Rivera"
    user.email = "alex.rivera@example.com"
    user.username = "alex"
    reset_url = (
        "https://admin.shopbellasxv.com/auth/password-reset/confirm"
        "?token=eyJhbGciOiJIUzI1NiJ9.demo-reset-token"
    )
    rendered = render_password_reset_request(user=user, reset_url=reset_url)
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_account_locked() -> EmailMessagePayload:
    from services.notification_templates import render_account_locked

    user = _fake_staff_user()
    locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    rendered = render_account_locked(staff_user=user, locked_until=locked_until)
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_role_changed() -> EmailMessagePayload:
    from services.notification_templates import render_role_changed

    user = _fake_staff_user()
    admin = _fake_staff_user()
    admin.role = "admin"
    admin.full_name = "Alex Rivera"
    admin.email = "alex.rivera@example.com"
    admin.username = "alex"
    rendered = render_role_changed(
        staff_user=user,
        old_role="user",
        new_role="sales",
        changed_by=admin,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


def _render_staff_welcome_admin() -> EmailMessagePayload:
    from services.notification_templates import render_welcome_new_user

    user = _fake_staff_user()
    user.role = "admin"
    user.full_name = "Alex Rivera"
    user.email = "alex.rivera@example.com"
    user.username = "alex"
    setup_url = (
        "https://admin.shopbellasxv.com/auth/password-reset/confirm"
        "?token=eyJhbGciOiJIUzI1NiJ9.demo-welcome-token-admin"
    )
    portal_login_url = "https://admin.shopbellasxv.com"
    rendered = render_welcome_new_user(
        staff_user=user,
        setup_url=setup_url,
        portal_login_url=portal_login_url,
    )
    return EmailMessagePayload(
        to=user.email,
        subject=rendered.subject,
        text=rendered.text,
        html=rendered.html,
    )


# ─── Registry ──────────────────────────────────────────────────────────────


@dataclass
class TestKind:
    kind: str
    audience: str
    builder: Callable[[], EmailMessagePayload]


REGISTRY: list[TestKind] = [
    TestKind(
        kind="booking.confirmation",
        audience="Customer (parent)",
        builder=_render_booking_confirmation,
    ),
    TestKind(
        kind="booking.reminder",
        audience="Customer (24h before appointment)",
        builder=_render_booking_reminder,
    ),
    TestKind(
        kind="booking.enrichment_invitation",
        audience="Customer (minutes after booking, profile prompt)",
        builder=_render_booking_enrichment_invitation,
    ),
    TestKind(
        kind="booking.reschedule_confirmation",
        audience="Customer after reschedule (lands at the new slot)",
        builder=_render_booking_reschedule_confirmation,
    ),
    TestKind(
        kind="booking.cancellation_confirmation",
        audience="Customer after cancellation",
        builder=_render_booking_cancellation_confirmation,
    ),
    TestKind(
        kind="staff.booking_assigned",
        audience="Assigned stylist (new booking on their column)",
        builder=_render_staff_booking_assigned,
    ),
    TestKind(
        kind="staff.booking_rescheduled",
        audience="Assigned stylist (booking moved on their calendar)",
        builder=_render_staff_booking_rescheduled,
    ),
    TestKind(
        kind="staff.booking_cancelled",
        audience="Assigned stylist (booking on their column cancelled)",
        builder=_render_staff_booking_cancelled,
    ),
    TestKind(
        kind="admin.walk_in_lead_created",
        audience="Admins (staff logged a walk-in / phone lead)",
        builder=_render_admin_walk_in_lead_created,
    ),
    TestKind(
        kind="admin.new_booking",
        audience="Admins (any new booking — existing internal_new_booking renderer)",
        builder=_render_admin_new_booking,
    ),
    TestKind(
        kind="staff.quote_signed",
        audience="Owner of the quote (customer signed in portal)",
        builder=_render_staff_quote_signed,
    ),
    TestKind(
        kind="staff.payment_received",
        audience="Owner of the invoice (payment recorded)",
        builder=_render_staff_payment_received,
    ),
    TestKind(
        kind="digest.staff_daily",
        audience="Each scheduled staffer (daily 6am)",
        builder=_render_digest_staff_daily,
    ),
    TestKind(
        kind="digest.staff_weekly",
        audience="Staff with upcoming shifts (Sunday 6pm)",
        builder=_render_digest_staff_weekly,
    ),
    TestKind(
        kind="digest.admin_daily",
        audience="Admins (daily 6am)",
        builder=_render_digest_admin_daily,
    ),
    TestKind(
        kind="manual.resend_schedule",
        audience="Staff (admin re-fires the schedule_published template)",
        builder=_render_manual_resend_schedule,
    ),
    TestKind(
        kind="staff.pin_reset",
        audience="Sales staff (PIN holder)",
        builder=_render_staff_pin_reset,
    ),
    TestKind(
        kind="staff.welcome_new_user",
        audience="New sales staff (PIN setup variant)",
        builder=_render_staff_welcome_sales,
    ),
    TestKind(
        kind="staff.welcome_new_user.admin",
        audience="New admin user (password setup variant) — same template, role-aware copy",
        builder=_render_staff_welcome_admin,
    ),
    TestKind(
        kind="admin.password_reset_request",
        audience="Admin / staff user (forgot-password flow)",
        builder=_render_admin_password_reset_request,
    ),
    TestKind(
        kind="admin.password_changed",
        audience="Admin / staff user (post-reset confirmation)",
        builder=_render_admin_password_changed,
    ),
    TestKind(
        kind="staff.account_locked",
        audience="Sales staff (failed-PIN lockout)",
        builder=_render_staff_account_locked,
    ),
    TestKind(
        kind="staff.role_changed",
        audience="Staff user whose access changed",
        builder=_render_staff_role_changed,
    ),
    TestKind(
        kind="booking.thank_you",
        audience="Customer after attended appointment",
        builder=_render_booking_thank_you,
    ),
    TestKind(
        kind="booking.no_show_followup",
        audience="Customer after missed appointment",
        builder=_render_booking_no_show_followup,
    ),
    TestKind(
        kind="payment.receipt",
        audience="Customer after payment recorded",
        builder=_render_payment_receipt,
    ),
    TestKind(
        kind="staff.schedule_published",
        audience="Staff with shifts that week",
        builder=_render_staff_schedule_published,
    ),
    TestKind(
        kind="staff.shift_edited",
        audience="Staffer whose shift changed",
        builder=_render_staff_shift_edited,
    ),
    TestKind(
        kind="staff.shift_deleted",
        audience="Staffer whose shift was removed",
        builder=_render_staff_shift_deleted,
    ),
    TestKind(
        kind="staff.shift_added",
        audience="Staffer with a newly-added shift",
        builder=_render_staff_shift_added,
    ),
    TestKind(
        kind="admin.time_off_requested",
        audience="Admins when staff requests time off",
        builder=_render_admin_time_off_requested,
    ),
    TestKind(
        kind="staff.time_off_approved",
        audience="Requester after approval",
        builder=_render_staff_time_off_approved,
    ),
    TestKind(
        kind="staff.time_off_denied",
        audience="Requester after denial",
        builder=_render_staff_time_off_denied,
    ),
    TestKind(
        kind="staff.time_off_amended",
        audience="Requester after owner amends pending request",
        builder=_render_staff_time_off_amended,
    ),
    TestKind(
        kind="staff.missing_clock_out",
        audience="Staffer with missing clock-out",
        builder=_render_staff_missing_clock_out,
    ),
    TestKind(
        kind="admin.missing_clock_out",
        audience="Admins reviewing missing clock-out",
        builder=_render_admin_missing_clock_out,
    ),
    # Additional kinds get appended here in the order from
    # docs/STAFF_EMAIL_BUILD_TRACKER.md as their renderers ship.
]


def _by_kind(kind: str) -> TestKind | None:
    for entry in REGISTRY:
        if entry.kind == kind:
            return entry
    return None


# ─── CLI ───────────────────────────────────────────────────────────────────


def _print_list() -> None:
    print(f"{'KIND':40s}  AUDIENCE")
    print("-" * 70)
    for entry in REGISTRY:
        print(f"{entry.kind:40s}  {entry.audience}")
    print()
    print(f"{len(REGISTRY)} registered fixture(s).")
    print("See docs/STAFF_EMAIL_BUILD_TRACKER.md for the full catalog.")


def _send_one(entry: TestKind) -> None:
    transport = get_email_transport()
    payload = entry.builder()
    print(
        f"[{entry.kind}] rendered subject={payload.subject!r} "
        f"intended_to={payload.to}"
    )
    transport.send(payload)
    landed = EMAIL_DEV_REDIRECT or payload.to
    print(f"[{entry.kind}] sent — should land at {landed}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--kind",
        help="Kind to render (e.g. booking.confirmation). Use 'all' to fire every registered kind.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List registered kinds and exit.",
    )
    args = p.parse_args()

    if args.list:
        _print_list()
        return 0

    if not args.kind:
        p.print_help()
        return 1

    if not EMAIL_DEV_REDIRECT:
        print(
            "WARNING: EMAIL_DEV_REDIRECT is not set. Emails will go to their "
            "real recipients (or be no-op'd by NullEmailTransport if SMTP is "
            "unconfigured). Set EMAIL_DEV_REDIRECT in .env to redirect every "
            "test send to a single inbox.",
            file=sys.stderr,
        )

    if args.kind == "all":
        for entry in REGISTRY:
            _send_one(entry)
        return 0

    entry = _by_kind(args.kind)
    if entry is None:
        print(f"ERROR: unknown kind {args.kind!r}", file=sys.stderr)
        print("Run with --list to see registered kinds.", file=sys.stderr)
        return 1

    _send_one(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
