"""Order-level discount snapshot helpers.

Phase 7 introduced stacked order-level discounts: each invoice/quote
can carry up to N discount rows that combine additively. Both
invoice_service and quote_service share the rules for resolving
incoming discount inputs into the rows we persist.

Per-row rules:

1. `preset_id` provided -> look up the preset on the BusinessProfile
   singleton. The current label and percent are snapshotted onto the
   row so a later rename does NOT rewrite history. Inactive presets are
   still resolvable so a re-save of an existing record without touching
   the discount keeps working even if staff toggle the preset off.
   When updating an existing invoice/quote, callers may pass the
   record's current snapshots as a fallback; if a preset was deleted
   after the record was saved, the old snapshot is preserved instead of
   blocking unrelated edits.
2. No `preset_id` -> custom one-off. Snapshot uses the caller's `label`
   (trimmed) or `"Custom"` as the fallback. `preset_id` is `None`.

Per-row percent must be between 0 and 50. Combined sum across all rows
must also stay <= 50 (`combined_discount_too_high`). Empty list clears
all discounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from database.models import BusinessProfile

_DISCOUNT_PERCENT_MAX = Decimal("50")
_DISCOUNT_PERCENT_MIN = Decimal("0")
_COMBINED_DISCOUNT_MAX = Decimal("50")


class DiscountSnapshotError(Exception):
    """Domain-level rejection. Service layer wraps as its own error
    type so the router error-mapping stays consistent."""

    def __init__(self, message: str, *, code: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


@dataclass(frozen=True)
class DiscountRowInput:
    """Raw input for one stacked discount row, as the editor sends it."""

    preset_id: str | None = None
    label: str | None = None
    percent: Decimal | int | str | float | None = None


@dataclass(frozen=True)
class DiscountRowSnapshot:
    """Resolved snapshot for one stacked discount row, ready to persist."""

    preset_id: str | None
    label: str
    percent: Decimal


def snapshot_order_discounts(
    db: Session,
    rows: list[DiscountRowInput] | list[dict[str, Any]] | None,
    *,
    existing_snapshots: list[DiscountRowSnapshot] | None = None,
) -> list[DiscountRowSnapshot]:
    """Resolve a list of raw inputs into snapshotted rows.

    Empty / None list returns an empty list (clears the stack). Each
    row is resolved independently against BusinessProfile presets;
    combined-cap is enforced at the end. `existing_snapshots` is only
    for update paths, where the editor may re-submit a saved preset row
    after the source preset was deleted from BusinessProfile.
    """
    if not rows:
        return []

    snapshots: list[DiscountRowSnapshot] = []
    bp = db.get(BusinessProfile, 1)
    presets = (bp.discount_presets or []) if bp is not None else []

    fallback_by_preset = {
        snap.preset_id: snap
        for snap in (existing_snapshots or [])
        if snap.preset_id is not None
    }

    for raw in rows:
        snapshots.append(_snapshot_one(raw, presets, fallback_by_preset))

    total = sum((row.percent for row in snapshots), Decimal("0"))
    if total > _COMBINED_DISCOUNT_MAX:
        raise DiscountSnapshotError(
            f"combined discount {total} exceeds {_COMBINED_DISCOUNT_MAX}% cap",
            code="combined_discount_too_high",
            combined_percent=str(total),
            cap_percent=str(_COMBINED_DISCOUNT_MAX),
        )

    return snapshots


def _snapshot_one(
    raw: DiscountRowInput | dict[str, Any],
    presets: list[dict[str, Any]],
    fallback_by_preset: dict[str, DiscountRowSnapshot] | None = None,
) -> DiscountRowSnapshot:
    if isinstance(raw, dict):
        raw_id = raw.get("preset_id")
        raw_label = raw.get("label")
        raw_percent = raw.get("percent")
    else:
        raw_id = raw.preset_id
        raw_label = raw.label
        raw_percent = raw.percent

    raw_id = (raw_id or "").strip() or None
    raw_label = (raw_label or "").strip() or None

    parsed_percent: Decimal | None
    if raw_percent is None or raw_percent == "":
        parsed_percent = None
    else:
        try:
            parsed_percent = (
                raw_percent
                if isinstance(raw_percent, Decimal)
                else Decimal(str(raw_percent))
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DiscountSnapshotError(
                "discount percent must be a number",
                code="invalid_discount_percent",
            ) from exc

    if raw_id is not None:
        match = next((p for p in presets if p.get("id") == raw_id), None)
        if match is None:
            fallback = (fallback_by_preset or {}).get(raw_id)
            if fallback is not None:
                return fallback
            raise DiscountSnapshotError(
                f"discount preset '{raw_id}' not found",
                code="discount_preset_not_found",
            )
        snap_label = (match.get("label") or "").strip() or "Discount"
        try:
            snap_percent = Decimal(str(match.get("percent", 0)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DiscountSnapshotError(
                "preset percent is malformed",
                code="invalid_discount_percent",
            ) from exc
        snap_percent = _clamp_percent(snap_percent).quantize(Decimal("0.01"))
        return DiscountRowSnapshot(
            preset_id=raw_id, label=snap_label, percent=snap_percent
        )

    # Custom path: percent must be present.
    if parsed_percent is None:
        raise DiscountSnapshotError(
            "discount percent is required when no preset is selected",
            code="discount_percent_required",
        )
    snap_percent = _clamp_percent(parsed_percent).quantize(Decimal("0.01"))
    snap_label = raw_label or "Custom"
    return DiscountRowSnapshot(
        preset_id=None, label=snap_label, percent=snap_percent
    )


def _clamp_percent(value: Decimal) -> Decimal:
    if value < _DISCOUNT_PERCENT_MIN or value > _DISCOUNT_PERCENT_MAX:
        raise DiscountSnapshotError(
            f"discount percent must be between {_DISCOUNT_PERCENT_MIN} "
            f"and {_DISCOUNT_PERCENT_MAX}",
            code="invalid_discount_percent",
        )
    return value
