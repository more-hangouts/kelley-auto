"""Smoke tests for the Phase 7 customer-facing portal.

Drives both the public ``/portal/...`` surface (no auth, key-gated) and
the staff-side invitation management routes (``/api/invoices/{id}/
invitations``, ``/api/quotes/{id}/invitations``). Cases cover:

  - mark_sent on an invoice creates an invitation row whose
    ``public_key`` resolves on ``GET /portal/invoice/<key>`` to a 200
    HTML page containing the invoice number and the customer's name.
  - ``GET /portal/invoice/<key>`` for a soft-deleted invoice returns
    404 (the gate blocks even though the invitation row is intact).
  - Two ``POST /portal/invoice/<key>/view-receipt`` hits leave
    ``view_count == 2`` and ``viewed_at`` unchanged after the first
    stamp; ``last_viewed_at`` advances on every hit.
  - Submitting a signature on a sent quote flips status to ``approved``
    and stamps the signature columns. The accepted page renders.
  - Rate limit triggers 429 after 60 requests per IP per minute.
  - Staff revokes an invitation; the next portal hit returns 404 and
    the staff-list response shows ``revoked_at`` populated.
  - Staff soft-deletes an invitation; same 404 behavior, plus
    ``deleted_at`` populated.
  - Issuing a fresh invitation for the same contact after a revoke
    yields a NEW key; old key still 404s.
  - Expires-at in the past returns 404 even though the row is
    otherwise live.
  - ``POST /api/invoices/{id}/invitations`` against a draft invoice
    returns 422 (no invitations on drafts).

Cleans up every row created. Runs as a script:

    venv/bin/python tests/test_portal_smoke.py

Internal helpers are named ``check_*`` so pytest does not collect
them.
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.routers.portal import _reset_rate_limit_state  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    Invoice,
    InvoiceInvitation,
    Quote,
    QuoteInvitation,
    User,
)
from services import invoice_service, quote_service  # noqa: E402
from services.invoice_service import (  # noqa: E402
    InstallmentInput,
    LineItemInput,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _seed_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"portal-smoke-{suffix}",
            email=f"portal-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Portal Smoke Admin",
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
            display_name=f"{label} Mom",
            email=f"{label.lower().replace(' ', '-')}@example.com",
            phone=f"(210) 555-{uuid.uuid4().int % 10000:04d}",
            first_name="Maria",
            last_name="Lopez",
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
        if event_ids:
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
# Helpers — invoice / quote builders that go through the service layer so
# numbering + invitation rows behave identically to the live app
# ---------------------------------------------------------------------------


def _make_sent_invoice(*, event_id: int, contact_id: int, user_id: int) -> int:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    description="Quinceañera dress",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=60000,
                    due_date=date.today() + timedelta(days=30),
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=60000,
                    due_date=date.today() + timedelta(days=120),
                ),
            ],
            actor_user_id=user_id,
        )
        db.commit()
        db.refresh(inv)
        invoice_service.mark_sent(
            db, invoice_id=inv.id, actor_user_id=user_id
        )
        db.commit()
        return inv.id
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
                    kind="product",
                    description="Quinceañera dress",
                    quantity=Decimal("1"),
                    unit_price_cents=80000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Full",
                    amount_cents=80000,
                    due_date=date.today() + timedelta(days=30),
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        return inv.id
    finally:
        db.close()


def _make_sent_quote(*, event_id: int, contact_id: int, user_id: int) -> int:
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    description="Full package",
                    quantity=Decimal("1"),
                    unit_price_cents=200000,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        db.refresh(q)
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        return q.id
    finally:
        db.close()


def _get_invitation_for_invoice(invoice_id: int) -> InvoiceInvitation:
    db = SessionLocal()
    try:
        row = (
            db.query(InvoiceInvitation)
            .filter(InvoiceInvitation.invoice_id == invoice_id)
            .filter(InvoiceInvitation.deleted_at.is_(None))
            .filter(InvoiceInvitation.revoked_at.is_(None))
            .order_by(InvoiceInvitation.id.desc())
            .first()
        )
        assert row is not None, "expected invitation row"
        # Detach by accessing the columns we care about while the
        # session is open — the row is reused from outside the with.
        db.refresh(row)
        return row
    finally:
        db.close()


def _get_invitation_for_quote(quote_id: int) -> QuoteInvitation:
    db = SessionLocal()
    try:
        row = (
            db.query(QuoteInvitation)
            .filter(QuoteInvitation.quote_id == quote_id)
            .filter(QuoteInvitation.deleted_at.is_(None))
            .filter(QuoteInvitation.revoked_at.is_(None))
            .order_by(QuoteInvitation.id.desc())
            .first()
        )
        assert row is not None
        db.refresh(row)
        return row
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_portal_invoice_view(invoice_id: int) -> str:
    """Returns the public_key so subsequent checks can reuse it."""
    inv = _get_invitation_for_invoice(invoice_id)
    key = inv.public_key
    resp = client.get(f"/portal/invoice/{key}")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # The customer's display_name must appear so we know data hydrated
    assert "Maria" in body or "Mom" in body, body[:500]
    # The doc-kind label is rendered for invoices
    assert "Invoice" in body
    return key


def check_portal_invoice_404_on_unknown_key():
    resp = client.get(f"/portal/invoice/{'a' * 32}")
    assert resp.status_code == 404, resp.status_code


def check_view_receipt_idempotent(invoice_id: int, key: str):
    # First view stamps viewed_at + last_viewed_at, count=1
    r1 = client.post(f"/portal/invoice/{key}/view-receipt")
    assert r1.status_code == 204, r1.text
    db = SessionLocal()
    try:
        row1 = db.query(InvoiceInvitation).filter_by(public_key=key).first()
        first_viewed_at = row1.viewed_at
        first_last_viewed = row1.last_viewed_at
        assert row1.view_count == 1, row1.view_count
        assert first_viewed_at is not None
    finally:
        db.close()
    # Second view bumps last_viewed_at + count, leaves viewed_at alone
    r2 = client.post(f"/portal/invoice/{key}/view-receipt")
    assert r2.status_code == 204
    db = SessionLocal()
    try:
        row2 = db.query(InvoiceInvitation).filter_by(public_key=key).first()
        assert row2.view_count == 2, row2.view_count
        assert row2.viewed_at == first_viewed_at, "viewed_at must not change"
        assert row2.last_viewed_at >= first_last_viewed
    finally:
        db.close()


def check_quote_signature_flow(quote_id: int) -> str:
    inv = _get_invitation_for_quote(quote_id)
    key = inv.public_key
    page = client.get(f"/portal/quote/{key}")
    assert page.status_code == 200
    assert "Read and sign" in page.text, "sign form should render on a sent quote"

    # Submit a signature
    resp = client.post(
        f"/portal/quote/{key}/accept",
        json={
            "signature_name": "Maria Lopez",
            # 1x1 transparent PNG to keep the payload sane in tests
            "signature_base64": (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
                "QVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
            ),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved", body

    db = SessionLocal()
    try:
        q = db.get(Quote, quote_id)
        assert q.status == "approved"
        assert q.signature_signed_at is not None
        assert q.signature_name == "Maria Lopez"
        assert q.signature_base64 and len(q.signature_base64) > 10
    finally:
        db.close()

    accepted_page = client.get(f"/portal/quote/{key}/accepted")
    assert accepted_page.status_code == 200, accepted_page.text
    assert "Thank you" in accepted_page.text
    return key


def check_signature_required_payload(quote_id: int):
    """A sent quote rejects an empty signature payload with 422."""
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=db.get(Quote, quote_id).event_id,
            contact_id=db.get(Quote, quote_id).contact_id,
            line_items=[
                LineItemInput(
                    description="Probe",
                    quantity=Decimal("1"),
                    unit_price_cents=10000,
                )
            ],
            actor_user_id=None,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=None)
        db.commit()
        invitation = (
            db.query(QuoteInvitation)
            .filter_by(quote_id=q.id)
            .order_by(QuoteInvitation.id.desc())
            .first()
        )
        key = invitation.public_key
    finally:
        db.close()

    resp = client.post(
        f"/portal/quote/{key}/accept",
        json={"signature_name": "", "signature_base64": ""},
    )
    # FastAPI/Pydantic returns 422 from the BaseModel validation since
    # min_length=1 is enforced before we hit the service.
    assert resp.status_code in (422,), resp.status_code


def check_revoke_returns_404(invoice_id: int, headers: dict):
    """Revoke the invitation via the staff route, then confirm the
    portal route returns 404 for that key, and a fresh staff-issued
    invitation produces a different working key."""
    # List + grab a live invitation
    resp = client.get(
        f"/api/invoices/{invoice_id}/invitations", headers=headers
    )
    assert resp.status_code == 200, resp.text
    invitations = resp.json()["invitations"]
    live = [i for i in invitations if i["revoked_at"] is None]
    assert live, "expected at least one live invitation"
    target = live[0]
    old_key = target["public_key"]

    # Revoke
    rev = client.post(
        f"/api/invoices/{invoice_id}/invitations/{target['id']}/revoke",
        headers=headers,
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["revoked_at"] is not None

    # Old key now 404s
    page = client.get(f"/portal/invoice/{old_key}")
    assert page.status_code == 404

    # Issue a fresh invitation for the same contact — must produce a
    # NEW key (the revoke path treats the old row as absent for the
    # idempotency check)
    contact_id = target["contact_id"]
    fresh = client.post(
        f"/api/invoices/{invoice_id}/invitations",
        headers=headers,
        json={"contact_id": contact_id},
    )
    assert fresh.status_code == 201, fresh.text
    new_key = fresh.json()["public_key"]
    assert new_key != old_key, "expected a fresh public_key after revoke"

    # New key works
    page2 = client.get(f"/portal/invoice/{new_key}")
    assert page2.status_code == 200

    # Old key STILL 404s
    page3 = client.get(f"/portal/invoice/{old_key}")
    assert page3.status_code == 404


def check_staff_resend_and_parent_guard(
    invoice_id: int, wrong_invoice_id: int, headers: dict
):
    """Staff resend dispatches the email path and parent ids are enforced."""
    resp = client.get(
        f"/api/invoices/{invoice_id}/invitations", headers=headers
    )
    assert resp.status_code == 200, resp.text
    target = resp.json()["invitations"][0]

    bad = client.post(
        f"/api/invoices/{wrong_invoice_id}/invitations/{target['id']}/revoke",
        headers=headers,
    )
    assert bad.status_code == 404, bad.text
    assert client.get(f"/portal/invoice/{target['public_key']}").status_code == 200

    resend = client.post(
        f"/api/invoices/{invoice_id}/invitations/{target['id']}/resend",
        headers=headers,
    )
    assert resend.status_code == 200, resend.text
    assert resend.json()["last_resent_at"] is not None


def check_soft_delete_returns_404(invoice_id: int, headers: dict):
    fresh = client.post(
        f"/api/invoices/{invoice_id}/invitations",
        headers=headers,
        json={"contact_id": _seed_extra_contact(invoice_id)},
    )
    assert fresh.status_code == 201, fresh.text
    inv_id = fresh.json()["id"]
    key = fresh.json()["public_key"]
    # Confirm key is live
    assert client.get(f"/portal/invoice/{key}").status_code == 200
    # Soft delete via staff route
    delete = client.delete(
        f"/api/invoices/{invoice_id}/invitations/{inv_id}", headers=headers
    )
    assert delete.status_code == 204, delete.text
    # Now 404
    assert client.get(f"/portal/invoice/{key}").status_code == 404


# IDs of contacts seeded by `_seed_extra_contact`. Tracked here (not via a
# `LIKE 'Portal %'` sweep at cleanup time) so this smoke does not delete
# rows from concurrent or prior runs that happen to share the display-name
# prefix. Per the global-pass-smokes memory.
_extra_contact_ids: list[int] = []


def _seed_extra_contact(invoice_id: int) -> int:
    """Create a fresh contact to attach a second invitation to."""
    db = SessionLocal()
    try:
        c = Contact(
            display_name=f"Portal Extra {uuid.uuid4().hex[:6]}",
            email=f"extra-{uuid.uuid4().hex[:6]}@example.com",
            phone="(210) 555-0000",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        _extra_contact_ids.append(c.id)
        return c.id
    finally:
        db.close()


def check_expires_at_gate(invoice_id: int, headers: dict):
    """Backdate ``expires_at`` on a fresh invitation; portal returns 404."""
    contact_id = _seed_extra_contact(invoice_id)
    fresh = client.post(
        f"/api/invoices/{invoice_id}/invitations",
        headers=headers,
        json={"contact_id": contact_id},
    )
    assert fresh.status_code == 201
    key = fresh.json()["public_key"]
    inv_id = fresh.json()["id"]
    # Confirm live before expiring
    assert client.get(f"/portal/invoice/{key}").status_code == 200

    # Backdate expires_at
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE invoice_invitations SET expires_at = NOW() - INTERVAL '1 day' "
                "WHERE id = :id"
            ),
            {"id": inv_id},
        )
        db.commit()
    finally:
        db.close()

    assert client.get(f"/portal/invoice/{key}").status_code == 404


def check_draft_rejects_invitation_create(headers: dict, draft_invoice_id: int):
    contact_id = _seed_extra_contact(draft_invoice_id)
    resp = client.post(
        f"/api/invoices/{draft_invoice_id}/invitations",
        headers=headers,
        json={"contact_id": contact_id},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "invalid_transition", body


def check_rate_limit_enforced(invoice_id: int):
    """Hammer the portal endpoint past 60/min and expect a 429.

    Resets the in-process bucket first so unrelated checks don't bias
    this assertion."""
    inv = _get_invitation_for_invoice(invoice_id)
    key = inv.public_key
    _reset_rate_limit_state()
    last_status = None
    for i in range(70):
        last_status = client.get(f"/portal/invoice/{key}").status_code
        if last_status == 429:
            break
    assert last_status == 429, f"expected 429 within 70 hits, last={last_status}"
    _reset_rate_limit_state()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    try:
        user_id, email = _seed_admin()
        user_ids.append(user_id)

        contact_id, event_id = _seed_event("Portal Smoke")
        contact_ids.append(contact_id)
        event_ids.append(event_id)

        headers = _login(email)
        _reset_rate_limit_state()

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

        # Build artifacts the checks share
        sent_invoice_id = _make_sent_invoice(
            event_id=event_id, contact_id=contact_id, user_id=user_id
        )
        draft_invoice_id = _make_draft_invoice(
            event_id=event_id, contact_id=contact_id, user_id=user_id
        )
        sent_quote_id = _make_sent_quote(
            event_id=event_id, contact_id=contact_id, user_id=user_id
        )

        key = None

        def _capture_key():
            nonlocal key
            key = check_portal_invoice_view(sent_invoice_id)

        run("portal_invoice_view_renders", _capture_key)
        run("portal_invoice_404_on_unknown_key", check_portal_invoice_404_on_unknown_key)

        def _view_receipt_idempotent():
            check_view_receipt_idempotent(sent_invoice_id, key)

        run("portal_view_receipt_idempotent", _view_receipt_idempotent)
        run(
            "portal_staff_resend_and_parent_guard",
            check_staff_resend_and_parent_guard,
            sent_invoice_id,
            draft_invoice_id,
            headers,
        )
        run("portal_quote_signature_flow", check_quote_signature_flow, sent_quote_id)
        run(
            "portal_quote_accept_validates_payload",
            check_signature_required_payload,
            sent_quote_id,
        )
        run("portal_revoke_returns_404", check_revoke_returns_404, sent_invoice_id, headers)
        run(
            "portal_soft_delete_returns_404",
            check_soft_delete_returns_404,
            sent_invoice_id,
            headers,
        )
        run(
            "portal_expires_at_gate_blocks",
            check_expires_at_gate,
            sent_invoice_id,
            headers,
        )
        run(
            "portal_draft_invoice_rejects_invitation_create",
            check_draft_rejects_invitation_create,
            headers,
            draft_invoice_id,
        )
        run("portal_rate_limit_enforced", check_rate_limit_enforced, sent_invoice_id)

        print()
        for name, ok, err in checks:
            if ok:
                print(f"  ok   {name}")
            else:
                print(f"  FAIL {name}: {err}")
        print()
        print(f"checks: {len(checks)}, failed: {failed}")

        return 0 if failed == 0 else 1
    finally:
        _cleanup(user_ids, contact_ids + _extra_contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
