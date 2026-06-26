"""Public site endpoints — Day 4.

Unauthenticated, CORS-allowed reads for the customer-facing marketing/sales
site. Mounted at ``/api/public``. This slice ships the vehicle inventory
contract (list + detail); the leads / posts / business-profile endpoints in
the Day 4 plan are separate follow-ups.

Every vehicle projection is the camelCase ``public_vehicle_dto`` allowlist —
no internal_sku / stock_number / wholesale / source / compat fields ever
reach the wire. Visibility gating (is_vehicle + active + status whitelist)
lives in services.public_inventory_service so the router stays thin.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from api.redis_rate_limit import enforce_or_raise, rate_limit
from database.connection import get_db
from services import public_inventory_service as inventory
from services import public_lead_service
from services.public_inventory_service import InventoryFilters
from services.public_lead_service import LeadInput, PublicLeadError

log = logging.getLogger(__name__)

router = APIRouter()

# Per-IP cap on lead submissions. The TestClient bypass in redis_rate_limit
# means smokes don't trip this unless they set X-Forwarded-For explicitly.
_lead_ip_limit = rate_limit(bucket="public_lead_ip", limit=10, window=600)


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
    source_page: str | None = Field(default=None, max_length=500)
    utm_source: str | None = Field(default=None, max_length=120)
    utm_medium: str | None = Field(default=None, max_length=120)
    utm_campaign: str | None = Field(default=None, max_length=120)
    utm_term: str | None = Field(default=None, max_length=120)
    utm_content: str | None = Field(default=None, max_length=120)
    # Honeypot — must stay empty. A bot that fills it gets a normal-looking
    # acknowledgement and no record is written.
    company_website: str | None = Field(default=None, max_length=200)
    # Turnstile token: accepted for forward-compat with the contract, not
    # verified until a TURNSTILE_SECRET is wired up.
    turnstile_token: str | None = Field(default=None, max_length=4000)

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
        source_page=payload.source_page,
        utm=payload.utm(),
    )
    try:
        public_lead_service.submit_public_lead(db, lead)
    except PublicLeadError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from exc

    db.commit()
    return _LEAD_ACK
