"""Smoke for E1: upload magic-byte validation + forced attachment disposition.

User-specified acceptance:

  1. Oversized → 413, no row written.
  2. Wrong MIME / wrong magic for declared extension → 415.
  3. Renamed executable (MZ header) as `.pdf` → 415, even when both
     filename suffix and `Content-Type` say `application/pdf`.
  4. Valid image / PDF uploads succeed (regression).
  5. Download path always serves `Content-Disposition: attachment`,
     and the legacy `?disposition=inline` query is ignored.
  6. Business-logo upload also magic-validates: a renamed `.exe`
     posed as `image/png` is rejected.

Each upload is its own isolated event so cleanup is straightforward.
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
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please-with-pad",
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from api.server import app  # noqa: E402
from config.settings import DOCUMENT_UPLOAD_MAX_MB  # noqa: E402
from database.auth import create_access_token, hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import Contact, Event, EventDocument, User  # noqa: E402
from services import document_storage  # noqa: E402


client = TestClient(app)

_user_ids: list[int] = []
_contact_ids: list[int] = []
_event_ids: list[int] = []
_document_ids: list[int] = []


def _make_admin() -> tuple[int, dict]:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"e1-admin-{suffix}",
            email=f"e1-admin-{suffix}@example.com",
            hashed_password=hash_password("Smoke-Pass-12345!"),
            full_name="E1 Admin",
            is_active=True,
            role="admin",
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _user_ids.append(u.id)
        token = create_access_token(u)
        return u.id, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _seed_event() -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        c = Contact(
            display_name=f"E1 Customer {suffix}",
            phone_e164=f"+1210555{uuid.uuid4().int % 10_000:04d}",
            phone="(210) 555-0001",
            email=f"e1-{suffix}@example.com",
        )
        db.add(c)
        db.flush()
        _contact_ids.append(c.id)
        e = Event(
            primary_contact_id=c.id,
            event_type="quinceanera",
            event_name=f"E1 Upload {suffix}",
            event_date=date.today() + timedelta(days=200),
            status="lead",
        )
        db.add(e)
        db.flush()
        _event_ids.append(e.id)
        db.commit()
        return e.id
    finally:
        db.close()


def _cleanup() -> None:
    db = SessionLocal()
    try:
        for ext in ("png", "jpg", "jpeg", "webp", "svg"):
            document_storage.delete_object(f"business/logo.{ext}")
        db.execute(
            sql_text(
                "UPDATE business_profile SET logo_storage_key = NULL "
                "WHERE id = 1"
            )
        )
        if _document_ids:
            db.execute(
                sql_text("DELETE FROM event_documents WHERE id = ANY(:ids)"),
                {"ids": _document_ids},
            )
        if _event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM event_status_change_events WHERE event_id = ANY(:ids)"),
                {"ids": _event_ids},
            )
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _event_ids},
            )
        if _contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _contact_ids},
            )
        if _user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _user_ids},
            )
        db.commit()
    finally:
        db.close()


# Real magic-byte prefixes for the valid-upload tests.
PDF_BODY = b"%PDF-1.4\n%test body for E1 smoke\n"
PNG_BODY = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xcf\xc0P\x00\x00\x00\x03\x00\x01"
    b"^\xb1\xe5\x07\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_BODY = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + (b"\x00" * 64)
    + b"\xff\xd9"
)

# Renamed executable: real Windows PE header. .exe pretending to be .pdf.
EXE_PDF = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"


try:
    admin_id, auth = _make_admin()
    event_id = _seed_event()

    # ---------------------------------------------------------------------
    # 1. Renamed .exe with both filename and content-type claiming PDF
    #    → 415, no DB row written. The extension allowlist passes
    #    (`.pdf`), the content-type allowlist passes (`application/pdf`),
    #    so this is purely the magic-byte check doing its job.
    # ---------------------------------------------------------------------
    db = SessionLocal()
    try:
        before = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
    finally:
        db.close()
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("invoice.pdf", EXE_PDF, "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 415, resp.text
    assert resp.json()["detail"] == "unsupported_type", resp.text
    db = SessionLocal()
    try:
        after = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
        assert after == before, "renamed-exe upload left a row behind"
    finally:
        db.close()
    print("renamed exe as .pdf → 415 + no row written ok")

    # ---------------------------------------------------------------------
    # 2. PNG bytes uploaded as `.jpg` → 415. Extension+content-type both
    #    say JPEG but the magic bytes are PNG.
    # ---------------------------------------------------------------------
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("photo.jpg", PNG_BODY, "image/jpeg")},
        data={"kind": "document"},
    )
    assert resp.status_code == 415, resp.text
    print("PNG bytes posed as .jpg → 415 ok")

    # ---------------------------------------------------------------------
    # 3. Valid PDF upload happy path → 201, DB row exists.
    # ---------------------------------------------------------------------
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("contract.pdf", PDF_BODY, "application/pdf")},
        data={"kind": "document", "label": "Contract"},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    _document_ids.append(doc_id)
    print("valid PDF upload → 201 ok")

    # ---------------------------------------------------------------------
    # 4. Valid PNG upload → 201.
    # ---------------------------------------------------------------------
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("dress.png", PNG_BODY, "image/png")},
        data={"kind": "document"},
    )
    assert resp.status_code == 201, resp.text
    _document_ids.append(resp.json()["id"])
    print("valid PNG upload → 201 ok")

    # ---------------------------------------------------------------------
    # 5. Valid JPEG upload → 201.
    # ---------------------------------------------------------------------
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("photo.jpg", JPEG_BODY, "image/jpeg")},
        data={"kind": "document"},
    )
    assert resp.status_code == 201, resp.text
    _document_ids.append(resp.json()["id"])
    print("valid JPEG upload → 201 ok")

    # ---------------------------------------------------------------------
    # 6. Oversized valid PDF → 413 (size cap fires after magic-byte gate).
    # ---------------------------------------------------------------------
    too_big = b"%PDF-1.4\n" + (b"\x00" * ((DOCUMENT_UPLOAD_MAX_MB + 1) * 1024 * 1024))
    db = SessionLocal()
    try:
        before = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
    finally:
        db.close()
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("huge.pdf", too_big, "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"] == "file_too_large", resp.text
    db = SessionLocal()
    try:
        after = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
        assert after == before, "oversized upload left a row behind"
    finally:
        db.close()
    print("oversized PDF (valid magic + over cap) → 413 + no row ok")

    # ---------------------------------------------------------------------
    # 7. Download forces attachment regardless of `?disposition=inline`.
    # ---------------------------------------------------------------------
    resp = client.get(f"/api/documents/{doc_id}/download", headers=auth)
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "").lower()
    assert cd.startswith("attachment"), cd
    assert "contract.pdf" in cd, cd

    resp = client.get(
        f"/api/documents/{doc_id}/download?disposition=inline", headers=auth
    )
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "").lower()
    assert cd.startswith("attachment"), cd
    print("download forces attachment; ?disposition=inline ignored ok")

    # ---------------------------------------------------------------------
    # 8. Logo upload also magic-validates (admin-only). Renamed exe
    #    posed as PNG → 415. Real PNG → 200.
    # ---------------------------------------------------------------------
    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.png", EXE_PDF, "image/png")},
    )
    assert resp.status_code == 415, resp.text
    assert resp.json()["detail"]["code"] == "unsupported_logo_type", resp.text
    print("logo: renamed exe posed as .png → 415 ok")

    resp = client.post(
        "/api/business-profile/logo",
        headers=auth,
        files={"file": ("logo.png", PNG_BODY, "image/png")},
    )
    # 200 if upload accepted; the existing logo (if any) is overwritten
    # in place at `business/logo.png`. We don't care about the response
    # body, only that the magic gate let it through.
    assert resp.status_code == 200, resp.text
    print("logo: valid PNG → 200 ok")

finally:
    _cleanup()
    print("cleanup done")

print("\ntest_upload_validation_smoke OK")
