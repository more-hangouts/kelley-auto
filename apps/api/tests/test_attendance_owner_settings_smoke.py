"""Smoke for Phase 9 sub-slice 3: owner settings exposure + EXIF assertion.

Covers two loose ends from earlier slices:

  - The four owner attendance/selfie/biweekly settings round-trip
    through the BusinessProfile API and the GET response now exposes
    them so the BusinessProfile.jsx form can render the current
    values. Patch path was already wired in Phase 7 Slice 2 and
    Phase 9 sub-slice 1 Priority 2; the gap was the response schema.
  - The selfie ingestion pipeline strips GPS EXIF from a JPEG
    (`services.clock_selfie._normalize_to_webp` doesn't pass `exif=`
    to Pillow's WebP encoder, so EXIF should be absent from the
    output). The Phase 9 doc lists this as a confirm-not-implement
    item; the smoke is the confirmation.

Mutates `business_profile` so it must run serially with other
attendance smokes per the project rule on shared singleton state.
Captures + restores the prior values in cleanup.
"""

import io
import os
import sys
import uuid
from datetime import date
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
from PIL import Image  # noqa: E402
from PIL.ExifTags import TAGS  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import BusinessProfile, User  # noqa: E402
from services.clock_selfie import validate_selfie_bytes  # noqa: E402

client = TestClient(app)

_user_ids: list[int] = []
_snapshot_loaded = False
_snapshot: dict = {}


def _make_admin() -> tuple[int, dict]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"admin-p9own-{suffix}",
            email=f"admin-p9own-{suffix}@example.com",
            hashed_password=hash_password("not-the-pin"),
            full_name="P9own Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        return u.id, {"Authorization": f"Bearer {create_access_token(u)}"}
    finally:
        db.close()


def _capture_snapshot() -> None:
    global _snapshot_loaded, _snapshot
    if _snapshot_loaded:
        return
    db = SessionLocal()
    try:
        profile = db.query(BusinessProfile).first()
        if profile is None:
            return
        _snapshot = {
            "attendance_gate_enabled": profile.attendance_gate_enabled,
            "selfie_policy": profile.selfie_policy,
            "selfie_retention_days": profile.selfie_retention_days,
            "biweekly_anchor_date": profile.biweekly_anchor_date,
        }
        _snapshot_loaded = True
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        if _snapshot_loaded:
            profile = db.query(BusinessProfile).first()
            if profile is not None:
                profile.attendance_gate_enabled = _snapshot.get(
                    "attendance_gate_enabled", True
                )
                profile.selfie_policy = _snapshot.get("selfie_policy", "optional")
                profile.selfie_retention_days = _snapshot.get(
                    "selfie_retention_days"
                )
                profile.biweekly_anchor_date = _snapshot.get(
                    "biweekly_anchor_date"
                )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


def _make_jpeg_with_exif() -> bytes:
    """Return a small JPEG carrying an EXIF block with both general
    metadata (Make/Model) and a GPS reference. We deliberately avoid
    GPS coordinate rationals because their tuple format varies across
    Pillow versions; a string GPS reference is enough evidence that
    a GPS IFD round-trips through JPEG encode."""
    img = Image.new("RGB", (200, 200), color=(120, 60, 60))
    exif = img.getexif()
    # Top-level EXIF tags (text — robust across Pillow versions).
    exif[271] = "TestPhone"  # Make
    exif[272] = "ModelXYZ"  # Model
    exif[306] = "2026:05:09 12:00:00"  # DateTime
    # GPS IFD: a single string field (LatitudeRef) is enough to make
    # the GPS block non-empty without touching rational arithmetic.
    gps_ifd = exif.get_ifd(0x8825)
    gps_ifd[1] = "N"
    gps_ifd[3] = "W"
    exif[0x8825] = gps_ifd
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _exif_summary(image_bytes: bytes) -> tuple[bool, dict]:
    """Decode `image_bytes` and return (has_exif, dump). `has_exif` is
    True if any EXIF top-level tag OR any GPS-IFD field is present."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        exif = img.getexif()
        if not exif:
            return False, {}
        top = {TAGS.get(k, k): v for k, v in exif.items()}
        gps_block = exif.get_ifd(0x8825) if 0x8825 in exif else {}
        return (bool(top) or bool(gps_block)), {
            "top": top,
            "gps_keys": list(gps_block.keys()) if gps_block else [],
        }


def main() -> None:
    _capture_snapshot()
    _admin_id, admin_headers = _make_admin()

    # ---- 1. EXIF strip: input JPEG has Make/Model + GPS-IFD, output
    #         WebP must have neither. ----
    jpeg = _make_jpeg_with_exif()
    has_in, info_in = _exif_summary(jpeg)
    assert has_in, (
        f"setup failure — synthetic JPEG should have EXIF, got {info_in!r}"
    )
    assert info_in["gps_keys"], (
        f"setup failure — synthetic JPEG should have GPS IFD, got {info_in!r}"
    )

    webp = validate_selfie_bytes(raw_bytes=jpeg, declared_mime="image/jpeg")
    has_out, info_out = _exif_summary(webp)
    assert not has_out, (
        f"selfie pipeline leaked EXIF into output WebP: {info_out!r}"
    )

    # The output should also be a valid WebP image.
    with Image.open(io.BytesIO(webp)) as out_img:
        assert out_img.format == "WEBP", out_img.format
        assert out_img.size[0] <= 1024 and out_img.size[1] <= 1024, out_img.size

    # ---- 2. GET /api/business-profile now exposes the four settings. ----
    resp = client.get("/api/business-profile", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "attendance_gate_enabled",
        "selfie_policy",
        "selfie_retention_days",
        "biweekly_anchor_date",
    ):
        assert key in body, f"GET response missing {key}"

    # ---- 3. PATCH round-trip on each of the four fields. ----
    new_anchor = "2026-01-05"
    resp = client.patch(
        "/api/business-profile",
        headers=admin_headers,
        json={
            "attendance_gate_enabled": False,
            "selfie_policy": "required",
            "selfie_retention_days": 90,
            "biweekly_anchor_date": new_anchor,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attendance_gate_enabled"] is False
    assert body["selfie_policy"] == "required"
    assert body["selfie_retention_days"] == 90
    assert body["biweekly_anchor_date"] == new_anchor

    # Postgres-side confirmation per the project rule on real-INSERT
    # validation.
    db = SessionLocal()
    try:
        row = db.query(BusinessProfile).first()
        assert row.biweekly_anchor_date == date.fromisoformat(new_anchor)
        assert row.selfie_retention_days == 90
        assert row.selfie_policy == "required"
        assert row.attendance_gate_enabled is False
    finally:
        db.close()

    # ---- 4. Explicit null clears the anchor and retention. ----
    resp = client.patch(
        "/api/business-profile",
        headers=admin_headers,
        json={
            "biweekly_anchor_date": None,
            "selfie_retention_days": None,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["biweekly_anchor_date"] is None
    assert body["selfie_retention_days"] is None

    db = SessionLocal()
    try:
        row = db.query(BusinessProfile).first()
        assert row.biweekly_anchor_date is None
        assert row.selfie_retention_days is None
    finally:
        db.close()

    # ---- 5. Invalid date is 422 from Pydantic before reaching the service. ----
    resp = client.patch(
        "/api/business-profile",
        headers=admin_headers,
        json={"biweekly_anchor_date": "not-a-date"},
    )
    assert resp.status_code == 422, resp.text

    # ---- 6. Out-of-range retention is 422. ----
    resp = client.patch(
        "/api/business-profile",
        headers=admin_headers,
        json={"selfie_retention_days": 0},
    )
    assert resp.status_code == 422, resp.text

    print("phase9 owner_settings smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
