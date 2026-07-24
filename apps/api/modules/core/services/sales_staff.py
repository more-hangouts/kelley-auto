"""Active-sales-user lookup, shared by Phase 5 walk-in assignment
and Phase 6 staff picker.

"Assignable" means active and role='sales'. PIN presence is NOT
required — a freshly-onboarded stylist who hasn't logged in yet can
still be the assignee on a walk-in the owner files on their behalf;
the owner mints the PIN later.

Active state is the only gate. Inactive (`is_active=False`) users are
deactivated stylists who shouldn't pick up new work even if their row
still exists for historical attribution.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import User


def list_assignable_sales_users(db: Session) -> list[User]:
    """Active sales users, ordered for picker display.

    Order: full_name (NULLS LAST), then username — stylists without a
    display name sort below those with one. Caller can re-sort if it
    needs something different (e.g. recency).
    """
    return (
        db.query(User)
        .filter(User.role == "sales")
        .filter(User.is_active.is_(True))
        .order_by(User.full_name.is_(None), User.full_name, User.username)
        .all()
    )


def is_assignable_sales_user(db: Session, user_id: int) -> bool:
    """True iff `user_id` is an active sales user.

    Used as the validation gate on routes that accept an
    `assigned_user_id` from the client — we never trust the client's
    word that the id belongs to a sales user.
    """
    row = db.get(User, user_id)
    if row is None:
        return False
    return row.role == "sales" and bool(row.is_active)
