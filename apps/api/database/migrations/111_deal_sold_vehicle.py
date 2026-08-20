"""The car they actually bought, kept apart from the car they asked about.

`events.vehicle_catalog_item_id` has carried both meanings since Day 3, and
that worked only while the two were the same car. They routinely are not: a
shopper inquires on the Altima in the listing photos, comes in, and drives off
in the Rogue. Today the only way to record that is to overwrite the link — and
overwriting it destroys the one fact that says where the lead came from.

That single column was doing two different jobs:

  * **Which listing produced this lead?** Written once, by the storefront, at
    intake. It is attribution: `/analytics` per-vehicle lead counts and the
    deal's "Asking about" line both read it, and both are answering a question
    about the PAST. Editing it rewrites history and silently moves a lead onto
    a car that never generated it.
  * **Which car closed?** Written at the END of the deal, by a staff member,
    and it is the one that drives inventory — `_propagate_vehicle_status`
    marks it `sold` when the deal reaches `sold`.

Conflating them means the wrong car gets marked sold whenever the customer
switched, which is the concrete bug here: the Rogue stays listed as available
and the Altima disappears from the site while sitting on the lot.

So `sold_vehicle_catalog_item_id` is the second, independent link.

  - **Nullable, and NULL is the normal state.** Only a deal that closed on a
    DIFFERENT car than it started on needs a value. `_propagate_vehicle_status`
    falls back to `vehicle_catalog_item_id` when this is NULL, so every
    existing deal keeps behaving exactly as it does today.
  - **ON DELETE SET NULL**, matching `vehicle_catalog_item_id`. Removing a
    catalog row must never block deleting inventory or cascade into deal
    history.
  - **Indexed**, unpartial: "what sold this month, and which car was it?"
    groups across the column, and the per-vehicle lookup ("show me the deal
    that closed this car") needs it too.

No backfill, and none is possible. For the 155 deals with an inquiry vehicle
we cannot know whether the customer switched — copying the inquiry link across
would assert that nobody ever did, which is the very thing this column exists
to stop asserting. NULL means "not recorded separately", and the fallback
makes that read as "closed on the car they asked about", which is the correct
default for every historical row.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE events
              ADD COLUMN IF NOT EXISTS sold_vehicle_catalog_item_id INTEGER
            """
        )
    )

    # Constraint names are global in Postgres, so probe by name.
    exists = connection.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": "fk_events_sold_vehicle_catalog_item"},
    ).first()
    if not exists:
        connection.execute(
            text(
                """
                ALTER TABLE events
                  ADD CONSTRAINT fk_events_sold_vehicle_catalog_item
                  FOREIGN KEY (sold_vehicle_catalog_item_id)
                  REFERENCES catalog_items (id)
                  ON DELETE SET NULL
                """
            )
        )

    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_sold_vehicle_catalog_item_id
              ON events (sold_vehicle_catalog_item_id)
            """
        )
    )
