"""The three questions the paper walk-in sheet asked that had nowhere to go.

Reps at Kelley worked off a printed intake sheet for years. Five questions,
asked in order, while the customer stood at the counter. Four of them already
had a home in the CRM after migration 104 — "how did you hear about us"
became `events.walk_in_source`, and the budget question became
`events.budget_range`. Three did not:

  2. What are you currently driving?
  3. What type of vehicle are you looking to purchase?
  5. National lender (bank) or in-house financing?

Until now the intake form concatenated those three answers into
`events.notes` as prose::

    Currently driving: 2014 Altima
    Looking for: Truck / Work Van
    Financing preference: In-house financing

That preserves the words and throws away the data. "How many walk-ins this
month wanted in-house financing?" is not answerable against a free-text
column, and the answers cannot render as fields on the deal the way Budget
does. This migration gives each of the three a column.

**Two enums and a free-text.** `desired_vehicle_type` and
`financing_preference` are CHECK-constrained slugs because they are the
reporting axes — the whole reason for the migration is being able to GROUP BY
them. `current_vehicle` stays free text: a trade-in is "2014 Altima, 180k,
needs tires", and bucketing that at intake would lose the part the rep
actually needs to read back later.

**Slugs, not display labels.** The columns store `truck_work_van`, not
"Truck / Work Van", so the button copy can be reworded without orphaning
historical rows or splitting a report across two spellings of the same
answer. The SPA owns the labels (`apps/admin/src/utils/walkInLeadIntake.js`).

**"Not sure yet" is NULL, not a value.** Both enums deliberately omit an
undecided/unknown member. A rep who does not know leaves the control empty
and the column stays NULL, which reads as "not answered" — the same
convention migration 104 chose for `walk_in_source`. Storing 'undecided'
would make an unasked question and an answered-but-undecided one
indistinguishable, and every report would have to special-case it.

**Nullable, no default and no backfill.** Every row that exists today
predates the question being asked in a structured way. A DEFAULT would stamp
a fabricated answer onto 300-odd historical deals; NULL is the truthful
value. The prose already sitting in `events.notes` on past walk-ins is left
exactly where it is — it is the audit record of what the rep typed, and
parsing it into columns would be inventing structured data from a format
that was never guaranteed.

Both enum columns get a partial index (NOT NULL only), matching the
`idx_events_walk_in_source` precedent: these are reporting filters over
columns that stay NULL for every storefront-originated deal, which is most
of the table. `current_vehicle` gets none — nothing groups on free text.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


# Keep in sync with DESIRED_VEHICLE_TYPE_VALUES in
# modules/booking/services/walk_in_service.py and VEHICLE_TYPE_OPTIONS in
# apps/admin/src/utils/walkInLeadIntake.js.
DESIRED_VEHICLE_TYPES = (
    "car",
    "suv",
    "minivan",
    "truck_work_van",
)

# Keep in sync with FINANCING_PREFERENCE_VALUES in
# modules/booking/services/walk_in_service.py and FINANCING_OPTIONS in
# apps/admin/src/utils/walkInLeadIntake.js.
#
# 'national_lender' is the paper sheet's "BANK"; 'in_house' is the BHPH
# program the dealership underwrites itself. The distinction drives which
# desk the deal lands on, so it is worth a column of its own.
FINANCING_PREFERENCES = (
    "national_lender",
    "in_house",
    "cash",
)

_CURRENT_VEHICLE_MAX = 120


def _add_check(connection, *, table: str, name: str, column: str, values) -> None:
    """Add a value-set CHECK once. Constraint names are global in Postgres,
    so existence is probed by name rather than by table+column."""
    exists = connection.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": name},
    ).first()
    if exists:
        return
    rendered = ", ".join(f"'{v}'" for v in values)
    connection.execute(
        text(
            f"""
            ALTER TABLE {table}
              ADD CONSTRAINT {name}
              CHECK ({column} IS NULL OR {column} IN ({rendered}))
            """
        )
    )


def upgrade(connection) -> None:
    connection.execute(
        text(
            f"""
            ALTER TABLE events
              ADD COLUMN IF NOT EXISTS current_vehicle VARCHAR({_CURRENT_VEHICLE_MAX}),
              ADD COLUMN IF NOT EXISTS desired_vehicle_type VARCHAR(32),
              ADD COLUMN IF NOT EXISTS financing_preference VARCHAR(32)
            """
        )
    )

    _add_check(
        connection,
        table="events",
        name="chk_events_desired_vehicle_type",
        column="desired_vehicle_type",
        values=DESIRED_VEHICLE_TYPES,
    )
    _add_check(
        connection,
        table="events",
        name="chk_events_financing_preference",
        column="financing_preference",
        values=FINANCING_PREFERENCES,
    )

    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_desired_vehicle_type
              ON events (desired_vehicle_type)
              WHERE desired_vehicle_type IS NOT NULL
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_financing_preference
              ON events (financing_preference)
              WHERE financing_preference IS NOT NULL
            """
        )
    )
