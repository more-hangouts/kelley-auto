"""Admin self-service account management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.cookies import ADMIN_SURFACE, set_session_cookies
from database.auth import (
    create_access_token,
    hash_password,
    require_admin_scope,
    verify_password,
)
from database.connection import get_db
from database.models import User
from services.password_reset import notify_password_changed

router = APIRouter()


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_scope),
) -> Response:
    """Set the current admin user's password after verifying the old one."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="current_password_incorrect",
        )

    current_user.hashed_password = hash_password(payload.new_password)
    # Revoke every existing admin/sales JWT for this user, then issue a
    # fresh admin session cookie so the browser that changed the password
    # can keep working without bouncing through login.
    current_user.token_version = (current_user.token_version or 0) + 1
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    # Phase 12.1 security tripwire: any password mutation leaves the
    # user an out-of-band confirmation so a hijacked-session change
    # surfaces immediately. Mirrors the reset-confirm path's pattern
    # (best-effort direct SMTP; failures are logged but never raise).
    notify_password_changed(current_user)

    set_session_cookies(
        response,
        surface=ADMIN_SURFACE,
        jwt_token=create_access_token(current_user),
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
