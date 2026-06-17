import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from api.cookies import (
    ADMIN_SESSION_COOKIE,
    ADMIN_SURFACE,
    SALES_SESSION_COOKIE,
    SALES_SURFACE,
)
from config.settings import ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY
from database.connection import get_db
from database.models import User

log = logging.getLogger(__name__)

ALGORITHM = "HS256"

# Allowed scopes minted by this module. Any other value in a token's
# `scope` claim is treated as malformed and rejected.
ADMIN_SCOPE = "admin"
SALES_SCOPE = "sales"
_VALID_SCOPES = frozenset({ADMIN_SCOPE, SALES_SCOPE})

# `User.role` → token scope. Migration 052 forces re-login on every
# user, so every active token after the cutover carries a scope.
_ROLE_TO_SCOPE = {
    "admin": ADMIN_SCOPE,
    "user": ADMIN_SCOPE,  # legacy non-sales staff keep admin-surface access
    "sales": SALES_SCOPE,
}

# Phase D6: retired `passlib[bcrypt]==1.7.4` in favor of direct
# `bcrypt==5.0.0`. The bcrypt algorithm itself is unchanged
# (`$2b$12$...`), and 5.0.0 reads/writes hashes that are wire-compatible
# with what passlib used to produce — verified by the D6 smoke against
# real prod-shaped hashes before the swap shipped.
_BCRYPT_COST = 12  # Matches every existing hash on prod; do not change without rehash.
_BCRYPT_MAX_PASSWORD_BYTES = 72  # bcrypt's fixed limit on input length.


def _to_bcrypt_bytes(password: str) -> bytes:
    """Encode and truncate to bcrypt's 72-byte input limit.

    Passlib silently truncated > 72-byte passwords; bcrypt 5.0 raises
    `ValueError` instead. We pre-truncate here to preserve the passlib
    contract exactly — every existing hash on disk was computed from
    `password.encode('utf-8')[:72]`, so the same shim on verify
    reproduces the original input. Pre-D6 long-password users keep
    authenticating with no rehash.
    """
    return password.encode("utf-8")[:_BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(
        _to_bcrypt_bytes(plain),
        bcrypt.gensalt(rounds=_BCRYPT_COST),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify a password against a stored bcrypt hash.

    Fails closed on every kind of malformed hash (empty string, garbled
    bytes, truncated, embedded null) by catching the umbrella `Exception`
    and returning False. Logs the underlying exception type so an
    operational anomaly (e.g. a corrupted column) surfaces in
    journalctl without breaking auth for that user.
    """
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "password.verify_failed",
            extra={"error_type": type(exc).__name__},
        )
        return False


def _scope_for_user(user: User) -> str:
    return _ROLE_TO_SCOPE.get(user.role, ADMIN_SCOPE)


def create_access_token(user: User, *, scope: str | None = None) -> str:
    """Mint a JWT for a user.

    `scope` defaults to the role-derived value (`admin` for admin/user
    roles, `sales` for sales). Pass an explicit scope only when the
    caller knows it should differ from the role-derived default —
    e.g. `create_sales_token` always emits `sales` regardless of role.
    """
    if scope is None:
        scope = _scope_for_user(user)
    if scope not in _VALID_SCOPES:
        raise ValueError(f"invalid scope: {scope}")

    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user.id),
        "tv": user.token_version,
        "scope": scope,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def create_sales_token(user: User) -> str:
    """Mint a sales-scope JWT.

    Used by the PIN-login path. Refuses to mint for non-sales users so
    a misuse from the admin path cannot accidentally produce a sales
    token for an admin account.
    """
    if user.role != "sales":
        raise ValueError("only role='sales' users can hold a sales token")
    return create_access_token(user, scope=SALES_SCOPE)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

_SCOPE_FORBIDDEN_EXC = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="scope_forbidden",
)


def _bearer_token_from_header(request: Request) -> str | None:
    """Return the bearer token from an `Authorization` header, if present."""
    raw = request.headers.get("authorization")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None


def _origin_prefers_sales(request: Request) -> bool:
    """Heuristic for which surface a same-eTLD ambiguity should resolve to.

    Used only when a browser is carrying BOTH the admin and sales session
    cookies at once (e.g. an owner who also signs in as a stylist on the
    same machine). The `Origin` and `Referer` headers identify which
    subdomain the page making the request is hosted on; if either points
    at `sales.*`, the sales cookie wins. All other cases (no overlap, no
    Origin header, an admin Origin) fall through to admin.
    """
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        # Case-insensitive substring check is enough: `sales.` only
        # appears in Origin/Referer when the request came from a
        # `sales.*` host. The trailing dot guards against a future
        # `salesreports.*` style hostname accidentally winning.
        if "://sales." in value.lower():
            return True
    return False


def resolve_request_token(request: Request) -> tuple[str, str]:
    """Pick the auth token for a request and report its source.

    Returns `(token, source)` where `source` is one of:
      - `"cookie:admin"`  — token came from the admin session cookie
      - `"cookie:sales"`  — token came from the sales session cookie
      - `"header"`        — token came from `Authorization: Bearer`

    Order: cookies first (the browser path), header fallback (smokes,
    curl, and any transitional clients still attaching `Authorization`).
    Raises 401 if no recognizable token is present at all.
    """
    admin_cookie = request.cookies.get(ADMIN_SESSION_COOKIE)
    sales_cookie = request.cookies.get(SALES_SESSION_COOKIE)
    if admin_cookie and sales_cookie:
        if _origin_prefers_sales(request):
            return sales_cookie, f"cookie:{SALES_SURFACE}"
        return admin_cookie, f"cookie:{ADMIN_SURFACE}"
    if admin_cookie:
        return admin_cookie, f"cookie:{ADMIN_SURFACE}"
    if sales_cookie:
        return sales_cookie, f"cookie:{SALES_SURFACE}"
    header_token = _bearer_token_from_header(request)
    if header_token:
        return header_token, "header"
    raise _CREDENTIALS_EXC


def _decode_and_validate(token: str, db: Session) -> tuple[User, str]:
    """Decode a JWT, load the user, and return `(user, scope)`.

    Raises 401 on any decode/lookup failure. The returned scope is
    one of the values in `_VALID_SCOPES`; tokens with a missing or
    unknown scope are rejected at this layer.
    """
    try:
        claims = decode_access_token(token)
    except InvalidTokenError:
        # D5: PyJWT's umbrella exception replaces jose.JWTError. Subclasses
        # cover every rejection path: ExpiredSignatureError, DecodeError
        # (malformed), InvalidSignatureError (wrong secret), and
        # InvalidAlgorithmError (alg=none, wrong alg). All collapse to the
        # same generic 401 we used to emit under jose, so the public
        # error contract is unchanged.
        raise _CREDENTIALS_EXC

    sub = claims.get("sub")
    tv = claims.get("tv")
    scope = claims.get("scope")
    if sub is None or tv is None or scope not in _VALID_SCOPES:
        raise _CREDENTIALS_EXC

    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise _CREDENTIALS_EXC

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise _CREDENTIALS_EXC
    if user.token_version != tv:
        raise _CREDENTIALS_EXC
    if not user.is_active:
        raise _CREDENTIALS_EXC

    return user, scope


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Return the user behind the request's auth token, regardless of scope.

    D3: reads the session cookie first, falls back to the `Authorization`
    header so smokes / scripts / any pre-cookie clients still work. Use
    one of `require_admin_scope`, `require_sales_scope`, or
    `require_any_scope(...)` in the dependency tree to gate by surface
    access; this dependency only authenticates.
    """
    token, _source = resolve_request_token(request)
    user, _scope = _decode_and_validate(token, db)
    return user


def get_current_user_with_scope(
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[User, str]:
    """Same as `get_current_user` but also returns the JWT's scope."""
    token, _source = resolve_request_token(request)
    return _decode_and_validate(token, db)


def require_admin_scope(
    bundle: tuple[User, str] = Depends(get_current_user_with_scope),
) -> User:
    """Require an admin-scoped token.

    Sales tokens get a 403, regardless of which subdomain the request
    came from. Admin/legacy `user` tokens pass.
    """
    user, scope = bundle
    if scope != ADMIN_SCOPE:
        raise _SCOPE_FORBIDDEN_EXC
    return user


def require_sales_scope(
    bundle: tuple[User, str] = Depends(get_current_user_with_scope),
) -> User:
    """Require a sales-scoped token.

    Admin tokens get a 403 here so admin-side accidents (a logged-in
    owner hitting a stylist-only endpoint by mistake) are surfaced
    immediately rather than silently authorized.
    """
    user, scope = bundle
    if scope != SALES_SCOPE:
        raise _SCOPE_FORBIDDEN_EXC
    return user


def bump_token_version(db: Session, user: User) -> None:
    """Invalidate every JWT currently issued to `user`.

    Phase D2: increments `users.token_version`. Existing tokens carry
    the pre-bump version in their `tv` claim, so `_decode_and_validate`
    rejects them with 401 on the next request. Same mechanism is used
    by future "log out everywhere," role downgrade, and incident-
    response flows — bumping is the universal revocation primitive.

    Commits the change so the bump is durable even if the calling
    route's outer transaction rolls back: a logout that bumped the
    counter must not be undone by a downstream error in the same
    request.
    """
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()


def require_any_scope(*allowed: str):
    """Build a dependency that allows multiple scopes.

    Used sparingly — only on endpoints where admin and sales reuse is
    deliberate (today: `POST /api/sales/events/{event_id}/participants`,
    which Phase 6 generalizes to `POST /api/events/{event_id}/participants`).
    """
    if not allowed:
        raise ValueError("require_any_scope needs at least one scope")
    allowed_set = frozenset(allowed)
    if not allowed_set.issubset(_VALID_SCOPES):
        raise ValueError(f"unknown scopes in {allowed!r}")

    def _dep(
        bundle: tuple[User, str] = Depends(get_current_user_with_scope),
    ) -> User:
        user, scope = bundle
        if scope not in allowed_set:
            raise _SCOPE_FORBIDDEN_EXC
        return user

    return _dep
