"""Where a lead came from, and how an appointment got on the calendar.

Two related gaps, one migration, because they are the same question asked
of two tables: *what is the origin of this row?*

**1. `events.walk_in_source` / `walk_in_source_detail`** — staff-entered
attribution for leads that never touch the storefront. Today the only
origin a walk-in carries is free text in `appointments.internal_notes`
("saw the Facebook video"), which nobody can group by. The structured
`source`/`medium`/`click_id` ladder from migration 096 lives on
`storefront_events` and is derived from UTM/referrer/click-id — a person
who walks through the door has none of that, and inventing it would make
`storefront_analytics` lie. So staff attribution gets its own columns on
the deal, deliberately NOT merged with digital attribution.

The vocabulary is a bucket plus a free-text detail rather than one long
enum, because the two questions have different half-lives. "How much of
our walk-in volume is social?" must stay answerable for years, while
"which post pulled?" changes weekly. `walk_in_source` is the stable
bucket; `walk_in_source_detail` carries "Facebook video", "Instagram
reel", "TikTok", "Marketplace" and is never grouped on directly.

`social_media` is one bucket rather than per-platform values for the same
reason: platforms come and go, and the reporting question is about the
channel.

**2. `appointments.source` / `booking_context`** — real columns for what
has been hiding in `raw_payload` JSONB. Two of the four appointment
creation paths stamp `raw_payload = {"source": ...}` and the other two
stamp nothing, so "how many appointments did staff book?" is a JSONB scan
against a key that only sometimes exists. Reporting does not belong in
JSONB. `raw_payload` is left untouched — it stays the audit record of what
the creating path saw.

The two columns are separate axes on purpose. `source` is *which code
path wrote this row* (a fact about the system); `booking_context` is *what
the staff member was doing* (a fact about the business). A phone-in and a
walk-in follow-up are both `staff_created`; only `booking_context` tells
them apart.

**Nullable, not NOT NULL DEFAULT.** A default would silently stamp
'public_booking' on rows written by some future path that forgot to set
it, turning a gap into a wrong answer. NULL reads as "unattributed",
which is true. All four current creation sites set it explicitly.

Backfill, in dependency order, from evidence already in the table:

  - `raw_payload->>'source' = 'walk_in'` → the walk-in placeholder
    (`walk_in_placeholder` / `walk_in`), 11 rows at authoring time.
  - `raw_payload->>'source' = 'public_lead'` → storefront lead-form
    placeholders, 41 rows. These are public self-service in origin, so
    they take `public_booking`; `booking_context` stays NULL because no
    staff member was in the loop. They are not booked visits and the
    distinction survives in `raw_payload`.
  - everything else → `public_booking` (the widget).
  - `rescheduled_from_id IS NOT NULL` → `customer_reschedule`, applied
    LAST so it wins over the origin-derived value. Zero rows today; the
    rule matters for future rows.

`events.walk_in_source` is indexed (partial, NOT NULL only) because it is
a reporting filter over a column that will be NULL for every
storefront-originated deal. `appointments.source` is not indexed — nothing
filters on it yet, and the table is small; add one when a report needs it.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


# Keep in sync with WALK_IN_SOURCE_VALUES in
# modules/booking/services/walk_in_service.py.
WALK_IN_SOURCES = (
    "social_media",
    "drive_by",
    "referral",
    "repeat_customer",
    "google_search",
    "website",
    "other",
)

# Keep in sync with APPOINTMENT_SOURCE_VALUES / BOOKING_CONTEXT_VALUES in
# modules/booking/services/booking_service.py.
APPOINTMENT_SOURCES = (
    "public_booking",
    "staff_created",
    "walk_in_placeholder",
    "customer_reschedule",
)

BOOKING_CONTEXTS = (
    "walk_in",
    "phone_call",
    "existing_customer",
    "admin",
    "other",
)


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
    # ---- events: staff-entered lead origin --------------------------------
    connection.execute(
        text(
            """
            ALTER TABLE events
              ADD COLUMN IF NOT EXISTS walk_in_source VARCHAR(32),
              ADD COLUMN IF NOT EXISTS walk_in_source_detail VARCHAR(200)
            """
        )
    )
    _add_check(
        connection,
        table="events",
        name="chk_events_walk_in_source",
        column="walk_in_source",
        values=WALK_IN_SOURCES,
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_events_walk_in_source
              ON events (walk_in_source)
              WHERE walk_in_source IS NOT NULL
            """
        )
    )

    # ---- appointments: origin out of JSONB, into columns ------------------
    connection.execute(
        text(
            """
            ALTER TABLE appointments
              ADD COLUMN IF NOT EXISTS source VARCHAR(32),
              ADD COLUMN IF NOT EXISTS booking_context VARCHAR(32)
            """
        )
    )
    _add_check(
        connection,
        table="appointments",
        name="chk_appointments_source",
        column="source",
        values=APPOINTMENT_SOURCES,
    )
    _add_check(
        connection,
        table="appointments",
        name="chk_appointments_booking_context",
        column="booking_context",
        values=BOOKING_CONTEXTS,
    )

    # Walk-in placeholders: the only rows that are a receipt of arrival
    # rather than a booking.
    connection.execute(
        text(
            """
            UPDATE appointments
               SET source = 'walk_in_placeholder',
                   booking_context = 'walk_in'
             WHERE source IS NULL
               AND raw_payload->>'source' = 'walk_in'
            """
        )
    )

    # Everything else that exists today came in through a public surface —
    # either the booking widget or the storefront lead form. booking_context
    # stays NULL: no staff member chose to create these.
    connection.execute(
        text(
            """
            UPDATE appointments
               SET source = 'public_booking'
             WHERE source IS NULL
            """
        )
    )

    # Applied last so it overrides the origin-derived value: a reschedule is
    # a distinct act regardless of how the original was booked.
    connection.execute(
        text(
            """
            UPDATE appointments
               SET source = 'customer_reschedule'
             WHERE rescheduled_from_id IS NOT NULL
            """
        )
    )
