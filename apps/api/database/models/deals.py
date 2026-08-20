"""Deals engine: events, invoices, quotes, payments, lead applications.

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



class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    primary_contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    event_type = Column(String(32), nullable=False)
    event_name = Column(String(200), nullable=False)
    event_date = Column(Date)
    court_size = Column(Integer)
    quince_theme = Column(String(200))
    quince_theme_colors = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    budget_range = Column(String(50))
    status = Column(String(32), nullable=False, server_default=text("'lead'"))
    status_changed_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    sales_credit_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    notes = Column(Text)
    # Day 3: optional link from a vehicle_sale deal to the catalog_items row
    # of the car being sold. Nullable — general leads and quinceañera events
    # leave it NULL. ON DELETE SET NULL so removing a vehicle never blocks
    # or cascades into its deal history.
    vehicle_catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="SET NULL")
    )
    # Migration 111: the car the deal actually CLOSED on, when it is not the
    # car the lead came in on. Above is attribution ("which listing produced
    # this lead?") and is written once at intake; this is the outcome ("which
    # car left the lot?") and is written by staff at the end. NULL — the normal
    # state — means "closed on the car they asked about", and the inventory
    # propagation falls back to `vehicle_catalog_item_id` accordingly.
    sold_vehicle_catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="SET NULL")
    )
    # Migration 104: staff-entered origin for leads that never touch the
    # storefront (walk-ins, phone-ins). Deliberately separate from the
    # derived source/medium attribution on storefront_events — a person who
    # walked through the door has no click data, and merging the two would
    # make the analytics lie. `walk_in_source` is the reportable bucket;
    # `walk_in_source_detail` is free text ("Facebook video") that is never
    # grouped on.
    walk_in_source = Column(String(32))
    walk_in_source_detail = Column(String(200))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class LeadApplication(Base):
    """Sensitive BHPH application PII for a vehicle-sale deal (migration 089).

    1:1 with an ``events`` row. High-sensitivity fields are Fernet ciphertext
    (BYTEA), read/written only through ``services/lead_application_service.py``
    which decrypts inside the permission-gated, audited endpoint. This table is
    deliberately NOT joined into the normal event serializers — application PII
    must never be fetched, rendered, emailed, or exported with the deal by
    accident.
    """

    __tablename__ = "lead_applications"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    # Fernet ciphertext (services/lead_pii_crypto.py):
    date_of_birth_ciphertext = Column(LargeBinary)
    driver_license_number_ciphertext = Column(LargeBinary)
    ssn_ciphertext = Column(LargeBinary)
    address_ciphertext = Column(LargeBinary)
    # Low-sensitivity workflow fields, plaintext:
    driver_license_state = Column(String(2))
    has_driver_license = Column(Boolean)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class EventParticipant(Base):
    __tablename__ = "event_participants"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role = Column(String(32), nullable=False)
    display_name = Column(String(200), nullable=False)
    phone = Column(String(32))
    email = Column(String(255))
    measurements = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    notes = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class EventStatusChangeEvent(Base):
    __tablename__ = "event_status_change_events"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    from_status = Column(String(32))
    to_status = Column(String(32), nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    changed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    notes = Column(Text)


class EventNote(Base):
    """One dated note on a deal — the rep-facing running log.

    Replaces the single free-text ``events.notes`` blob (migration 100
    backfills that column into a first note per deal and leaves it in place
    for legacy readers). Each row is one entry in the timeline, authored by
    a user, editable and soft-deletable.

    A note can also carry a FOLLOW-UP REMINDER: ``remind_at`` +
    ``remind_user_id`` + ``remind_channel``. The reminder pass
    (modules/deals/services/note_reminder_runner.py) picks up due rows,
    delivers them, and stamps ``reminder_sent_at`` — the stamp is the
    idempotency guard, so a second pass on the same day never re-sends.
    ``resolved_at`` is the rep saying "handled", which retires the reminder
    whether or not it has fired.

    ``author_display_name`` snapshots the author's name at write time
    (mirroring contact_call_attempts): the FK is ON DELETE SET NULL so a
    departed rep doesn't erase who wrote the note.
    """

    __tablename__ = "event_notes"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    body = Column(Text, nullable=False)
    author_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    author_display_name = Column(String(200))

    # Follow-up reminder (all NULL when the note is just a note).
    remind_at = Column(DateTime(timezone=True))
    remind_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    remind_channel = Column(String(16), nullable=False, server_default=text("'email'"))
    reminder_sent_at = Column(DateTime(timezone=True))
    resolved_at = Column(DateTime(timezone=True))
    resolved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    edited_at = Column(DateTime(timezone=True))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class EventDocument(Base):
    __tablename__ = "event_documents"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind = Column(String(16), nullable=False)
    filename = Column(String(500), nullable=False)
    content_type = Column(String(150), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    storage_key = Column(String(500), nullable=False)
    label = Column(String(200))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    invoice_amount_cents = Column(BigInteger)
    invoice_status = Column(String(16))
    invoice_issued_at = Column(DateTime(timezone=True))
    invoice_paid_at = Column(DateTime(timezone=True))

    # Phase 4a: optional pointer back to a canonical invoices.id row.
    # Populated only on kind='external_invoice' (enforced by CHECK).
    # Phase 4b's data migration backfills this for retagged legacy rows.
    linked_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL")
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    # Phase 10.2: which event participant's buyer journey this invoice
    # belongs to. NULL = celebrant's invoice or unspecified.
    event_participant_id = Column(
        Integer, ForeignKey("event_participants.id", ondelete="SET NULL")
    )
    invoice_number = Column(String(32), unique=True)
    status = Column(String(16), nullable=False, server_default=text("'draft'"))
    issue_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    due_date = Column(Date)

    subtotal_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    total_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    paid_to_date_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    balance_cents = Column(BigInteger, nullable=False, server_default=text("0"))

    # Phase 7: order-level discounts moved to a 1:N child table
    # (`invoice_order_discounts`). When the child table has at least
    # one row, `discount_cents` becomes a derived display value (sum
    # of per-discount savings) and the totals service uses pre-tax
    # math. With zero rows and `discount_cents > 0`, the legacy
    # post-tax flat-amount math still applies for already-sent records.

    terms = Column(Text)
    footer = Column(Text)
    public_notes = Column(Text)
    private_notes = Column(Text)
    po_number = Column(String(64))

    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    sold_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    sent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    paid_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)

    revision = Column(Integer, nullable=False, server_default=text("1"))
    last_pdf_rendered_revision = Column(Integer)
    last_pdf_rendered_at = Column(DateTime(timezone=True))
    last_pdf_render_error = Column(Text)

    legacy_migration_run_id = Column(UUID(as_uuid=True))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceOrderDiscount(Base):
    __tablename__ = "invoice_order_discounts"

    id = Column(BigInteger, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    # `preset_id` references `business_profile.discount_presets[].id` —
    # not a real foreign key because presets live inside a JSONB blob.
    # NULL marks a "Custom %" entry.
    preset_id = Column(Text)
    label = Column(Text, nullable=False)
    percent = Column(Numeric(5, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    kind = Column(String(16), nullable=False, server_default=text("'product'"))
    product_key = Column(String(120))
    # Legacy column. New lines (both catalog-backed and non-catalog) write
    # NULL here; the customer-facing copy comes from `public_description` or
    # the joined `catalog_items` row. Existing rows keep their staff-typed
    # text and still render to customers because that text is on issued
    # PDFs already.
    description = Column(Text)
    quantity = Column(Numeric(10, 2), nullable=False, server_default=text("1"))
    unit_price_cents = Column(BigInteger, nullable=False)
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_rate = Column(Numeric(7, 5), nullable=False, server_default=text("0"))
    tax_name = Column(String(40))
    line_subtotal_cents = Column(BigInteger, nullable=False)
    line_tax_cents = Column(BigInteger, nullable=False)
    line_total_cents = Column(BigInteger, nullable=False)
    # Legacy column. Stops rendering to customers at the Phase 4 render
    # swap; staff-readable historic context stays.
    notes = Column(Text)
    catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="RESTRICT")
    )
    size_label = Column(String(40))
    public_description = Column(Text)
    internal_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceInstallment(Base):
    __tablename__ = "invoice_installments"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    label = Column(String(60), nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    paid_at = Column(DateTime(timezone=True))
    staff_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InvoiceInvitation(Base):
    __tablename__ = "invoice_invitations"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    public_key = Column(String(64), unique=True, nullable=False)
    sent_at = Column(DateTime(timezone=True))
    last_resent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    last_viewed_at = Column(DateTime(timezone=True))
    view_count = Column(Integer, nullable=False, server_default=text("0"))
    email_opened_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True)
    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    # Phase 10.2: which event participant's buyer journey this quote
    # belongs to. NULL = celebrant's quote or unspecified.
    event_participant_id = Column(
        Integer, ForeignKey("event_participants.id", ondelete="SET NULL")
    )
    quote_number = Column(String(32), unique=True)
    status = Column(String(16), nullable=False, server_default=text("'draft'"))
    issue_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    expires_at = Column(Date)

    subtotal_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    total_cents = Column(BigInteger, nullable=False, server_default=text("0"))

    # Phase 7: order-level discounts moved to a 1:N child table
    # (`quote_order_discounts`). See `Invoice` for the same semantics.

    terms = Column(Text)
    footer = Column(Text)
    public_notes = Column(Text)
    private_notes = Column(Text)
    po_number = Column(String(64))
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    sent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    rejected_at = Column(DateTime(timezone=True))
    rejection_reason = Column(Text)
    converted_at = Column(DateTime(timezone=True))
    converted_invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="SET NULL")
    )
    cancelled_at = Column(DateTime(timezone=True))
    cancellation_reason = Column(Text)

    signature_base64 = Column(Text)
    signature_signed_at = Column(DateTime(timezone=True))
    signature_ip = Column(INET)
    signature_name = Column(String(120))
    # Phase 5 of the sales portal — captured opportunistically from the
    # `User-Agent` request header during in-store signing for the
    # evidentiary trail. Nullable so older rows and tests that don't
    # provide a header continue to work.
    signature_user_agent = Column(String(255))
    # C3: HMAC-SHA256 hex over the canonical signed payload, stamped
    # by services.quote_signature_hmac at sign time. Schema CHECK
    # requires this once signature_signed_at is set; trigger
    # `trg_quote_signature_immutable` blocks any UPDATE that would
    # change it (or any other signature column) after the row is
    # signed.
    signature_hmac = Column(String(64))

    revision = Column(Integer, nullable=False, server_default=text("1"))
    last_pdf_rendered_revision = Column(Integer)
    last_pdf_rendered_at = Column(DateTime(timezone=True))
    last_pdf_render_error = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteOrderDiscount(Base):
    __tablename__ = "quote_order_discounts"

    id = Column(BigInteger, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    preset_id = Column(Text)
    label = Column(Text, nullable=False)
    percent = Column(Numeric(5, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"

    id = Column(Integer, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    kind = Column(String(16), nullable=False, server_default=text("'product'"))
    product_key = Column(String(120))
    # See InvoiceLineItem.description: nullable for new lines, populated
    # on legacy rows.
    description = Column(Text)
    quantity = Column(Numeric(10, 2), nullable=False, server_default=text("1"))
    unit_price_cents = Column(BigInteger, nullable=False)
    discount_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    tax_rate = Column(Numeric(7, 5), nullable=False, server_default=text("0"))
    tax_name = Column(String(40))
    line_subtotal_cents = Column(BigInteger, nullable=False)
    line_tax_cents = Column(BigInteger, nullable=False)
    line_total_cents = Column(BigInteger, nullable=False)
    notes = Column(Text)
    catalog_item_id = Column(
        Integer, ForeignKey("catalog_items.id", ondelete="RESTRICT")
    )
    size_label = Column(String(40))
    public_description = Column(Text)
    internal_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class QuoteInstallment(Base):
    """Phase 4 of the discount/payment-term refactor.

    Mirrors `InvoiceInstallment` minus the payment-state columns
    (`paid_at`, `staff_notes`). Quote schedules carry the customer's
    chosen plan from quote sign-off into the converted invoice; nothing
    on a quote has been paid yet, so the payment-state columns are
    deliberately absent.
    """

    __tablename__ = "quote_installments"

    id = Column(BigInteger, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    label = Column(Text)
    amount_cents = Column(BigInteger, nullable=False)
    due_date = Column(Date, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class QuoteInvitation(Base):
    __tablename__ = "quote_invitations"

    id = Column(Integer, primary_key=True)
    quote_id = Column(
        Integer, ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False
    )
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    public_key = Column(String(64), unique=True, nullable=False)
    sent_at = Column(DateTime(timezone=True))
    last_resent_at = Column(DateTime(timezone=True))
    viewed_at = Column(DateTime(timezone=True))
    last_viewed_at = Column(DateTime(timezone=True))
    view_count = Column(Integer, nullable=False, server_default=text("0"))
    email_opened_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    revoked_at = Column(DateTime(timezone=True))
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    contact_id = Column(
        Integer, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False
    )
    payment_number = Column(String(32), unique=True)
    amount_cents = Column(BigInteger, nullable=False)
    applied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    unapplied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    refunded_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    payment_date = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    method = Column(String(20), nullable=False)
    transaction_reference = Column(String(120))
    status = Column(String(24), nullable=False, server_default=text("'completed'"))
    notes = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id = Column(Integer, primary_key=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    applied_cents = Column(BigInteger, nullable=False)
    refunded_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class RefundEvent(Base):
    __tablename__ = "refund_events"

    id = Column(Integer, primary_key=True)
    payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    amount_cents = Column(BigInteger, nullable=False)
    from_unapplied_cents = Column(BigInteger, nullable=False, server_default=text("0"))
    from_allocations_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    refund_method = Column(String(20), nullable=False)
    refund_reference = Column(String(120))
    notes = Column(Text)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class NumberingState(Base):
    __tablename__ = "numbering_state"

    id = Column(SmallInteger, primary_key=True, server_default=text("1"))
    invoice_year = Column(SmallInteger, nullable=False)
    invoice_seq = Column(Integer, nullable=False, server_default=text("0"))
    quote_year = Column(SmallInteger, nullable=False)
    quote_seq = Column(Integer, nullable=False, server_default=text("0"))
    # Phase 6: payment numbering shares the singleton row.
    payment_year = Column(SmallInteger, nullable=False)
    payment_seq = Column(Integer, nullable=False, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class InstallmentReminderState(Base):
    __tablename__ = "installment_reminder_state"

    installment_id = Column(
        Integer,
        ForeignKey("invoice_installments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reminder1_sent_at = Column(DateTime(timezone=True))
    reminder2_sent_at = Column(DateTime(timezone=True))
    reminder3_sent_at = Column(DateTime(timezone=True))
    late_fee_applied_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

