"""Concurrency smoke for invoice number allocation.

Phase 13. Confirms the ``SELECT ... FOR UPDATE`` row lock on
``numbering_state`` actually serializes ``mark_sent`` so two staff
hitting "Send" at the same instant cannot collide on an invoice
number.

Cases:

  - **N concurrent sends yield N distinct numbers.** Ten draft
    invoices, ten threads each calling ``mark_sent`` after a barrier
    sync. Every invoice ends up sent, every number matches
    ``INV-YYYY-NNNNNN``, and every number is unique.
  - **Sends share a contiguous sequence.** The N numbers form an
    unbroken range (modulo whatever ``numbering_state`` counted at
    test start). A duplicate or skipped seq would fail this.
  - **Concurrent edit on one invoice doesn't block sending another.**
    A long-running update on invoice A and a send on invoice B
    interleave correctly — the row lock is on
    ``numbering_state``, not on the whole transaction.

Why direct service calls and not HTTP: the FastAPI ``TestClient``
single-threads through one ASGI app, which would make this test
useless for proving the row lock. Each thread opens its own
``SessionLocal`` so they hit Postgres on separate connections.

Cleans up every row created.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
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
    Contact,
    Event,
    Invoice,
    User,
)
from services import invoice_service  # noqa: E402
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)

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
            username=f"concurrent-smoke-{suffix}",
            email=f"concurrent-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Concurrent Smoke Admin",
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


def _make_draft_invoice(*, event_id: int, contact_id: int, user_id: int) -> int:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    description="Concurrency probe",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=60000,
                    due_date=_shop_today() + timedelta(days=30),
                    sort_order=0,
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=60000,
                    due_date=_shop_today() + timedelta(days=90),
                    sort_order=1,
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        return inv.id
    finally:
        db.close()


def _cleanup(user_ids, contact_ids, event_ids):
    db = SessionLocal()
    try:
        if event_ids:
            for sql in (
                "DELETE FROM activity_log WHERE event_id = ANY(:eids)",
                "DELETE FROM invoice_invitations WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = ANY(:eids))",
                "DELETE FROM invoice_installments WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = ANY(:eids))",
                "DELETE FROM invoice_line_items WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE event_id = ANY(:eids))",
                "DELETE FROM invoices WHERE event_id = ANY(:eids)",
                "DELETE FROM event_status_change_events WHERE event_id = ANY(:eids)",
                "DELETE FROM events WHERE id = ANY(:eids)",
            ):
                db.execute(sql_text(sql), {"eids": event_ids})
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
# Checks
# ---------------------------------------------------------------------------


_NUM_THREADS = 10


def check_concurrent_sends_yield_unique_numbers(*, user_id, draft_ids):
    """Ten threads, ten distinct ``invoice_numbers``."""
    barrier = threading.Barrier(_NUM_THREADS)
    errors: list[str] = []
    error_lock = threading.Lock()

    def _send(invoice_id: int) -> None:
        # Wait so all threads cross into mark_sent at roughly the same
        # instant — the row lock is the only thing serializing them.
        barrier.wait()
        db = SessionLocal()
        try:
            invoice_service.mark_sent(
                db, invoice_id=invoice_id, actor_user_id=user_id
            )
            db.commit()
        except Exception as exc:  # pragma: no cover — flake forensics
            with error_lock:
                errors.append(f"invoice {invoice_id}: {exc!r}")
            db.rollback()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=_NUM_THREADS) as pool:
        futures = [pool.submit(_send, iid) for iid in draft_ids]
        for fut in as_completed(futures):
            fut.result()  # surface unexpected exceptions

    assert not errors, f"send threads raised: {errors}"

    db = SessionLocal()
    try:
        rows = (
            db.query(Invoice.id, Invoice.invoice_number, Invoice.status)
            .filter(Invoice.id.in_(draft_ids))
            .all()
        )
    finally:
        db.close()

    assert len(rows) == _NUM_THREADS
    numbers = [r.invoice_number for r in rows]
    statuses = [r.status for r in rows]
    assert all(s == "sent" for s in statuses), f"statuses: {statuses}"
    assert all(n is not None for n in numbers), f"unassigned: {numbers}"
    assert len(set(numbers)) == _NUM_THREADS, (
        f"duplicate invoice numbers under contention: {numbers}"
    )

    # Format probe: every number is INV-YYYY-NNNNNN.
    year = datetime.now(_SHOP_TZ).year
    for n in numbers:
        assert n.startswith(f"INV-{year}-"), n
        seq_part = n.rsplit("-", 1)[1]
        assert seq_part.isdigit() and len(seq_part) == 6, n


def check_concurrent_sends_form_contiguous_run(*, draft_ids):
    """The N numbers we just allocated should sit on a contiguous run.

    Other smokes may have used numbers before us, so we don't anchor
    to seq=1 — we anchor to ``min(seq)`` for this batch. A duplicate
    or skipped allocation would break this.
    """
    db = SessionLocal()
    try:
        numbers = [
            r[0]
            for r in db.query(Invoice.invoice_number)
            .filter(Invoice.id.in_(draft_ids))
            .all()
        ]
    finally:
        db.close()
    seqs = sorted(int(n.rsplit("-", 1)[1]) for n in numbers)
    assert seqs[-1] - seqs[0] + 1 == len(seqs), (
        f"non-contiguous allocation: {seqs}"
    )


def check_send_does_not_block_unrelated_edit(
    *, user_id, edit_invoice_id, send_invoice_id
):
    """An open transaction holding a long edit on invoice A must not
    keep invoice B from sending. The ``numbering_state`` lock is
    short-lived and scoped, so B's send acquires it and finishes
    while A's transaction is still pending.
    """

    barrier = threading.Barrier(2)
    edit_done = threading.Event()
    send_done = threading.Event()
    edit_errors: list[BaseException] = []

    def _hold_edit() -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            # Touch invoice A inside an open transaction; do NOT
            # commit until the send finishes.
            db.execute(
                sql_text(
                    "UPDATE invoices SET private_notes ="
                    " COALESCE(private_notes,'') || 'edit' WHERE id = :id"
                ),
                {"id": edit_invoice_id},
            )
            # Wait for the send to complete before releasing.
            assert send_done.wait(timeout=15), "send thread did not finish"
            db.commit()
        except BaseException as exc:  # pragma: no cover — diagnostics
            edit_errors.append(exc)
            db.rollback()
        finally:
            db.close()
            edit_done.set()

    def _send() -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            # Give the edit thread a head start at acquiring its row.
            time.sleep(0.05)
            invoice_service.mark_sent(
                db, invoice_id=send_invoice_id, actor_user_id=user_id
            )
            db.commit()
        finally:
            db.close()
            send_done.set()

    edit_thread = threading.Thread(target=_hold_edit)
    send_thread = threading.Thread(target=_send)
    edit_thread.start()
    send_thread.start()

    # The send must complete in well under the edit-thread timeout.
    assert send_done.wait(timeout=10), (
        "send blocked behind unrelated edit — row lock scope regression"
    )

    edit_thread.join(timeout=20)
    send_thread.join(timeout=20)
    assert edit_done.is_set()
    assert not edit_errors, f"edit thread raised: {edit_errors}"

    db = SessionLocal()
    try:
        sent_row = db.get(Invoice, send_invoice_id)
        assert sent_row is not None
        assert sent_row.status == "sent"
        assert sent_row.invoice_number is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(name, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        print(f"  ok   {name}")
        return True
    except AssertionError as exc:
        print(f"  FAIL {name}: {exc}")
        return False
    except Exception as exc:
        print(f"  ERR  {name}: {exc!r}")
        return False


def main() -> int:
    user_id = _seed_admin()
    user_ids = [user_id]
    contact_ids: list[int] = []
    event_ids: list[int] = []
    draft_ids: list[int] = []
    extra_draft_ids: list[int] = []
    failed = 0

    try:
        # Seed N events for the concurrent-send case plus 2 extras for
        # the edit-vs-send interleave.
        for i in range(_NUM_THREADS):
            c, e = _seed_event(f"Concurrent {i}")
            contact_ids.append(c)
            event_ids.append(e)
            draft_ids.append(
                _make_draft_invoice(event_id=e, contact_id=c, user_id=user_id)
            )

        for label in ("EditA", "SendB"):
            c, e = _seed_event(label)
            contact_ids.append(c)
            event_ids.append(e)
            extra_draft_ids.append(
                _make_draft_invoice(event_id=e, contact_id=c, user_id=user_id)
            )

        # Send A first so the long edit thread has a sent row to update;
        # mark_sent only works on drafts.
        db = SessionLocal()
        try:
            invoice_service.mark_sent(
                db, invoice_id=extra_draft_ids[0], actor_user_id=user_id
            )
            db.commit()
        finally:
            db.close()

        if not run(
            "ten_concurrent_sends_yield_unique_numbers",
            check_concurrent_sends_yield_unique_numbers,
            user_id=user_id,
            draft_ids=draft_ids,
        ):
            failed += 1

        if not run(
            "concurrent_sends_form_contiguous_run",
            check_concurrent_sends_form_contiguous_run,
            draft_ids=draft_ids,
        ):
            failed += 1

        if not run(
            "send_does_not_block_unrelated_edit",
            check_send_does_not_block_unrelated_edit,
            user_id=user_id,
            edit_invoice_id=extra_draft_ids[0],
            send_invoice_id=extra_draft_ids[1],
        ):
            failed += 1

        print(f"\nchecks: 3, failed: {failed}")
        return 1 if failed else 0
    finally:
        _cleanup(
            user_ids=user_ids,
            contact_ids=contact_ids,
            event_ids=event_ids,
        )


if __name__ == "__main__":
    sys.exit(main())
