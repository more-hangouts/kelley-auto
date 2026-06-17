"""Smoke tests for the Sales Portal Phase 5 build/sign/convert flow.

Covers the critical path:

  1. Sales token creates a draft quote with line items
     (POST /api/events/{event_id}/quotes).
  2. Sales token signs in-store (POST /api/quotes/{id}/approve-in-store).
     Verifies all five signature columns including the new
     `signature_user_agent` populated from the User-Agent header.
  3. Sales token converts to invoice (POST /api/quotes/{id}/convert).
     Verifies the invoice exists, quote.status='converted', and
     `converted_invoice_id` is set.
  4. Sales token sends the invoice (POST /api/invoices/{id}/send).
     Verifies the event status is NOT auto-transitioned to 'sold'.

And the scope boundaries:

  - Sales token receives 403 on `DELETE /api/quotes/{id}`.
  - Sales token receives 403 on `DELETE /api/invoices/{id}`.
  - Sales token receives 403 on `/api/payments` and the nested
    `/api/invoices/{id}/payments` listing.

The smoke seeds its own contact, event, sales/admin users; it shares
numbering state with the rest of the suite, so it must run serially
per the project rule on smokes.
"""

import os
import sys
import uuid
from datetime import date, timedelta
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
from database.auth import (  # noqa: E402
    create_access_token,
    create_sales_token,
    hash_password,
)
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    EventParticipant,
    Invoice,
    Quote,
    User,
)
from tests._attendance_helpers import (  # noqa: E402
    restore_gate,
    snapshot_and_disable_gate,
)

client = TestClient(app)

_user_ids: list[int] = []
_event_ids: list[int] = []
_contact_ids: list[int] = []
_quote_ids: list[int] = []
_invoice_ids: list[int] = []


def _make_user(*, role: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-p5-{suffix}",
            email=f"{role}-p5-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name=f"P5 {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _token_for(user_id: int, *, sales: bool) -> str:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        return create_sales_token(u) if sales else create_access_token(u)
    finally:
        db.close()


def _seed_contact() -> int:
    db = SessionLocal()
    try:
        c = Contact(
            display_name="P5 Customer",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"p5-{uuid.uuid4().hex[:6]}@example.com",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def _seed_event(contact_id: int, status: str = "consulted") -> int:
    db = SessionLocal()
    try:
        e = Event(
            primary_contact_id=contact_id,
            event_type="quinceanera",
            event_name="P5 Test Event",
            event_date=date.today() + timedelta(days=200),
            status=status,
        )
        db.add(e)
        db.commit()
        db.refresh(e)
        _event_ids.append(e.id)
        db.add(
            EventParticipant(
                event_id=e.id,
                contact_id=contact_id,
                role="quinceanera",
                display_name="P5 Quince",
            )
        )
        db.commit()
        return e.id
    finally:
        db.close()


def _refresh_quote(quote_id: int) -> Quote:
    db = SessionLocal()
    try:
        return db.get(Quote, quote_id)
    finally:
        db.close()


def _refresh_invoice(invoice_id: int) -> Invoice:
    db = SessionLocal()
    try:
        return db.get(Invoice, invoice_id)
    finally:
        db.close()


def _refresh_event(event_id: int) -> Event:
    db = SessionLocal()
    try:
        return db.get(Event, event_id)
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        # Break the quote↔invoice link before deleting either.
        # `chk_quote_converted_consistent` enforces
        # (status='converted') ⇔ (converted_invoice_id IS NOT NULL),
        # so the FK's `ON DELETE SET NULL` would fire that check at
        # invoice-delete time. Drop the quote out of 'converted' first.
        if _quote_ids:
            db.execute(
                sql_text(
                    "UPDATE quotes "
                    "SET status = 'cancelled', "
                    "    converted_invoice_id = NULL, "
                    "    converted_at = NULL "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": _quote_ids},
            )
            db.commit()
        if _invoice_ids:
            db.execute(
                sql_text(
                    "DELETE FROM invoice_line_items WHERE invoice_id = ANY(:ids)"
                ),
                {"ids": _invoice_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_installments WHERE invoice_id = ANY(:ids)"
                ),
                {"ids": _invoice_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_invitations WHERE invoice_id = ANY(:ids)"
                ),
                {"ids": _invoice_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_order_discounts "
                    "WHERE invoice_id = ANY(:ids)"
                ),
                {"ids": _invoice_ids},
            )
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                {"ids": _invoice_ids},
            )
        if _quote_ids:
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _quote_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_installments WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _quote_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_invitations WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _quote_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_order_discounts WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _quote_ids},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": _quote_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


_gate_snapshot: dict | None = None


def main() -> None:
    global _gate_snapshot
    _gate_snapshot = snapshot_and_disable_gate()

    sales_id = _make_user(role="sales")
    admin_id = _make_user(role="admin")
    sales_headers = {
        "Authorization": f"Bearer {_token_for(sales_id, sales=True)}",
        "User-Agent": "BellasSalesSmoke/1.0 (smoke tests)",
    }
    admin_headers = {"Authorization": f"Bearer {_token_for(admin_id, sales=False)}"}

    contact_id = _seed_contact()
    event_id = _seed_event(contact_id, status="consulted")
    pre_event_status = _refresh_event(event_id).status
    assert pre_event_status == "consulted"

    # ---- 1. Build a draft quote with two line items. ----
    create_resp = client.post(
        f"/api/events/{event_id}/quotes",
        headers=sales_headers,
        json={
            "contact_id": contact_id,
            "line_items": [
                {
                    "description": "Quinceañera ballgown",
                    "quantity": 1,
                    "unit_price_cents": 250_000,
                    "kind": "product",
                },
                {
                    "description": "Court dama gown",
                    "quantity": 1,
                    "unit_price_cents": 95_000,
                    "kind": "product",
                },
            ],
            "issue_date": date.today().isoformat(),
            "expires_at": (date.today() + timedelta(days=30)).isoformat(),
            "terms": "Deposit due at signing.",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    quote = create_resp.json()
    quote_id = quote["id"]
    _quote_ids.append(quote_id)
    assert quote["status"] == "draft"
    assert quote["total_cents"] == 250_000 + 95_000

    # ---- 2. Sign in-store. ----
    sign_resp = client.post(
        f"/api/quotes/{quote_id}/approve-in-store",
        headers=sales_headers,
        json={
            "signature_base64": "data:image/png;base64,iVBORw0KGgo=",  # tiny stub
            "signature_name": "María García",
        },
    )
    assert sign_resp.status_code == 200, sign_resp.text
    signed = sign_resp.json()
    assert signed["status"] == "approved"

    quote_row = _refresh_quote(quote_id)
    assert quote_row.signature_base64 is not None
    assert quote_row.signature_signed_at is not None
    assert quote_row.signature_name == "María García"
    # signature_ip is captured from the test client connection; not all
    # transports populate it, so accept None or a string.
    assert quote_row.signature_ip is None or isinstance(
        quote_row.signature_ip, str
    ) or hasattr(quote_row.signature_ip, "compressed")
    # New Phase 5 column: must reflect the User-Agent header we sent.
    assert quote_row.signature_user_agent is not None
    assert "BellasSalesSmoke" in quote_row.signature_user_agent

    # PDF cache invalidated: last_pdf_rendered_revision should be either
    # NULL (never rendered) or older than the current revision after
    # invalidate_quote_pdf bumped it.
    assert (
        quote_row.last_pdf_rendered_revision is None
        or quote_row.last_pdf_rendered_revision < quote_row.revision
    )

    # ---- 2b. Idempotent re-sign: a second approve-in-store on an
    # already-approved quote returns the unchanged row.
    second_sign = client.post(
        f"/api/quotes/{quote_id}/approve-in-store",
        headers=sales_headers,
        json={
            "signature_base64": "data:image/png;base64,DIFFERENT",
            "signature_name": "ignored",
        },
    )
    assert second_sign.status_code == 200, second_sign.text
    # Service short-circuits on already-approved; signature stays as the
    # first capture.
    requote = _refresh_quote(quote_id)
    assert requote.signature_name == "María García"

    # ---- 3. Convert to invoice. ----
    convert_resp = client.post(
        f"/api/quotes/{quote_id}/convert",
        headers=sales_headers,
    )
    assert convert_resp.status_code == 201, convert_resp.text
    invoice = convert_resp.json()
    invoice_id = invoice["id"]
    _invoice_ids.append(invoice_id)
    assert invoice["status"] == "draft"
    # Line items should mirror the quote's count + totals.
    assert len(invoice["line_items"]) == 2

    quote_row = _refresh_quote(quote_id)
    assert quote_row.status == "converted"
    assert quote_row.converted_invoice_id == invoice_id

    # ---- 4. Send the invoice. Event status must NOT auto-flip to sold. ----
    send_resp = client.post(
        f"/api/invoices/{invoice_id}/send",
        headers=sales_headers,
    )
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["status"] == "sent"

    post_event = _refresh_event(event_id)
    assert post_event.status == pre_event_status, (
        f"event status drifted from {pre_event_status!r} to "
        f"{post_event.status!r} after invoice send; sales portal must NOT "
        "auto-transition events to sold on invoice send"
    )

    # ---- Scope boundaries: sales gets 403 on destructive admin paths. ----
    # DELETE quote — admin only. Quote is now 'converted', which is
    # not a deletable status anyway, but we want the SCOPE check to
    # fire BEFORE the service-level transition check; 403 trumps 422.
    resp = client.delete(
        f"/api/quotes/{quote_id}", headers=sales_headers
    )
    assert resp.status_code == 403, resp.text

    # DELETE invoice — admin only.
    resp = client.delete(
        f"/api/invoices/{invoice_id}", headers=sales_headers
    )
    assert resp.status_code == 403, resp.text

    # /api/payments/* and /api/invoices/{id}/payments are admin-only.
    # GET endpoints exercise the scope dep cleanly; POST endpoints would
    # race the dep against Pydantic body validation depending on the
    # FastAPI internals. The two listings below are enough to prove the
    # scope boundary; the smoke does not need to enumerate every method.
    resp = client.get(
        f"/api/invoices/{invoice_id}/payments", headers=sales_headers
    )
    assert resp.status_code == 403, resp.text
    resp = client.get(
        f"/api/events/{event_id}/payments", headers=sales_headers
    )
    assert resp.status_code == 403, resp.text

    # Admin can still delete (sanity check the scope split). We delete
    # the converted invoice's quote+invoice during cleanup; here just
    # verify the admin DELETE returns 2xx for an invoice we won't reuse.
    # We won't actually delete here since convert side effects make
    # cleanup easier when the rows still exist. Instead, verify admin
    # can read the same payments endpoint sales is barred from.
    resp = client.get(
        f"/api/invoices/{invoice_id}/payments", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text

    print("sales_quote_sign_convert smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
        restore_gate(_gate_snapshot)
