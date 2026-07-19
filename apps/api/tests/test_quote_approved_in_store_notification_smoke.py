"""Smoke for D3 of Phase 9.4: quote.approved_in_store staff event.

The in-store approval path captures signature + IP + UA and writes its
own activity_log row, but didn't route through the staff notification
event bus. This slice closes that: ``quote_service.approve_in_store``
now calls ``notification_routing.record_event(kind="quote.approved_in_store", ...)``
with digest timing, so the lead/event owner who wasn't on the floor
will see it in the daily digest.

Seeds an admin user (the in-store witness), a sales user (the lead
owner), a contact, an event owned by the sales user, and a draft quote
with one line item. Hits POST /api/quotes/{id}/approve-in-store as the
admin, then asserts:

  - The route returns 200 and the quote is now 'approved'.
  - One ``staff_notification_events`` row exists with kind=
    'quote.approved_in_store', subject_kind='event', subject_id=event,
    actor_user_id=admin, and payload carrying quote_id +
    signature_name + approved_at.
  - The recipient resolution (``recipients_for``) for that event row
    returns the sales user (intrinsic targeting via the event owner).
  - No ``notification_jobs`` rows are enqueued for this kind. Digest
    timing means fan-out is deferred to the digest worker; immediate
    enqueue would break the timing contract.
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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.redis_rate_limit import flush_for_testing  # noqa: E402
from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import (  # noqa: E402
    BusinessProfile,
    Contact,
    Event,
    Quote,
    QuoteLineItem,
    StaffNotificationEvent,
    User,
)
from services import notification_routing  # noqa: E402

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_quote_ids: list[int] = []


def _make_user(*, role: str, label: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-quote-approval-{label}-{suffix}"
        u = User(
            username=username,
            email=f"quote-approval-notif-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Quote Approval Notif Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, create_access_token(u)
    finally:
        db.close()


def _capture_gate() -> bool:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        return row.attendance_gate_enabled if row else True
    finally:
        db.close()


def _set_gate(*, enabled: bool) -> None:
    db = SessionLocal()
    try:
        row = db.get(BusinessProfile, 1)
        if row is not None:
            row.attendance_gate_enabled = enabled
            db.commit()
    finally:
        db.close()


def _seed_draft_quote(*, owner_user_id: int) -> dict:
    """Contact + event (owned by sales user) + draft quote with one line."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55506{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Quote Approval Notif Smoke {tag}",
            email=f"quote-approval-notif-c-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["quote-approval-notif-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Quote Approval Notif Smoke Quince {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=owner_user_id,
            notes="quote-approval-notif-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        quote = Quote(
            event_id=event.id,
            contact_id=contact.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=10000,
            discount_cents=0,
            tax_cents=0,
            total_cents=10000,
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
            unit_price_cents=10000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=10000,
            line_tax_cents=0,
            line_total_cents=10000,
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


def _seed_event_no_owner() -> dict:
    """Same shape as _seed_draft_quote but the event has no owner."""
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55507{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Quote Approval Notif Smoke noown {tag}",
            email=f"quote-approval-notif-no-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["quote-approval-notif-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Quote Approval Notif Smoke Quince noown {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            owner_user_id=None,
            notes="quote-approval-notif-smoke",
        )
        db.add(event)
        db.flush()
        _created_event_ids.append(event.id)

        quote = Quote(
            event_id=event.id,
            contact_id=contact.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=10000,
            discount_cents=0,
            tax_cents=0,
            total_cents=10000,
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
            unit_price_cents=10000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=10000,
            line_tax_cents=0,
            line_total_cents=10000,
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
    gate_snapshot = _capture_gate()

    try:
        admin_id, admin_token = _make_user(role="admin", label="actor")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        sales_owner_id, _ = _make_user(role="sales", label="owner")

        # Admin tokens bypass the attendance gate, but disable the gate
        # globally anyway so the route doesn't accidentally exercise that
        # path during the smoke.
        _set_gate(enabled=False)

        seed = _seed_draft_quote(owner_user_id=sales_owner_id)
        event_id = seed["event_id"]
        quote_id = seed["quote_id"]

        # ---- Approve in-store via the route ----
        resp = client.post(
            f"/api/quotes/{quote_id}/approve-in-store",
            headers=admin_headers,
            json={
                "signature_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
                "signature_name": "Smoke Customer",
                "signature_ip": "198.51.100.7",
            },
        )
        assert resp.status_code == 200, resp.text

        # ---- Quote state change ----
        db = SessionLocal()
        try:
            quote = db.get(Quote, quote_id)
            assert quote.status == "approved", quote.status
            assert quote.signature_signed_at is not None, quote
            assert quote.approved_at is not None, quote
        finally:
            db.close()

        # ---- staff_notification_events row recorded ----
        db = SessionLocal()
        try:
            rows = (
                db.query(StaffNotificationEvent)
                .filter(StaffNotificationEvent.kind == "quote.approved_in_store")
                .filter(StaffNotificationEvent.subject_kind == "event")
                .filter(StaffNotificationEvent.subject_id == event_id)
                .all()
            )
            assert len(rows) == 1, [r.id for r in rows]
            row = rows[0]
            assert row.actor_user_id == admin_id, row.actor_user_id
            assert row.payload.get("quote_id") == quote_id, row.payload
            assert row.payload.get("signature_name") == "Smoke Customer", row.payload
            assert row.payload.get("approved_at") is not None, row.payload
        finally:
            db.close()

        # ---- recipients_for returns the event owner intrinsically ----
        db = SessionLocal()
        try:
            row = (
                db.query(StaffNotificationEvent)
                .filter(StaffNotificationEvent.kind == "quote.approved_in_store")
                .filter(StaffNotificationEvent.subject_id == event_id)
                .first()
            )
            recipients = notification_routing.recipients_for(db, row)
            recipient_ids = [r.user_id for r in recipients]
            assert sales_owner_id in recipient_ids, recipient_ids
        finally:
            db.close()

        # ---- Digest timing: no notification_jobs enqueued for this kind ----
        db = SessionLocal()
        try:
            job_count = db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM notification_jobs "
                    "WHERE kind = :k AND recipient_user_id = ANY(:ids)"
                ),
                {
                    "k": "quote.approved_in_store",
                    "ids": [sales_owner_id, admin_id],
                },
            ).scalar()
            assert job_count == 0, job_count
        finally:
            db.close()

        # ---- Owner-less event: record_event still writes the log row,
        # but recipients_for returns no intrinsic recipient.
        seed2 = _seed_event_no_owner()
        event2_id = seed2["event_id"]
        quote2_id = seed2["quote_id"]

        resp = client.post(
            f"/api/quotes/{quote2_id}/approve-in-store",
            headers=admin_headers,
            json={
                "signature_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
                "signature_name": "Owner-less Customer",
                "signature_ip": "198.51.100.8",
            },
        )
        assert resp.status_code == 200, resp.text

        db = SessionLocal()
        try:
            row = (
                db.query(StaffNotificationEvent)
                .filter(StaffNotificationEvent.kind == "quote.approved_in_store")
                .filter(StaffNotificationEvent.subject_id == event2_id)
                .first()
            )
            assert row is not None, "event row not written"
            recipients = notification_routing.recipients_for(db, row)
            # No intrinsic owner; no role-default subscribers for this
            # kind. Recipient list is empty.
            assert recipients == [], [r.user_id for r in recipients]
        finally:
            db.close()

        print("quote_approved_in_store_notification smoke ok")
    finally:
        # Restore the attendance gate even if the test failed.
        _set_gate(enabled=gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
