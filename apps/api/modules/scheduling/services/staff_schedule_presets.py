"""Admin-configurable schedule presets service (Phase 10 Slice 3).

CRUD + soft-delete for `staff_schedule_presets`, the rows that back
the "Preset" dropdown in the manager weekly grid. Slice 2 had three
presets hardcoded in `AdminScheduleGrid.jsx`; this slice moves them
into the DB so the manager can edit / add / archive presets from the
admin UI without a code change.

Validation rules (mirror the schema CHECKs with clearer error codes):

  - `label` is required (non-empty after trim) and max 80 chars.
  - `end_time > start_time` (the CHECK is strict — equal times are
    rejected, the manager shouldn't be able to publish a zero-minute
    shift even by accident).
  - `late_grace_minutes` between 0 and 120.
  - `sort_order` non-negative.
  - `label` must be unique among ACTIVE presets — the partial unique
    on the table catches it, the service raises 409 with a clear code.

Archive (`active=False`) is the only delete path. We keep archived
rows so future audit / reporting that points at a preset id by
foreign key (no such FK exists today; this is forward-compat
discipline) doesn't dangle.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import StaffSchedulePreset


class StaffSchedulePresetError(Exception):
    """Stable error codes the router maps to HTTP statuses."""

    def __init__(
        self,
        code: str,
        *,
        http_status: int = 400,
        extra: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.extra = dict(extra or {})


def _preset_to_dict(p: StaffSchedulePreset) -> dict:
    return {
        "id": p.id,
        "label": p.label,
        "start_time": p.start_time.isoformat(timespec="minutes"),
        "end_time": p.end_time.isoformat(timespec="minutes"),
        "late_grace_minutes": int(p.late_grace_minutes),
        "sort_order": int(p.sort_order),
        "active": bool(p.active),
        "created_by_user_id": p.created_by_user_id,
        "created_at": p.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": p.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _validate_payload(
    *,
    label: str,
    start_time_: time,
    end_time_: time,
    late_grace_minutes: int,
    sort_order: int,
) -> None:
    if not label or not label.strip():
        raise StaffSchedulePresetError("label_required", http_status=422)
    if len(label.strip()) > 80:
        raise StaffSchedulePresetError("label_too_long", http_status=422)
    if end_time_ <= start_time_:
        raise StaffSchedulePresetError("invalid_time_range", http_status=422)
    if not (0 <= late_grace_minutes <= 120):
        raise StaffSchedulePresetError(
            "late_grace_out_of_range", http_status=422
        )
    if sort_order < 0:
        raise StaffSchedulePresetError(
            "sort_order_negative", http_status=422
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_presets(
    db: Session, *, active_only: bool = True
) -> list[dict]:
    """Return presets ordered by `sort_order, label` so the grid's
    dropdown renders deterministically. `active_only=False` is the
    admin management view; the grid itself passes `active_only=True`.
    """
    stmt = select(StaffSchedulePreset).order_by(
        StaffSchedulePreset.sort_order,
        StaffSchedulePreset.label,
    )
    if active_only:
        stmt = stmt.where(StaffSchedulePreset.active.is_(True))
    return [
        _preset_to_dict(p) for p in db.execute(stmt).scalars().all()
    ]


def create_preset(
    db: Session,
    *,
    actor_user_id: int,
    label: str,
    start_time_: time,
    end_time_: time,
    late_grace_minutes: int = 30,
    sort_order: int = 100,
) -> dict:
    label = (label or "").strip()
    _validate_payload(
        label=label,
        start_time_=start_time_,
        end_time_=end_time_,
        late_grace_minutes=late_grace_minutes,
        sort_order=sort_order,
    )

    preset = StaffSchedulePreset(
        label=label,
        start_time=start_time_,
        end_time=end_time_,
        late_grace_minutes=late_grace_minutes,
        sort_order=sort_order,
        active=True,
        created_by_user_id=actor_user_id,
    )
    db.add(preset)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # The partial unique index is the only constraint left this
        # late; map it to a clean code so the UI can surface "that
        # name is already in use" without parsing Postgres text.
        if "uq_ssp_active_label" in str(exc.orig):
            raise StaffSchedulePresetError(
                "duplicate_label", http_status=409
            ) from exc
        raise
    return _preset_to_dict(preset)


def update_preset(
    db: Session,
    *,
    preset_id: int,
    fields: dict,
) -> dict:
    """Partial update on any field except `id`, `created_*`. Activating
    an archived preset is allowed (pass `active=True`); the
    `label` partial unique check runs against the post-update state."""
    preset = db.get(StaffSchedulePreset, preset_id)
    if preset is None:
        raise StaffSchedulePresetError("preset_not_found", http_status=404)

    allowed = {
        "label",
        "start_time",
        "end_time",
        "late_grace_minutes",
        "sort_order",
        "active",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise StaffSchedulePresetError(
            "unknown_field",
            http_status=422,
            extra={"fields": sorted(unknown)},
        )
    if not fields:
        raise StaffSchedulePresetError("nothing_to_update", http_status=422)

    new_label = (
        (fields.get("label") or "").strip()
        if "label" in fields
        else preset.label
    )
    new_start = fields.get("start_time", preset.start_time)
    new_end = fields.get("end_time", preset.end_time)
    new_grace = int(fields.get("late_grace_minutes", preset.late_grace_minutes))
    new_sort = int(fields.get("sort_order", preset.sort_order))
    _validate_payload(
        label=new_label,
        start_time_=new_start,
        end_time_=new_end,
        late_grace_minutes=new_grace,
        sort_order=new_sort,
    )

    if "label" in fields:
        preset.label = new_label
    if "start_time" in fields:
        preset.start_time = new_start
    if "end_time" in fields:
        preset.end_time = new_end
    if "late_grace_minutes" in fields:
        preset.late_grace_minutes = new_grace
    if "sort_order" in fields:
        preset.sort_order = new_sort
    if "active" in fields:
        preset.active = bool(fields["active"])
    preset.updated_at = datetime.now(timezone.utc)

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if "uq_ssp_active_label" in str(exc.orig):
            raise StaffSchedulePresetError(
                "duplicate_label", http_status=409
            ) from exc
        raise
    return _preset_to_dict(preset)


def archive_preset(db: Session, *, preset_id: int) -> dict:
    """Soft-delete: flip `active` to False. Idempotent — archiving an
    already-archived preset is a no-op (returns the row unchanged)."""
    preset = db.get(StaffSchedulePreset, preset_id)
    if preset is None:
        raise StaffSchedulePresetError("preset_not_found", http_status=404)
    if preset.active:
        preset.active = False
        preset.updated_at = datetime.now(timezone.utc)
        db.flush()
    return _preset_to_dict(preset)


__all__ = [
    "StaffSchedulePresetError",
    "archive_preset",
    "create_preset",
    "list_presets",
    "update_preset",
]
