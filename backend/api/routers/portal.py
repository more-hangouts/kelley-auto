"""Public-portal router.

Phase 7. Five surfaces:

  - Public, key-gated, mounted at ``/portal/...``: invoice view, quote
    view, view-receipt, accept-and-sign, accepted confirmation. No
    auth. Every route runs through the three invitation gates in
    ``portal_service``.
  - Static portal CSS at ``/portal/static/portal.css``.
  - Staff-side invitation management mounted at ``/api/invoices/{id}/
    invitations`` and ``/api/quotes/{id}/invitations``. Auth-gated.
"""

from __future__ import annotations

import logging
import threading
import time
import ipaddress
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope, require_any_scope
from services.attendance_gate import require_floor_access
from database.connection import get_db
from database.models import Invoice, Quote, User
from services import invoice_pdf, portal_email, portal_service
from services.invoice_pdf import PdfRenderError
from services.portal_email import PortalEmailError
from services.portal_service import PortalServiceError
from api.redis_rate_limit import flush_for_testing, rate_limit

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES = Jinja2Templates(directory=str(_REPO_ROOT / "templates"))


def _money_filter(cents) -> str:
    """Render integer cents as ``$1,234.56`` for portal templates."""
    try:
        amt = int(cents)
    except (TypeError, ValueError):
        return "$0.00"
    sign = "-" if amt < 0 else ""
    amt = abs(amt)
    dollars, c = divmod(amt, 100)
    return f"{sign}${dollars:,}.{c:02d}"


_TEMPLATES.env.filters["money"] = _money_filter


portal_router = APIRouter()
invoice_invitations_router = APIRouter()
quote_invitations_router = APIRouter()


# ---------------------------------------------------------------------------
# Per-IP rate limiter — anti-enumeration. In-process, single uvicorn worker.
# ---------------------------------------------------------------------------


_RATE_LIMIT_PER_MIN = 60
_RATE_LIMIT_WINDOW_SEC = 60
_rate_lock = threading.Lock()
_rate_state: dict[str, deque] = defaultdict(deque)
_PORTAL_REDIS_RATE_LIMIT_PATTERNS = [
    "rl:portal_ip:*",
    "rl:portal_key:*",
]


def _portal_key_scope(request: Request) -> str:
    raw = str(request.path_params.get("public_key") or "missing")
    return raw[:200]


_portal_ip_limit = rate_limit(bucket="portal_ip", limit=60, window=60)
_portal_key_limit = rate_limit(
    bucket="portal_key",
    limit=30,
    window=60,
    key_fn=_portal_key_scope,
)
_PORTAL_RATE_LIMIT_DEPS = [
    Depends(_portal_ip_limit),
    Depends(_portal_key_limit),
]


def _rate_limit(request: Request) -> None:
    """Sliding-window 60/min per IP across all portal endpoints.

    The state is per-process; running multiple uvicorn workers would
    require Redis or similar. The shop runs a single worker today so an
    in-memory deque is enough — and the deque caps memory at the rate
    limit times the number of distinct IPs.
    """
    ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SEC
    with _rate_lock:
        bucket = _rate_state[ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_PER_MIN:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
            )
        bucket.append(now)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gone(business=None) -> HTMLResponse:
    """Render the generic "this link is not available" page with a 404
    status. Used for revoked, expired, deleted, or never-existed keys —
    all four reasons collapse into one response so a probe can't tell
    which case it hit."""
    body = _TEMPLATES.get_template("portal/gone.html").render()
    return HTMLResponse(content=body, status_code=404)


def _client_ip(request: Request) -> str | None:
    """Return the request IP if it parses as a valid v4/v6 address.

    Test harnesses (FastAPI's TestClient) use the literal string
    ``"testclient"`` as ``client.host``, which the Postgres ``inet``
    type rejects. Returning ``None`` for un-parseable values keeps the
    signature insert clean without leaking the test detail into prod
    logic — a real request always has a real IP."""
    if not request.client or not request.client.host:
        return None
    host = request.client.host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


# ---------------------------------------------------------------------------
# Public portal — invoice
# ---------------------------------------------------------------------------


@portal_router.get(
    "/invoice/{public_key}",
    response_class=HTMLResponse,
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def view_invoice(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    _rate_limit(request)
    result = portal_service.get_invoice_view_by_key(db, public_key)
    if result is None:
        return _gone()
    view, _invitation = result
    body = _TEMPLATES.get_template("portal/invoice.html").render(
        inv=view,
        business=view.business,
        view_receipt_url=f"/portal/invoice/{public_key}/view-receipt",
        pdf_url=f"/portal/invoice/{public_key}/pdf",
    )
    return HTMLResponse(content=body)


@portal_router.post(
    "/invoice/{public_key}/view-receipt",
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def stamp_invoice_view(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _rate_limit(request)
    found = portal_service.stamp_invoice_view(db, public_key)
    if not found:
        # Still return 404 silently. The page already rendered if the
        # link was valid; this endpoint just stamps state.
        db.rollback()
        return Response(status_code=404)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Public portal — quote
# ---------------------------------------------------------------------------


@portal_router.get(
    "/quote/{public_key}",
    response_class=HTMLResponse,
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def view_quote(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    _rate_limit(request)
    result = portal_service.get_quote_view_by_key(db, public_key)
    if result is None:
        return _gone()
    view, _invitation = result
    body = _TEMPLATES.get_template("portal/quote.html").render(
        q=view,
        business=view.business,
        view_receipt_url=f"/portal/quote/{public_key}/view-receipt",
        accept_url=f"/portal/quote/{public_key}/accept",
        accepted_url=f"/portal/quote/{public_key}/accepted",
        pdf_url=f"/portal/quote/{public_key}/pdf",
    )
    return HTMLResponse(content=body)


@portal_router.post(
    "/quote/{public_key}/view-receipt",
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def stamp_quote_view(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _rate_limit(request)
    found = portal_service.stamp_quote_view(db, public_key)
    if not found:
        db.rollback()
        return Response(status_code=404)
    db.commit()
    return Response(status_code=204)


class AcceptQuotePayload(BaseModel):
    signature_name: str = Field(min_length=1, max_length=120)
    signature_base64: str = Field(min_length=1, max_length=2_000_000)


@portal_router.post(
    "/quote/{public_key}/accept",
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def accept_quote(
    public_key: str,
    payload: AcceptQuotePayload,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    _rate_limit(request)
    try:
        quote = portal_service.accept_quote_by_key(
            db,
            public_key=public_key,
            signature_base64=payload.signature_base64,
            signature_name=payload.signature_name,
            signature_ip=_client_ip(request),
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        if exc.code == "not_found":
            return JSONResponse(status_code=404, content={"detail": {"code": "not_found"}})
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": exc.code, "message": str(exc)}},
        )
    log.info(
        "portal.quote.accepted",
        extra={
            "quote_id": quote.id,
            "ip": _client_ip(request),
        },
    )
    return JSONResponse(
        status_code=200,
        content={
            "quote_id": quote.id,
            "status": quote.status,
            "signed_at": quote.signature_signed_at.isoformat()
            if quote.signature_signed_at
            else None,
        },
    )


@portal_router.get(
    "/quote/{public_key}/accepted",
    response_class=HTMLResponse,
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def view_quote_accepted(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    _rate_limit(request)
    result = portal_service.get_quote_view_by_key(db, public_key)
    if result is None:
        return _gone()
    view, _invitation = result
    if view.signed_at is None:
        # Not signed yet — bounce back to the main quote page where the
        # signature pad is.
        return HTMLResponse(
            status_code=303,
            content="",
            headers={"Location": f"/portal/quote/{public_key}"},
        )
    body = _TEMPLATES.get_template("portal/accepted.html").render(
        q=view, business=view.business
    )
    return HTMLResponse(content=body)


# ---------------------------------------------------------------------------
# Public portal — invoice + quote PDF download (Phase 8)
# ---------------------------------------------------------------------------


def _portal_pdf_response(path, filename: str) -> FileResponse:
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=filename,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            # The key itself is the auth token — don't let intermediate
            # caches store the bytes against the URL.
            "Cache-Control": "private, no-store",
        },
    )


@portal_router.get(
    "/invoice/{public_key}/pdf",
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def get_invoice_pdf(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> FileResponse:
    """Customer download. Three-gate the invitation, then lazily render
    or serve the cached PDF. Render failures return 503 — the same
    behavior as the staff route — so a customer hitting a stuck render
    sees a real error rather than a half-PDF."""
    _rate_limit(request)
    result = portal_service.get_invoice_view_by_key(db, public_key)
    if result is None:
        return Response(status_code=404)
    invoice = db.get(Invoice, result[1].invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        return Response(status_code=404)
    try:
        path = invoice_pdf.ensure_invoice_pdf(db, invoice_id=invoice.id)
        db.commit()
    except PdfRenderError as exc:
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    return _portal_pdf_response(
        path, invoice_pdf.invoice_pdf_filename(invoice)
    )


@portal_router.get(
    "/quote/{public_key}/pdf",
    dependencies=_PORTAL_RATE_LIMIT_DEPS,
)
def get_quote_pdf(
    public_key: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> FileResponse:
    _rate_limit(request)
    result = portal_service.get_quote_view_by_key(db, public_key)
    if result is None:
        return Response(status_code=404)
    quote = db.get(Quote, result[1].quote_id)
    if quote is None or quote.deleted_at is not None:
        return Response(status_code=404)
    try:
        path = invoice_pdf.ensure_quote_pdf(db, quote_id=quote.id)
        db.commit()
    except PdfRenderError as exc:
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": str(exc)},
        )
    return _portal_pdf_response(path, invoice_pdf.quote_pdf_filename(quote))


# ---------------------------------------------------------------------------
# Static portal CSS — served from this router so the /portal prefix
# stays self-contained even when api/server.py mounts other things.
# ---------------------------------------------------------------------------


_PORTAL_CSS_PATH = _REPO_ROOT / "templates" / "portal" / "static" / "portal.css"


@portal_router.get("/static/portal.css")
def portal_css() -> Response:
    if not _PORTAL_CSS_PATH.exists():
        return Response(status_code=404)
    return Response(
        content=_PORTAL_CSS_PATH.read_bytes(),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


# ---------------------------------------------------------------------------
# Staff-side invitation management
# ---------------------------------------------------------------------------


_ERROR_STATUS_MAP: dict[str, int] = {
    "invoice_not_found": 404,
    "quote_not_found": 404,
    "contact_not_found": 404,
    "invitation_not_found": 404,
    "invitation_deleted": 410,
    "invitation_revoked": 410,
    "invalid_transition": 422,
}


def _raise_for(exc: PortalServiceError) -> None:
    code = _ERROR_STATUS_MAP.get(exc.code, 400)
    raise HTTPException(
        status_code=code, detail={"code": exc.code, "message": str(exc)}
    )


def _email_failed(message: str) -> None:
    raise HTTPException(
        status_code=502,
        detail={"code": "email_send_failed", "message": message},
    )


class InvitationCreatePayload(BaseModel):
    contact_id: int


class StaffInvitationResponse(BaseModel):
    id: int
    contact_id: int
    public_key: str
    portal_url: str
    sent_at: datetime | None
    last_resent_at: datetime | None
    viewed_at: datetime | None
    last_viewed_at: datetime | None
    view_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by_user_id: int | None
    deleted_at: datetime | None


class StaffInvitationListResponse(BaseModel):
    invitations: list[StaffInvitationResponse]


def _to_response(view: portal_service.StaffInvitationView) -> StaffInvitationResponse:
    return StaffInvitationResponse(
        id=view.id,
        contact_id=view.contact_id,
        public_key=view.public_key,
        portal_url=view.portal_url,
        sent_at=view.sent_at,
        last_resent_at=view.last_resent_at,
        viewed_at=view.viewed_at,
        last_viewed_at=view.last_viewed_at,
        view_count=view.view_count,
        expires_at=view.expires_at,
        revoked_at=view.revoked_at,
        revoked_by_user_id=view.revoked_by_user_id,
        deleted_at=view.deleted_at,
    )


# ---- Invoice invitations ---------------------------------------------------


@invoice_invitations_router.get(
    "/{invoice_id}/invitations", response_model=StaffInvitationListResponse
)
def list_invoice_invitations(
    invoice_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    include_deleted: bool = False,
) -> StaffInvitationListResponse:
    rows = portal_service.list_invitations_for_invoice(
        db, invoice_id=invoice_id, include_deleted=include_deleted
    )
    return StaffInvitationListResponse(
        invitations=[_to_response(r) for r in rows]
    )


@invoice_invitations_router.post(
    "/{invoice_id}/invitations",
    response_model=StaffInvitationResponse,
    status_code=201,
)
def add_invoice_invitation(
    invoice_id: int,
    payload: InvitationCreatePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> StaffInvitationResponse:
    try:
        view = portal_service.add_invoice_invitation(
            db,
            invoice_id=invoice_id,
            contact_id=payload.contact_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@invoice_invitations_router.post(
    "/{invoice_id}/invitations/{invitation_id}/revoke",
    response_model=StaffInvitationResponse,
)
def revoke_invoice_invitation(
    invoice_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> StaffInvitationResponse:
    try:
        view = portal_service.revoke_invoice_invitation(
            db,
            invoice_id=invoice_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@invoice_invitations_router.post(
    "/{invoice_id}/invitations/{invitation_id}/resend",
    response_model=StaffInvitationResponse,
)
def resend_invoice_invitation(
    invoice_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> StaffInvitationResponse:
    try:
        view = portal_service.resend_invoice_invitation(
            db,
            invoice_id=invoice_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    invoice = db.get(Invoice, invoice_id)
    try:
        portal_email.send_invoice_invitations(
            db, invoice=invoice, invitation_ids=[invitation_id]
        )
    except PortalEmailError as exc:
        log.warning(
            "portal.invoice_invitation.email_failed",
            extra={"invoice_id": invoice_id, "invitation_id": invitation_id, "error": str(exc)},
        )
        _email_failed("Resend was recorded but the email failed to deliver. Try again in a moment.")
    return _to_response(view)


@invoice_invitations_router.delete(
    "/{invoice_id}/invitations/{invitation_id}",
    status_code=204,
    response_class=Response,
)
def delete_invoice_invitation(
    invoice_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        portal_service.soft_delete_invoice_invitation(
            db,
            invoice_id=invoice_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return Response(status_code=204)


# ---- Quote invitations -----------------------------------------------------


@quote_invitations_router.get(
    "/{quote_id}/invitations", response_model=StaffInvitationListResponse
)
def list_quote_invitations(
    quote_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    include_deleted: bool = False,
) -> StaffInvitationListResponse:
    rows = portal_service.list_invitations_for_quote(
        db, quote_id=quote_id, include_deleted=include_deleted
    )
    return StaffInvitationListResponse(
        invitations=[_to_response(r) for r in rows]
    )


@quote_invitations_router.post(
    "/{quote_id}/invitations",
    response_model=StaffInvitationResponse,
    status_code=201,
)
def add_quote_invitation(
    quote_id: int,
    payload: InvitationCreatePayload,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> StaffInvitationResponse:
    try:
        view = portal_service.add_quote_invitation(
            db,
            quote_id=quote_id,
            contact_id=payload.contact_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@quote_invitations_router.post(
    "/{quote_id}/invitations/{invitation_id}/revoke",
    response_model=StaffInvitationResponse,
)
def revoke_quote_invitation(
    quote_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> StaffInvitationResponse:
    try:
        view = portal_service.revoke_quote_invitation(
            db,
            quote_id=quote_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@quote_invitations_router.post(
    "/{quote_id}/invitations/{invitation_id}/resend",
    response_model=StaffInvitationResponse,
)
def resend_quote_invitation(
    quote_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_floor_access("admin", "sales"))],
) -> StaffInvitationResponse:
    try:
        view = portal_service.resend_quote_invitation(
            db,
            quote_id=quote_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    quote = db.get(Quote, quote_id)
    try:
        portal_email.send_quote_invitations(
            db, quote=quote, invitation_ids=[invitation_id]
        )
    except PortalEmailError as exc:
        log.warning(
            "portal.quote_invitation.email_failed",
            extra={"quote_id": quote_id, "invitation_id": invitation_id, "error": str(exc)},
        )
        _email_failed("Resend was recorded but the email failed to deliver. Try again in a moment.")
    return _to_response(view)


@quote_invitations_router.delete(
    "/{quote_id}/invitations/{invitation_id}",
    status_code=204,
    response_class=Response,
)
def delete_quote_invitation(
    quote_id: int,
    invitation_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        portal_service.soft_delete_quote_invitation(
            db,
            quote_id=quote_id,
            invitation_id=invitation_id,
            actor_user_id=user.id,
        )
        db.commit()
    except PortalServiceError as exc:
        db.rollback()
        _raise_for(exc)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Test/utility hooks
# ---------------------------------------------------------------------------


def _reset_rate_limit_state() -> None:
    """Test helper: drains portal rate-limit state so smoke reruns are clean."""
    with _rate_lock:
        _rate_state.clear()
    try:
        flush_for_testing(_PORTAL_REDIS_RATE_LIMIT_PATTERNS)
    except Exception as exc:  # noqa: BLE001
        log.warning("portal.rate_limit_reset_failed", extra={"error": str(exc)})
