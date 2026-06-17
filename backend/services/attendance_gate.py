"""Server-enforced punched-out gate (Phase 7 Slice 2 of the Sales Portal).

The Phase 7 plan locked in: "Today's appointment list/detail,
appointment notes, tried-on logging, quotes, invoices, and
participant creation require an active punch unless the owner
disables the attendance gate." Slice 1 wired clock-in itself; this
module enforces the gate on every mutation endpoint a sales token
can hit.

Why server-enforced and not just frontend-hidden: a stylist with an
old tab open from before they punched out, or a future native client,
would otherwise still be able to mutate today's floor. Frontend
gating is good UX; this dependency is what keeps the rule real.

Admin tokens always bypass — admins are not on the clock and the
gate is sales-staff-specific. Sales tokens get checked against
`current_status`; if the owner has flipped
`business_profile.attendance_gate_enabled` off (covering staff,
boutique under construction, etc.), the gate becomes a no-op.

Design note: this dep replaces `require_sales_scope` /
`require_any_scope("admin", "sales")` on mutation endpoints — it
already enforces the scope check internally so the per-route
decoration stays one-line. Read endpoints keep the lighter
require_*_scope deps; the doc deliberately scopes the gate to
mutations so an on-the-way-to-the-shop stylist can still see
today's list on her phone.
"""

from __future__ import annotations

from typing import Annotated, Iterable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from database.auth import get_current_user_with_scope
from database.connection import get_db
from database.models import BusinessProfile, User
from services import clock_in

# Mirror of `database.auth._VALID_SCOPES` to keep this module's
# Depends graph standalone; the scope set is small and frozen, so a
# tiny duplication is cheaper than reaching into auth's private module
# constants.
_VALID_SCOPES = frozenset({"admin", "sales"})

_SCOPE_FORBIDDEN_EXC = HTTPException(
    status_code=403, detail={"code": "scope_forbidden"}
)

_ATTENDANCE_GATE_EXC = HTTPException(
    status_code=403,
    detail={
        "code": "attendance_gate",
        "message": (
            "Clock in to start working the floor. The attendance gate "
            "blocks appointment mutations until you punch in."
        ),
    },
)


def _is_gate_enabled(db: Session) -> bool:
    """Read the singleton business_profile row. If it doesn't exist
    (fresh install) the gate is off — Slice 1 deploys before the
    owner finishes setting up, and we'd rather not lock everyone out
    of the portal during onboarding."""
    profile = db.get(BusinessProfile, 1)
    if profile is None:
        return False
    return bool(profile.attendance_gate_enabled)


def require_floor_access(*allowed_scopes: str):
    """Dependency factory for sales-portal mutation endpoints.

    - Rejects any token whose `scope` claim is not in `allowed_scopes`
      with 403 (same shape as `require_admin_scope` /
      `require_sales_scope`).
    - For sales-scope tokens, additionally enforces the punched-in
      gate when `business_profile.attendance_gate_enabled` is True.
    - Admin-scope tokens are passed through unconditionally — admins
      are not on the clock.

    Use on routes the doc lists as floor mutations: appointment status
    and notes, tried-on add/patch/delete, quote create/send/sign/
    convert, invoice create/send, participant create.
    """
    if not allowed_scopes:
        raise ValueError("require_floor_access needs at least one scope")
    allowed = frozenset(allowed_scopes)
    if not allowed.issubset(_VALID_SCOPES):
        raise ValueError(
            f"unknown scopes in {allowed_scopes!r}; expected subset of "
            f"{sorted(_VALID_SCOPES)}"
        )

    def _dep(
        bundle: Annotated[
            tuple[User, str], Depends(get_current_user_with_scope)
        ],
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        user, scope = bundle
        if scope not in allowed:
            raise _SCOPE_FORBIDDEN_EXC
        if scope != "sales":
            return user
        if not _is_gate_enabled(db):
            return user
        state, _last = clock_in.current_status(db, user.id)
        if state == "out":
            raise _ATTENDANCE_GATE_EXC
        return user

    return _dep


__all__: Iterable[str] = ["require_floor_access"]
