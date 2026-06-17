"""Catalog SKU obfuscation Phase 7 hardening smoke.

Runs as a script:

    venv/bin/python tests/test_catalog_hardening_smoke.py
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
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

from api.server import app  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event  # noqa: E402
from services import invoice_service, portal_service, quote_service  # noqa: E402
from services.catalog_service import (  # noqa: E402
    FORBIDDEN_PUBLIC_RENDER_KEYS,
    CatalogItemInput,
    CatalogServiceError,
    assert_public_render_keys,
    create_catalog_item,
)
from services.invoice_service import InstallmentInput, LineItemInput  # noqa: E402


client = TestClient(app)
_PREFIX = f"P7-HARD-{uuid.uuid4().hex[:8].upper()}-"
_DESIGNER = "Phase Seven Designer"
_STYLE = "P7STYLE"
_INTERNAL_SKU = _PREFIX + "MORI-P7STYLE-IVORY"


def _get_seq() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT catalog_public_code_seq FROM numbering_state "
                    "WHERE id = 1"
                )
            ).scalar()
        )
    finally:
        db.close()


def _reset_seq(value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": value},
        )
        db.commit()
    finally:
        db.close()


def _seed() -> tuple[int, int, int]:
    db = SessionLocal()
    try:
        contact = Contact(
            display_name=_PREFIX + "Customer",
            phone="(210) 555-1111",
            email="phase7@example.com",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name=_PREFIX + "Event",
            event_date=date.today() + timedelta(days=180),
            quince_theme_colors=[],
            status="lead",
        )
        db.add(event)
        db.flush()
        cat = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_INTERNAL_SKU,
                color="Ivory",
                category="quince_gown",
                designer=_DESIGNER,
                style_number=_STYLE,
                house_name="Isabella",
            ),
        )
        db.commit()
        return contact.id, event.id, cat.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        p = _PREFIX + "%"
        events_subq = "(SELECT id FROM events WHERE event_name LIKE :p)"
        db.execute(
            sql_text(
                "DELETE FROM quote_invitations WHERE quote_id IN "
                f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                "DELETE FROM quote_line_items WHERE quote_id IN "
                f"(SELECT id FROM quotes WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(sql_text(f"DELETE FROM quotes WHERE event_id IN {events_subq}"), {"p": p})
        db.execute(
            sql_text(
                "DELETE FROM invoice_invitations WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_installments WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(
            sql_text(
                "DELETE FROM invoice_line_items WHERE invoice_id IN "
                f"(SELECT id FROM invoices WHERE event_id IN {events_subq})"
            ),
            {"p": p},
        )
        db.execute(sql_text(f"DELETE FROM invoices WHERE event_id IN {events_subq}"), {"p": p})
        db.execute(sql_text("DELETE FROM events WHERE event_name LIKE :p"), {"p": p})
        db.execute(sql_text("DELETE FROM contacts WHERE display_name LIKE :p"), {"p": p})
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": p},
        )
        db.commit()
    finally:
        db.close()


def _walk_keys(value, *, path: str = "$") -> None:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in FORBIDDEN_PUBLIC_RENDER_KEYS, (path, key)
            _walk_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _walk_keys(child, path=f"{path}[{idx}]")


def _make_quote(db, event_id: int, contact_id: int, catalog_id: int):
    quote = quote_service.create_quote(
        db,
        event_id=event_id,
        contact_id=contact_id,
        line_items=[
            LineItemInput(
                kind="product",
                catalog_item_id=catalog_id,
                size_label="08",
                quantity=Decimal("1"),
                unit_price_cents=120000,
            )
        ],
        public_notes="Safe public note",
    )
    quote_service.mark_sent(db, quote_id=quote.id)
    db.flush()
    return quote


def check_public_dto_and_signed_json(contact_id: int, event_id: int, catalog_id: int) -> None:
    db = SessionLocal()
    try:
        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[
                LineItemInput(
                    kind="product",
                    catalog_item_id=catalog_id,
                    size_label="08",
                    quantity=Decimal("1"),
                    unit_price_cents=120000,
                )
            ],
            installments=[
                InstallmentInput(
                    label="Deposit",
                    amount_cents=120000,
                    due_date=date.today() + timedelta(days=30),
                )
            ],
        )
        invoice_service.mark_sent(db, invoice_id=inv.id)
        quote = _make_quote(db, event_id, contact_id, catalog_id)
        db.commit()

        invoice_key = db.execute(
            sql_text(
                "SELECT public_key FROM invoice_invitations "
                "WHERE invoice_id = :id"
            ),
            {"id": inv.id},
        ).scalar()
        quote_key = db.execute(
            sql_text(
                "SELECT public_key FROM quote_invitations "
                "WHERE quote_id = :id"
            ),
            {"id": quote.id},
        ).scalar()

        invoice_view = portal_service.get_invoice_view_by_key(db, invoice_key)[0]
        quote_view = portal_service.get_quote_view_by_key(db, quote_key)[0]
        assert_public_render_keys(invoice_view)
        assert_public_render_keys(quote_view)
        _walk_keys(invoice_view)
        _walk_keys(quote_view)

        resp = client.post(
            f"/portal/quote/{quote_key}/accept",
            json={
                "signature_name": "Phase Seven Customer",
                "signature_base64": "not-a-real-signature-but-valid-shape",
            },
        )
        assert resp.status_code == 200, resp.text
        _walk_keys(resp.json())
    finally:
        db.close()


def check_public_text_guard(contact_id: int, event_id: int, catalog_id: int) -> None:
    db = SessionLocal()
    try:
        for field_name, kwargs in (
            ("public_notes", {"public_notes": f"Ask about {_DESIGNER}"}),
            ("terms", {"terms": f"Vendor style {_STYLE} is final sale"}),
            ("footer", {"footer": f"SKU {_INTERNAL_SKU}"}),
        ):
            try:
                invoice_service.create_invoice(
                    db,
                    event_id=event_id,
                    contact_id=contact_id,
                    line_items=[],
                    installments=[],
                    **kwargs,
                )
            except Exception as exc:
                assert getattr(exc, "code", None) == "catalog_leak", (
                    field_name,
                    exc,
                )
            else:
                raise AssertionError(f"{field_name} leak was accepted")
            db.rollback()

        inv = invoice_service.create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            line_items=[],
            installments=[],
        )
        db.commit()
        try:
            invoice_service.update_invoice(
                db,
                invoice_id=inv.id,
                patch={"public_notes": f"Contains {_STYLE}"},
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "catalog_leak", exc
        else:
            raise AssertionError("invoice update leak was accepted")
        db.rollback()

        inv = db.get(type(inv), inv.id)
        try:
            invoice_service.cancel_invoice(
                db, invoice_id=inv.id, reason=f"Cancelled {_DESIGNER}"
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "catalog_leak", exc
        else:
            raise AssertionError("invoice cancellation leak was accepted")
        db.rollback()

        quote = _make_quote(db, event_id, contact_id, catalog_id)
        db.commit()
        try:
            quote_service.reject_quote(
                db, quote_id=quote.id, reason=f"Rejected {_STYLE}"
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "catalog_leak", exc
        else:
            raise AssertionError("quote rejection leak was accepted")
        db.rollback()
    finally:
        db.close()


def _strip_template_comments(text: str) -> str:
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text


def check_template_lint() -> None:
    paths = [
        *(_REPO_ROOT / "templates" / "pdf").glob("*.html"),
        *(_REPO_ROOT / "templates" / "portal").glob("*.html"),
        _REPO_ROOT / "services" / "portal_email.py",
        _REPO_ROOT / "services" / "notification_templates.py",
    ]
    forbidden = sorted(FORBIDDEN_PUBLIC_RENDER_KEYS)
    hits: list[str] = []
    for path in paths:
        text = _strip_template_comments(path.read_text())
        for token in forbidden:
            pattern = re.compile(
                rf"(\.\s*{re.escape(token)}\b|\[['\"]{re.escape(token)}['\"]\])"
            )
            for match in pattern.finditer(text):
                hits.append(f"{path.relative_to(_REPO_ROOT)}:{match.group(0)}")
    assert not hits, "\n".join(hits)


def check_public_code_trigger(catalog_id: int) -> None:
    db = SessionLocal()
    try:
        try:
            db.execute(
                sql_text(
                    "UPDATE catalog_items SET public_code = 'BVX-99999' "
                    "WHERE id = :id"
                ),
                {"id": catalog_id},
            )
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError("public_code raw SQL update succeeded")
    finally:
        db.close()


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_seq()
    contact_id, event_id, catalog_id = _seed()
    try:
        check_public_dto_and_signed_json(contact_id, event_id, catalog_id)
        print("public DTO + signed-link JSON forbidden keys absent ok")
        check_public_text_guard(contact_id, event_id, catalog_id)
        print("public text forbidden-substring guard ok")
        check_template_lint()
        print("customer template forbidden-token lint ok")
        check_public_code_trigger(catalog_id)
        print("public_code immutable trigger ok")
        print()
        print("catalog phase 7 hardening smoke ok")
        return 0
    finally:
        _cleanup()
        _reset_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
