"""Reminder + quote-expiry cron.

Phase 11. Two daily passes that nudge customers and tidy up stale
state:

  - ``run_reminder_pass(db, today)`` walks every unpaid installment
    on a sent or partial invoice and fires reminder1/2/3 when the
    business-profile offset rule matches today. Idempotent: per-slot
    ``installment_reminder_state.*_sent_at`` stamps prevent a second
    run on the same day from re-sending.
  - ``run_quote_expiry_pass(db, today)`` flips quotes past their
    ``expires_at`` to status ``expired`` and logs ``quote.expired``.

Design notes:

  - **Sync-send, not queue.** The plan suggested enqueuing into
    ``notification_jobs``, but that table's schema is appointment-
    tied (Phase 7 ran into the same wall). The cron loop already
    serializes work; sync send keeps the path readable and ensures
    a stamp only lands on a successful dispatch. SMTP failure rolls
    the whole installment forward to the next pass.
  - **Idempotency lives in the row, not the cron.** Each slot has its
    own ``*_sent_at`` so reminder1 firing today doesn't block
    reminder2 from firing tomorrow.
  - **Late fee is an opt-in side effect of reminder3.** When the
    business profile has a non-zero ``reminder_late_fee_cents`` (or
    ``reminder_late_fee_pct``) and reminder3 fires for an installment
    whose ``late_fee_applied_at`` is NULL, ``invoice_service.
    append_late_fee`` runs after the email send. Email-failed-but-
    fee-already-applied is an OK failure mode; staff sees the line
    item, customer sees the fee on the next reminder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import APP_TIMEZONE
from database.models import (
    BusinessProfile,
    InstallmentReminderState,
    Invoice,
    InvoiceInstallment,
    InvoiceInvitation,
    Quote,
)
from services import activity_log, invoice_service, portal_email
from services.portal_email import PortalEmailError

log = logging.getLogger(__name__)


def _shop_tz() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE)
    except Exception:  # pragma: no cover — bad config
        log.exception("reminder_runner.bad_timezone")
        return ZoneInfo("UTC")


def _today_in_shop_tz() -> date:
    """Authoritative "today" for daily passes.

    Production runs in UTC but the shop operates in ``APP_TIMEZONE``;
    a restart late in the UTC evening can cross midnight UTC while
    still being the prior business day locally. Always compute against
    the shop tz so reminder offsets and quote expiry trigger on the
    customer's calendar, not the server's.
    """
    return datetime.now(_shop_tz()).date()


def _to_shop_date(dt: datetime) -> date:
    """Convert a stored UTC timestamp to a shop-calendar date.

    ``invoice.sent_at`` is written tz-aware UTC, but legacy rows or
    naive datetimes from a non-tz column may slip through. Treat
    naive as UTC so the conversion is well-defined.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_shop_tz()).date()


# ---------------------------------------------------------------------------
# Reminder pass
# ---------------------------------------------------------------------------


@dataclass
class _ReminderSlot:
    index: int  # 1, 2, or 3
    enabled: bool
    days_offset: int
    offset_basis: str  # 'before_due' | 'after_due' | 'after_sent'


def _slots_from_profile(profile: BusinessProfile) -> list[_ReminderSlot]:
    return [
        _ReminderSlot(
            index=1,
            enabled=bool(profile.reminder1_enabled),
            days_offset=int(profile.reminder1_days_offset or 0),
            offset_basis=profile.reminder1_offset_basis or "before_due",
        ),
        _ReminderSlot(
            index=2,
            enabled=bool(profile.reminder2_enabled),
            days_offset=int(profile.reminder2_days_offset or 0),
            offset_basis=profile.reminder2_offset_basis or "before_due",
        ),
        _ReminderSlot(
            index=3,
            enabled=bool(profile.reminder3_enabled),
            days_offset=int(profile.reminder3_days_offset or 0),
            offset_basis=profile.reminder3_offset_basis or "before_due",
        ),
    ]


def _slot_target_date(
    slot: _ReminderSlot,
    *,
    installment_due: date,
    invoice_sent_at: datetime | None,
) -> date | None:
    """When does ``slot`` fire for an installment with this due date?

    Returns the target date (``today == target`` means fire). Returns
    ``None`` for misconfigured / impossible combinations (e.g. an
    ``after_sent`` rule on an invoice with no sent_at — shouldn't
    happen since reminders only run on sent/partial invoices, but
    defensive belt).
    """
    days = int(slot.days_offset or 0)
    if slot.offset_basis == "before_due":
        return installment_due - timedelta(days=days)
    if slot.offset_basis == "after_due":
        return installment_due + timedelta(days=days)
    if slot.offset_basis == "after_sent":
        if invoice_sent_at is None:
            return None
        return _to_shop_date(invoice_sent_at) + timedelta(days=days)
    return None


def _ensure_reminder_state(
    db: Session, installment_id: int
) -> InstallmentReminderState:
    """Idempotent upsert of the per-installment state row."""
    state = db.get(InstallmentReminderState, installment_id)
    if state is None:
        state = InstallmentReminderState(installment_id=installment_id)
        db.add(state)
        db.flush()
    return state


def _slot_sent_at(state: InstallmentReminderState, idx: int) -> datetime | None:
    return getattr(state, f"reminder{idx}_sent_at")


def _stamp_slot(
    state: InstallmentReminderState, idx: int, when: datetime
) -> None:
    setattr(state, f"reminder{idx}_sent_at", when)
    state.updated_at = when


def _live_invitation(
    db: Session, invoice_id: int
) -> InvoiceInvitation | None:
    """Pick the invitation we'll address the reminder to.

    Prefer the one tied to the invoice's billing contact. If that's
    been revoked / soft-deleted / expired, fall back to the oldest
    live invitation (which is the one the customer probably
    bookmarked). Mirrors the portal's three-gate lookup so we never
    email a link the portal would itself reject.
    """
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        return None
    now_utc = datetime.now(timezone.utc)
    primary = (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.invoice_id == invoice_id)
        .filter(InvoiceInvitation.contact_id == invoice.contact_id)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
        .filter(
            (InvoiceInvitation.expires_at.is_(None))
            | (InvoiceInvitation.expires_at > now_utc)
        )
        .order_by(InvoiceInvitation.id.asc())
        .first()
    )
    if primary is not None:
        return primary
    return (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.invoice_id == invoice_id)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
        .filter(
            (InvoiceInvitation.expires_at.is_(None))
            | (InvoiceInvitation.expires_at > now_utc)
        )
        .order_by(InvoiceInvitation.id.asc())
        .first()
    )


def _format_due_date(d: date, *, today: date) -> str:
    """Customer-facing copy. Avoids "tomorrow"/"yesterday" in favor of
    a calendar date that doesn't drift if the email is read late."""
    delta = (d - today).days
    if delta == 0:
        return f"today ({d.strftime('%B %-d')})"
    return d.strftime("%B %-d, %Y")


def _compute_late_fee_cents(
    profile: BusinessProfile, *, balance_cents: int
) -> int:
    """Flat-or-percent, whichever is non-zero. Flat wins if both are
    set. Returns 0 when neither is configured."""
    flat = int(profile.reminder_late_fee_cents or 0)
    if flat > 0:
        return flat
    pct = profile.reminder_late_fee_pct
    if pct is None or Decimal(str(pct)) <= 0:
        return 0
    fee = (Decimal(int(balance_cents)) * Decimal(str(pct))).quantize(
        Decimal("1"), rounding=ROUND_HALF_EVEN
    )
    return max(0, int(fee))


@dataclass
class ReminderPassResult:
    sent_count: int
    skipped_no_email: int
    smtp_failures: int
    late_fees_applied: int


def run_reminder_pass(
    db: Session, *, today: date | None = None
) -> ReminderPassResult:
    today = today or _today_in_shop_tz()
    now_dt = datetime.now(timezone.utc)

    profile = db.get(BusinessProfile, 1)
    if profile is None:
        log.warning("reminder_runner.profile_missing")
        return ReminderPassResult(0, 0, 0, 0)

    slots = [s for s in _slots_from_profile(profile) if s.enabled]
    if not slots:
        return ReminderPassResult(0, 0, 0, 0)

    # Pull every unpaid installment on a live sent/partial invoice.
    # Single query — the shop has hundreds of installments, not
    # millions, and a bulk fetch keeps the cron loop simple.
    rows = db.execute(
        select(
            InvoiceInstallment.id,
            InvoiceInstallment.invoice_id,
            InvoiceInstallment.due_date,
            InvoiceInstallment.label,
            InvoiceInstallment.amount_cents,
            Invoice.invoice_number,
            Invoice.sent_at,
            Invoice.balance_cents,
        )
        .join(Invoice, Invoice.id == InvoiceInstallment.invoice_id)
        .where(InvoiceInstallment.paid_at.is_(None))
        .where(Invoice.deleted_at.is_(None))
        .where(Invoice.status.in_(("sent", "partial")))
    ).all()

    sent_count = 0
    skipped_no_email = 0
    smtp_failures = 0
    late_fees_applied = 0

    for r in rows:
        for slot in slots:
            target = _slot_target_date(
                slot,
                installment_due=r.due_date,
                invoice_sent_at=r.sent_at,
            )
            if target is None or target != today:
                continue

            state = _ensure_reminder_state(db, r.id)
            if _slot_sent_at(state, slot.index) is not None:
                continue  # idempotent: already sent on a prior pass

            invitation = _live_invitation(db, r.invoice_id)
            if invitation is None:
                # No way to address the customer; stamp the slot so we
                # don't keep retrying every day for an invoice whose
                # invitations were all revoked.
                _stamp_slot(state, slot.index, now_dt)
                db.flush()
                continue

            invoice = db.get(Invoice, r.invoice_id)
            try:
                emailed = portal_email.send_invoice_reminder(
                    db,
                    invoice=invoice,
                    invitation=invitation,
                    installment_label=r.label,
                    installment_amount_cents=int(r.amount_cents),
                    due_date_text=_format_due_date(r.due_date, today=today),
                    reminder_index=slot.index,
                )
            except PortalEmailError:
                # Don't stamp on SMTP failure — let the next pass try
                # again. The exception was already logged.
                smtp_failures += 1
                continue

            _stamp_slot(state, slot.index, now_dt)
            db.flush()

            if emailed:
                sent_count += 1
            else:
                skipped_no_email += 1

            activity_log.log_activity(
                db,
                event_id=invoice.event_id,
                actor_kind="system",
                actor_user_id=None,
                activity_type=activity_log.INVOICE_REMINDER_SENT,
                subject_kind="invoice",
                subject_id=invoice.id,
                payload={
                    "invoice_number": invoice.invoice_number,
                    "installment_id": int(r.id),
                    "reminder_index": slot.index,
                    "delivered": emailed,
                },
            )

            # Late fee fires only on reminder3, only when the profile
            # configures one, and only once per installment.
            if (
                slot.index == 3
                and state.late_fee_applied_at is None
            ):
                fee = _compute_late_fee_cents(
                    profile, balance_cents=int(r.balance_cents or 0)
                )
                if fee > 0:
                    try:
                        invoice_service.append_late_fee(
                            db,
                            invoice_id=invoice.id,
                            fee_cents=fee,
                            actor_user_id=None,
                        )
                        state.late_fee_applied_at = now_dt
                        db.flush()
                        late_fees_applied += 1
                    except invoice_service.InvoiceServiceError as exc:
                        log.warning(
                            "reminder_runner.late_fee_failed",
                            extra={
                                "invoice_id": invoice.id,
                                "installment_id": int(r.id),
                                "code": exc.code,
                            },
                        )

    db.commit()
    log.info(
        "reminder_runner.pass_complete",
        extra={
            "today": today.isoformat(),
            "sent": sent_count,
            "skipped_no_email": skipped_no_email,
            "smtp_failures": smtp_failures,
            "late_fees_applied": late_fees_applied,
        },
    )
    return ReminderPassResult(
        sent_count=sent_count,
        skipped_no_email=skipped_no_email,
        smtp_failures=smtp_failures,
        late_fees_applied=late_fees_applied,
    )


# ---------------------------------------------------------------------------
# Quote-expiry pass
# ---------------------------------------------------------------------------


@dataclass
class QuoteExpiryResult:
    expired_count: int


def run_quote_expiry_pass(
    db: Session, *, today: date | None = None
) -> QuoteExpiryResult:
    """Flip ``sent`` quotes past their ``expires_at`` to ``expired``.

    Approved/converted/cancelled quotes are intentionally left alone
    — those are terminal states and don't get an automatic flip.
    """
    today = today or _today_in_shop_tz()
    now_dt = datetime.now(timezone.utc)

    rows = (
        db.query(Quote)
        .filter(Quote.deleted_at.is_(None))
        .filter(Quote.status == "sent")
        .filter(Quote.expires_at.is_not(None))
        .filter(Quote.expires_at < today)
        .all()
    )
    for quote in rows:
        quote.status = "expired"
        quote.updated_at = now_dt
        db.flush()
        activity_log.log_activity(
            db,
            event_id=quote.event_id,
            actor_kind="system",
            actor_user_id=None,
            activity_type=activity_log.QUOTE_EXPIRED,
            subject_kind="quote",
            subject_id=quote.id,
            payload={"quote_number": quote.quote_number},
        )
    db.commit()
    log.info(
        "reminder_runner.quote_expiry_complete",
        extra={"today": today.isoformat(), "expired": len(rows)},
    )
    return QuoteExpiryResult(expired_count=len(rows))


# ---------------------------------------------------------------------------
# Single entrypoint for the daily worker
# ---------------------------------------------------------------------------


def run_daily(db: Session) -> tuple[ReminderPassResult, QuoteExpiryResult]:
    """Both passes back-to-back. The worker calls this once a day."""
    return run_reminder_pass(db), run_quote_expiry_pass(db)
