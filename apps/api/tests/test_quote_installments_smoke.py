"""Smoke tests for Phase 4 (quote installments).

Covers the cases enumerated in
`docs/INVOICE_DISCOUNTS_AND_TERMS_PHASES.md` under Phase 4:

- Round-trip a quote with three installments via POST + GET.
- Replace the schedule via PATCH (full-list semantics).
- Convert that quote to an invoice and verify all rows copied verbatim
  (label, amount, due date, sort order).
- Convert a quote with no installments and verify the legacy 50/50
  default still appears.
- A quote installment with a NULL label converts into an invoice row
  with a sane default label (`Installment N`), since
  `invoice_installments.label` is NOT NULL.

Mints its own admin user and seeds a fresh event/contact, snapshots and
restores the BusinessProfile so the test is isolated. Runs as a script:
`venv/bin/python tests/test_quote_installments_smoke.py`.
Internal helpers are named `check_*` so pytest does not collect them.
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
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event, User  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"qinst-smoke-{suffix}",
            email=f"qinst-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Phase 4 Smoke Admin",
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


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(user_ids, contact_ids, event_ids) -> None:
    db = SessionLocal()
    try:
        if event_ids:
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
            db.execute(
                sql_text(
                    "DELETE FROM quote_invitations WHERE quote_id IN ("
                    "SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_installments WHERE quote_id IN ("
                    "SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM quote_line_items WHERE quote_id IN ("
                    "SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            # Quotes first, before invoices, so the converted-quote
            # consistency CHECK does not fail mid-cleanup.
            db.execute(
                sql_text(
                    "DELETE FROM quotes WHERE event_id = ANY(:eids)"
                ),
                {"eids": event_ids},
            )
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _line(price_cents: int) -> dict:
    return {
        "description": f"Line ${price_cents / 100:.2f}",
        "quantity": "1",
        "unit_price_cents": price_cents,
        "discount_cents": 0,
        "tax_rate": "0",
    }


def _send_and_approve(auth, quote_id: int, contact_id: int) -> None:
    resp = client.post(
        f"/api/quotes/{quote_id}/send",
        headers=auth,
        json={"contact_ids": [contact_id]},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/quotes/{quote_id}/approve",
        headers=auth,
        json={
            "signature_base64": "data:image/png;base64,iVBORw0KGgo=",
            "signature_name": "Test Customer",
        },
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_round_trip_three_installments(auth, contact_id, event_id) -> int:
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(900000)],
        # Phase 5 added a deposit floor at 50% of total; a 3-way even
        # split (300k/300k/300k) would now reject. The test's intent is
        # the round-trip shape, not the floor, so opt into custom amounts
        # to preserve the original even split.
        "custom_amounts": True,
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=14)).isoformat(),
            },
            {
                "label": "Mid-stage",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=60)).isoformat(),
            },
            {
                "label": "Balance",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=120)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote = resp.json()
    assert quote["installments"]
    assert len(quote["installments"]) == 3, quote["installments"]
    assert [i["label"] for i in quote["installments"]] == [
        "Deposit", "Mid-stage", "Balance",
    ]
    assert sum(i["amount_cents"] for i in quote["installments"]) == 900000

    # GET round-trip returns the same shape.
    quote_id = quote["id"]
    resp = client.get(f"/api/quotes/{quote_id}", headers=auth)
    assert resp.status_code == 200
    assert len(resp.json()["installments"]) == 3
    return quote_id


def check_patch_replaces_schedule(auth, quote_id) -> None:
    today = date.today()
    resp = client.patch(
        f"/api/quotes/{quote_id}",
        headers=auth,
        json={
            "installments": [
                {
                    "label": "Single",
                    "amount_cents": 900000,
                    "due_date": (today + timedelta(days=30)).isoformat(),
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    quote = resp.json()
    assert len(quote["installments"]) == 1, quote["installments"]
    assert quote["installments"][0]["label"] == "Single"
    assert quote["installments"][0]["amount_cents"] == 900000


def check_conversion_carries_three_installments(
    auth, contact_id, event_id
) -> None:
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(900000)],
        # Even 3-way split — opt out of the Phase 5 deposit floor so
        # the original conversion-fidelity check stays intact.
        "custom_amounts": True,
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=14)).isoformat(),
            },
            {
                "label": "Mid-stage",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=60)).isoformat(),
            },
            {
                "label": "Balance",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=120)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote_id = resp.json()["id"]
    _send_and_approve(auth, quote_id, contact_id)

    resp = client.post(f"/api/quotes/{quote_id}/convert", headers=auth)
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["installments"]) == 3, invoice["installments"]
    insts = sorted(invoice["installments"], key=lambda i: i["sort_order"])
    assert [i["label"] for i in insts] == ["Deposit", "Mid-stage", "Balance"]
    assert [i["amount_cents"] for i in insts] == [300000, 300000, 300000]
    assert insts[-1]["due_date"] == (today + timedelta(days=120)).isoformat()


def check_conversion_falls_through_to_default(
    auth, contact_id, event_id
) -> None:
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        # No `installments` key — converted invoice should mint the
        # legacy 50/50 default.
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote_id = resp.json()["id"]
    _send_and_approve(auth, quote_id, contact_id)

    resp = client.post(f"/api/quotes/{quote_id}/convert", headers=auth)
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["installments"]) == 2
    labels = sorted(i["label"] for i in invoice["installments"])
    assert labels == ["Balance", "Deposit"]
    total = sum(i["amount_cents"] for i in invoice["installments"])
    assert total == invoice["total_cents"]


def check_null_label_converts_to_default_label(
    auth, contact_id, event_id
) -> None:
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(200000)],
        "installments": [
            {
                # Send the field with a null label to confirm the
                # API+DB allow it on the quote side.
                "label": None,
                "amount_cents": 200000,
                "due_date": (today + timedelta(days=21)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote = resp.json()
    assert quote["installments"][0]["label"] is None

    quote_id = quote["id"]
    _send_and_approve(auth, quote_id, contact_id)
    resp = client.post(f"/api/quotes/{quote_id}/convert", headers=auth)
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    # `invoice_installments.label` is NOT NULL — conversion fills in a
    # numbered default. Customer-facing copy stays neutral.
    assert invoice["installments"][0]["label"] == "Installment 1"
    assert invoice["installments"][0]["amount_cents"] == 200000


def check_zero_amount_installment_rejected(auth, contact_id, event_id) -> None:
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "installments": [
            {
                "label": "Bad",
                "amount_cents": 0,
                "due_date": (today + timedelta(days=14)).isoformat(),
            }
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    # Pydantic catches `amount_cents > 0` at validation time.
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    try:
        user_id, user_email = _make_admin()
        user_ids.append(user_id)
        auth = _login(user_email)

        # Round-trip + PATCH share one event/contact.
        c1, e1 = _seed_event("Phase 4 round-trip")
        contact_ids.append(c1)
        event_ids.append(e1)
        quote_id = check_round_trip_three_installments(auth, c1, e1)
        print("round-trip 3 installments via POST + GET ok")
        check_patch_replaces_schedule(auth, quote_id)
        print("PATCH installments replaces full list ok")

        check_zero_amount_installment_rejected(auth, c1, e1)
        print("zero-amount installment rejected ok")

        # Conversion paths each use a fresh event so prior conversions
        # don't shape the schedule defaults.
        c2, e2 = _seed_event("Phase 4 convert preserves")
        contact_ids.append(c2)
        event_ids.append(e2)
        check_conversion_carries_three_installments(auth, c2, e2)
        print("quote -> invoice conversion copies all 3 rows verbatim ok")

        c3, e3 = _seed_event("Phase 4 convert default")
        contact_ids.append(c3)
        event_ids.append(e3)
        check_conversion_falls_through_to_default(auth, c3, e3)
        print("conversion with no schedule falls back to legacy 50/50 ok")

        c4, e4 = _seed_event("Phase 4 null label")
        contact_ids.append(c4)
        event_ids.append(e4)
        check_null_label_converts_to_default_label(auth, c4, e4)
        print("null-label quote installment converts to 'Installment N' ok")

        print()
        print("phase 4 quote installments smoke ok")
        return 0
    finally:
        _cleanup(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
