"""Smoke tests for Phase 7 (stacked order-level discounts).

Covers:

- Two stacked presets (Military 5% + Same-day 2%) combine additively;
  the order discount is 7% off the taxable base.
- A stacked preset + custom row coexist on one invoice; both snapshot
  independently.
- Combined > 50% cap is rejected with `combined_discount_too_high`.
- Per-row >50% is rejected by Pydantic at the request boundary.
- PATCH with `order_discounts: []` clears the stack and returns the
  record to the legacy flat-amount path.
- Quote conversion copies the stacked rows verbatim into the invoice.

Internal helpers are named `check_*` so pytest does not collect them.
Runs as a script: `venv/bin/python tests/test_stacked_discounts_smoke.py`.
"""

from __future__ import annotations

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
from database.models import BusinessProfile, Contact, Event, User  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"stacked-smoke-{suffix}",
            email=f"stacked-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="Phase 7 Smoke Admin",
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
            for tbl, idcol, parent in (
                ("invoice_invitations", "invoice_id", "invoices"),
                ("invoice_installments", "invoice_id", "invoices"),
                ("invoice_line_items", "invoice_id", "invoices"),
                ("invoice_order_discounts", "invoice_id", "invoices"),
                ("quote_invitations", "quote_id", "quotes"),
                ("quote_installments", "quote_id", "quotes"),
                ("quote_line_items", "quote_id", "quotes"),
                ("quote_order_discounts", "quote_id", "quotes"),
            ):
                db.execute(
                    sql_text(
                        f"DELETE FROM {tbl} WHERE {idcol} IN ("
                        f"SELECT id FROM {parent} WHERE event_id = ANY(:eids))"
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
# Fixture helpers
# ---------------------------------------------------------------------------


def _line(price_cents: int, *, tax_rate: str = "0") -> dict:
    return {
        "description": f"Line ${price_cents / 100:.2f}",
        "quantity": "1",
        "unit_price_cents": price_cents,
        "discount_cents": 0,
        "tax_rate": tax_rate,
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_two_presets_combine_additively(auth, contact_id, event_id) -> None:
    """Military 5% + Same-day 2% = 7% off the taxable base of $4000."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "order_discounts": [
            {"preset_id": "military"},
            {"preset_id": "same_day"},
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert len(inv["order_discounts"]) == 2
    labels = [d["label"] for d in inv["order_discounts"]]
    assert "Military" in labels
    assert "Same-day" in labels
    # Pre-order subtotal is the gross $4000 (no per-line discounts).
    assert inv["subtotal_cents"] == 400000
    # Combined 7% of 400000 = 28000.
    assert inv["discount_cents"] == 28000
    # No tax on this fixture, so total = 372000.
    assert inv["total_cents"] == 372000


def check_preset_plus_custom_coexist(auth, contact_id, event_id) -> None:
    """A preset row and a custom row stack independently."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(200000)],
        "order_discounts": [
            {"preset_id": "moonlight"},  # 10%
            {"label": "Friends", "percent": "5"},
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    snaps = inv["order_discounts"]
    assert len(snaps) == 2
    # Combined 15% of 200000 = 30000 off.
    assert inv["discount_cents"] == 30000
    assert inv["total_cents"] == 170000
    # Verify the custom row landed without a preset_id.
    custom = next(s for s in snaps if s["label"] == "Friends")
    assert custom["preset_id"] is None
    assert custom["percent"] == "5.00"
    moonlight = next(s for s in snaps if s["preset_id"] == "moonlight")
    assert moonlight["label"] == "Moonlight Ballroom"
    assert moonlight["percent"] == "10.00"


def check_combined_cap_rejected(auth, contact_id, event_id) -> None:
    """Sum of stacked percents > 50 must reject with
    `combined_discount_too_high`."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "order_discounts": [
            {"label": "A", "percent": "30"},
            {"label": "B", "percent": "30"},  # combined 60%
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "combined_discount_too_high"


def check_per_row_cap_rejected(auth, contact_id, event_id) -> None:
    """A single row > 50% must reject at Pydantic validation time."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "order_discounts": [{"label": "Too big", "percent": "60"}],
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 422, resp.text


def check_clear_via_patch(auth, contact_id, event_id) -> None:
    """`order_discounts: []` clears the stack and returns the record to
    the legacy flat-amount path."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(100000)],
        "order_discounts": [{"preset_id": "moonlight"}],  # 10%
    }
    resp = client.post(
        f"/api/events/{event_id}/invoices", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    invoice_id = inv["id"]
    assert inv["discount_cents"] == 10000

    resp = client.patch(
        f"/api/invoices/{invoice_id}",
        headers=auth,
        json={"order_discounts": []},
    )
    assert resp.status_code == 200, resp.text
    cleared = resp.json()
    assert cleared["order_discounts"] == []
    assert cleared["discount_cents"] == 0
    assert cleared["total_cents"] == 100000


def check_quote_conversion_carries_stack(auth, contact_id, event_id) -> None:
    """Convert a quote with two stacked rows and verify both copy
    verbatim into the invoice."""
    body = {
        "contact_id": contact_id,
        "line_items": [_line(400000)],
        "order_discounts": [
            {"preset_id": "military"},
            {"preset_id": "same_day"},
        ],
    }
    resp = client.post(
        f"/api/events/{event_id}/quotes", headers=auth, json=body
    )
    assert resp.status_code == 201, resp.text
    quote = resp.json()
    quote_id = quote["id"]
    assert len(quote["order_discounts"]) == 2

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
            "signature_name": "Phase 7 Customer",
        },
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/quotes/{quote_id}/convert", headers=auth)
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert len(invoice["order_discounts"]) == 2
    inv_snaps = sorted(
        invoice["order_discounts"], key=lambda s: s["sort_order"]
    )
    quote_snaps = sorted(
        quote["order_discounts"], key=lambda s: s["sort_order"]
    )
    for q_row, i_row in zip(quote_snaps, inv_snaps):
        assert q_row["preset_id"] == i_row["preset_id"]
        assert q_row["label"] == i_row["label"]
        assert q_row["percent"] == i_row["percent"]
    assert invoice["discount_cents"] == 28000
    assert invoice["total_cents"] == 372000


def check_deleted_preset_snapshot_survives_patch(auth, contact_id, event_id) -> None:
    """If a preset is deleted after a record is saved, PATCH may preserve
    the existing snapshotted row. Brand-new unknown preset ids still reject
    because create paths do not pass an existing-snapshot fallback."""
    temp_id = f"phase7_deleted_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        profile = db.get(BusinessProfile, 1)
        original_presets = list(profile.discount_presets or [])
        profile.discount_presets = [
            *original_presets,
            {
                "id": temp_id,
                "label": "Temporary Deleted Preset",
                "percent": "5",
                "active": True,
            },
        ]
        db.commit()
    finally:
        db.close()

    try:
        resp = client.post(
            f"/api/events/{event_id}/invoices",
            headers=auth,
            json={
                "contact_id": contact_id,
                "line_items": [_line(100000)],
                "order_discounts": [{"preset_id": temp_id}],
            },
        )
        assert resp.status_code == 201, resp.text
        invoice = resp.json()
        invoice_id = invoice["id"]
        assert invoice["discount_cents"] == 5000

        db = SessionLocal()
        try:
            profile = db.get(BusinessProfile, 1)
            profile.discount_presets = [
                p for p in (profile.discount_presets or []) if p.get("id") != temp_id
            ]
            db.commit()
        finally:
            db.close()

        resp = client.patch(
            f"/api/invoices/{invoice_id}",
            headers=auth,
            json={
                "private_notes": "preset was deleted after save",
                "order_discounts": [{"preset_id": temp_id}],
            },
        )
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["private_notes"] == "preset was deleted after save"
        assert patched["discount_cents"] == 5000
        before = invoice["order_discounts"][0]
        after = patched["order_discounts"][0]
        assert after["sort_order"] == before["sort_order"]
        assert after["preset_id"] == before["preset_id"]
        assert after["label"] == before["label"]
        assert after["percent"] == before["percent"]

        resp = client.post(
            f"/api/events/{event_id}/invoices",
            headers=auth,
            json={
                "contact_id": contact_id,
                "line_items": [_line(100000)],
                "order_discounts": [{"preset_id": temp_id}],
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "discount_preset_not_found"
    finally:
        db = SessionLocal()
        try:
            profile = db.get(BusinessProfile, 1)
            profile.discount_presets = original_presets
            db.commit()
        finally:
            db.close()


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

        c1, e1 = _seed_event("Phase 7 stacked")
        contact_ids.append(c1)
        event_ids.append(e1)
        check_two_presets_combine_additively(auth, c1, e1)
        print("two stacked presets combine additively ok")

        c2, e2 = _seed_event("Phase 7 mixed")
        contact_ids.append(c2)
        event_ids.append(e2)
        check_preset_plus_custom_coexist(auth, c2, e2)
        print("preset + custom rows coexist on one invoice ok")

        c3, e3 = _seed_event("Phase 7 cap")
        contact_ids.append(c3)
        event_ids.append(e3)
        check_combined_cap_rejected(auth, c3, e3)
        print("combined > 50% rejected with combined_discount_too_high ok")

        c4, e4 = _seed_event("Phase 7 per-row")
        contact_ids.append(c4)
        event_ids.append(e4)
        check_per_row_cap_rejected(auth, c4, e4)
        print("per-row > 50% rejected at Pydantic boundary ok")

        c5, e5 = _seed_event("Phase 7 clear")
        contact_ids.append(c5)
        event_ids.append(e5)
        check_clear_via_patch(auth, c5, e5)
        print("PATCH order_discounts=[] clears the stack ok")

        c6, e6 = _seed_event("Phase 7 convert")
        contact_ids.append(c6)
        event_ids.append(e6)
        check_quote_conversion_carries_stack(auth, c6, e6)
        print("quote conversion copies stacked rows verbatim ok")

        c7, e7 = _seed_event("Phase 7 deleted preset")
        contact_ids.append(c7)
        event_ids.append(e7)
        check_deleted_preset_snapshot_survives_patch(auth, c7, e7)
        print("deleted preset snapshot survives unrelated PATCH ok")

        print()
        print("phase 7 stacked discounts smoke ok")
        return 0
    finally:
        _cleanup(user_ids, contact_ids, event_ids)


if __name__ == "__main__":
    sys.exit(main())
