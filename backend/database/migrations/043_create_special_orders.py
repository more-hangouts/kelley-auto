from sqlalchemy import text


def upgrade(connection) -> None:
    # Catalog SKU obfuscation Phase 5: special_orders is the "where is
    # my dress?" log. Tracks the ordered → received → picked_up
    # lifecycle for catalog-backed line items without pretending the
    # shop has a stock-counting inventory engine. v1 deliberately does
    # NOT model warehouse locations, reservations, stock decrements,
    # vendor sync, or partial fulfillments.
    #
    # Foreign-key choices:
    #
    #   event_id            ON DELETE RESTRICT — special orders are
    #                       business-critical history. Deleting an
    #                       event with open special orders should fail
    #                       loud rather than silently orphan the
    #                       lifecycle log.
    #
    #   invoice_line_item_id ON DELETE SET NULL — staff occasionally
    #                       reshape an invoice (replace a line, restart
    #                       the build) and the special order should
    #                       survive the line edit. The catalog_item_id
    #                       + size_label snapshot is sufficient to
    #                       answer "what's still on order" even if the
    #                       originating invoice line is gone.
    #
    #   catalog_item_id     ON DELETE RESTRICT — the catalog row is
    #                       the only persistent source of truth for
    #                       what was ordered. Phase 1 already prevents
    #                       physical deletion of catalog rows that
    #                       have invoice/quote line references; this
    #                       extends the same protection to special
    #                       orders.
    #
    # Status CHECK constraints the vocabulary in the database, not just
    # in service code, so a future migration script or ad-hoc SQL
    # session cannot quietly insert a row in 'shipped' or 'returned'
    # without a schema decision.
    #
    # picked_up requires received: the picked_up_at stamp implies the
    # dress was received first. Without this CHECK, staff could mark a
    # line picked_up directly from delayed and skip the received
    # state, leaving the timeline confusing.
    connection.execute(
        text(
            """
            CREATE TABLE special_orders (
                id                       SERIAL PRIMARY KEY,
                event_id                 INTEGER NOT NULL
                                         REFERENCES events(id) ON DELETE RESTRICT,
                invoice_line_item_id     INTEGER
                                         REFERENCES invoice_line_items(id)
                                         ON DELETE SET NULL,
                catalog_item_id          INTEGER NOT NULL
                                         REFERENCES catalog_items(id)
                                         ON DELETE RESTRICT,
                size_label               VARCHAR(40) NOT NULL,
                status                   VARCHAR(24) NOT NULL DEFAULT 'needed',
                ordered_at               TIMESTAMPTZ,
                eta_date                 DATE,
                received_at              TIMESTAMPTZ,
                picked_up_at             TIMESTAMPTZ,
                vendor_order_number      VARCHAR(120),
                internal_notes           TEXT,
                created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                CONSTRAINT chk_special_orders_status
                  CHECK (status IN (
                    'needed',
                    'ordered',
                    'delayed',
                    'received',
                    'picked_up',
                    'cancelled'
                  )),
                CONSTRAINT chk_special_orders_size_label_nonempty
                  CHECK (length(trim(size_label)) > 0),
                CONSTRAINT chk_special_orders_picked_up_requires_received
                  CHECK (picked_up_at IS NULL OR received_at IS NOT NULL),
                CONSTRAINT chk_special_orders_ordered_status_has_ordered_at
                  CHECK (
                    status NOT IN ('ordered', 'delayed') OR ordered_at IS NOT NULL
                  ),
                CONSTRAINT chk_special_orders_received_status_has_received_at
                  CHECK (
                    status NOT IN ('received', 'picked_up') OR received_at IS NOT NULL
                  ),
                CONSTRAINT chk_special_orders_picked_up_status_has_picked_up_at
                  CHECK (
                    status <> 'picked_up' OR picked_up_at IS NOT NULL
                  )
            )
            """
        )
    )
    # Most special-order reads come from the event detail screen and
    # the dashboard "what's still on order" widget. Both filter by
    # event and want the lifecycle bucket; the partial index keeps the
    # active set small.
    connection.execute(
        text(
            "CREATE INDEX idx_special_orders_event_status "
            "ON special_orders (event_id, status)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_special_orders_open "
            "ON special_orders (event_id) "
            "WHERE status NOT IN ('picked_up', 'cancelled')"
        )
    )
