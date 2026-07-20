"""NHTSA vPIC VIN decode.

Thin wrapper over the free, key-less vPIC ``DecodeVinValues`` endpoint.
Maps the handful of fields our Add-Vehicle form prefills; everything else
in vPIC's ~130-field response is ignored. Network/parse failures degrade
to ``(None, error_code)`` so the caller can say "decode unavailable, enter
manually" instead of 500-ing — decode is a convenience, never a gate.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

_VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"

# vPIC field name -> our vehicle form field. Order is display-ish only.
_FIELD_MAP = {
    "ModelYear": "year",
    "Make": "make",
    "Model": "model",
    "Trim": "trim",
    "BodyClass": "body_type",
    "FuelTypePrimary": "fuel_type",
    "TransmissionStyle": "transmission",
    "DriveType": "drivetrain",
}

# vPIC uses these strings for "no data" in various fields.
_EMPTY_VALUES = {"", "not applicable", "n/a", "null", "0"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _EMPTY_VALUES:
        return None
    return text


def decode(vin: str, *, timeout: float = 8.0) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(decoded_fields, error_code)``.

    ``decoded_fields`` is a dict of our form-field names to values (only
    the fields vPIC actually resolved). On any failure returns
    ``(None, code)`` with code one of ``decode_unavailable`` /
    ``no_result``.
    """
    try:
        resp = httpx.get(
            _VPIC_URL.format(vin=vin),
            params={"format": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("vPIC decode failed for %s: %s", vin, exc)
        return None, "decode_unavailable"

    results = payload.get("Results") or []
    if not results:
        return None, "no_result"
    row = results[0]

    out: dict[str, Any] = {}
    for src, dst in _FIELD_MAP.items():
        cleaned = _clean(row.get(src))
        if cleaned is not None:
            out[dst] = cleaned

    if "year" in out:
        try:
            out["year"] = int(out["year"])
        except (TypeError, ValueError):
            out.pop("year", None)

    # vPIC returns Make in ALL CAPS ("NISSAN"); title-case it for display
    # unless it's a short all-caps brand (GMC, BMW, KIA, RAM) we leave alone.
    make = out.get("make")
    if make and make.isupper() and len(make) > 3:
        out["make"] = make.title()

    # vPIC BodyClass is verbose ("Sport Utility Vehicle [SUV]/..."). If it
    # carries a bracketed abbreviation, prefer that short form for the
    # form's free-text body_type field (staff can still edit it).
    body = out.get("body_type")
    if body:
        match = re.search(r"\[([A-Za-z0-9/ ]{1,6})\]", body)
        if match:
            out["body_type"] = match.group(1).strip()

    if not out:
        return None, "no_result"
    return out, None
