"""Contact / customer records.

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



class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    display_name = Column(String(200), nullable=False)
    email = Column(String(255))
    phone = Column(String(32))
    phone_e164 = Column(String(20))
    address = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tags = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    notes = Column(Text)
    marketing_consent_at = Column(DateTime(timezone=True))
    # SMS opt-out (migration 094). Distinct from marketing_consent_at (email).
    sms_opted_out_at = Column(DateTime(timezone=True))
    sms_opt_out_source = Column(Text)
    # SMS express consent (migration 095). Set when the customer checks the
    # optional consent box on a storefront form. Outbound SMS requires
    # sms_consent_at set AND sms_opted_out_at clear.
    sms_consent_at = Column(DateTime(timezone=True))
    sms_consent_source = Column(Text)
    deleted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


