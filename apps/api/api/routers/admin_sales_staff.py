"""Admin-side staff management.

Owner workflows for the staff roster: list everyone, create a new
staff profile (sales by default), edit name / role / active / wage /
commission, and mint / clear PINs for sales users.

Endpoint shape:

  GET    /                       — all roles, ordered by name
  POST   /                       — create (role defaults to 'sales')
  PATCH  /{id}                   — edit mutable profile + compensation
  POST   /{id}/pin               — mint a fresh PIN (sales users only)
  DELETE /{id}/pin               — clear PIN auth (sales users only)
  POST   /{id}/unlock            — clear PIN lockout (sales users only)

PIN endpoints stay scoped to `role='sales'` so a stray request can't
mint a PIN onto an admin account (admins log in by password). The
profile + compensation endpoints work across roles so the manager
can edit anyone's wage from the same screen.

`hourly_wage` and `commission_rate` are returned ONLY from this
admin-scoped router. The sales `/api/sales/auth/me` surface and the
public staff picker deliberately omit them — see SalesUserOut /
StaffPickerRow in `api/routers/sales_auth.py`.

All endpoints gate on `require_admin_scope`; a sales token gets 403.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.auth import hash_password, require_admin_scope
from database.connection import get_db
from database.models import User
from services import sales_auth
from services.email_transport import EmailMessagePayload

router = APIRouter()
log = logging.getLogger(__name__)


# Roles managers can pick from in the Staff Profiles UI. Kept narrow
# on purpose — the access scopes elsewhere only know about these
# three values, and a stray role string would silently lock the user
# out of every gated surface.
ALLOWED_ROLES: frozenset[str] = frozenset({"admin", "sales", "user"})


class SalesStaffOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    has_pin: bool
    force_pin_change: bool
    pin_locked: bool
    last_pin_used_at: str | None
    last_login: str | None
    # Compensation. Stored as Numeric in PG; emitted as float for
    # straightforward JSON. `commission_rate` is the decimal fraction
    # (0.075 == 7.5%); the UI multiplies by 100 for display.
    hourly_wage: float | None
    commission_rate: float | None
    is_archived: bool
    deleted_at: str | None

    @classmethod
    def from_user(cls, user: User) -> "SalesStaffOut":
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            is_archived=user.deleted_at is not None,
            deleted_at=(
                user.deleted_at.isoformat()
                if user.deleted_at is not None
                else None
            ),
            has_pin=user.pin_hash is not None,
            force_pin_change=bool(user.force_pin_change),
            pin_locked=sales_auth.is_locked(user),
            last_pin_used_at=(
                user.last_pin_used_at.isoformat()
                if user.last_pin_used_at is not None
                else None
            ),
            last_login=(
                user.last_login.isoformat()
                if user.last_login is not None
                else None
            ),
            hourly_wage=(
                float(user.hourly_wage)
                if user.hourly_wage is not None
                else None
            ),
            commission_rate=(
                float(user.commission_rate)
                if user.commission_rate is not None
                else None
            ),
        )


class SalesStaffCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=2, max_length=100)
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role: str = "sales"
    hourly_wage: float | None = None
    commission_rate: float | None = None


class SalesStaffPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = None
    is_active: bool | None = None
    hourly_wage: float | None = None
    commission_rate: float | None = None


class PinMintResponse(BaseModel):
    pin: str
    user: SalesStaffOut


class ArchiveStaffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


def _validate_role(value: str) -> str:
    if value not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_role", "allowed": sorted(ALLOWED_ROLES)},
        )
    return value


def _coerce_hourly_wage(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_hourly_wage"},
        ) from exc
    if d < 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_hourly_wage"},
        )
    # NUMERIC(10, 2) — quantize to two decimal places so the CHECK and
    # the wire format agree about cents.
    return d.quantize(Decimal("0.01"))


def _coerce_commission_rate(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_commission_rate"},
        ) from exc
    if d < 0 or d > 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_commission_rate"},
        )
    # NUMERIC(5, 4) — four decimal places of precision matches the
    # storage column exactly.
    return d.quantize(Decimal("0.0001"))


def _list_staff(db: Session, *, archived: bool = False) -> list[User]:
    q = db.query(User)
    if archived:
        q = q.filter(User.deleted_at.is_not(None))
    else:
        q = q.filter(User.deleted_at.is_(None))
    return q.order_by(
        User.full_name.is_(None), User.full_name, User.username
    ).all()


def _get_staff(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="staff_user_not_found")
    return user


def _get_sales_user(db: Session, user_id: int) -> User:
    """PIN endpoints stay scoped to role='sales' — minting a PIN onto
    an admin would silently enable an unintended auth path."""
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .filter(User.role == "sales")
        .first()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="sales_user_not_found")
    return user


def _send_role_changed_email_safe(
    *,
    staff_user: User,
    old_role: str,
    new_role: str,
    changed_by: User,
) -> None:
    if not staff_user.email:
        return
    try:
        from services import email_transport
        from services.notification_templates import render_role_changed

        rendered = render_role_changed(
            staff_user=staff_user,
            old_role=old_role,
            new_role=new_role,
            changed_by=changed_by,
        )
        email_transport.get_email_transport().send(
            EmailMessagePayload(
                to=staff_user.email,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "admin_sales_staff.role_changed_email_failed user_id=%s",
            staff_user.id,
        )


@router.get("", response_model=list[SalesStaffOut])
def list_sales_staff(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    archived: bool = False,
) -> list[SalesStaffOut]:
    """Active roster by default; `?archived=true` returns the archived
    (soft-deleted) staff for the restore view."""
    return [
        SalesStaffOut.from_user(u)
        for u in _list_staff(db, archived=archived)
    ]


@router.post("", response_model=SalesStaffOut, status_code=201)
def create_sales_staff(
    payload: SalesStaffCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> SalesStaffOut:
    """Create a staff user. Defaults to role='sales' for backwards
    compatibility with the existing "Add stylist" flow.

    No PIN is set here — call `POST /{id}/pin` to mint one (sales
    users only). `hashed_password` is set to a random unusable hash
    so password-login never accepts this account until a password is
    set through a separate reset flow.
    """
    username = payload.username.strip()
    email = payload.email.lower()
    full_name = payload.full_name.strip() if payload.full_name else None
    role = _validate_role(payload.role)
    hourly_wage = _coerce_hourly_wage(payload.hourly_wage)
    commission_rate = _coerce_commission_rate(payload.commission_rate)

    existing_username = (
        db.query(User).filter(func.lower(User.username) == username.lower()).first()
    )
    if existing_username is not None:
        raise HTTPException(status_code=409, detail="username_taken")
    existing_email = (
        db.query(User).filter(func.lower(User.email) == email).first()
    )
    if existing_email is not None:
        raise HTTPException(status_code=409, detail="email_taken")

    # Hash a random string we throw away — keeps the column non-null
    # and guarantees the password-login path rejects this account.
    placeholder_password = secrets.token_urlsafe(32)
    user = User(
        username=username,
        email=email,
        full_name=full_name,
        hashed_password=hash_password(placeholder_password),
        is_active=True,
        role=role,
        permissions=[],
        hourly_wage=hourly_wage,
        commission_rate=commission_rate,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return SalesStaffOut.from_user(user)


@router.patch("/{user_id}", response_model=SalesStaffOut)
def patch_sales_staff(
    user_id: int,
    payload: SalesStaffPatchRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> SalesStaffOut:
    """Update mutable profile + compensation fields.

    Username / email uniqueness is enforced case-insensitively, the
    same way create does it. Role changes go through `_validate_role`.
    `hourly_wage` and `commission_rate` round-trip through the same
    coercers as create so the validation surface stays consistent.
    """
    sent = payload.model_fields_set
    if not sent:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )

    user = _get_staff(db, user_id)
    old_role = user.role

    if "username" in sent:
        new_username = (payload.username or "").strip()
        if not new_username:
            raise HTTPException(status_code=422, detail="username_required")
        existing = (
            db.query(User)
            .filter(func.lower(User.username) == new_username.lower())
            .filter(User.id != user.id)
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="username_taken")
        user.username = new_username

    if "email" in sent:
        new_email = (payload.email or "").lower()
        if not new_email:
            raise HTTPException(status_code=422, detail="email_required")
        existing = (
            db.query(User)
            .filter(func.lower(User.email) == new_email)
            .filter(User.id != user.id)
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="email_taken")
        user.email = new_email

    if "full_name" in sent:
        full = (payload.full_name or "").strip()
        user.full_name = full or None

    if "role" in sent:
        user.role = _validate_role(payload.role)

    if "is_active" in sent:
        user.is_active = bool(payload.is_active)

    if "hourly_wage" in sent:
        user.hourly_wage = _coerce_hourly_wage(payload.hourly_wage)

    if "commission_rate" in sent:
        user.commission_rate = _coerce_commission_rate(payload.commission_rate)

    db.commit()
    db.refresh(user)
    if "role" in sent and old_role != user.role:
        _send_role_changed_email_safe(
            staff_user=user,
            old_role=old_role,
            new_role=user.role,
            changed_by=_admin,
        )
    return SalesStaffOut.from_user(user)


@router.post("/{user_id}/pin", response_model=PinMintResponse)
def mint_pin_for_sales_staff(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> PinMintResponse:
    """Mint a fresh 6-digit PIN. Returned ONCE in plaintext.

    Always sets `force_pin_change = TRUE` — the stylist must change
    it on first login. Idempotent against repeated owner clicks: each
    call overwrites the prior PIN (intentional; "the owner clicked
    reset" should always invalidate the prior PIN).
    """
    user = _get_sales_user(db, user_id)
    new_pin = sales_auth.generate_pin()
    sales_auth.set_pin(db, user, new_pin, force_change=True)
    db.commit()
    db.refresh(user)
    return PinMintResponse(pin=new_pin, user=SalesStaffOut.from_user(user))


@router.delete("/{user_id}/pin", status_code=204, response_class=Response)
def clear_pin_for_sales_staff(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    """Wipe PIN auth state.

    The stylist can no longer PIN-login until the owner mints a new
    PIN. Useful when a stylist leaves or when an iPad is lost.
    """
    user = _get_sales_user(db, user_id)
    sales_auth.clear_pin(db, user)
    db.commit()
    return Response(status_code=204)


@router.post("/{user_id}/unlock", response_model=SalesStaffOut)
def unlock_pin_lockout(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> SalesStaffOut:
    """Clear an active PIN lockout without changing the PIN."""
    user = _get_sales_user(db, user_id)
    user.pin_locked_until = None
    user.pin_failed_count = 0
    db.commit()
    db.refresh(user)
    return SalesStaffOut.from_user(user)


def _active_admin_count(db: Session) -> int:
    return (
        db.query(User)
        .filter(User.role == "admin")
        .filter(User.is_active.is_(True))
        .filter(User.deleted_at.is_(None))
        .count()
    )


@router.post("/{user_id}/archive", response_model=SalesStaffOut)
def archive_sales_staff(
    user_id: int,
    payload: ArchiveStaffRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> SalesStaffOut:
    """Archive (soft delete) a staff member: hide them from the roster
    and block login/PIN + scheduling via `is_active=False`, while keeping
    all their history. Reversible through `/restore`.

    Guards: an admin can't archive their own account, and the last active
    admin can't be archived (avoids locking everyone out)."""
    user = _get_staff(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=409, detail={"code": "already_archived"}
        )
    if user.id == admin.id:
        raise HTTPException(
            status_code=409, detail={"code": "cannot_archive_self"}
        )
    if (
        user.role == "admin"
        and user.is_active
        and _active_admin_count(db) <= 1
    ):
        raise HTTPException(
            status_code=409, detail={"code": "last_active_admin"}
        )

    user.deleted_at = datetime.now(timezone.utc)
    user.deleted_by_user_id = admin.id
    user.deleted_reason = (payload.reason or "").strip() or None
    user.is_active = False
    # Invalidate any live session/token for the archived account.
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(user)
    return SalesStaffOut.from_user(user)


@router.post("/{user_id}/restore", response_model=SalesStaffOut)
def restore_sales_staff(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> SalesStaffOut:
    """Restore an archived staff member to the active roster. Clears the
    soft-delete and reactivates the account (PIN, if any, is untouched)."""
    user = _get_staff(db, user_id)
    if user.deleted_at is None:
        raise HTTPException(status_code=409, detail={"code": "not_archived"})
    user.deleted_at = None
    user.deleted_by_user_id = None
    user.deleted_reason = None
    user.is_active = True
    db.commit()
    db.refresh(user)
    return SalesStaffOut.from_user(user)
