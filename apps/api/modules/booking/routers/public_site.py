"""Public site endpoints — Day 4.

Unauthenticated, CORS-allowed reads for the customer-facing marketing/sales
site. Mounted at ``/api/public``. Endpoints:

  * ``GET  /inventory`` + ``/inventory/{idOrListingCode}`` — vehicle list/detail
  * ``POST /leads``                                         — lead intake -> vehicle_sale deal
  * ``GET  /business-profile``                              — storefront NAP

Posts/blog/content are intentionally NOT served here: the Next.js site's
Payload CMS owns public content and remains its source of truth. A second
FastAPI posts table would duplicate authoring/publishing/media/SEO surface
for no benefit — the Day 5 site reads content from Payload directly. Revisit
only if there's a concrete reason Payload cannot serve it.

Every vehicle projection is the camelCase ``public_vehicle_dto`` allowlist —
no internal_sku / stock_number / wholesale / source / compat fields ever
reach the wire. Visibility gating (is_vehicle + active + status whitelist)
lives in services.public_inventory_service so the router stays thin.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from api.redis_rate_limit import enforce_or_raise, rate_limit
from config import settings
from database.connection import get_db
from modules.booking.services import booking_service
from modules.core.services import business_profile_service
from modules.core.services import document_storage
from modules.analytics.services import meta_capi_service
from modules.inventory.services import public_inventory_service as inventory
from modules.contacts.services import public_lead_service
from modules.analytics.services import storefront_analytics_service
from modules.core.services.business_profile_service import BusinessProfileError
from modules.inventory.services.public_inventory_service import InventoryFilters
from modules.contacts.services.public_lead_service import LeadInput, PublicLeadError
from modules.analytics.services.storefront_analytics_service import TrackingContext

log = logging.getLogger(__name__)

router = APIRouter()

# Per-IP cap on lead submissions. The TestClient bypass in redis_rate_limit
# means smokes don't trip this unless they set X-Forwarded-For explicitly.
_lead_ip_limit = rate_limit(bucket="public_lead_ip", limit=10, window=600)

_STATE_NAME_TO_CODE = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


def _normalize_state_code(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    cleaned = raw.strip().upper()
    if not cleaned:
        return raw
    return _STATE_NAME_TO_CODE.get(cleaned, cleaned)


class InventoryListResponse(BaseModel):
    # `items` is a list of public_vehicle_dto dicts. We deliberately do NOT
    # pin a per-item model here: the DTO in catalog_service is the single
    # source of the public contract (and asserts no forbidden keys), so a
    # second schema would just be a copy to drift out of sync.
    items: list[dict[str, Any]]
    total: int
    page: int
    limit: int


@router.get("/inventory", response_model=InventoryListResponse)
def list_inventory(
    db: Annotated[Session, Depends(get_db)],
    make: str | None = None,
    model: str | None = None,
    body_type: str | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    drivetrain: str | None = None,
    min_price: int | None = Query(default=None, ge=0, description="Whole USD."),
    max_price: int | None = Query(default=None, ge=0, description="Whole USD."),
    min_year: int | None = Query(default=None, ge=1980),
    max_year: int | None = Query(default=None, ge=1980),
    max_mileage: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, max_length=120),
    status: str | None = Query(
        default=None,
        description="Public list status filter: 'available' (default) or "
        "'pending'. Other values fall back to the default.",
    ),
    sort: str = Query(default=inventory.DEFAULT_SORT),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=inventory.DEFAULT_LIMIT, ge=1, le=inventory.MAX_LIMIT),
) -> InventoryListResponse:
    """Public, paginated vehicle list. Defaults to in-stock (``available``)
    cars sorted newest-first; hides sold/delivered/hidden/wholesale and any
    non-vehicle or inactive row."""
    if sort not in inventory.SORT_KEYS:
        sort = inventory.DEFAULT_SORT
    # Whole-dollar price params -> cents (the DTO/storage unit).
    filters = InventoryFilters(
        make=make,
        model=model,
        body_type=body_type,
        fuel_type=fuel_type,
        transmission=transmission,
        drivetrain=drivetrain,
        min_price_cents=min_price * 100 if min_price is not None else None,
        max_price_cents=max_price * 100 if max_price is not None else None,
        min_year=min_year,
        max_year=max_year,
        max_mileage=max_mileage,
        q=q,
        status=status,
        sort=sort,
        page=page,
        limit=limit,
    )
    items, total = inventory.list_public_inventory(db, filters)
    return InventoryListResponse(items=items, total=total, page=page, limit=limit)


@router.get("/inventory/{id_or_listing_code}")
def get_inventory_item(
    id_or_listing_code: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Public vehicle detail by numeric id or listingCode (public_code).

    Serves available/pending/sold/delivered; 404 for hidden/wholesale/
    inactive/non-vehicle/unknown."""
    dto = inventory.get_public_vehicle(db, id_or_listing_code)
    if dto is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")
    return dto


class PublicLeadRequest(BaseModel):
    # Tolerate extra keys: a marketing form may post fields we don't model
    # yet, and a public endpoint shouldn't 422 a real customer over one.
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    # Vehicle reference — either is accepted; listing_code wins when both
    # are sent. A ref that no longer points at a for-sale car degrades to a
    # general lead server-side (it is not an error).
    vehicle_id: int | None = None
    listing_code: str | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=4000)
    preferred_day: str | None = Field(default=None, max_length=60)
    preferred_time: str | None = Field(default=None, max_length=60)
    # Structured preferred appointment slot: dealership-local date + hour. When
    # present, the lead becomes a pending appointment on the calendar.
    preferred_date: str | None = Field(default=None, max_length=10)
    preferred_hour: int | None = Field(default=None, ge=0, le=23)
    source_page: str | None = Field(default=None, max_length=500)
    utm_source: str | None = Field(default=None, max_length=120)
    utm_medium: str | None = Field(default=None, max_length=120)
    utm_campaign: str | None = Field(default=None, max_length=120)
    utm_term: str | None = Field(default=None, max_length=120)
    utm_content: str | None = Field(default=None, max_length=120)
    # Structured BHPH application fields (optional). Sensitive values are
    # encrypted at rest in lead_applications and NEVER written to events.notes
    # or the activity_log payload. Sending these as discrete fields (rather
    # than concatenated into `message`) is what keeps PII out of the deal
    # record.
    date_of_birth: str | None = Field(default=None, max_length=40)
    driver_license_number: str | None = Field(default=None, max_length=40)
    driver_license_state: str | None = Field(default=None, max_length=2)
    has_driver_license: bool | None = None
    address_street: str | None = Field(default=None, max_length=200)
    address_city: str | None = Field(default=None, max_length=120)
    address_state: str | None = Field(default=None, max_length=2)
    address_zip: str | None = Field(default=None, max_length=12)
    # A2P 10DLC: True only when the customer actively checked the OPTIONAL
    # SMS-consent box (never pre-checked, never required to submit). Stamped
    # onto the contact as sms_consent_at/_source; outbound SMS is gated on it.
    sms_consent: bool = False
    # Honeypot — must stay empty. A bot that fills it gets a normal-looking
    # acknowledgement and no record is written.
    company_website: str | None = Field(default=None, max_length=200)
    # Turnstile token: accepted for forward-compat with the contract, not
    # verified until a TURNSTILE_SECRET is wired up.
    turnstile_token: str | None = Field(default=None, max_length=4000)
    # First-party analytics identity + Meta attribution cookies. All optional
    # — a lead without them still creates a deal, just with no journey. These
    # are pseudonymous ids/cookies, NOT application PII.
    ka_vid: str | None = Field(default=None, max_length=64)
    ka_sid: str | None = Field(default=None, max_length=64)
    event_id: str | None = Field(default=None, max_length=64)
    fbp: str | None = Field(default=None, max_length=255)
    fbc: str | None = Field(default=None, max_length=255)
    # Ad click ids captured on the landing URL. These let a UTM-less paid
    # click still attribute (fbclid→facebook, gclid→google, msclkid→bing).
    fbclid: str | None = Field(default=None, max_length=255)
    gclid: str | None = Field(default=None, max_length=255)
    msclkid: str | None = Field(default=None, max_length=255)
    landing_page: str | None = Field(default=None, max_length=1000)
    referrer: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def _normalize_state_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            normalized = dict(data)
            for key in ("driver_license_state", "address_state"):
                if key in normalized:
                    normalized[key] = _normalize_state_code(normalized[key])
            return normalized
        return data

    @model_validator(mode="after")
    def _require_contact_channel(self) -> "PublicLeadRequest":
        if not (self.phone and self.phone.strip()) and not (
            self.email and self.email.strip()
        ):
            raise ValueError("either phone or email is required")
        return self

    def vehicle_ref(self) -> str | None:
        if self.listing_code and self.listing_code.strip():
            return self.listing_code.strip()
        if self.vehicle_id is not None:
            return str(self.vehicle_id)
        return None

    def utm(self) -> dict[str, str]:
        pairs = {
            "source": self.utm_source,
            "medium": self.utm_medium,
            "campaign": self.utm_campaign,
            "term": self.utm_term,
            "content": self.utm_content,
        }
        return {k: v for k, v in pairs.items() if v}

    def tracking(self) -> TrackingContext:
        """Assemble the analytics/attribution context off the lead payload."""
        return TrackingContext(
            visitor_key=(self.ka_vid or None),
            session_key=(self.ka_sid or None),
            event_id=(self.event_id or None),
            landing_page=(self.landing_page or None),
            source_page=(self.source_page or None),
            referrer=(self.referrer or None),
            utm=self.utm(),
            fbp=(self.fbp or None),
            fbc=(self.fbc or None),
            fbclid=(self.fbclid or None),
            gclid=(self.gclid or None),
            msclkid=(self.msclkid or None),
            listing_code=(self.listing_code or None),
            vehicle_id=self.vehicle_id,
        )

    def address(self) -> dict[str, str] | None:
        """Assemble the home-address parts into a dict, or None if all blank.
        Stored encrypted in the application table, never in notes."""
        parts = {
            "street": self.address_street,
            "city": self.address_city,
            "state": self.address_state,
            "zip": self.address_zip,
        }
        cleaned = {k: v.strip() for k, v in parts.items() if v and v.strip()}
        return cleaned or None


class PublicLeadResponse(BaseModel):
    ok: bool
    message: str


# Fixed acknowledgement for EVERY successful path — new deal, duplicate
# append, or honeypot drop. Never leaks IDs or whether a contact/deal
# already existed.
_LEAD_ACK = PublicLeadResponse(ok=True, message="Thanks, we received your request.")


@router.post(
    "/leads",
    response_model=PublicLeadResponse,
    dependencies=[Depends(_lead_ip_limit)],
)
def submit_lead(
    payload: PublicLeadRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> PublicLeadResponse:
    """Public lead intake. Creates or appends to a vehicle_sale deal and
    returns a generic acknowledgement (no IDs, no existence hints)."""
    # Honeypot: acknowledge like normal, write nothing.
    if payload.company_website and payload.company_website.strip():
        log.info("public_lead.honeypot_triggered")
        return _LEAD_ACK

    # Per-identifier cap so one email/phone can't hammer the endpoint past
    # the per-IP bucket (e.g. rotating IPs). request= honors the TestClient
    # bypass so unrelated smokes don't 429.
    ident = (payload.email or payload.phone or "").strip().lower()
    if ident:
        enforce_or_raise(
            bucket="public_lead_identifier",
            scoped=ident,
            limit=5,
            window=600,
            request=request,
        )

    lead = LeadInput(
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        vehicle_ref=payload.vehicle_ref(),
        message=payload.message,
        preferred_day=payload.preferred_day,
        preferred_time=payload.preferred_time,
        preferred_date=payload.preferred_date,
        preferred_hour=payload.preferred_hour,
        source_page=payload.source_page,
        utm=payload.utm(),
        date_of_birth=payload.date_of_birth,
        driver_license_number=payload.driver_license_number,
        driver_license_state=payload.driver_license_state,
        has_driver_license=payload.has_driver_license,
        address=payload.address(),
        sms_consent=payload.sms_consent,
    )
    # Request context rides on the tracking payload for Meta CAPI matching
    # (client IP + UA are required unhashed for web events). The analytics
    # tables themselves still only ever store a hashed IP.
    tracking = payload.tracking()
    tracking.client_ip = _client_ip(request)
    tracking.user_agent = request.headers.get("user-agent")

    try:
        public_lead_service.submit_public_lead(db, lead, tracking=tracking)
    except PublicLeadError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from exc

    db.commit()
    # Fast path: push the queued CAPI event to Meta right after the response
    # goes out (no-op until META_CAPI_ENABLED + credentials are configured).
    background_tasks.add_task(meta_capi_service.flush_queue)
    return _LEAD_ACK


# Per-IP cap on analytics beacons. Higher than leads — a real session fires
# many events — but still bounded so a single client can't flood the table.
_track_ip_limit = rate_limit(bucket="storefront_track_ip", limit=120, window=60)


class TrackEventRequest(BaseModel):
    # A public beacon endpoint must be maximally forgiving: ignore unknown keys
    # and never 422 a real visitor over a malformed field.
    model_config = ConfigDict(extra="ignore")

    ka_vid: str | None = Field(default=None, max_length=64)
    ka_sid: str | None = Field(default=None, max_length=64)
    event_name: str = Field(max_length=50)
    event_id: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=1000)
    referrer: str | None = Field(default=None, max_length=1000)
    landing_page: str | None = Field(default=None, max_length=1000)
    utm_source: str | None = Field(default=None, max_length=120)
    utm_medium: str | None = Field(default=None, max_length=120)
    utm_campaign: str | None = Field(default=None, max_length=120)
    utm_term: str | None = Field(default=None, max_length=120)
    utm_content: str | None = Field(default=None, max_length=120)
    fbclid: str | None = Field(default=None, max_length=255)
    gclid: str | None = Field(default=None, max_length=255)
    msclkid: str | None = Field(default=None, max_length=255)
    listing_code: str | None = Field(default=None, max_length=40)
    vehicle_id: int | None = None
    metadata: dict[str, Any] | None = None

    def click_ids(self) -> dict[str, str]:
        pairs = {
            "fbclid": self.fbclid,
            "gclid": self.gclid,
            "msclkid": self.msclkid,
        }
        return {k: v for k, v in pairs.items() if v}

    def utm(self) -> dict[str, str]:
        pairs = {
            "source": self.utm_source,
            "medium": self.utm_medium,
            "campaign": self.utm_campaign,
            "term": self.utm_term,
            "content": self.utm_content,
        }
        return {k: v for k, v in pairs.items() if v}


class TrackAck(BaseModel):
    ok: bool


_TRACK_ACK = TrackAck(ok=True)


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/track",
    response_model=TrackAck,
    dependencies=[Depends(_track_ip_limit)],
)
def track_event(
    payload: TrackEventRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TrackAck:
    """First-party analytics beacon. Records one storefront event against the
    visitor/session. Always returns a plain ack — analytics never errors a
    shopper, and delivery is disabled entirely when the kill switch is off."""
    if not settings.STOREFRONT_ANALYTICS_ENABLED:
        return _TRACK_ACK

    # Crawlers, link-preview renderers, and Meta's ad-review fetches get the
    # same friendly ack and no row — stored, they'd masquerade as shoppers.
    user_agent = request.headers.get("user-agent")
    if storefront_analytics_service.is_bot_user_agent(user_agent):
        return _TRACK_ACK

    ip_hash = booking_service.hash_ip(_client_ip(request))
    try:
        storefront_analytics_service.record_event(
            db,
            visitor_key=payload.ka_vid,
            session_key=payload.ka_sid,
            event_name=payload.event_name,
            event_id=payload.event_id,
            path=payload.path,
            referrer=payload.referrer,
            utm=payload.utm(),
            click_ids=payload.click_ids(),
            listing_code=payload.listing_code,
            vehicle_id=payload.vehicle_id,
            metadata=payload.metadata,
            landing_page=payload.landing_page,
            user_agent=user_agent,
            ip_hash=ip_hash,
        )
        db.commit()
    except Exception:  # analytics is best-effort; never fail the beacon
        db.rollback()
        log.exception("storefront track failed event_name=%s", payload.event_name)
    return _TRACK_ACK


@router.get("/business-profile")
def get_business_profile(
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Public storefront NAP — name, address, phone, email, website. No
    tax/invoice/reminder/operational fields."""
    try:
        return business_profile_service.get_public_profile(db)
    except BusinessProfileError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from exc


_MEDIA_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


@router.get("/media/{key:path}")
def get_public_media(key: str) -> Response:
    """Serve a stored vehicle photo by storage key. PUBLIC + unauthenticated,
    but STRICTLY scoped to the ``vehicles/`` prefix so business logos, event
    documents, and any other stored object can never be read through here.
    ``document_storage.resolve_path`` independently rejects path traversal."""
    if not key.startswith("vehicles/"):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        path = document_storage.resolve_path(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    media_type = _MEDIA_CONTENT_TYPES.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media_type)
