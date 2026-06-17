"""Sales-portal authentication endpoints.

Stylist PIN login, account lookup, and change-PIN. The admin-side
"reset a stylist's PIN" endpoints live in `api/routers/admin_sales_staff.py`
because they belong to the owner's surface, not to /api/sales/*.

The PIN flow returns uniform 401 responses on bad-identifier and
bad-PIN cases so the response cannot be used to enumerate which
stylist accounts exist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.cookies import SALES_SURFACE, clear_session_cookies, set_session_cookies
from api.redis_rate_limit import enforce_or_raise, rate_limit
from database.auth import (
    SALES_SCOPE,
    bump_token_version,
    create_sales_token,
    require_sales_scope,
)
from database.connection import get_db
from database.models import User
from services import sales_auth

router = APIRouter()

# Per-IP rate limit on /auth/pin. Layered with the per-row lockout in
# services/sales_auth (5 attempts triggers 15-minute lock) and the
# per-identifier bucket below. 10/min is generous for shared office NAT;
# the per-identifier bucket tightens brute-force on a known username.
_pin_ip_limit = rate_limit(bucket="pin_ip", limit=10, window=60)

_INVALID_PIN_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid_pin",
)


class PinLoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    pin: str = Field(min_length=sales_auth.PIN_LENGTH, max_length=sales_auth.PIN_LENGTH)


class SalesUserOut(BaseModel):
    id: int
    username: str
    full_name: str | None
    role: str
    force_pin_change: bool


class PinLoginResponse(BaseModel):
    access_token: str
    token_type: str
    scope: str
    user: SalesUserOut
    force_pin_change: bool


class ChangePinRequest(BaseModel):
    current_pin: str = Field(min_length=sales_auth.PIN_LENGTH, max_length=sales_auth.PIN_LENGTH)
    new_pin: str = Field(min_length=sales_auth.PIN_LENGTH, max_length=sales_auth.PIN_LENGTH)


class StaffPickerRow(BaseModel):
    """Public picker row.

    Deliberately omits `users.id` per the Phase 1 design lock-in
    ("Do not expose sequential user_ids as the primary login handle").
    The frontend submits `username` as the `identifier` on POST
    `/auth/pin`. `full_name` falls back to `username` when not set so
    the picker tile is never blank.
    """

    username: str
    full_name: str


@router.get("/auth/staff-picker", response_model=list[StaffPickerRow])
def staff_picker(
    db: Annotated[Session, Depends(get_db)],
) -> list[StaffPickerRow]:
    """Unauthenticated picker for the kiosk-style PIN login.

    Returns active sales users who have a PIN minted, ordered by
    display name. Stylists tap their tile and the UI submits their
    `username` as the `identifier` on the subsequent PIN POST.

    No `users.id` in the response. Per the design doc: "If the UI
    shows a staff picker, still rate-limit and lock per row" — that
    rate-limit is enforced on `/auth/pin` (nginx zone `sales_pin`),
    not here. This read is non-sensitive (boutique stylists are
    public-facing names) but should still be rate-limited at the
    nginx layer to keep automated probes from scraping the roster.
    """
    rows = (
        db.query(User)
        .filter(User.role == "sales")
        .filter(User.is_active.is_(True))
        .filter(User.pin_hash.isnot(None))
        .order_by(User.full_name.is_(None), User.full_name, User.username)
        .all()
    )
    return [
        StaffPickerRow(
            username=u.username,
            full_name=u.full_name or u.username,
        )
        for u in rows
    ]


@router.post(
    "/auth/pin",
    response_model=PinLoginResponse,
    dependencies=[Depends(_pin_ip_limit)],
)
def pin_login(
    payload: PinLoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> PinLoginResponse:
    """Exchange `{identifier, pin}` for a sales-scope JWT.

    Returns 401 with detail `invalid_pin` for missing user, no PIN
    set, or wrong PIN. Returns 423 with `Retry-After` when the row is
    locked. Format errors return 422 from Pydantic.
    """
    # Per-identifier bucket: tightens brute-force across IPs against one
    # account. Set to 10/min so the existing 5-attempt row lockout
    # (services/sales_auth) fires first on bad-PIN sequences and the
    # client sees the more informative 423 + Retry-After. This rate
    # limit is defense-in-depth: it bites when row-lockout state somehow
    # cannot be updated (e.g., Redis is the only consistent layer).
    # Always counts even for non-existent identifiers so the 429 cannot
    # be used to enumerate which usernames exist.
    enforce_or_raise(
        bucket="pin_identifier",
        scoped=payload.identifier.lower().strip(),
        limit=10,
        window=60,
        request=request,
    )

    try:
        sales_auth.validate_pin_format(payload.pin)
    except sales_auth.InvalidPinFormat:
        raise _INVALID_PIN_EXC

    user = sales_auth.find_pin_user_by_identifier(db, payload.identifier)
    if user is None:
        raise _INVALID_PIN_EXC

    try:
        result = sales_auth.verify_pin(db, user, payload.pin)
    except sales_auth.PinAccountLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="pin_locked",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except sales_auth.PinError:
        db.commit()  # persist failure-counter / lockout state
        raise _INVALID_PIN_EXC

    db.commit()
    db.refresh(user)

    token = create_sales_token(user)
    # D3: same dual-issue as admin /login — set HttpOnly session + readable
    # CSRF cookies for the browser path, keep `access_token` in the body
    # for header-bearer transition clients (smokes / curl).
    set_session_cookies(response, surface=SALES_SURFACE, jwt_token=token)
    return PinLoginResponse(
        access_token=token,
        token_type="bearer",
        scope=SALES_SCOPE,
        user=SalesUserOut(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            force_pin_change=bool(result.force_change),
        ),
        force_pin_change=bool(result.force_change),
    )


@router.get("/auth/me", response_model=SalesUserOut)
def me(
    current_user: Annotated[User, Depends(require_sales_scope)],
) -> SalesUserOut:
    return SalesUserOut(
        id=current_user.id,
        username=current_user.username,
        full_name=current_user.full_name,
        role=current_user.role,
        force_pin_change=bool(current_user.force_pin_change),
    )


@router.post("/auth/logout", status_code=204)
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
) -> Response:
    """Server-side logout for sales tokens.

    Phase D2: bumps `users.token_version` so every JWT currently issued
    to this user becomes 401 on next request. Mirrors the admin
    `/api/auth/logout` route — same revocation primitive, same 204
    response shape. The next stylist sign-in re-enters the PIN flow.

    D3: also clears the sales session + CSRF cookies on the way out.
    """
    bump_token_version(db, current_user)
    clear_session_cookies(response, surface=SALES_SURFACE)
    response.status_code = 204
    return response


@router.post("/auth/kiosk-lock", status_code=204)
def kiosk_lock(response: Response) -> Response:
    """Clear sales session + CSRF cookies on this device only.

    Unlike `/auth/logout`, this does NOT bump `users.token_version`.
    Shared tablets need a quick "lock and let the next stylist sign in"
    affordance; bumping token_version would silently sign the stylist
    out of every other device they touched today (their phone, another
    tablet, etc.). The cookie clear is local to whichever browser is
    making this request.

    Idempotent and unauthenticated by design — a tablet that has
    already drifted off a valid session can still ask the server to
    overwrite its stale cookies with empty Max-Age=0 cookies. Returns
    204 either way.
    """
    clear_session_cookies(response, surface=SALES_SURFACE)
    response.status_code = 204
    return response


@router.post("/auth/change-pin", status_code=204, response_class=Response)
def change_pin(
    payload: ChangePinRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_sales_scope)],
) -> Response:
    """Stylist-initiated PIN change.

    The current PIN must verify before the new one is accepted. This
    is the only path that clears `force_pin_change`. The endpoint runs
    behind `require_sales_scope`, so a force-change-required user has
    already authenticated; the lockout/failure logic still applies if
    they fat-finger the current PIN repeatedly.
    """
    if payload.new_pin == payload.current_pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_pin_must_differ",
        )

    try:
        sales_auth.validate_pin_format(payload.new_pin)
    except sales_auth.InvalidPinFormat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pin_must_be_6_digits",
        )

    try:
        sales_auth.verify_pin(db, current_user, payload.current_pin)
    except sales_auth.PinAccountLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="pin_locked",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except sales_auth.PinError:
        db.commit()
        raise _INVALID_PIN_EXC

    sales_auth.set_pin(db, current_user, payload.new_pin, force_change=False)
    db.commit()
    return Response(status_code=204)
