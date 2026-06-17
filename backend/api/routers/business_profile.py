"""Business profile router.

Singleton resource at `/api/business-profile`. Three endpoints:
  - GET — read the profile (every authenticated user)
  - PATCH — partial update (every authenticated user; admin-gating can be
    added later if shop staff want a separate role)
  - POST /logo — multipart logo upload, replaces the prior file
  - DELETE /logo — clear the logo
  - GET /logo — stream the logo image so the editor can preview it

Logo bytes live in `document_storage` under `business/logo.<ext>` so they
share the disk-space guards already in place for event documents.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import business_profile_service, document_storage
from services.business_profile_service import BusinessProfileError
from services.upload_validation import (
    HEAD_BYTES_NEEDED,
    UploadValidationError,
    validate_magic_bytes,
)

log = logging.getLogger(__name__)

router = APIRouter()


_ERROR_STATUS_MAP = {
    "business_profile_missing": 404,
    "unknown_fields": 422,
    "legal_name_required": 422,
    "invalid_country": 422,
    "invalid_tax_rate": 422,
    "unsupported_logo_type": 415,
    "empty_file": 422,
    "logo_too_large": 413,
    "insufficient_storage": 507,
    "invalid_discount_presets": 422,
    "too_many_discount_presets": 422,
    "invalid_discount_preset_label": 422,
    "invalid_discount_preset_percent": 422,
    "invalid_discount_preset_id": 422,
    "duplicate_discount_preset_id": 422,
    "invalid_default_payment_plan_count": 422,
    "invalid_default_deposit_percent": 422,
    "invalid_target_labor_pct": 422,
    "invalid_gps_accuracy_buffer_max_m": 422,
    "invalid_trusted_clock_in_ips": 422,
}


def _raise_for(exc: BusinessProfileError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS_MAP.get(exc.code, 400),
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


_OffsetBasis = Literal["before_due", "after_due", "after_sent"]


class DiscountPresetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=40)
    label: str = Field(..., min_length=1, max_length=60)
    percent: Decimal = Field(..., ge=0, le=50)
    active: bool = True


class BusinessProfileResponse(BaseModel):
    legal_name: str
    display_name: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str
    phone: str | None
    email: str | None
    website: str | None
    has_logo: bool
    default_tax_rate: Decimal
    default_tax_name: str | None
    default_invoice_terms: str | None
    default_invoice_footer: str | None
    default_payment_instructions: str | None
    # Phase 11
    reminder1_enabled: bool
    reminder1_days_offset: int
    reminder1_offset_basis: str
    reminder2_enabled: bool
    reminder2_days_offset: int
    reminder2_offset_basis: str
    reminder3_enabled: bool
    reminder3_days_offset: int
    reminder3_offset_basis: str
    reminder_late_fee_cents: int
    reminder_late_fee_pct: Decimal
    discount_presets: list[DiscountPresetModel]
    default_payment_plan_count: int | None
    default_deposit_percent: Decimal | None
    # Phase 7 Slice 2 attendance settings (read path, write path was
    # already wired). The frontend needs these on GET to render the
    # current values.
    attendance_gate_enabled: bool
    selfie_policy: Literal["required", "optional", "disabled"]
    selfie_retention_days: int | None
    # Phase 9 sub-slice 1 Priority 2 — biweekly pay-period anchor.
    biweekly_anchor_date: date | None
    # Phase 10 Slice 6 (Epic 6.2) — target labor cost as a percent of
    # weekly revenue.
    target_labor_pct: Decimal | None
    # Clock-in reliability slice A — accuracy buffer cap. 0 disables the
    # buffer entirely; default 50 matches the rule of thumb.
    gps_accuracy_buffer_max_m: int
    # Clock-in reliability slice C — trusted-network fallback.
    trusted_network_enabled: bool
    trusted_clock_in_ips: list[str]
    updated_at: datetime
    updated_by_user_id: int | None


class BusinessProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=40)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=2)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    default_tax_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    default_tax_name: str | None = Field(default=None, max_length=40)
    default_invoice_terms: str | None = None
    default_invoice_footer: str | None = None
    default_payment_instructions: str | None = None
    # Phase 11 reminder schedule. Each slot is independent — staff can
    # leave reminder2 disabled and still configure reminder1/3.
    reminder1_enabled: bool | None = None
    reminder1_days_offset: int | None = Field(default=None, ge=0, le=365)
    reminder1_offset_basis: _OffsetBasis | None = None
    reminder2_enabled: bool | None = None
    reminder2_days_offset: int | None = Field(default=None, ge=0, le=365)
    reminder2_offset_basis: _OffsetBasis | None = None
    reminder3_enabled: bool | None = None
    reminder3_days_offset: int | None = Field(default=None, ge=0, le=365)
    reminder3_offset_basis: _OffsetBasis | None = None
    reminder_late_fee_cents: int | None = Field(default=None, ge=0)
    reminder_late_fee_pct: Decimal | None = Field(default=None, ge=0, lt=1)
    # Phase 1 of the discount/payment-term refactor.
    # `discount_presets` replaces the list wholesale (no merge semantics).
    discount_presets: list[DiscountPresetModel] | None = None
    default_payment_plan_count: Literal[1, 2, 3] | None = None
    default_deposit_percent: Decimal | None = Field(default=None, ge=50, le=100)
    # Phase 7 Slice 2 — attendance settings.
    attendance_gate_enabled: bool | None = None
    selfie_policy: Literal["required", "optional", "disabled"] | None = None
    selfie_retention_days: int | None = Field(default=None, ge=1, le=3650)
    # Phase 9 sub-slice 1 Priority 2 — biweekly pay-period anchor.
    # Nullable on purpose: explicit null clears the anchor and the
    # reporting service falls back to its legacy rolling 14-day window.
    biweekly_anchor_date: date | None = None
    # Phase 10 Slice 6 (Epic 6.2) — target labor cost percent. Null
    # clears the column; the schedule grid hides the goal chip when
    # unset.
    target_labor_pct: Decimal | None = Field(default=None, gt=0, le=100)
    # Clock-in reliability slice A — accuracy buffer cap. 0 disables.
    gps_accuracy_buffer_max_m: int | None = Field(default=None, ge=0, le=200)
    # Clock-in reliability slice C — trusted-network fallback.
    trusted_network_enabled: bool | None = None
    # Owner-managed list of trusted IPs/CIDRs. Cap at 32 entries to
    # avoid pathological PATCH payloads; the boutique only needs the
    # shop's static public IP and maybe one or two CIDRs at most.
    trusted_clock_in_ips: list[str] | None = Field(default=None, max_length=32)


def _to_response(view) -> BusinessProfileResponse:
    return BusinessProfileResponse(
        legal_name=view.legal_name,
        display_name=view.display_name,
        address_line1=view.address_line1,
        address_line2=view.address_line2,
        city=view.city,
        state=view.state,
        postal_code=view.postal_code,
        country=view.country,
        phone=view.phone,
        email=view.email,
        website=view.website,
        has_logo=bool(view.logo_storage_key),
        default_tax_rate=view.default_tax_rate,
        default_tax_name=view.default_tax_name,
        default_invoice_terms=view.default_invoice_terms,
        default_invoice_footer=view.default_invoice_footer,
        default_payment_instructions=view.default_payment_instructions,
        reminder1_enabled=view.reminder1_enabled,
        reminder1_days_offset=view.reminder1_days_offset,
        reminder1_offset_basis=view.reminder1_offset_basis,
        reminder2_enabled=view.reminder2_enabled,
        reminder2_days_offset=view.reminder2_days_offset,
        reminder2_offset_basis=view.reminder2_offset_basis,
        reminder3_enabled=view.reminder3_enabled,
        reminder3_days_offset=view.reminder3_days_offset,
        reminder3_offset_basis=view.reminder3_offset_basis,
        reminder_late_fee_cents=view.reminder_late_fee_cents,
        reminder_late_fee_pct=view.reminder_late_fee_pct,
        discount_presets=[
            DiscountPresetModel(
                id=p.get("id"),
                label=p.get("label", ""),
                percent=Decimal(str(p.get("percent", 0))),
                active=bool(p.get("active", True)),
            )
            for p in (view.discount_presets or [])
        ],
        default_payment_plan_count=view.default_payment_plan_count,
        default_deposit_percent=view.default_deposit_percent,
        attendance_gate_enabled=view.attendance_gate_enabled,
        selfie_policy=view.selfie_policy,
        selfie_retention_days=view.selfie_retention_days,
        biweekly_anchor_date=view.biweekly_anchor_date,
        target_labor_pct=view.target_labor_pct,
        gps_accuracy_buffer_max_m=view.gps_accuracy_buffer_max_m,
        trusted_network_enabled=view.trusted_network_enabled,
        trusted_clock_in_ips=list(view.trusted_clock_in_ips),
        updated_at=view.updated_at,
        updated_by_user_id=view.updated_by_user_id,
    )


@router.get("", response_model=BusinessProfileResponse)
def get_business_profile(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> BusinessProfileResponse:
    try:
        view = business_profile_service.get_profile(db)
    except BusinessProfileError as exc:
        _raise_for(exc)
    return _to_response(view)


@router.patch("", response_model=BusinessProfileResponse)
def patch_business_profile(
    payload: BusinessProfilePatch,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> BusinessProfileResponse:
    raw = payload.model_dump(exclude_unset=True)
    try:
        view = business_profile_service.update_profile(
            db, patch=raw, actor_user_id=user.id
        )
        db.commit()
        log.info(
            "business_profile.updated",
            extra={
                "user_id": user.id,
                "fields": sorted(raw.keys()),
            },
        )
    except BusinessProfileError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@router.post("/logo", response_model=BusinessProfileResponse)
async def upload_business_logo(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
    file: Annotated[UploadFile, File(...)],
) -> BusinessProfileResponse:
    raw_name = (file.filename or "logo").strip() or "logo"
    content_type = (file.content_type or "application/octet-stream").lower()

    # Read into memory — logos are tiny, capped at 2 MB by the service.
    body = await file.read()
    byte_size = len(body)

    # E1 magic-byte gate: the service-layer allowlist still picks the
    # extension from filename+content-type, but a renamed `.exe`/`.html`
    # would slip past that check if both the filename suffix and the
    # browser-supplied content-type pretended to match. Sniff the
    # actual bytes before the file ever reaches `set_logo`.
    ext = ""
    if "." in raw_name:
        ext = raw_name.rsplit(".", 1)[1].lower()
    if not ext:
        # No extension on filename — derive from content_type.
        ct_to_ext = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg"}
        ext = ct_to_ext.get(content_type, "")
    try:
        validate_magic_bytes(declared_ext=ext, head=body[:HEAD_BYTES_NEEDED])
    except UploadValidationError as exc:
        # Translate upload_validation's flat-string code into this router's
        # `{code, message}` dict shape that `_raise_for` produces for every
        # other business-profile error. `unsupported_type` maps to the
        # logo-specific `unsupported_logo_type` so the existing frontend +
        # smoke contract holds.
        logo_code = (
            "unsupported_logo_type"
            if exc.code == UploadValidationError.UNSUPPORTED_TYPE
            else exc.code
        )
        raise HTTPException(
            status_code=_ERROR_STATUS_MAP.get(logo_code, exc.status),
            detail={"code": logo_code, "message": str(exc) or logo_code},
        ) from exc

    from io import BytesIO

    try:
        view = business_profile_service.set_logo(
            db,
            filename=raw_name,
            content_type=content_type,
            fileobj=BytesIO(body),
            byte_size=byte_size,
            actor_user_id=user.id,
        )
        db.commit()
    except BusinessProfileError as exc:
        db.rollback()
        _raise_for(exc)

    return _to_response(view)


@router.delete("/logo", response_model=BusinessProfileResponse)
def delete_business_logo(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_admin_scope)],
) -> BusinessProfileResponse:
    try:
        view = business_profile_service.remove_logo(db, actor_user_id=user.id)
        db.commit()
    except BusinessProfileError as exc:
        db.rollback()
        _raise_for(exc)
    return _to_response(view)


@router.get("/logo")
def get_business_logo(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    """Stream the logo image. Auth-gated like the rest of the surface; the
    Phase 7 portal will fetch this server-side when it renders the
    customer page so the customer never needs an auth token."""
    try:
        view = business_profile_service.get_profile(db)
    except BusinessProfileError as exc:
        _raise_for(exc)

    if not view.logo_storage_key:
        raise HTTPException(status_code=404, detail={"code": "no_logo"})
    try:
        path = document_storage.resolve_path(view.logo_storage_key)
    except ValueError:
        raise HTTPException(
            status_code=500, detail={"code": "invalid_storage_key"}
        )
    if not path.is_file():
        raise HTTPException(status_code=410, detail={"code": "logo_missing"})

    ext = view.logo_storage_key.rsplit(".", 1)[-1].lower()
    media_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media_type)
