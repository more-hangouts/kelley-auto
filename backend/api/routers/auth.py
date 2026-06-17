from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.cookies import ADMIN_SURFACE, clear_session_cookies, set_session_cookies
from api.redis_rate_limit import enforce_or_raise, rate_limit
from database.auth import (
    bump_token_version,
    create_access_token,
    require_admin_scope,
    verify_password,
)
from database.connection import SessionLocal, get_db
from database.models import User
from services import password_reset

router = APIRouter()

# Per-IP rate limit on /login. Layered with nginx's `login` zone and the
# per-email check below. 10/min is generous for shared office NAT; the
# per-email bucket is the tighter brute-force shield.
_login_ip_limit = rate_limit(bucket="login_ip", limit=10, window=60)

# D4 password reset limits. Request: per-IP defends a single attacker
# enumerating against many emails; per-email defends an attacker
# spraying a known account to flood the user's inbox. Confirm: per-IP
# only — token brute-force is computationally infeasible at 256 bits.
_reset_request_ip_limit = rate_limit(
    bucket="password_reset_ip", limit=10, window=60
)
_reset_confirm_ip_limit = rate_limit(
    bucket="password_reset_confirm_ip", limit=10, window=60
)

_INVALID_CREDS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: str | None
    role: str
    permissions: list

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            permissions=user.permissions or [],
        )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserOut


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(_login_ip_limit)],
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    # Per-email bucket: tightens brute-force across IPs against one account.
    # Always counts, even for non-existent emails, so the 429 response cannot
    # be used to enumerate registered emails.
    enforce_or_raise(
        bucket="login_email",
        scoped=payload.email.lower().strip(),
        limit=5,
        window=60,
        request=request,
    )

    user = (
        db.query(User)
        .filter(func.lower(User.email) == payload.email.lower())
        .first()
    )
    if user is None:
        raise _INVALID_CREDS
    if not verify_password(payload.password, user.hashed_password):
        raise _INVALID_CREDS
    if not user.is_active:
        raise _INVALID_CREDS

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(user)
    # D3: issue HttpOnly session + readable CSRF cookies. The browser
    # path will rely on these on subsequent requests; the response body
    # still carries `access_token` so smokes and any header-bearer
    # clients keep working through the transition.
    set_session_cookies(response, surface=ADMIN_SURFACE, jwt_token=token)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        user=UserOut.from_user(user),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(require_admin_scope)) -> UserOut:
    """Admin-surface /me. Sales tokens get 403 here; sales staff use
    `/api/sales/auth/me` which is gated on `require_sales_scope`.
    Keeping the two surfaces strictly separated avoids cross-reads
    from a token issued for the other side."""
    return UserOut.from_user(current_user)


class PasswordResetRequestPayload(BaseModel):
    email: EmailStr


class PasswordResetConfirmPayload(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    # bcrypt truncates at 72 bytes; we surface a min that matches our
    # security baseline. The bcrypt shim in database.auth handles the
    # upper boundary regardless of what the user types here.
    new_password: str = Field(min_length=12, max_length=256)


# Uniform error for every confirm-side failure mode (missing token,
# already used, expired, malformed, deactivated user). Keeping a
# single error shape prevents a caller from probing token state via
# differential responses.
_RESET_INVALID_EXC = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="reset_invalid_or_expired",
)


@router.post(
    "/password-reset/request",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_reset_request_ip_limit)],
)
def request_password_reset(
    payload: PasswordResetRequestPayload,
    background: BackgroundTasks,
    request: Request,
) -> None:
    """Begin a password reset for the supplied email.

    Returns 204 in every case — existing email, non-existent email,
    deactivated user — so the response cannot be used to enumerate
    accounts. The email-send happens in a background task so the
    request returns at sub-millisecond latency regardless of which
    branch ran, shrinking the timing channel the per-email rate
    limit was already covering.
    """
    # Per-email bucket: always counts before the user lookup, so
    # 429 cannot be used to enumerate registered emails (mirrors
    # B2 /login, B3 /confirm patterns). Tight at 3/min so a single
    # email cannot be mailbombed via repeated reset requests.
    enforce_or_raise(
        bucket="password_reset_email",
        scoped=str(payload.email).strip().lower(),
        limit=3,
        window=60,
        request=request,
    )
    # Background task pulls its own SessionLocal — never reuse the
    # request-scoped `db` after the route returns. Anti-enumeration
    # demands the route itself does no DB work that varies by branch.
    background.add_task(_run_request_reset_in_background, str(payload.email))


def _run_request_reset_in_background(email: str) -> None:
    db = SessionLocal()
    try:
        password_reset.request_reset(db, email=email)
    finally:
        db.close()


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_reset_confirm_ip_limit)],
)
def confirm_password_reset(
    payload: PasswordResetConfirmPayload,
    db: Session = Depends(get_db),
) -> None:
    """Consume a reset token + set the user's new password.

    Every failure mode collapses to a single 400 with
    `detail='reset_invalid_or_expired'`. On success, the user's
    `token_version` is bumped so every existing JWT for this account
    dies (incident-response by design — a reset implies the previous
    sessions might be compromised). The user must log in again to
    pick up a fresh token under the new version.
    """
    try:
        password_reset.confirm_reset(
            db, token=payload.token, new_password=payload.new_password
        )
    except password_reset.ConfirmResetFailure:
        raise _RESET_INVALID_EXC


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    current_user: User = Depends(require_admin_scope),
    db: Session = Depends(get_db),
) -> Response:
    """Server-side logout for admin tokens.

    Phase D2: bumps `users.token_version` so every JWT currently issued
    to this user — admin or sales scope — becomes 401 on next request.
    Idempotent in the sense that a second call from the now-stale token
    will fail at the auth dependency with 401, which is exactly what we
    want (the second click can't accidentally bump twice and confuse
    a still-active session on another device).

    D3: also clears the admin session + CSRF cookies. Order matters
    only loosely — the cookie clear is the browser-visible signal; the
    token_version bump is the authoritative revocation that survives
    a malicious replay of the just-set-empty cookie.
    """
    bump_token_version(db, current_user)
    clear_session_cookies(response, surface=ADMIN_SURFACE)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
