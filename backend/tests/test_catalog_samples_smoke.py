"""Catalog SKU obfuscation Phase 6 — samples + admin PATCH smoke.

Phase 6 keeps sample handling intentionally boolean: a catalog row
either has a floor sample or it does not. v1 deliberately avoids
reservations, stock decrements, warehouse locations, and inventory
valuation. This smoke covers:

  - ``find_catalog_items`` and ``search_catalog`` honor the
    ``is_sample`` filter (None=both, True=samples only,
    False=non-samples only) without changing the rest of the listing
    contract.
  - ``/api/catalog?is_sample=true`` on the ranked search path returns
    only sample rows.
  - ``/api/catalog/{id}`` admin PATCH flips ``is_sample`` and
    ``active``.
  - PATCH refuses to rewrite ``internal_sku`` (would break invoice/
    quote/special-order references) and ``public_code`` (immutable
    once issued; Phase 7 will add a DB trigger as belt-and-suspenders).
  - Empty PATCH body is a no-op (does not bump ``updated_at``).
  - Unknown fields and a non-list ``image_urls`` are rejected with
    domain codes.
  - Required catalog fields reject explicit nulls / invalid values
    cleanly instead of leaking database constraint errors.

Runs as a script:

    venv/bin/python tests/test_catalog_samples_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
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
from database.models import CatalogItem, User  # noqa: E402
from services.catalog_service import (  # noqa: E402
    CatalogItemInput,
    create_catalog_item,
    find_catalog_items,
    search_catalog,
)


client = TestClient(app)
_PREFIX = f"P6-SAMP-{uuid.uuid4().hex[:8].upper()}-"


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


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


def _seed() -> dict[str, int]:
    """Three rows: one sample (active), one non-sample (active), one
    sample (inactive). Lets the tests check the orthogonal axes
    without overlap."""
    db = SessionLocal()
    try:
        sample = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "SAMPLE-IVORY",
                color="Ivory",
                category="quince_gown",
                designer="Phase 6 Vendor",
                style_number="S-001",
                house_name="Sample House",
                is_sample=True,
                active=True,
            ),
        )
        non_sample = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "ORDERED-CHAMPAGNE",
                color="Champagne",
                category="quince_gown",
                designer="Phase 6 Vendor",
                style_number="S-002",
                house_name="Ordered House",
                is_sample=False,
                active=True,
            ),
        )
        sample_inactive = create_catalog_item(
            db,
            CatalogItemInput(
                internal_sku=_PREFIX + "SAMPLE-RETIRED",
                color="Sand",
                category="quince_gown",
                designer="Phase 6 Vendor",
                style_number="S-003",
                house_name="Sample Retired",
                is_sample=True,
                active=False,
            ),
        )
        db.commit()
        return {
            "sample": sample.id,
            "non_sample": non_sample.id,
            "sample_inactive": sample_inactive.id,
        }
    finally:
        db.close()


def _wipe(user_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        db.execute(
            sql_text("DELETE FROM catalog_items WHERE internal_sku LIKE :p"),
            {"p": _PREFIX + "%"},
        )
        if user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": user_ids},
            )
        db.commit()
    finally:
        db.close()


def _make_user(role: str) -> tuple[int, dict[str, str]]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"p6-{role}-{suffix}",
            email=f"p6-{role}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Phase 6 {role}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        resp = client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "smoke-pass-12345"},
        )
        assert resp.status_code == 200, resp.text
        return user.id, {"Authorization": f"Bearer {resp.json()['access_token']}"}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service-layer checks
# ---------------------------------------------------------------------------


def check_find_filter(ids: dict[str, int]) -> None:
    db = SessionLocal()
    try:
        # Default: active-only, both samples and non-samples.
        rows = find_catalog_items(db, designer="Phase 6 Vendor", limit=10)
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "SAMPLE-IVORY" in skus
        assert _PREFIX + "ORDERED-CHAMPAGNE" in skus
        assert _PREFIX + "SAMPLE-RETIRED" not in skus, (
            "active_only default still surfaced an inactive row"
        )

        # is_sample=True surfaces only samples (active).
        rows = find_catalog_items(
            db, designer="Phase 6 Vendor", is_sample=True, limit=10
        )
        skus = {r.internal_sku for r in rows}
        assert skus == {_PREFIX + "SAMPLE-IVORY"}, skus

        # is_sample=False surfaces only non-samples.
        rows = find_catalog_items(
            db, designer="Phase 6 Vendor", is_sample=False, limit=10
        )
        skus = {r.internal_sku for r in rows}
        assert skus == {_PREFIX + "ORDERED-CHAMPAGNE"}, skus

        # Combined: include inactive + samples-only surfaces both
        # sample rows.
        rows = find_catalog_items(
            db,
            designer="Phase 6 Vendor",
            active_only=False,
            is_sample=True,
            limit=10,
        )
        skus = {r.internal_sku for r in rows}
        assert skus == {
            _PREFIX + "SAMPLE-IVORY",
            _PREFIX + "SAMPLE-RETIRED",
        }, skus
    finally:
        db.close()


def check_search_filter(ids: dict[str, int]) -> None:
    db = SessionLocal()
    try:
        # Search by internal_sku prefix, samples-only.
        rows = search_catalog(
            db, q=_PREFIX, is_sample=True, limit=10
        )
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "SAMPLE-IVORY" in skus
        assert _PREFIX + "ORDERED-CHAMPAGNE" not in skus
        # Inactive sample is hidden by default.
        assert _PREFIX + "SAMPLE-RETIRED" not in skus

        # is_sample=False excludes the sample row.
        rows = search_catalog(
            db, q=_PREFIX, is_sample=False, limit=10
        )
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "ORDERED-CHAMPAGNE" in skus
        assert _PREFIX + "SAMPLE-IVORY" not in skus

        # Include inactive + samples-only surfaces the retired sample.
        rows = search_catalog(
            db,
            q=_PREFIX,
            include_inactive=True,
            is_sample=True,
            limit=10,
        )
        skus = {r.internal_sku for r in rows}
        assert _PREFIX + "SAMPLE-RETIRED" in skus
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Router checks
# ---------------------------------------------------------------------------


def check_router_filter(ids: dict[str, int], headers) -> None:
    resp = client.get(
        "/api/catalog",
        headers=headers,
        params={"q": _PREFIX, "is_sample": "true", "limit": 10},
    )
    assert resp.status_code == 200, resp.text
    skus = {r["internal_sku"] for r in resp.json()}
    assert _PREFIX + "SAMPLE-IVORY" in skus
    assert _PREFIX + "ORDERED-CHAMPAGNE" not in skus

    resp = client.get(
        "/api/catalog",
        headers=headers,
        params={"q": _PREFIX, "is_sample": "false", "limit": 10},
    )
    assert resp.status_code == 200
    skus = {r["internal_sku"] for r in resp.json()}
    assert _PREFIX + "ORDERED-CHAMPAGNE" in skus
    assert _PREFIX + "SAMPLE-IVORY" not in skus


def check_admin_patch_toggle(ids: dict[str, int], headers) -> None:
    """Flip is_sample and active via admin PATCH; confirm the change
    sticks and updated_at advances."""
    cid = ids["non_sample"]
    db = SessionLocal()
    try:
        before = db.get(CatalogItem, cid)
        assert before.is_sample is False
        assert before.active is True
        before_updated = before.updated_at
    finally:
        db.close()

    # Sleep a hair so the updated_at delta is observable on fast clocks.
    time.sleep(0.01)

    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=headers,
        json={"is_sample": True, "active": False, "house_name": "Renamed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_sample"] is True
    assert body["active"] is False
    assert body["house_name"] == "Renamed"

    db = SessionLocal()
    try:
        after = db.get(CatalogItem, cid)
        assert after.is_sample is True
        assert after.active is False
        assert after.house_name == "Renamed"
        assert after.updated_at > before_updated, (
            "updated_at did not advance after PATCH"
        )
    finally:
        db.close()


def check_admin_patch_immutability(ids: dict[str, int], headers) -> None:
    """internal_sku and public_code must be rejected even with admin
    auth. Existing references on invoices/quotes/special-orders
    rely on internal_sku as the stable lookup; public_code is the
    customer-facing immutable identifier."""
    cid = ids["sample"]

    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=headers,
        json={"internal_sku": _PREFIX + "RENAMED-IVORY"},
    )
    # Pydantic's extra="forbid" intercepts unknown fields with 422.
    assert resp.status_code == 422, resp.text

    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=headers,
        json={"public_code": "BVX-99999"},
    )
    assert resp.status_code == 422, resp.text

    # Confirm the row didn't drift.
    db = SessionLocal()
    try:
        row = db.get(CatalogItem, cid)
        assert row.internal_sku == _PREFIX + "SAMPLE-IVORY"
        assert row.public_code.startswith("BVX-")
        assert row.public_code != "BVX-99999"
    finally:
        db.close()


def check_admin_patch_empty_body_noop(ids: dict[str, int], headers) -> None:
    cid = ids["sample"]
    db = SessionLocal()
    try:
        before_updated = db.get(CatalogItem, cid).updated_at
    finally:
        db.close()
    time.sleep(0.01)
    resp = client.patch(
        f"/api/catalog/{cid}", headers=headers, json={}
    )
    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        after_updated = db.get(CatalogItem, cid).updated_at
        assert after_updated == before_updated, (
            "empty PATCH bumped updated_at"
        )
    finally:
        db.close()


def check_admin_patch_image_urls_invalid(ids: dict[str, int], headers) -> None:
    cid = ids["sample"]
    # Pydantic catches the type mismatch as 422 before the service.
    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=headers,
        json={"image_urls": "not-a-list"},
    )
    assert resp.status_code == 422, resp.text


def check_admin_patch_required_fields(ids: dict[str, int], headers) -> None:
    cid = ids["sample"]
    for field_name in ("color", "category", "is_sample", "active"):
        resp = client.patch(
            f"/api/catalog/{cid}",
            headers=headers,
            json={field_name: None},
        )
        assert resp.status_code == 422, (field_name, resp.text)
        assert resp.json()["detail"]["code"] == "catalog_field_required"

    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=headers,
        json={"category": "not_a_category"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "catalog_category_invalid"


def check_non_admin_cannot_patch(ids: dict[str, int], staff_headers) -> None:
    cid = ids["sample"]
    resp = client.patch(
        f"/api/catalog/{cid}",
        headers=staff_headers,
        json={"is_sample": False},
    )
    assert resp.status_code == 403, resp.text


def check_patch_404(headers) -> None:
    resp = client.patch(
        "/api/catalog/9999999", headers=headers, json={"is_sample": True}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "catalog_item_not_found"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"using prefix {_PREFIX}")
    seq_baseline = _get_seq()
    print(f"catalog_public_code_seq baseline = {seq_baseline}")
    ids = _seed()
    print(f"seeded {len(ids)} catalog rows")
    admin_id, admin_headers = _make_user("admin")
    staff_id, staff_headers = _make_user("user")
    try:
        check_find_filter(ids)
        print("find_catalog_items is_sample filter ok")
        check_search_filter(ids)
        print("search_catalog is_sample filter ok")
        check_router_filter(ids, admin_headers)
        print("/api/catalog?is_sample router filter ok")
        check_admin_patch_toggle(ids, admin_headers)
        print("admin PATCH flips is_sample/active + bumps updated_at ok")
        check_admin_patch_immutability(ids, admin_headers)
        print("admin PATCH rejects internal_sku/public_code ok")
        check_admin_patch_empty_body_noop(ids, admin_headers)
        print("empty PATCH body is no-op ok")
        check_admin_patch_image_urls_invalid(ids, admin_headers)
        print("invalid image_urls rejected ok")
        check_admin_patch_required_fields(ids, admin_headers)
        print("required PATCH fields reject null/invalid values ok")
        check_non_admin_cannot_patch(ids, staff_headers)
        print("non-admin cannot PATCH ok")
        check_patch_404(admin_headers)
        print("PATCH 404 on missing id ok")
        print()
        print("catalog phase 6 samples + PATCH smoke ok")
        return 0
    finally:
        _wipe([admin_id, staff_id])
        _reset_seq(seq_baseline)
        print(f"cleanup done (seq reset to {seq_baseline})")


if __name__ == "__main__":
    sys.exit(main())
