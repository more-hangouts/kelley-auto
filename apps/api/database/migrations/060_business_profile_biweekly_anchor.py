"""Phase 9 sub-slice 1, Priority 2: biweekly pay-period anchor.

Adds `biweekly_anchor_date DATE NULL` to the singleton
`business_profile` row so attendance reporting can align 14-day
buckets to the owner's actual pay cadence instead of a rolling
"today minus 13 days" placeholder. The column stays nullable on
purpose: the `bucket=biweek` aggregation rejects with a 422
`pay_period_anchor_missing` when the anchor is unset, and the
existing `pay_period` range key continues to use the rolling-window
placeholder for backward compatibility.

DML probes round-trip a real anchor date and confirm the new column
defaults to NULL on existing rows.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD COLUMN biweekly_anchor_date DATE NULL
            """
        )
    )

    profile_row = connection.execute(
        text("SELECT id FROM business_profile ORDER BY id LIMIT 1")
    ).first()
    if profile_row is None:
        # Fresh install — schema is in place; behavior smokes will
        # exercise the column once the owner saves a profile.
        return

    profile_id = int(profile_row[0])

    # Existing rows must default to NULL (the rolling-window fallback
    # is what we want until the owner sets a real anchor).
    initial = connection.execute(
        text(
            "SELECT biweekly_anchor_date FROM business_profile WHERE id = :id"
        ),
        {"id": profile_id},
    ).scalar()
    assert initial is None, (
        f"biweekly_anchor_date should default NULL on existing rows, got {initial!r}"
    )

    # Round-trip a real date.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "UPDATE business_profile SET biweekly_anchor_date = '2026-01-05' "
                "WHERE id = :id"
            ),
            {"id": profile_id},
        )
        row = connection.execute(
            text(
                "SELECT biweekly_anchor_date FROM business_profile WHERE id = :id"
            ),
            {"id": profile_id},
        ).scalar()
        assert row is not None
        assert str(row) == "2026-01-05", row
    finally:
        sp.rollback()
