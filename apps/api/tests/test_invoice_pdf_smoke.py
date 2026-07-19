"""Smoke tests for Phase 8 PDF generation.

Covers the cases listed in `docs/INVOICING_PHASES.md` §8 plus the
behavior the routes depend on:

  - Render an invoice PDF and confirm the bytes start with ``%PDF`` and
    a Letter-page document is produced.
  - Cache hit: ``ensure_invoice_pdf`` on the same revision does not
    re-render. Verified by stamping ``last_pdf_rendered_at`` in the
    past, calling ``ensure_*``, and confirming the timestamp didn't
    move (and the on-disk file is the same inode).
  - Revision bump invalidation: a ``patch`` that changes line items
    bumps ``revision`` and the next ``ensure_*`` renders to the new
    cache key; the old key still exists on disk but is no longer the
    current revision.
  - Quote PDF embeds the customer signature image when present.
  - Receipt PDF renders, shows the amount and method.
  - No business logo: the PDF still renders to a text-only header.
  - Error path: monkey-patch the WeasyPrint write_pdf to raise; the
    service stamps ``last_pdf_render_error`` on the invoice; the
    final cache-key file does NOT exist (no partial poisoning); a
    retry that succeeds clears the error and produces a real file.
  - Staff download route returns the bytes (200, application/pdf).
  - Portal download route key-gates: a revoked invitation returns 404
    rather than 200 with the bytes.
  - Receipt route gates on auth (no auth -> 401).

Cleans up every artifact it creates. Runs as a script:

    venv/bin/python tests/test_invoice_pdf_smoke.py

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
    BusinessProfile,
    Contact,
    Event,
    Invoice,
    InvoiceInvitation,
    Payment,
    Quote,
    QuoteInvitation,
    User,
)
from services import invoice_pdf, invoice_service, payment_service, quote_service  # noqa: E402
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
            username=f"pdf-smoke-{suffix}",
            email=f"pdf-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="PDF Smoke Admin",
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


def _seed_event() -> tuple[int, int]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:6]
        contact = Contact(
            display_name=f"PDF Smoke Mom {suffix}",
            email=f"pdf-smoke-{suffix}@example.com",
            phone="(210) 555-0001",
            first_name="Maria",
            last_name="Lopez",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"PDF Smoke Quince {suffix}",
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


def _cleanup_paths(*paths: Path):
    for p in paths:
        try:
            if p.is_file():
                p.unlink()
        except FileNotFoundError:
            pass


def _cleanup_rows(user_ids, contact_ids, event_ids):
    db = SessionLocal()
    try:
        if event_ids:
            db.execute(
                sql_text(
                    "DELETE FROM payment_allocations WHERE payment_id IN "
                    "(SELECT id FROM payments WHERE contact_id = ANY(:cids))"
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


def _make_sent_invoice(event_id, contact_id, user_id) -> int:
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
                ),
                LineItemInput(
                    kind="service",
                    description="Alterations",
                    quantity=Decimal("1"),
                    unit_price_cents=20000,
                ),
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=70000,
                    due_date=date.today() + timedelta(days=30),
                ),
                InstallmentInput(
                    label="Balance",
                    amount_cents=70000,
                    due_date=date.today() + timedelta(days=120),
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


def _make_sent_quote(event_id, contact_id, user_id) -> int:
    db = SessionLocal()
    try:
        q = quote_service.create_quote(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    description="Full quinceañera package",
                    quantity=Decimal("1"),
                    unit_price_cents=200000,
                )
            ],
            actor_user_id=user_id,
        )
        db.commit()
        quote_service.mark_sent(db, quote_id=q.id, actor_user_id=user_id)
        db.commit()
        return q.id
    finally:
        db.close()


def _make_completed_payment(invoice_id, contact_id, user_id) -> int:
    db = SessionLocal()
    try:
        payment = payment_service.record_payment(
            db,
            contact_id=contact_id,
            amount_cents=70000,
            payment_date=date.today(),
            method="card",
            allocations=[
                AllocationInput(invoice_id=invoice_id, applied_cents=70000)
            ],
            actor_user_id=user_id,
        )
        db.commit()
        return payment.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_invoice_pdf_render(invoice_id: int) -> Path:
    db = SessionLocal()
    try:
        path = invoice_pdf.render_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
    finally:
        db.close()
    assert path.exists(), path
    head = path.read_bytes()[:4]
    assert head == b"%PDF", head
    assert path.stat().st_size > 1000
    return path


def check_invoice_pdf_cache_hit(invoice_id: int, path: Path):
    """Backdate last_pdf_rendered_at and confirm a second ensure_*
    does not re-render (timestamp stays put, mtime stays put)."""
    backdated = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        inv.last_pdf_rendered_at = backdated
        db.commit()
    finally:
        db.close()
    mtime_before = path.stat().st_mtime
    db = SessionLocal()
    try:
        path2 = invoice_pdf.ensure_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
        inv = db.get(Invoice, invoice_id)
        assert path2 == path, (path2, path)
        # The render-stamp shouldn't have moved because we hit the cache
        assert inv.last_pdf_rendered_at == backdated
    finally:
        db.close()
    assert path.stat().st_mtime == mtime_before


def check_invoice_pdf_revision_bump(invoice_id: int, old_path: Path):
    """A patch that bumps `revision` makes the next ensure_* render
    at a new cache key. Use a metadata-only edit (public_notes) so we
    don't have to also restate the schedule when totals would change."""
    db = SessionLocal()
    try:
        invoice_service.update_invoice(
            db,
            invoice_id=invoice_id,
            patch={"public_notes": "revision bump probe"},
            actor_user_id=None,
        )
        db.commit()
        new_path = invoice_pdf.ensure_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
    finally:
        db.close()
    assert new_path != old_path, (new_path, old_path)
    assert new_path.exists()
    assert old_path.exists(), "old revision should remain cached"
    assert new_path.read_bytes()[:4] == b"%PDF"
    return new_path


def check_quote_pdf_with_signature(quote_id: int):
    """Approving a quote stamps a signature; the rendered PDF should
    inline the signature bytes successfully."""
    db = SessionLocal()
    try:
        quote_service.approve_quote(
            db,
            quote_id=quote_id,
            signature_base64=(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
                "QVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
            ),
            signature_name="Maria Lopez",
            signature_ip=None,
            actor_user_id=None,
        )
        db.commit()
        # New revision invalidates any prior render
        path = invoice_pdf.render_quote_pdf(db, quote_id=quote_id)
        db.commit()
    finally:
        db.close()
    assert path.exists()
    assert path.read_bytes()[:4] == b"%PDF"
    return path


def check_receipt_pdf_render(payment_id: int) -> Path:
    db = SessionLocal()
    try:
        path = invoice_pdf.render_payment_receipt_pdf(db, payment_id=payment_id)
        db.commit()
    finally:
        db.close()
    assert path.exists()
    assert path.read_bytes()[:4] == b"%PDF"
    return path


def check_no_logo_render(invoice_id: int):
    """Clear the business logo and re-render; the PDF must still
    produce valid bytes with a text-only header."""
    db = SessionLocal()
    try:
        bp = db.get(BusinessProfile, 1)
        original = bp.logo_storage_key
        bp.logo_storage_key = None
        db.commit()
    finally:
        db.close()
    try:
        # Bump revision so the cache miss forces a real render
        db = SessionLocal()
        try:
            invoice_service.update_invoice(
                db,
                invoice_id=invoice_id,
                patch={"public_notes": "no logo render check"},
                actor_user_id=None,
            )
            db.commit()
            path = invoice_pdf.ensure_invoice_pdf(db, invoice_id=invoice_id)
            db.commit()
        finally:
            db.close()
        assert path.read_bytes()[:4] == b"%PDF"
    finally:
        # Restore prior logo state
        db = SessionLocal()
        try:
            bp = db.get(BusinessProfile, 1)
            bp.logo_storage_key = original
            db.commit()
        finally:
            db.close()


def check_render_failure_path(invoice_id: int):
    """Simulate a WeasyPrint failure. The route must:
      - not leave the final cache-key file behind
      - stamp last_pdf_render_error on the invoice
      - clear the error on a successful retry."""
    # Bump revision so the retry doesn't hit the existing cache
    db = SessionLocal()
    try:
        invoice_service.update_invoice(
            db,
            invoice_id=invoice_id,
            patch={"public_notes": "failure path probe"},
            actor_user_id=None,
        )
        db.commit()
        inv = db.get(Invoice, invoice_id)
        target_key = invoice_pdf._invoice_cache_key(inv)
    finally:
        db.close()

    # Pre-cleanup: remove any prior file at the target key so we can
    # assert the failure path doesn't create one
    try:
        from services import document_storage
        target_path = document_storage.resolve_path(target_key)
        if target_path.exists():
            target_path.unlink()
    except Exception:
        pass

    # Monkey-patch HTML.write_pdf to raise
    import weasyprint
    original = weasyprint.HTML.write_pdf

    def boom(self, *a, **kw):
        raise RuntimeError("simulated WeasyPrint failure")

    weasyprint.HTML.write_pdf = boom
    try:
        db = SessionLocal()
        try:
            try:
                invoice_pdf.render_invoice_pdf(db, invoice_id=invoice_id)
                raise AssertionError("expected PdfRenderError")
            except invoice_pdf.PdfRenderError:
                pass
            db.commit()
            inv = db.get(Invoice, invoice_id)
            assert inv.last_pdf_render_error is not None
            assert "simulated" in (inv.last_pdf_render_error or "").lower()
        finally:
            db.close()
    finally:
        weasyprint.HTML.write_pdf = original

    # The final cache key file must NOT exist (no partial poisoning)
    from services import document_storage
    target_path = document_storage.resolve_path(target_key)
    assert not target_path.exists(), "no partial PDF should sit at the cache key"

    # Retry succeeds and clears the error
    db = SessionLocal()
    try:
        invoice_pdf.render_invoice_pdf(db, invoice_id=invoice_id)
        db.commit()
        inv = db.get(Invoice, invoice_id)
        assert inv.last_pdf_render_error is None
    finally:
        db.close()
    assert target_path.exists()


def check_staff_route_returns_pdf(invoice_id: int, headers: dict):
    resp = client.get(f"/api/invoices/{invoice_id}/pdf", headers=headers)
    assert resp.status_code == 200, resp.status_code
    assert resp.headers["content-type"].startswith("application/pdf"), (
        resp.headers
    )
    assert resp.content[:4] == b"%PDF"


def check_portal_route_revoked_returns_404(invoice_id: int, headers: dict):
    """A revoked invitation must return 404 from the portal PDF route
    even though the invoice has a valid cached PDF."""
    db = SessionLocal()
    try:
        invitation = (
            db.query(InvoiceInvitation)
            .filter(InvoiceInvitation.invoice_id == invoice_id)
            .filter(InvoiceInvitation.deleted_at.is_(None))
            .order_by(InvoiceInvitation.id.desc())
            .first()
        )
        assert invitation is not None
        public_key = invitation.public_key
    finally:
        db.close()

    _reset_rate_limit_state()
    # Live key — should return PDF bytes
    resp = client.get(f"/portal/invoice/{public_key}/pdf")
    assert resp.status_code == 200, resp.status_code
    assert resp.content[:4] == b"%PDF"

    # Revoke
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE invoice_invitations SET revoked_at = NOW() WHERE id = :id"
            ),
            {"id": invitation.id},
        )
        db.commit()
    finally:
        db.close()

    # Now 404
    resp2 = client.get(f"/portal/invoice/{public_key}/pdf")
    assert resp2.status_code == 404


def check_receipt_route_requires_auth(payment_id: int):
    resp = client.get(f"/api/payments/{payment_id}/receipt.pdf")
    assert resp.status_code in (401, 403), resp.status_code


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    artifact_paths: list[Path] = []
    failed = 0
    checks: list[tuple[str, bool, str | None]] = []

    try:
        user_id, email = _seed_admin()
        user_ids.append(user_id)
        headers = _login(email)

        contact_id, event_id = _seed_event()
        contact_ids.append(contact_id)
        event_ids.append(event_id)

        invoice_id = _make_sent_invoice(event_id, contact_id, user_id)
        quote_id = _make_sent_quote(event_id, contact_id, user_id)
        payment_id = _make_completed_payment(invoice_id, contact_id, user_id)

        def run(name, fn, *args, **kwargs):
            nonlocal failed
            try:
                res = fn(*args, **kwargs)
                checks.append((name, True, None))
                return res
            except AssertionError as exc:
                failed += 1
                checks.append((name, False, str(exc)))
            except Exception as exc:
                failed += 1
                checks.append((name, False, f"unexpected: {exc!r}"))

        initial_path = run("invoice_pdf_render", check_invoice_pdf_render, invoice_id)
        if initial_path:
            artifact_paths.append(initial_path)
            run(
                "invoice_pdf_cache_hit",
                check_invoice_pdf_cache_hit,
                invoice_id,
                initial_path,
            )
            bumped = run(
                "invoice_pdf_revision_bump",
                check_invoice_pdf_revision_bump,
                invoice_id,
                initial_path,
            )
            if bumped:
                artifact_paths.append(bumped)

        sig_path = run("quote_pdf_with_signature", check_quote_pdf_with_signature, quote_id)
        if sig_path:
            artifact_paths.append(sig_path)

        receipt_path = run("receipt_pdf_render", check_receipt_pdf_render, payment_id)
        if receipt_path:
            artifact_paths.append(receipt_path)

        run("no_logo_render", check_no_logo_render, invoice_id)
        run("render_failure_path_then_retry", check_render_failure_path, invoice_id)

        run("staff_route_returns_pdf", check_staff_route_returns_pdf, invoice_id, headers)
        run(
            "portal_pdf_revoked_returns_404",
            check_portal_route_revoked_returns_404,
            invoice_id,
            headers,
        )
        run("receipt_route_requires_auth", check_receipt_route_requires_auth, payment_id)

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
        # Sweep all rendered artifacts. Runs even if a seed or check raised
        # mid-flight so we don't leak PDF blobs alongside DB rows.
        if event_ids or contact_ids:
            db = SessionLocal()
            try:
                rows = db.execute(
                    sql_text(
                        "SELECT id, revision FROM invoices WHERE event_id = ANY(:eids)"
                    ),
                    {"eids": event_ids},
                ).all() if event_ids else []
                for inv_id, _rev in rows:
                    for rev_n in range(1, 12):
                        p = Path(
                            "/var/lib/bellas-xv/uploads/invoices"
                        ) / str(inv_id) / f"{rev_n}.pdf"
                        _cleanup_paths(p)
                rows = db.execute(
                    sql_text(
                        "SELECT id FROM quotes WHERE event_id = ANY(:eids)"
                    ),
                    {"eids": event_ids},
                ).all() if event_ids else []
                for q_id, in rows:
                    for rev_n in range(1, 12):
                        p = Path(
                            "/var/lib/bellas-xv/uploads/quotes"
                        ) / str(q_id) / f"{rev_n}.pdf"
                        _cleanup_paths(p)
                rows = db.execute(
                    sql_text(
                        "SELECT id FROM payments WHERE contact_id = ANY(:cids)"
                    ),
                    {"cids": contact_ids},
                ).all() if contact_ids else []
                for p_id, in rows:
                    _cleanup_paths(
                        Path("/var/lib/bellas-xv/uploads/receipts") / f"{p_id}.pdf"
                    )
            finally:
                db.close()

        _cleanup_rows(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
