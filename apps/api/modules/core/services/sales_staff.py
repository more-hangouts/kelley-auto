"""Assignable-user lookup, shared by Phase 5 walk-in assignment
and Phase 6 staff picker.

"Assignable" means active and role in ('sales', 'admin'). PIN presence
is NOT required — a freshly-onboarded stylist who hasn't logged in yet
can still be the assignee on a walk-in the owner files on their behalf;
the owner mints the PIN later.

Admins are included (2026-07-24) so an admin can own leads/appointments
directly, not only reassign them to reps. Active state is still a gate:
inactive (`is_active=False`) users are deactivated staff who shouldn't
pick up new work even if their row still exists for historical
attribution.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import User

# Roles eligible to own leads/appointments. Kept as a single source of
# truth so the list query and the validation gate can never drift.
ASSIGNABLE_ROLES = ("sales", "admin")


def list_assignable_sales_users(db: Session) -> list[User]:
    """Active assignable users (sales + admin), ordered for picker display.

    Order: full_name (NULLS LAST), then username — users without a
    display name sort below those with one. Caller can re-sort if it
    needs something different (e.g. recency).
    """
    return (
        db.query(User)
        .filter(User.role.in_(ASSIGNABLE_ROLES))
        .filter(User.is_active.is_(True))
        .order_by(User.full_name.is_(None), User.full_name, User.username)
        .all()
    )


def is_assignable_sales_user(db: Session, user_id: int) -> bool:
    """True iff `user_id` is an active assignable user (sales or admin).

    Used as the validation gate on routes that accept an
    `assigned_user_id` from the client — we never trust the client's
    word that the id belongs to an assignable user.
    """
    row = db.get(User, user_id)
    if row is None:
        return False
    return row.role in ASSIGNABLE_ROLES and bool(row.is_active)
