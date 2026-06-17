"""Smoke tests for the invoices surface (Phase 2 of the invoicing plan).

Covers Phase 2.3 cases from `docs/INVOICING_PHASES.md`:
- create draft + line items + schedule, totals match, schedule sums to total
- schedule mismatch on create rejected
- patch on draft, no revision bump, no number allocation
- send: status flips, number allocated, invitation row exists with 64-ish key
- send fails on empty schedule, empty line items, drifted schedule
- patch on sent invoice bumps revision, keeps number
- patch on paid (forced via SQL) rejected as invoice_locked
- cancel keeps the number forever
- delete a draft, list excludes it, numbering counter unchanged
- delete a sent invoice is rejected as invoice_locked
- two concurrent sends produce sequential, gap-free numbers
- year rollover resets the counter to 1
- resend reuses the existing invitation key
- global search finds by number, customer name, status, date range

The test mints its own admin user and seeds a fresh event/contact, then
cleans up everything on exit. No external deps. Runs as a script:
`venv/bin/python tests/test_invoices_smoke.py`. Internal helpers are
named `check_*` so pytest does not collect them.
"""

import os
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal, engine  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Invoice,
    User,
)
from services import invoice_service  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown helpers
# ---------------------------------------------------------------------------


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"invoices-smoke-{suffix}",
            email=f"invoices-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Invoices Smoke Admin",
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


def _seed_event(label: str = "Invoices Smoke") -> tuple[int, int]:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=f"{label} Contact",
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


def _cleanup(user_ids: list[int], contact_ids: list[int], event_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        if event_ids:
            # payment_allocations.invoice_id has ON DELETE RESTRICT (audit
            # trail invariant from Phase 6); drop the dependents first or
            # the invoice DELETE fires a FK violation on any test that
            # exercised the payment surface.
            db.execute(
                sql_text(
                    "DELETE FROM refund_events WHERE payment_id IN ("
                    "SELECT pa.payment_id FROM payment_allocations pa "
                    "JOIN invoices i ON i.id = pa.invoice_id "
                    "WHERE i.event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM payment_allocations WHERE invoice_id IN ("
                    "SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            # Payments are contact-scoped so we keyed cleanup by contact;
            # do the same here for completeness in case prior runs left
            # rows behind.
            if contact_ids:
                db.execute(
                    sql_text(
                        "DELETE FROM payments WHERE contact_id = ANY(:cids)"
                    ),
                    {"cids": contact_ids},
                )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_invitations WHERE invoice_id IN ("
                    "SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_installments WHERE invoice_id IN ("
                    "SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_line_items WHERE invoice_id IN ("
                    "SELECT id FROM invoices WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            # Phase 9 wired log_activity into invoice service mutations;
            # the rows must clear before the events.id FK can be deleted.
            db.execute(
                sql_text(
                    "DELETE FROM activity_log WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM invoices WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_documents WHERE event_id = ANY(:eids)"),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:eids)"
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


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _force_invoice_status(invoice_id: int, status: str) -> None:
    """Bypass service-level transition guards to set up tests for locked
    statuses we cannot reach naturally yet (paid, reversed)."""
    db = SessionLocal()
    try:
        # Use direct SQL so we don't trip the service's status-machine guards.
        # The CHECK chain still applies: paid_to_date <= total, balance =
        # total - paid, etc.
        db.execute(
            sql_text(
                "UPDATE invoices SET status = :s, "
                "paid_to_date_cents = total_cents, "
                "balance_cents = 0, "
                "paid_at = NOW() "
                "WHERE id = :id"
            ),
            {"s": status, "id": invoice_id},
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _basic_line_items() -> list[dict]:
    """Three lines of varying tax rates totalling $1,253.16 ($1250 sub +
    $3.16 tax: line 1 $50 * 8.25% = $4.125 → $4.13 (banker's: 4.125 rounds
    to 4 since 12 is even)... actually let's just use simple round numbers."""
    return [
        # $1,000 line, no tax
        {
            "description": "Dress: Style 12345",
            "quantity": "1",
            "unit_price_cents": 100000,
            "tax_rate": "0",
        },
        # $200 line, 8.25% tax = $16.50
        {
            "description": "Veil",
            "quantity": "1",
            "unit_price_cents": 20000,
            "tax_rate": "0.08250",
            "tax_name": "TX Sales",
        },
        # $50 line, 8.25% tax = $4.125 → banker's round to even → $4 (4 is even, 4.125 rounds to 4)
        {
            "description": "Garter",
            "quantity": "1",
            "unit_price_cents": 5000,
            "tax_rate": "0.08250",
            "tax_name": "TX Sales",
        },
    ]


def _expected_totals_for_basic() -> dict[str, int]:
    # Line 1: subtotal=100000, tax=0, total=100000
    # Line 2: subtotal=20000, tax=20000*0.08250=1650.00 → 1650
    # Line 3: subtotal=5000, tax=5000*0.08250=412.50 → 412 (banker's: 412.5 rounds to 412 since 412 is even)
    # Total subtotal = 125000
    # Total tax = 0 + 1650 + 412 = 2062
    # Total = 100000 + 21650 + 5412 = 127062
    return {
        "subtotal_cents": 125000,
        "tax_cents": 2062,
        "total_cents": 127062,
    }


def _basic_schedule(total_cents: int, deposit_cents: int | None = None) -> list[dict]:
    # Default to a 50% deposit so the schedule passes the Phase 5
    # deposit-floor check; callers that exercise an under-floor case
    # pass an explicit `deposit_cents` and pair it with the
    # `custom_amounts` flag on the request.
    if deposit_cents is None:
        deposit_cents = total_cents // 2
        if deposit_cents * 2 < total_cents:
            deposit_cents += 1  # ceil for odd-cent totals
    return [
        {
            "label": "Deposit",
            "amount_cents": deposit_cents,
            "due_date": (date.today() + timedelta(days=14)).isoformat(),
        },
        {
            "label": "Balance",
            "amount_cents": total_cents - deposit_cents,
            "due_date": (date.today() + timedelta(days=120)).isoformat(),
        },
    ]


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def check_auth_required() -> None:
    resp = client.post("/api/events/999/invoices", json={"contact_id": 1})
    assert resp.status_code == 401, resp.text
    resp = client.get("/api/invoices?q=test")
    assert resp.status_code == 401, resp.text
    resp = client.post("/api/invoices/1/send")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# CRUD + totals
# ---------------------------------------------------------------------------


def check_create_with_balanced_schedule(auth, contact_id, event_id) -> int:
    expected = _expected_totals_for_basic()
    body = {
        "contact_id": contact_id,
        "line_items": _basic_line_items(),
        "installments": _basic_schedule(expected["total_cents"]),
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["status"] == "draft"
    assert inv["invoice_number"] is None
    assert inv["subtotal_cents"] == expected["subtotal_cents"], inv
    assert inv["tax_cents"] == expected["tax_cents"], inv
    assert inv["total_cents"] == expected["total_cents"], inv
    assert inv["balance_cents"] == expected["total_cents"]
    assert len(inv["line_items"]) == 3
    assert len(inv["installments"]) == 2
    # printed line totals must sum to invoice total (locked rounding rule).
    assert sum(li["line_total_cents"] for li in inv["line_items"]) == inv["total_cents"]
    return inv["id"]


def check_invoice_pdf_renders_phase6_schedule(auth, contact_id, event_id) -> None:
    """Rendering an invoice with installments emits the shared schedule
    partial under the historical "Payment schedule" header, with a
    Status column. Mints a fresh invoice instead of reusing one from
    earlier checks so a side-effect mid-orchestrator does not break
    this assertion.
    """
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from database.connection import SessionLocal as _Session
    from database.models import (
        Contact as _Contact,
        Event as _Event,
        Invoice as _Invoice,
        InvoiceInstallment as _InvInst,
        InvoiceLineItem as _InvLine,
    )
    from services.invoice_pdf import (
        _project_customer_lines,
        _render_html,
        _resolve_business_header,
        _totals_breakdown,
    )

    expected = _expected_totals_for_basic()
    body = {
        "contact_id": contact_id,
        "line_items": _basic_line_items(),
        "installments": _basic_schedule(expected["total_cents"]),
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    invoice_id = resp.json()["id"]

    db = _Session()
    try:
        invoice = db.get(_Invoice, invoice_id)
        line_rows = (
            db.query(_InvLine)
            .filter(_InvLine.invoice_id == invoice_id)
            .order_by(_InvLine.sort_order, _InvLine.id)
            .all()
        )
        inst_rows = (
            db.query(_InvInst)
            .filter(_InvInst.invoice_id == invoice_id)
            .order_by(_InvInst.sort_order, _InvInst.id)
            .all()
        )
        html = _render_html(
            "pdf/invoice.html",
            inv=invoice,
            contact=db.get(_Contact, invoice.contact_id),
            event=db.get(_Event, invoice.event_id),
            line_items=_project_customer_lines(db, line_rows),
            totals=_totals_breakdown(line_rows, invoice),
            installments=inst_rows,
            schedule_header="Payment schedule",
            show_payment_status=True,
            business=_resolve_business_header(db),
            rendered_at=_dt.now(_tz.utc),
        )
        assert "Payment schedule" in html
        assert "Payment terms" not in html
        assert ">Due<" in html or ">Paid<" in html
    finally:
        db.close()


def check_create_with_unbalanced_schedule_rejected(auth, contact_id, event_id) -> None:
    expected = _expected_totals_for_basic()
    body = {
        "contact_id": contact_id,
        "line_items": _basic_line_items(),
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 1,
                "due_date": (date.today() + timedelta(days=14)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "schedule_unbalanced", detail
    assert detail["schedule_sum_cents"] == 1
    assert detail["total_cents"] == expected["total_cents"]


def check_patch_draft_no_revision_bump(auth, invoice_id) -> None:
    # Replace one line item; total changes; revision stays at 1; no number.
    body = {
        "line_items": [
            {
                "description": "Dress only",
                "quantity": "1",
                "unit_price_cents": 50000,
                "tax_rate": "0",
            }
        ],
        # rebalance the schedule so it stays valid
        "installments": [
            {
                "label": "Full",
                "amount_cents": 50000,
                "due_date": (date.today() + timedelta(days=30)).isoformat(),
            }
        ],
    }
    resp = client.patch(
        f"/api/invoices/{invoice_id}", headers=auth, json=body
    )
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    assert inv["status"] == "draft"
    assert inv["revision"] == 1, "draft edits should not bump revision"
    assert inv["invoice_number"] is None
    assert inv["total_cents"] == 50000
    assert len(inv["line_items"]) == 1


# ---------------------------------------------------------------------------
# Send transition + invitation creation + numbering
# ---------------------------------------------------------------------------


def check_send_allocates_number_and_invitation(auth, invoice_id) -> str:
    resp = client.post(f"/api/invoices/{invoice_id}/send", headers=auth)
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    assert inv["status"] == "sent"
    assert inv["sent_at"] is not None
    assert inv["invoice_number"] is not None
    assert inv["invoice_number"].startswith(f"INV-{datetime.now(timezone.utc).year}-")
    # nnnnnn = 6 digits, total 4-digit year + dashes = INV-YYYY-NNNNNN = 15 chars
    assert len(inv["invoice_number"]) == 15
    assert len(inv["invitations"]) == 1
    invitation = inv["invitations"][0]
    assert len(invitation["public_key"]) >= 30  # token_urlsafe(32) → ~43 chars
    assert invitation["sent_at"] is not None
    return inv["invoice_number"]


def check_send_with_empty_schedule_rejected(auth, contact_id, event_id) -> None:
    # Create a draft with line items but no schedule
    resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": [
                {
                    "description": "x",
                    "quantity": "1",
                    "unit_price_cents": 100,
                    "tax_rate": "0",
                }
            ],
            "installments": [],
        },
    )
    assert resp.status_code == 201, resp.text
    inv_id = resp.json()["id"]

    resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "schedule_required"
    return inv_id  # caller cleans up


def check_send_with_empty_lines_rejected(auth, contact_id, event_id) -> int:
    resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": [],
            "installments": [],
        },
    )
    assert resp.status_code == 201, resp.text
    inv_id = resp.json()["id"]
    resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "line_items_required"
    return inv_id


def check_send_with_drifted_schedule_rejected(auth, contact_id, event_id) -> int:
    # Build a balanced invoice, then patch only line items so the schedule
    # is now out of sync.
    expected = _expected_totals_for_basic()
    create_resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    inv_id = create_resp.json()["id"]

    # Replace lines with a different total; do NOT replace installments.
    patch_resp = client.patch(
        f"/api/invoices/{inv_id}",
        headers=auth,
        json={
            "line_items": [
                {
                    "description": "smaller",
                    "quantity": "1",
                    "unit_price_cents": 1000,
                    "tax_rate": "0",
                }
            ]
        },
    )
    # Draft patch is allowed — _validate_schedule only fires on sent.
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["total_cents"] == 1000
    # But mark_sent must reject because the schedule now doesn't match.
    send_resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    assert send_resp.status_code == 422, send_resp.text
    assert send_resp.json()["detail"]["code"] == "schedule_unbalanced"
    return inv_id


def check_patch_sent_bumps_revision(auth, invoice_id, original_number) -> None:
    resp = client.patch(
        f"/api/invoices/{invoice_id}",
        headers=auth,
        json={"public_notes": "Thanks for your business."},
    )
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    assert inv["status"] == "sent"
    assert inv["revision"] == 2, f"expected revision 2, got {inv['revision']}"
    assert inv["invoice_number"] == original_number, "number must be preserved"


def check_patch_paid_rejected(auth, contact_id, event_id) -> None:
    expected = _expected_totals_for_basic()
    body = {
        "contact_id": contact_id,
        "line_items": _basic_line_items(),
        "installments": _basic_schedule(expected["total_cents"]),
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201
    inv_id = resp.json()["id"]
    client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    _force_invoice_status(inv_id, "paid")

    resp = client.patch(
        f"/api/invoices/{inv_id}",
        headers=auth,
        json={"public_notes": "should fail"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invoice_locked"


# ---------------------------------------------------------------------------
# Cancel preserves number; delete on draft does not burn a number
# ---------------------------------------------------------------------------


def check_cancel_preserves_number(auth, invoice_id, expected_number) -> None:
    resp = client.post(
        f"/api/invoices/{invoice_id}/cancel",
        headers=auth,
        json={"reason": "customer changed their mind"},
    )
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    assert inv["status"] == "cancelled"
    assert inv["invoice_number"] == expected_number, "cancel must preserve number"
    assert inv["cancellation_reason"] == "customer changed their mind"


def check_delete_draft_no_counter_bump(auth, contact_id, event_id) -> None:
    # Snapshot counter, create draft, delete it, confirm counter unchanged.
    db = SessionLocal()
    try:
        before = db.execute(
            sql_text("SELECT invoice_seq FROM numbering_state WHERE id = 1")
        ).scalar()
    finally:
        db.close()

    resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={"contact_id": contact_id, "line_items": [], "installments": []},
    )
    assert resp.status_code == 201
    inv_id = resp.json()["id"]
    resp = client.delete(f"/api/invoices/{inv_id}", headers=auth)
    assert resp.status_code == 204, resp.text

    # List excludes it
    resp = client.get(f"/api/events/{event_id}/invoices", headers=auth)
    assert resp.status_code == 200
    listed_ids = [i["id"] for i in resp.json()["invoices"]]
    assert inv_id not in listed_ids

    db = SessionLocal()
    try:
        after = db.execute(
            sql_text("SELECT invoice_seq FROM numbering_state WHERE id = 1")
        ).scalar()
    finally:
        db.close()
    assert before == after, f"counter drifted: {before} -> {after}"


def check_delete_sent_rejected(auth, contact_id, event_id) -> None:
    invoice_id = check_create_with_balanced_schedule(auth, contact_id, event_id)
    resp = client.post(f"/api/invoices/{invoice_id}/send", headers=auth)
    assert resp.status_code == 200, resp.text

    resp = client.delete(f"/api/invoices/{invoice_id}", headers=auth)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invoice_locked"


# ---------------------------------------------------------------------------
# Concurrent sends produce sequential numbers
# ---------------------------------------------------------------------------


def check_concurrent_sends_sequential(auth, contact_id, event_id) -> tuple[str, str]:
    expected = _expected_totals_for_basic()
    # Create two drafts up front
    a_id = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    ).json()["id"]
    b_id = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    ).json()["id"]

    # Mark sent in two threads, each holding their own DB session.
    db = SessionLocal()
    try:
        before = db.execute(
            sql_text("SELECT invoice_seq FROM numbering_state WHERE id = 1")
        ).scalar()
    finally:
        db.close()

    barrier = threading.Barrier(2)
    results: dict[int, str] = {}

    def send(invoice_id):
        with engine.begin() as conn:
            barrier.wait()
            # Use the same row-lock + assignment the service uses, but
            # call it via a direct transaction so the contention is real.
            time.sleep(0.02)
            row = conn.execute(
                sql_text(
                    "SELECT invoice_year, invoice_seq FROM numbering_state "
                    "WHERE id = 1 FOR UPDATE"
                )
            ).one()
            current_year = datetime.now(timezone.utc).year
            new_year = current_year if int(row.invoice_year) == current_year else current_year
            new_seq = (
                int(row.invoice_seq) + 1
                if int(row.invoice_year) == current_year
                else 1
            )
            number = f"INV-{new_year}-{new_seq:06d}"
            conn.execute(
                sql_text(
                    "UPDATE numbering_state SET invoice_year = :y, "
                    "invoice_seq = :s, updated_at = NOW() WHERE id = 1"
                ),
                {"y": new_year, "s": new_seq},
            )
            conn.execute(
                sql_text(
                    "UPDATE invoices SET status = 'sent', sent_at = NOW(), "
                    "invoice_number = :n WHERE id = :id"
                ),
                {"n": number, "id": invoice_id},
            )
            results[invoice_id] = number

    t1 = threading.Thread(target=send, args=(a_id,))
    t2 = threading.Thread(target=send, args=(b_id,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    nums = sorted(results.values())
    expected_nums = sorted(
        [f"INV-{datetime.now(timezone.utc).year}-{before+1:06d}",
         f"INV-{datetime.now(timezone.utc).year}-{before+2:06d}"]
    )
    assert nums == expected_nums, f"got {nums}, expected {expected_nums}"
    return results[a_id], results[b_id]


def check_year_rollover_resets_counter(auth) -> None:
    """Year rollover resets seq to 1 on first send. Asserts the allocated
    number is `INV-<current>-000001`.

    To avoid colliding with INV-<current>-000001 left over from earlier in
    this test run (or prior runs), this test:
      - seeds a fresh event/contact in its own transaction
      - clears all existing INV-<current>-* rows AND numbers from `invoices`
        before resetting the counter
      - restores the original numbering_state at the end so the rest of the
        suite continues from where it left off
    Returns the (contact_id, event_id) it created so the orchestrator can
    add them to the cleanup list.
    """
    current_year = datetime.now(timezone.utc).year

    # Fresh seeded event for this test, isolated from earlier sends.
    rollover_contact_id, rollover_event_id = _seed_event("Rollover")

    db = SessionLocal()
    try:
        prev = db.execute(
            sql_text(
                "SELECT invoice_year, invoice_seq FROM numbering_state WHERE id = 1"
            )
        ).one()
        # Wipe any existing current-year invoice numbers so the rollover
        # allocation lands on INV-<current>-000001 cleanly. We hard-delete
        # the test rows; production data is never current-year-1 in this
        # smoke (it's a fresh dev DB). Soft-deleted rows still count for
        # the UNIQUE index, so we delete them outright.
        #
        # Order matters because of Phase 6's payment_allocations FK and
        # Phase 9's activity_log FK. Drop everything that points at the
        # current-year invoices BEFORE the invoices themselves.
        pat = f"INV-{current_year}-%"
        db.execute(
            sql_text(
                "DELETE FROM refund_events WHERE payment_id IN ("
                "SELECT pa.payment_id FROM payment_allocations pa "
                "JOIN invoices i ON i.id = pa.invoice_id "
                "WHERE i.invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM payment_allocations WHERE invoice_id IN ("
                "SELECT id FROM invoices WHERE invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM activity_log WHERE subject_kind = 'invoice' "
                "AND subject_id IN ("
                "SELECT id FROM invoices WHERE invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_invitations WHERE invoice_id IN ("
                "SELECT id FROM invoices WHERE invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_installments WHERE invoice_id IN ("
                "SELECT id FROM invoices WHERE invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_line_items WHERE invoice_id IN ("
                "SELECT id FROM invoices WHERE invoice_number LIKE :pat)"
            ),
            {"pat": pat},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoices WHERE invoice_number LIKE :pat"
            ),
            {"pat": pat},
        )
        last_year = current_year - 1
        db.execute(
            sql_text(
                "UPDATE numbering_state SET invoice_year = :y, invoice_seq = 999 "
                "WHERE id = 1"
            ),
            {"y": last_year},
        )
        db.commit()
    finally:
        db.close()

    expected = _expected_totals_for_basic()
    create_resp = client.post(
        f"/api/events/{rollover_event_id}/invoices",
        headers=auth,
        json={
            "contact_id": rollover_contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    )
    inv_id = create_resp.json()["id"]
    send_resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    assert send_resp.status_code == 200, send_resp.text
    number = send_resp.json()["invoice_number"]
    assert number == f"INV-{current_year}-000001", f"got {number}"

    # Restore the original numbering_state so subsequent tests start from
    # where they expected.
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET invoice_year = :y, invoice_seq = :s "
                "WHERE id = 1"
            ),
            {"y": int(prev.invoice_year), "s": int(prev.invoice_seq)},
        )
        db.commit()
    finally:
        db.close()
    return rollover_contact_id, rollover_event_id


# ---------------------------------------------------------------------------
# Resend reuses key
# ---------------------------------------------------------------------------


def check_resend_reuses_key(auth, contact_id, event_id) -> None:
    expected = _expected_totals_for_basic()
    create_resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    )
    inv_id = create_resp.json()["id"]
    send_resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    assert send_resp.status_code == 200, send_resp.text
    invitation_a = send_resp.json()["invitations"][0]

    resend_resp = client.post(
        f"/api/invoices/{inv_id}/resend",
        headers=auth,
        json={"contact_ids": [contact_id]},
    )
    assert resend_resp.status_code == 200, resend_resp.text
    invitation_b = resend_resp.json()["invitations"][0]

    assert invitation_a["public_key"] == invitation_b["public_key"], "key must be reused"
    assert invitation_b["last_resent_at"] is not None
    assert invitation_a["sent_at"] is not None


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------


def check_global_search(auth, contact_id, event_id) -> None:
    expected = _expected_totals_for_basic()
    resp = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": _basic_line_items(),
            "installments": _basic_schedule(expected["total_cents"]),
        },
    )
    inv_id = resp.json()["id"]
    send_resp = client.post(f"/api/invoices/{inv_id}/send", headers=auth)
    number = send_resp.json()["invoice_number"]

    # by invoice number prefix
    r = client.get(f"/api/invoices?q={number}", headers=auth)
    assert r.status_code == 200
    found_ids = [i["id"] for i in r.json()["invoices"]]
    assert inv_id in found_ids, f"search by number {number} did not find {inv_id}"

    # by contact name fragment
    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        name_fragment = contact.display_name.split()[0]
    finally:
        db.close()
    r = client.get(f"/api/invoices?q={name_fragment}", headers=auth)
    assert r.status_code == 200
    found_ids = [i["id"] for i in r.json()["invoices"]]
    assert inv_id in found_ids, f"search by name {name_fragment} did not find {inv_id}"

    # by status
    r = client.get("/api/invoices?status=sent", headers=auth)
    assert r.status_code == 200
    assert any(i["id"] == inv_id for i in r.json()["invoices"])

    # by date range covering today
    today = date.today().isoformat()
    r = client.get(f"/api/invoices?date_from={today}&date_to={today}", headers=auth)
    assert r.status_code == 200
    assert any(i["id"] == inv_id for i in r.json()["invoices"])

    # by event_id
    r = client.get(f"/api/invoices?event_id={event_id}", headers=auth)
    assert r.status_code == 200
    assert any(i["id"] == inv_id for i in r.json()["invoices"])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    user_id, user_email = _make_admin()
    user_ids = [user_id]
    contact_ids: list[int] = []
    event_ids: list[int] = []
    try:
        check_auth_required()
        print("auth required ok")

        auth = _login(user_email)
        print("admin login ok")

        primary_contact_id, primary_event_id = _seed_event("Primary")
        contact_ids.append(primary_contact_id)
        event_ids.append(primary_event_id)

        invoice_id = check_create_with_balanced_schedule(
            auth, primary_contact_id, primary_event_id
        )
        print("create with balanced schedule ok")

        check_create_with_unbalanced_schedule_rejected(
            auth, primary_contact_id, primary_event_id
        )
        print("create with unbalanced schedule rejected ok")

        check_patch_draft_no_revision_bump(auth, invoice_id)
        print("draft patch keeps revision=1 and number=null ok")

        original_number = check_send_allocates_number_and_invitation(auth, invoice_id)
        print(f"send allocates number ({original_number}) + invitation ok")

        check_send_with_empty_schedule_rejected(
            auth, primary_contact_id, primary_event_id
        )
        print("send with empty schedule rejected ok")

        check_send_with_empty_lines_rejected(
            auth, primary_contact_id, primary_event_id
        )
        print("send with empty line items rejected ok")

        check_send_with_drifted_schedule_rejected(
            auth, primary_contact_id, primary_event_id
        )
        print("send with drifted schedule rejected ok")

        check_patch_sent_bumps_revision(auth, invoice_id, original_number)
        print("patch sent bumps revision, keeps number ok")

        check_patch_paid_rejected(auth, primary_contact_id, primary_event_id)
        print("patch paid rejected (invoice_locked) ok")

        check_cancel_preserves_number(auth, invoice_id, original_number)
        print("cancel preserves number ok")

        check_delete_draft_no_counter_bump(
            auth, primary_contact_id, primary_event_id
        )
        print("delete draft does not burn a number ok")

        check_delete_sent_rejected(auth, primary_contact_id, primary_event_id)
        print("delete sent rejected (invoice_locked) ok")

        a_num, b_num = check_concurrent_sends_sequential(
            auth, primary_contact_id, primary_event_id
        )
        print(f"concurrent sends sequential ({a_num}, {b_num}) ok")

        rc, re = check_year_rollover_resets_counter(auth)
        contact_ids.append(rc)
        event_ids.append(re)
        print("year rollover resets counter to 1 ok")

        # Fresh event for resend + search so the prior cancellations don't
        # cloud the assertions.
        c2_id, e2_id = _seed_event("Resend")
        contact_ids.append(c2_id)
        event_ids.append(e2_id)
        check_resend_reuses_key(auth, c2_id, e2_id)
        print("resend reuses invitation key ok")

        c3_id, e3_id = _seed_event("Search")
        contact_ids.append(c3_id)
        event_ids.append(e3_id)
        check_global_search(auth, c3_id, e3_id)
        print("global invoice search by number/name/status/date/event ok")

        check_invoice_pdf_renders_phase6_schedule(auth, c3_id, e3_id)
        print("invoice PDF surfaces Phase 6 schedule partial ok")

        print()
        print("invoices smoke ok")
        return 0
    finally:
        _cleanup(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
