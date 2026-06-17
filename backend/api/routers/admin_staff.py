"""Admin staff account actions that are not tied to sales PIN auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import password_reset

router = APIRouter()


def _get_staff_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="staff_user_not_found")
    return user


@router.post(
    "/{user_id}/send-password-reset",
    status_code=status.HTTP_204_NO_CONTENT,
)
def send_staff_password_reset(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin_scope),
) -> Response:
    """Send a reset link to another active admin user."""
    target = _get_staff_user(db, user_id)
    if target.role != "admin":
        raise HTTPException(status_code=422, detail="target_not_admin")
    if not target.is_active:
        raise HTTPException(status_code=409, detail="target_user_inactive")
    password_reset.request_reset_for_user(db, user=target)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
