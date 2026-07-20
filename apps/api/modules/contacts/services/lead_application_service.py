"""Read/write BHPH lead-application PII with encryption + audit.

The ONLY module that decrypts ``lead_applications`` rows. Every decrypted read
and every write emits an ``activity_log`` row (``application.pii_viewed`` /
``application.pii_updated``) so there is a permanent who-touched-the-PII trail.
Audit payloads record WHICH fields were present/changed — never the values.

Sensitive fields (``date_of_birth``, ``driver_license_number``, ``ssn``,
``address``) are Fernet ciphertext via ``services/lead_pii_crypto.py``.
``driver_license_state`` and ``has_driver_license`` are plaintext workflow
fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from database.models import LeadApplication
from services import activity_log
from modules.contacts.services import lead_pii_crypto

log = logging.getLogger(__name__)

# The sensitive (encrypted) field names, in display order. Used to build audit
# "which fields" lists without ever touching values.
_SENSITIVE_FIELDS = ("date_of_birth", "driver_license_number", "ssn", "address")


@dataclass
class ApplicationInput:
    """Structured BHPH fields from intake or a staff edit. All optional; only
    provided (non-None) fields are written."""

    date_of_birth: str | None = None
    driver_license_number: str | None = None
    ssn: str | None = None
    address: dict[str, Any] | None = None
    driver_license_state: str | None = None
    has_driver_license: bool | None = None

    def is_empty(self) -> bool:
        return all(
            getattr(self, f.name) in (None, "", {})
            for f in self.__dataclass_fields__.values()  # type: ignore[attr-defined]
        )

    def present_sensitive(self) -> list[str]:
        """Names of the encrypted fields that carry a value (for audit)."""
        out = []
        for name in _SENSITIVE_FIELDS:
            v = getattr(self, name)
            if v not in (None, "", {}):
                out.append(name)
        return out


def _apply_to_row(row: LeadApplication, data: ApplicationInput) -> None:
    """Encrypt/set only the provided fields onto ``row`` (partial update)."""
    if data.date_of_birth is not None:
        row.date_of_birth_ciphertext = lead_pii_crypto.encrypt_optional(
            data.date_of_birth
        )
    if data.driver_license_number is not None:
        row.driver_license_number_ciphertext = lead_pii_crypto.encrypt_optional(
            data.driver_license_number
        )
    if data.ssn is not None:
        row.ssn_ciphertext = lead_pii_crypto.encrypt_optional(data.ssn)
    if data.address is not None:
        row.address_ciphertext = lead_pii_crypto.encrypt_json(data.address)
    if data.driver_license_state is not None:
        row.driver_license_state = data.driver_license_state or None
    if data.has_driver_license is not None:
        row.has_driver_license = data.has_driver_license


def upsert_application(
    db: Session,
    *,
    event_id: int,
    contact_id: int,
    data: ApplicationInput,
    actor_kind: str = "system",
    actor_user_id: int | None = None,
) -> LeadApplication:
    """Create or update the application row for a deal, encrypting sensitive
    fields, and audit the write as ``application.pii_updated``. Caller owns the
    commit. Used by both public intake (actor_kind='system') and staff edits
    (actor_kind='staff', with actor_user_id)."""
    row = (
        db.query(LeadApplication)
        .filter(LeadApplication.event_id == event_id)
        .first()
    )
    created = row is None
    if row is None:
        row = LeadApplication(event_id=event_id, contact_id=contact_id)
        db.add(row)
    _apply_to_row(row, data)
    db.flush()

    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPLICATION_PII_UPDATED,
        subject_kind="event",
        subject_id=event_id,
        payload={
            "created": created,
            "fields_written": data.present_sensitive(),
            "has_driver_license": data.has_driver_license,
            "driver_license_state": data.driver_license_state,
        },
    )
    db.flush()
    return row


def get_application_decrypted(
    db: Session,
    *,
    event_id: int,
    actor_user_id: int,
) -> dict[str, Any] | None:
    """Return the fully decrypted application for a deal, auditing the read as
    ``application.pii_viewed`` with the viewing user's id. Returns None if the
    deal has no application. Call ONLY from the permission-gated endpoint."""
    row = (
        db.query(LeadApplication)
        .filter(LeadApplication.event_id == event_id)
        .first()
    )
    if row is None:
        return None

    result = {
        "event_id": row.event_id,
        "contact_id": row.contact_id,
        "date_of_birth": lead_pii_crypto.decrypt_optional(
            row.date_of_birth_ciphertext
        ),
        "driver_license_number": lead_pii_crypto.decrypt_optional(
            row.driver_license_number_ciphertext
        ),
        "ssn": lead_pii_crypto.decrypt_optional(row.ssn_ciphertext),
        "address": lead_pii_crypto.decrypt_json(row.address_ciphertext),
        "driver_license_state": row.driver_license_state,
        "has_driver_license": row.has_driver_license,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }

    activity_log.log_activity(
        db,
        event_id=event_id,
        actor_kind="staff",
        actor_user_id=actor_user_id,
        activity_type=activity_log.APPLICATION_PII_VIEWED,
        subject_kind="event",
        subject_id=event_id,
        payload={
            "fields_present": [
                f for f in _SENSITIVE_FIELDS if result.get(f) not in (None, "", {})
            ],
        },
    )
    db.flush()
    return result
