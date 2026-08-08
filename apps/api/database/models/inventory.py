"""Catalog items (vehicles) and special orders.

Split from the former monolithic database/models.py (Phase 3). All classes
subclass the single Base from database.connection; foreign keys are string
references, so cross-domain FKs need no import between these files.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID

from database.connection import Base



class CatalogItem(Base):
    """One row per orderable style + color combination.

    Two identifier semantics:
      - `internal_sku`: real designer SKU staff types and searches by.
        Never returned from public/customer-facing endpoints.
      - `public_code`: opaque customer-facing code (KAP-NNNNN) minted by
        services/catalog_service.py under a row-level lock on
        numbering_state. Once assigned, never rewritten by service code;
        Phase 7 will add a DB trigger as belt-and-suspenders.

    The category whitelist, image_urls array shape, and public_code
    format (^KAP-[0-9]{5}$) are enforced by CHECK constraints in
    migration 041; if you change those rules, change the migration.
    """

    __tablename__ = "catalog_items"

    id = Column(Integer, primary_key=True)
    internal_sku = Column(String(160), unique=True, nullable=False)
    public_code = Column(String(32), unique=True, nullable=False)
    designer = Column(String(120))
    style_number = Column(String(80))
    color = Column(String(80), nullable=False)
    house_name = Column(String(120))
    product_title = Column(String(200))
    category = Column(String(40), nullable=False)
    description_text = Column(Text)
    image_urls = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Per-photo alt text (migration 102), keyed by the URL in `image_urls`
    # rather than by position — the admin grid reorders photos, and a
    # positional map would silently start describing the wrong picture.
    image_alts = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # How this car is sold (migration 103): 'bhph' (dealer carries the
    # note — the lot's default) or 'cash' (sold outright, no financing).
    sale_type = Column(String(16), nullable=False, server_default=text("'bhph'"))
    source_platform = Column(String(40))
    source_product_id = Column(String(80))
    source_product_handle = Column(String(160))
    source_product_url = Column(Text)
    source_collection_url = Column(Text)
    source_product_type = Column(String(120))
    is_sample = Column(Boolean, nullable=False, server_default=text("FALSE"))
    active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    unit_price_cents = Column(Integer)
    # Vehicle inventory overlay (migration 085). `is_vehicle` is the
    # discriminator; legacy gown rows may have mirrored vehicle fields from
    # the compatibility backfill but keep is_vehicle=false.
    is_vehicle = Column(Boolean, nullable=False, server_default=text("FALSE"))
    vin = Column(String(17))
    stock_number = Column(String(64))
    year = Column(SmallInteger)
    make = Column(String(80))
    model = Column(String(80))
    trim = Column(String(80))
    mileage = Column(Integer)
    transmission = Column(String(40))
    fuel_type = Column(String(40))
    exterior_color = Column(String(60))
    interior_color = Column(String(60))
    body_type = Column(String(40))
    drivetrain = Column(String(20))
    condition = Column(String(20))
    vehicle_status = Column(String(20))
    carfax_url = Column(Text)
    video_url = Column(Text)
    features_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Wholesale inputs/provenance behind the computed unit_price_cents.
    # See services/pricing.py and migration 084.
    wholesale_cents = Column(Integer)
    wholesale_as_of = Column(Date)
    wholesale_source = Column(Text)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class SpecialOrder(Base):
    """One row per "where is my dress?" lifecycle entry.

    Phase 5 of the catalog SKU obfuscation plan. Tracks the
    needed → ordered → received → picked_up flow against a
    catalog-backed line without modeling stock counts, vendor
    integrations, or warehouse locations.

    Status vocabulary, the picked-up-requires-received invariant, and
    the ``status='ordered'/'received'/'picked_up'`` ↔ corresponding
    timestamp checks are enforced by CHECK constraints in migration
    043. The service layer enforces the same rules in Python so the
    error messages are friendlier than the DB violation, but the
    constraints are the back-stop.

    ``invoice_line_item_id`` is ON DELETE SET NULL because staff edit
    invoices freely; ``catalog_item_id`` and ``event_id`` are ON
    DELETE RESTRICT because losing either would orphan the lifecycle
    log without a way to reconstruct what was on order.

    ``vendor_order_number`` and ``internal_notes`` are staff-only;
    they are NEVER returned from public endpoints, embedded in
    activity payloads that customers can read, or rendered on any
    customer surface. Phase 7's lint will assert that.
    """

    __tablename__ = "special_orders"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_line_item_id = Column(
        Integer,
        ForeignKey("invoice_line_items.id", ondelete="SET NULL"),
    )
    catalog_item_id = Column(
        Integer,
        ForeignKey("catalog_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    size_label = Column(String(40), nullable=False)
    status = Column(String(24), nullable=False, server_default=text("'needed'"))
    ordered_at = Column(DateTime(timezone=True))
    eta_date = Column(Date)
    received_at = Column(DateTime(timezone=True))
    picked_up_at = Column(DateTime(timezone=True))
    vendor_order_number = Column(String(120))
    internal_notes = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


# ---------------------------------------------------------------------------
# Phase 7 Slice 1 of the Sales Portal — clock-in foundation. Selfie
# storage, owner attendance review UI, and the punched-out gate on
# existing sales endpoints arrive in Slice 2.
# ---------------------------------------------------------------------------


