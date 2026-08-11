"""Who gets commission credit for bringing the customer in.

Distinct from `events.owner_user_id`, and the distinction is the whole point.

At Kelley the CRM is worked almost entirely by the admin staff — they own
the leads, run the pipeline, and are the `owner_user_id` on nearly every
deal. But the person who *brought the customer through the door* is usually
a salesperson who never touches the CRM at all, and that person is owed
commission. Line 2 of the old printed intake sheet was exactly this: the
salesperson's name, written at the top, next to nothing else.

Folding that into `owner_user_id` would have been wrong in both directions:
it would hand pipeline ownership to someone who does not work the pipeline,
and it would lose the credit the moment an admin reassigned the lead — which
is a routine, frequent act. Ownership answers "whose queue is this in?" and
changes over a deal's life; credit answers "who earned this?" and must not.

So: a second, independent user reference.

  - **Nullable.** Plenty of leads walk in with no salesperson involved
    (the customer called, or found the website). NULL means "nobody is
    owed credit", which is a real and common answer — not a gap.
  - **ON DELETE SET NULL**, matching `owner_user_id`. Removing a user must
    never block or cascade into deal history. The commission record for a
    departed rep is a payroll question, not a foreign-key question.
  - **Indexed**, unpartial. Unlike `walk_in_source` this is not a filter
    applied to a mostly-NULL column — "what did each rep bring in this
    month?" groups across it, and a full index serves both the grouping and
    the per-rep lookup.

No backfill. Credit on historical rows is unknowable: `owner_user_id` on an
old deal is the admin who filed it, and copying that across would fabricate
commission attribution for people who are not owed it. NULL is the honest
value for every row that predates the question being asked.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE events
              ADD COLUMN IF NOT EXISTS sales_credit_user_id INTEGER
            """
        )
    )

    # Constraint names are global in Postgres, so probe by name.
    exists = connection.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": "fk_events_sales_credit_user"},
    ).first()
    if not exists:
        connection.execute(
            text(
                """
                ALTER TABLE events
                  ADD CONSTRAINT fk_events_sales_credit_user
                  FOREIGN KEY (sales_credit_user_id)
                  REFERENCES users (id)
                  ON DELETE SET NULL
                """
            )
        )

    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_sales_credit_user_id
              ON events (sales_credit_user_id)
            """
        )
    )
