"""Smoke for the Phase 4 walk-in assignment hook.

Exercises ``services.walk_in_service.create_walk_in_lead`` at the
service layer (route is Phase 5):

  - With ``assigned_user_id=<sales user>``:
      * appointments.assigned_user_id == sales_user_id
      * events.owner_user_id == sales_user_id (overrides any
        event_in.owner_user_id so both fields agree on the stylist)
      * activity_log row for ``event.walk_in_created`` has
        actor_user_id == caller (not the assignee — created-by and
        assigned-to are distinct fields)
  - With ``assigned_user_id=None`` (admin route's current behavior):
      * appointments.assigned_user_id stays NULL — the new Phase 4
        field is opt-in
      * events.owner_user_id falls back to ``actor_user_id`` per the
        pre-existing event_service behavior at services/event_service.py
        (``owner_user_id=o.owner_user_id or actor_user_id``). This
        preserves what the admin walk-in surface has always done; the
        smoke pins the invariant so a future regression in event_service
        would be caught here too.
      * activity_log actor is still the caller

Names use the ``Walk-In Assign Smoke`` prefix added to
``scripts/cleanup_admin_smoke_pollution.sql`` so a crashed run is
sweepable.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

os.environ.setdefault("APP_TIMEZONE", "America/Chicago")
os.environ.setdefault("ALLOW_AUDIT_MUTATION", "1")
os.environ.setdefault(
    "SECRET_KEY",
    "test-key-not-for-production-just-smoke-testing-only-please",
)

from sqlalchemy import text as sql_text  # noqa: E402

from database.auth import hash_password  # noqa: E402
from database.connection import SessionLocal  # noqa: E402
from database.models import ActivityLog, Appointment, Event, User  # noqa: E402
from services import walk_in_service  # noqa: E402
from services.walk_in_service import (  # noqa: E402
    WalkInContactInput,
    WalkInEnrichmentInput,
    WalkInEventInput,
)

_created_user_ids: list[int] = []
_created_contact_ids: list[int] = []
_created_event_ids: list[int] = []
_created_appt_ids: list[int] = []


def _make_user(role: str, label: str) -> int:
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex[:8]
        u = User(
            username=f"{role}-smoke-{label}-{suffix}",
            email=f"{role}-smoke-{label}-{suffix}@example.com",
            hashed_password=hash_password("smoke-pass-12345"),
            full_name=f"Walk-In Assign Smoke {role.title()} {label}",
            is_active=True,
            role=role,
            permissions=[],
            token_version=0,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        _created_user_ids.append(u.id)
        return u.id
    finally:
        db.close()


def _unique_phone() -> str:
    suffix = uuid.uuid4().int % 10_000
    return f"(210) 555-{suffix:04d}"


def _run_case(*, actor_user_id: int, assigned_user_id: int | None, tag: str):
    """Drive one create_walk_in_lead call inside its own transaction."""
    db = SessionLocal()
    try:
        contact_in = WalkInContactInput(
            first_name="Walk",
            last_name=f"In {tag}",
            display_name=f"Walk-In Assign Smoke {tag}",
            email=f"walkin-assign-{tag.lower()}@example.com",
            phone=_unique_phone(),
        )
        event_in = WalkInEventInput(
            celebrant_first_name=f"Celebrant {tag}",
            celebrant_last_name="Smoke",
            event_name=f"Walk-In Assign Smoke Quince {tag}",
            event_date=date(2027, 7, 4),
            owner_user_id=None,
        )
        enrichment_in = WalkInEnrichmentInput(
            party_size_bucket="pair",
            court_size=None,
            quince_theme=None,
            quince_theme_colors=None,
            budget_range=None,
            dress_styles=None,
            colors=None,
            notes=None,
        )
        result = walk_in_service.create_walk_in_lead(
            db,
            actor_user_id=actor_user_id,
            contact_in=contact_in,
            event_in=event_in,
            enrichment_in=enrichment_in,
            assigned_user_id=assigned_user_id,
        )
        db.commit()
        _created_contact_ids.append(result.contact.id)
        _created_event_ids.append(result.event.id)
        _created_appt_ids.append(result.appointment.id)
        return result.appointment.id, result.event.id
    finally:
        db.close()


def _read_state(appointment_id: int, event_id: int) -> dict:
    db = SessionLocal()
    try:
        appt = db.get(Appointment, appointment_id)
        event = db.get(Event, event_id)
        activity = (
            db.query(ActivityLog)
            .filter(ActivityLog.event_id == event_id)
            .filter(ActivityLog.activity_type == "event.walk_in_created")
            .order_by(ActivityLog.id.desc())
            .first()
        )
        return {
            "appt_assigned_user_id": appt.assigned_user_id if appt else None,
            "event_owner_user_id": event.owner_user_id if event else None,
            "activity_actor_user_id": (
                activity.actor_user_id if activity else None
            ),
            "activity_actor_kind": (activity.actor_kind if activity else None),
        }
    finally:
        db.close()


def _cleanup() -> None:
    if not (
        _created_appt_ids
        or _created_event_ids
        or _created_contact_ids
        or _created_user_ids
    ):
        return
    db = SessionLocal()
    try:
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM activity_log WHERE event_id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_status_change_events "
                    "WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
            db.execute(
                sql_text(
                    "DELETE FROM event_participants WHERE event_id = ANY(:ids)"
                ),
                {"ids": _created_event_ids},
            )
        if _created_appt_ids:
            db.execute(
                sql_text(
                    "DELETE FROM appointment_enrichment_responses "
                    "WHERE appointment_id = ANY(:ids)"
                ),
                {"ids": _created_appt_ids},
            )
            db.execute(
                sql_text("DELETE FROM appointments WHERE id = ANY(:ids)"),
                {"ids": _created_appt_ids},
            )
        if _created_event_ids:
            db.execute(
                sql_text("DELETE FROM events WHERE id = ANY(:ids)"),
                {"ids": _created_event_ids},
            )
        if _created_contact_ids:
            db.execute(
                sql_text("DELETE FROM contacts WHERE id = ANY(:ids)"),
                {"ids": _created_contact_ids},
            )
        if _created_user_ids:
            db.execute(
                sql_text("DELETE FROM users WHERE id = ANY(:ids)"),
                {"ids": _created_user_ids},
            )
        db.commit()
    finally:
        db.close()


def main() -> None:
    admin_id = _make_user("admin", "actor")
    sales_id = _make_user("sales", "assignee")

    # ---- Case 1: assigned_user_id provided ----
    appt_id, event_id = _run_case(
        actor_user_id=admin_id,
        assigned_user_id=sales_id,
        tag=f"ASSIGN-{uuid.uuid4().hex[:6].upper()}",
    )
    state = _read_state(appt_id, event_id)
    assert state["appt_assigned_user_id"] == sales_id, state
    assert state["event_owner_user_id"] == sales_id, state
    assert state["activity_actor_user_id"] == admin_id, state
    assert state["activity_actor_kind"] == "staff", state

    # ---- Case 2: assigned_user_id None (admin route's existing behavior) ----
    appt_id_none, event_id_none = _run_case(
        actor_user_id=admin_id,
        assigned_user_id=None,
        tag=f"NONE-{uuid.uuid4().hex[:6].upper()}",
    )
    state_none = _read_state(appt_id_none, event_id_none)
    assert state_none["appt_assigned_user_id"] is None, state_none
    # events.owner_user_id falls back to the actor when no explicit
    # owner is supplied — pre-existing event_service behavior, kept by
    # Phase 4 unchanged. Asserted here so a future event_service refactor
    # that drops the fallback gets caught.
    assert state_none["event_owner_user_id"] == admin_id, state_none
    # Actor stays the admin in both cases — created-by and assigned-to
    # are deliberately not the same field.
    assert state_none["activity_actor_user_id"] == admin_id, state_none

    print("walk_in_assignment smoke ok")


if __name__ == "__main__":
    try:
        main()
    finally:
        _cleanup()
