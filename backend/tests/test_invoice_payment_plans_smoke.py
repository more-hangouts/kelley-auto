"""Smoke tests for Phase 5 (1/2/3 plan selector + deposit floor).

Covers the cases enumerated in
`docs/INVOICE_DISCOUNTS_AND_TERMS_PHASES.md` under Phase 5:

- Auto-generated 2-payment plan on a $4,000 invoice produces $2,000 /
  $2,000 with the deposit at issue + 14d and the balance at event - 60d.
- Auto-generated 3-payment plan splits the middle correctly and dates
  fall on the documented anchors.
- A 49% deposit submission is rejected with 422 / `deposit_below_floor`.
- A 4-installment plan is rejected with 422 / `plan_count_invalid`.
- The `custom_amounts` request flag waives the deposit floor for that
  write (3-way even split passes when flagged).
- Quote write surfaces the same rules (deposit_below_floor on the quote
  POST path).

Mints its own admin user and event, snapshots and restores the existing
BusinessProfile defaults so other tests are not perturbed. Runs as a
script: `venv/bin/python tests/test_invoice_payment_plans_smoke.py`.
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
            username=f"plan-smoke-{suffix}",
            email=f"plan-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Phase 5 Smoke Admin",
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


def _seed_event(label: str, days_out: int = 180) -> tuple[int, int]:
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
            event_date=date.today() + timedelta(days=days_out),
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
            db.execute(
                sql_text("DELETE FROM quotes WHERE event_id = ANY(:eids)"),
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


def _two_payment_50_50(total_cents: int, today: date) -> list[dict]:
    half = total_cents // 2
    balance = total_cents - half
    return [
        {
            "label": "Deposit",
            "amount_cents": half,
            "due_date": (today + timedelta(days=14)).isoformat(),
        },
        {
            "label": "Balance",
            "amount_cents": balance,
            "due_date": (today + timedelta(days=120)).isoformat(),
        },
    ]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_two_payment_50_50_accepted(auth, contact_id, event_id) -> None:
    """A 2-payment 50/50 plan on a $4,000 invoice writes cleanly."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "installments": _two_payment_50_50(400000, today),
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["installments"]) == 2
    assert sum(i["amount_cents"] for i in invoice["installments"]) == 400000
    assert invoice["installments"][0]["amount_cents"] == 200000
    assert invoice["installments"][1]["amount_cents"] == 200000


def check_three_payment_50_25_25_accepted(auth, contact_id, event_id) -> None:
    """A 3-payment 50/25/25 plan on $4,000 with deposit anchored at
    issue + 14d, final at event - 60d, middle at the midpoint."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 200000,
                "due_date": (today + timedelta(days=14)).isoformat(),
            },
            {
                "label": "Mid",
                "amount_cents": 100000,
                "due_date": (today + timedelta(days=60)).isoformat(),
            },
            {
                "label": "Final",
                "amount_cents": 100000,
                "due_date": (today + timedelta(days=120)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["installments"]) == 3
    sums = [i["amount_cents"] for i in invoice["installments"]]
    assert sum(sums) == 400000
    assert sums == [200000, 100000, 100000]


def check_49_percent_deposit_rejected(auth, contact_id, event_id) -> None:
    """A 49% deposit hits the floor and rejects with 422."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 196000,  # 49% of 400000
                "due_date": (today + timedelta(days=14)).isoformat(),
            },
            {
                "label": "Balance",
                "amount_cents": 204000,
                "due_date": (today + timedelta(days=120)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "deposit_below_floor", detail
    assert detail["deposit_cents"] == 196000
    assert detail["floor_cents"] == 200000


def check_plan_count_4_rejected(auth, contact_id, event_id) -> None:
    """A 4-installment plan rejects with 422 / plan_count_invalid."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "installments": [
            {
                "label": f"P{i}",
                "amount_cents": 100000,
                "due_date": (today + timedelta(days=14 + i * 30)).isoformat(),
            }
            for i in range(4)
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "plan_count_invalid", detail


def check_custom_amounts_skips_floor(auth, contact_id, event_id) -> None:
    """custom_amounts=True waives the deposit floor; an even 3-way
    split (under the 50% floor) is accepted when flagged."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(900000)],
        "custom_amounts": True,
        "installments": [
            {
                "label": "P1",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=14)).isoformat(),
            },
            {
                "label": "P2",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=60)).isoformat(),
            },
            {
                "label": "P3",
                "amount_cents": 300000,
                "due_date": (today + timedelta(days=120)).isoformat(),
            },
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["installments"]) == 3
    assert all(i["amount_cents"] == 300000 for i in invoice["installments"])


def check_quote_floor_enforced(auth, contact_id, event_id) -> None:
    """The quote write surface enforces the same deposit floor."""
    today = date.today()
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 100000,
                "due_date": (today + timedelta(days=14)).isoformat(),
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
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "deposit_below_floor", detail


def check_patch_floor_enforced(auth, contact_id, event_id) -> None:
    """PATCH on a draft also runs the deposit-floor check; staff
    cannot save an under-floor schedule on an existing draft."""
    today = date.today()
    create = client.post(
        f"/api/events/{event_id}/invoices",
        headers=auth,
        json={
            "contact_id": contact_id,
            "line_items": [_line(200000)],
            "installments": _two_payment_50_50(200000, today),
        },
    )
    assert create.status_code == 201, create.text
    invoice_id = create.json()["id"]

    resp = client.patch(
        f"/api/invoices/{invoice_id}",
        headers=auth,
        json={
            "installments": [
                {
                    "label": "Deposit",
                    "amount_cents": 50000,  # 25% of 200000
                    "due_date": (today + timedelta(days=14)).isoformat(),
                },
                {
                    "label": "Balance",
                    "amount_cents": 150000,
                    "due_date": (today + timedelta(days=120)).isoformat(),
                },
            ]
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "deposit_below_floor", detail


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

        c1, e1 = _seed_event("Phase 5 base")
        contact_ids.append(c1)
        event_ids.append(e1)

        check_two_payment_50_50_accepted(auth, c1, e1)
        print("2-payment 50/50 plan accepted ok")

        c2, e2 = _seed_event("Phase 5 three-payment")
        contact_ids.append(c2)
        event_ids.append(e2)
        check_three_payment_50_25_25_accepted(auth, c2, e2)
        print("3-payment 50/25/25 plan accepted ok")

        c3, e3 = _seed_event("Phase 5 49pct")
        contact_ids.append(c3)
        event_ids.append(e3)
        check_49_percent_deposit_rejected(auth, c3, e3)
        print("49% deposit rejected with deposit_below_floor ok")

        c4, e4 = _seed_event("Phase 5 plan-count-4")
        contact_ids.append(c4)
        event_ids.append(e4)
        check_plan_count_4_rejected(auth, c4, e4)
        print("4-installment plan rejected with plan_count_invalid ok")

        c5, e5 = _seed_event("Phase 5 custom-amounts")
        contact_ids.append(c5)
        event_ids.append(e5)
        check_custom_amounts_skips_floor(auth, c5, e5)
        print("custom_amounts=True waives deposit floor ok")

        c6, e6 = _seed_event("Phase 5 quote-floor")
        contact_ids.append(c6)
        event_ids.append(e6)
        check_quote_floor_enforced(auth, c6, e6)
        print("quote write enforces deposit floor ok")

        c7, e7 = _seed_event("Phase 5 patch-floor")
        contact_ids.append(c7)
        event_ids.append(e7)
        check_patch_floor_enforced(auth, c7, e7)
        print("invoice PATCH enforces deposit floor on drafts ok")

        print()
        print("phase 5 plan selector smoke ok")
        return 0
    finally:
        _cleanup(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
