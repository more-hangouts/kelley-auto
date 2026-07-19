"""Admin per-day schedule router (Phase 10 Slice 1).

Owner-side surface under `/api/admin/schedule`:

  GET    /week                          — one-shot grid payload for a week
  POST   /entries                       — create a draft or published entry
  PATCH  /entries/{id}                  — edit a draft (published rows are
                                          immutable through this verb)
  DELETE /entries/{id}                  — delete a draft
  PATCH  /entries/{id}/published        — edit a published row; fires
                                          `staff.shift_edited` with an
                                          old/new snapshot in payload
  POST   /entries/{id}/retract          — move a published row back to
                                          draft; fires `staff.shift_deleted`
                                          with the published-shift snapshot
                                          in payload
  POST   /publish                       — flip every draft in the week to
                                          published (rejects if any conflict
                                          with approved time-off)
  POST   /entries/{id}/notes            — set or clear `manager_notes`
                                          (works on published rows too)
  POST   /entries/{id}/excuse           — flip a `no_show` row to `excused`

All routes require `require_admin_scope` — sales tokens get 403 even
when they happen to call from the sales subdomain. Mounts under
`/api/admin/schedule` in `api/server.py`.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database.auth import require_admin_scope
from database.connection import get_db
from database.models import User
from services import auto_scheduler, staff_schedule, staff_schedule_presets
from services.staff_schedule import StaffScheduleError
from services.staff_schedule_presets import StaffSchedulePresetError

router = APIRouter()


def _raise(exc: StaffScheduleError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(status_code=exc.http_status, detail=detail) from exc


def _raise_preset(exc: StaffSchedulePresetError) -> None:
    detail: dict[str, object] = {"code": exc.code}
    detail.update(exc.extra)
    raise HTTPException(status_code=exc.http_status, detail=detail) from exc


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    business_date: date
    starts_at_local: datetime
    ends_at_local: datetime
    source: str = "manual"
    source_shift_id: int | None = None
    late_grace_minutes: int | None = Field(default=None, ge=0, le=120)
    manager_notes: str | None = Field(default=None, max_length=500)
    publish: bool = False


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date | None = None
    starts_at_local: datetime | None = None
    ends_at_local: datetime | None = None
    late_grace_minutes: int | None = Field(default=None, ge=0, le=120)
    manager_notes: str | None = Field(default=None, max_length=500)
    source_shift_id: int | None = None


class PublishWeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    user_ids: list[int] | None = None


class GenerateDraftWeekRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    overrides: dict | None = None
    user_ids: list[int] | None = None


class ResendPublishedRequest(BaseModel):
    """Body for ``POST /weeks/{week_start}/resend-published``. Empty body
    or omitting ``user_ids`` resends to every staffer with at least one
    published shift in the week."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] | None = None


class NotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class ExcuseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class ResolveMissingOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    out_at_local: datetime
    notes: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/week")
def get_week(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    week_start: date = Query(...),
    user_ids: list[int] | None = Query(default=None),
) -> dict:
    try:
        return staff_schedule.list_week(
            db, week_start=week_start, user_ids=user_ids
        )
    except StaffScheduleError as exc:
        _raise(exc)


@router.post("/entries", status_code=201)
def create_entry(
    payload: EntryCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_schedule.create_entry(
            db,
            actor_user_id=admin.id,
            user_id=payload.user_id,
            business_date_=payload.business_date,
            starts_at_local=payload.starts_at_local,
            ends_at_local=payload.ends_at_local,
            source=payload.source,
            source_shift_id=payload.source_shift_id,
            late_grace_minutes=payload.late_grace_minutes,
            manager_notes=payload.manager_notes,
            publish=payload.publish,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.patch("/entries/{entry_id}")
def patch_entry(
    entry_id: int,
    payload: EntryPatch,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    fields = {
        k: v
        for k, v in payload.model_dump().items()
        if k in payload.model_fields_set
    }
    if not fields:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )
    try:
        result = staff_schedule.update_entry(
            db, entry_id=entry_id, fields=fields
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.delete("/entries/{entry_id}", status_code=204, response_class=Response)
def delete_entry(
    entry_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> Response:
    try:
        staff_schedule.delete_entry(db, entry_id=entry_id)
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=204)


@router.patch("/entries/{entry_id}/published")
def patch_published_entry(
    entry_id: int,
    payload: EntryPatch,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Edit a published row's start/end/notes/grace. Fires
    ``staff.shift_edited`` to the affected staffer with an old/new
    snapshot in payload. Use the draft PATCH verb above for entries
    that haven't been published yet."""
    fields = {
        k: v
        for k, v in payload.model_dump().items()
        if k in payload.model_fields_set
    }
    if not fields:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )
    try:
        result = staff_schedule.update_published_entry(
            db,
            actor_user_id=admin.id,
            entry_id=entry_id,
            fields=fields,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/entries/{entry_id}/retract")
def retract_entry(
    entry_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Move a published row back to draft and fire
    ``staff.shift_deleted`` to the affected staffer. The row survives
    as a draft so the audit story remains tractable — re-publishing
    is a separate verb."""
    try:
        result = staff_schedule.retract_published_entry(
            db,
            actor_user_id=admin.id,
            entry_id=entry_id,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/publish")
def publish(
    payload: PublishWeekRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_schedule.publish_week(
            db,
            actor_user_id=admin.id,
            week_start=payload.week_start,
            user_ids=payload.user_ids,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/weeks/{week_start_iso}/resend-published")
def resend_published_week(
    week_start_iso: str,
    payload: ResendPublishedRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Re-send the staff.schedule_published email for everyone with a
    published shift in this week. Admin's "resend this week's schedule"
    affordance from the grid. Path-bound to a Monday-start ISO date so
    a stale tab doesn't fire against a week the admin no longer means."""
    try:
        week_start = date.fromisoformat(week_start_iso)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_week_start",
                "message": "week_start_iso must be YYYY-MM-DD",
            },
        )
    try:
        result = staff_schedule.resend_published_week(
            db,
            actor_user_id=admin.id,
            week_start=week_start,
            user_ids=payload.user_ids,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.get("/auto-schedule/rules")
def auto_schedule_rules(
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Return the active auto-scheduler rule set so the manager can
    preview the parameters in the Generate Draft dialog before
    confirming. Currently the defaults; future slice can persist."""
    return auto_scheduler.rules_to_dict(auto_scheduler.DEFAULT_RULES)


@router.post("/generate-draft-week")
def generate_draft_week(
    payload: GenerateDraftWeekRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Populate DRAFT shifts for the week so the manager can review +
    publish. Never publishes on the manager's behalf."""
    try:
        result = auto_scheduler.generate_draft_week(
            db,
            actor_user_id=admin.id,
            week_start=payload.week_start,
            overrides=payload.overrides,
            user_ids=payload.user_ids,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/entries/{entry_id}/publish")
def publish_entry(
    entry_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Publish a single draft entry from the grid's detail dialog.

    The week-level `/publish` already exists; this endpoint exists so
    the manager can post one shift right from the entry edit view
    without having to publish the whole week. Same time-off lock and
    conflict semantics as `publish_week` — see
    `services.staff_schedule.publish_entry`.
    """
    try:
        result = staff_schedule.publish_entry(
            db, actor_user_id=admin.id, entry_id=entry_id
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/entries/{entry_id}/notes")
def set_notes(
    entry_id: int,
    payload: NotesRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_schedule.set_manager_notes(
            db, entry_id=entry_id, notes=payload.notes
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/entries/{entry_id}/excuse")
def excuse(
    entry_id: int,
    payload: ExcuseRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_schedule.mark_excused(
            db,
            actor_user_id=admin.id,
            entry_id=entry_id,
            notes=payload.notes,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


@router.post("/entries/{entry_id}/resolve-missing-out")
def resolve_missing_out(
    entry_id: int,
    payload: ResolveMissingOutRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Slice-4 recovery for `missing_out_punch`. Manager supplies an
    out-time (boutique-local); the service inserts a paired manual
    out-punch, stamps the entry, and re-derives attendance_status."""
    try:
        result = staff_schedule.resolve_missing_out_punch(
            db,
            actor_user_id=admin.id,
            entry_id=entry_id,
            out_at_local=payload.out_at_local,
            notes=payload.notes,
        )
    except StaffScheduleError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Attendance Review read paths (Phase 10 Slice 2)
# ---------------------------------------------------------------------------


@router.get("/flagged-exceptions")
def list_flagged_exceptions(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    from_date: date = Query(...),
    to_date: date = Query(...),
    user_id: int | None = Query(default=None),
) -> dict:
    """No-show entries in the bounded window. Used by Attendance
    Review's 'Flagged exceptions' card."""
    try:
        rows = staff_schedule.list_flagged_exceptions(
            db, from_date=from_date, to_date=to_date, user_id=user_id
        )
    except StaffScheduleError as exc:
        _raise(exc)
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "exceptions": rows,
    }


@router.get("/variance")
def hours_variance(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    from_date: date = Query(...),
    to_date: date = Query(...),
    user_id: int | None = Query(default=None),
) -> dict:
    """Per-staff scheduled-vs-actual hours over published entries in
    the bounded window. Used by Attendance Review's 'Hours variance'
    card so payroll can see scheduled vs worked at a glance."""
    try:
        rows = staff_schedule.hours_variance(
            db, from_date=from_date, to_date=to_date, user_id=user_id
        )
    except StaffScheduleError as exc:
        _raise(exc)
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Schedule shift presets (Phase 10 Slice 3)
# ---------------------------------------------------------------------------


class PresetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    start_time: time
    end_time: time
    late_grace_minutes: int = Field(default=30, ge=0, le=120)
    sort_order: int = Field(default=100, ge=0)


class PresetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=80)
    start_time: time | None = None
    end_time: time | None = None
    late_grace_minutes: int | None = Field(default=None, ge=0, le=120)
    sort_order: int | None = Field(default=None, ge=0)
    active: bool | None = None


@router.get("/presets")
def list_presets(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
    include_archived: bool = Query(default=False),
) -> dict:
    """List schedule presets. `include_archived=True` returns inactive
    rows too (used by the admin management surface); the grid fetches
    with the default (active only).
    """
    rows = staff_schedule_presets.list_presets(
        db, active_only=not include_archived
    )
    return {"presets": rows}


@router.post("/presets", status_code=201)
def create_preset(
    payload: PresetCreate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    try:
        result = staff_schedule_presets.create_preset(
            db,
            actor_user_id=admin.id,
            label=payload.label,
            start_time_=payload.start_time,
            end_time_=payload.end_time,
            late_grace_minutes=payload.late_grace_minutes,
            sort_order=payload.sort_order,
        )
    except StaffSchedulePresetError as exc:
        db.rollback()
        _raise_preset(exc)
    db.commit()
    return result


@router.patch("/presets/{preset_id}")
def patch_preset(
    preset_id: int,
    payload: PresetPatch,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    fields = {
        k: v
        for k, v in payload.model_dump().items()
        if k in payload.model_fields_set
    }
    if not fields:
        raise HTTPException(
            status_code=422, detail={"code": "nothing_to_update"}
        )
    try:
        result = staff_schedule_presets.update_preset(
            db, preset_id=preset_id, fields=fields
        )
    except StaffSchedulePresetError as exc:
        db.rollback()
        _raise_preset(exc)
    db.commit()
    return result


@router.delete("/presets/{preset_id}")
def archive_preset(
    preset_id: int,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin_scope)],
) -> dict:
    """Soft-delete: flips `active` to False rather than removing the
    row. Re-activate with PATCH `{"active": true}` if needed."""
    try:
        result = staff_schedule_presets.archive_preset(
            db, preset_id=preset_id
        )
    except StaffSchedulePresetError as exc:
        db.rollback()
        _raise_preset(exc)
    db.commit()
    return result
