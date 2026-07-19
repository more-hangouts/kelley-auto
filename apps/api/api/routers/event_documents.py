"""Event document uploads.

Two routers because the routes split across `/api/events/{id}/documents` and
`/api/documents/{id}/...`. server.py mounts each at the matching prefix.

Every route requires `get_current_user`; downloads are NOT served via a public
URL because v1 storage is local disk and a leaked link would be unauthenticated
direct file access.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from config.settings import DOCUMENT_UPLOAD_MAX_MB
from database.auth import require_any_scope
from services.attendance_gate import require_floor_access
from database.connection import get_db
from database.models import Event, EventDocument, Invoice, User
from services import document_storage
from services.upload_validation import (
    HEAD_BYTES_NEEDED,
    UploadValidationError,
    validate_magic_bytes,
)

log = logging.getLogger(__name__)

# Reject uploads when free space is below this many MB. Picked as a small
# multiple of the upload cap so a single in-flight upload can't push the disk
# over the edge. Disk-space guard logs a warning at 80% usage; this is the
# hard stop.
_DISK_FREE_FLOOR_MB = max(64, DOCUMENT_UPLOAD_MAX_MB * 2)
_DISK_FREE_FLOOR_BYTES = _DISK_FREE_FLOOR_MB * 1024 * 1024
_DISK_USAGE_WARN_PCT = 80

# Both routers stay in this file; server.py mounts them with the right prefix.
event_documents_router = APIRouter()
documents_router = APIRouter()


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

# Extension -> acceptable content-types. Browsers occasionally send
# `application/octet-stream` for HEIC; we still accept the upload if the
# extension matches and the content-type is in the broader allowlist.
_ALLOWED: dict[str, set[str]] = {
    "pdf": {"application/pdf"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "heic": {"image/heic", "image/heif", "application/octet-stream"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
}

_MAX_BYTES = DOCUMENT_UPLOAD_MAX_MB * 1024 * 1024
# Phase 4b retired `invoice` as an upload kind. The legacy column survives on
# pre-existing rows (and the GET routes still surface it for read), but new
# uploads must declare `document` or `external_invoice`. The `external_invoice`
# kind represents a vendor's PDF or an alterations subcontractor's bill;
# uploaders may optionally link it to a canonical invoice via `linked_invoice_id`.
_DocumentKind = Literal["document", "external_invoice"]
_InvoiceStatus = Literal["draft", "sent", "paid", "void"]


def _extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DocumentResponse(BaseModel):
    id: int
    event_id: int
    uploaded_by_user_id: int | None
    kind: str
    filename: str
    content_type: str
    byte_size: int
    storage_key: str
    label: str | None
    created_at: datetime
    updated_at: datetime

    # Phase 4b: legacy invoice_* columns are read-only — populated on
    # pre-Phase-4b 'invoice' rows (now retagged to 'external_invoice' by
    # migration 026) and on any 'external_invoice' carried across the
    # rollback season. Writes through the PATCH endpoint are rejected.
    invoice_amount_cents: int | None
    invoice_status: str | None
    invoice_issued_at: datetime | None
    invoice_paid_at: datetime | None
    # Phase 4b: pointer back to the canonical invoices.id row, populated
    # for migrated legacy rows or for fresh external_invoice uploads that
    # opted to attach to a specific invoice.
    linked_invoice_id: int | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]


class DocumentCountsResponse(BaseModel):
    document: int
    # Phase 4b: `invoice` removed (legacy uploader retired). `external_invoice`
    # is the new attachment count. `outstanding_invoices` now reads from the
    # canonical `invoices` table — counts rows in 'sent' or 'partial' status.
    external_invoice: int
    outstanding_invoices: int


class DocumentPatch(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    # Phase 4b removed the invoice_* mutation paths from this PATCH. Money
    # state lives on the canonical invoices table now; PATCHes that touch
    # those columns return 422 invoice_fields_retired (handled in the route
    # body so the error has a stable shape).
    invoice_amount_cents: int | None = Field(default=None, ge=0)
    invoice_status: _InvoiceStatus | None = None
    invoice_issued_at: datetime | None = None
    invoice_paid_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(d: EventDocument) -> DocumentResponse:
    return DocumentResponse(
        id=d.id,
        event_id=d.event_id,
        uploaded_by_user_id=d.uploaded_by_user_id,
        kind=d.kind,
        filename=d.filename,
        content_type=d.content_type,
        byte_size=d.byte_size,
        storage_key=d.storage_key,
        label=d.label,
        created_at=d.created_at,
        updated_at=d.updated_at,
        invoice_amount_cents=d.invoice_amount_cents,
        invoice_status=d.invoice_status,
        invoice_issued_at=d.invoice_issued_at,
        invoice_paid_at=d.invoice_paid_at,
        linked_invoice_id=d.linked_invoice_id,
    )


def _get_document_or_404(db: Session, document_id: int) -> EventDocument:
    doc = db.get(EventDocument, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="document_not_found")
    return doc


def _can_delete(doc: EventDocument, user: User) -> bool:
    """Phase 5 permission: the original uploader can always delete; admins can
    delete anything. Other users see a disabled control with a tooltip in the
    UI; the server enforces the rule independently."""
    if user.role == "admin":
        return True
    return doc.uploaded_by_user_id == user.id


def _check_disk_space() -> None:
    """Log a warning over 80% usage; raise 507 if free space is below the
    safety floor. Ordering matters: the warn-log is informational, the raise
    is the hard stop."""
    usage = document_storage.disk_usage()
    pct_used = (usage.used / usage.total) * 100 if usage.total else 0
    if pct_used >= _DISK_USAGE_WARN_PCT:
        log.warning(
            "document_storage.high_usage",
            extra={
                "pct_used": round(pct_used, 1),
                "free_mb": usage.free // (1024 * 1024),
                "total_mb": usage.total // (1024 * 1024),
            },
        )
    if usage.free < _DISK_FREE_FLOOR_BYTES:
        log.error(
            "document_storage.insufficient_space",
            extra={
                "free_mb": usage.free // (1024 * 1024),
                "floor_mb": _DISK_FREE_FLOOR_MB,
            },
        )
        raise HTTPException(status_code=507, detail="insufficient_storage")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@event_documents_router.post(
    "/{event_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
    file: Annotated[UploadFile, File(...)],
    kind: Annotated[_DocumentKind, Form(...)],
    label: Annotated[str | None, Form()] = None,
    linked_invoice_id: Annotated[int | None, Form()] = None,
) -> DocumentResponse:
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="event_not_found")

    raw_name = (file.filename or "").strip() or "upload"
    ext = _extension(raw_name)
    if ext not in _ALLOWED:
        raise HTTPException(status_code=415, detail="unsupported_type")
    content_type = (file.content_type or "application/octet-stream").lower()
    if content_type not in _ALLOWED[ext]:
        raise HTTPException(status_code=415, detail="unsupported_type")

    # Phase 4b: linked_invoice_id is only meaningful for external_invoice
    # uploads. Reject mismatched combinations early so the row never reaches
    # the CHECK constraint (cleaner error than a 500).
    if linked_invoice_id is not None:
        if kind != "external_invoice":
            raise HTTPException(
                status_code=422,
                detail="linked_invoice_id_only_on_external_invoice",
            )
        target = db.get(Invoice, linked_invoice_id)
        if target is None or target.event_id != event_id:
            raise HTTPException(
                status_code=422, detail="linked_invoice_id_not_on_event"
            )

    _check_disk_space()

    # Insert the row first so we have a real id for the storage key. Flush to
    # populate the id without committing — the upload may still fail.
    doc = EventDocument(
        event_id=event_id,
        uploaded_by_user_id=user.id,
        kind=kind,
        filename=raw_name[:500],
        content_type=content_type[:150],
        byte_size=0,
        storage_key="",
        label=(label or None),
        linked_invoice_id=linked_invoice_id,
    )
    db.add(doc)
    db.flush()

    storage_key = document_storage.build_key(event_id, doc.id, raw_name)
    path = document_storage.resolve_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    aborted = False
    head_buffer = bytearray()
    magic_validated = False
    try:
        with path.open("wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_BYTES:
                    aborted = True
                    break
                # E1 magic-byte gate: accumulate the leading bytes from the
                # incoming stream and validate as soon as we have enough.
                # Reject BEFORE writing them to disk so a renamed executable
                # never lands on the filesystem (not even briefly under the
                # storage_key for this event/doc id).
                if not magic_validated:
                    head_buffer.extend(chunk)
                    if len(head_buffer) >= HEAD_BYTES_NEEDED:
                        try:
                            validate_magic_bytes(
                                declared_ext=ext, head=bytes(head_buffer[:HEAD_BYTES_NEEDED])
                            )
                        except UploadValidationError as exc:
                            document_storage.delete_object(storage_key)
                            db.rollback()
                            raise HTTPException(
                                status_code=exc.status, detail=exc.code
                            ) from exc
                        magic_validated = True
                out.write(chunk)
        # If the upload finished without accumulating HEAD_BYTES_NEEDED, the
        # file was tiny — still validate against whatever we have. An empty
        # body or stub too short to identify is rejected as a 415, matching
        # the wider unsupported_type response shape.
        if not magic_validated:
            try:
                validate_magic_bytes(declared_ext=ext, head=bytes(head_buffer))
            except UploadValidationError as exc:
                document_storage.delete_object(storage_key)
                db.rollback()
                raise HTTPException(
                    status_code=exc.status, detail=exc.code
                ) from exc
    except Exception:
        document_storage.delete_object(storage_key)
        db.rollback()
        raise

    if aborted:
        document_storage.delete_object(storage_key)
        db.rollback()
        raise HTTPException(status_code=413, detail="file_too_large")

    doc.byte_size = written
    doc.storage_key = storage_key
    db.commit()
    db.refresh(doc)
    log.info(
        "event_document.upload",
        extra={
            "user_id": user.id,
            "event_id": event_id,
            "document_id": doc.id,
            "kind": doc.kind,
            "byte_size": doc.byte_size,
        },
    )
    return _to_response(doc)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@event_documents_router.get(
    "/{event_id}/document-counts",
    response_model=DocumentCountsResponse,
)
def document_counts(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> DocumentCountsResponse:
    """Tab-badge counts. Single aggregate query so the layout fetches once."""
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="event_not_found")

    # Phase 4b: counts are sourced from two tables now. Document and
    # external_invoice attachment counts come from event_documents (one
    # query); the outstanding-canonical-invoices count comes from invoices
    # (a second query). Two roundtrips beats a join when neither row count
    # is large per event.
    doc_row = db.execute(
        EventDocument.__table__.select()
        .with_only_columns(
            func.count(case((EventDocument.kind == "document", 1))).label("doc_count"),
            func.count(
                case((EventDocument.kind == "external_invoice", 1))
            ).label("ext_inv_count"),
        )
        .where(EventDocument.event_id == event_id)
        .where(EventDocument.deleted_at.is_(None))
    ).one()

    outstanding = db.execute(
        select(func.count(Invoice.id))
        .where(Invoice.event_id == event_id)
        .where(Invoice.deleted_at.is_(None))
        .where(Invoice.status.in_(("sent", "partial")))
    ).scalar() or 0

    return DocumentCountsResponse(
        document=doc_row.doc_count,
        external_invoice=doc_row.ext_inv_count,
        outstanding_invoices=int(outstanding),
    )


@event_documents_router.get(
    "/{event_id}/documents",
    response_model=DocumentListResponse,
)
def list_documents(
    event_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    kind: _DocumentKind | None = Query(default=None),
) -> DocumentListResponse:
    event = db.get(Event, event_id)
    if event is None or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="event_not_found")

    q = (
        db.query(EventDocument)
        .filter(EventDocument.event_id == event_id)
        .filter(EventDocument.deleted_at.is_(None))
    )
    if kind is not None:
        q = q.filter(EventDocument.kind == kind)
    rows = q.order_by(EventDocument.created_at.desc()).all()
    return DocumentListResponse(documents=[_to_response(d) for d in rows])


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@documents_router.get("/{document_id}/download")
def download_document(
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> FileResponse:
    """E1: always `Content-Disposition: attachment` for user-uploaded
    event documents. The previous `disposition` query param allowed
    callers to opt into inline rendering, which combined with our
    `content_type` allowlist (e.g. `image/svg+xml` via HEIC's
    `application/octet-stream` fallback, or a renamed `.html` masquerading
    as something else) gave an attacker a path to script execution
    against an authenticated admin. Hard-setting attachment forces the
    browser to download instead of render. PDF preview UX, if ever
    needed, lives behind a server-rendered preview, not raw inline."""
    doc = _get_document_or_404(db, document_id)
    try:
        path = document_storage.resolve_path(doc.storage_key)
    except ValueError:
        raise HTTPException(status_code=500, detail="invalid_storage_key")
    if not path.is_file():
        raise HTTPException(status_code=410, detail="file_missing")
    return FileResponse(
        path,
        media_type=doc.content_type,
        filename=doc.filename,
        content_disposition_type="attachment",
    )


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@documents_router.patch("/{document_id}", response_model=DocumentResponse)
def patch_document(
    document_id: int,
    payload: DocumentPatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> DocumentResponse:
    doc = _get_document_or_404(db, document_id)

    # Phase 4b: invoice_* fields became read-only. The columns survive on the
    # row for one-season rollback safety, but money state lives on the
    # canonical invoices table now. Reject any PATCH that tries to write
    # them so callers don't silently keep editing the legacy shadow.
    if any(
        v is not None
        for v in (
            payload.invoice_amount_cents,
            payload.invoice_status,
            payload.invoice_issued_at,
            payload.invoice_paid_at,
        )
    ):
        raise HTTPException(status_code=422, detail="invoice_fields_retired")

    # `label` uses model_fields_set so the client can clear it by sending
    # `{"label": null}` or `{"label": ""}`. Omitting the key leaves it alone.
    if "label" in payload.model_fields_set:
        doc.label = payload.label if payload.label else None

    doc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(doc)
    log.info(
        "event_document.patch",
        extra={
            "user_id": user.id,
            "event_id": doc.event_id,
            "document_id": doc.id,
            "kind": doc.kind,
            "fields": sorted(payload.model_fields_set),
        },
    )
    return _to_response(doc)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@documents_router.delete(
    "/{document_id}",
    status_code=204,
    response_class=Response,
)
def delete_document(
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> Response:
    doc = _get_document_or_404(db, document_id)
    if not _can_delete(doc, user):
        raise HTTPException(status_code=403, detail="delete_forbidden")
    doc.deleted_at = datetime.now(timezone.utc)
    db.commit()
    log.info(
        "event_document.delete",
        extra={
            "user_id": user.id,
            "event_id": doc.event_id,
            "document_id": doc.id,
            "kind": doc.kind,
            "byte_size": doc.byte_size,
        },
    )
    return Response(status_code=204)
