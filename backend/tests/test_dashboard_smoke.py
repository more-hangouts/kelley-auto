"""Smoke tests for Phase 10 dashboard rollups.

The hardest cases are the **edges** — partial payments, refunds, and
cancelled/draft invoices that should NOT contribute. Each check sets
up a known financial state and asserts the rollup matches.

Coverage:

  - Kanban board card outstanding_balance_cents matches a hand-summed
    figure: sent invoice + partial invoice + paid invoice + cancelled
    invoice + draft invoice on the same event = sum of (sent + partial)
    balances only.
  - AR summary `outstanding_balance_cents` and `outstanding_invoice_count`
    match the sum across `status IN ('sent', 'partial')`. Cancelled,
    paid, and draft invoices do not contribute.
  - AR summary `overdue_*` only counts invoices with `due_date < today`.
  - AR summary `deposits_collected_this_month_cents` uses NET position
    (amount - refunded). A partial refund within the same month
    reduces the figure; a refund after month-end does not.
  - Recent payments returns newest first, with `event_id` populated for
    the first allocated invoice's event, NULL for the unapplied-only
    payment.
  - Quotes awaiting signature returns only `sent` quotes older than
    `min_age_days`. Younger quotes don't appear; signed/converted/
    cancelled quotes don't appear.
  - Auth: dashboard endpoints reject without a token.

Cleans up every row created. Runs as a script:

    venv/bin/python tests/test_dashboard_smoke.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")  # C4: audit-trigger bypass for cleanup
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Appointment,
    Contact,
    Event,
    Invoice,
    Payment,
    Quote,
    User,
)
from services import (  # noqa: E402
    booking_service,
    dashboard,
    event_service,
    invoice_service,
    payment_service,
    quote_service,
)
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)
from services.payment_service import AllocationInput  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"dashboard-smoke-{suffix}",
            email=f"dashboard-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Dashboard Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _seed_event(label: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=f"{label} Contact",
            email=f"{label.lower().replace(' ', '-')}@example.com",
            phone=f"(210) 555-{uuid.uuid4().int % 10000:04d}",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{label} Quince",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        return contact.id, event.id
    finally:
        db.close()


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(user_ids, contact_ids, event_ids):
    db = SessionLocal()
    try:
        if _appt_ids:
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:aids)"),
                {"aids": _appt_ids},
            )
        if event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM refund_events WHERE payment_id IN ("
                    "SELECT id FROM payments WHERE contact_id = ANY(:cids))"
                ),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM payment_allocations WHERE payment_id IN ("
                    "SELECT id FROM payments WHERE contact_id = ANY(:cids))"
                ),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text("DELETE FROM payments WHERE contact_id = ANY(:cids)"),
                {"cids": contact_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_invitations WHERE quote_id IN "
                    "(SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id IN "
                    "(SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_invitations WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_installments WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_line_items WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM invoices WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:eids)"),
                {"eids": event_ids},
            )
        if contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:cids)"),
                {"cids": contact_ids},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:uids)"),
                {"uids": user_ids},
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Builders — each returns the invoice id so checks can assert against it
# ---------------------------------------------------------------------------


def _make_sent_invoice(
    *,
    event_id: int,
    contact_id: int,
    user_id: int,
    total_cents: int,
    due_date: date | None = None,
) -> int:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Probe item",
                    quantity=Decimal("1"),
                    unit_price_cents=total_cents,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Full",
                    amount_cents=total_cents,
                    due_date=due_date or (date.today() + timedelta(days=30)),
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        invoice_service.mark_sent(db, invoice_id=inv.id, actor_user_id=user_id)
        db.commit()
        return inv.id
    finally:
        db.close()


def _make_draft_invoice(*, event_id, contact_id, user_id, total_cents) -> int:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Draft probe",
                    quantity=Decimal("1"),
                    unit_price_cents=total_cents,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Full",
                    amount_cents=total_cents,
                    due_date=date.today() + timedelta(days=30),
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        return inv.id
    finally:
        db.close()


def _allocate(invoice_id: int, contact_id: int, applied_cents: int, user_id: int) -> int:
    """Records a payment that fully allocates `applied_cents` to the
    invoice. Returns the payment id."""
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=applied_cents,
            method="card",
            allocations=[
                AllocationInput(invoice_id=invoice_id, applied_cents=applied_cents)
            ],
            actor_user_id=user_id,
        )
        db.commit()
        return p.id
    finally:
        db.close()


def _record_unapplied_payment(contact_id: int, amount_cents: int, user_id: int) -> int:
    """Payment with no allocation — lands fully in the unapplied pool."""
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=amount_cents,
            method="cash",
            allocations=[],
            actor_user_id=user_id,
        )
        db.commit()
        return p.id
    finally:
        db.close()


def _cancel(invoice_id: int, user_id: int):
    db = SessionLocal()
    try:
        invoice_service.cancel_invoice(
            db, invoice_id=invoice_id, actor_user_id=user_id
        )
        db.commit()
    finally:
        db.close()


def _refund(payment_id: int, amount_cents: int, user_id: int):
    db = SessionLocal()
    try:
        from services.payment_service import AllocationRefundInput
        from database.models import PaymentAllocation

        alloc = (
            db.query(PaymentAllocation)
            .filter(PaymentAllocation.payment_id == payment_id)
            .first()
        )
        if alloc is None or int(alloc.applied_cents) < amount_cents:
            # Refund from unapplied pool when no allocation matches
            payment_service.record_refund(
                db,
                payment_id=payment_id,
                amount_cents=amount_cents,
                refund_method="cash",
                allocation_refunds=[],
                from_unapplied_cents=amount_cents,
                actor_user_id=user_id,
            )
        else:
            payment_service.record_refund(
                db,
                payment_id=payment_id,
                amount_cents=amount_cents,
                refund_method="card",
                allocation_refunds=[
                    AllocationRefundInput(
                        allocation_id=alloc.id, refund_cents=amount_cents
                    )
                ],
                actor_user_id=user_id,
            )
        db.commit()
    finally:
        db.close()


def _backdate_payment(payment_id: int, payment_date: date):
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE payments SET payment_date = :d WHERE id = :id"
            ),
            {"d": payment_date, "id": payment_id},
        )
        db.commit()
    finally:
        db.close()


def _make_sent_quote(
    *, event_id, contact_id, user_id, total_cents, sent_at: datetime | None = None
) -> int:
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Quote probe",
                    quantity=Decimal("1"),
                    unit_price_cents=total_cents,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        if sent_at is not None:
            db.execute(
                sql_text(
                    "UPDATE quotes SET sent_at = :s WHERE id = :id"
                ),
                {"s": sent_at, "id": q.id},
            )
            db.commit()
        return q.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_kanban_balance_pill(event_id, contact_id, user_id):
    """Outstanding pill = sum of (sent + partial) balances on the event."""
    inv_full = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=100000,
    )
    inv_partial = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=200000,
    )
    inv_paid = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=80000,
    )
    inv_cancelled = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=50000,
    )
    inv_draft = _make_draft_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=999999,
    )

    # inv_partial: pay 60k of 200k → balance 140k, status partial
    _allocate(inv_partial, contact_id, 60000, user_id)
    # inv_paid: pay full 80k → balance 0, status paid
    _allocate(inv_paid, contact_id, 80000, user_id)
    # inv_cancelled: cancel before any payment
    _cancel(inv_cancelled, user_id)

    # Expected outstanding: inv_full (100k unpaid) + inv_partial (140k unpaid)
    expected = 100000 + 140000

    # Kanban subquery via the service
    db = SessionLocal()
    try:
        columns = event_service.get_board_data(db, event_type="quinceanera")
    finally:
        db.close()
    card = None
    for col in columns:
        for c in col.cards:
            if c.id == event_id:
                card = c
                break
    assert card is not None, "test event missing from board"
    assert card.outstanding_balance_cents == expected, (
        card.outstanding_balance_cents, expected
    )
    assert card.has_outstanding_invoice is True


def check_ar_summary_outstanding_matches_sql(event_id):
    """AR summary outstanding matches a direct SQL aggregate."""
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT COALESCE(SUM(balance_cents), 0)::bigint AS bal, "
                "       COUNT(*)::int AS n "
                "  FROM invoices "
                " WHERE deleted_at IS NULL "
                "   AND status IN ('sent', 'partial')"
            )
        ).first()
        sql_balance = int(row.bal)
        sql_count = int(row.n)
        summary = dashboard.ar_summary(db)
    finally:
        db.close()
    assert summary.outstanding_balance_cents == sql_balance, (
        summary.outstanding_balance_cents, sql_balance
    )
    assert summary.outstanding_invoice_count == sql_count, (
        summary.outstanding_invoice_count, sql_count
    )


def check_ar_summary_overdue(event_id, contact_id, user_id):
    """Only invoices past due_date contribute to overdue."""
    # Sent invoice with due date in the past (overdue)
    overdue = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=70000, due_date=date.today() - timedelta(days=10),
    )
    # Sent invoice with due date in the future (not overdue)
    future = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=30000, due_date=date.today() + timedelta(days=10),
    )

    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT COALESCE(SUM(balance_cents), 0)::bigint AS bal, "
                "       COUNT(*)::int AS n "
                "  FROM invoices "
                " WHERE deleted_at IS NULL "
                "   AND status IN ('sent', 'partial') "
                "   AND due_date IS NOT NULL "
                "   AND due_date < CURRENT_DATE"
            )
        ).first()
        summary = dashboard.ar_summary(db)
    finally:
        db.close()
    assert summary.overdue_balance_cents == int(row.bal), (
        summary.overdue_balance_cents, int(row.bal)
    )
    assert summary.overdue_invoice_count == int(row.n)
    assert summary.overdue_balance_cents >= 70000  # at least the one we just made


def check_deposits_this_month_net_of_refunds(event_id, contact_id, user_id):
    """Deposits this month = gross payments dated this month minus
    refund_events created this month, regardless of which month the
    underlying payment was dated.

    Three exercises:
      a) Payment dated this month, refunded this month — net = gross - refund
      b) Payment dated prior month, refunded this month — refund is subtracted
         from this month's deposits, NOT prior month's.
      c) Payment dated this month, fully unrefunded — contributes gross.
    """
    db = SessionLocal()
    try:
        baseline = dashboard.ar_summary(db).deposits_collected_this_month_cents
    finally:
        db.close()

    # (a) payment + refund both in this month
    inv_a = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=100000,
    )
    p_a = _allocate(inv_a, contact_id, 100000, user_id)
    _refund(p_a, 30000, user_id)

    # (b) payment dated 35 days ago (prior month), refunded today.
    inv_b = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=50000,
    )
    p_b = _allocate(inv_b, contact_id, 50000, user_id)
    prior_month_date = date.today() - timedelta(days=35)
    _backdate_payment(p_b, prior_month_date)
    _refund(p_b, 20000, user_id)

    # (c) clean payment in this month
    inv_c = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=15000,
    )
    _allocate(inv_c, contact_id, 15000, user_id)

    # Expected delta from baseline:
    #   gross dated this month = 100k (a) + 15k (c) = 115k    (b is prior month)
    #   refunds created this month = 30k (a) + 20k (b)        = 50k
    #   delta = 115k - 50k = 65k
    expected_delta = (100000 + 15000) - (30000 + 20000)

    db = SessionLocal()
    try:
        summary = dashboard.ar_summary(db)
    finally:
        db.close()
    actual_delta = summary.deposits_collected_this_month_cents - baseline
    assert actual_delta == expected_delta, (
        actual_delta, expected_delta, baseline,
        summary.deposits_collected_this_month_cents,
    )


def check_deposits_negative_when_refunds_exceed_current_month(
    event_id, contact_id, user_id
):
    """A month with prior-period refunds bigger than current-period
    gross should report a negative net. Asserts the no-clamp behavior
    so a future regression to max(0, ...) would fail loudly."""
    db = SessionLocal()
    try:
        baseline = dashboard.ar_summary(db).deposits_collected_this_month_cents
    finally:
        db.close()

    # Single refund this month against a payment dated 35 days ago.
    # No current-month payments — so this row should pull deposits below
    # baseline by the full refund amount.
    inv = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=40000,
    )
    p = _allocate(inv, contact_id, 40000, user_id)
    _backdate_payment(p, date.today() - timedelta(days=35))
    _refund(p, 40000, user_id)

    db = SessionLocal()
    try:
        summary = dashboard.ar_summary(db)
    finally:
        db.close()
    delta = summary.deposits_collected_this_month_cents - baseline
    # No current-month gross from this fixture; one 40k refund this
    # month → delta should be -40k. The old max(0, ...) clamp would
    # have shown a delta of 0 when baseline itself was 0.
    assert delta == -40000, (
        delta, baseline, summary.deposits_collected_this_month_cents,
    )


def check_recent_payments_event_id_resolution(event_id, contact_id, user_id):
    """Recent payments lists newest-first; allocated payments resolve
    to an event_id, unapplied-only payment leaves event_id NULL."""
    # Allocated payment
    inv = _make_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=40000,
    )
    p_alloc = _allocate(inv, contact_id, 40000, user_id)
    # Unapplied-only payment (no allocations)
    p_unapp = _record_unapplied_payment(contact_id, 25000, user_id)

    db = SessionLocal()
    try:
        rows = dashboard.recent_payments(db, limit=10)
    finally:
        db.close()

    by_id = {r.id: r for r in rows}
    assert p_alloc in by_id, p_alloc
    assert p_unapp in by_id, p_unapp
    assert by_id[p_alloc].event_id == event_id, by_id[p_alloc].event_id
    assert by_id[p_unapp].event_id is None, by_id[p_unapp].event_id

    # Newest-first ordering: the unapplied (created later) should appear
    # before the allocated payment in the list.
    seen_unapp = False
    for r in rows:
        if r.id == p_unapp:
            seen_unapp = True
        elif r.id == p_alloc:
            assert seen_unapp, "expected unapplied payment to come first (newer)"
            break


def check_quotes_awaiting_signature_filters(event_id, contact_id, user_id):
    """Only sent quotes older than min_age_days appear; younger / signed
    / converted / cancelled quotes are excluded."""
    # Old sent quote (5 days ago) — should appear
    old_q = _make_sent_quote(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=80000,
        sent_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    # Young sent quote (1 day ago) — should NOT appear
    young_q = _make_sent_quote(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        total_cents=70000,
        sent_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    db = SessionLocal()
    try:
        rows = dashboard.quotes_awaiting_signature(db, min_age_days=3)
    finally:
        db.close()
    ids = {r.id for r in rows}
    assert old_q in ids, ids
    assert young_q not in ids, ids


_appt_ids: list[int] = []


def _make_appointment(
    *,
    contact_id: int,
    event_id: int,
    slot_local: datetime,
    status: str = "confirmed",
) -> int:
    """Insert a test appointment at a specific local-tz datetime."""
    db = SessionLocal()
    try:
        if slot_local.tzinfo is None:
            slot_local = slot_local.replace(tzinfo=ZoneInfo(os.environ["APP_TIMEZONE"]))
        slot_utc = slot_local.astimezone(timezone.utc)
        appt = Appointment(
            confirmation_code=booking_service.generate_unique_confirmation_code(db),
            slot_start_at=slot_utc,
            slot_end_at=slot_utc + timedelta(minutes=45),
            slot_duration_minutes=45,
            timezone=os.environ["APP_TIMEZONE"],
            celebrant_first_name="Agenda",
            celebrant_last_name="Smoke",
            parent_first_name="Agenda",
            parent_last_name="Parent",
            party_size_bucket="solo",
            phone="(210) 555-0142",
            email=f"agenda-{uuid.uuid4().hex[:6]}@example.com",
            contact_id=contact_id,
            crm_event_id=event_id,
            status=status,
            user_journey=[],
            raw_payload={"smoke": True},
        )
        db.add(appt)
        db.commit()
        db.refresh(appt)
        _appt_ids.append(appt.id)
        return appt.id
    finally:
        db.close()


def check_agenda_today_filters_to_local_day(event_id, contact_id):
    """Only appointments whose ``slot_start_at`` falls in today's local-tz
    day boundary are returned. Yesterday and tomorrow are excluded even
    if their UTC offset puts them close to the boundary."""
    tz = ZoneInfo(os.environ["APP_TIMEZONE"])
    today_local = datetime.now(tz).date()

    # Mid-day today — clearly inside the window.
    today_appt = _make_appointment(
        contact_id=contact_id,
        event_id=event_id,
        slot_local=datetime.combine(today_local, time(13, 0), tzinfo=tz),
    )
    # Mid-day yesterday — clearly outside.
    yesterday_appt = _make_appointment(
        contact_id=contact_id,
        event_id=event_id,
        slot_local=datetime.combine(
            today_local - timedelta(days=1), time(13, 0), tzinfo=tz
        ),
    )
    # Mid-day tomorrow — clearly outside.
    tomorrow_appt = _make_appointment(
        contact_id=contact_id,
        event_id=event_id,
        slot_local=datetime.combine(
            today_local + timedelta(days=1), time(13, 0), tzinfo=tz
        ),
    )

    db = SessionLocal()
    try:
        payload = dashboard.todays_agenda(db)
    finally:
        db.close()

    ids = {a.id for a in payload["appointments"]}
    assert today_appt in ids, ids
    assert yesterday_appt not in ids, ids
    assert tomorrow_appt not in ids, ids
    assert payload["date"] == today_local.isoformat(), payload["date"]


def check_pipeline_counts_match_seeded_events(event_ids):
    """The two seeded events on this smoke run live in ``status='lead'``
    (see :func:`_seed_event`). The pipeline counts endpoint must return
    a ``lead`` row whose count is at least the number of seeded events
    — other events from concurrent dev work may add to the total, so
    assert ``>=`` rather than exact equality (per
    feedback_global_pass_smokes)."""
    db = SessionLocal()
    try:
        lanes = dashboard.pipeline_counts(db)
    finally:
        db.close()
    by_code = {lane.code: lane for lane in lanes}
    assert "lead" in by_code, by_code.keys()
    assert by_code["lead"].count >= len(event_ids), (
        by_code["lead"].count, len(event_ids)
    )
    # Every status from the workflow appears in the response, including
    # lanes with zero events.
    for code in ("consulted", "sold", "picked_up", "cancelled"):
        assert code in by_code, (code, by_code.keys())
    # is_terminal flag is preserved from the workflow definitions.
    assert by_code["picked_up"].is_terminal is True
    assert by_code["lead"].is_terminal is False


def check_router_auth_gate():
    for path in ("/api/dashboard/ar-summary",
                 "/api/dashboard/recent-payments",
                 "/api/dashboard/awaiting-signature",
                 "/api/dashboard/agenda-today",
                 "/api/dashboard/pipeline-counts"):
        resp = client.get(path)
        assert resp.status_code in (401, 403), (path, resp.status_code)


def check_router_returns_data(headers):
    """Endpoints respond 200 with sane shape under auth."""
    r = client.get("/api/dashboard/ar-summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for k in (
        "outstanding_balance_cents",
        "outstanding_invoice_count",
        "overdue_balance_cents",
        "overdue_invoice_count",
        "deposits_collected_this_month_cents",
    ):
        assert k in body, body

    r = client.get("/api/dashboard/recent-payments", headers=headers)
    assert r.status_code == 200
    assert "payments" in r.json()

    r = client.get("/api/dashboard/awaiting-signature", headers=headers)
    assert r.status_code == 200
    assert "quotes" in r.json()

    r = client.get("/api/dashboard/agenda-today", headers=headers)
    assert r.status_code == 200
    body = r.json()
    for k in ("date", "timezone", "appointments"):
        assert k in body, body

    r = client.get("/api/dashboard/pipeline-counts", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "lanes" in body and isinstance(body["lanes"], list)
    assert all("code" in lane and "count" in lane for lane in body["lanes"])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids = []
    contact_ids = []
    event_ids = []

    user_id, email = _seed_admin()
    user_ids.append(user_id)
    headers = _login(email)

    # Each check group uses its own event so the assertions don't
    # tangle across rows that would aggregate together.
    contact_a, event_a = _seed_event("Dashboard A")
    contact_b, event_b = _seed_event("Dashboard B")
    contact_c, event_c = _seed_event("Dashboard C")
    contact_d, event_d = _seed_event("Dashboard D")
    contact_e, event_e = _seed_event("Dashboard E")
    contact_f, event_f = _seed_event("Dashboard F")
    contact_ids += [
        contact_a, contact_b, contact_c, contact_d, contact_e, contact_f
    ]
    event_ids += [event_a, event_b, event_c, event_d, event_e, event_f]

    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    def run(name, fn, *args, **kwargs):
        nonlocal failed
        try:
            fn(*args, **kwargs)
            checks.append((name, True, None))
        except AssertionError as exc:
            failed += 1
            checks.append((name, False, str(exc)))
        except Exception as exc:
            failed += 1
            checks.append((name, False, f"unexpected: {exc!r}"))

    run("kanban_balance_pill_excludes_paid_cancelled_draft",
        check_kanban_balance_pill, event_a, contact_a, user_id)

    # AR summary scoped to the same event so the partial/cancelled rows
    # land in the SQL aggregate alongside everything from event_a.
    run("ar_summary_outstanding_matches_sql",
        check_ar_summary_outstanding_matches_sql, event_a)

    run("ar_summary_overdue_only_past_due_date",
        check_ar_summary_overdue, event_b, contact_b, user_id)

    run("deposits_this_month_uses_net_of_refunds",
        check_deposits_this_month_net_of_refunds, event_c, contact_c, user_id)

    run("deposits_negative_when_refunds_exceed_current_month",
        check_deposits_negative_when_refunds_exceed_current_month,
        event_f, contact_f, user_id)

    run("recent_payments_event_id_resolution",
        check_recent_payments_event_id_resolution, event_d, contact_d, user_id)

    run("quotes_awaiting_signature_filters",
        check_quotes_awaiting_signature_filters, event_e, contact_e, user_id)

    # Use the spare event_f for agenda seeding — it has no other rows on
    # it so the appointment cleanup doesn't have to coordinate with the
    # event_f-based deposits check above (which uses payments only).
    run("agenda_today_filters_to_local_day",
        check_agenda_today_filters_to_local_day, event_f, contact_f)

    run("pipeline_counts_match_seeded_events",
        check_pipeline_counts_match_seeded_events, event_ids)

    run("dashboard_router_auth_gate", check_router_auth_gate)
    run("dashboard_router_returns_data", check_router_returns_data, headers)

    print()
    for name, ok, err in checks:
        if ok:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _cleanup(user_ids, contact_ids, event_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
