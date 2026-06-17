"""Dashboard rollups — accounts receivable, recent payments, awaiting-
signature quotes.

Phase 10. The widgets on the staff Dashboard pull from short, focused
queries here. Each rollup is a single SQL — the widgets render a
summary + a tiny list, not a paginated grid.

Why a separate module:

  - The aggregations cross domains (invoices, payments, quotes) and
    don't belong inside any one of the per-entity services.
  - Future widgets (deposits this month, conversion funnel, stylist
    workload) get a natural home without bloating invoice_service.

The shop runs at quince-event scale (low hundreds of live invoices),
so none of these queries needs special indexing beyond what already
exists. If a widget ever takes more than a few hundred ms, the answer
is a partial index, not a materialized view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Appointment,
    Contact,
    Event,
    Invoice,
    Payment,
    Quote,
    RefundEvent,
    StaffPunch,
    User,
)
from services.booking_service import shop_tz
from services.event_workflow import all_statuses


# ---------------------------------------------------------------------------
# Accounts receivable summary
# ---------------------------------------------------------------------------


@dataclass
class ARSummary:
    """Top-of-dashboard AR snapshot. All amounts in cents.

    `outstanding_balance_cents` and `overdue_balance_cents` use the same
    `status IN ('sent', 'partial')` filter as the kanban pill so the
    two surfaces always agree. `overdue` is "due_date < today".

    `deposits_collected_this_month_cents` is **gross payments dated
    this month minus refund_events created this month**. We can't use
    `payments.refunded_cents` because that's lifetime state — it would
    subtract refunds that happened in earlier months from the
    current-month gross, and miss refunds created this month against
    payments from earlier months. The pair (gross-by-payment-date,
    refunds-by-event-date) reads "money in this month" correctly.
    """

    outstanding_balance_cents: int
    outstanding_invoice_count: int
    overdue_balance_cents: int
    overdue_invoice_count: int
    deposits_collected_this_month_cents: int


def ar_summary(db: Session, *, today: date | None = None) -> ARSummary:
    today = today or date.today()
    month_start = today.replace(day=1)

    # Outstanding rollup: only live, money-owing invoices count.
    outstanding_row = db.execute(
        select(
            func.coalesce(func.sum(Invoice.balance_cents), 0).label("balance"),
            func.count(Invoice.id).label("n"),
        )
        .where(Invoice.deleted_at.is_(None))
        .where(Invoice.status.in_(("sent", "partial")))
    ).first()

    overdue_row = db.execute(
        select(
            func.coalesce(func.sum(Invoice.balance_cents), 0).label("balance"),
            func.count(Invoice.id).label("n"),
        )
        .where(Invoice.deleted_at.is_(None))
        .where(Invoice.status.in_(("sent", "partial")))
        .where(Invoice.due_date.is_not(None))
        .where(Invoice.due_date < today)
    ).first()

    # Gross payments dated this month. Excludes failed / cancelled
    # payments — those never represented real money in.
    gross_row = db.execute(
        select(
            func.coalesce(func.sum(Payment.amount_cents), 0).label("gross")
        )
        .where(Payment.deleted_at.is_(None))
        .where(Payment.status.in_(("completed", "partially_refunded", "refunded")))
        .where(Payment.payment_date >= month_start)
        .where(Payment.payment_date <= today)
    ).first()

    # Refunds events created this month, regardless of which month the
    # underlying payment was dated. `created_at` is the timestamp the
    # refund was recorded in our system; it's the right axis for "how
    # much did we refund this month".
    month_start_dt = datetime.combine(
        month_start, datetime.min.time(), tzinfo=timezone.utc
    )
    refunds_row = db.execute(
        select(
            func.coalesce(func.sum(RefundEvent.amount_cents), 0).label("refunded")
        )
        .where(RefundEvent.created_at >= month_start_dt)
    ).first()

    # Raw subtraction — no clamp. A month where prior-period refunds
    # exceed current-period gross is genuinely negative net cash, and
    # hiding it behind a max(0, ...) would mask exactly the signal
    # staff most needs to see. The widget renders the negative figure.
    deposits_net = int(gross_row.gross or 0) - int(refunds_row.refunded or 0)

    return ARSummary(
        outstanding_balance_cents=int(outstanding_row.balance or 0),
        outstanding_invoice_count=int(outstanding_row.n or 0),
        overdue_balance_cents=int(overdue_row.balance or 0),
        overdue_invoice_count=int(overdue_row.n or 0),
        deposits_collected_this_month_cents=deposits_net,
    )


# ---------------------------------------------------------------------------
# Recent payments
# ---------------------------------------------------------------------------


@dataclass
class RecentPayment:
    id: int
    payment_number: str | None
    contact_id: int
    contact_name: str
    amount_cents: int
    method: str
    status: str
    payment_date: date
    created_at: datetime
    # First allocated invoice's event id, used by the UI to deep-link
    # the row into the right event. NULL when the payment landed
    # entirely in the unapplied pool.
    event_id: int | None


def recent_payments(db: Session, *, limit: int = 10) -> list[RecentPayment]:
    """Last N payments across all events, newest first.

    Joins `payment_allocations` and `invoices` so the row knows which
    event to deep-link into. A payment with no allocation (everything
    landed in the unapplied pool) returns ``event_id=None`` and the
    caller can fall back to the contact's most recent event or a
    "Payment received" row that doesn't link anywhere.
    """
    limit = max(1, min(int(limit), 50))

    # Primary list: sort by created_at DESC then id DESC for ties.
    payments = (
        db.query(Payment, Contact.display_name)
        .join(Contact, Contact.id == Payment.contact_id)
        .filter(Payment.deleted_at.is_(None))
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(limit)
        .all()
    )
    if not payments:
        return []
    payment_ids = [p.id for (p, _name) in payments]

    # Resolve "primary event" per payment via the oldest allocation.
    # Two-query lookup keeps the SQL readable and avoids a window
    # function for what's a bounded ~10-row list. A payment with no
    # allocations stays out of the map and gets event_id=None.
    from database.models import PaymentAllocation

    alloc_min = (
        select(
            PaymentAllocation.payment_id.label("pid"),
            func.min(PaymentAllocation.id).label("alloc_id"),
        )
        .where(PaymentAllocation.payment_id.in_(payment_ids))
        .group_by(PaymentAllocation.payment_id)
        .subquery()
    )
    join_rows = db.execute(
        select(
            alloc_min.c.pid,
            Invoice.event_id.label("event_id"),
        )
        .join(PaymentAllocation, PaymentAllocation.id == alloc_min.c.alloc_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
    ).all()
    event_id_by_payment = {r.pid: int(r.event_id) for r in join_rows}

    out: list[RecentPayment] = []
    for p, contact_name in payments:
        out.append(
            RecentPayment(
                id=int(p.id),
                payment_number=p.payment_number,
                contact_id=int(p.contact_id),
                contact_name=contact_name,
                amount_cents=int(p.amount_cents),
                method=p.method,
                status=p.status,
                payment_date=p.payment_date,
                created_at=p.created_at,
                event_id=event_id_by_payment.get(int(p.id)),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Quotes awaiting signature
# ---------------------------------------------------------------------------


@dataclass
class AwaitingSignatureQuote:
    id: int
    quote_number: str | None
    event_id: int
    contact_id: int
    contact_name: str
    total_cents: int
    sent_at: datetime
    days_since_sent: int


def quotes_awaiting_signature(
    db: Session, *, min_age_days: int = 3, limit: int = 25
) -> list[AwaitingSignatureQuote]:
    """`sent` quotes older than `min_age_days`. Newest stale first.

    The default `min_age_days=3` matches the plan's "older than 3 days"
    spec — anything younger isn't worth nudging staff about. Bumping
    the threshold (e.g., to 7 for slower seasons) is a single arg.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(min_age_days))
    rows = (
        db.query(Quote, Contact.display_name)
        .join(Contact, Contact.id == Quote.contact_id)
        .filter(Quote.deleted_at.is_(None))
        .filter(Quote.status == "sent")
        .filter(Quote.sent_at.is_not(None))
        .filter(Quote.sent_at <= cutoff)
        .order_by(Quote.sent_at.asc())  # oldest first — most stale at top
        .limit(max(1, min(int(limit), 100)))
        .all()
    )
    now = datetime.now(timezone.utc)
    out: list[AwaitingSignatureQuote] = []
    for q, name in rows:
        delta = now - q.sent_at
        out.append(
            AwaitingSignatureQuote(
                id=int(q.id),
                quote_number=q.quote_number,
                event_id=int(q.event_id),
                contact_id=int(q.contact_id),
                contact_name=name,
                total_cents=int(q.total_cents or 0),
                sent_at=q.sent_at,
                days_since_sent=int(delta.total_seconds() // 86400),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Today's agenda
# ---------------------------------------------------------------------------


@dataclass
class AgendaItem:
    id: int
    slot_start_at: datetime
    slot_end_at: datetime
    party_size_bucket: str
    status: str
    crm_event_id: int | None
    contact_id: int | None
    # Best display name: contact.display_name when the appointment is linked
    # to a contact, else "<celebrant_first> <celebrant_last>" off the booking
    # form. Either may show up — the widget renders the string we give it.
    display_name: str


def todays_agenda(db: Session) -> dict:
    """Today's appointments in APP_TIMEZONE, ordered by start time.

    Mirrors the ``sales_appointments.list_today`` filter — convert the
    local day bounds to UTC up front so the query stays index-friendly on
    ``slot_start_at`` instead of wrapping the column in ``AT TIME ZONE``.

    Returns a serializable dict with ``date``, ``timezone``, and
    ``appointments`` (a list of AgendaItem-equivalent records). The
    router can pass this straight through; widget consumes it as JSON.
    """
    tz = shop_tz()
    today_local = datetime.now(tz).date()
    start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    rows = db.execute(
        select(Appointment, Contact.display_name)
        .outerjoin(Contact, Contact.id == Appointment.contact_id)
        .where(Appointment.slot_start_at >= start_utc)
        .where(Appointment.slot_start_at < end_utc)
        .order_by(Appointment.slot_start_at)
    ).all()

    items: list[AgendaItem] = []
    for appt, contact_name in rows:
        if contact_name:
            name = contact_name
        else:
            parts = [appt.celebrant_first_name, appt.celebrant_last_name]
            name = " ".join(p for p in parts if p).strip() or "Unknown"
        items.append(
            AgendaItem(
                id=int(appt.id),
                slot_start_at=appt.slot_start_at,
                slot_end_at=appt.slot_end_at,
                party_size_bucket=appt.party_size_bucket,
                status=appt.status,
                crm_event_id=(
                    int(appt.crm_event_id) if appt.crm_event_id else None
                ),
                contact_id=int(appt.contact_id) if appt.contact_id else None,
                display_name=name,
            )
        )

    return {
        "date": today_local.isoformat(),
        "timezone": str(tz),
        "appointments": items,
    }


# ---------------------------------------------------------------------------
# Sales per labor hour leaderboard
# ---------------------------------------------------------------------------


@dataclass
class SPLHLeaderboardRow:
    user_id: int
    username: str | None
    full_name: str | None
    revenue_cents: int
    invoice_count: int
    actual_hours: float
    splh_cents_per_hour: int | None


@dataclass
class SPLHLeaderboard:
    from_date: date
    to_date: date
    revenue_basis: str
    rows: list[SPLHLeaderboardRow]


def _current_week_bounds(today: date | None = None) -> tuple[date, date]:
    tz = shop_tz()
    local_today = today or datetime.now(tz).date()
    week_start = local_today - timedelta(days=local_today.weekday())
    return week_start, week_start + timedelta(days=6)


def _utc_window(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    tz = shop_tz()
    start_local = datetime.combine(start_date, time.min, tzinfo=tz)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _paired_hours_by_user(rows: list[StaffPunch]) -> dict[int, float]:
    totals: dict[int, float] = {}
    open_by_user: dict[int, StaffPunch] = {}
    for punch in rows:
        if punch.status == "void":
            continue
        uid = int(punch.user_id)
        if punch.direction == "in":
            open_by_user[uid] = punch
            continue
        open_in = open_by_user.get(uid)
        if open_in is None:
            continue
        delta = (punch.punched_at - open_in.punched_at).total_seconds()
        if delta > 0:
            totals[uid] = totals.get(uid, 0.0) + delta / 3600.0
        open_by_user.pop(uid, None)
    return totals


def splh_leaderboard(
    db: Session,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 10,
) -> SPLHLeaderboard:
    """Sales per labor hour, cash-basis, for the selected week.

    Revenue is `SUM(invoices.paid_to_date_cents)` for invoices issued
    in the range and attributed via `sold_by_user_id`. Hours are paired
    actual clock-in/clock-out punches in the same boutique-local date
    window. This keeps the widget aligned with payroll reality instead
    of scheduled hours.
    """
    if from_date is None or to_date is None:
        default_from, default_to = _current_week_bounds()
        from_date = from_date or default_from
        to_date = to_date or default_to
    if to_date < from_date:
        from_date, to_date = to_date, from_date
    limit = max(1, min(int(limit), 25))

    revenue_rows = db.execute(
        select(
            Invoice.sold_by_user_id.label("user_id"),
            func.coalesce(func.sum(Invoice.paid_to_date_cents), 0).label(
                "revenue_cents"
            ),
            func.count(Invoice.id).label("invoice_count"),
        )
        .where(Invoice.deleted_at.is_(None))
        .where(Invoice.sold_by_user_id.is_not(None))
        .where(Invoice.issue_date >= from_date)
        .where(Invoice.issue_date <= to_date)
        .where(Invoice.status.notin_(("draft", "cancelled", "reversed")))
        .where(Invoice.paid_to_date_cents > 0)
        .group_by(Invoice.sold_by_user_id)
    ).all()
    revenue_by_user = {
        int(r.user_id): int(r.revenue_cents or 0) for r in revenue_rows
    }
    invoice_count_by_user = {
        int(r.user_id): int(r.invoice_count or 0) for r in revenue_rows
    }

    start_utc, end_utc = _utc_window(from_date, to_date)
    punch_rows = (
        db.execute(
            select(StaffPunch)
            .where(StaffPunch.punched_at >= start_utc)
            .where(StaffPunch.punched_at < end_utc)
            .order_by(StaffPunch.user_id, StaffPunch.punched_at, StaffPunch.id)
        )
        .scalars()
        .all()
    )
    hours_by_user = _paired_hours_by_user(list(punch_rows))

    user_ids = set(revenue_by_user) | set(hours_by_user)
    user_map: dict[int, User] = {}
    if user_ids:
        user_map = {
            int(u.id): u
            for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars()
        }

    rows: list[SPLHLeaderboardRow] = []
    for uid in sorted(user_ids):
        hours = round(float(hours_by_user.get(uid, 0.0)), 2)
        revenue = int(revenue_by_user.get(uid, 0))
        splh = int(round(revenue / hours)) if hours > 0 else None
        user = user_map.get(uid)
        rows.append(
            SPLHLeaderboardRow(
                user_id=uid,
                username=user.username if user else None,
                full_name=user.full_name if user else None,
                revenue_cents=revenue,
                invoice_count=int(invoice_count_by_user.get(uid, 0)),
                actual_hours=hours,
                splh_cents_per_hour=splh,
            )
        )

    rows.sort(
        key=lambda r: (
            r.splh_cents_per_hour is None,
            -(r.splh_cents_per_hour or 0),
            -r.revenue_cents,
            r.full_name or r.username or "",
        )
    )
    return SPLHLeaderboard(
        from_date=from_date,
        to_date=to_date,
        revenue_basis="paid_to_date_cents",
        rows=rows[:limit],
    )


# ---------------------------------------------------------------------------
# Pipeline lane counts
# ---------------------------------------------------------------------------


@dataclass
class PipelineLaneCount:
    code: str
    label: str
    sort_order: int
    is_terminal: bool
    count: int


def pipeline_counts(
    db: Session, *, event_type: str = "quinceanera"
) -> list[PipelineLaneCount]:
    """Current row count per workflow status. Lanes with no rows still
    appear with ``count=0`` so the widget shows the full pipeline shape,
    not just the lanes that happen to be populated.
    """
    statuses = all_statuses(event_type)

    rows = db.execute(
        select(Event.status, func.count(Event.id))
        .where(Event.event_type == event_type)
        .where(Event.deleted_at.is_(None))
        .group_by(Event.status)
    ).all()
    by_code = {code: int(count) for code, count in rows}

    return [
        PipelineLaneCount(
            code=s.code,
            label=s.label,
            sort_order=s.sort_order,
            is_terminal=s.is_terminal,
            count=by_code.get(s.code, 0),
        )
        for s in statuses
    ]
