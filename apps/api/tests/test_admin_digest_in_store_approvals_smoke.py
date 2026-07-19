"""Smoke for the in-store-approval digest hook (Phase 9.4d).

Closes the gap the email-tracker audit surfaced: ``quote.approved_in_store``
was emitted on the staff notification event bus with timing=``digest``
but no summarizer was reading those rows. The event row landed in
``staff_notification_events`` and never surfaced anywhere.

This smoke proves the end-to-end path:

  1. ``quote_service.approve_in_store`` writes a
     ``staff_notification_events`` row of kind ``quote.approved_in_store``
     (already covered separately by
     ``tests/test_quote_approved_in_store_notification_smoke.py``).
  2. ``staff_digest_runner._in_store_approvals_since`` reads the row
     into a ``(label, value)`` tuple for the renderer.
  3. ``run_admin_daily`` passes the tuples to
     ``render_admin_daily_digest`` and the rendered text/html include
     the quote number and signature name in the new
     "Quotes signed in-store" section.

A test-mode email transport captures the send so the rendered output
is asserted directly. Dedup ledger row in ``notification_jobs`` is
also verified.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    Contact,
    Event,
    NotificationJob,
    Quote,
    QuoteLineItem,
    User,
)
from services import (  # noqa: E402
    email_transport,
    notification_routing,
    quote_service,
    staff_digest_runner,
)

# ─── Test transport: capture sends instead of mailing ──────────────────────


_captured: list = []


class _Capture:
    def send(self, msg):
        _captured.append(msg)


def _install_capture() -> None:
    """Replace the global transport getter so send_rendered_safely's
    backing call hits the capture instead of any real backend. Matches
    the pattern in tests/test_staff_digest_runner_smoke.py."""
    capture = _Capture()
    email_transport.get_email_transport = lambda: capture  # type: ignore


# ─── Seed bookkeeping ──────────────────────────────────────────────────────


_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_quote_ids: list[int] = []


def _make_user(*, role: str, label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-smoke-digest-instore-{label}-{suffix}",
            email=f"digest-instore-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Digest In-Store Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _seed_draft_quote(*, owner_user_id: int) -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55508{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Digest In-Store Smoke {tag}",
            email=f"digest-instore-c-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["digest-instore-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Digest In-Store Smoke Quince {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="digest-instore-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        quote = Quote(
            event_id=event.id,
            contact_id=contact.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=20000,
            discount_cents=0,
            tax_cents=0,
            total_cents=20000,
            terms="net 0",
        )
        db.add(quote)
        db.flush()
        _created_quote_ids.append(quote.id)

        line = QuoteLineItem(
            quote_id=quote.id,
            sort_order=0,
            kind="product",
            description="Sample dress",
            quantity=1,
            unit_price_cents=20000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=20000,
            line_tax_cents=0,
            line_total_cents=20000,
        )
        db.add(line)
        db.commit()
        return {
            "contact_id": contact.id,
            "event_id": event.id,
            "quote_id": quote.id,
        }
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _created_user_ids:
            db.execute(
                sql_text(
                    "DELETE FROM staff_notification_events "
                    "WHERE actor_user_id = ANY(:ids)"
                ),
                {"ids": _created_user_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM notification_jobs "
                    "WHERE recipient_user_id = ANY(:ids)"
                ),
                {"ids": _created_user_ids},
            )
        if _created_quote_ids:
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id = ANY(:ids)"
                ),
                {"ids": _created_quote_ids},
            )
            db.execute(
                sql_text("DELETE FROM quotes WHERE id = ANY(:ids)"),
                {"ids": _created_quote_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
        if _created_contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _created_contact_ids},
            )
        if _created_user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _created_user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    flush_for_testing()
    _install_capture()
    _captured.clear()

    admin_id = _make_user(role="admin", label="recipient")
    sales_owner_id = _make_user(role="sales", label="owner")
    seed = _seed_draft_quote(owner_user_id=sales_owner_id)

    # ---- Trigger the in-store approval. quote_service.approve_in_store
    # writes the staff_notification_events row. ----
    db = SessionLocal()
    try:
        quote_service.approve_in_store(
            db,
            quote_id=seed["quote_id"],
            signature_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
            signature_name="Smoke Digest Customer",
            signature_ip="198.51.100.42",
            actor_user_id=admin_id,
        )
        db.commit()
        # Read the assigned quote_number back so the assertion can match
        # whatever _assign_quote_number produced.
        approved = db.get(Quote, seed["quote_id"])
        assigned_quote_number = approved.quote_number
        assert assigned_quote_number, approved
    finally:
        db.close()

    # ---- Run the admin daily digest. The summarizer reads the event
    # row, the renderer surfaces it in the new section. ----
    db = SessionLocal()
    try:
        today_local = datetime.now(timezone.utc).date()
        sent = staff_digest_runner.run_admin_daily(db, digest_date=today_local)
        assert sent >= 1, sent
    finally:
        db.close()

    # ---- Capture inspection: find the message addressed to our admin
    # and confirm the new section appears with the quote info.
    db = SessionLocal()
    try:
        admin_user = db.get(User, admin_id)
        admin_email = admin_user.email
    finally:
        db.close()

    own = [
        msg
        for msg in _captured
        if getattr(msg, "to", None) == admin_email
        or (
            isinstance(getattr(msg, "to", None), (list, tuple))
            and admin_email in msg.to
        )
    ]
    assert own, (
        f"no captured digest message for admin {admin_email}; "
        f"captured={[getattr(m, 'to', None) for m in _captured]}"
    )

    msg = own[0]
    text_body = getattr(msg, "text", "") or ""
    html_body = getattr(msg, "html", "") or ""

    assert "Quotes signed in-store" in text_body, text_body[:400]
    assert assigned_quote_number in text_body, (assigned_quote_number, text_body[:400])
    assert "Smoke Digest Customer" in text_body, text_body[:400]

    assert "Quotes signed in-store" in html_body, html_body[:400]
    assert assigned_quote_number in html_body, (assigned_quote_number, html_body[:400])
    assert "Smoke Digest Customer" in html_body, html_body[:400]

    # ---- Dedup ledger row recorded so a re-run is a no-op.
    db = SessionLocal()
    try:
        ledger = (
            db.query(NotificationJob)
            .filter(NotificationJob.kind == "digest.admin_daily")
            .filter(NotificationJob.recipient_user_id == admin_id)
            .all()
        )
        assert len(ledger) == 1, [r.id for r in ledger]
        assert ledger[0].status == "sent", ledger[0].status
    finally:
        db.close()

    # ---- Recipient resolution still names the event owner as the
    # intrinsic recipient on the wrapped quote.approved_in_store row.
    # The digest covers the boutique-wide view; the intrinsic targeting
    # stays for the future "staff daily reads event log" surface.
    db = SessionLocal()
    try:
        rows = notification_routing.recipients_for(
            db,
            db.query(
                __import__(
                    "database.models", fromlist=["StaffNotificationEvent"]
                ).StaffNotificationEvent
            )
            .filter_by(kind="quote.approved_in_store", subject_id=seed["event_id"])
            .first(),
        )
        recipient_ids = [r.user_id for r in rows]
        assert sales_owner_id in recipient_ids, recipient_ids
    finally:
        db.close()

    print("admin_digest_in_store_approvals smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
