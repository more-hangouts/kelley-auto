"""Smoke tests for the business profile surface (Phase 3 backend slice).

Covers:
- 401 on every route without a token
- GET returns the seeded singleton
- PATCH updates editable fields, rejects unknown ones
- legal_name cannot be cleared
- default_tax_rate validates [0, 1)
- country forced to 2-letter ISO
- logo upload (png + svg + jpg), download, delete
- logo type rejection (.exe), oversize rejection
- prior logo file is removed when a new ext replaces it

Storage uses a tempdir override of DOCUMENT_STORAGE_ROOT so the real
upload tree is never touched. Cleans up on exit.
"""

import os
import shutil
import sys
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Override DOCUMENT_STORAGE_ROOT before any settings import.
_TMP_STORAGE = tempfile.mkdtemp(prefix="business-profile-smoke-")
os.environ["DOCUMENT_STORAGE_ROOT"] = _TMP_STORAGE

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import User  # noqa: E402

client = TestClient(app)


def _make_admin() -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"bp-smoke-{suffix}",
            email=f"bp-smoke-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name="BP Smoke Admin",
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


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


_SEEDED_DISCOUNT_PRESETS = [
    {"id": "moonlight", "label": "Moonlight Ballroom", "percent": 10, "active": True},
    {"id": "military", "label": "Military", "percent": 5, "active": True},
    {"id": "same_day", "label": "Same-day", "percent": 2, "active": True},
]


def _restore_seed_profile() -> dict:
    """Snapshot the singleton row, then reset it to migration-seed values so
    the test runs deterministically regardless of what real-world data is in
    the singleton. The caller restores from the returned dict on exit."""
    db = SessionLocal()
    try:
        row = db.execute(
            sql_text("SELECT * FROM business_profile WHERE id = 1")
        ).mappings().one()
        snapshot = dict(row)
        db.execute(
            sql_text(
                """
                UPDATE business_profile SET
                    legal_name = 'Bellas XV',
                    display_name = NULL,
                    address_line1 = NULL,
                    address_line2 = NULL,
                    city = NULL,
                    state = NULL,
                    postal_code = NULL,
                    country = 'US',
                    phone = NULL,
                    email = NULL,
                    website = NULL,
                    logo_storage_key = NULL,
                    default_tax_rate = 0,
                    default_tax_name = NULL,
                    default_invoice_terms = NULL,
                    default_invoice_footer = NULL,
                    default_payment_instructions = NULL,
                    discount_presets = :discount_presets ::jsonb,
                    default_payment_plan_count = NULL,
                    default_deposit_percent = NULL,
                    updated_by_user_id = NULL,
                    updated_at = NOW()
                WHERE id = 1
                """
            ),
            {
                "discount_presets": (
                    '[{"id":"moonlight","label":"Moonlight Ballroom","percent":10,"active":true},'
                    '{"id":"military","label":"Military","percent":5,"active":true},'
                    '{"id":"same_day","label":"Same-day","percent":2,"active":true}]'
                ),
            },
        )
        db.commit()
        return snapshot
    finally:
        db.close()


def _restore_profile(snapshot: dict) -> None:
    db = SessionLocal()
    try:
        # JSONB columns come back as Python lists from the SELECT. Re-encode
        # to JSON so the bind parameter goes back through the JSONB cast.
        import json

        params = dict(snapshot)
        if isinstance(params.get("discount_presets"), (list, dict)):
            params["discount_presets"] = json.dumps(params["discount_presets"])
        db.execute(
            sql_text(
                """
                UPDATE business_profile SET
                    legal_name = :legal_name,
                    display_name = :display_name,
                    address_line1 = :address_line1,
                    address_line2 = :address_line2,
                    city = :city,
                    state = :state,
                    postal_code = :postal_code,
                    country = :country,
                    phone = :phone,
                    email = :email,
                    website = :website,
                    logo_storage_key = :logo_storage_key,
                    default_tax_rate = :default_tax_rate,
                    default_tax_name = :default_tax_name,
                    default_invoice_terms = :default_invoice_terms,
                    default_invoice_footer = :default_invoice_footer,
                    default_payment_instructions = :default_payment_instructions,
                    discount_presets = :discount_presets ::jsonb,
                    default_payment_plan_count = :default_payment_plan_count,
                    default_deposit_percent = :default_deposit_percent,
                    updated_by_user_id = :updated_by_user_id,
                    updated_at = :updated_at
                WHERE id = 1
                """
            ),
            params,
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required
# ---------------------------------------------------------------------------


def check_auth_required():
    for method, path in (
        ("get", "/api/business-profile"),
        ("patch", "/api/business-profile"),
        ("post", "/api/business-profile/logo"),
        ("delete", "/api/business-profile/logo"),
        ("get", "/api/business-profile/logo"),
    ):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


# ---------------------------------------------------------------------------
# GET / PATCH
# ---------------------------------------------------------------------------


def check_get_returns_singleton(auth):
    resp = client.get("/api/business-profile", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "legal_name" in body
    assert body["legal_name"] == "Bellas XV"  # migration seed
    assert body["country"] == "US"
    assert body["has_logo"] is False


def check_patch_updates_fields(auth):
    body = {
        "legal_name": "Bella's XV LLC",
        "display_name": "Bella's XV",
        "address_line1": "123 Main St",
        "city": "San Antonio",
        "state": "TX",
        "postal_code": "78205",
        "phone": "(210) 555-0100",
        "email": "hello@shopbellasxv.com",
        "default_tax_rate": "0.08250",
        "default_tax_name": "TX Sales",
        "default_invoice_terms": "Balance due 30 days before event date.",
        "default_invoice_footer": "Thank you for your business.",
        "default_payment_instructions": "Pay by check or Zelle to (210) 555-0100.",
    }
    resp = client.patch("/api/business-profile", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["legal_name"] == "Bella's XV LLC"
    assert out["default_tax_rate"] == "0.08250"
    assert out["address_line1"] == "123 Main St"
    assert out["state"] == "TX"


def check_patch_rejects_unknown_fields(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"not_a_field": "x"},
    )
    # Pydantic strict mode rejects extra fields with 422.
    assert resp.status_code == 422, resp.text


def check_patch_rejects_empty_legal_name(auth):
    resp = client.patch(
        "/api/business-profile", headers=auth, json={"legal_name": "   "}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "legal_name_required"


def check_patch_rejects_invalid_tax_rate(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"default_tax_rate": "1.5"},
    )
    # Pydantic catches this at validation time (lt=1) → 422 from FastAPI.
    assert resp.status_code == 422, resp.text


def check_patch_normalizes_country(auth):
    resp = client.patch(
        "/api/business-profile", headers=auth, json={"country": "us"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["country"] == "US"


# ---------------------------------------------------------------------------
# Logo upload / download / delete
# ---------------------------------------------------------------------------


_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63600000000005000158d2efc40000000049454e44"
    "ae426082"
)
_SVG_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="purple"/></svg>'
)


def check_logo_round_trip(auth):
    # Upload a PNG.
    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.png", _PNG_1X1, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_logo"] is True

    # Download it back.
    resp = client.get("/api/business-profile/logo", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == _PNG_1X1[:8]


def check_logo_replace_with_different_ext_clears_old(auth):
    # Start with a PNG (likely already there from prior check) so we can
    # confirm the SVG replacement deletes the .png on disk.
    upload = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.png", _PNG_1X1, "image/png")},
    )
    assert upload.status_code == 200, upload.text

    png_path = Path(_TMP_STORAGE) / "business" / "logo.png"
    assert png_path.exists()

    # Replace with SVG.
    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.svg", _SVG_BYTES, "image/svg+xml")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_logo"] is True

    svg_path = Path(_TMP_STORAGE) / "business" / "logo.svg"
    assert svg_path.exists()
    assert not png_path.exists(), "old logo file should be deleted on ext change"

    resp = client.get("/api/business-profile/logo", headers=auth)
    assert resp.headers["content-type"] == "image/svg+xml"


def check_logo_delete_clears_state(auth):
    resp = client.delete("/api/business-profile/logo", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_logo"] is False
    resp = client.get("/api/business-profile/logo", headers=auth)
    assert resp.status_code == 404, resp.text


def check_logo_rejects_bad_type(auth):
    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert resp.status_code == 415, resp.text
    assert resp.json()["detail"]["code"] == "unsupported_logo_type"


# ---------------------------------------------------------------------------
# Phase 1: discount presets and payment plan defaults
# ---------------------------------------------------------------------------


def check_seed_presets_present(auth):
    resp = client.get("/api/business-profile", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    presets = body["discount_presets"]
    assert len(presets) == 3, presets
    by_id = {p["id"]: p for p in presets}
    assert by_id["moonlight"]["label"] == "Moonlight Ballroom"
    assert by_id["military"]["label"] == "Military"
    assert by_id["same_day"]["label"] == "Same-day"
    # Defaults arrive null until staff configure them.
    assert body["default_payment_plan_count"] is None
    assert body["default_deposit_percent"] is None


def check_patch_presets_round_trip(auth):
    payload = {
        "discount_presets": [
            {"id": "moonlight", "label": "Moonlight Ballroom", "percent": "12", "active": True},
            {"label": "Sister Promo", "percent": "7.5", "active": True},  # no id -> slug
            {"id": "veteran", "label": "Veteran", "percent": "5", "active": False},
        ],
        "default_payment_plan_count": 3,
        "default_deposit_percent": "60",
    }
    resp = client.patch("/api/business-profile", headers=auth, json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    presets = body["discount_presets"]
    assert len(presets) == 3
    assert presets[0]["id"] == "moonlight"
    assert presets[0]["percent"] == "12.00"
    # Server slugified the missing id.
    assert presets[1]["id"] == "sister_promo"
    assert presets[2]["id"] == "veteran"
    assert presets[2]["active"] is False
    assert body["default_payment_plan_count"] == 3
    assert body["default_deposit_percent"] == "60.00"


def check_patch_rejects_too_many_presets(auth):
    presets = [
        {"label": f"Promo {i}", "percent": "1"} for i in range(13)
    ]
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"discount_presets": presets},
    )
    # Pydantic validation does not cap list length; service-layer does.
    # Either is acceptable as 422.
    assert resp.status_code == 422, resp.text


def check_patch_rejects_percent_out_of_range(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"discount_presets": [{"label": "Too Big", "percent": "60"}]},
    )
    assert resp.status_code == 422, resp.text


def check_patch_rejects_blank_label(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"discount_presets": [{"label": "  ", "percent": "5"}]},
    )
    assert resp.status_code == 422, resp.text


def check_patch_rejects_duplicate_ids(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={
            "discount_presets": [
                {"id": "promo", "label": "First", "percent": "5"},
                {"id": "promo", "label": "Second", "percent": "5"},
            ]
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "duplicate_discount_preset_id"


def check_patch_rejects_bad_plan_count(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"default_payment_plan_count": 4},
    )
    assert resp.status_code == 422, resp.text


def check_patch_rejects_low_deposit(auth):
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={"default_deposit_percent": "49"},
    )
    assert resp.status_code == 422, resp.text


def check_patch_clears_defaults(auth):
    """Sending null clears the optional plan defaults back to fallback."""
    resp = client.patch(
        "/api/business-profile",
        headers=auth,
        json={
            "default_payment_plan_count": None,
            "default_deposit_percent": None,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_payment_plan_count"] is None
    assert body["default_deposit_percent"] is None


def check_logo_rejects_oversize(auth):
    # Real PNG magic so E1's magic-byte gate lets the payload through to
    # the size cap; the leading 8 bytes satisfy `validate_magic_bytes`,
    # everything past that pushes the body over the 2 MB cap.
    png_magic = b"\x89PNG\r\n\x1a\n"
    big = png_magic + b"\x00" * (3 * 1024 * 1024 - len(png_magic))
    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.png", big, "image/png")},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"]["code"] == "logo_too_large"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def main() -> int:
    user_id = None
    snapshot = _restore_seed_profile()
    try:
        check_auth_required()
        print("auth required ok")

        user_id, user_email = _make_admin()
        auth = _login(user_email)
        print("admin login ok")

        check_get_returns_singleton(auth)
        print("GET returns singleton ok")

        check_patch_updates_fields(auth)
        print("PATCH updates editable fields ok")

        check_patch_rejects_unknown_fields(auth)
        print("PATCH rejects unknown fields ok")

        check_patch_rejects_empty_legal_name(auth)
        print("PATCH rejects empty legal_name ok")

        check_patch_rejects_invalid_tax_rate(auth)
        print("PATCH rejects invalid tax_rate ok")

        check_patch_normalizes_country(auth)
        print("PATCH normalizes country to upper ok")

        check_seed_presets_present(auth)
        print("seed discount presets present ok")

        check_patch_presets_round_trip(auth)
        print("PATCH presets + plan defaults round trip ok")

        check_patch_rejects_too_many_presets(auth)
        print("PATCH rejects >12 presets ok")

        check_patch_rejects_percent_out_of_range(auth)
        print("PATCH rejects preset percent out of range ok")

        check_patch_rejects_blank_label(auth)
        print("PATCH rejects blank preset label ok")

        check_patch_rejects_duplicate_ids(auth)
        print("PATCH rejects duplicate preset ids ok")

        check_patch_rejects_bad_plan_count(auth)
        print("PATCH rejects bad default plan count ok")

        check_patch_rejects_low_deposit(auth)
        print("PATCH rejects deposit below floor ok")

        check_patch_clears_defaults(auth)
        print("PATCH clears optional defaults ok")

        check_logo_round_trip(auth)
        print("logo upload + download round trip ok")

        check_logo_replace_with_different_ext_clears_old(auth)
        print("logo replace with new ext clears old file ok")

        check_logo_delete_clears_state(auth)
        print("logo delete clears state ok")

        check_logo_rejects_bad_type(auth)
        print("logo bad type rejected ok")

        check_logo_rejects_oversize(auth)
        print("logo oversize rejected ok")

        print()
        print("business profile smoke ok")
        return 0
    finally:
        # Restore the singleton for re-runs.
        _restore_profile(snapshot)
        if user_id is not None:
            db = SessionLocal()
            try:
                db.execute(
                    sql_text("DELETE FROM users WHERE id = :id"), {"id": user_id}
                )
                db.commit()
            finally:
                db.close()
        shutil.rmtree(_TMP_STORAGE, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
