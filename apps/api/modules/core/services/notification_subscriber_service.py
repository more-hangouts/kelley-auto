"""Notification subscriber registry — the "who gets what" service
(Omnichannel Inbox Plan, Part 1; migration 093).

This is the fourth recipient-resolution layer, sitting on top of the three in
``services.notification_routing`` (intrinsic targeting → role defaults →
per-user overrides). It lets the owner route any subscribable event kind to
arbitrary people, including **external, login-less recipients** — a front-desk
address, the dealership principal, an accountant — who exist only to receive
email alerts.

Two consumers:

  - ``recipients_for`` (routing) calls :func:`subscribers_for_kind` to fold
    subscribers into an event's recipient set. Delivery email is resolved
    here: a linked subscriber inherits the user's authoritative address (and
    is dropped if the user is inactive); an external subscriber uses its own.

  - The admin router calls the CRUD + :func:`list_subscribers` /
    :func:`catalog` helpers to render and edit the people × kinds matrix.

Design mirrors ``notification_preferences_service``: a self-describing catalog
so the UI needs no code change when a new kind becomes subscribable, and a
partial-update contract that rejects unknown/non-subscribable kinds rather
than silently inserting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import (
    NotificationSubscriber,
    NotificationSubscription,
    User,
)
from modules.core.services.notification_preferences_service import KIND_DESCRIPTORS


# ─── Subscribable kinds ─────────────────────────────────────────────────────
#
# The allowlist of event kinds an admin can route to arbitrary subscribers.
# Deliberately a curated subset of the routing catalog: only kinds where "send
# this to some extra person" is a sensible operation. Intrinsic-only kinds
# (e.g. "your shift was edited" → the affected staffer) are excluded — routing
# them to a bystander would be a confusing no-op.
#
# Phase 2 (inbox) appends the ``inbox.*`` kinds here; no other code changes.
SUBSCRIBABLE_KINDS: tuple[str, ...] = (
    "inbox.message_received",
    "admin.new_booking",
    "admin.walk_in_lead_created",
    "admin.time_off_requested",
    "admin.missing_clock_out",
)

#: Channels a subscription may target. Phase 1 delivers email only; the others
#: are accepted by the schema but not yet fanned out by the dispatcher.
VALID_CHANNELS: frozenset[str] = frozenset({"email", "in_app", "sms"})


class SubscriberError(Exception):
    """Service-layer error with a stable code + HTTP status. The router maps
    it to an HTTPException; this module never raises HTTPException directly.
    """

    def __init__(self, code: str, *, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class ResolvedSubscriber:
    """A subscriber resolved to a deliverable address for one kind."""

    user_id: int | None
    email: str


# ─── Routing consumption ────────────────────────────────────────────────────


def subscribers_for_kind(db: Session, kind: str) -> list[ResolvedSubscriber]:
    """Active subscribers who want ``kind`` on the email channel, resolved to
    a deliverable address. Called by ``recipients_for`` as the union layer.

    Fails soft per row: a linked subscriber whose user is inactive or has no
    email is skipped; an external subscriber with no email (which the CHECK
    forbids at write time) is skipped defensively.
    """
    if kind not in SUBSCRIBABLE_KINDS:
        return []

    rows = (
        db.query(NotificationSubscriber, NotificationSubscription)
        .join(
            NotificationSubscription,
            NotificationSubscription.subscriber_id == NotificationSubscriber.id,
        )
        .filter(
            NotificationSubscription.kind == kind,
            NotificationSubscription.channel == "email",
            NotificationSubscription.enabled.is_(True),
            NotificationSubscriber.is_active.is_(True),
        )
        .all()
    )

    out: list[ResolvedSubscriber] = []
    for subscriber, _sub in rows:
        if subscriber.user_id is not None:
            user = db.get(User, subscriber.user_id)
            if user is None or not user.is_active or not user.email:
                continue
            out.append(
                ResolvedSubscriber(user_id=user.id, email=user.email)
            )
        elif subscriber.email:
            out.append(
                ResolvedSubscriber(user_id=None, email=subscriber.email)
            )
    return out


# ─── Catalog (self-describing UI contract) ─────────────────────────────────


def catalog() -> list[dict]:
    """The subscribable kinds, labelled for the UI, in a stable render order.
    A kind missing from ``KIND_DESCRIPTORS`` still renders with its raw key so
    a catalog gap degrades instead of vanishing.
    """
    out: list[dict] = []
    for kind in SUBSCRIBABLE_KINDS:
        descriptor = KIND_DESCRIPTORS.get(kind, {})
        out.append(
            {
                "kind": kind,
                "label": descriptor.get("label", kind),
                "category": descriptor.get("category", "Other"),
                "description": descriptor.get("description", ""),
            }
        )
    return out


# ─── Read ───────────────────────────────────────────────────────────────────


def _resolved_email(db: Session, subscriber: NotificationSubscriber) -> str | None:
    if subscriber.user_id is not None:
        user = db.get(User, subscriber.user_id)
        return user.email if user else None
    return subscriber.email


def list_subscribers(db: Session) -> list[dict]:
    """Every subscriber with its resolved identity and per-kind subscription
    map — the shape the admin matrix renders straight from.
    """
    subscribers = (
        db.query(NotificationSubscriber)
        .order_by(NotificationSubscriber.display_name.asc())
        .all()
    )
    subs_by_subscriber: dict[int, dict[str, bool]] = {}
    for sub in db.query(NotificationSubscription).all():
        subs_by_subscriber.setdefault(sub.subscriber_id, {})[
            f"{sub.kind}:{sub.channel}"
        ] = sub.enabled

    out: list[dict] = []
    for s in subscribers:
        subscription_map = subs_by_subscriber.get(s.id, {})
        # Surface the email-channel subscriptions the matrix toggles.
        kinds = {
            kind: subscription_map.get(f"{kind}:email", False)
            for kind in SUBSCRIBABLE_KINDS
        }
        out.append(
            {
                "id": s.id,
                "user_id": s.user_id,
                "display_name": s.display_name,
                "email": _resolved_email(db, s),
                "phone_e164": s.phone_e164,
                "has_login": s.user_id is not None,
                "is_active": s.is_active,
                "subscriptions": kinds,
            }
        )
    return out


# ─── Write ──────────────────────────────────────────────────────────────────


def create_subscriber(
    db: Session,
    *,
    display_name: str,
    email: str | None = None,
    user_id: int | None = None,
    phone_e164: str | None = None,
) -> dict:
    """Add a subscriber. External (login-less) subscribers require an email;
    a linked subscriber may omit it and inherit the user's address. Raises
    ``SubscriberError`` on validation failure or a duplicate user/email.
    """
    display_name = (display_name or "").strip()
    if not display_name:
        raise SubscriberError("display_name_required", http_status=422)

    email = (email or "").strip() or None
    if user_id is None and not email:
        raise SubscriberError("email_required_for_external", http_status=422)

    if user_id is not None:
        user = db.get(User, user_id)
        if user is None:
            raise SubscriberError("user_not_found", http_status=404)

    subscriber = NotificationSubscriber(
        user_id=user_id,
        display_name=display_name,
        email=email,
        phone_e164=(phone_e164 or "").strip() or None,
    )
    db.add(subscriber)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Partial unique indexes: one row per user, one external row per email.
        raise SubscriberError("subscriber_already_exists", http_status=409) from exc
    return _subscriber_dict(db, subscriber)


def update_subscriptions(
    db: Session,
    subscriber_id: int,
    updates: Iterable[tuple[str, bool]],
    *,
    channel: str = "email",
) -> dict:
    """Upsert a subscriber's toggles for the given channel. Partial: only the
    kinds passed are touched. Rejects unknown/non-subscribable kinds.
    """
    if channel not in VALID_CHANNELS:
        raise SubscriberError("invalid_channel", http_status=422)

    subscriber = db.get(NotificationSubscriber, subscriber_id)
    if subscriber is None:
        raise SubscriberError("subscriber_not_found", http_status=404)

    pending = [(k, bool(v)) for k, v in updates]
    bad = sorted({k for k, _ in pending if k not in SUBSCRIBABLE_KINDS})
    if bad:
        raise SubscriberError("kind_not_subscribable", http_status=422)

    now = datetime.now(timezone.utc)
    for kind, enabled in pending:
        stmt = pg_insert(NotificationSubscription).values(
            subscriber_id=subscriber_id,
            kind=kind,
            channel=channel,
            enabled=enabled,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["subscriber_id", "kind", "channel"],
            set_={"enabled": enabled, "updated_at": now},
        )
        db.execute(stmt)
    db.flush()
    return _subscriber_dict(db, subscriber)


def set_active(db: Session, subscriber_id: int, *, is_active: bool) -> dict:
    """Activate / deactivate a subscriber. Deactivating stops all their
    notifications without losing their toggle history."""
    subscriber = db.get(NotificationSubscriber, subscriber_id)
    if subscriber is None:
        raise SubscriberError("subscriber_not_found", http_status=404)
    subscriber.is_active = is_active
    subscriber.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _subscriber_dict(db, subscriber)


def delete_subscriber(db: Session, subscriber_id: int) -> None:
    """Remove a subscriber and (via ON DELETE CASCADE) its subscriptions."""
    subscriber = db.get(NotificationSubscriber, subscriber_id)
    if subscriber is None:
        raise SubscriberError("subscriber_not_found", http_status=404)
    db.delete(subscriber)
    db.flush()


def _subscriber_dict(db: Session, s: NotificationSubscriber) -> dict:
    subs = (
        db.query(NotificationSubscription)
        .filter(
            NotificationSubscription.subscriber_id == s.id,
            NotificationSubscription.channel == "email",
        )
        .all()
    )
    enabled_by_kind = {sub.kind: sub.enabled for sub in subs}
    return {
        "id": s.id,
        "user_id": s.user_id,
        "display_name": s.display_name,
        "email": _resolved_email(db, s),
        "phone_e164": s.phone_e164,
        "has_login": s.user_id is not None,
        "is_active": s.is_active,
        "subscriptions": {
            kind: enabled_by_kind.get(kind, False)
            for kind in SUBSCRIBABLE_KINDS
        },
    }
