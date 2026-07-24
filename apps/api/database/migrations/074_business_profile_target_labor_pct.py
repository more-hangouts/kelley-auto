"""Phase 10 Slice 6 — target labor % on business_profile (Epic 6.2).

One new column on the `business_profile` singleton: `target_labor_pct`
(Numeric(5, 2)) — the owner's target labor cost as a percent of weekly
revenue. When set, the admin schedule grid shows "Sales goal: $X" =
`labor_cost_cents / target_labor_pct * 100` next to the actual revenue
for the visible week.

Schema notes:

  - Numeric(5, 2) matches `default_deposit_percent`'s shape. Values
    are percent points (e.g. 20.00 = 20%) not fractions.
  - CHECK (target_labor_pct IS NULL OR (target_labor_pct > 0
    AND target_labor_pct <= 100)): 0 would force a divide-by-zero in
    the chip math; NULL is the "not set" sentinel.
  - Default NULL — boutiques that don't track labor % keep the chip
    hidden until they fill it in.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD COLUMN target_labor_pct NUMERIC(5, 2)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE business_profile "
            "ADD CONSTRAINT chk_bp_target_labor_pct "
            "CHECK (target_labor_pct IS NULL "
            "       OR (target_labor_pct > 0 AND target_labor_pct <= 100))"
        )
    )

    # ===== DML probes =====
    sp = connection.begin_nested()
    try:
        # Setting a valid value round-trips.
        connection.execute(
            text(
                "UPDATE business_profile SET target_labor_pct = 20.00 "
                "WHERE id = 1"
            )
        )
        got = connection.execute(
            text(
                "SELECT target_labor_pct FROM business_profile WHERE id = 1"
            )
        ).scalar()
        assert got is not None
        # Numeric round-trips as Decimal; coerce to string for stable compare.
        assert str(got) == "20.00", f"expected 20.00, got {got!r}"

        # Reset to NULL.
        connection.execute(
            text(
                "UPDATE business_profile SET target_labor_pct = NULL "
                "WHERE id = 1"
            )
        )
        assert (
            connection.execute(
                text(
                    "SELECT target_labor_pct FROM business_profile "
                    "WHERE id = 1"
                )
            ).scalar()
            is None
        )

        # 0 should be rejected by the CHECK.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE business_profile SET target_labor_pct = 0 "
                        "WHERE id = 1"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_bp_target_labor_pct accepted 0"
                )
        finally:
            sp_inner.rollback()

        # 100.01 should be rejected.
        sp_inner = connection.begin_nested()
        try:
            try:
                connection.execute(
                    text(
                        "UPDATE business_profile "
                        "SET target_labor_pct = 100.01 WHERE id = 1"
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError(
                    "chk_bp_target_labor_pct accepted 100.01"
                )
        finally:
            sp_inner.rollback()
    finally:
        sp.rollback()
