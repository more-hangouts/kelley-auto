from sqlalchemy import text


def upgrade(connection) -> None:
    # Catalog SKU obfuscation Phase 1: catalog_items is the single source
    # of truth for orderable styles. One row per style + color
    # combination. Two identifier columns:
    #
    # - internal_sku: real designer SKU (e.g. MORI-4080000-BLACK-RED-ROSE)
    #   used by staff in the admin UI, search, reorder views, and reports.
    #   Never returned from any public/customer-facing endpoint.
    #
    # - public_code: opaque Bellas-only code (e.g. BVX-00042) minted by
    #   the catalog service under numbering_state.catalog_public_code_seq.
    #   Only thing that hits customer documents. Immutable once assigned;
    #   Phase 7 adds a trigger that rejects any UPDATE touching this
    #   column.
    #
    # Vendor identity is intentionally not encoded into public_code. A
    # customer holding two BVX codes cannot tell whether they are the same
    # vendor, different vendors, or sample-only Bellas rows.
    #
    # The source_* block records where each row was scraped from so
    # re-scrapes and re-imports can be diffed and so an orphan import can
    # always be traced back to its origin. JSONB image_urls keeps CDN URLs
    # in display order; v1 deliberately avoids a separate images table.
    connection.execute(
        text(
            """
            CREATE TABLE catalog_items (
                id                       SERIAL PRIMARY KEY,
                internal_sku             VARCHAR(160) NOT NULL UNIQUE,
                public_code              VARCHAR(32)  NOT NULL UNIQUE,
                designer                 VARCHAR(120),
                style_number             VARCHAR(80),
                color                    VARCHAR(80)  NOT NULL,
                house_name               VARCHAR(120),
                product_title            VARCHAR(200),
                category                 VARCHAR(40)  NOT NULL,
                description_text         TEXT,
                image_urls               JSONB        NOT NULL DEFAULT '[]'::jsonb,
                source_platform          VARCHAR(40),
                source_product_id        VARCHAR(80),
                source_product_handle    VARCHAR(160),
                source_product_url       TEXT,
                source_collection_url    TEXT,
                source_product_type      VARCHAR(120),
                is_sample                BOOLEAN      NOT NULL DEFAULT false,
                active                   BOOLEAN      NOT NULL DEFAULT true,
                created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_catalog_items_category
                  CHECK (category IN (
                    'quince_gown',
                    'bridal_gown',
                    'formal_gown',
                    'accessory',
                    'service'
                  )),
                CONSTRAINT chk_catalog_items_image_urls_array
                  CHECK (jsonb_typeof(image_urls) = 'array'),
                CONSTRAINT chk_catalog_items_public_code_format
                  CHECK (public_code ~ '^BVX-[0-9]{5}$')
            )
            """
        )
    )
    # Phase 3 search uses ILIKE '%term%' which btree cannot accelerate, so
    # v1 deliberately avoids per-column substring indexes. The two
    # constraints above already create unique btree indexes on internal_sku
    # and public_code, which cover the only equality lookups in v1
    # (catalog_service.get_by_internal_sku, get_by_public_code, and the
    # uniqueness checks on insert).
    #
    # The single explicit index below supports the most common reporting
    # filter ("active items by designer") without committing to pg_trgm
    # before catalog volume justifies it. Phase 3 revisits indexing once
    # actual search query patterns are visible.
    connection.execute(
        text(
            "CREATE INDEX idx_catalog_items_designer_active "
            "ON catalog_items (designer) "
            "WHERE active = true AND designer IS NOT NULL"
        )
    )
