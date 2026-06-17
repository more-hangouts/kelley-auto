from sqlalchemy import text


def upgrade(connection) -> None:
    # Singleton row holding the legal/branding/render-target data every PDF
    # and portal page reads. The Phase 3 Settings UI edits this; Phase 8
    # PDFs read it on every render so a profile update applies retroactively
    # to any re-rendered invoice.
    connection.execute(
        text(
            """
            CREATE TABLE business_profile (
                id                            SMALLINT PRIMARY KEY DEFAULT 1,
                legal_name                    VARCHAR(200) NOT NULL,
                display_name                  VARCHAR(200),
                address_line1                 VARCHAR(200),
                address_line2                 VARCHAR(200),
                city                          VARCHAR(120),
                state                         VARCHAR(40),
                postal_code                   VARCHAR(20),
                country                       VARCHAR(2) NOT NULL DEFAULT 'US',
                phone                         VARCHAR(40),
                email                         VARCHAR(255),
                website                       VARCHAR(255),
                logo_storage_key              VARCHAR(500),
                default_tax_rate              NUMERIC(7, 5) NOT NULL DEFAULT 0,
                default_tax_name              VARCHAR(40),
                default_invoice_terms         TEXT,
                default_invoice_footer        TEXT,
                default_payment_instructions  TEXT,
                updated_by_user_id            INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_business_profile_singleton CHECK (id = 1),
                CONSTRAINT chk_business_profile_tax_rate_range CHECK (
                    default_tax_rate >= 0 AND default_tax_rate < 1
                )
            )
            """
        )
    )
    # Placeholder row. Staff complete it from Settings in Phase 3 before
    # Phase 8 starts rendering PDFs against it.
    connection.execute(
        text(
            """
            INSERT INTO business_profile (id, legal_name)
            VALUES (1, 'Bellas XV')
            """
        )
    )
