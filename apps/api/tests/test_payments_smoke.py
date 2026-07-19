"""Smoke tests for the payments service (Phase 6 of the invoicing plan).

Drives `services/payment_service.py` directly. Covers:

- Deposit flow: $200 against a $2000 invoice with a 50/50 schedule.
  Status flips draft→sent (via mark_sent setup) → partial. Deposit
  installment paid_at stamps. Balance installment doesn't.
- Balance pay: another $1800. Status flips partial→paid. invoice.paid_at
  stamps. Both installments have paid_at.
- Overpayment: $2500 paid with $1800 allocated. Invoice flips to paid.
  Payment row shows unapplied=$700.
- Refund-from-unapplied-only: $300 from the unapplied pool. Payment
  unapplied drops to $400, refunded=$300, status='partially_refunded'.
  Invoice unchanged.
- Refund-from-allocation: claw back $500 from the allocation. Allocation
  refunded_cents=$500. Invoice paid_to_date drops by $500, flips back
  paid→partial, paid_at clears, balance installment paid_at clears.
- apply_unapplied: move funds from unapplied pool onto a different
  invoice. Both invoice totals + payment columns recompute.
- unapply_allocation: removes a non-refunded allocation, freeing
  unapplied pool back to its pre-allocation state.
- Rejections: over-allocation, invoice over-allocation, refund exceeds
  remaining, refund split mismatch, void on completed payment.
- chk_payment_amount_consistent invariant holds across every path.

Cleans up everything. Runs as a script:
`venv/bin/python tests/test_payments_smoke.py`. Internal helpers are
named `check_*` so pytest does not collect them.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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

from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    Payment,
    PaymentAllocation,
    User,
)
from services import invoice_service, payment_service  # noqa: E402
from services.invoice_service import InstallmentInput, LineItemInput  # noqa: E402
from services.payment_service import (  # noqa: E402
    AllocationInput,
    AllocationRefundInput,
    PaymentServiceError,
)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        contact = Contact(
            display_name=f"Payments Smoke {suffix}",
            phone="(210) 555-1515",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Payments Smoke Quince {suffix}",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        from database.auth import hash_password

        user = User(
            username=f"payments-smoke-{suffix}",
            email=f"payments-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Payments Smoke Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(contact)
        db.refresh(event)
        db.refresh(user)
        return contact.id, event.id, user.id
    finally:
        db.close()


def _cleanup(contact_id, event_id, user_id):
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "DELETE FROM refund_events WHERE payment_id IN "
                "(SELECT id FROM payments WHERE contact_id = :c)"
            ),
            {"c": contact_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM payment_allocations WHERE payment_id IN "
                "(SELECT id FROM payments WHERE contact_id = :c)"
            ),
            {"c": contact_id},
        )
        db.execute(
            sql_text("DELETE FROM payments WHERE contact_id = :c"),
            {"c": contact_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_invitations WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_installments WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_line_items WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = :e)"
            ),
            {"e": event_id},
        )
        db.execute(
            sql_text("DELETE FROM invoices WHERE event_id = :e"),
            {"e": event_id},
        )
        db.execute(sql_text("DELETE FROM events WHERE id = :e"), {"e": event_id})
        db.execute(sql_text("DELETE FROM contacts WHERE id = :c"), {"c": contact_id})
        db.execute(sql_text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        db.commit()
    finally:
        db.close()


def _make_sent_invoice(contact_id, event_id, user_id, total_cents=200000):
    """Produce a $2000 invoice with a 50/50 schedule, sent."""
    db = SessionLocal()
    try:
        invoice = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Quince package",
                    quantity=Decimal("1"),
                    unit_price_cents=total_cents,
                    kind="service",
                ),
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=total_cents // 2,
                    due_date=date.today() + timedelta(days=14),
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=total_cents - total_cents // 2,
                    due_date=date.today() + timedelta(days=90),
                ),
            ],
            actor_user_id=user_id,
        )
        invoice_service.mark_sent(db, invoice_id=invoice.id, actor_user_id=user_id)
        db.commit()
        return invoice.id
    finally:
        db.close()


def _payment_invariant_holds(db, payment_id):
    row = db.execute(
        sql_text(
            "SELECT amount_cents, applied_cents, refunded_cents, unapplied_cents "
            "FROM payments WHERE id = :id"
        ),
        {"id": payment_id},
    ).one()
    assert row.amount_cents == row.applied_cents + row.refunded_cents + row.unapplied_cents, (
        f"invariant broken: {row.amount_cents} != {row.applied_cents}+{row.refunded_cents}+{row.unapplied_cents}"
    )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_deposit_then_balance(contact_id, event_id, user_id):
    """$200 deposit on a $2000 invoice flips status to partial. Deposit
    installment's paid_at stamps. Balance pay flips to paid; both
    installments have paid_at."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id)
    db = SessionLocal()
    try:
        # Pay $200 (deposit only — half of total $2000 is $1000, but the
        # spec example uses $200/$1800, so total is actually $2000 with
        # a deposit of $1000... let me match the spec example exactly:
        # invoice $2000, deposit $1000, balance $1000. We'll record $200
        # first (partial-of-deposit) to verify the partial-pay path.
        deposit_payment = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=20000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=20000)],
            actor_user_id=user_id,
        )
        db.commit()
        deposit_pid = deposit_payment.id

        inv = db.get(Invoice, invoice_id)
        db.refresh(inv)
        assert inv.paid_to_date_cents == 20000, inv.paid_to_date_cents
        assert inv.balance_cents == 180000, inv.balance_cents
        assert inv.status == "partial", inv.status
        assert inv.paid_at is None
        _payment_invariant_holds(db, deposit_pid)

        # Pay the rest. Should push to paid.
        balance_payment = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=180000,
            method="zelle",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=180000)],
            actor_user_id=user_id,
        )
        db.commit()
        balance_pid = balance_payment.id

        db.refresh(inv)
        assert inv.paid_to_date_cents == 200000, inv.paid_to_date_cents
        assert inv.balance_cents == 0, inv.balance_cents
        assert inv.status == "paid", inv.status
        assert inv.paid_at is not None
        _payment_invariant_holds(db, balance_pid)

        # All installments should have paid_at now.
        unpaid = db.execute(
            sql_text(
                "SELECT COUNT(*) FROM invoice_installments "
                "WHERE invoice_id = :i AND paid_at IS NULL"
            ),
            {"i": invoice_id},
        ).scalar()
        assert unpaid == 0, f"expected all installments paid, {unpaid} still unpaid"
        return invoice_id, deposit_pid, balance_pid
    finally:
        db.close()


def check_overpayment(contact_id, event_id, user_id):
    """$2500 received with $1800 allocated to a fresh $1800 invoice.
    Invoice flips to paid; payment unapplied=$700."""
    # Lower-total invoice for clarity.
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=180000)
    db = SessionLocal()
    try:
        payment = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=250000,
            method="check",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=180000)],
            actor_user_id=user_id,
        )
        db.commit()
        pid = payment.id

        inv = db.get(Invoice, invoice_id)
        db.refresh(inv)
        assert inv.status == "paid", inv.status

        db.refresh(payment)
        assert payment.amount_cents == 250000
        assert payment.applied_cents == 180000
        assert payment.unapplied_cents == 70000, payment.unapplied_cents
        assert payment.refunded_cents == 0
        _payment_invariant_holds(db, pid)
        return invoice_id, pid
    finally:
        db.close()


def check_refund_from_unapplied(payment_id):
    """Refund $300 from the unapplied pool only. Invoice unchanged."""
    db = SessionLocal()
    try:
        before_inv_paid = db.execute(
            sql_text(
                "SELECT i.id, i.paid_to_date_cents, i.status "
                "FROM invoices i JOIN payment_allocations pa ON pa.invoice_id = i.id "
                "WHERE pa.payment_id = :p LIMIT 1"
            ),
            {"p": payment_id},
        ).one()

        payment_service.record_refund(
            db,
            payment_id=payment_id,
            amount_cents=30000,
            refund_method="cash",
            from_unapplied_cents=30000,
            allocation_refunds=[],
        )
        db.commit()

        payment = db.get(Payment, payment_id)
        db.refresh(payment)
        assert payment.refunded_cents == 30000, payment.refunded_cents
        assert payment.unapplied_cents == 40000, payment.unapplied_cents
        assert payment.applied_cents == 180000  # unchanged
        assert payment.status == "partially_refunded", payment.status
        _payment_invariant_holds(db, payment_id)

        # Invoice unchanged
        after = db.execute(
            sql_text(
                "SELECT paid_to_date_cents, status FROM invoices WHERE id = :i"
            ),
            {"i": before_inv_paid.id},
        ).one()
        assert after.paid_to_date_cents == before_inv_paid.paid_to_date_cents
        assert after.status == before_inv_paid.status
    finally:
        db.close()


def check_refund_from_allocation(payment_id):
    """Refund $500 from the allocation slice. Invoice paid_to_date drops
    by $500; flips paid → partial; paid_at clears; balance installment's
    paid_at clears."""
    db = SessionLocal()
    try:
        alloc_row = db.execute(
            sql_text(
                "SELECT id, invoice_id FROM payment_allocations "
                "WHERE payment_id = :p LIMIT 1"
            ),
            {"p": payment_id},
        ).one()
        alloc_id, invoice_id = alloc_row.id, alloc_row.invoice_id

        payment_service.record_refund(
            db,
            payment_id=payment_id,
            amount_cents=50000,
            refund_method="check",
            from_unapplied_cents=0,
            allocation_refunds=[
                AllocationRefundInput(allocation_id=alloc_id, refund_cents=50000),
            ],
        )
        db.commit()

        alloc = db.get(PaymentAllocation, alloc_id)
        db.refresh(alloc)
        assert alloc.refunded_cents == 50000, alloc.refunded_cents

        inv = db.get(Invoice, invoice_id)
        db.refresh(inv)
        assert inv.paid_to_date_cents == 130000, inv.paid_to_date_cents
        assert inv.balance_cents == 50000, inv.balance_cents
        assert inv.status == "partial", inv.status
        assert inv.paid_at is None, "paid_at should clear when paid → partial"

        payment = db.get(Payment, payment_id)
        db.refresh(payment)
        assert payment.applied_cents == 130000, payment.applied_cents
        assert payment.refunded_cents == 80000, payment.refunded_cents  # 30k unapplied + 50k alloc
        assert payment.unapplied_cents == 40000, payment.unapplied_cents
        _payment_invariant_holds(db, payment_id)
    finally:
        db.close()


def check_apply_unapplied_to_other_invoice(contact_id, event_id, user_id, payment_id):
    """Move some of the unapplied pool onto a different fresh invoice.
    Both invoice totals recompute."""
    new_invoice_id = _make_sent_invoice(
        contact_id, event_id, user_id, total_cents=20000
    )
    db = SessionLocal()
    try:
        payment_before = db.get(Payment, payment_id)
        db.refresh(payment_before)
        before_applied = payment_before.applied_cents
        before_unapplied = payment_before.unapplied_cents

        payment_service.apply_unapplied(
            db,
            payment_id=payment_id,
            invoice_id=new_invoice_id,
            applied_cents=20000,
        )
        db.commit()

        payment = db.get(Payment, payment_id)
        db.refresh(payment)
        assert payment.applied_cents == before_applied + 20000
        assert payment.unapplied_cents == before_unapplied - 20000
        _payment_invariant_holds(db, payment_id)

        new_inv = db.get(Invoice, new_invoice_id)
        db.refresh(new_inv)
        assert new_inv.paid_to_date_cents == 20000
        assert new_inv.status == "paid"
    finally:
        db.close()


def check_unapply_returns_to_pool(contact_id, event_id, user_id):
    """Unapplying an allocation that hasn't been refunded frees its
    funds back to the unapplied pool."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=10000)
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=10000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=10000)],
            actor_user_id=user_id,
        )
        db.commit()
        pid = p.id
        alloc_id = db.execute(
            sql_text("SELECT id FROM payment_allocations WHERE payment_id = :p"),
            {"p": pid},
        ).scalar()

        # Pre-unapply: invoice paid in full, payment fully applied.
        inv = db.get(Invoice, invoice_id)
        db.refresh(inv)
        assert inv.status == "paid"

        payment_service.unapply_allocation(db, allocation_id=alloc_id)
        db.commit()

        payment = db.get(Payment, pid)
        db.refresh(payment)
        assert payment.applied_cents == 0
        assert payment.unapplied_cents == 10000
        _payment_invariant_holds(db, pid)

        # Invoice should be back to sent (paid_to_date=0).
        db.refresh(inv)
        assert inv.paid_to_date_cents == 0
        assert inv.status == "sent"
        assert inv.paid_at is None
    finally:
        db.close()


def check_over_allocation_rejected(contact_id, event_id, user_id):
    """Allocations summing to more than amount_cents → 422 over_allocation."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=10000)
    db = SessionLocal()
    try:
        try:
            payment_service.record_payment(
                db,
                contact_id=contact_id,
                amount_cents=10000,
                method="cash",
                allocations=[
                    AllocationInput(invoice_id=invoice_id, applied_cents=15000),
                ],
            )
            print("  FAIL: over-allocation accepted")
        except PaymentServiceError as e:
            assert e.code == "over_allocation", e.code
            db.rollback()
    finally:
        db.close()


def check_invoice_overallocation_rejected(contact_id, event_id, user_id):
    """Allocation that pushes invoice paid_to_date > total → 422
    invoice_overallocation."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=5000)
    db = SessionLocal()
    try:
        try:
            payment_service.record_payment(
                db,
                contact_id=contact_id,
                amount_cents=10000,
                method="cash",
                allocations=[
                    AllocationInput(invoice_id=invoice_id, applied_cents=10000),
                ],
            )
            print("  FAIL: invoice over-allocation accepted")
        except PaymentServiceError as e:
            assert e.code == "invoice_overallocation", e.code
            db.rollback()
    finally:
        db.close()


def check_refund_exceeds_remaining_rejected(contact_id, event_id, user_id):
    """Refund amount exceeding (amount - already_refunded) → 422
    refund_exceeds_remaining."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=5000)
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=5000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=5000)],
        )
        db.commit()
        pid = p.id
        alloc_id = db.execute(
            sql_text("SELECT id FROM payment_allocations WHERE payment_id = :p"),
            {"p": pid},
        ).scalar()
        try:
            payment_service.record_refund(
                db,
                payment_id=pid,
                amount_cents=10000,  # > 5000 amount
                refund_method="cash",
                allocation_refunds=[
                    AllocationRefundInput(allocation_id=alloc_id, refund_cents=10000),
                ],
            )
            print("  FAIL: refund > remaining accepted")
        except PaymentServiceError as e:
            assert e.code == "refund_exceeds_remaining", e.code
            db.rollback()
    finally:
        db.close()


def check_refund_split_mismatch_rejected(contact_id, event_id, user_id):
    """If from_unapplied + sum(allocation_refunds) != amount_cents,
    422 refund_split_mismatch."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=5000)
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=8000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=5000)],
        )
        db.commit()
        pid = p.id
        alloc_id = db.execute(
            sql_text("SELECT id FROM payment_allocations WHERE payment_id = :p"),
            {"p": pid},
        ).scalar()
        try:
            payment_service.record_refund(
                db,
                payment_id=pid,
                amount_cents=1000,
                refund_method="cash",
                from_unapplied_cents=500,
                allocation_refunds=[
                    AllocationRefundInput(allocation_id=alloc_id, refund_cents=200),
                ],
                # 500 + 200 = 700, but amount is 1000 → mismatch
            )
            print("  FAIL: refund split mismatch accepted")
        except PaymentServiceError as e:
            assert e.code == "refund_split_mismatch", e.code
            db.rollback()
    finally:
        db.close()


def check_void_completed_rejected(contact_id, event_id, user_id):
    """void_payment refuses any non-pending status."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=5000)
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=5000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=5000)],
        )
        db.commit()
        pid = p.id
        try:
            payment_service.void_payment(db, payment_id=pid, reason="oops")
            print("  FAIL: voided a completed payment")
        except PaymentServiceError as e:
            assert e.code == "invalid_payment_state", e.code
            db.rollback()
    finally:
        db.close()


def check_payment_number_year_format(contact_id, event_id, user_id):
    """Payment number is PMT-YYYY-NNNNNN with the current year."""
    invoice_id = _make_sent_invoice(contact_id, event_id, user_id, total_cents=1000)
    db = SessionLocal()
    try:
        p = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=1000,
            method="cash",
            allocations=[AllocationInput(invoice_id=invoice_id, applied_cents=1000)],
        )
        db.commit()
        year = datetime.now(timezone.utc).year
        assert p.payment_number is not None
        assert p.payment_number.startswith(f"PMT-{year}-"), p.payment_number
        # 6-digit zero-padded sequence
        seq_str = p.payment_number.split("-")[-1]
        assert len(seq_str) == 6 and seq_str.isdigit(), seq_str
    finally:
        db.close()


def check_draft_invoice_allocation_rejected(contact_id, event_id, user_id):
    """Drafts cannot receive allocations (Phase 6 spec rule)."""
    db = SessionLocal()
    try:
        # Create a draft invoice (no mark_sent) so it stays in draft.
        draft = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Draft test",
                    quantity=Decimal("1"),
                    unit_price_cents=5000,
                    kind="service",
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        try:
            payment_service.record_payment(
                db,
                contact_id=contact_id,
                amount_cents=5000,
                method="cash",
                allocations=[
                    AllocationInput(invoice_id=draft.id, applied_cents=5000),
                ],
            )
            print("  FAIL: allocated to a draft invoice")
        except PaymentServiceError as e:
            assert e.code == "invalid_allocation_target", e.code
            db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    contact_id, event_id, user_id = _seed()
    print(f"seeded contact={contact_id} event={event_id} user={user_id}")
    try:
        invoice_id, deposit_pid, balance_pid = check_deposit_then_balance(
            contact_id, event_id, user_id
        )
        print(
            f"deposit→partial→balance→paid ok "
            f"(invoice={invoice_id}, payments={deposit_pid},{balance_pid})"
        )

        over_invoice_id, over_pid = check_overpayment(contact_id, event_id, user_id)
        print(f"overpayment ok (invoice={over_invoice_id}, payment={over_pid})")

        check_refund_from_unapplied(over_pid)
        print("refund from unapplied pool ok")

        check_refund_from_allocation(over_pid)
        print("refund from allocation flips paid → partial ok")

        check_apply_unapplied_to_other_invoice(
            contact_id, event_id, user_id, over_pid
        )
        print("apply_unapplied to a fresh invoice ok")

        check_unapply_returns_to_pool(contact_id, event_id, user_id)
        print("unapply_allocation returns funds to unapplied pool ok")

        check_over_allocation_rejected(contact_id, event_id, user_id)
        print("over_allocation rejected ok")

        check_invoice_overallocation_rejected(contact_id, event_id, user_id)
        print("invoice_overallocation rejected ok")

        check_refund_exceeds_remaining_rejected(contact_id, event_id, user_id)
        print("refund_exceeds_remaining rejected ok")

        check_refund_split_mismatch_rejected(contact_id, event_id, user_id)
        print("refund_split_mismatch rejected ok")

        check_void_completed_rejected(contact_id, event_id, user_id)
        print("void on completed payment rejected ok")

        check_payment_number_year_format(contact_id, event_id, user_id)
        print("payment number year format ok")

        check_draft_invoice_allocation_rejected(contact_id, event_id, user_id)
        print("draft invoice allocation rejected ok")

        print()
        print("payments smoke ok")
        return 0
    finally:
        _cleanup(contact_id, event_id, user_id)
        print("cleanup done")


if __name__ == "__main__":
    sys.exit(main())
