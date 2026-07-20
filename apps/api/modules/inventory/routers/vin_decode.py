"""VIN decode + scan API.

`decode/{vin}` powers the Add-Vehicle "Decode VIN" button: normalize +
structurally validate, compute the check-digit warning, then ask NHTSA
vPIC to resolve year/make/model/trim. `scan` powers "Scan VIN" — it OCRs
a photo of a door-jamb sticker / plate and returns only checksum-valid
candidates (a misread almost always breaks the checksum), so staff are
never shown a garbage guess. Both degrade gracefully: a vPIC outage or an
unreadable photo returns 200 with empty data, never a 5xx, so manual
entry always works.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.settings import VEHICLE_PHOTO_MAX_MB
from database.auth import require_any_scope
from database.connection import get_db
from database.models import CatalogItem, User
from modules.inventory.services import vin as vin_util
from modules.inventory.services import vin_decode, vin_ocr
from services.upload_validation import (
    HEAD_BYTES_NEEDED,
    UploadValidationError,
    validate_magic_bytes,
)

router = APIRouter()

_SCAN_MAX_BYTES = VEHICLE_PHOTO_MAX_MB * 1024 * 1024
_SCAN_ALLOWED_EXT = ("jpg", "jpeg", "png", "webp")
_SCAN_CT_TO_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class VinDecodeResponse(BaseModel):
    vin: str
    check_digit_ok: bool
    decoded: dict[str, Any]
    error: str | None = None
    existing_vehicle_id: int | None = None


class VinScanCandidate(BaseModel):
    vin: str
    check_digit_ok: bool


class VinScanResponse(BaseModel):
    found: bool
    best: VinDecodeResponse | None = None
    candidates: list[VinScanCandidate] = []


def _resolve_vin(db: Session, normalized: str) -> VinDecodeResponse:
    """Decode + duplicate-check a known-normalized VIN. Shared by decode
    and scan so both return the identical shape the UI already handles."""
    check_ok = vin_util.vin_check_digit_ok(normalized)
    decoded, error = vin_decode.decode(normalized)
    existing = (
        db.query(CatalogItem.id)
        .filter(CatalogItem.vin == normalized)
        .filter(CatalogItem.is_vehicle.is_(True))
        .first()
    )
    return VinDecodeResponse(
        vin=normalized,
        check_digit_ok=check_ok,
        decoded=decoded or {},
        error=error,
        existing_vehicle_id=int(existing[0]) if existing else None,
    )


@router.get("/decode/{vin}", response_model=VinDecodeResponse)
def decode_vin(
    vin: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
) -> VinDecodeResponse:
    try:
        normalized = vin_util.normalize_vin(vin)
    except vin_util.VinError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if normalized is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "vehicle_vin_invalid", "message": "Enter a VIN first."},
        )
    return _resolve_vin(db, normalized)


@router.post("/scan", response_model=VinScanResponse)
async def scan_vin(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_any_scope("admin", "sales"))],
    file: Annotated[UploadFile, File()],
) -> VinScanResponse:
    body = await file.read()
    if not body:
        raise HTTPException(
            status_code=422,
            detail={"code": "vehicle_photo_empty", "message": "The photo is empty."},
        )
    if len(body) > _SCAN_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "vehicle_photo_too_large",
                "message": f"Photo too large (max {VEHICLE_PHOTO_MAX_MB} MB).",
            },
        )
    # Magic-byte check so we only hand real images to Tesseract.
    ext = _SCAN_CT_TO_EXT.get((file.content_type or "").lower())
    if ext is None and "." in (file.filename or ""):
        cand = file.filename.rsplit(".", 1)[1].lower()
        ext = cand if cand in _SCAN_ALLOWED_EXT else None
    if ext is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "vehicle_photo_unsupported_type",
                "message": "Photos must be JPG, PNG, or WebP.",
            },
        )
    try:
        validate_magic_bytes(declared_ext=ext, head=body[:HEAD_BYTES_NEEDED])
    except UploadValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "vehicle_photo_unsupported_type",
                "message": "That file isn't a valid image.",
            },
        ) from exc

    candidates, _text = vin_ocr.scan(body)
    # Only surface checksum-valid reads — never suggest a garbage VIN.
    valid = [c for c in candidates if c["check_digit_ok"]]
    if not valid:
        return VinScanResponse(found=False, best=None, candidates=[])

    best = _resolve_vin(db, valid[0]["vin"])
    return VinScanResponse(
        found=True,
        best=best,
        candidates=[VinScanCandidate(**c) for c in valid[:5]],
    )
