"""Smoke for Phase 10.3b/c quote + invoice participant-tagging routes.

Both routes share the same shape:

  - ``PATCH /api/quotes/{id}/participant``
  - ``PATCH /api/invoices/{id}/participant``

Both are gated by ``require_floor_access("admin", "sales")`` — the
shared mutation surface used by every other quote/invoice write.

The shared service ``buyer_journey.attach_{quote,invoice}_to_participant``
is exercised by ``test_event_participant_fk_smoke`` and by the
appointment-tagging smoke; this one focuses on the route surface:
admin happy path, sales happy path under attendance gate disabled,
sales 403 under attendance gate enabled, plus the same 404 / 400
validation paths the appointment tagging route exercises.
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
    ActivityLog,
    BusinessProfile,
    Contact,
    Event,
    EventParticipant,
    Invoice,
    Quote,
    QuoteLineItem,
    User,
)

client = TestClient(app)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_participant_ids: list[int] = []
_created_quote_ids: list[int] = []
_created_invoice_ids: list[int] = []


def _make_user(*, role: str, label: str) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        username = f"{role}-smoke-qi-tag-{label}-{suffix}"
        u = User(
            username=username,
            email=f"phase10-smoke-{role}-qi-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 10 Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id, username, create_access_token(u)
    finally:
        db.close()


def _login_sales(sales_id: int, sales_username: str, admin_headers: dict) -> dict:
    mint = client.post(
        f"/api/admin/sales-staff/{sales_id}/pin", headers=admin_headers
    )
    assert mint.status_code == 200, mint.text
    login = client.post(
        "/api/sales/auth/pin",
        json={"identifier": sales_username, "pin": mint.json()["pin"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


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


def _seed() -> dict:
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:6].upper()
        digits = f"55511{uuid.uuid4().int % 100_000:05d}"
        contact = Contact(
            display_name=f"Phase 10 Smoke {tag}",
            email=f"phase10-smoke-qic-{tag.lower()}@example.com",
            phone=f"(210) 555-{digits[5:9]}",
            phone_e164=f"+1{digits[:10]}",
            tags=["phase10-smoke"],
        )
        db.add(contact)
        db.flush()
        _created_contact_ids.append(contact.id)

        event_a = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Phase 10 Smoke QI Quince A {tag}",
            event_date=date(2027, 9, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        event_b = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=f"Phase 10 Smoke QI Quince B {tag}",
            event_date=date(2027, 10, 20),
            quince_theme_colors=[],
            status="lead",
            status_changed_at=datetime.now(timezone.utc),
            notes="phase10-smoke",
        )
        db.add_all([event_a, event_b])
        db.flush()
        _created_event_ids.extend([event_a.id, event_b.id])

        chambelan_a = EventParticipant(
            event_id=event_a.id,
            contact_id=contact.id,
            role="chambelan",
            display_name=f"Chambelan A {tag}",
        )
        other_event_participant = EventParticipant(
            event_id=event_b.id,
            contact_id=contact.id,
            role="chambelan",
            display_name=f"Chambelan B {tag}",
        )
        db.add_all([chambelan_a, other_event_participant])
        db.flush()
        _created_participant_ids.extend(
            [chambelan_a.id, other_event_participant.id]
        )

        quote = Quote(
            event_id=event_a.id,
            contact_id=contact.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=15000,
            discount_cents=0,
            tax_cents=0,
            total_cents=15000,
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
            unit_price_cents=15000,
            discount_cents=0,
            tax_rate=0,
            line_subtotal_cents=15000,
            line_tax_cents=0,
            line_total_cents=15000,
        )
        db.add(line)

        invoice = Invoice(
            event_id=event_a.id,
            contact_id=contact.id,
            status="draft",
            issue_date=date.today(),
            subtotal_cents=15000,
            discount_cents=0,
            tax_cents=0,
            total_cents=15000,
            paid_to_date_cents=0,
            balance_cents=15000,
        )
        db.add(invoice)
        db.flush()
        _created_invoice_ids.append(invoice.id)
        db.commit()

        return {
            "event_a_id": event_a.id,
            "event_b_id": event_b.id,
            "chambelan_a_id": chambelan_a.id,
            "other_event_participant_id": other_event_participant.id,
            "quote_id": quote.id,
            "invoice_id": invoice.id,
        }
    finally:
        db.close()


def _audit_rows(event_id: int, activity_type: str, subject_id: int) -> list[ActivityLog]:
    db = SessionLocal()
    try:
        return (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == activity_type)
            .filter(ActivityLog.subject_id == subject_id)
            .order_by(ActivityLog.id.asc())
            .all()
        )
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
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
        if _created_invoice_ids:
            db.execute(
                sql_text("DELETE FROM invoices WHERE id = ANY(:ids)"),
                {"ids": _created_invoice_ids},
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
        if _created_participant_ids:
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE id = ANY(:ids)"
                ),
                {"ids": _created_participant_ids},
            )
        if _created_event_ids:
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


def _gate_detail_code(detail) -> str | None:
    if isinstance(detail, dict):
        return detail.get("code")
    return detail


def main() -> None:
    flush_for_testing()
    gate_snapshot = _capture_gate()

    try:
        admin_id, _, admin_token = _make_user(role="admin", label="actor")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        sales_id, sales_username, _ = _make_user(role="sales", label="floor")
        sales_headers = _login_sales(sales_id, sales_username, admin_headers)
        _set_gate(enabled=False)

        seed = _seed()
        event_a_id = seed["event_a_id"]
        chambelan_id = seed["chambelan_a_id"]
        quote_id = seed["quote_id"]
        invoice_id = seed["invoice_id"]

        # ===== Quote tagging =====

        # Admin happy path + audit row.
        resp = client.patch(
            f"/api/quotes/{quote_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["quote_id"] == quote_id, body
        assert body["event_participant_id"] == chambelan_id, body

        rows = _audit_rows(event_a_id, "quote.participant_attached", quote_id)
        assert len(rows) == 1, [r.id for r in rows]
        row = rows[0]
        assert row.actor_user_id == admin_id, row.actor_user_id
        assert row.subject_kind == "quote", row.subject_kind
        assert row.payload.get("from_event_participant_id") is None, row.payload
        assert row.payload.get("to_event_participant_id") == chambelan_id, row.payload

        # Sales happy path (detach) + audit row.
        resp = client.patch(
            f"/api/quotes/{quote_id}/participant",
            headers=sales_headers,
            json={"event_participant_id": None},
        )
        assert resp.status_code == 200, resp.text
        rows = _audit_rows(event_a_id, "quote.participant_attached", quote_id)
        assert len(rows) == 2, [r.id for r in rows]
        latest = rows[-1]
        assert latest.actor_user_id == sales_id, latest.actor_user_id
        assert latest.payload.get("from_event_participant_id") == chambelan_id
        assert latest.payload.get("to_event_participant_id") is None

        # Cross-event participant rejected → 400.
        resp = client.patch(
            f"/api/quotes/{quote_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": seed["other_event_participant_id"]},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "participant_event_mismatch", resp.text

        # Nonexistent quote → 404.
        resp = client.patch(
            "/api/quotes/99999999/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "quote_not_found", resp.text

        # ===== Invoice tagging =====

        # Admin happy path + audit row.
        resp = client.patch(
            f"/api/invoices/{invoice_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["invoice_id"] == invoice_id, body
        assert body["event_participant_id"] == chambelan_id, body
        rows = _audit_rows(
            event_a_id, "invoice.participant_attached", invoice_id
        )
        assert len(rows) == 1, [r.id for r in rows]
        assert rows[0].subject_kind == "invoice", rows[0].subject_kind
        assert rows[0].actor_user_id == admin_id

        # Idempotent re-PATCH on invoice writes no extra row.
        resp = client.patch(
            f"/api/invoices/{invoice_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 200, resp.text
        rows = _audit_rows(
            event_a_id, "invoice.participant_attached", invoice_id
        )
        assert len(rows) == 1, [r.id for r in rows]

        # Cross-event participant rejected → 400.
        resp = client.patch(
            f"/api/invoices/{invoice_id}/participant",
            headers=admin_headers,
            json={"event_participant_id": seed["other_event_participant_id"]},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "participant_event_mismatch", resp.text

        # Nonexistent invoice → 404.
        resp = client.patch(
            "/api/invoices/99999999/participant",
            headers=admin_headers,
            json={"event_participant_id": chambelan_id},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "invoice_not_found", resp.text

        # ===== Attendance gate (sales path on both kinds) =====

        _set_gate(enabled=True)
        for path in (
            f"/api/quotes/{quote_id}/participant",
            f"/api/invoices/{invoice_id}/participant",
        ):
            resp = client.patch(
                path,
                headers=sales_headers,
                json={"event_participant_id": chambelan_id},
            )
            assert resp.status_code == 403, resp.text
            assert _gate_detail_code(resp.json().get("detail")) == "attendance_gate", (
                path,
                resp.text,
            )
        _set_gate(enabled=False)

        print("quote_invoice_participant_tag smoke ok")
    finally:
        _set_gate(enabled=gate_snapshot)


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
