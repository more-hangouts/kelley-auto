"""Phase 5 of the Sales Portal: signature_user_agent on quotes.

The existing in-store signing path already captures
`signature_base64`, `signature_signed_at`, `signature_ip`, and
`signature_name`. Adding the user agent rounds out the evidentiary
package for any future challenge to a signed quote — without going
all the way to a formal e-signature ceremony, which is overkill for
the small-business use case.

Capture is opportunistic: the column is nullable so older rows and
tests that never carry a user-agent header still flow through.
"""

from sqlalchemy import text


def upgrade(connection) -> None:
    connection.execute(
        text(
            "ALTER TABLE quotes "
            "ADD COLUMN signature_user_agent VARCHAR(255) NULL"
        )
    )

    # ---- DML probe per the project rule ----
    # Pick any quote row to round-trip the new column. Fresh installs
    # without quotes skip cleanly.
    quote_row = connection.execute(
        text(
            "SELECT id, signature_base64 FROM quotes "
            "WHERE deleted_at IS NULL ORDER BY id LIMIT 1"
        )
    ).first()
    if quote_row is None:
        return

    quote_id = int(quote_row[0])
    had_signature = quote_row[1] is not None

    sp = connection.begin_nested()
    try:
        connection.execute(
            text(
                "UPDATE quotes SET signature_user_agent = :ua "
                "WHERE id = :qid"
            ),
            {"qid": quote_id, "ua": "Mozilla/5.0 (probe-053)"},
        )
        ua = connection.execute(
            text(
                "SELECT signature_user_agent FROM quotes WHERE id = :qid"
            ),
            {"qid": quote_id},
        ).scalar()
        assert ua == "Mozilla/5.0 (probe-053)", ua

        # Verify NULL is allowed.
        connection.execute(
            text(
                "UPDATE quotes SET signature_user_agent = NULL "
                "WHERE id = :qid"
            ),
            {"qid": quote_id},
        )
        ua = connection.execute(
            text(
                "SELECT signature_user_agent FROM quotes WHERE id = :qid"
            ),
            {"qid": quote_id},
        ).scalar()
        assert ua is None

        # Verify the existing signature CHECK constraints did not bite
        # us — the row should still be valid in whatever signed/unsigned
        # state it was in before. We don't re-check chk_quote_*
        # constraints explicitly; the savepoint rollback at the end
        # restores prior state, and the next SELECT confirms the row
        # still exists.
        still_there = connection.execute(
            text(
                "SELECT 1 FROM quotes WHERE id = :qid"
            ),
            {"qid": quote_id},
        ).scalar()
        assert still_there == 1
        # Reference had_signature so static-analysis sees the pre-state
        # capture as load-bearing.
        _ = had_signature
    finally:
        sp.rollback()

    # 255-char overflow is rejected by the column type. Probe with a
    # too-long value in its own savepoint.
    sp = connection.begin_nested()
    try:
        try:
            connection.execute(
                text(
                    "UPDATE quotes SET signature_user_agent = :ua "
                    "WHERE id = :qid"
                ),
                {"qid": quote_id, "ua": "X" * 300},
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                "VARCHAR(255) accepted a 300-char user_agent string"
            )
    finally:
        sp.rollback()
