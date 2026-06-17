"""Smoke tests for the event-documents surface.

Mints its own ephemeral admin user, contact, and event, drops a tempdir over
DOCUMENT_STORAGE_ROOT so we never touch the real upload tree, then exercises
upload, list, download, patch, delete, and the various rejection paths.
Cleans up everything (rows + tempdir) on exit.
"""

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# CRITICAL: env overrides must happen before anything imports config.settings.
# DOCUMENT_STORAGE_ROOT and DOCUMENT_UPLOAD_MAX_MB get frozen at import time.
_TMP_STORAGE = tempfile.mkdtemp(prefix="event-docs-smoke-")
os.environ["DOCUMENT_STORAGE_ROOT"] = _TMP_STORAGE
os.environ.setdefault("DOCUMENT_UPLOAD_MAX_MB", "25")

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
from database.models import Contact, Event, EventDocument, User  # noqa: E402


client = TestClient(app)


def _make_user(role="admin"):
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"docs-smoke-{role}-{suffix}",
            email=f"docs-smoke-{role}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Docs Smoke {role.title()}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.email
    finally:
        db.close()


def _make_admin():
    return _make_user("admin")


def _seed_event():
    db = SessionLocal()
    try:
        contact = Contact(
            display_name="Docs Smoke Contact",
            phone="(210) 555-9999",
        )
        db.add(contact)
        db.flush()
        event = Event(
            primary_contact_id=contact.id,
            event_type="quinceanera",
            event_name="Docs Smoke Event",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return contact.id, event.id
    finally:
        db.close()


def _cleanup(contact_id, event_id, user_ids):
    db = SessionLocal()
    try:
        if event_id is not None:
            db.execute(
                sql_text("DELETE FROM event_documents WHERE event_id = :eid"),
                {"eid": event_id},
            )
            # Phase 4b: the smoke now creates a canonical invoice on the
            # event. Tear down the invoice tree before deleting the event
            # row so the FK constraint doesn't fire.
            db.execute(
                sql_text(
                    "DELETE FROM invoice_invitations WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = :eid)"
                ),
                {"eid": event_id},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_installments WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = :eid)"
                ),
                {"eid": event_id},
            )
            db.execute(
                sql_text(
                    "DELETE FROM invoice_line_items WHERE invoice_id IN "
                    "(SELECT id FROM invoices WHERE event_id = :eid)"
                ),
                {"eid": event_id},
            )
            db.execute(
                sql_text("DELETE FROM invoices WHERE event_id = :eid"),
                {"eid": event_id},
            )
            db.execute(sql_text("DELETE FROM events WHERE id = :eid"), {"eid": event_id})
        if contact_id is not None:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = :cid"), {"cid": contact_id}
            )
        for uid in user_ids:
            if uid is not None:
                db.execute(sql_text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Auth required (sample one route per surface)
# ---------------------------------------------------------------------------

resp = client.get("/api/events/1/documents")
assert resp.status_code == 401, f"events list expected 401, got {resp.status_code}: {resp.text}"
resp = client.get("/api/documents/1/download")
assert resp.status_code == 401, f"download expected 401, got {resp.status_code}: {resp.text}"
resp = client.delete("/api/documents/1")
assert resp.status_code == 401, f"delete expected 401, got {resp.status_code}: {resp.text}"
print("auth required ok")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

user_id: int | None = None
staff_a_id: int | None = None
staff_b_id: int | None = None
contact_id = None
event_id = None

try:
    # Seed inside the try so that a mid-seed exception (e.g. _make_user
    # raising after the row INSERT commits but before it returns) still
    # leaves the finally to clean up whatever IDs we did capture.
    user_id, user_email = _make_admin()
    staff_a_id, staff_a_email = _make_user("user")
    staff_b_id, staff_b_email = _make_user("user")

    resp = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    print("admin login ok")

    contact_id, event_id = _seed_event()
    print(f"seeded contact={contact_id} event={event_id}")

    # ----- counts on empty event are zero -----
    resp = client.get(f"/api/events/{event_id}/document-counts", headers=auth)
    assert resp.status_code == 200, resp.text
    counts = resp.json()
    assert counts == {
        "document": 0,
        "external_invoice": 0,
        "outstanding_invoices": 0,
    }, counts
    print("counts empty ok")

    # ----- list on empty event -----
    resp = client.get(f"/api/events/{event_id}/documents", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["documents"] == [], resp.json()
    print("empty list ok")

    # ----- upload to unknown event -----
    resp = client.post(
        "/api/events/999999999/documents",
        headers=auth,
        files={"file": ("x.pdf", b"%PDF-1.4 short", "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 404, resp.text
    print("upload to unknown event 404 ok")

    # ----- reject unsupported extension -----
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        data={"kind": "document"},
    )
    assert resp.status_code == 415, resp.text
    print("reject .exe ok")

    # ----- reject mismatched content type -----
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("x.pdf", b"%PDF", "text/plain")},
        data={"kind": "document"},
    )
    assert resp.status_code == 415, resp.text
    print("reject mismatched content-type ok")

    # ----- happy path: upload a small PDF -----
    pdf_bytes = b"%PDF-1.4 minimal smoke test body\n"
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("contract.pdf", pdf_bytes, "application/pdf")},
        data={"kind": "document", "label": "Signed contract"},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["kind"] == "document"
    assert doc["filename"] == "contract.pdf"
    assert doc["byte_size"] == len(pdf_bytes), doc
    assert doc["label"] == "Signed contract"
    assert doc["storage_key"].startswith(f"events/{event_id}/{doc['id']}/")
    document_id = doc["id"]
    on_disk = Path(_TMP_STORAGE) / doc["storage_key"]
    assert on_disk.is_file(), on_disk
    assert on_disk.read_bytes() == pdf_bytes
    print(f"upload pdf ok (id={document_id})")

    # ----- list shows it -----
    resp = client.get(f"/api/events/{event_id}/documents", headers=auth)
    assert resp.status_code == 200, resp.text
    docs = resp.json()["documents"]
    assert len(docs) == 1 and docs[0]["id"] == document_id
    print("list shows document ok")

    # ----- download returns the bytes -----
    resp = client.get(f"/api/documents/{document_id}/download", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.content == pdf_bytes, len(resp.content)
    cd = resp.headers.get("content-disposition", "")
    assert "contract.pdf" in cd, cd
    assert cd.lower().startswith("attachment"), cd
    print("download ok")

    # ----- E1: disposition is always attachment now -----
    # The pre-E1 affordance to override with `?disposition=inline` is
    # gone; any caller that passes the query param gets the unchanged
    # attachment response (the param is no longer declared, so FastAPI
    # silently ignores it on a GET — no 422 here, just a 200 attachment).
    resp = client.get(
        f"/api/documents/{document_id}/download?disposition=inline", headers=auth
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == pdf_bytes
    cd = resp.headers.get("content-disposition", "")
    assert cd.lower().startswith("attachment"), cd
    assert "contract.pdf" in cd, cd
    print("inline override ignored; still attachment ok")

    # ----- patch label on a document -----
    resp = client.patch(
        f"/api/documents/{document_id}",
        headers=auth,
        json={"label": "Renamed contract"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "Renamed contract"
    print("patch label ok")

    # ----- clearing label: empty string and explicit null both clear; missing
    # key leaves the value alone. The Phase 3 rename UI relies on this.
    resp = client.patch(
        f"/api/documents/{document_id}", headers=auth, json={"label": ""}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] is None, resp.json()
    resp = client.patch(
        f"/api/documents/{document_id}", headers=auth, json={"label": "Set again"}
    )
    assert resp.json()["label"] == "Set again"
    resp = client.patch(
        f"/api/documents/{document_id}", headers=auth, json={"label": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] is None, resp.json()
    resp = client.patch(
        f"/api/documents/{document_id}", headers=auth, json={"label": "Sticky"}
    )
    assert resp.json()["label"] == "Sticky"
    resp = client.patch(f"/api/documents/{document_id}", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "Sticky", resp.json()
    print("clear label ok")

    # ----- Phase 4b: legacy kind='invoice' uploads are retired -----
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("legacy-inv.pdf", b"%PDF-legacy", "application/pdf")},
        data={"kind": "invoice"},
    )
    assert resp.status_code == 422, resp.text
    print("legacy kind='invoice' upload rejected (4b) ok")

    # ----- Phase 4b: PATCH writing invoice_* fields returns 422 -----
    resp = client.patch(
        f"/api/documents/{document_id}",
        headers=auth,
        json={"invoice_amount_cents": 1000},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("detail") == "invoice_fields_retired", resp.text
    print("PATCH invoice_* on any kind 422 invoice_fields_retired (4b) ok")

    # ----- Phase 4b: create a canonical invoice on the event so kanban + counts
    # have something outstanding to surface. The smoke uses the service layer
    # directly to avoid re-paving the canonical-invoice routes which already
    # have their own smoke (`tests/test_invoices_smoke.py`).
    from datetime import date as _date, timedelta as _timedelta  # noqa: E402

    from services.invoice_service import (  # noqa: E402
        InstallmentInput,
        LineItemInput,
        create_invoice,
        mark_sent,
    )

    db = SessionLocal()
    try:
        canonical = create_invoice(
            db,
            event_id=event_id,
            contact_id=contact_id,
            actor_user_id=user_id,
            issue_date=_date.today(),
            line_items=[
                LineItemInput(
                    kind="service",
                    description="Phase 4b smoke invoice",
                    quantity=1,
                    unit_price_cents=125000,
                ),
            ],
            installments=[
                InstallmentInput(
                    label="Balance",
                    amount_cents=125000,
                    due_date=_date.today() + _timedelta(days=30),
                ),
            ],
        )
        canonical_id = canonical.id
        mark_sent(db, invoice_id=canonical_id, actor_user_id=user_id)
        db.commit()
    finally:
        db.close()
    print(f"canonical invoice created + sent (id={canonical_id})")

    # ----- Phase 4b: counts surface external_invoice + outstanding from the
    # canonical invoices table.
    # Upload an external_invoice attachment first so the new count is non-zero.
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("vendor.pdf", b"%PDF-vendor", "application/pdf")},
        data={"kind": "external_invoice", "linked_invoice_id": str(canonical_id)},
    )
    assert resp.status_code == 201, resp.text
    ext_doc = resp.json()
    assert ext_doc["kind"] == "external_invoice"
    assert ext_doc["linked_invoice_id"] == canonical_id
    ext_doc_id = ext_doc["id"]
    print(f"external_invoice upload + link ok (doc_id={ext_doc_id})")

    resp = client.get(f"/api/events/{event_id}/document-counts", headers=auth)
    assert resp.status_code == 200, resp.text
    counts = resp.json()
    assert counts == {
        "document": 1,
        "external_invoice": 1,
        "outstanding_invoices": 1,
    }, counts
    print("counts reflect external_invoice + canonical outstanding ok")

    # ----- Phase 4b: board surfaces has_outstanding_invoice from canonical.
    resp = client.get("/api/events/board", headers=auth)
    assert resp.status_code == 200, resp.text
    board = resp.json()
    found = False
    for col in board["columns"]:
        for card in col["cards"]:
            if card["id"] == event_id:
                assert card["has_outstanding_invoice"] is True, card
                found = True
                break
    assert found, "seeded event missing from board"
    print("board has_outstanding_invoice (canonical-sourced) ok")

    # ----- Phase 4b: linked_invoice_id rejection paths.
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("oops.pdf", b"%PDF", "application/pdf")},
        data={"kind": "document", "linked_invoice_id": str(canonical_id)},
    )
    assert resp.status_code == 422, resp.text
    assert (
        resp.json().get("detail")
        == "linked_invoice_id_only_on_external_invoice"
    ), resp.text
    print("linked_invoice_id on document kind rejected ok")

    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("ghost.pdf", b"%PDF", "application/pdf")},
        data={"kind": "external_invoice", "linked_invoice_id": "9999999"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("detail") == "linked_invoice_id_not_on_event", resp.text
    print("linked_invoice_id pointing off-event rejected ok")

    # ----- Cancel canonical invoice; outstanding signals fall to false.
    db = SessionLocal()
    try:
        from services.invoice_service import cancel_invoice  # noqa: E402

        cancel_invoice(db, invoice_id=canonical_id, actor_user_id=user_id)
        db.commit()
    finally:
        db.close()
    counts = client.get(
        f"/api/events/{event_id}/document-counts", headers=auth
    ).json()
    assert counts["outstanding_invoices"] == 0, counts
    board = client.get("/api/events/board", headers=auth).json()
    for col in board["columns"]:
        for card in col["cards"]:
            if card["id"] == event_id:
                assert card["has_outstanding_invoice"] is False, card
    print("cancelled canonical invoice clears outstanding signals ok")

    # ----- list filtered by kind -----
    resp = client.get(
        f"/api/events/{event_id}/documents?kind=document", headers=auth
    )
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    assert len(docs) == 1 and docs[0]["id"] == document_id, docs
    resp = client.get(
        f"/api/events/{event_id}/documents?kind=external_invoice", headers=auth
    )
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    assert len(docs) == 1 and docs[0]["id"] == ext_doc_id, docs
    print("kind filter (document + external_invoice) ok")

    # ----- delete authorization: only uploader or admin -----
    # Log in as staff_a, upload a doc, confirm staff_b can't delete it but
    # staff_a (uploader) and admin both can.
    resp = client.post(
        "/api/auth/login",
        json={"email": staff_a_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200
    auth_a = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = client.post(
        "/api/auth/login",
        json={"email": staff_b_email, "password": "smoke-pass-12345"},
    )
    assert resp.status_code == 200
    auth_b = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth_a,
        files={"file": ("a.pdf", b"%PDF-staff-a", "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 201, resp.text
    a_doc_id = resp.json()["id"]

    resp = client.delete(f"/api/documents/{a_doc_id}", headers=auth_b)
    assert resp.status_code == 403, resp.text
    assert resp.json().get("detail") == "delete_forbidden"
    resp = client.delete(f"/api/documents/{a_doc_id}", headers=auth_a)
    assert resp.status_code == 204, resp.text
    print("delete authorization ok")

    # Admin can delete anyone's upload.
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth_a,
        files={"file": ("a2.pdf", b"%PDF-staff-a-2", "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 201
    a2_doc_id = resp.json()["id"]
    resp = client.delete(f"/api/documents/{a2_doc_id}", headers=auth)
    assert resp.status_code == 204, resp.text
    print("admin override delete ok")

    # ----- soft delete -----
    resp = client.delete(f"/api/documents/{document_id}", headers=auth)
    assert resp.status_code == 204, resp.text
    resp = client.get(f"/api/events/{event_id}/documents", headers=auth)
    assert resp.status_code == 200
    ids = [d["id"] for d in resp.json()["documents"]]
    assert document_id not in ids, ids
    resp = client.get(f"/api/documents/{document_id}/download", headers=auth)
    assert resp.status_code == 404, resp.text
    print("soft delete ok")

    # ----- size limit: exceed 25 MB and confirm 413 -----
    db = SessionLocal()
    try:
        before = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
    finally:
        db.close()
    # E1: the upload route now magic-byte-validates the leading chunk
    # before the size guard ever fires. Use a real PDF header followed
    # by filler so the size check is the one we actually exercise.
    too_big = b"%PDF-1.4\n" + (b"\x00" * (26 * 1024 * 1024))
    resp = client.post(
        f"/api/events/{event_id}/documents",
        headers=auth,
        files={"file": ("big.pdf", too_big, "application/pdf")},
        data={"kind": "document"},
    )
    assert resp.status_code == 413, f"expected 413, got {resp.status_code}"
    # Row count unchanged confirms the partial upload rolled back cleanly.
    db = SessionLocal()
    try:
        after = (
            db.query(EventDocument)
            .filter(EventDocument.event_id == event_id)
            .count()
        )
        assert after == before, (before, after)
    finally:
        db.close()
    print("size limit + rollback ok")

    # ----- path traversal rejected at storage layer -----
    from services import document_storage

    try:
        document_storage.resolve_path("../etc/passwd")
        print("FAIL: traversal accepted")
        sys.exit(1)
    except ValueError:
        pass
    try:
        document_storage.resolve_path("/etc/passwd")
        print("FAIL: absolute path accepted")
        sys.exit(1)
    except ValueError:
        pass
    print("path traversal rejected ok")

finally:
    _cleanup(contact_id, event_id, [user_id, staff_a_id, staff_b_id])
    shutil.rmtree(_TMP_STORAGE, ignore_errors=True)
    print("cleanup done")

print("\nevent documents smoke ok")
