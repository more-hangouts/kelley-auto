"""Clock-in endpoints (Phase 7 of the Sales Portal).

Three routes under `/api/sales/clock`, all gated on
`require_sales_scope`:

    POST /api/sales/clock/in     — clock in (geofence enforced)
    POST /api/sales/clock/out    — clock out (geofence echoed, not enforced)
    GET  /api/sales/clock/status — current state + today's punches

Slice 1 shipped these as JSON. Slice 2 promotes them to multipart
with an optional `selfie` file part, gated on the owner's
`business_profile.selfie_policy` setting (`required`, `optional`, or
`disabled`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.auth import require_sales_scope
from database.connection import get_db
from database.models import BusinessProfile, StaffPunch, User
from services import clock_in, clock_selfie
from services.business_time import business_date, shop_tz
from services.clock_in import ClockInError
from services.clock_selfie import SelfieStorageError

router = APIRouter()


class PunchResponse(BaseModel):
    id: int
    user_id: int
    direction: Literal["in", "out"]
    punched_at: datetime
    # Boutique-local rendering of `punched_at`. The frontend renders
    # this directly so a punch at 11:30pm local on Saturday displays as
    # Saturday even when its UTC date is Sunday.
    punched_at_local: str
    status: str
    location_id: int | None
    distance_to_location_m: float | None
    client_accuracy_m: float | None
    selfie_storage_key: str | None
    # Slice 2B-2: stylists need to see auto-close + needs-review state
    # on their own punches so they can hit "Confirm hours" or file a
    # correction.
    auto_closed: bool
    auto_close_reason: str | None
    hours_confirmation_status: str
    # Reliability slice A: how the gate accepted the punch. `'gps'`,
    # `'gps_with_accuracy_buffer'`, or `'trusted_network'` (slice C).
    # `accepted_buffer_m` is the slack value used when the buffer fired.
    accepted_by: str
    accepted_buffer_m: float | None
    # Reliability slice C: evidence flag, set whenever the request IP
    # matched the trusted-network list — regardless of how the punch
    # was actually accepted. During the log-only window this is the
    # owner's signal that the trusted list is hitting reliably.
    trusted_network_detected: bool


class StatusResponse(BaseModel):
    state: Literal["in", "out"]
    last_punch: PunchResponse | None
    today_punches: list[PunchResponse]
    timezone: str
    business_date: str
    selfie_policy: str
    # When True, the server-side attendance gate blocks sales-scope
    # appointment mutations while `state == 'out'`. The frontend uses
    # this to decide whether to redirect a punched-out stylist to
    # `/clock` after PIN login. Owner can flip this off without a
    # deploy via PATCH /api/business-profile.
    attendance_gate_enabled: bool
    # Reliability slice C: per-request trusted-network state so the UI
    # can show "Connected through boutique network" before the user
    # taps clock-in. `enabled` is the owner toggle; `detected` is the
    # current request's IP-match result.
    trusted_network_enabled: bool
    trusted_network_detected: bool


def _to_punch_response(punch: StaffPunch) -> PunchResponse:
    from services.business_time import to_business_local

    return PunchResponse(
        id=punch.id,
        user_id=punch.user_id,
        direction=punch.direction,
        punched_at=punch.punched_at,
        punched_at_local=to_business_local(punch.punched_at).isoformat(),
        status=punch.status,
        location_id=punch.location_id,
        distance_to_location_m=(
            float(punch.distance_to_location_m)
            if punch.distance_to_location_m is not None
            else None
        ),
        client_accuracy_m=(
            float(punch.client_accuracy_m)
            if punch.client_accuracy_m is not None
            else None
        ),
        selfie_storage_key=punch.selfie_storage_key,
        auto_closed=bool(punch.auto_closed),
        auto_close_reason=punch.auto_close_reason,
        hours_confirmation_status=punch.hours_confirmation_status,
        accepted_by=punch.accepted_by or "gps",
        accepted_buffer_m=(
            float(punch.accepted_buffer_m)
            if punch.accepted_buffer_m is not None
            else None
        ),
        trusted_network_detected=bool(punch.trusted_network_detected),
    )


def _client_ip(request: Request) -> str | None:
    if request.client and request.client.host:
        return request.client.host
    return None


def _real_client_ip(request: Request) -> str | None:
    """Resolve the real public client IP for the trusted-network check.

    We sit behind nginx (`proxy_pass` from `api.shopbellasxv.com`), so
    `request.client.host` is always 127.0.0.1 in production. nginx
    forwards the original client address in `X-Forwarded-For`; the
    left-most entry is the client we care about. Falls back to
    `request.client.host` for unit tests / direct localhost calls.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return _client_ip(request)


def _raise_for_clock(exc: ClockInError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(status_code=exc.http_status, detail=detail) from exc


def _raise_for_selfie(exc: SelfieStorageError) -> None:
    raise HTTPException(
        status_code=exc.http_status, detail={"code": exc.code}
    ) from exc


def _resolve_selfie_policy(db: Session) -> str:
    profile = db.query(BusinessProfile).first()
    return profile.selfie_policy if profile is not None else "optional"


def _resolve_accuracy_buffer_max_m(db: Session) -> int:
    """Read the owner-configured cap on accuracy slack. Default 50 when
    the column has not been set; 0 disables the buffer entirely."""
    profile = db.query(BusinessProfile).first()
    if profile is None or profile.gps_accuracy_buffer_max_m is None:
        return 50
    return int(profile.gps_accuracy_buffer_max_m)


def _resolve_trusted_network(db: Session) -> tuple[bool, list[str]]:
    """Return (enabled, ip_list). `enabled=False` disables the
    acceptance bypass even if the IP list matches; the audit flag still
    flows so the owner can verify reliability before flipping the
    toggle on."""
    profile = db.query(BusinessProfile).first()
    if profile is None:
        return False, []
    raw = profile.trusted_clock_in_ips or []
    ips = [str(x) for x in raw if isinstance(x, str)]
    return bool(profile.trusted_network_enabled), ips


def _resolve_attendance_gate_enabled(db: Session) -> bool:
    """Mirror of `services.attendance_gate._is_gate_enabled` for the
    status read. The gate dep itself stays self-contained; this
    helper just exposes the same setting to the SalesApp /clock UI."""
    profile = db.query(BusinessProfile).first()
    if profile is None:
        return False
    return bool(profile.attendance_gate_enabled)


async def _validate_selfie_part(
    selfie: UploadFile | None, policy: str
) -> bytes | None:
    """Apply the policy + read the upload + validate. Returns
    pre-normalized WebP bytes ready to write, or None when no selfie
    should be persisted.

    Policy outcomes:
      required + missing  → 400 selfie_required
      disabled + present  → 400 selfie_disabled
      optional + missing  → no-op (None)
      any policy + bad    → SelfieStorageError 4xx (validate_selfie_bytes)

    Validating BEFORE the punch row is created means a 4xx selfie
    rejection does not leave a partial punch behind.
    """
    if policy == "disabled" and selfie is not None:
        raise HTTPException(
            status_code=400, detail={"code": "selfie_disabled"}
        )
    if policy == "required" and selfie is None:
        raise HTTPException(
            status_code=400, detail={"code": "selfie_required"}
        )
    if selfie is None:
        return None

    raw = await selfie.read()
    try:
        return clock_selfie.validate_selfie_bytes(
            raw_bytes=raw, declared_mime=selfie.content_type
        )
    except SelfieStorageError as exc:
        _raise_for_selfie(exc)


def _attach_selfie(
    db: Session,
    *,
    punch: StaffPunch,
    webp_bytes: bytes | None,
    user_id: int,
) -> None:
    """If a selfie was prepared, write it and stamp the punch. Disk-
    write failure rolls the whole transaction back so a punch never
    lives without its selfie when policy required one (and never has
    a stamped storage key pointing at nothing)."""
    if webp_bytes is None:
        return
    try:
        key = clock_selfie.write_selfie_bytes(
            user_id=user_id,
            punch_id=punch.id,
            webp_bytes=webp_bytes,
        )
    except SelfieStorageError as exc:
        db.rollback()
        _raise_for_selfie(exc)
    punch.selfie_storage_key = key
    db.flush()


@router.post("/in", response_model=PunchResponse)
async def post_clock_in(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
    client_latitude: Annotated[
        float | None, Form(ge=-90, le=90)
    ] = None,
    client_longitude: Annotated[
        float | None, Form(ge=-180, le=180)
    ] = None,
    client_accuracy_m: Annotated[
        float | None, Form(ge=0, le=100_000)
    ] = None,
    selfie: Annotated[UploadFile | None, File()] = None,
) -> PunchResponse:
    policy = _resolve_selfie_policy(db)
    webp = await _validate_selfie_part(selfie, policy)

    trusted_enabled, trusted_ips = _resolve_trusted_network(db)
    trusted_match = clock_in.is_ip_in_trusted_list(
        _real_client_ip(request), trusted_ips
    )

    try:
        punch = clock_in.punch_in(
            db,
            user=current_user,
            client_lat=client_latitude,
            client_lng=client_longitude,
            client_accuracy_m=client_accuracy_m,
            accuracy_buffer_max_m=_resolve_accuracy_buffer_max_m(db),
            trusted_network_match=trusted_match,
            trusted_network_enabled=trusted_enabled,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ClockInError as exc:
        db.rollback()
        _raise_for_clock(exc)

    _attach_selfie(db, punch=punch, webp_bytes=webp, user_id=current_user.id)
    db.commit()
    db.refresh(punch)
    return _to_punch_response(punch)


@router.post("/out", response_model=PunchResponse)
async def post_clock_out(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
    client_latitude: Annotated[
        float | None, Form(ge=-90, le=90)
    ] = None,
    client_longitude: Annotated[
        float | None, Form(ge=-180, le=180)
    ] = None,
    client_accuracy_m: Annotated[
        float | None, Form(ge=0, le=100_000)
    ] = None,
    selfie: Annotated[UploadFile | None, File()] = None,
) -> PunchResponse:
    policy = _resolve_selfie_policy(db)
    webp = await _validate_selfie_part(selfie, policy)

    _, trusted_ips = _resolve_trusted_network(db)
    trusted_match = clock_in.is_ip_in_trusted_list(
        _real_client_ip(request), trusted_ips
    )

    try:
        punch = clock_in.punch_out(
            db,
            user=current_user,
            client_lat=client_latitude,
            client_lng=client_longitude,
            client_accuracy_m=client_accuracy_m,
            trusted_network_match=trusted_match,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ClockInError as exc:
        db.rollback()
        _raise_for_clock(exc)

    _attach_selfie(db, punch=punch, webp_bytes=webp, user_id=current_user.id)
    db.commit()
    db.refresh(punch)
    return _to_punch_response(punch)


@router.get("/status", response_model=StatusResponse)
def get_clock_status(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
) -> StatusResponse:
    state, last = clock_in.current_status(db, current_user.id)
    today = clock_in.today_punches(db, current_user.id)
    trusted_enabled, trusted_ips = _resolve_trusted_network(db)
    trusted_match = clock_in.is_ip_in_trusted_list(
        _real_client_ip(request), trusted_ips
    )
    return StatusResponse(
        state=state,
        last_punch=_to_punch_response(last) if last is not None else None,
        today_punches=[_to_punch_response(p) for p in today],
        timezone=str(shop_tz()),
        business_date=business_date().isoformat(),
        selfie_policy=_resolve_selfie_policy(db),
        attendance_gate_enabled=_resolve_attendance_gate_enabled(db),
        trusted_network_enabled=trusted_enabled,
        trusted_network_detected=trusted_match,
    )
