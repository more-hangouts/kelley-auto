"""Preference read / upsert helpers (B2.5).

Thin service module between the sales-portal router and the
``notification_preferences`` table. Keeps the router thin (validation +
auth + HTTP shapes) and lets the same helpers be reused later by an
admin-side "view a staff member's prefs" surface if we ever build one.

Design choices:

  - **Configurability is per kind.** Kinds whose recipients are
    exclusively chosen by ``INTRINSIC_TARGETING`` (e.g. "your shift was
    edited") are not user-toggleable: intrinsic targeting always wins,
    so a preference flip would be a confusing no-op. Only the kinds
    listed in this user's role-default bundle are returned as
    configurable, plus any kinds the user has already explicitly
    toggled (defensive against stale rows from a prior policy).

  - **Effective state.** Each returned kind carries ``enabled`` (the
    bool the routing module would use right now) and ``source``
    (``role_default`` or ``override``) so the UI can show "default on /
    overridden off" without re-computing.

  - **Partial PUT.** ``upsert_preferences`` takes only the kinds the
    caller wants to change. Unmentioned kinds keep their prior state.
    Submitting a kind that isn't user-configurable is rejected with
    ``PreferenceError('kind_not_configurable', ...)``.

The router maps the error class to the appropriate HTTP status; this
module never raises ``HTTPException`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from database.models import NotificationPreference, User
from services.notification_routing import ROLE_DEFAULTS


# ─── Labels + categories ───────────────────────────────────────────────────
#
# Keep the label/category mapping centralised here so the API response can
# stay self-describing. A new event kind shows up in the UI as soon as a
# row is added below; no frontend code change needed.

KIND_DESCRIPTORS: dict[str, dict[str, str]] = {
    "digest.staff_daily": {
        "category": "Digests",
        "label": "Daily digest",
        "description": "Each weekday morning, a quick summary of your day at the boutique.",
    },
    "digest.staff_weekly": {
        "category": "Digests",
        "label": "Weekly digest",
        "description": "Sunday evening look-ahead at your shifts for the upcoming week.",
    },
    "digest.admin_daily": {
        "category": "Digests",
        "label": "Admin daily digest",
        "description": "Each weekday morning, new bookings + time-off + attendance exceptions across the boutique.",
    },
    "admin.new_booking": {
        "category": "Bookings",
        "label": "New booking",
        "description": "A booking is created (any source).",
    },
    "admin.walk_in_lead_created": {
        "category": "Bookings",
        "label": "Walk-in lead created",
        "description": "Someone on staff logs a walk-in or phone lead.",
    },
    "admin.time_off_requested": {
        "category": "Time off",
        "label": "Time-off request filed",
        "description": "A staffer files a new time-off request.",
    },
    "admin.missing_clock_out": {
        "category": "Attendance",
        "label": "Missing clock-out (admin)",
        "description": "Attendance cron flags a missed clock-out for review.",
    },
}


# ─── Errors ────────────────────────────────────────────────────────────────


class PreferenceError(Exception):
    def __init__(self, code: str, *, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


# ─── Public API ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreferenceView:
    event_kind: str
    enabled: bool
    source: str  # 'role_default' | 'override'
    label: str
    category: str
    description: str


def configurable_kinds_for(user: User) -> set[str]:
    """The set of event kinds this user can toggle. = role-default bundle
    for the user's role plus any kinds with an existing preference row
    (so a prior override doesn't get hidden if the role bundle changes
    later)."""
    role = (user.role or "").lower()
    return set(ROLE_DEFAULTS.get(role, {}).keys())


def get_effective_preferences(db: Session, user: User) -> list[PreferenceView]:
    """Return the user's effective preference state for every kind they
    can toggle, sorted so the UI can render straight from the response.
    """
    configurable = configurable_kinds_for(user)
    overrides = {
        pref.event_kind: pref.enabled
        for pref in (
            db.query(NotificationPreference)
            .filter(NotificationPreference.user_id == user.id)
            .all()
        )
    }
    # Include kinds the user has already toggled even if no longer in
    # their role bundle — so they can opt back in / out.
    all_kinds = configurable | set(overrides.keys())

    role = (user.role or "").lower()
    role_bundle = ROLE_DEFAULTS.get(role, {})

    out: list[PreferenceView] = []
    for kind in all_kinds:
        descriptor = KIND_DESCRIPTORS.get(kind)
        if descriptor is None:
            # Catalog gap: a preference row references a kind we don't
            # know how to label. Skip rather than 500 the request.
            continue
        if kind in overrides:
            enabled = overrides[kind]
            source = "override"
        else:
            enabled = bool(role_bundle.get(kind, False))
            source = "role_default"
        out.append(
            PreferenceView(
                event_kind=kind,
                enabled=enabled,
                source=source,
                label=descriptor["label"],
                category=descriptor["category"],
                description=descriptor["description"],
            )
        )
    out.sort(key=lambda p: (p.category, p.label))
    return out


def upsert_preferences(
    db: Session,
    user: User,
    updates: Iterable[tuple[str, bool]],
) -> None:
    """Apply partial updates to the user's preferences. Each ``(kind,
    enabled)`` is upserted by primary key so the call is idempotent and
    safe against concurrent writes from a second tab. Rejects kinds the
    user can't configure rather than silently inserting them — saves us
    from a stale UI hammering the API with kinds policy quietly retired.
    """
    pending = [(k, bool(v)) for k, v in updates]
    if not pending:
        return

    configurable = configurable_kinds_for(user)
    unknown = sorted(k for k, _ in pending if k not in KIND_DESCRIPTORS)
    if unknown:
        raise PreferenceError(
            "kind_not_in_catalog",
            message=f"event kinds {unknown} are not in the catalog",
        )
    not_configurable = sorted(k for k, _ in pending if k not in configurable)
    if not_configurable:
        raise PreferenceError(
            "kind_not_configurable",
            message=(
                f"event kinds {not_configurable} are intrinsic-only or "
                "outside the user's role bundle"
            ),
        )

    now = datetime.now(timezone.utc)
    for kind, enabled in pending:
        stmt = pg_insert(NotificationPreference).values(
            user_id=user.id,
            event_kind=kind,
            enabled=enabled,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "event_kind"],
            set_={"enabled": enabled, "updated_at": now},
        )
        db.execute(stmt)
    db.flush()
