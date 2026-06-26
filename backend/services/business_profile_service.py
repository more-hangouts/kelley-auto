"""Business profile singleton.

Holds the legal/branding/render-target data every PDF and portal page
reads. Phase 8 PDF rendering reads this on every render so a profile
update applies retroactively to any re-rendered invoice. Phase 3 ships
the editor surface; the underlying table landed in Phase 1.

Logo upload reuses `services/document_storage` under a fixed key prefix
(`business/logo.<ext>`) so the same disk-space guards and file-type
allowlist apply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO

from sqlalchemy.orm import Session

from database.models import BusinessProfile
from services import document_storage

log = logging.getLogger(__name__)


class BusinessProfileError(Exception):
    """Domain-level rejection — surfaced as 4xx by the router."""

    def __init__(self, message: str, *, code: str = "business_profile_error") -> None:
        super().__init__(message)
        self.code = code


# Editable fields. The singleton id is fixed; created_at/updated_at are
# system-managed; logo_storage_key is updated by `set_logo` not patch.
_EDITABLE_FIELDS = {
    "legal_name",
    "display_name",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
    "phone",
    "email",
    "website",
    "default_tax_rate",
    "default_tax_name",
    "default_invoice_terms",
    "default_invoice_footer",
    "default_payment_instructions",
    # Phase 11 reminder schedule.
    "reminder1_enabled",
    "reminder1_days_offset",
    "reminder1_offset_basis",
    "reminder2_enabled",
    "reminder2_days_offset",
    "reminder2_offset_basis",
    "reminder3_enabled",
    "reminder3_days_offset",
    "reminder3_offset_basis",
    "reminder_late_fee_cents",
    "reminder_late_fee_pct",
    # Phase 1 of the discount/payment-term refactor.
    "discount_presets",
    "default_payment_plan_count",
    "default_deposit_percent",
    # Phase 7 Slice 2 of the Sales Portal — attendance settings. Owner
    # can flip the gate or change the selfie policy without a deploy.
    "attendance_gate_enabled",
    "selfie_policy",
    "selfie_retention_days",
    # Phase 9 sub-slice 1 Priority 2 — biweekly pay-period anchor.
    "biweekly_anchor_date",
    # Phase 10 Slice 6 (Epic 6.2) — target labor % for the schedule
    # grid's sales-goal chip.
    "target_labor_pct",
    # Clock-in reliability slice A — accuracy buffer cap. 0-200.
    "gps_accuracy_buffer_max_m",
    # Clock-in reliability slice C — trusted-network fallback.
    "trusted_network_enabled",
    "trusted_clock_in_ips",
}

_SELFIE_POLICY_VALUES = frozenset({"required", "optional", "disabled"})

_REMINDER_OFFSET_BASES = frozenset({"before_due", "after_due", "after_sent"})

# Discount presets caps (mirrored by the migration check / planning doc).
_DISCOUNT_PRESETS_MAX = 12
_DISCOUNT_PERCENT_MAX = Decimal("50")
_DISCOUNT_LABEL_MAX_LEN = 60
_PRESET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Logo upload allowlist. Only image types; PDFs/DOCX would not render in
# the PDF header anyway. Keep narrow and obvious.
_LOGO_ALLOWED_EXT_TO_CT: dict[str, set[str]] = {
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "svg": {"image/svg+xml"},
}
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — logos are small


@dataclass
class BusinessProfileView:
    legal_name: str
    display_name: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str
    phone: str | None
    email: str | None
    website: str | None
    logo_storage_key: str | None
    default_tax_rate: Decimal
    default_tax_name: str | None
    default_invoice_terms: str | None
    default_invoice_footer: str | None
    default_payment_instructions: str | None
    # Phase 11
    reminder1_enabled: bool
    reminder1_days_offset: int
    reminder1_offset_basis: str
    reminder2_enabled: bool
    reminder2_days_offset: int
    reminder2_offset_basis: str
    reminder3_enabled: bool
    reminder3_days_offset: int
    reminder3_offset_basis: str
    reminder_late_fee_cents: int
    reminder_late_fee_pct: Decimal
    discount_presets: list[dict[str, Any]]
    default_payment_plan_count: int | None
    default_deposit_percent: Decimal | None
    # Phase 7 Slice 2 attendance settings (read path, write path was
    # already wired). The frontend needs these on GET so the form can
    # render the current values.
    attendance_gate_enabled: bool
    selfie_policy: str
    selfie_retention_days: int | None
    # Phase 9 sub-slice 1 Priority 2 — biweekly pay-period anchor.
    biweekly_anchor_date: date | None
    # Phase 10 Slice 6 (Epic 6.2) — target labor cost as a percent of
    # weekly revenue.
    target_labor_pct: Decimal | None
    # Clock-in reliability slice A — owner-tunable accuracy slack cap.
    gps_accuracy_buffer_max_m: int
    # Clock-in reliability slice C — trusted-network fallback.
    trusted_network_enabled: bool
    trusted_clock_in_ips: list[str]
    updated_at: datetime
    updated_by_user_id: int | None


def get_profile(db: Session) -> BusinessProfileView:
    row = db.get(BusinessProfile, 1)
    if row is None:
        raise BusinessProfileError(
            "business profile singleton missing",
            code="business_profile_missing",
        )
    return _to_view(row)


def get_public_profile(db: Session) -> dict[str, Any]:
    """Customer-facing business NAP (name, address, phone) for the public
    site — Day 4.

    An explicit allowlist: only the storefront-public identity + contact
    fields. Deliberately EXCLUDES every operational/financial field on the
    singleton — tax rate/name, invoice terms/footer/payment instructions,
    all reminder settings, attendance/selfie/GPS/trusted-network config,
    labor targets, pay-period anchor, and the updated_by audit. camelCase to
    match the other public DTOs. Returns ``{}``-safe values (nulls) rather
    than raising when optional fields are blank; the singleton always exists
    in a provisioned DB, so a missing row surfaces as the service error.
    """
    v = get_profile(db)
    return {
        "name": v.display_name or v.legal_name,
        "legalName": v.legal_name,
        "address": {
            "line1": v.address_line1,
            "line2": v.address_line2,
            "city": v.city,
            "state": v.state,
            "postalCode": v.postal_code,
            "country": v.country,
        },
        "phone": v.phone,
        "email": v.email,
        "website": v.website,
    }


def update_profile(
    db: Session,
    *,
    patch: dict[str, Any],
    actor_user_id: int | None = None,
) -> BusinessProfileView:
    row = db.get(BusinessProfile, 1)
    if row is None:
        raise BusinessProfileError(
            "business profile singleton missing",
            code="business_profile_missing",
        )

    unknown = set(patch) - _EDITABLE_FIELDS
    if unknown:
        raise BusinessProfileError(
            f"unknown fields: {sorted(unknown)}",
            code="unknown_fields",
        )

    if "legal_name" in patch:
        new_name = (patch["legal_name"] or "").strip()
        if not new_name:
            raise BusinessProfileError(
                "legal_name cannot be empty",
                code="legal_name_required",
            )
        row.legal_name = new_name

    if "country" in patch:
        new_country = (patch["country"] or "").strip().upper() or "US"
        if len(new_country) != 2:
            raise BusinessProfileError(
                "country must be a 2-letter ISO code",
                code="invalid_country",
            )
        row.country = new_country

    if "default_tax_rate" in patch:
        rate_value = patch["default_tax_rate"]
        rate = (
            rate_value
            if isinstance(rate_value, Decimal)
            else Decimal(str(rate_value or 0))
        )
        if rate < 0 or rate >= 1:
            raise BusinessProfileError(
                "default_tax_rate must be in [0, 1)",
                code="invalid_tax_rate",
            )
        row.default_tax_rate = rate

    # Plain string/None passthroughs — let empty strings clear the column.
    for field_name in (
        "display_name",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "phone",
        "email",
        "website",
        "default_tax_name",
        "default_invoice_terms",
        "default_invoice_footer",
        "default_payment_instructions",
    ):
        if field_name in patch:
            value = patch[field_name]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, field_name, value)

    # Phase 11 reminder schedule.
    for slot in ("reminder1", "reminder2", "reminder3"):
        enabled_key = f"{slot}_enabled"
        offset_key = f"{slot}_days_offset"
        basis_key = f"{slot}_offset_basis"
        if enabled_key in patch:
            setattr(row, enabled_key, bool(patch[enabled_key]))
        if offset_key in patch:
            try:
                setattr(row, offset_key, int(patch[offset_key]))
            except (TypeError, ValueError) as exc:
                raise BusinessProfileError(
                    f"{offset_key} must be an integer",
                    code="invalid_reminder_offset",
                ) from exc
        if basis_key in patch:
            basis = (patch[basis_key] or "").strip()
            if basis not in _REMINDER_OFFSET_BASES:
                raise BusinessProfileError(
                    f"{basis_key} must be one of {sorted(_REMINDER_OFFSET_BASES)}",
                    code="invalid_reminder_offset_basis",
                )
            setattr(row, basis_key, basis)

    if "reminder_late_fee_cents" in patch:
        try:
            fee = int(patch["reminder_late_fee_cents"])
        except (TypeError, ValueError) as exc:
            raise BusinessProfileError(
                "reminder_late_fee_cents must be an integer",
                code="invalid_late_fee",
            ) from exc
        if fee < 0:
            raise BusinessProfileError(
                "reminder_late_fee_cents cannot be negative",
                code="invalid_late_fee",
            )
        row.reminder_late_fee_cents = fee
    if "reminder_late_fee_pct" in patch:
        pct_value = patch["reminder_late_fee_pct"]
        pct = (
            pct_value
            if isinstance(pct_value, Decimal)
            else Decimal(str(pct_value or 0))
        )
        if pct < 0 or pct >= 1:
            raise BusinessProfileError(
                "reminder_late_fee_pct must be in [0, 1)",
                code="invalid_late_fee",
            )
        row.reminder_late_fee_pct = pct

    if "discount_presets" in patch:
        row.discount_presets = _normalize_discount_presets(
            patch["discount_presets"]
        )

    if "default_payment_plan_count" in patch:
        count_raw = patch["default_payment_plan_count"]
        if count_raw is None:
            row.default_payment_plan_count = None
        else:
            try:
                count = int(count_raw)
            except (TypeError, ValueError) as exc:
                raise BusinessProfileError(
                    "default_payment_plan_count must be an integer",
                    code="invalid_default_payment_plan_count",
                ) from exc
            if count not in (1, 2, 3):
                raise BusinessProfileError(
                    "default_payment_plan_count must be 1, 2, or 3",
                    code="invalid_default_payment_plan_count",
                )
            row.default_payment_plan_count = count

    if "default_deposit_percent" in patch:
        deposit_raw = patch["default_deposit_percent"]
        if deposit_raw is None or deposit_raw == "":
            row.default_deposit_percent = None
        else:
            deposit = (
                deposit_raw
                if isinstance(deposit_raw, Decimal)
                else _decimal_or_raise(
                    deposit_raw,
                    field="default_deposit_percent",
                    code="invalid_default_deposit_percent",
                )
            )
            if deposit < 50 or deposit > 100:
                raise BusinessProfileError(
                    "default_deposit_percent must be between 50 and 100",
                    code="invalid_default_deposit_percent",
                )
            row.default_deposit_percent = deposit.quantize(Decimal("0.01"))

    # Phase 7 Slice 2 attendance settings.
    if "attendance_gate_enabled" in patch:
        row.attendance_gate_enabled = bool(patch["attendance_gate_enabled"])

    if "selfie_policy" in patch:
        policy = patch["selfie_policy"]
        if policy not in _SELFIE_POLICY_VALUES:
            raise BusinessProfileError(
                f"selfie_policy must be one of {sorted(_SELFIE_POLICY_VALUES)}",
                code="invalid_selfie_policy",
            )
        row.selfie_policy = policy

    if "selfie_retention_days" in patch:
        retention_raw = patch["selfie_retention_days"]
        if retention_raw is None:
            row.selfie_retention_days = None
        else:
            try:
                retention = int(retention_raw)
            except (TypeError, ValueError) as exc:
                raise BusinessProfileError(
                    "selfie_retention_days must be an integer or null",
                    code="invalid_selfie_retention_days",
                ) from exc
            if retention < 1 or retention > 3650:
                raise BusinessProfileError(
                    "selfie_retention_days must be between 1 and 3650",
                    code="invalid_selfie_retention_days",
                )
            row.selfie_retention_days = retention

    # Phase 10 Slice 6 (Epic 6.2) — target labor cost percent.
    if "target_labor_pct" in patch:
        target_raw = patch["target_labor_pct"]
        if target_raw is None or target_raw == "":
            row.target_labor_pct = None
        else:
            target = (
                target_raw
                if isinstance(target_raw, Decimal)
                else _decimal_or_raise(
                    target_raw,
                    field="target_labor_pct",
                    code="invalid_target_labor_pct",
                )
            )
            if target <= 0 or target > 100:
                raise BusinessProfileError(
                    "target_labor_pct must be greater than 0 and at most 100",
                    code="invalid_target_labor_pct",
                )
            row.target_labor_pct = target.quantize(Decimal("0.01"))

    # Clock-in reliability slice C — trusted-network fallback.
    if "trusted_network_enabled" in patch:
        row.trusted_network_enabled = bool(patch["trusted_network_enabled"])

    if "trusted_clock_in_ips" in patch:
        raw_ips = patch["trusted_clock_in_ips"]
        if raw_ips is None:
            raw_ips = []
        if not isinstance(raw_ips, list):
            raise BusinessProfileError(
                "trusted_clock_in_ips must be an array",
                code="invalid_trusted_clock_in_ips",
            )
        # Normalize + validate each entry. We accept both single IPs
        # and CIDR networks; anything that ipaddress refuses to parse
        # is a typo we want to reject at PATCH time so the owner gets
        # immediate feedback rather than a silently-ignored entry.
        import ipaddress

        normalized: list[str] = []
        for raw in raw_ips:
            if not isinstance(raw, str):
                raise BusinessProfileError(
                    "trusted_clock_in_ips entries must be strings",
                    code="invalid_trusted_clock_in_ips",
                )
            token = raw.strip()
            if not token:
                continue
            try:
                if "/" in token:
                    ipaddress.ip_network(token, strict=False)
                else:
                    ipaddress.ip_address(token)
            except (TypeError, ValueError) as exc:
                raise BusinessProfileError(
                    f"trusted_clock_in_ips entry not a valid IP or CIDR: {token}",
                    code="invalid_trusted_clock_in_ips",
                ) from exc
            normalized.append(token)
        row.trusted_clock_in_ips = normalized

    # Clock-in reliability slice A — accuracy buffer cap.
    if "gps_accuracy_buffer_max_m" in patch:
        buf_raw = patch["gps_accuracy_buffer_max_m"]
        try:
            buf = int(buf_raw)
        except (TypeError, ValueError) as exc:
            raise BusinessProfileError(
                "gps_accuracy_buffer_max_m must be an integer",
                code="invalid_gps_accuracy_buffer_max_m",
            ) from exc
        if buf < 0 or buf > 200:
            raise BusinessProfileError(
                "gps_accuracy_buffer_max_m must be between 0 and 200",
                code="invalid_gps_accuracy_buffer_max_m",
            )
        row.gps_accuracy_buffer_max_m = buf

    # Phase 9 sub-slice 1 Priority 2 — biweekly pay-period anchor.
    # Membership-only check so an explicit null clears the column and
    # the reporting service falls back to its rolling 14-day window.
    if "biweekly_anchor_date" in patch:
        anchor_raw = patch["biweekly_anchor_date"]
        if anchor_raw is None or anchor_raw == "":
            row.biweekly_anchor_date = None
        elif isinstance(anchor_raw, date):
            row.biweekly_anchor_date = anchor_raw
        else:
            try:
                row.biweekly_anchor_date = date.fromisoformat(str(anchor_raw))
            except ValueError as exc:
                raise BusinessProfileError(
                    "biweekly_anchor_date must be an ISO date or null",
                    code="invalid_biweekly_anchor_date",
                ) from exc

    row.updated_by_user_id = actor_user_id
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return _to_view(row)


def _logo_extension(filename: str, content_type: str) -> str:
    """Pick the on-disk extension. Trust the client's filename suffix when
    it matches the allowlist; otherwise fall back to the content_type."""
    if "." in filename:
        ext = filename.rsplit(".", 1)[1].lower()
        if ext in _LOGO_ALLOWED_EXT_TO_CT:
            return ext
    ct = (content_type or "").lower()
    for ext, cts in _LOGO_ALLOWED_EXT_TO_CT.items():
        if ct in cts:
            return ext
    raise BusinessProfileError(
        "unsupported logo type — allowed: png, jpg, svg",
        code="unsupported_logo_type",
    )


def set_logo(
    db: Session,
    *,
    filename: str,
    content_type: str,
    fileobj: BinaryIO,
    byte_size: int,
    actor_user_id: int | None = None,
) -> BusinessProfileView:
    """Replace the business logo. Stores under
    `business/logo.<ext>` so the file always overwrites the prior one and
    the storage_key is predictable. Returns the refreshed profile view."""
    if byte_size <= 0:
        raise BusinessProfileError("logo file is empty", code="empty_file")
    if byte_size > _LOGO_MAX_BYTES:
        raise BusinessProfileError(
            f"logo too large ({byte_size} bytes, max {_LOGO_MAX_BYTES})",
            code="logo_too_large",
        )
    ext = _logo_extension(filename, content_type)

    # Disk-space guard mirrors the document upload route's floor.
    usage = document_storage.disk_usage()
    if usage.free < _LOGO_MAX_BYTES * 4:
        raise BusinessProfileError(
            "insufficient disk space for logo upload",
            code="insufficient_storage",
        )

    storage_key = f"business/logo.{ext}"
    # Delete any prior logo file under a different extension so we don't
    # leave orphans (e.g. logo.png → logo.svg replacement).
    for other_ext in _LOGO_ALLOWED_EXT_TO_CT:
        if other_ext != ext:
            document_storage.delete_object(f"business/logo.{other_ext}")
    document_storage.put_object(storage_key, fileobj)

    row = db.get(BusinessProfile, 1)
    row.logo_storage_key = storage_key
    row.updated_by_user_id = actor_user_id
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    log.info(
        "business_profile.logo_uploaded",
        extra={
            "user_id": actor_user_id,
            "storage_key": storage_key,
            "byte_size": byte_size,
        },
    )
    return _to_view(row)


def remove_logo(
    db: Session, *, actor_user_id: int | None = None
) -> BusinessProfileView:
    row = db.get(BusinessProfile, 1)
    if row.logo_storage_key:
        document_storage.delete_object(row.logo_storage_key)
        row.logo_storage_key = None
        row.updated_by_user_id = actor_user_id
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
    return _to_view(row)


def _decimal_or_raise(value: Any, *, field: str, code: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BusinessProfileError(
            f"{field} must be a number", code=code
        ) from exc


def _slugify_preset_label(label: str) -> str:
    """Generate a stable id from the label when the client did not send one.
    Lowercase, ASCII-ish slug; we strip everything outside [a-z0-9_-]."""
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return base[:40] or "preset"


def _normalize_discount_presets(value: Any) -> list[dict[str, Any]]:
    """Validate and normalize the presets list into the storage shape.

    Replaces in full (no merge). Service layer fills in missing ids,
    rejects duplicates, caps the list, and clamps the percent range.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessProfileError(
            "discount_presets must be a list",
            code="invalid_discount_presets",
        )
    if len(value) > _DISCOUNT_PRESETS_MAX:
        raise BusinessProfileError(
            f"discount_presets cannot exceed {_DISCOUNT_PRESETS_MAX} entries",
            code="too_many_discount_presets",
        )

    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise BusinessProfileError(
                f"discount_presets[{idx}] must be an object",
                code="invalid_discount_presets",
            )
        label = (raw.get("label") or "").strip()
        if not label:
            raise BusinessProfileError(
                f"discount_presets[{idx}].label is required",
                code="invalid_discount_preset_label",
            )
        if len(label) > _DISCOUNT_LABEL_MAX_LEN:
            raise BusinessProfileError(
                f"discount_presets[{idx}].label exceeds "
                f"{_DISCOUNT_LABEL_MAX_LEN} characters",
                code="invalid_discount_preset_label",
            )

        percent = _decimal_or_raise(
            raw.get("percent", 0),
            field=f"discount_presets[{idx}].percent",
            code="invalid_discount_preset_percent",
        )
        if percent < 0 or percent > _DISCOUNT_PERCENT_MAX:
            raise BusinessProfileError(
                f"discount_presets[{idx}].percent must be between 0 and "
                f"{_DISCOUNT_PERCENT_MAX}",
                code="invalid_discount_preset_percent",
            )

        active = bool(raw.get("active", True))

        preset_id = (raw.get("id") or "").strip().lower()
        if not preset_id:
            preset_id = _slugify_preset_label(label)
            # Disambiguate against ids we have already seen this round.
            base_id = preset_id
            n = 2
            while preset_id in seen_ids:
                preset_id = f"{base_id}_{n}"
                n += 1
        elif not _PRESET_ID_RE.match(preset_id):
            raise BusinessProfileError(
                f"discount_presets[{idx}].id must match [a-z0-9_-]+",
                code="invalid_discount_preset_id",
            )

        if preset_id in seen_ids:
            raise BusinessProfileError(
                f"duplicate discount preset id '{preset_id}'",
                code="duplicate_discount_preset_id",
            )
        seen_ids.add(preset_id)

        # Quantize percent to two decimal places and store as a string so
        # the JSONB blob preserves precision across PATCH round-trips
        # (floats would silently drop trailing zeros).
        quantized = percent.quantize(Decimal("0.01"))
        normalized.append(
            {
                "id": preset_id,
                "label": label,
                "percent": str(quantized),
                "active": active,
            }
        )
    return normalized


def _to_view(row: BusinessProfile) -> BusinessProfileView:
    return BusinessProfileView(
        legal_name=row.legal_name,
        display_name=row.display_name,
        address_line1=row.address_line1,
        address_line2=row.address_line2,
        city=row.city,
        state=row.state,
        postal_code=row.postal_code,
        country=row.country,
        phone=row.phone,
        email=row.email,
        website=row.website,
        logo_storage_key=row.logo_storage_key,
        default_tax_rate=Decimal(str(row.default_tax_rate or 0)),
        default_tax_name=row.default_tax_name,
        default_invoice_terms=row.default_invoice_terms,
        default_invoice_footer=row.default_invoice_footer,
        default_payment_instructions=row.default_payment_instructions,
        reminder1_enabled=bool(row.reminder1_enabled),
        reminder1_days_offset=int(row.reminder1_days_offset or 0),
        reminder1_offset_basis=row.reminder1_offset_basis or "before_due",
        reminder2_enabled=bool(row.reminder2_enabled),
        reminder2_days_offset=int(row.reminder2_days_offset or 0),
        reminder2_offset_basis=row.reminder2_offset_basis or "before_due",
        reminder3_enabled=bool(row.reminder3_enabled),
        reminder3_days_offset=int(row.reminder3_days_offset or 0),
        reminder3_offset_basis=row.reminder3_offset_basis or "before_due",
        reminder_late_fee_cents=int(row.reminder_late_fee_cents or 0),
        reminder_late_fee_pct=Decimal(str(row.reminder_late_fee_pct or 0)),
        discount_presets=list(row.discount_presets or []),
        default_payment_plan_count=(
            int(row.default_payment_plan_count)
            if row.default_payment_plan_count is not None
            else None
        ),
        default_deposit_percent=(
            Decimal(str(row.default_deposit_percent))
            if row.default_deposit_percent is not None
            else None
        ),
        attendance_gate_enabled=bool(row.attendance_gate_enabled),
        selfie_policy=row.selfie_policy or "optional",
        selfie_retention_days=(
            int(row.selfie_retention_days)
            if row.selfie_retention_days is not None
            else None
        ),
        biweekly_anchor_date=row.biweekly_anchor_date,
        target_labor_pct=(
            Decimal(str(row.target_labor_pct))
            if row.target_labor_pct is not None
            else None
        ),
        gps_accuracy_buffer_max_m=int(
            row.gps_accuracy_buffer_max_m
            if row.gps_accuracy_buffer_max_m is not None
            else 50
        ),
        trusted_network_enabled=bool(row.trusted_network_enabled),
        trusted_clock_in_ips=[
            str(x) for x in (row.trusted_clock_in_ips or []) if isinstance(x, str)
        ],
        updated_at=row.updated_at,
        updated_by_user_id=row.updated_by_user_id,
    )
