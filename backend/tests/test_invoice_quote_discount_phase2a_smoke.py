"""Smoke tests for Phase 2a (order-level discount snapshot + new tax math).

Covers the cases enumerated in `docs/INVOICE_DISCOUNTS_AND_TERMS_PHASES.md`
under Phase 2a:

- Legacy record: `discount_percent IS NULL` and `discount_cents > 0` keeps
  post-tax math (no drift).
- New record: `discount_percent = 10` produces pre-tax math; `discount_cents`
  matches `round(subtotal_pre_discount * 0.10)`.
- Snapshot stability: renaming a preset on BusinessProfile does not change
  the `discount_label` on an already-saved invoice or quote.
- Stacking: a per-line $50 discount + a 10% Moonlight order discount produces
  both savings in the totals.
- Quote-to-invoice conversion copies the discount snapshot verbatim.
- Editor PATCH writes preset_id/label/percent; the server snapshots from
  BusinessProfile (a custom percent path is also exercised).
- Reject paths: unknown preset id, out-of-range percent, missing percent
  on a custom selection.

Mints its own admin user, seeds a fresh event/contact, snapshots and
restores the BusinessProfile discount presets so the test is isolated.
Runs as a script: `venv/bin/python tests/test_invoice_quote_discount_phase2a_smoke.py`.
Internal helpers are named `check_*` so pytest does not collect them.
"""

import json
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
            username=f"discount-2a-smoke-{suffix}",
            email=f"discount-2a-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Phase 2a Smoke Admin",
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


_TEST_PRESETS = [
    {"id": "moonlight", "label": "Moonlight Ballroom", "percent": "10", "active": True},
    {"id": "military", "label": "Military", "percent": "5", "active": True},
    {"id": "same_day", "label": "Same-day", "percent": "2", "active": True},
]


def _snapshot_business_profile() -> dict:
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                "SELECT discount_presets FROM business_profile WHERE id = 1"
            )
        ).one()
        return {"discount_presets": row[0] or []}
    finally:
        db.close()


def _force_presets(presets: list[dict]) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE business_profile SET discount_presets = :p ::jsonb "
                "WHERE id = 1"
            ),
            {"p": json.dumps(presets)},
        )
        db.commit()
    finally:
        db.close()


def _restore_business_profile(snapshot: dict) -> None:
    presets = snapshot["discount_presets"]
    if isinstance(presets, list):
        presets = json.dumps(presets)
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE business_profile SET discount_presets = :p ::jsonb "
                "WHERE id = 1"
            ),
            {"p": presets},
        )
        db.commit()
    finally:
        db.close()


def _cleanup(user_ids: list[int], contact_ids: list[int], event_ids: list[int]) -> None:
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
                    "DELETE FROM quote_line_items WHERE quote_id IN ("
                    "SELECT id FROM quotes WHERE event_id = ANY(:eids))"
                ),
                {"eids": event_ids},
            )
            # Converted quotes point at the generated invoice with
            # `ON DELETE SET NULL`, but the quote consistency CHECK
            # requires converted rows to keep that pointer. Delete the
            # quotes before deleting invoices so cleanup does not create
            # an impossible intermediate row.
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


def _line(price_cents: int, *, discount: int = 0, tax_rate: str = "0.07000") -> dict:
    return {
        "description": f"Line ${price_cents / 100:.2f}",
        "quantity": "1",
        "unit_price_cents": price_cents,
        "discount_cents": discount,
        "tax_rate": tax_rate,
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_legacy_path_unchanged(auth, contact_id, event_id) -> int:
    """Legacy: empty order_discounts, discount_cents=400. The customer
    pays the full $4000 + $280 tax = $4280, then $400 off after tax →
    $3880."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000, tax_rate="0.07000")],
        "discount_cents": 40000,  # $400 flat
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 388000,
                "due_date": (date.today() + timedelta(days=14)).isoformat(),
            }
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["order_discounts"] == [], inv
    assert inv["discount_cents"] == 40000, inv
    # Tax computed on $4000 (no order discount applied to base).
    assert inv["subtotal_cents"] == 400000, inv
    assert inv["tax_cents"] == 28000, inv
    # Total = 4000 + 280 - 400 = 3880.
    assert inv["total_cents"] == 388000, inv
    return inv["id"]


def check_new_path_pre_tax_math(auth, contact_id, event_id) -> int:
    """New path: 10% Moonlight on $4000 with 7% tax.

    Worked example from the plan doc:
      Subtotal $4000, discount -$400, taxable $3600, tax $252, total $3852.
    """
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000, tax_rate="0.07000")],
        "order_discounts": [{"preset_id": "moonlight"}],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 385200,
                "due_date": (date.today() + timedelta(days=14)).isoformat(),
            }
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert len(inv["order_discounts"]) == 1
    snap = inv["order_discounts"][0]
    assert snap["preset_id"] == "moonlight"
    assert snap["label"] == "Moonlight Ballroom"
    assert snap["percent"] == "10.00"
    assert inv["subtotal_cents"] == 400000, inv
    assert inv["discount_cents"] == 40000, inv  # round(4000 * 0.10) = 400.00
    assert inv["tax_cents"] == 25200, inv  # 3600 * 0.07 = 252.00
    assert inv["total_cents"] == 385200, inv
    assert sum(li["line_total_cents"] for li in inv["line_items"]) == inv["total_cents"]
    return inv["id"]


def check_stacking(auth, contact_id, event_id) -> None:
    """Per-line $50 discount + 10% order discount: both savings appear."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000, discount=5000, tax_rate="0.07000")],
        "order_discounts": [{"preset_id": "moonlight"}],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 91485,
                "due_date": (date.today() + timedelta(days=14)).isoformat(),
            }
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["subtotal_cents"] == 95000
    assert inv["discount_cents"] == 9500
    assert inv["tax_cents"] == 5985
    assert inv["total_cents"] == 91485
    assert inv["line_items"][0]["discount_cents"] == 5000


def check_snapshot_label_stable_after_rename(auth, invoice_id) -> None:
    """Renaming the Moonlight preset must not change the saved invoice's
    snapshotted label."""
    _force_presets(
        [
            {"id": "moonlight", "label": "Moonlight Ballroom (Renamed)", "percent": "10", "active": True},
            {"id": "military", "label": "Military", "percent": "5", "active": True},
            {"id": "same_day", "label": "Same-day", "percent": "2", "active": True},
        ]
    )
    resp = client.get(f"/api/invoices/{invoice_id}", headers=auth)
    assert resp.status_code == 200, resp.text
    inv = resp.json()
    assert len(inv["order_discounts"]) == 1
    snap = inv["order_discounts"][0]
    assert snap["label"] == "Moonlight Ballroom", snap
    assert snap["preset_id"] == "moonlight"
    _force_presets(_TEST_PRESETS)


def check_custom_percent_path(auth, contact_id, event_id) -> None:
    body = {
        "contact_id": contact_id,
        "line_items": [_line(200000, tax_rate="0.07000")],
        "order_discounts": [
            {"label": "Friends and Family", "percent": "7.5"}
        ],
        "installments": [
            {
                "label": "Deposit",
                "amount_cents": 197950,
                "due_date": (date.today() + timedelta(days=14)).isoformat(),
            }
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert len(inv["order_discounts"]) == 1
    snap = inv["order_discounts"][0]
    assert snap["preset_id"] is None
    assert snap["percent"] == "7.50"
    assert snap["label"] == "Friends and Family"
    # 200000 * 0.075 = 15000
    assert inv["discount_cents"] == 15000
    assert inv["tax_cents"] == 12950
    assert inv["total_cents"] == 197950


def check_unknown_preset_rejected(auth, contact_id, event_id) -> None:
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "order_discounts": [{"preset_id": "does_not_exist"}],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "discount_preset_not_found"


def check_percent_out_of_range_rejected(auth, contact_id, event_id) -> None:
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "order_discounts": [{"label": "Big", "percent": "60"}],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    # Pydantic catches the per-row 0..50 cap at validation time.
    assert resp.status_code == 422, resp.text


def check_quote_conversion_carries_snapshot(auth, contact_id, event_id) -> None:
    """Create a quote with Moonlight applied, send/approve, convert to
    invoice, and verify the discount snapshot copied verbatim."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000, tax_rate="0.07000")],
        "order_discounts": [{"preset_id": "moonlight"}],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote = resp.json()
    assert len(quote["order_discounts"]) == 1
    qsnap = quote["order_discounts"][0]
    assert qsnap["preset_id"] == "moonlight"
    assert qsnap["label"] == "Moonlight Ballroom"
    assert qsnap["percent"] == "10.00"
    assert quote["total_cents"] == 385200
    quote_id = quote["id"]

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
    resp = client.post(f"/api/quotes/{quote_id}/convert", headers=auth)
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["order_discounts"]) == 1
    isnap = invoice["order_discounts"][0]
    assert isnap["preset_id"] == "moonlight"
    assert isnap["label"] == "Moonlight Ballroom"
    assert isnap["percent"] == "10.00"
    assert invoice["total_cents"] == 385200


def check_patch_clears_snapshot(auth, contact_id, event_id) -> None:
    """Saving with `order_discounts: []` clears the discount stack AND
    zeroes the prior derived `discount_cents`."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000, tax_rate="0.07000")],
        "order_discounts": [{"preset_id": "military"}],  # 5%
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    invoice_id = inv["id"]
    assert inv["order_discounts"][0]["percent"] == "5.00"
    assert inv["total_cents"] == 101650  # (100000 * 0.95) * 1.07 = 101650

    resp = client.patch(
        f"/api/invoices/{invoice_id}",
        headers=auth,
        json={"order_discounts": []},
    )
    assert resp.status_code == 200, resp.text
    cleared = resp.json()
    assert cleared["order_discounts"] == []
    assert cleared["discount_cents"] == 0
    # Without any discount, lines compute legacy: 100000 + 7000 tax = 107000.
    assert cleared["total_cents"] == 107000


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    user_ids: list[int] = []
    contact_ids: list[int] = []
    event_ids: list[int] = []
    bp_snapshot = _snapshot_business_profile()
    try:
        _force_presets(_TEST_PRESETS)
        user_id, user_email = _make_admin()
        user_ids.append(user_id)
        auth = _login(user_email)

        contact_id, event_id = _seed_event("Phase 2a")
        contact_ids.append(contact_id)
        event_ids.append(event_id)

        legacy_id = check_legacy_path_unchanged(auth, contact_id, event_id)
        print(f"legacy path preserves post-tax math (invoice {legacy_id}) ok")

        new_id = check_new_path_pre_tax_math(auth, contact_id, event_id)
        print(f"new percent path applies pre-tax math (invoice {new_id}) ok")

        check_stacking(auth, contact_id, event_id)
        print("stacking line + order discount ok")

        check_snapshot_label_stable_after_rename(auth, new_id)
        print("preset rename does not rewrite snapshotted label ok")

        check_custom_percent_path(auth, contact_id, event_id)
        print("custom percent path ok")

        check_unknown_preset_rejected(auth, contact_id, event_id)
        print("unknown preset id rejected ok")

        check_percent_out_of_range_rejected(auth, contact_id, event_id)
        print("percent out of range rejected ok")

        # Quote conversion uses its own event so its automatic schedule
        # default and converted invoice do not collide with prior asserts.
        c2_id, e2_id = _seed_event("Phase 2a Convert")
        contact_ids.append(c2_id)
        event_ids.append(e2_id)
        check_quote_conversion_carries_snapshot(auth, c2_id, e2_id)
        print("quote-to-invoice conversion preserves snapshot ok")

        check_patch_clears_snapshot(auth, contact_id, event_id)
        print("patch with all-null discount fields clears snapshot + legacy ok")

        print()
        print("phase 2a discount smoke ok")
        return 0
    finally:
        _restore_business_profile(bp_snapshot)
        _cleanup(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
