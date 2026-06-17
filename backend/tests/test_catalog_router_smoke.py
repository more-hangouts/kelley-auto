"""Smoke tests for the authenticated staff catalog API."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

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
_SKU_PREFIX = f"CAT-ROUTER-{uuid.uuid4().hex[:8].upper()}-"
_MORI_PREFIX = f"MORI-{_SKU_PREFIX}"


def _make_user(role: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"catalog-router-{role}-{suffix}",
            email=f"catalog-router-{role}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Catalog Router {role}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id, user.email
    finally:
        db.close()


def _login(email: str) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


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


def _cleanup(user_ids: list[int], baseline_seq: int) -> None:
    db = SessionLocal()
    try:
        for prefix in (_SKU_PREFIX, _MORI_PREFIX):
            db.execute(
                sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
                {"p": prefix + "%"},
            )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.execute(
            sql_text(
                "UPDATE numbering_state SET catalog_public_code_seq = :s "
                "WHERE id = 1"
            ),
            {"s": baseline_seq},
        )
        db.commit()
    finally:
        db.close()


def _imported_count() -> int:
    db = SessionLocal()
    try:
        return int(
            db.execute(
                sql_text(
                    "SELECT COUNT(*) FROM catalog_items "
                    "WHERE designer = 'Morilee'"
                )
            ).scalar()
        )
    finally:
        db.close()


def main() -> int:
    baseline_seq = _get_seq()
    admin_id, admin_email = _make_user("admin")
    staff_id, staff_email = _make_user("sales")
    try:
        admin_auth = _login(admin_email)
        user_auth = _login(staff_email)
        print("login ok")

        resp = client.get("/api/catalog", headers=user_auth, params={"limit": 1})
        assert resp.status_code == 200, resp.text
        print("sales-scoped catalog read ok")

        resp = client.post(
            "/api/catalog",
            headers=user_auth,
            json={
                "internal_sku": _SKU_PREFIX + "SALES-WRITE",
                "designer": "Denied Designer",
                "style_number": "DENIED",
                "color": "Ivory",
                "category": "quince_gown",
            },
        )
        assert resp.status_code == 403, resp.text
        print("sales-scoped catalog write denied ok")

        morilee_payload = {
            "internal_sku": _MORI_PREFIX + "SEEDED",
            "designer": "Morilee",
            "style_number": "M-ROUTER-001",
            "color": "Blush",
            "category": "quince_gown",
            "product_title": "Router Morilee Seed",
            "description_text": "Router smoke seed row.",
            "image_urls": ["https://example.com/morilee.jpg"],
        }
        resp = client.post(
            "/api/catalog", headers=admin_auth, json=morilee_payload
        )
        assert resp.status_code == 201, resp.text

        resp = client.get(
            "/api/catalog",
            headers=admin_auth,
            params={"designer": "Morilee", "limit": 1},
        )
        assert resp.status_code == 200, resp.text
        imported = resp.json()
        assert len(imported) == 1, imported
        assert imported[0]["internal_sku"].startswith("MORI-")
        assert _imported_count() >= 1
        print("Morilee-filtered list ok")

        payload = {
            "internal_sku": _SKU_PREFIX + "BASIC",
            "designer": "Test Designer",
            "style_number": "T-001",
            "color": "Ivory",
            "category": "quince_gown",
            "product_title": "Router Test Gown",
            "description_text": "Router smoke item.",
            "image_urls": ["https://example.com/image.jpg"],
        }
        resp = client.post("/api/catalog", headers=admin_auth, json=payload)
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["id"] > 0
        assert created["public_code"].startswith("BVX-")
        assert created["internal_sku"] == payload["internal_sku"]
        print("create ok")

        resp = client.post("/api/catalog", headers=admin_auth, json=payload)
        assert resp.status_code == 409, resp.text
        print("duplicate rejected ok")

        bad = dict(payload)
        bad["internal_sku"] = _SKU_PREFIX + "PUBLIC-CODE-SETTER"
        bad["public_code"] = "BVX-99999"
        resp = client.post("/api/catalog", headers=admin_auth, json=bad)
        assert resp.status_code == 422, resp.text
        print("public_code setter rejected ok")

        for path in (
            f"/api/catalog/{created['id']}",
            f"/api/catalog/by-internal-sku/{created['internal_sku']}",
            f"/api/catalog/by-public-code/{created['public_code']}",
        ):
            resp = client.get(path, headers=admin_auth)
            assert resp.status_code == 200, f"{path}: {resp.text}"
            assert resp.json()["id"] == created["id"]
        print("lookup endpoints ok")

        print()
        print("catalog router smoke ok")
        return 0
    finally:
        _cleanup([admin_id, staff_id], baseline_seq)


if __name__ == "__main__":
    sys.exit(main())
