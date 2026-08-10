"""Track the browser ring stage so the PSTN fallback fires exactly once.

Phase 2 rings dashboard softphones first and only falls back to a phone number
when none of them answers. Deciding "none of them answered" needs state that
survives across requests and across BOTH uvicorn workers, because each rep leg
reports its outcome in a separate Twilio status callback that may land on
either worker.

  * ``rep_legs_total`` — how many browser clients we actually rang.
  * ``rep_legs_done``  — how many have reported a terminal outcome.
  * ``fallback_started`` — the once-only guard.

The fallback is originated when ``rep_legs_done`` reaches ``rep_legs_total``
and nobody joined. Counting rather than reacting to the first failure matters:
with two reps online, the first one declining must NOT yank the caller away
from the second one's still-ringing phone.

``fallback_started`` is flipped with a conditional UPDATE (``WHERE NOT
fallback_started``) so two simultaneous callbacks cannot both start a fallback
leg — the loser's UPDATE matches zero rows.

Forward-only, matching the repo convention (no downgrade()).
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE inbound_calls
                ADD COLUMN rep_legs_total   INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN rep_legs_done    INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN fallback_started BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
