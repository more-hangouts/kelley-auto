"""Public-portal access service.

Phase 7. The customer-facing portal is signed-link only — no login, no
account. Every read/write goes through ``invoice_invitations`` or
``quote_invitations`` rows whose ``public_key`` was minted in Phase 1
(``invoice_service.mark_sent``) or Phase 5 (``quote_service.mark_sent``).

Three gates always apply when looking up by key:

  - ``deleted_at IS NULL`` — staff soft-deleted the invitation.
  - ``revoked_at IS NULL`` — staff explicitly killed the link.
  - ``expires_at IS NULL OR expires_at > NOW()`` — TTL window. The column
    exists but isn't populated yet; honored here so a future "30-day
    link" policy needs no service change.

A failed gate returns ``None`` from the lookup. The router translates
``None`` into HTTP 404 — never 401 — so a probe can't distinguish "wrong
key" from "revoked key" from "expired link" from "this invoice never
existed". That's the whole point of the gate.

Same module owns the staff-side invitation management surface (list,
create, revoke, resend, soft-delete) so the lifecycle that mints rows
in invoice_service / quote_service and the lifecycle that retires them
sit next to each other.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from database.models import (
    CatalogItem,
    Contact,
    Event,
    Invoice,
    InvoiceInstallment,
    InvoiceInvitation,
    InvoiceLineItem,
    Quote,
    QuoteInvitation,
    QuoteLineItem,
    User,
)
from services import quote_service
from services.email_transport import send_rendered_safely
from services.business_profile_service import (
    BusinessProfileError,
    BusinessProfileView,
    get_profile,
)
from services.catalog_service import assert_public_render_keys, customer_line_view

log = logging.getLogger(__name__)


class PortalServiceError(Exception):
    """Domain-level rejection. Surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str = "portal_error") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# View dataclasses — what the templates see
# ---------------------------------------------------------------------------


@dataclass
class PortalLineItem:
    """Customer-safe portal line. Phase 4 dropped ``description`` and
    ``notes`` from this DTO entirely so a future template change cannot
    accidentally read staff-typed text. The portal partial reads the
    same fields as the PDF partial, all derived through
    ``catalog_service.customer_line_view`` so the two surfaces cannot
    drift.
    """

    public_code: str | None
    display_text: str
    quantity: str
    unit_price_cents: int
    line_total_cents: int
    kind: str


@dataclass
class PortalInstallment:
    label: str
    amount_cents: int
    due_date: str  # already formatted "Month D, YYYY" — template stays dumb
    paid: bool


@dataclass
class PortalContact:
    display_name: str
    email: str | None


@dataclass
class PortalBusiness:
    """Subset of BusinessProfile that's safe to show on a public page.

    Excludes: ``private_notes``, ``default_payment_instructions`` (staff-
    facing copy), and any internal-only fields. The portal is rendered
    server-side so missing the singleton doesn't crash — fields fall back
    to a static shop string.
    """

    legal_name: str
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    phone: str | None
    email: str | None
    website: str | None
    logo_storage_key: str | None


@dataclass
class PortalInvoiceView:
    invoice_id: int
    invoice_number: str | None
    status: str
    issue_date: str
    due_date: str | None
    contact: PortalContact
    business: PortalBusiness
    line_items: list[PortalLineItem]
    installments: list[PortalInstallment]
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    paid_to_date_cents: int
    balance_cents: int
    terms: str | None
    footer: str | None
    public_notes: str | None


@dataclass
class PortalQuoteView:
    quote_id: int
    quote_number: str | None
    status: str
    issue_date: str
    expires_at: str | None
    contact: PortalContact
    business: PortalBusiness
    line_items: list[PortalLineItem]
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    terms: str | None
    footer: str | None
    public_notes: str | None
    signed_at: datetime | None
    signature_name: str | None
    is_signable: bool  # True iff status == 'sent'


# ---------------------------------------------------------------------------
# Public lookup helpers (key-gated)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_invoice_invitation(
    db: Session, public_key: str
) -> InvoiceInvitation | None:
    """Apply the three gates. Returns the invitation row or None."""
    if not public_key or len(public_key) > 200:
        return None
    row = (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.public_key == public_key)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
        .first()
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= _now():
        return None
    return row


def _fetch_quote_invitation(
    db: Session, public_key: str
) -> QuoteInvitation | None:
    if not public_key or len(public_key) > 200:
        return None
    row = (
        db.query(QuoteInvitation)
        .filter(QuoteInvitation.public_key == public_key)
        .filter(QuoteInvitation.deleted_at.is_(None))
        .filter(QuoteInvitation.revoked_at.is_(None))
        .first()
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= _now():
        return None
    return row


def _format_date(d) -> str:
    if d is None:
        return ""
    try:
        return d.strftime("%B %-d, %Y")
    except Exception:  # pragma: no cover — non-POSIX strftime
        return d.isoformat()


def _portal_business(db: Session) -> PortalBusiness:
    try:
        view: BusinessProfileView = get_profile(db)
    except BusinessProfileError:
        # The portal still has to render even if the singleton row is
        # missing in some misconfigured environment. Fall back to a
        # minimal stub rather than 500ing the customer.
        return PortalBusiness(
            legal_name="Bella's XV",
            address_line1=None,
            address_line2=None,
            city=None,
            state=None,
            postal_code=None,
            phone=None,
            email=None,
            website=None,
            logo_storage_key=None,
        )
    return PortalBusiness(
        legal_name=view.legal_name,
        address_line1=view.address_line1,
        address_line2=view.address_line2,
        city=view.city,
        state=view.state,
        postal_code=view.postal_code,
        phone=view.phone,
        email=view.email,
        website=view.website,
        logo_storage_key=view.logo_storage_key,
    )


def _to_portal_line(
    li: InvoiceLineItem | QuoteLineItem,
    catalog: CatalogItem | None,
) -> PortalLineItem:
    qty = li.quantity
    qty_str = (
        str(qty.normalize())
        if hasattr(qty, "normalize")
        else str(qty)
    )
    # Strip a trailing ".0" that Decimal.normalize() can leave in
    # exponential form — render "1" not "1E+0".
    if "E" in qty_str:
        qty_str = format(qty, "f").rstrip("0").rstrip(".") or "0"
    view = customer_line_view(li, catalog)
    return PortalLineItem(
        public_code=view.public_code,
        display_text=view.display_text,
        quantity=qty_str,
        unit_price_cents=view.unit_price_cents,
        line_total_cents=view.line_total_cents,
        kind=view.kind,
    )


def _project_portal_lines(
    db: Session, line_rows: list[InvoiceLineItem | QuoteLineItem]
) -> list[PortalLineItem]:
    """Resolve catalog snapshots in one batch and run every line
    through ``_to_portal_line``. Same shape as
    ``invoice_pdf._project_customer_lines``; the two surfaces stay
    aligned so the catalog-leak rules in
    ``catalog_service.customer_line_view`` apply everywhere a
    customer can read line text.
    """
    catalog_ids = {
        int(li.catalog_item_id) for li in line_rows if li.catalog_item_id
    }
    catalog_by_id: dict[int, CatalogItem] = {}
    if catalog_ids:
        rows = (
            db.query(CatalogItem)
            .filter(CatalogItem.id.in_(catalog_ids))
            .all()
        )
        catalog_by_id = {int(r.id): r for r in rows}
    projected = [
        _to_portal_line(li, catalog_by_id.get(li.catalog_item_id))
        for li in line_rows
    ]
    assert_public_render_keys(projected)
    return projected


def get_invoice_view_by_key(
    db: Session, public_key: str
) -> tuple[PortalInvoiceView, InvoiceInvitation] | None:
    """Render data for ``/portal/invoice/<key>``. ``None`` means 404."""
    invitation = _fetch_invoice_invitation(db, public_key)
    if invitation is None:
        return None
    invoice = db.get(Invoice, invitation.invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        return None
    contact = db.get(Contact, invitation.contact_id)
    if contact is None:
        return None

    line_rows = (
        db.query(InvoiceLineItem)
        .filter(InvoiceLineItem.invoice_id == invoice.id)
        .order_by(InvoiceLineItem.sort_order.asc(), InvoiceLineItem.id.asc())
        .all()
    )
    inst_rows = (
        db.query(InvoiceInstallment)
        .filter(InvoiceInstallment.invoice_id == invoice.id)
        .order_by(
            InvoiceInstallment.sort_order.asc(), InvoiceInstallment.id.asc()
        )
        .all()
    )
    view = PortalInvoiceView(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        issue_date=_format_date(invoice.issue_date),
        due_date=_format_date(invoice.due_date) if invoice.due_date else None,
        contact=PortalContact(
            display_name=contact.display_name, email=contact.email
        ),
        business=_portal_business(db),
        line_items=_project_portal_lines(db, line_rows),
        installments=[
            PortalInstallment(
                label=inst.label,
                amount_cents=int(inst.amount_cents),
                due_date=_format_date(inst.due_date),
                paid=inst.paid_at is not None,
            )
            for inst in inst_rows
        ],
        subtotal_cents=int(invoice.subtotal_cents),
        discount_cents=int(invoice.discount_cents),
        tax_cents=int(invoice.tax_cents),
        total_cents=int(invoice.total_cents),
        paid_to_date_cents=int(invoice.paid_to_date_cents),
        balance_cents=int(invoice.balance_cents),
        terms=invoice.terms,
        footer=invoice.footer,
        public_notes=invoice.public_notes,
    )
    return view, invitation


def get_quote_view_by_key(
    db: Session, public_key: str
) -> tuple[PortalQuoteView, QuoteInvitation] | None:
    """Render data for ``/portal/quote/<key>``. ``None`` means 404."""
    invitation = _fetch_quote_invitation(db, public_key)
    if invitation is None:
        return None
    quote = db.get(Quote, invitation.quote_id)
    if quote is None or quote.deleted_at is not None:
        return None
    contact = db.get(Contact, invitation.contact_id)
    if contact is None:
        return None

    line_rows = (
        db.query(QuoteLineItem)
        .filter(QuoteLineItem.quote_id == quote.id)
        .order_by(QuoteLineItem.sort_order.asc(), QuoteLineItem.id.asc())
        .all()
    )
    view = PortalQuoteView(
        quote_id=quote.id,
        quote_number=quote.quote_number,
        status=quote.status,
        issue_date=_format_date(quote.issue_date),
        expires_at=_format_date(quote.expires_at) if quote.expires_at else None,
        contact=PortalContact(
            display_name=contact.display_name, email=contact.email
        ),
        business=_portal_business(db),
        line_items=_project_portal_lines(db, line_rows),
        subtotal_cents=int(quote.subtotal_cents),
        discount_cents=int(quote.discount_cents),
        tax_cents=int(quote.tax_cents),
        total_cents=int(quote.total_cents),
        terms=quote.terms,
        footer=quote.footer,
        public_notes=quote.public_notes,
        signed_at=quote.signature_signed_at,
        signature_name=quote.signature_name,
        is_signable=quote.status == "sent",
    )
    return view, invitation


# ---------------------------------------------------------------------------
# Mutations triggered from the portal
# ---------------------------------------------------------------------------


def stamp_invoice_view(db: Session, public_key: str) -> bool:
    """Idempotent on first-view; always bumps view_count + last_viewed_at.

    Returns True if the row was found and stamped, False otherwise (the
    router maps False to 404 silently — view tracking on a revoked link
    must not leak liveness signal).
    """
    from services import activity_log  # local to avoid import cycle

    invitation = _fetch_invoice_invitation(db, public_key)
    if invitation is None:
        return False
    now = _now()
    is_first_view = invitation.viewed_at is None
    invitation.last_viewed_at = now
    invitation.view_count = (invitation.view_count or 0) + 1
    if is_first_view:
        invitation.viewed_at = now
    invitation.updated_at = now
    db.flush()
    if is_first_view:
        # One activity row per invitation per first-view. Repeat views
        # bump the counter on the invitation row but don't spam the
        # timeline; staff care that the link was opened, not how many
        # times the customer reloaded the tab.
        invoice = db.get(Invoice, invitation.invoice_id)
        if invoice is not None:
            activity_log.log_activity(
                db,
                event_id=invoice.event_id,
                actor_kind="customer",
                actor_user_id=None,
                activity_type=activity_log.INVOICE_VIEWED,
                subject_kind="invoice",
                subject_id=invoice.id,
                payload={
                    "invoice_number": invoice.invoice_number,
                    "invitation_id": invitation.id,
                },
            )
    return True


def stamp_quote_view(db: Session, public_key: str) -> bool:
    from services import activity_log  # local to avoid import cycle

    invitation = _fetch_quote_invitation(db, public_key)
    if invitation is None:
        return False
    now = _now()
    is_first_view = invitation.viewed_at is None
    invitation.last_viewed_at = now
    invitation.view_count = (invitation.view_count or 0) + 1
    if is_first_view:
        invitation.viewed_at = now
    invitation.updated_at = now
    db.flush()
    if is_first_view:
        quote = db.get(Quote, invitation.quote_id)
        if quote is not None:
            activity_log.log_activity(
                db,
                event_id=quote.event_id,
                actor_kind="customer",
                actor_user_id=None,
                activity_type=activity_log.QUOTE_VIEWED,
                subject_kind="quote",
                subject_id=quote.id,
                payload={
                    "quote_number": quote.quote_number,
                    "invitation_id": invitation.id,
                },
            )
    return True


def accept_quote_by_key(
    db: Session,
    *,
    public_key: str,
    signature_base64: str,
    signature_name: str,
    signature_ip: str | None,
) -> Quote:
    """Customer signature route. Records the signature on the quote and
    flips status to 'approved'. The signature lives on the quote itself
    because the signed quote IS the contract; the invitation row only
    proves who clicked the link.

    Idempotent: a re-submission against an already-approved quote
    returns the existing quote without overwriting the signature.
    """
    invitation = _fetch_quote_invitation(db, public_key)
    if invitation is None:
        raise PortalServiceError("invitation not found", code="not_found")
    quote = db.get(Quote, invitation.quote_id)
    if quote is None or quote.deleted_at is not None:
        raise PortalServiceError("quote not found", code="not_found")
    try:
        result = quote_service.approve_quote(
            db,
            quote_id=quote.id,
            signature_base64=signature_base64,
            signature_name=signature_name,
            signature_ip=signature_ip,
            actor_user_id=None,
        )
    except quote_service.QuoteServiceError as exc:
        # Map signature_required + invalid_transition into portal codes.
        # Anything unexpected bubbles up so the router 500s noisily.
        if exc.code in ("signature_required", "invalid_transition"):
            raise PortalServiceError(str(exc), code=exc.code) from exc
        raise
    _send_quote_signed_email(db, quote=result)
    return result


def _send_quote_signed_email(db: Session, *, quote: Quote) -> None:
    """Notify the quote owner that the customer signed. Best-effort —
    SMTP failures don't block the signature commit. Owner is
    ``quote.created_by_user_id``; falls back to no-op if the quote was
    drafted by a now-removed user (FK is ``ON DELETE SET NULL``)."""
    if quote.created_by_user_id is None:
        return
    owner = db.get(User, quote.created_by_user_id)
    if owner is None or not owner.email or not owner.is_active:
        return
    contact = db.get(Contact, quote.contact_id)
    customer_name = (contact.display_name if contact else None) or "Customer"

    from config.settings import ADMIN_BASE_URL
    from services import notification_templates

    rendered = notification_templates.render_staff_quote_signed(
        staff_user=owner,
        quote_number=quote.quote_number or f"#{quote.id}",
        customer_name=customer_name,
        quote_total_cents=int(quote.total_cents or 0),
        signed_at=quote.signature_signed_at or datetime.now(timezone.utc),
        admin_url=f"{ADMIN_BASE_URL}/quotes/{quote.id}",
    )
    send_rendered_safely(
        to=owner.email, rendered=rendered, scope="portal.quote_signed"
    )


# ---------------------------------------------------------------------------
# Staff-side invitation management — called from the auth-gated routers
# ---------------------------------------------------------------------------


_DocKind = Literal["invoice", "quote"]


@dataclass
class StaffInvitationView:
    id: int
    contact_id: int
    public_key: str
    sent_at: datetime | None
    last_resent_at: datetime | None
    viewed_at: datetime | None
    last_viewed_at: datetime | None
    view_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by_user_id: int | None
    deleted_at: datetime | None
    portal_url: str  # Pre-built so the staff UI can copy/share without
    # re-deriving the prefix in every screen.


def _portal_url(kind: _DocKind, public_key: str) -> str:
    from config.settings import PORTAL_BASE_URL

    base = (PORTAL_BASE_URL or "").rstrip("/")
    return f"{base}/portal/{kind}/{public_key}"


def _row_to_staff_view(
    row: InvoiceInvitation | QuoteInvitation, kind: _DocKind
) -> StaffInvitationView:
    return StaffInvitationView(
        id=row.id,
        contact_id=row.contact_id,
        public_key=row.public_key,
        sent_at=row.sent_at,
        last_resent_at=row.last_resent_at,
        viewed_at=row.viewed_at,
        last_viewed_at=row.last_viewed_at,
        view_count=int(row.view_count or 0),
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        revoked_by_user_id=row.revoked_by_user_id,
        deleted_at=row.deleted_at,
        portal_url=_portal_url(kind, row.public_key),
    )


def list_invitations_for_invoice(
    db: Session, *, invoice_id: int, include_deleted: bool = False
) -> list[StaffInvitationView]:
    q = db.query(InvoiceInvitation).filter(
        InvoiceInvitation.invoice_id == invoice_id
    )
    if not include_deleted:
        q = q.filter(InvoiceInvitation.deleted_at.is_(None))
    rows = q.order_by(InvoiceInvitation.id.asc()).all()
    return [_row_to_staff_view(r, "invoice") for r in rows]


def list_invitations_for_quote(
    db: Session, *, quote_id: int, include_deleted: bool = False
) -> list[StaffInvitationView]:
    q = db.query(QuoteInvitation).filter(QuoteInvitation.quote_id == quote_id)
    if not include_deleted:
        q = q.filter(QuoteInvitation.deleted_at.is_(None))
    rows = q.order_by(QuoteInvitation.id.asc()).all()
    return [_row_to_staff_view(r, "quote") for r in rows]


def add_invoice_invitation(
    db: Session,
    *,
    invoice_id: int,
    contact_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.deleted_at is not None:
        raise PortalServiceError("invoice not found", code="invoice_not_found")
    if invoice.status == "draft":
        # Mirror the invariant from invoice_service: drafts have no
        # invitations because they haven't been sent yet.
        raise PortalServiceError(
            "cannot add invitation to a draft invoice",
            code="invalid_transition",
        )
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise PortalServiceError("contact not found", code="contact_not_found")
    # If a live row already exists, return it instead of creating a
    # duplicate (the staff UI should resend, not double-mint).
    existing = (
        db.query(InvoiceInvitation)
        .filter(InvoiceInvitation.invoice_id == invoice_id)
        .filter(InvoiceInvitation.contact_id == contact_id)
        .filter(InvoiceInvitation.deleted_at.is_(None))
        .filter(InvoiceInvitation.revoked_at.is_(None))
        .first()
    )
    if existing is not None:
        return _row_to_staff_view(existing, "invoice")

    row = InvoiceInvitation(
        invoice_id=invoice_id,
        contact_id=contact_id,
        public_key=secrets.token_urlsafe(32),
        sent_at=_now(),
    )
    db.add(row)
    db.flush()
    log.info(
        "portal.invoice_invitation.created",
        extra={
            "user_id": actor_user_id,
            "invoice_id": invoice_id,
            "contact_id": contact_id,
            "invitation_id": row.id,
        },
    )
    return _row_to_staff_view(row, "invoice")


def add_quote_invitation(
    db: Session,
    *,
    quote_id: int,
    contact_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise PortalServiceError("quote not found", code="quote_not_found")
    if quote.status == "draft":
        raise PortalServiceError(
            "cannot add invitation to a draft quote",
            code="invalid_transition",
        )
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise PortalServiceError("contact not found", code="contact_not_found")
    existing = (
        db.query(QuoteInvitation)
        .filter(QuoteInvitation.quote_id == quote_id)
        .filter(QuoteInvitation.contact_id == contact_id)
        .filter(QuoteInvitation.deleted_at.is_(None))
        .filter(QuoteInvitation.revoked_at.is_(None))
        .first()
    )
    if existing is not None:
        return _row_to_staff_view(existing, "quote")

    row = QuoteInvitation(
        quote_id=quote_id,
        contact_id=contact_id,
        public_key=secrets.token_urlsafe(32),
        sent_at=_now(),
    )
    db.add(row)
    db.flush()
    log.info(
        "portal.quote_invitation.created",
        extra={
            "user_id": actor_user_id,
            "quote_id": quote_id,
            "contact_id": contact_id,
            "invitation_id": row.id,
        },
    )
    return _row_to_staff_view(row, "quote")


def _get_invoice_invitation_or_raise(
    db: Session, invitation_id: int
) -> InvoiceInvitation:
    row = db.get(InvoiceInvitation, invitation_id)
    if row is None:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    return row


def _get_quote_invitation_or_raise(
    db: Session, invitation_id: int
) -> QuoteInvitation:
    row = db.get(QuoteInvitation, invitation_id)
    if row is None:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    return row


def revoke_invoice_invitation(
    db: Session,
    *,
    invoice_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    row = _get_invoice_invitation_or_raise(db, invitation_id)
    if row.invoice_id != invoice_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is not None:
        raise PortalServiceError(
            "invitation is deleted", code="invitation_deleted"
        )
    if row.revoked_at is None:
        row.revoked_at = _now()
        row.revoked_by_user_id = actor_user_id
        row.updated_at = _now()
        db.flush()
        log.info(
            "portal.invoice_invitation.revoked",
            extra={
                "user_id": actor_user_id,
                "invoice_id": row.invoice_id,
                "invitation_id": row.id,
            },
        )
        from services import activity_log  # local to avoid import cycle
        invoice = db.get(Invoice, row.invoice_id)
        if invoice is not None:
            activity_log.log_activity(
                db,
                event_id=invoice.event_id,
                actor_kind="staff" if actor_user_id else "system",
                actor_user_id=actor_user_id,
                activity_type=activity_log.INVITATION_REVOKED,
                subject_kind="invoice",
                subject_id=invoice.id,
                payload={
                    "invoice_number": invoice.invoice_number,
                    "invitation_id": row.id,
                    "contact_id": row.contact_id,
                },
            )
    return _row_to_staff_view(row, "invoice")


def revoke_quote_invitation(
    db: Session,
    *,
    quote_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    row = _get_quote_invitation_or_raise(db, invitation_id)
    if row.quote_id != quote_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is not None:
        raise PortalServiceError(
            "invitation is deleted", code="invitation_deleted"
        )
    if row.revoked_at is None:
        row.revoked_at = _now()
        row.revoked_by_user_id = actor_user_id
        row.updated_at = _now()
        db.flush()
        log.info(
            "portal.quote_invitation.revoked",
            extra={
                "user_id": actor_user_id,
                "quote_id": row.quote_id,
                "invitation_id": row.id,
            },
        )
        from services import activity_log  # local to avoid import cycle
        quote = db.get(Quote, row.quote_id)
        if quote is not None:
            activity_log.log_activity(
                db,
                event_id=quote.event_id,
                actor_kind="staff" if actor_user_id else "system",
                actor_user_id=actor_user_id,
                activity_type=activity_log.INVITATION_REVOKED,
                subject_kind="quote",
                subject_id=quote.id,
                payload={
                    "quote_number": quote.quote_number,
                    "invitation_id": row.id,
                    "contact_id": row.contact_id,
                },
            )
    return _row_to_staff_view(row, "quote")


def soft_delete_invoice_invitation(
    db: Session,
    *,
    invoice_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> None:
    row = _get_invoice_invitation_or_raise(db, invitation_id)
    if row.invoice_id != invoice_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is None:
        row.deleted_at = _now()
        row.updated_at = _now()
        db.flush()
        log.info(
            "portal.invoice_invitation.deleted",
            extra={
                "user_id": actor_user_id,
                "invoice_id": row.invoice_id,
                "invitation_id": row.id,
            },
        )


def soft_delete_quote_invitation(
    db: Session,
    *,
    quote_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> None:
    row = _get_quote_invitation_or_raise(db, invitation_id)
    if row.quote_id != quote_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is None:
        row.deleted_at = _now()
        row.updated_at = _now()
        db.flush()
        log.info(
            "portal.quote_invitation.deleted",
            extra={
                "user_id": actor_user_id,
                "quote_id": row.quote_id,
                "invitation_id": row.id,
            },
        )


def resend_invoice_invitation(
    db: Session,
    *,
    invoice_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    """Bumps ``last_resent_at`` and re-emits the email. Reuses the same
    public_key so prior bookmarks still work. Refuses if the row is
    revoked (rotate via add_invoice_invitation instead) or deleted."""
    row = _get_invoice_invitation_or_raise(db, invitation_id)
    if row.invoice_id != invoice_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is not None:
        raise PortalServiceError(
            "invitation is deleted", code="invitation_deleted"
        )
    if row.revoked_at is not None:
        raise PortalServiceError(
            "invitation is revoked; create a fresh one instead",
            code="invitation_revoked",
        )
    now = _now()
    row.last_resent_at = now
    if row.sent_at is None:
        row.sent_at = now
    row.updated_at = now
    db.flush()
    log.info(
        "portal.invoice_invitation.resent",
        extra={
            "user_id": actor_user_id,
            "invoice_id": row.invoice_id,
            "invitation_id": row.id,
        },
    )
    from services import activity_log  # local to avoid import cycle
    invoice = db.get(Invoice, row.invoice_id)
    if invoice is not None:
        activity_log.log_activity(
            db,
            event_id=invoice.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.INVITATION_RESENT,
            subject_kind="invoice",
            subject_id=invoice.id,
            payload={
                "invoice_number": invoice.invoice_number,
                "invitation_id": row.id,
                "contact_id": row.contact_id,
            },
        )
    return _row_to_staff_view(row, "invoice")


def resend_quote_invitation(
    db: Session,
    *,
    quote_id: int,
    invitation_id: int,
    actor_user_id: int | None = None,
) -> StaffInvitationView:
    row = _get_quote_invitation_or_raise(db, invitation_id)
    if row.quote_id != quote_id:
        raise PortalServiceError(
            "invitation not found", code="invitation_not_found"
        )
    if row.deleted_at is not None:
        raise PortalServiceError(
            "invitation is deleted", code="invitation_deleted"
        )
    if row.revoked_at is not None:
        raise PortalServiceError(
            "invitation is revoked; create a fresh one instead",
            code="invitation_revoked",
        )
    now = _now()
    row.last_resent_at = now
    if row.sent_at is None:
        row.sent_at = now
    row.updated_at = now
    db.flush()
    log.info(
        "portal.quote_invitation.resent",
        extra={
            "user_id": actor_user_id,
            "quote_id": row.quote_id,
            "invitation_id": row.id,
        },
    )
    from services import activity_log  # local to avoid import cycle
    quote = db.get(Quote, row.quote_id)
    if quote is not None:
        activity_log.log_activity(
            db,
            event_id=quote.event_id,
            actor_kind="staff" if actor_user_id else "system",
            actor_user_id=actor_user_id,
            activity_type=activity_log.INVITATION_RESENT,
            subject_kind="quote",
            subject_id=quote.id,
            payload={
                "quote_number": quote.quote_number,
                "invitation_id": row.id,
                "contact_id": row.contact_id,
            },
        )
    return _row_to_staff_view(row, "quote")
