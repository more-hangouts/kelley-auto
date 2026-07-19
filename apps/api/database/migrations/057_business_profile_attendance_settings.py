"""Phase 7 Slice 2 of the Sales Portal: attendance settings on business_profile.

Three new fields on the singleton `business_profile` row, all
defaulted so existing rows pick up sane behavior at deploy time:

  - `attendance_gate_enabled BOOLEAN DEFAULT TRUE`. Owner switch that
    disables the server-enforced "must be punched in" rule on
    appointment mutations. Default true: a stylist who hasn't clocked
    in cannot mutate today's appointments through the sales portal.
    Owners flip this off in unusual operational modes (covering staff,
    boutique under construction, etc.) without a deploy.
  - `selfie_policy VARCHAR(16) DEFAULT 'optional'`. Per the phase
    plan, three values: `required`, `optional`, `disabled`. CHECK
    constraint enforces the closed set so a typo does not silently
    open a back door.
  - `selfie_retention_days INTEGER DEFAULT 365`. Slice 2's retention
    cron deletes selfie files older than this; the column lands now
    so the cron has a setting to read against once it ships. NULL
    means "keep forever"; numeric means "delete after this many days
    while preserving punch metadata."

DML probes round-trip every column and verify the CHECK rejects an
out-of-set selfie_policy.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD COLUMN attendance_gate_enabled BOOLEAN
                    NOT NULL DEFAULT TRUE,
                ADD COLUMN selfie_policy VARCHAR(16)
                    NOT NULL DEFAULT 'optional',
                ADD COLUMN selfie_retention_days INTEGER NULL DEFAULT 365
            """
        )
    )

    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD CONSTRAINT chk_business_profile_selfie_policy
                CHECK (selfie_policy IN ('required', 'optional', 'disabled'))
            """
        )
    )

    connection.execute(
        text(
            """
            ALTER TABLE business_profile
                ADD CONSTRAINT chk_business_profile_selfie_retention_days
                CHECK (
                    selfie_retention_days IS NULL
                    OR (selfie_retention_days >= 1
                        AND selfie_retention_days <= 3650)
                )
            """
        )
    )

    # ---- DML probes per the project rule ----
    profile_row = connection.execute(
        text("SELECT id FROM business_profile ORDER BY id LIMIT 1")
    ).first()
    if profile_row is None:
        # Fresh install with no business_profile row yet — schema is
        # in place; behavior smokes will catch regressions once the
        # owner saves a profile.
        return

    profile_id = int(profile_row[0])

    # Round-trip every new column.
    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                """
                UPDATE business_profile
                   SET attendance_gate_enabled = FALSE,
                       selfie_policy = 'required',
                       selfie_retention_days = 90
                 WHERE id = :id
                """
            ),
            {"id": profile_id},
        )
        row = connection.execute(
            text(
                "SELECT attendance_gate_enabled, selfie_policy, "
                "selfie_retention_days FROM business_profile WHERE id = :id"
            ),
            {"id": profile_id},
        ).first()
        assert row is not None
        assert row[0] is False, row
        assert row[1] == "required", row
        assert row[2] == 90, row

        # NULL retention is allowed (forever).
        connection.execute(
            text(
                "UPDATE business_profile SET selfie_retention_days = NULL "
                "WHERE id = :id"
            ),
            {"id": profile_id},
        )
        ret = connection.execute(
            text(
                "SELECT selfie_retention_days FROM business_profile "
                "WHERE id = :id"
            ),
            {"id": profile_id},
        ).scalar()
        assert ret is None
    finally:
        sp.rollback()

    # CHECK rejects out-of-set policy values.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    "UPDATE business_profile SET selfie_policy = 'maybe' "
                    "WHERE id = :id"
                ),
                {"id": profile_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "selfie_policy CHECK accepted 'maybe'"
            )
    finally:
        sp.rollback()

    # CHECK rejects out-of-range retention.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    "UPDATE business_profile SET selfie_retention_days = 0 "
                    "WHERE id = :id"
                ),
                {"id": profile_id},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "selfie_retention_days CHECK accepted 0"
            )
    finally:
        sp.rollback()
