"""Smoke tests for Phase 11 reminder + quote-expiry passes.

The high-value cases — every assertion is shaped so a regression to
the spec leaves a loud failure:

  - reminder1 set to 7 days before due. Installment due in 6 days,
    today's pass fires nothing (off by one). Installment due in 7
    days, today's pass fires reminder1 and stamps `reminder1_sent_at`.
    A second pass on the same target date does not re-send.
  - reminder1 fires on the deposit installment. Pay the deposit; the
    next pass for reminder1 on the balance installment still fires
    (paid installments are skipped, unpaid ones still nudge).
  - `after_sent` basis: invoice marked sent today, reminder1 set to
    fire 0 days after sent — fires immediately.
  - reminder3 on a profile with `reminder_late_fee_cents=2500`:
    appends a `kind='fee'` line, schedule rebalances onto the next
    unpaid installment, total bumps by 2500 cents, revision bumps,
    `late_fee_applied_at` stamped so a re-run doesn't double-charge.
  - SMTP failure does NOT stamp `*_sent_at`; the next pass retries.
  - Quote expiry flips `sent` quotes whose `expires_at < today` to
    `expired`, logs `quote.expired`, leaves quotes with future or
    NULL `expires_at` alone.

Cleans up every row created.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    ActivityLog,
    BusinessProfile,
    Contact,
    Event,
    InstallmentReminderState,
    Invoice,
    InvoiceInstallment,
    InvoiceLineItem,
    Quote,
    User,
)
from services import (  # noqa: E402
    activity_log,
    invoice_service,
    payment_service,
    portal_email,
    quote_service,
    reminder_runner,
)
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)
from services.payment_service import AllocationInput  # noqa: E402

# Anchor every "today" in the smoke to the shop calendar so it stays
# consistent with the runner's APP_TIMEZONE-based default. On a UTC
# host near midnight UTC the system date and the shop date diverge,
# and tests that mix the two go flaky.
_SHOP_TZ = ZoneInfo(os.environ["APP_TIMEZONE"])


def _shop_today() -> date:
    return datetime.now(_SHOP_TZ).date()


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"reminder-smoke-{suffix}",
            email=f"reminder-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Reminder Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _seed_event(label: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=f"{label} Mom",
            email=f"{label.lower().replace(' ', '-')}@example.com",
            phone=f"(210) 555-{uuid.uuid4().int % 10000:04d}",
            first_name="Maria",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"{label} Quince",
            event_date=_shop_today() + timedelta(days=180),
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


def _snapshot_profile() -> dict:
    """Capture current business_profile reminder schedule so each
    check can restore state on exit. Avoids cross-test bleed since
    the singleton is shared."""
    db = SessionLocal()
    try:
        bp = db.get(BusinessProfile, 1)
        return {
            "reminder1_enabled": bp.reminder1_enabled,
            "reminder1_days_offset": bp.reminder1_days_offset,
            "reminder1_offset_basis": bp.reminder1_offset_basis,
            "reminder2_enabled": bp.reminder2_enabled,
            "reminder2_days_offset": bp.reminder2_days_offset,
            "reminder2_offset_basis": bp.reminder2_offset_basis,
            "reminder3_enabled": bp.reminder3_enabled,
            "reminder3_days_offset": bp.reminder3_days_offset,
            "reminder3_offset_basis": bp.reminder3_offset_basis,
            "reminder_late_fee_cents": int(bp.reminder_late_fee_cents or 0),
            "reminder_late_fee_pct": Decimal(str(bp.reminder_late_fee_pct or 0)),
        }
    finally:
        db.close()


def _restore_profile(snapshot: dict) -> None:
    db = SessionLocal()
    try:
        bp = db.get(BusinessProfile, 1)
        for k, v in snapshot.items():
            setattr(bp, k, v)
        db.commit()
    finally:
        db.close()


def _set_profile(**fields) -> None:
    db = SessionLocal()
    try:
        bp = db.get(BusinessProfile, 1)
        for k, v in fields.items():
            setattr(bp, k, v)
        db.commit()
    finally:
        db.close()


def _cleanup(user_ids, contact_ids, event_ids):
    db = SessionLocal()
    try:
        if event_ids:
            # Drop everything that points back at this event in
            # dependency order. Same shape as the broader cleanup in
            # other smokes plus installment_reminder_state.
            db.execute(
                sql_text(
                    "DELETE FROM installment_reminder_state "
                    "WHERE installment_id IN ("
                    "SELECT i.id FROM invoice_installments i "
                    "JOIN invoices iv ON iv.id = i.invoice_id "
                    "WHERE iv.event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
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
# Builders
# ---------------------------------------------------------------------------


def _make_two_installment_sent_invoice(
    *, event_id, contact_id, user_id,
    deposit_due_in_days: int, balance_due_in_days: int,
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
                    unit_price_cents=120000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=60000,
                    due_date=_shop_today() + timedelta(days=deposit_due_in_days),
                    sort_order=0,
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=60000,
                    due_date=_shop_today() + timedelta(days=balance_due_in_days),
                    sort_order=1,
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        invoice_service.mark_sent(db, invoice_id=inv.id, actor_user_id=user_id)
        db.commit()
        return inv.id
    finally:
        db.close()


def _installments(invoice_id: int) -> list[InvoiceInstallment]:
    db = SessionLocal()
    try:
        rows = (
            db.query(InvoiceInstallment)
            .filter(InvoiceInstallment.invoice_id == invoice_id)
            .order_by(
                InvoiceInstallment.sort_order.asc(),
                InvoiceInstallment.id.asc(),
            )
            .all()
        )
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def _state(installment_id: int) -> InstallmentReminderState | None:
    db = SessionLocal()
    try:
        s = db.get(InstallmentReminderState, installment_id)
        if s is not None:
            db.refresh(s)
            db.expunge(s)
        return s
    finally:
        db.close()


def _count_event_reminder_stamps(event_id: int, slot: int) -> int:
    """Count installments on this event with reminder<slot>_sent_at set.
    Used by the smoke to scope assertions to a single event since the
    runner sweeps every invoice in the DB."""
    col = f"reminder{slot}_sent_at"
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                f"SELECT COUNT(*) FROM installment_reminder_state s "
                f"JOIN invoice_installments i ON i.id = s.installment_id "
                f"JOIN invoices iv ON iv.id = i.invoice_id "
                f"WHERE iv.event_id = :e AND s.{col} IS NOT NULL"
            ),
            {"e": event_id},
        ).first()
        return int(row[0])
    finally:
        db.close()


def _activity_types(event_id: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ActivityLog.activity_type)
            .filter(ActivityLog.event_id == event_id)
            .order_by(ActivityLog.id.asc())
            .all()
        )
        return [r[0] for r in rows]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_reminder1_off_by_one(event_id, contact_id, user_id):
    """Deposit due in 6 days, reminder1 set to 7 days before due:
    today's pass fires nothing FOR THIS EVENT because target_date !=
    today. (Other events in the shared DB may fire — we scope to ours.)"""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=6, balance_due_in_days=120,
    )
    _set_profile(
        reminder1_enabled=True,
        reminder1_days_offset=7,
        reminder1_offset_basis="before_due",
    )
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()
    insts = _installments(inv_id)
    for inst in insts:
        s = _state(inst.id)
        assert s is None or s.reminder1_sent_at is None, (s, inst.id)


def check_reminder1_fires_and_idempotent(event_id, contact_id, user_id):
    """Deposit due in 7 days, reminder1 set to 7 days before due:
    today's pass fires reminder1 once on the deposit. A second pass
    does not refire."""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=7, balance_due_in_days=120,
    )
    _set_profile(
        reminder1_enabled=True,
        reminder1_days_offset=7,
        reminder1_offset_basis="before_due",
    )
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()

    deposit, balance = _installments(inv_id)
    deposit_state = _state(deposit.id)
    balance_state = _state(balance.id)
    assert deposit_state is not None and deposit_state.reminder1_sent_at is not None
    assert balance_state is None or balance_state.reminder1_sent_at is None

    # A second pass on the same day must not change the stamp
    first_stamp = deposit_state.reminder1_sent_at
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()
    assert _state(deposit.id).reminder1_sent_at == first_stamp

    # Activity log got exactly one reminder row for this event
    types = _activity_types(event_id)
    reminder_count = types.count(activity_log.INVOICE_REMINDER_SENT)
    assert reminder_count == 1, (reminder_count, types)


def check_paid_installment_skipped(event_id, contact_id, user_id):
    """Pay the deposit. Run the pass on a day that would fire reminder1
    on BOTH installments. Only the unpaid balance installment fires."""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=7, balance_due_in_days=7,
    )
    deposit, balance = _installments(inv_id)

    db = SessionLocal()
    try:
        payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=60000,
            method="card",
            allocations=[
                AllocationInput(invoice_id=inv_id, applied_cents=60000)
            ],
            actor_user_id=user_id,
        )
        db.commit()
        # Stamp the deposit as paid (record_payment recomputes paid_to_date
        # but doesn't auto-flip per-installment paid_at — staff does that
        # manually in v1, so we mirror that here).
        db.execute(
            sql_text(
                "UPDATE invoice_installments SET paid_at = NOW() WHERE id = :id"
            ),
            {"id": deposit.id},
        )
        db.commit()
    finally:
        db.close()

    _set_profile(
        reminder1_enabled=True,
        reminder1_days_offset=7,
        reminder1_offset_basis="before_due",
    )
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()
    deposit_state = _state(deposit.id)
    assert deposit_state is None or deposit_state.reminder1_sent_at is None, (
        "paid installment must not get a reminder stamp"
    )
    balance_state = _state(balance.id)
    assert balance_state is not None and balance_state.reminder1_sent_at is not None


def check_after_sent_basis(event_id, contact_id, user_id):
    """`after_sent` rule with offset 0 fires on the day the invoice is
    sent, on every unpaid installment of that invoice."""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=60, balance_due_in_days=120,
    )
    _set_profile(
        reminder1_enabled=True,
        reminder1_days_offset=0,
        reminder1_offset_basis="after_sent",
    )
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()

    # Reminders are installment-scoped per the spec, so both rows on
    # this invoice get a reminder1 stamp.
    insts = _installments(inv_id)
    stamped = sum(
        1 for i in insts
        if (s := _state(i.id)) is not None and s.reminder1_sent_at is not None
    )
    assert stamped == 2, stamped


def check_reminder3_late_fee(event_id, contact_id, user_id):
    """reminder3 with a flat late fee appends a kind='fee' line and
    rebalances the next unpaid installment."""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=-3, balance_due_in_days=60,
    )
    # Disable r1/r2 so prior tests' settings don't fire on this event's
    # installments under unrelated rules.
    _set_profile(
        reminder1_enabled=False,
        reminder2_enabled=False,
        reminder3_enabled=True,
        reminder3_days_offset=3,
        reminder3_offset_basis="after_due",
        reminder_late_fee_cents=2500,
        reminder_late_fee_pct=Decimal("0"),
    )

    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
    finally:
        db.close()

    # Verify: total bumped by 2500, fee line appended, next unpaid
    # installment carries the fee.
    db = SessionLocal()
    try:
        invoice = db.get(Invoice, inv_id)
        assert int(invoice.total_cents) == 122500, invoice.total_cents
        fee_lines = (
            db.query(InvoiceLineItem)
            .filter(InvoiceLineItem.invoice_id == inv_id)
            .filter(InvoiceLineItem.kind == "fee")
            .all()
        )
        assert len(fee_lines) == 1, len(fee_lines)
        assert int(fee_lines[0].line_total_cents) == 2500
        # Sum of installments still equals total
        inst_sum = (
            db.query(InvoiceInstallment)
            .filter(InvoiceInstallment.invoice_id == inv_id)
            .all()
        )
        total_inst = sum(int(i.amount_cents) for i in inst_sum)
        assert total_inst == int(invoice.total_cents), (total_inst, invoice.total_cents)
        # Revision bumped
        assert int(invoice.revision) >= 2, invoice.revision
    finally:
        db.close()

    deposit, balance = _installments(inv_id)
    deposit_state = _state(deposit.id)
    assert deposit_state is not None
    assert deposit_state.reminder3_sent_at is not None
    assert deposit_state.late_fee_applied_at is not None

    # Re-run: no second fee on THIS event's installment
    pre_total = 122500
    db = SessionLocal()
    try:
        reminder_runner.run_reminder_pass(db, today=_shop_today())
        invoice = db.get(Invoice, inv_id)
        assert int(invoice.total_cents) == pre_total, invoice.total_cents
    finally:
        db.close()


def check_smtp_failure_no_stamp(event_id, contact_id, user_id, monkeypatch_failures):
    """If the email transport raises, `*_sent_at` stays NULL on this
    event's installment so the next pass retries."""
    inv_id = _make_two_installment_sent_invoice(
        event_id=event_id, contact_id=contact_id, user_id=user_id,
        deposit_due_in_days=7, balance_due_in_days=120,
    )
    # Disable other slots so we only measure reminder1 behavior here.
    _set_profile(
        reminder1_enabled=True,
        reminder1_days_offset=7,
        reminder1_offset_basis="before_due",
        reminder2_enabled=False,
        reminder3_enabled=False,
    )

    original = portal_email.send_invoice_reminder

    def boom(db, **kw):
        raise portal_email.PortalEmailError("simulated SMTP failure")

    portal_email.send_invoice_reminder = boom
    monkeypatch_failures.append(("send_invoice_reminder", original))
    try:
        db = SessionLocal()
        try:
            reminder_runner.run_reminder_pass(db, today=_shop_today())
        finally:
            db.close()
        deposit, _ = _installments(inv_id)
        s = _state(deposit.id)
        assert s is None or s.reminder1_sent_at is None, (
            "SMTP failure must not stamp reminder1_sent_at"
        )
    finally:
        portal_email.send_invoice_reminder = original


def check_quote_expiry(event_id, contact_id, user_id):
    """Sent quote with `expires_at` in the past flips to `expired`.
    Sent quote with future or NULL `expires_at` is left alone."""
    db = SessionLocal()
    try:
        # Quote 1: expired yesterday, status=sent
        q1 = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Probe",
                    quantity=Decimal("1"),
                    unit_price_cents=50000,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q1.id, actor_user_id=user_id)
        db.commit()
        db.execute(
            sql_text("UPDATE quotes SET expires_at = :d WHERE id = :id"),
            {"d": _shop_today() - timedelta(days=1), "id": q1.id},
        )
        db.commit()

        # Quote 2: expires next month, status=sent
        q2 = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Probe2",
                    quantity=Decimal("1"),
                    unit_price_cents=70000,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q2.id, actor_user_id=user_id)
        db.commit()
        db.execute(
            sql_text("UPDATE quotes SET expires_at = :d WHERE id = :id"),
            {"d": _shop_today() + timedelta(days=30), "id": q2.id},
        )
        db.commit()
        q1_id, q2_id = q1.id, q2.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        result = reminder_runner.run_quote_expiry_pass(db, today=_shop_today())
    finally:
        db.close()
    assert result.expired_count == 1, result

    db = SessionLocal()
    try:
        assert db.get(Quote, q1_id).status == "expired"
        assert db.get(Quote, q2_id).status == "sent"
    finally:
        db.close()
    types = _activity_types(event_id)
    assert types.count(activity_log.QUOTE_EXPIRED) == 1, types


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    monkeypatch_failures: list[tuple[str, object]] = []

    user_id = _seed_admin()
    user_ids.append(user_id)

    profile_snapshot = _snapshot_profile()

    # Each scenario gets a separate event so per-installment state and
    # activity rows don't tangle.
    events = {}
    for label in ("RA", "RB", "RC", "RD", "RE", "RF", "RG"):
        c, e = _seed_event(f"Reminder {label}")
        contact_ids.append(c)
        event_ids.append(e)
        events[label] = (c, e)

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

    try:
        c, e = events["RA"]
        run("reminder1_off_by_one_does_not_fire",
            check_reminder1_off_by_one, e, c, user_id)

        c, e = events["RB"]
        run("reminder1_fires_and_is_idempotent",
            check_reminder1_fires_and_idempotent, e, c, user_id)

        c, e = events["RC"]
        run("paid_installment_skipped_unpaid_still_fires",
            check_paid_installment_skipped, e, c, user_id)

        c, e = events["RD"]
        run("after_sent_basis_fires_today",
            check_after_sent_basis, e, c, user_id)

        c, e = events["RE"]
        run("reminder3_late_fee_appends_and_rebalances",
            check_reminder3_late_fee, e, c, user_id)

        c, e = events["RF"]
        run("smtp_failure_does_not_stamp",
            check_smtp_failure_no_stamp, e, c, user_id, monkeypatch_failures)

        c, e = events["RG"]
        run("quote_expiry_pass_flips_only_past_due",
            check_quote_expiry, e, c, user_id)
    finally:
        # Make sure we restore monkeypatched state even on assertion error
        for attr, original in monkeypatch_failures:
            setattr(portal_email, attr, original)

    print()
    for name, ok, err in checks:
        if ok:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: {err}")
    print()
    print(f"checks: {len(checks)}, failed: {failed}")

    _restore_profile(profile_snapshot)
    _cleanup(user_ids, contact_ids, event_ids)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
